import json
from unittest import TestCase
from unittest.mock import Mock

from team_agents.agent_retry import RetryableAgentError, TransientTimeout
from team_agents.operations import MAX_ATTEMPTS, Operations


def decision(action, route="", wait=0, timeout=60, reason="because"):
    return json.dumps({"action": action, "route": route, "wait_seconds": wait,
                       "timeout_seconds": timeout, "reason": reason})


def make(decide, advisor=None, routes=("smart-auto", "smart-anthropic")):
    log = []
    ops = Operations(decide, advisor=advisor, routes=routes, emit=log.append, sleep=lambda s: None)
    return ops, log


class OperationsTests(TestCase):
    def test_agent_switches_route_and_the_switch_sticks_for_the_run(self):
        decide = Mock(side_effect=[TransientTimeout("timed out."), "ok", "ok again"])
        advisor = Mock(return_value=decision("switch_route", "smart-anthropic", timeout=45))
        ops, log = make(decide, advisor)
        self.assertEqual(ops("r", "t", model="smart-auto", stage="Architect tool choice"), "ok")
        ops("r", "t", model="smart-auto", stage="QA")
        self.assertEqual([c.kwargs["model"] for c in decide.call_args_list],
                         ["smart-auto", "smart-anthropic", "smart-anthropic"])
        self.assertEqual(decide.call_args_list[1].kwargs["timeout"], 45)
        situation = json.loads(advisor.call_args.args[1])
        self.assertEqual(situation["stage"], "Architect tool choice")
        self.assertIn("timed out", situation["error"])
        self.assertEqual(situation["available_routes"], ["smart-auto", "smart-anthropic"])
        self.assertTrue(any("Switching smart-auto -> smart-anthropic" in line for line in log))

    def test_agent_can_give_up(self):
        decide = Mock(side_effect=RetryableAgentError("HTTP 503."))
        ops, log = make(decide, Mock(return_value=decision("give_up", reason="router outage")))
        with self.assertRaises(RetryableAgentError):
            ops("r", "t", model="smart-auto", stage="QA")
        self.assertEqual(decide.call_count, 1)
        self.assertIn("router outage", log[-1])

    def test_switch_to_an_unknown_route_is_refused(self):
        decide = Mock(side_effect=[TransientTimeout("slow."), "ok"])
        ops, _ = make(decide, Mock(return_value=decision("switch_route", "made-up")))
        ops("r", "t", model="smart-auto")
        self.assertEqual(decide.call_args_list[1].kwargs["model"], "smart-auto")

    def test_falls_back_to_default_policy_when_its_own_model_fails(self):
        decide = Mock(side_effect=[TransientTimeout("slow."), "ok"])
        ops, log = make(decide, Mock(side_effect=TransientTimeout("ops model down.")))
        self.assertEqual(ops("r", "t", model="smart-auto"), "ok")
        self.assertTrue(any("default policy" in line for line in log))

    def test_attempts_are_capped_no_matter_what_the_agent_says(self):
        decide = Mock(side_effect=TransientTimeout("slow."))
        ops, _ = make(decide, Mock(return_value=decision("retry")))
        with self.assertRaises(TransientTimeout):
            ops("r", "t", model="m")
        self.assertEqual(decide.call_count, MAX_ATTEMPTS)

    def test_non_retryable_errors_pass_straight_through(self):
        decide = Mock(side_effect=SystemExit("HTTP 401."))
        advisor = Mock()
        ops, _ = make(decide, advisor)
        with self.assertRaises(SystemExit):
            ops("r", "t", model="m")
        advisor.assert_not_called()

    def test_slow_success_can_trigger_a_proactive_reroute(self):
        times = iter([0, 90, 100, 101])
        decide = Mock(return_value="ok")
        advisor = Mock(return_value=decision("switch_route", "smart-anthropic"))
        ops = Operations(decide, advisor=advisor, routes=["smart-auto", "smart-anthropic"],
                         emit=lambda t: None, sleep=lambda s: None, clock=lambda: next(times))
        ops("r", "t", model="smart-auto")
        ops("r", "t", model="smart-auto")
        self.assertEqual(decide.call_args_list[1].kwargs["model"], "smart-anthropic")

    def test_stage_failure_decision(self):
        ops, _ = make(Mock(), Mock(return_value=decision("give_up", reason="hopeless")))
        self.assertEqual(ops.stage_failed(["QA did not return clean JSON."]), "s")
        ops, _ = make(Mock(), Mock(return_value=decision("retry")))
        self.assertEqual(ops.stage_failed(["x"]), "r")
