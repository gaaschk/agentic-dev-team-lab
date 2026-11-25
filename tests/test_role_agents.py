import contextlib
import io
import json
from unittest import TestCase
from unittest.mock import Mock, patch
import sys

import plan_release
from team_agents.reviewer_agent import REVIEW_FORMAT
from team_agents.role_agents import Artifact, run
from tests.test_qa_agent import QA_PLAN
from tests.test_plan_release import DESIGN, PLAN


class ArtifactAgentTests(TestCase):
    def exercise(self, decide, *, optional=False):
        artifacts = {"criteria": Artifact("Requirements", "frozen scope"),
                     "candidate": Artifact("Code", "candidate bytes")}
        if optional:
            artifacts["notes"] = Artifact("Optional", "notes")
        return run(
            role="Reviewer", stage="Reviewer", task="Review.",
            artifacts=artifacts,
            required={"criteria", "candidate"}, output_format=REVIEW_FORMAT,
            model="test-model", decide=decide,
        )

    def test_cannot_submit_early_or_reread(self):
        for actions in (["submit"], ["read:candidate", "read:candidate"], ["read:/etc/passwd"]):
            call = Mock(side_effect=[json.dumps({"action": a}) for a in actions])
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "tool action not offered"):
                    self.exercise(call, optional=True)
            self.assertEqual(call.call_count, len(actions))

    def test_reads_exact_candidate_and_criteria_before_final(self):
        call = Mock(side_effect=['{"action":"read:candidate"}', '{"verdict":"PASS","blockers":[]}'])
        with contextlib.redirect_stdout(io.StringIO()):
            answer = self.exercise(call)
        self.assertEqual(json.loads(answer)["verdict"], "PASS")
        self.assertEqual(call.call_count, 2)
        self.assertNotIn("candidate bytes", call.call_args_list[0].args[1])
        self.assertEqual(
            json.loads(call.call_args.args[1].split("\n\n", 1)[1]),
            {"candidate": "candidate bytes", "criteria": "frozen scope"},
        )

    def test_team_mode_keeps_handoffs_and_approvals(self):
        actions = [
            '{"action":"read:brief"}', json.dumps(PLAN),
            '{"action":"read:approved_plan"}', '{"action":"submit"}', json.dumps(DESIGN),
            '{"action":"read:requirements"}', json.dumps(QA_PLAN),
            "A proposed candidate",
            '{"action":"read:criteria"}',
            '{"verdict":"PASS","blockers":[]}',
        ]
        with (
            patch.dict("os.environ", {"DEFAULT_MODEL": "smart-sonnet", "LAB_QA_MODEL": "smart-auto"}, clear=True),
            patch.object(sys, "argv", ["plan_release.py", "Build a reading list"]),
            patch.object(plan_release, "ask_role", side_effect=actions) as decide,
            patch("builtins.input", side_effect=["y", "y"]) as approvals,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            plan_release.main()
        self.assertEqual(approvals.call_count, 2)
        for call in decide.call_args_list:
            expected_model = "smart-auto" if call.kwargs["stage"].startswith("QA") else "smart-sonnet"
            self.assertEqual(call.kwargs["model"], expected_model)
        self.assertEqual([call.kwargs["stage"] for call in decide.call_args_list][5:8],
                         ["QA tool choice", "QA", "Implementer"])
        self.assertIn("QA plan only. No tests have run.", output.getvalue())
        self.assertIn("Reviewer tool: read(candidate)", output.getvalue())
        observations = json.loads(decide.call_args.args[1].split("\n\n", 1)[1])
        self.assertEqual(observations["candidate"], "A proposed candidate")
        self.assertEqual(observations["criteria"]["plan"], PLAN)
        self.assertEqual(observations["criteria"]["design"], DESIGN)

    def test_team_retries_bad_tool_choice_without_reapproving(self):
        actions = [
            '{"action":"read:brief"}', json.dumps(PLAN),
            '{"action":"read:approved_plan"}', '{"action":"submit"}', json.dumps(DESIGN),
            '{"action":"read:requirements"}', json.dumps(QA_PLAN),
            "A proposed candidate",
            '{"action":"submit"}',  # Illegal before either artifact is read.
            '{"action":"read:criteria"}', '{"verdict":"PASS","blockers":[]}',
        ]
        with (
            patch.object(sys, "argv", ["plan_release.py", "Build a reading list"]),
            patch.object(plan_release, "ask_role", side_effect=actions) as decide,
            patch("builtins.input", side_effect=["y", "y", "r"]) as prompt,
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            plan_release.main()
        self.assertEqual(prompt.call_count, 3)
        self.assertIn("[a] Retry all", prompt.call_args.args[0])
        self.assertIn("Retrying Reviewer", errors.getvalue())
        self.assertIn('"verdict": "PASS"', output.getvalue())
        self.assertEqual(decide.call_args_list[-3], decide.call_args_list[-2])
