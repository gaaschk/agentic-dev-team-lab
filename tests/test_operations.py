from unittest import TestCase
from unittest.mock import Mock

from team_agents.agent_retry import RetryableAgentError, TransientTimeout
from team_agents.operations import Operations


def ops_for(decide, **kwargs):
    log = []
    return Operations(decide, emit=log.append, sleep=lambda s: None, **kwargs), log


class OperationsTests(TestCase):
    def test_retries_a_single_timed_out_call_and_recovers(self):
        decide = Mock(side_effect=[TransientTimeout("slow."), "ok"])
        ops, log = ops_for(decide, fallbacks=[])
        self.assertEqual(ops("role", "task", model="smart-auto", stage="Architect"), "ok")
        self.assertEqual(decide.call_count, 2)
        self.assertTrue(any("Retrying Architect" in line for line in log))

    def test_switches_to_fallback_route_after_a_timeout_and_keeps_it(self):
        decide = Mock(side_effect=[TransientTimeout("slow."), "ok", "ok again"])
        ops, _ = ops_for(decide, fallbacks=["smart-sonnet"])
        ops("role", "task", model="smart-auto", stage="A")
        ops("role", "task", model="smart-auto", stage="B")
        routes = [call.kwargs["model"] for call in decide.call_args_list]
        self.assertEqual(routes, ["smart-auto", "smart-sonnet", "smart-sonnet"])

    def test_server_errors_retry_on_the_same_route(self):
        decide = Mock(side_effect=[RetryableAgentError("HTTP 503."), "ok"])
        ops, _ = ops_for(decide, fallbacks=["smart-sonnet"])
        ops("role", "task", model="smart-auto")
        self.assertEqual([c.kwargs["model"] for c in decide.call_args_list], ["smart-auto"] * 2)

    def test_gives_up_after_the_attempt_limit(self):
        decide = Mock(side_effect=TransientTimeout("slow."))
        ops, log = ops_for(decide, attempts=3, fallbacks=[])
        with self.assertRaises(TransientTimeout):
            ops("role", "task", model="m", stage="QA")
        self.assertEqual(decide.call_count, 3)
        self.assertIn("giving up", log[-1])

    def test_non_retryable_errors_pass_straight_through(self):
        decide = Mock(side_effect=SystemExit("HTTP 401."))
        ops, _ = ops_for(decide)
        with self.assertRaises(SystemExit):
            ops("role", "task", model="m")
        self.assertEqual(decide.call_count, 1)
