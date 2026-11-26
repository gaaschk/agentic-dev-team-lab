import contextlib
import io
import sys
from unittest import TestCase
from unittest.mock import Mock, patch

import plan_release
from examples.loan_submission import BRIEF as LOAN
from examples.reading_list import BRIEF as READING_LIST


class ReleaseCLITests(TestCase):
    def test_prompt_and_both_examples_take_same_workflow_path(self):
        for args, expected in (
            (["Build a notes app"], "Build a notes app"),
            (["--example", "loan-submission"], LOAN),
            (["--example", "reading-list"], READING_LIST),
        ):
            with self.subTest(args=args):
                engine = Mock(review_runtime="native")
                with (patch.dict(plan_release.WORKFLOW_RUNTIMES, {"native": engine}),
                      patch.object(sys, "argv", ["plan_release.py", *args])):
                    plan_release.main()
                steps, state = engine.call_args.args
                self.assertEqual(state, {"goal": expected})
                self.assertEqual([getattr(step, "name", "Approval") for step in steps], [
                    "Product Lead", "Approval", "UX Designer", "Architect", "Approval", "QA",
                        "Implementer", "Reviewer", "Security Reviewer",
                ])

    def test_requires_exactly_one_nonblank_input(self):
        for args in ([], [" "], ["prompt", "--example", "reading-list"], ["--example", "unknown"]):
            with self.subTest(args=args):
                with (patch.object(sys, "argv", ["plan_release.py", *args]),
                      contextlib.redirect_stderr(io.StringIO()),
                      self.assertRaises(SystemExit) as exc):
                    plan_release.main()
                self.assertEqual(exc.exception.code, 2)
