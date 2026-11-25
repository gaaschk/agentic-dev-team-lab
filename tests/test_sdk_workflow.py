"""Exercise the entire CLI with the real SDK and a deterministic HTTP fixture."""

import contextlib
import importlib.util
import io
import json
import sys
from unittest import TestCase, skipUnless
from unittest.mock import patch

import plan_release
from runtimes import openai_runtime
from tests.test_plan_release import DESIGN, PLAN
from tests.test_qa_agent import QA_PLAN


@skipUnless(importlib.util.find_spec("agents"), "optional OpenAI Agents SDK not installed")
class SDKWorkflowTests(TestCase):
    def exercise(self, approvals):
        import httpx

        requests = []
        events = []
        choices = iter(approvals)

        def answer(prompt):
            self.assertIn("Approve", prompt)
            events.append("approve plan" if "this plan" in prompt else "approve design")
            return next(choices)

        def router(request):
            self.assertEqual(str(request.url), "https://router.example/v1/chat/completions")
            self.assertEqual(request.headers["X-Smart-Router-Memory"], "off")
            data = json.loads(request.content)
            self.assertEqual(data["model"], "smart-auto")
            self.assertFalse(data["agent"])
            requests.append(data)
            fields = data.get("response_format", {}).get("json_schema", {}).get("schema", {}).get("properties", {})
            name = next((label for field, label in (
                ("release_goal", "release_plan"), ("components", "technical_design"),
                ("checks", "qa_test_plan"), ("verdict", "review_verdict"),
            ) if field in fields), "implementer")
            events.append(name)
            tools = [item["function"]["name"] for item in (data.get("tools") or [])]
            if name == "review_verdict" and tools:
                selected = tools[0]
                message = {"role": "assistant", "tool_calls": [{
                    "id": f"call_{len(requests)}", "type": "function",
                    "function": {"name": selected, "arguments": "{}"},
                }]}
            else:
                payload = {
                    "release_plan": json.dumps(PLAN),
                    "technical_design": json.dumps(DESIGN),
                    "qa_test_plan": json.dumps(QA_PLAN),
                    "implementer": "Candidate preview, not executable by the runner.",
                    "review_verdict": '{"verdict":"PASS","blockers":[]}',
                }[name]
                message = {"role": "assistant", "content": payload}
            return httpx.Response(200, headers={
                "X-Request-ID": request.headers["X-Request-ID"], "X-Smart-Router-Memory": "off",
            }, json={
                "id": "chatcmpl-test", "object": "chat.completion", "created": 1, "model": "smart-auto",
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if "tool_calls" in message else "stop"}],
            })

        real_run = openai_runtime.run_agent

        async def call(*args, **kwargs):
            return await real_run(*args, **{**kwargs, "transport": httpx.MockTransport(router)})

        with (patch.dict("os.environ", {
                  "SMART_ROUTER_URL": "https://router.example", "SMART_ROUTER_API_KEY": "test",
                  "DEFAULT_MODEL": "smart-auto",
              }, clear=True),
              patch.object(sys, "argv", ["plan_release.py", "--workflow-runtime", "openai",
                                         "--example", "loan-submission"]),
              patch.object(openai_runtime, "run_agent", call),
              patch.object(plan_release, "ask_role", side_effect=AssertionError("Native path called")),
              patch("builtins.input", side_effect=answer),
              contextlib.redirect_stdout(io.StringIO()) as output,
              contextlib.redirect_stderr(io.StringIO())):
            try:
                plan_release.main()
            except SystemExit as exc:
                return events, requests, output.getvalue(), str(exc)
        return events, requests, output.getvalue(), None

    def test_all_five_sdk_roles_use_smart_auto(self):
        events, requests, output, error = self.exercise(["y", "y"])
        self.assertIsNone(error)
        self.assertEqual(events, [
            "release_plan", "approve plan", "technical_design", "approve design",
            "qa_test_plan", "implementer", "review_verdict", "review_verdict", "review_verdict",
        ])
        self.assertNotIn("Candidate preview", json.dumps(requests[2]))
        self.assertIn("Candidate preview", json.dumps(requests[-1]))
        self.assertIn("No tests have run", output)
        self.assertIn('"verdict": "PASS"', output)

    def test_declining_design_stops_before_qa_and_implementer(self):
        events, _, _, error = self.exercise(["y", "n"])
        self.assertEqual(events, ["release_plan", "approve plan", "technical_design", "approve design"])
        self.assertEqual(error, "Stopped. Implementer not called.")
