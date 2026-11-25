"""Run the CrewAI adapter, with model traffic simulated locally."""

import contextlib
import importlib.util
import io
import json
import sys
from unittest import TestCase, skipUnless
from unittest.mock import patch

import plan_release
from runtimes.crewai_runtime import decide
from tests.test_plan_release import DESIGN, PLAN
from tests.test_qa_agent import QA_PLAN


@skipUnless(importlib.util.find_spec("crewai"), "optional CrewAI not installed")
class CrewAIWorkflowTests(TestCase):
    def exercise(self, approvals, *, transient=False):
        events = []
        responses = iter(approvals)
        replies = {
            "CrewAI Product Lead": json.dumps(PLAN),
            "CrewAI Architect": json.dumps(DESIGN),
            "CrewAI QA": json.dumps(QA_PLAN),
            "CrewAI Implementer": "Preview contains Final Answer: as text.",
            "CrewAI Reviewer": '{"verdict":"PASS","blockers":[]}',
        }

        def router(role, task, **options):
            self.assertEqual(options["model"], "smart-auto")
            stage = options["stage"]
            events.append(stage)
            if stage == "CrewAI Implementer" and transient and events.count(stage) == 1:
                raise plan_release.RetryableAgentError("Temporary trouble.")
            if stage == "CrewAI Reviewer":
                evidence = json.loads(task.rsplit("\n", 1)[1])
                self.assertEqual(evidence["candidate"], replies["CrewAI Implementer"])
            return replies[stage]

        def respond(prompt):
            events.append(prompt)
            return next(responses)

        with (patch.dict("os.environ", {"DEFAULT_MODEL": "smart-auto"}, clear=True),
              patch.object(sys, "argv", [
                  "plan_release.py", "--workflow-runtime", "crewai", "--example", "reading-list",
              ]),
              patch.object(plan_release, "ask_role", side_effect=router),
              patch("builtins.input", side_effect=respond),
              contextlib.redirect_stdout(io.StringIO()) as output,
              contextlib.redirect_stderr(io.StringIO())):
            try:
                plan_release.main()
            except SystemExit as exc:
                return events, output.getvalue(), str(exc)
        return events, output.getvalue(), None

    def test_crewai_flow_stops_at_declined_design(self):
        events, _, error = self.exercise(["y", "n"])
        self.assertEqual(events, [
            "CrewAI Product Lead", "Approve this plan? [y/N] ",
            "CrewAI Architect", "Approve this design? [y/N] ",
        ])
        self.assertEqual(error, "Stopped. Implementer not called.")

    def test_full_crewai_flow_and_bounded_retry(self):
        events, output, error = self.exercise(["y", "y", "r"], transient=True)
        self.assertIsNone(error)
        self.assertEqual(events[:5], [
            "CrewAI Product Lead", "Approve this plan? [y/N] ",
            "CrewAI Architect", "Approve this design? [y/N] ", "CrewAI QA",
        ])
        self.assertEqual(events[5], "CrewAI Implementer")
        self.assertIn("Retry once", events[6])
        self.assertEqual(events[7:], ["CrewAI Implementer", "CrewAI Reviewer"])
        self.assertIn('"verdict": "PASS"', output)

    def test_dependency_can_be_used_without_openai_sdk(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(decide("role", "task", transport=lambda *a, **k: "one"), "one")
