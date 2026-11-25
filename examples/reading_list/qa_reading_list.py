"""Black-box acceptance test for the lab's interactive reading-list contract."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

SANDBOX = Path("/usr/bin/sandbox-exec")
PROFILE = """(version 1)
(deny default)
(allow file-read-metadata)
(allow file-read* (literal "/") (subpath "/System") (subpath "/usr") (subpath "/opt/homebrew")
                 (subpath "/Library/Apple") (subpath "/Library/Developer")
                 (subpath (param "QA_SOURCE")) (subpath (param "QA_DATA"))
                 (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))
(allow file-map-executable (subpath "/System") (subpath "/usr")
                           (subpath "/opt/homebrew") (subpath "/Library/Apple")
                           (subpath "/Library/Developer"))
(allow mach-lookup)
(allow sysctl-read)
(allow process-exec (literal (param "QA_PYTHON")))
(allow file-write* (subpath (param "QA_DATA")))
(deny network*)
"""


class QAFailure(Exception):
    pass


def check(candidate, *, sandboxed=False, expected_empty=None):
    candidate = Path(candidate).resolve()
    if sandboxed and not SANDBOX.is_file():
        raise QAFailure("OS sandbox unavailable; candidate was not run")
    with TemporaryDirectory(prefix="readinglist-data-") as d:
        data = str(Path(d).resolve())
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": data,
            "TMPDIR": data,
            "READINGLIST_FILE": str(Path(data) / "books.json"),
            "PYTHONIOENCODING": "utf-8",
        }
        interpreter = Path(sys.executable).resolve()
        if sandboxed:
            embedded = interpreter.parent.parent / "Resources/Python.app/Contents/MacOS/Python"
            if embedded.is_file():
                interpreter = embedded.resolve()
        python = str(interpreter)
        command = [python, "-B", "-S", str(candidate / "readinglist.py")]
        if sandboxed:
            command = [
                str(SANDBOX), "-p", PROFILE, "-D", f"QA_SOURCE={candidate}",
                "-D", f"QA_DATA={data}", "-D", f"QA_PYTHON={python}", *command,
            ]

        def run_app(choices):
            try:
                result = subprocess.run(
                    command, cwd=candidate, env=env, input=choices, encoding="utf-8",
                    capture_output=True, errors="replace", timeout=10,
                )
            except subprocess.TimeoutExpired:
                raise QAFailure("app timed out") from None
            except OSError as exc:
                raise QAFailure(f"could not start app ({type(exc).__name__})") from None
            if result.returncode != 0:
                # Candidate output is untrusted; escape terminal control codes.
                raise QAFailure(f"app exited {result.returncode}: {result.stderr[:350]!r}")
            return result.stdout

        if expected_empty is not None and expected_empty not in run_app("2\n3\n"):
            raise QAFailure("new empty-list message missing")
        if "Book added." not in run_app("1\nDune\nFrank Herbert\n3\n"):
            raise QAFailure("add did not confirm the save")
        if "1. Dune — Frank Herbert" not in run_app("2\n3\n"):
            raise QAFailure("book missing after restart")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 -B -m examples.reading_list.qa_reading_list <candidate folder>")
    try:
        check(sys.argv[1])
    except QAFailure as exc:
        raise SystemExit(f"QA FAIL: {exc}") from None
    print("QA PASS: add, list, and restart persistence")
