import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from team_agents.agent_retry import AgentRetry, RetryableAgentError
from examples.reading_list.implementer_agent import run


class ImplementerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ("readinglist.py", "store.py"):
            (self.root / name).write_text(f"source for {name}", encoding="utf-8")

    def run_with(self, replies):
        self.calls = []

        def model(role, payload, **kwargs):
            self.calls.append((json.loads(payload), kwargs))
            return json.dumps(next(replies))

        with contextlib.redirect_stdout(io.StringIO()):
            return run(self.root, decide=model)

    def test_read_then_finish(self):
        result = self.run_with(iter((
            {"action": "read_file:readinglist.py", "answer": ""},
            {"action": "finish", "answer": "Change list_books() in readinglist.py."},
        )))
        self.assertIn("list_books", result)
        first, second = (c[0] for c in self.calls)
        self.assertNotIn("finish", first["available_actions"])
        self.assertEqual(second["available_actions"], ["finish", "read_file:store.py"])
        self.assertEqual(second["observations"], {"readinglist.py": "source for readinglist.py"})

    def test_duplicate_is_rejected_by_runner(self):
        repeat = {"action": "read_file:readinglist.py", "answer": ""}
        with self.assertRaisesRegex(SystemExit, "action not allowed"):
            self.run_with(iter((repeat, repeat)))
        self.assertEqual(len(self.calls), 2)

    def test_after_both_reads_only_finish_is_offered(self):
        self.run_with(iter((
            {"action": "read_file:store.py", "answer": ""},
            {"action": "read_file:readinglist.py", "answer": ""},
            {"action": "finish", "answer": "See list_books()."},
        )))
        schema = self.calls[-1][1]["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["action"]["enum"], ["finish"])

    def test_edit_is_offered_after_target_read_and_only_once(self):
        from unittest.mock import patch
        from examples.reading_list.implementer_agent import EDIT_ACTION, NEW_MESSAGE, OLD_MESSAGE, create_edit

        original = f'def list_books(books):\n    if not books:\n        print("{OLD_MESSAGE}")\n'
        (self.root / "readinglist.py").write_text(original, encoding="utf-8")
        replies = iter((
            {"action": "read_file:readinglist.py", "answer": ""},
            {"action": EDIT_ACTION, "answer": ""},
            {"action": "finish", "answer": "Updated list_books()."},
        ))
        choices = []

        def model(role, payload, **kwargs):
            choices.append(json.loads(payload)["available_actions"])
            return json.dumps(next(replies))

        with TemporaryDirectory() as scratch, patch("examples.reading_list.implementer_agent.mkdtemp", return_value=scratch), contextlib.redirect_stdout(io.StringIO()):
            edited = create_edit(self.root, decide=model)
            self.assertEqual((edited / "readinglist.py").read_text(), original.replace(OLD_MESSAGE, NEW_MESSAGE))
            self.assertEqual((edited / "store.py").read_text(), (self.root / "store.py").read_text())
            self.assertEqual((self.root / "readinglist.py").read_text(), original)
        self.assertNotIn(EDIT_ACTION, choices[0])
        self.assertIn(EDIT_ACTION, choices[1])
        self.assertEqual(choices[2], ["finish"])

    def test_retry_after_edit_does_not_replay_edit(self):
        from unittest.mock import Mock, patch
        from examples.reading_list.implementer_agent import EDIT_ACTION

        decide = Mock(side_effect=[
            '{"action":"read_file:readinglist.py","answer":""}',
            '{"action":"' + EDIT_ACTION + '","answer":""}',
            RetryableAgentError("timed out"),
            '{"action":"finish","answer":"Done."}',
        ])
        apply = Mock(return_value="updated")
        with (patch("builtins.input", return_value="a"),
              contextlib.redirect_stdout(io.StringIO()),
              contextlib.redirect_stderr(io.StringIO())):
            self.assertEqual(run(self.root, decide=decide, apply_edit=apply, retry=AgentRetry()), "Done.")
        apply.assert_called_once_with("source for readinglist.py")
        self.assertEqual(decide.call_args_list[-2], decide.call_args_list[-1])
