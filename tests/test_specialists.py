import json
import os
import tempfile
from unittest import TestCase
from unittest.mock import Mock, patch

from team_agents.agent_retry import AgentRetry, RetryableAgentError
from team_agents.product_lead_agent import product_plan
from team_agents.workbench import Team
from tests.test_plan_release import DESIGN, PLAN, PROPOSAL
from tests.test_qa_agent import QA_PLAN
from workflows.release import release_workflow
from workflows.runner import run

UX_SPEC = {"flows": ["Add a book."], "states": ["Empty list says so."],
           "accessibility": [], "requirements": []}
PASS = {"verdict": "PASS", "blockers": []}
BLOCK = {"verdict": "CHANGES_REQUIRED", "blockers": [
    {"file": "app.py", "evidence": "eval(title)", "consequence": "Runs any code."}]}


def team_plan(*names):
    return {**PLAN, "specialists": [{"name": name, "reason": "Needed."} for name in names]}


class SpecialistTests(TestCase):
    def release(self, plan, security=PASS):
        replies = {"Product Lead": plan, "UX Designer": UX_SPEC, "Architect": DESIGN,
                   "QA": QA_PLAN, "Implementer": PROPOSAL, "Security Reviewer": security}
        seen, skipped = {}, []

        def artifacts(*, stage, artifacts, required, **_):
            seen[stage] = {name: artifacts[name].value for name in required}
            return json.dumps(replies[stage])

        def decide(role, task, *, stage, **_):
            seen[stage] = task
            return json.dumps(replies[stage])

        workflow = release_workflow(decide=decide, review=lambda *a: json.dumps(PASS),
                                    artifact_runner=artifacts)
        try:
            state = run(workflow, {"goal": "g"}, retry=AgentRetry(prompt=Mock(side_effect=AssertionError)),
                        prompt=lambda text: "y", emit=lambda text: None, on_skip=skipped.append)
        except SystemExit as exc:
            return str(exc), seen, skipped
        return state, seen, skipped

    def test_unselected_specialists_are_skipped(self):
        state, seen, skipped = self.release(team_plan())
        self.assertEqual(skipped, ["UX Designer", "Security Reviewer"])
        self.assertNotIn("UX Designer", seen)
        self.assertNotIn("Security Reviewer", seen)
        self.assertNotIn("ux_spec", seen["Architect"])
        self.assertNotIn("ux_spec", seen["Implementer"])
        self.assertIsNone(state["security_review"])

    def test_ux_advice_reaches_architect_qa_and_implementer(self):
        state, seen, skipped = self.release(team_plan("UX Designer"))
        self.assertEqual(skipped, ["Security Reviewer"])
        self.assertEqual(seen["Architect"]["ux_spec"], UX_SPEC)
        self.assertEqual(seen["QA"]["ux_spec"], UX_SPEC)
        self.assertIn("Empty list says so.", seen["Implementer"])
        self.assertEqual(state["ux_spec"], UX_SPEC)

    def test_security_pass_completes_the_run_on_the_exact_proposal(self):
        state, seen, _ = self.release(team_plan("Security Reviewer"))
        self.assertEqual(state["security_review"], PASS)
        self.assertEqual(seen["Security Reviewer"]["candidate"], PROPOSAL)

    def test_security_blocker_ends_the_run_without_a_retry_prompt(self):
        result, _, _ = self.release(team_plan("Security Reviewer"), security=BLOCK)
        self.assertIn("Blocked by the Security Reviewer", result)
        self.assertIn("Runs any code.", result)

    def test_plan_rejects_a_team_outside_the_roster_or_repeated(self):
        for plan in (team_plan("Marketer"), team_plan("UX Designer", "UX Designer"),
                     {**PLAN, "specialists": [{"name": "UX Designer", "reason": " "}]}):
            with self.assertRaises(RetryableAgentError):
                product_plan(None, "g", decide=None,
                             artifact_runner=lambda **_: json.dumps(plan))

    def test_specialists_get_memory_like_other_agents(self):
        stages = []

        def decide(role, task, *, stage, **_):
            stages.append(stage)
            return json.dumps({"tool": "decide", "path": "", "content": "", "command": "", "notes": ""})

        with patch.dict(os.environ, {"LAB_AGENTS_HOME": tempfile.mkdtemp()}):
            team = Team(decide, emit=lambda text: None, shell=True)
            for name in ("UX Designer", "Security Reviewer"):
                team("role", "t", stage=name)
        self.assertEqual(stages, ["UX Designer memory", "UX Designer",
                                  "Security Reviewer memory", "Security Reviewer"])
