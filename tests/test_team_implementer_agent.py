import json
from unittest import TestCase
from unittest.mock import Mock

from team_agents.agent_retry import RetryableAgentError
from team_agents.implementer_agent import implement, is_safe_relative_path, parse_files


class SafeRelativePathTests(TestCase):
    def test_accepts_plain_relative_paths(self):
        self.assertTrue(is_safe_relative_path("app.py"))
        self.assertTrue(is_safe_relative_path("src/app.py"))

    def test_rejects_absolute_and_traversal_paths(self):
        for path in ("/etc/passwd", "../outside.py", "src/../../outside.py", "", None, 3):
            self.assertFalse(is_safe_relative_path(path))

    def test_rejects_dot_and_backslash_paths(self):
        """Regression: "." is a no-op component that write_text() can't
        handle (IsADirectoryError), and a backslash means a path separator
        to the platform Path used for writing but not to the PurePosixPath
        used here to validate — accepting one let Windows-style traversal
        like "src\\..\\escape.py" slip past this check."""
        for path in (".", "./", "src\\..\\escape.py", "src\\app.py"):
            self.assertFalse(is_safe_relative_path(path))


class ParseFilesTests(TestCase):
    def test_parses_a_valid_file_list(self):
        raw = json.dumps({"files": [{"path": "app.py", "content": "print(1)"}]})
        self.assertEqual(parse_files(raw), {"files": [{"path": "app.py", "content": "print(1)"}]})

    def test_rejects_malformed_json(self):
        with self.assertRaisesRegex(RetryableAgentError, "clean JSON"):
            parse_files("not json")

    def test_rejects_an_empty_file_list(self):
        with self.assertRaisesRegex(RetryableAgentError, "no files"):
            parse_files(json.dumps({"files": []}))

    def test_rejects_an_unsafe_path(self):
        raw = json.dumps({"files": [{"path": "../../etc/passwd", "content": "x"}]})
        with self.assertRaisesRegex(RetryableAgentError, "unsafe file path"):
            parse_files(raw)


class ImplementTests(TestCase):
    def test_returns_the_parsed_file_list(self):
        decide = Mock(return_value=json.dumps({"files": [{"path": "app.py", "content": "x"}]}))
        result = implement(None, "goal", {"plan": 1}, {"design": 1}, decide=decide)
        self.assertEqual(result, {"files": [{"path": "app.py", "content": "x"}]})
        self.assertEqual(decide.call_args.kwargs["stage"], "Implementer")
        self.assertIn("response_format", decide.call_args.kwargs)
