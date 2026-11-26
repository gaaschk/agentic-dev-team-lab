import asyncio
import contextlib
import importlib.util
import io
import json
import sys
from unittest import TestCase, skipUnless
from unittest.mock import Mock, patch

import plan_release
from team_agents.reviewer_agent import REVIEW_FORMAT, ask_agent_reviewer, parse_review, review_artifacts
from runtimes import select_reviewer
from runtimes.openai_runtime import run_agent, run_tool_artifacts
from team_agents.agent_retry import RetryableAgentError


class RuntimeSelectionTests(TestCase):
    def test_all_runtimes_use_the_same_reviewer_role(self):
        native = select_reviewer("native", decide=Mock())
        openai = select_reviewer("openai", decide=Mock())
        crewai = select_reviewer("crewai", decide=Mock())
        self.assertTrue(all(reviewer.func is ask_agent_reviewer for reviewer in (native, openai, crewai)))
        self.assertIs(openai.keywords["artifact_runner"], run_tool_artifacts)

    def test_cli_uses_reviewer_associated_with_workflow_runtime(self):
        engine = Mock(review_runtime="openai")
        args = ["plan_release.py", "--workflow-runtime", "test-engine", "Example goal"]
        with (patch.dict(plan_release.WORKFLOW_RUNTIMES, {"test-engine": engine}),
              patch.object(sys, "argv", args),
              patch.object(plan_release, "select_reviewer", wraps=select_reviewer) as selector):
            plan_release.main()
        selector.assert_called_once_with("openai", decide=plan_release.ask_role)
        engine.assert_called_once()
        self.assertEqual(engine.call_args.args[1], {"goal": "Example goal"})
        self.assertEqual(engine.call_args.args[0][-1].name, "Security Reviewer")


@skipUnless(importlib.util.find_spec("agents"), "optional OpenAI Agents SDK not installed")
class OpenAIRuntimeTests(TestCase):
    def simulate(self, actions, *, echo=True):
        import httpx

        sent = []
        request_ids = set()

        def router(request):
            self.assertEqual(str(request.url), "https://router.example/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer synthetic-key")
            self.assertEqual(request.headers["X-Smart-Router-Memory"], "off")
            request_id = request.headers["X-Request-ID"]
            self.assertNotIn(request_id, request_ids)
            request_ids.add(request_id)
            data = json.loads(request.content)
            self.assertEqual(data["model"], "mock-route")
            self.assertFalse(data["agent"])
            self.assertFalse(data.get("stream", False))
            self.assertEqual(data["response_format"]["type"], "json_schema")
            sent.append(data)
            action = actions[len(sent) - 1]
            message = {"role": "assistant", "content": None}
            if isinstance(action, str):
                message["tool_calls"] = [{
                    "id": f"call_{len(sent)}", "type": "function",
                    "function": {"name": action, "arguments": "{}"},
                }]
            else:
                message["content"] = json.dumps(action)
            return httpx.Response(200, headers={
                "X-Request-ID": request_id, "X-Smart-Router-Memory": "off" if echo else "on",
            }, json={
                "id": "chatcmpl-test", "object": "chat.completion", "created": 1, "model": "mock-route",
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if isinstance(action, str) else "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        env = {
            "SMART_ROUTER_URL": "https://router.example/v1",
            "SMART_ROUTER_API_KEY": "synthetic-key",
            "DEFAULT_MODEL": "mock-route",
        }
        with (patch.dict("os.environ", env, clear=True),
              contextlib.redirect_stdout(io.StringIO()),
              contextlib.redirect_stderr(io.StringIO())):
            result = asyncio.run(run_agent(
                "Be a reviewer.", "Review this exact candidate.", stage="Reviewer", model="mock-route",
                response_format=REVIEW_FORMAT,
                artifacts=review_artifacts({"criteria": ["read"]}, {"interface": "CLI"}, "EXACT CANDIDATE"),
                required={"criteria", "candidate"}, transport=httpx.MockTransport(router),
            ))
        return parse_review(result), sent

    def test_real_sdk_reads_both_inputs_and_returns_verdict(self):
        for verdict in (
            {"verdict": "PASS", "blockers": []},
            {"verdict": "CHANGES_REQUIRED", "blockers": [
                {"file": "fixture", "evidence": "Missing import", "consequence": "NameError"}
            ]},
        ):
            result, sent = self.simulate(["read_criteria", "read_candidate", verdict])
            self.assertEqual(result, verdict)
            self.assertNotIn("EXACT CANDIDATE", json.dumps(sent[0]))
            self.assertIn("EXACT CANDIDATE", json.dumps(sent[-1]))
            first_tools = {t["function"]["name"] for t in sent[0]["tools"]}
            self.assertEqual(first_tools, {"read_criteria", "read_candidate"})
            self.assertEqual([t["function"]["name"] for t in sent[1]["tools"]], ["read_candidate"])
            self.assertFalse(sent[-1].get("tools"))

    def test_early_verdict_is_not_accepted(self):
        with self.assertRaisesRegex(RetryableAgentError, "did not read required artifacts"):
            self.simulate([{"verdict": "PASS", "blockers": []}])

    def test_invalid_structured_output_is_retryable(self):
        with self.assertRaises(RetryableAgentError):
            self.simulate(["read_criteria", "read_candidate", {"verdict": "INVALID"}])

    def test_router_must_confirm_memory_off(self):
        with self.assertRaisesRegex(SystemExit, "did not confirm memory"):
            self.simulate(["read_criteria"], echo=False)


class ComparisonFixtureTests(TestCase):
    def test_only_negative_candidate_lacks_json_import(self):
        import ast
        from examples.reading_list.review_fixtures import BROKEN, CLEAN

        self.assertEqual(BROKEN, CLEAN.replace("import json\n", "", 1))
        for candidate in (CLEAN, BROKEN):
            names = {node.name for node in ast.parse(candidate).body if isinstance(node, ast.FunctionDef)}
            self.assertEqual(names, {"main"})
