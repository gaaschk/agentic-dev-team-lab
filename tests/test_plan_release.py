import contextlib
import io
import json
import sys
import unittest
from unittest.mock import Mock, patch

import plan_release


PLAN = {
    "release_goal": "A persistent reading list.",
    "in_scope": ["Add and list."],
    "out_of_scope": [],
    "acceptance_criteria": ["See a saved book after restarting."],
    "open_questions": [],
}

DESIGN = {
    "components": ["CLI and storage"],
    "persistence": "Use a JSON file.",
    "interface": "Support add and list.",
    "build_order": ["Build storage first."],
    "verification": ["Run add and list in separate processes."],
    "risks": [],
    "blockers": [],
}

PROPOSAL = {"files": [{"path": "app.py", "content": "print('hello')"}]}


class Response(io.StringIO):
    def __init__(self, memory_mode):
        super().__init__(json.dumps({"choices": [{"message": {"content": "Answer"}}]}))
        self.headers = {"X-Smart-Router-Memory": memory_mode}


class ReleaseTests(unittest.TestCase):
    def run_workflow(self, replies, approvals):
        call = Mock(side_effect=replies)

        def respond(role, task, **options):
            stage = options["stage"]
            if stage.endswith("tool choice"):
                action = {
                    "Product Lead tool choice": "read:brief",
                    "Architect tool choice": "read:approved_plan",
                    "QA tool choice": "read:requirements",
                    "Reviewer tool choice": "read:criteria",
                }[stage]
                if "submit" in json.loads(task)["available_actions"]:
                    action = "submit"
                return json.dumps({"action": action})
            if stage == "QA":
                return '{"checks":[{"criterion_id":1,"name":"Restart","setup":"Fresh data","action":"Save and restart","expected":"Book appears"}]}'
            return call(role, task, **options)

        with (
            patch.object(sys, "argv", ["plan_release.py", "Build a reading list"]),
            patch.object(plan_release, "ask_role", side_effect=respond),
            patch("builtins.input", side_effect=approvals) as prompt,
            contextlib.redirect_stderr(io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            try:
                plan_release.main()
            except SystemExit as exc:
                return str(exc), call.call_args_list, prompt.call_count, output.getvalue()
            return None, call.call_args_list, prompt.call_count, output.getvalue()

    def test_both_approvals_are_required(self):
        for replies, approvals, call_count in [
            ([json.dumps(PLAN)], ["n"], 1),
            ([json.dumps(PLAN), json.dumps(DESIGN)], ["y", "n"], 2),
        ]:
            result, calls, prompts, _ = self.run_workflow(replies, approvals)
            self.assertIn("not called", result)
            self.assertEqual(len(calls), call_count)
            self.assertEqual(prompts, len(approvals))

    def test_valid_approved_plan_and_design_reach_review(self):
        result, calls, prompts, output = self.run_workflow(
            [json.dumps(PLAN), json.dumps(DESIGN), json.dumps(PROPOSAL), '{"verdict":"PASS","blockers":[]}'],
            ["y", "y"],
        )
        self.assertIsNone(result)
        self.assertEqual(prompts, 2)
        self.assertEqual(len(calls), 4)
        sent = json.loads(calls[2].args[1].split("\n\n", 1)[1])
        self.assertEqual(sent["approved_plan"], PLAN)
        self.assertEqual(sent["approved_design"], DESIGN)
        reviewed = json.loads(calls[3].args[1].split("\n\n", 1)[1])
        self.assertEqual(reviewed["criteria"], {"plan": PLAN, "design": DESIGN})
        self.assertEqual(reviewed["candidate"], PROPOSAL)
        self.assertIn("app.py", output)
        self.assertIn("PASS", output)

    def test_invalid_design_never_reaches_design_approval(self):
        result, calls, prompts, _ = self.run_workflow(
            [json.dumps(PLAN), "A recap of the product requirements"], ["y", "n"]
        )
        self.assertIn("Implementer not called", result)
        self.assertEqual(prompts, 2)
        self.assertEqual(len(calls), 2)

    def test_invalid_architect_can_retry_same_plan_once(self):
        empty = json.dumps({**DESIGN, "persistence": ""})
        result, calls, prompts, _ = self.run_workflow(
            [json.dumps(PLAN), empty, json.dumps(DESIGN)], ["y", "y", "n"]
        )
        self.assertIn("Implementer not called", result)
        self.assertEqual(prompts, 3)
        self.assertEqual(len(calls), 3)
        first, retry = (call.args[1].split("\n\n", 1) for call in calls[1:])
        self.assertEqual(json.loads(first[1]), json.loads(retry[1]))
        self.assertIn("empty persistence", retry[0])

    def test_second_invalid_architect_stops_without_another_retry(self):
        empty = json.dumps({**DESIGN, "persistence": ""})
        result, calls, prompts, _ = self.run_workflow(
            [json.dumps(PLAN), empty, empty], ["y", "y"]
        )
        self.assertIn("empty persistence", result)
        self.assertEqual(prompts, 2)
        self.assertEqual(len(calls), 3)

    def test_architect_blocker_stops_the_handoff(self):
        result, calls, prompts, _ = self.run_workflow(
            [json.dumps(PLAN), json.dumps({**DESIGN, "persistence": "", "blockers": ["Need a decision."]})],
            ["y"],
        )
        self.assertIn("Need a decision", result)
        self.assertEqual(prompts, 1)
        self.assertEqual(len(calls), 2)

    def test_retry_all_is_per_run_and_does_not_replace_approvals(self):
        good_review = '{"verdict":"PASS","blockers":[]}'
        result, calls, prompts, output = self.run_workflow(
            ["broken", json.dumps(PLAN),
             plan_release.RetryableAgentError("timed out"), json.dumps(DESIGN),
             None, json.dumps(PROPOSAL), "not JSON", good_review],
            ["a", "y", "y"],
        )
        self.assertIsNone(result)
        self.assertEqual(len(calls), 8)
        self.assertEqual(prompts, 3)
        self.assertIn("PASS", output)

        result, calls, prompts, _ = self.run_workflow(["broken"], [""])
        self.assertIn("not retried", result)
        self.assertEqual((len(calls), prompts), (1, 1))

    def test_retry_once_does_not_select_retry_all(self):
        result, calls, prompts, _ = self.run_workflow(
            ["broken", json.dumps(PLAN), "broken"], ["r", "y", "s"]
        )
        self.assertIn("Implementer not called", result)
        self.assertEqual((len(calls), prompts), (3, 3))

    def test_retry_all_never_retries_a_real_review_blocker(self):
        verdict = json.dumps({"verdict": "CHANGES_REQUIRED", "blockers": [
            {"file": "app.py", "evidence": "No save call.", "consequence": "Data is lost."}
        ]})
        result, calls, prompts, output = self.run_workflow(
            ["broken", json.dumps(PLAN), json.dumps(DESIGN), json.dumps(PROPOSAL), verdict], ["a", "y", "y"]
        )
        self.assertIsNone(result)
        self.assertEqual((len(calls), prompts), (5, 3))
        self.assertIn("CHANGES_REQUIRED", output)
        self.assertIn("Data is lost.", output)

    def test_retry_all_still_stops_after_one_failed_retry(self):
        result, calls, prompts, _ = self.run_workflow(["broken", "still broken"], ["a"])
        self.assertIn("invalid JSON", result)
        self.assertEqual((len(calls), prompts), (2, 1))

    def test_nonretryable_failure_never_prompts(self):
        result, calls, prompts, _ = self.run_workflow(
            [SystemExit("router did not confirm memory is off")], []
        )
        self.assertIn("memory is off", result)
        self.assertEqual((len(calls), prompts), (1, 0))

    def test_request_checks_effective_memory_mode(self):
        env = {"SMART_ROUTER_URL": "https://example.invalid", "SMART_ROUTER_API_KEY": "test"}
        with patch.dict("os.environ", env), patch.object(plan_release, "build_opener") as build:
            open_request = build.return_value.open
            open_request.return_value = Response("on")
            with self.assertRaisesRegex(SystemExit, "did not confirm memory is off"):
                plan_release.ask_role("Role", "Goal")
            sent = open_request.call_args.args[0]
            self.assertEqual(sent.get_header("X-smart-router-memory"), "off")
            def echo(request, timeout):
                response = Response("off")
                response.headers["X-Request-ID"] = request.get_header("X-request-id")
                return response

            open_request.side_effect = echo
            with contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(plan_release.ask_role("Role", "Goal"), "Answer")
            self.assertIn("request ID:", output.getvalue())

    def test_null_content_is_a_retryable_failure(self):
        """Regression: some routes return content=None; callers that parse
        JSON crashed on it instead of treating it as a failed call."""
        from team_agents.agent_retry import RetryableAgentError
        env = {"SMART_ROUTER_URL": "https://example.invalid", "SMART_ROUTER_API_KEY": "test"}
        with patch.dict("os.environ", env), patch.object(plan_release, "build_opener") as build:
            def empty(request, timeout):
                response = Response("off")
                response.seek(0)
                response.truncate()
                response.write(json.dumps({"choices": [{"message": {"content": None}}]}))
                response.seek(0)
                response.headers["X-Request-ID"] = request.get_header("X-request-id")
                return response

            build.return_value.open.side_effect = empty
            with self.assertRaisesRegex(RetryableAgentError, "no text content"):
                plan_release.ask_role("Role", "Goal", stage="Operations")

    def test_request_failure_exposes_kind_stage_and_id(self):
        from urllib.error import HTTPError, URLError
        from uuid import UUID

        env = {"SMART_ROUTER_URL": "https://example.invalid", "SMART_ROUTER_API_KEY": "test"}
        with patch.dict("os.environ", env), patch.object(plan_release, "build_opener") as build:
            for error, expected in (
                (TimeoutError(), "request timed out"),
                (URLError(ConnectionRefusedError()), "connection failed (ConnectionRefusedError)"),
                (HTTPError("https://example.invalid", 503, "private sentinel", {}, None), "HTTP 503"),
            ):
                open_request = build.return_value.open
                open_request.side_effect = error
                with self.assertRaises(SystemExit) as exc:
                    plan_release.ask_role("Role", "Goal", stage="Implementer")
                request_id = open_request.call_args.args[0].get_header("X-request-id")
                UUID(request_id)
                diagnostic = str(exc.exception)
                self.assertIn("Implementer", diagnostic)
                self.assertIn(request_id, diagnostic)
                self.assertIn(expected, diagnostic)
                self.assertNotIn("private sentinel", diagnostic)


if __name__ == "__main__":
    unittest.main()
