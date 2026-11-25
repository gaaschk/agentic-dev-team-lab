import contextlib
import io
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from team_agents.agent_retry import AgentRetry, RetryableAgentError
from team_agents.qa_agent import parse_plan, propose


QA_PLAN = {"checks": [{
    "criterion_id": 1,
    "name": "State survives restart",
    "setup": "Use fresh local data.",
    "action": "Save an item, restart the app, and list items.",
    "expected": "The original item is present.",
}]}


class QAAgentTests(TestCase):
    def test_requires_coverage_for_each_criterion(self):
        with self.assertRaisesRegex(RetryableAgentError, "criteria: 2"):
            parse_plan(json.dumps(QA_PLAN), 2)

    def test_rejects_unknown_ids_and_verdicts(self):
        for invalid in (
            {"checks": [{**QA_PLAN["checks"][0], "criterion_id": 3}]},
            {"checks": [{**QA_PLAN["checks"][0], "criterion_id": True}]},
            {**QA_PLAN, "verdict": "PASS"},
        ):
            with self.assertRaises(RetryableAgentError):
                parse_plan(json.dumps(invalid), 1)

    def test_retry_keeps_inputs_independent_of_candidate(self):
        read = '{"action":"read:requirements"}'
        router = Mock(side_effect=[read, '{"checks":[]}', read, json.dumps(QA_PLAN)])
        plan = {
            "in_scope": ["Store a note"],
            "out_of_scope": ["Sync"],
            "acceptance_criteria": ["A saved note survives restart"],
            "implementation": "DO NOT GIVE QA THIS DATA",
        }
        design = {"interface": "notes save and notes list", "persistence": "HIDDEN INTERNALS"}
        with (patch("builtins.input", return_value="r") as prompt,
              contextlib.redirect_stdout(io.StringIO()),
              contextlib.redirect_stderr(io.StringIO())):
            self.assertEqual(propose(plan, design, decide=router, retry=AgentRetry()), QA_PLAN)
        prompt.assert_called_once()
        payload = router.call_args.args[1]
        evidence = json.loads(payload.rsplit("\n\n", 1)[1])
        self.assertEqual(evidence["requirements"]["criteria"], [
            {"criterion_id": 1, "requirement": plan["acceptance_criteria"][0]}
        ])
        self.assertIn("notes save and notes list", payload)
        self.assertNotIn("DO NOT GIVE QA THIS DATA", payload)
        self.assertNotIn("HIDDEN INTERNALS", payload)
