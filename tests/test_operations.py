import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from team_agents.agent_retry import RetryableAgentError, TransientTimeout
from team_agents.operations import FALLBACK_ATTEMPTS, MAX_FAILURES, Operations


def step(tool, path="", content="", command=""):
    return json.dumps({"tool": tool, "path": path, "content": content, "command": command,
                       "action": "keep", "route": "", "wait_seconds": 0, "timeout_seconds": 0,
                       "reason": ""})


def decision(action, route="", wait=0, timeout=60, reason="because"):
    return json.dumps({"tool": "decide", "path": "", "content": "", "command": "",
                       "action": action, "route": route, "wait_seconds": wait,
                       "timeout_seconds": timeout, "reason": reason})


def make(decide, advisor=None, routes=("smart-auto", "smart-anthropic"), **kwargs):
    log = []
    workspace = tempfile.mkdtemp()
    ops = Operations(decide, advisor=advisor, routes=routes, emit=log.append, sleep=lambda s: None,
                     workspace=workspace, shell=kwargs.pop("shell", False), **kwargs)
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
        situation = json.loads(advisor.call_args_list[0].args[1])
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

    def test_the_agent_decides_how_many_retries(self):
        decide = Mock(side_effect=[TransientTimeout("slow.")] * 6 + ["ok"])
        ops, _ = make(decide, Mock(return_value=decision("retry")))
        self.assertEqual(ops("r", "t", model="m"), "ok")
        self.assertEqual(decide.call_count, 7)

    def test_agent_sees_run_totals_when_deciding(self):
        decide = Mock(side_effect=[TransientTimeout("slow."), TransientTimeout("slow."), "ok"])
        advisor = Mock(return_value=decision("retry"))
        ops, _ = make(decide, advisor)
        ops("r", "t", model="m")
        second = json.loads(advisor.call_args_list[1].args[1])
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(second["run_totals"]["failed_calls"], 2)

    def test_run_wide_failed_call_limit_stops_an_agent_that_never_gives_up(self):
        decide = Mock(side_effect=TransientTimeout("slow."))
        ops, log = make(decide, Mock(return_value=decision("retry")))
        with self.assertRaises(TransientTimeout):
            ops("r", "t", model="m")
        self.assertEqual(decide.call_count, MAX_FAILURES)
        self.assertTrue(any("recovery limit reached" in line for line in log))
        # The limit is run-wide: a new call (a replayed stage) can't reset it.
        decide.reset_mock()
        with self.assertRaises(TransientTimeout):
            ops("r", "t", model="m")
        self.assertEqual(decide.call_count, 1)
        self.assertEqual(ops.stage_failed(["x"]), "s")

    def test_run_wide_recovery_time_limit_counts_waits(self):
        decide = Mock(side_effect=TransientTimeout("slow."))
        with patch.dict(os.environ, {"LAB_OPERATIONS_RECOVERY_SECONDS": "100"}):
            ops, _ = make(decide, Mock(return_value=decision("retry", wait=40)))
        with self.assertRaises(TransientTimeout):
            ops("r", "t", model="m")
        self.assertEqual(decide.call_count, 3)  # a 3rd 40s wait would total 120s, so no 4th request

    def test_memory_failures_leave_recovery_for_the_delivery_stages(self):
        """An advisor that always says retry must not let an optional memory
        step spend the recovery that a required stage needs."""
        now = [0.0]

        def decide(role, task, *, stage, **kwargs):
            now[0] += 30
            if stage.endswith(" memory") or decide.fail_next:
                decide.fail_next = False
                raise TransientTimeout("slow.")
            return "ok"

        advisor = Mock(return_value=decision("retry", timeout=600))
        for limit, memory_calls in (("1800", 9), ("100000", MAX_FAILURES * 15 // 100)):
            decide.fail_next = False
            spy = Mock(side_effect=decide)
            with patch.dict(os.environ, {"LAB_OPERATIONS_RECOVERY_SECONDS": limit}):
                ops, log = make(spy, advisor, clock=lambda: now[0])
            with self.assertRaises(TransientTimeout):
                ops("r", "t", model="m", stage="QA memory", timeout=90)
            self.assertEqual(spy.call_count, memory_calls)  # 270s at 30s each, or 15 failures
            self.assertEqual({c.kwargs["timeout"] for c in spy.call_args_list}, {90})
            self.assertIn("memory recovery limit reached", log[-1])
            decide.fail_next = True
            self.assertEqual(ops("r", "t", model="m", stage="Implementer"), "ok")

    def test_a_skipped_memory_wait_is_not_billed_to_the_run(self):
        slept = []
        ops, _ = make(Mock(side_effect=TransientTimeout("slow.")),
                      Mock(return_value=decision("retry", wait=120)), clock=lambda: 0.0)
        ops.sleep = slept.append
        with self.assertRaises(TransientTimeout):
            ops("r", "t", model="m", stage="QA memory")
        self.assertEqual(slept, [120, 120])  # a third wait would pass memory's 270s
        self.assertEqual((ops.recovery_seconds, ops.memory_seconds), (240, 240))

    def test_without_its_model_it_falls_back_to_a_short_fixed_policy(self):
        decide = Mock(side_effect=TransientTimeout("slow."))
        ops, _ = make(decide, Mock(side_effect=SystemExit("ops model down.")))
        with self.assertRaises(TransientTimeout):
            ops("r", "t", model="m")
        self.assertEqual(decide.call_count, FALLBACK_ATTEMPTS)

    def test_interactive_runs_never_retry_behind_the_humans_back(self):
        decide = Mock(side_effect=RetryableAgentError("HTTP 503."))
        advisor = Mock()
        ops, _ = make(decide, advisor, autonomous=False)
        with self.assertRaises(RetryableAgentError):
            ops("r", "t", model="m")
        self.assertEqual(decide.call_count, 1)
        advisor.assert_not_called()

    def test_non_retryable_errors_pass_straight_through(self):
        decide = Mock(side_effect=SystemExit("HTTP 401."))
        advisor = Mock()
        ops, _ = make(decide, advisor)
        with self.assertRaises(SystemExit):
            ops("r", "t", model="m")
        advisor.assert_not_called()

    def test_slow_success_can_trigger_a_proactive_reroute(self):
        times = iter([0, 0, 90, 100, 100, 101])
        decide = Mock(return_value="ok")
        advisor = Mock(return_value=decision("switch_route", "smart-anthropic"))
        ops = Operations(decide, advisor=advisor, routes=["smart-auto", "smart-anthropic"],
                         emit=lambda t: None, sleep=lambda s: None, clock=lambda: next(times),
                         workspace=tempfile.mkdtemp(), shell=False)
        ops("r", "t", model="smart-auto")
        ops("r", "t", model="smart-auto")
        self.assertEqual(decide.call_args_list[1].kwargs["model"], "smart-anthropic")

    def test_stage_failure_decision(self):
        ops, _ = make(Mock(), Mock(return_value=decision("give_up", reason="hopeless")))
        self.assertEqual(ops.stage_failed(["QA did not return clean JSON."]), "s")
        ops, _ = make(Mock(), Mock(return_value=decision("retry")))
        self.assertEqual(ops.stage_failed(["x"]), "r")
        ops, _ = make(Mock(), Mock(side_effect=SystemExit("down")))
        self.assertEqual(ops.stage_failed(["x"]), "s")


class OperationsToolTests(TestCase):
    def test_agent_writes_memory_then_decides(self):
        decide = Mock(side_effect=[TransientTimeout("slow."), "ok"])
        advisor = Mock(side_effect=[
            step("read_file", path="memory.json"),
            decision("retry"),
            step("write_file", path="memory.json", content='{"smart-auto": "retry worked"}'),
            decision("keep"),
        ])
        ops, _ = make(decide, advisor)
        ops("r", "t", model="smart-auto", stage="QA")
        self.assertIn("retry worked", (ops.workspace / "memory.json").read_text())
        observations = json.loads(advisor.call_args_list[1].args[1])["observations"]
        self.assertTrue(observations[0]["result"].startswith("error"))

    def test_memory_survives_across_runs_in_the_same_workspace(self):
        workspace = tempfile.mkdtemp()
        Operations(Mock(), workspace=workspace).use_tool(
            {"tool": "write_file", "path": "notes/routes.md", "content": "anthropic is fast"})
        ops = Operations(Mock(), workspace=workspace)
        self.assertEqual(ops.use_tool({"tool": "read_file", "path": "notes/routes.md"}),
                         "anthropic is fast")

    def test_file_tools_cannot_leave_the_workspace(self):
        ops, _ = make(Mock())
        for path in ("../escape.txt", "/etc/passwd"):
            result = ops.use_tool({"tool": "write_file", "path": path, "content": "x"})
            self.assertTrue(result.startswith("error"))
        self.assertFalse((Path(ops.workspace).parent / "escape.txt").exists())

    def test_shell_is_sandboxed_by_default_and_the_host_shell_is_opt_in(self):
        for setting, works, mode in ((None, True, "sandbox"), (None, False, False),
                                     ("1", False, False), ("0", True, False),
                                     ("host", False, "host")):
            env = {} if setting is None else {"LAB_AGENT_SHELL": setting}
            with patch.dict(os.environ, env, clear=True), \
                 patch("team_agents.sandbox.available", return_value=works):
                self.assertEqual(make(Mock(), shell=None)[0].bench.shell, mode, (setting, works))
        ops, _ = make(Mock())
        self.assertIn("disabled", ops.use_tool({"tool": "run_command", "command": "echo hi"}))
        ops, _ = make(Mock(), shell="host")
        self.assertEqual(ops.use_tool({"tool": "run_command", "command": "echo hi"}), "exit 0\nhi\n")

    def test_shell_commands_do_not_receive_credentials(self):
        ops, _ = make(Mock(), shell="host")
        with patch.dict(os.environ, {"SMART_ROUTER_API_KEY": "sentinel"}):
            result = ops.use_tool({"tool": "run_command", "command": "echo [$SMART_ROUTER_API_KEY]"})
        self.assertEqual(result, "exit 0\n[]\n")

    def test_tool_loop_is_capped(self):
        advisor = Mock(return_value=step("list_files"))
        ops, log = make(Mock(), advisor)
        self.assertIsNone(ops.think({"situation": "call_failed"}))
        self.assertTrue(any("No decision after" in line for line in log))

    def test_its_own_calls_avoid_a_route_it_switched_away_from(self):
        decide = Mock(side_effect=[TransientTimeout("slow."), "ok"])
        advisor = Mock(return_value=decision("switch_route", "smart-anthropic"))
        ops, _ = make(decide, advisor)
        with patch.dict(os.environ, {"LAB_OPERATIONS_MODEL": "smart-auto"}):
            ops("r", "t", model="smart-auto")
        self.assertEqual(advisor.call_args_list[-1].kwargs["model"], "smart-anthropic")

    def test_stage_failures_with_successful_calls_still_hit_the_run_wide_limit(self):
        """Regression: invalid output fails a stage without any failed call,
        so stage replays used to bypass the run-wide limit entirely."""
        from team_agents.operations import MAX_FAILURES
        ops, log = make(Mock(), Mock(return_value=decision("retry")))
        choices = [ops.stage_failed(["QA did not return clean JSON."]) for _ in range(MAX_FAILURES)]
        self.assertEqual(choices[:-1], ["r"] * (MAX_FAILURES - 1))
        self.assertEqual(choices[-1], "s")
        self.assertTrue(any("recovery limit reached" in line for line in log))

    def test_a_wait_that_uses_up_the_recovery_time_starts_no_new_request(self):
        decide = Mock(side_effect=TransientTimeout("slow."))
        slept = []
        with patch.dict(os.environ, {"LAB_OPERATIONS_RECOVERY_SECONDS": "100"}):
            ops, log = make(decide, Mock(return_value=decision("retry", wait=100)))
        ops.sleep = slept.append
        with self.assertRaises(TransientTimeout):
            ops("r", "t", model="m")
        self.assertEqual(decide.call_count, 1)
        self.assertEqual(slept, [])
        self.assertTrue(any("would reach the run-wide recovery limit" in line for line in log))

    def test_memory_steps_are_flagged_optional_and_keep_their_timeout(self):
        decide = Mock(side_effect=[TransientTimeout("slow."), "ok"])
        advisor = Mock(return_value=decision("switch_route", "smart-anthropic", timeout=45))
        ops, _ = make(decide, advisor)
        ops("r", "t", model="smart-auto", stage="Architect memory", timeout=90)
        self.assertEqual(decide.call_args_list[0].kwargs["timeout"], 90)
        self.assertEqual(decide.call_args_list[1].kwargs["timeout"], 45)
        self.assertTrue(json.loads(advisor.call_args_list[0].args[1])["optional_step"])
