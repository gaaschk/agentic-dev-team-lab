from unittest import TestCase
from unittest.mock import Mock, patch

from team_agents.agent_retry import AgentRetry, RetryableAgentError
from workflows.runner import Approval, Step, run


class WorkflowRunnerTests(TestCase):
    def test_invalid_handoff_rejected_before_any_action(self):
        action = Mock()
        invalid = (
            Step("Produce", (), "unapproved", action),
            Step("Consume", ("approved",), "result", action),
        )
        with self.assertRaisesRegex(ValueError, "Invalid workflow handoff"):
            run(invalid, {}, retry=AgentRetry(), prompt=Mock(), emit=Mock())
        action.assert_not_called()

    def test_denial_and_eof_never_call_consumer(self):
        consumer = Mock()
        workflow = (
            Approval("draft", "approved", "Approve?", "Declined."),
            Step("Consume", ("approved",), "result", consumer),
        )
        for prompt in (Mock(return_value="n"), Mock(side_effect=EOFError)):
            with self.assertRaisesRegex(SystemExit, "Declined"):
                run(workflow, {"draft": {}}, retry=AgentRetry(), prompt=prompt, emit=Mock())
        consumer.assert_not_called()

    def test_retry_uses_exact_approved_input_without_reapproving(self):
        seen = []

        def consume(failure, artifact):
            seen.append(artifact["contents"])
            artifact["contents"] = "changed by consumer"
            if failure is None:
                raise RetryableAgentError("Temporary failure.")
            return "done"

        workflow = (
            Approval("draft", "approved", "Approve?", "Declined."),
            Step("Consume", ("approved",), "result", consume),
        )
        prompt = Mock(return_value="y")
        with patch("builtins.input", return_value="r") as retry_prompt:
            result = run(workflow, {"draft": {"contents": "fixed"}},
                         retry=AgentRetry(), prompt=prompt, emit=Mock())
        self.assertEqual(seen, ["fixed", "fixed"])
        self.assertEqual(result["approved"], {"contents": "fixed"})
        self.assertEqual(result["result"], "done")
        prompt.assert_called_once_with("Approve?")
        retry_prompt.assert_called_once()
