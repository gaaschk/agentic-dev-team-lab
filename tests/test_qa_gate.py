import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from examples.reading_list import qa_gate
from examples.reading_list.qa_reading_list import QAFailure


class GateTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.source = Path(tmp.name)
        (self.source / "readinglist.py").write_text("original\n", encoding="utf-8")
        (self.source / "store.py").write_text("storage\n", encoding="utf-8")
        self.reviewer = Mock(return_value='{"verdict": "PASS", "blockers": []}')

    def run_case(self, answer, check):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return qa_gate.run(self.source, self.reviewer, prompt=answer, check=check)

    def test_rejection_or_invalid_json_never_requests_execution(self):
        for review in (
            "PASS",
            '{"verdict":"PASS","blockers":["issue"]}',
            '{"verdict":"CHANGES_REQUIRED","blockers":[]}',
        ):
            self.reviewer.return_value = review
            prompt, check = Mock(), Mock()
            self.assertEqual(self.run_case(prompt, check), 2)
            prompt.assert_not_called()
            check.assert_not_called()

    def test_declining_prevents_execution(self):
        check = Mock()
        self.assertEqual(self.run_case(Mock(return_value="n"), check), 0)
        check.assert_not_called()

    def test_qa_gets_exact_reviewed_snapshot_even_if_original_changes(self):
        def approve(prompt):
            self.assertIn("Run sandboxed QA on candidate", prompt)
            (self.source / "readinglist.py").write_text("different\n", encoding="utf-8")
            return "y"

        def check(folder, *, sandboxed):
            self.assertTrue(sandboxed)
            self.assertNotEqual(folder, self.source)
            self.assertEqual((folder / "readinglist.py").read_text(), "original\n")
            self.assertEqual((folder / "store.py").read_text(), "storage\n")

        self.assertEqual(self.run_case(approve, check), 0)
        self.assertEqual(self.reviewer.call_args.args[3]["readinglist.py"], "original\n")

    def test_qa_failure_requires_rework(self):
        self.assertEqual(self.run_case(Mock(return_value="y"), Mock(side_effect=QAFailure("test"))), 1)

    def test_optional_empty_message_goes_to_review_and_qa(self):
        wanted = "No books yet."
        check = Mock()
        with contextlib.redirect_stdout(io.StringIO()):
            result = qa_gate.run(
                self.source, self.reviewer, prompt=Mock(return_value="y"),
                check=check, expected_empty=wanted,
            )
        self.assertEqual(result, 0)
        plan = self.reviewer.call_args.args[1]
        self.assertTrue(any(wanted in rule for rule in plan["acceptance_criteria"]))
        self.assertTrue(check.call_args.kwargs["sandboxed"])
        self.assertEqual(check.call_args.kwargs["expected_empty"], wanted)
