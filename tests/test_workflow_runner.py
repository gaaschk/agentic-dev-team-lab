from unittest import TestCase
from unittest.mock import Mock, patch

from team_agents.agent_retry import AgentRetry, RetryableAgentError, TransientTimeout
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

    def test_operations_agent_auto_retries_a_timeout_on_a_stage_marked_safe(self):
        seen = []

        def consume(failure, artifact):
            seen.append(artifact["contents"])
            artifact["contents"] = "changed by consumer"
            if failure is None:
                raise TransientTimeout("Temporary failure.")
            return "done"

        workflow = (
            Approval("draft", "approved", "Approve?", "Declined."),
            Step("Consume", ("approved",), "result", consume, auto_retryable=True),
        )
        prompt = Mock(return_value="y")
        with patch("builtins.input") as retry_prompt:
            result = run(workflow, {"draft": {"contents": "fixed"}},
                         retry=AgentRetry(auto_retries=1), prompt=prompt, emit=Mock())
        self.assertEqual(seen, ["fixed", "fixed"])
        self.assertEqual(result["approved"], {"contents": "fixed"})
        self.assertEqual(result["result"], "done")
        prompt.assert_called_once_with("Approve?")
        retry_prompt.assert_not_called()

    def test_malformed_output_goes_straight_to_the_human_even_on_a_safe_stage(self):
        """Regression: only a TransientTimeout is eligible for the automatic
        retry. A plain RetryableAgentError (malformed output, HTTP errors)
        always asks a human, even on a stage marked auto_retryable."""
        def consume(failure):
            raise RetryableAgentError("Malformed Product Lead output.")

        workflow = (Step("Produce", (), "result", consume, auto_retryable=True,
                          retry_stop="Stopped."),)
        with patch("builtins.input") as retry_prompt:
            retry_prompt.return_value = "s"
            with self.assertRaisesRegex(SystemExit, "Stopped."):
                run(workflow, {}, retry=AgentRetry(auto_retries=1), prompt=Mock(), emit=Mock())
        retry_prompt.assert_called_once()

    def test_auto_retry_budget_is_shared_once_across_the_whole_run(self):
        """Regression: the automatic retry is a one-time budget for the run,
        not a per-stage allowance. Once an earlier stage spends it, a later
        stage's timeout goes straight to the human."""
        def flaky_once(failure):
            if failure is None:
                raise TransientTimeout("First timeout.")
            return "ok"

        def always_timeout(failure):
            raise TransientTimeout("Second timeout.")

        workflow = (
            Step("First", (), "a", flaky_once, auto_retryable=True),
            Step("Second", (), "b", always_timeout, auto_retryable=True, retry_stop="Stopped."),
        )
        with patch("builtins.input") as retry_prompt:
            retry_prompt.return_value = "s"
            with self.assertRaisesRegex(SystemExit, "Stopped."):
                run(workflow, {}, retry=AgentRetry(auto_retries=1), prompt=Mock(), emit=Mock())
        retry_prompt.assert_called_once()

    def test_on_start_and_on_output_report_each_step_separately_from_emit(self):
        started, finished, emitted = [], [], []
        workflow = (
            Step("Produce", (), "result", lambda failure: "value", render=lambda r: f"rendered:{r}"),
        )
        run(workflow, {}, retry=AgentRetry(), prompt=Mock(), emit=emitted.append,
            on_start=started.append, on_output=lambda name, text: finished.append((name, text)))
        self.assertEqual(started, ["Produce"])
        self.assertEqual(finished, [("Produce", "rendered:value")])
        self.assertEqual(emitted, [])

    def test_human_still_decides_once_auto_retries_are_exhausted(self):
        def consume(failure, artifact):
            raise TransientTimeout("Still failing.")

        workflow = (
            Approval("draft", "approved", "Approve?", "Declined."),
            Step("Consume", ("approved",), "result", consume, retry_stop="Stopped.",
                 auto_retryable=True),
        )
        prompt = Mock(return_value="y")
        with patch("builtins.input", return_value="s"):
            with self.assertRaisesRegex(SystemExit, "Stopped."):
                run(workflow, {"draft": {}}, retry=AgentRetry(auto_retries=1),
                    prompt=prompt, emit=Mock())

    def test_on_output_still_fires_when_a_step_gives_up(self):
        finished = []

        def consume(failure):
            raise RetryableAgentError("Still failing.")

        workflow = (Step("Consume", (), "result", consume, retry_stop="Stopped."),)
        with patch("builtins.input", return_value="s"):
            with self.assertRaisesRegex(SystemExit, "Stopped."):
                run(workflow, {}, retry=AgentRetry(), prompt=Mock(), emit=Mock(),
                    on_output=lambda name, text: finished.append((name, text)))
        self.assertEqual(finished, [("Consume", "Stopped.")])
