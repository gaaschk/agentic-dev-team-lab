"""Review and QA an explicitly selected snapshot of the reading-list candidate."""

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory

from team_agents import ROLES_DIR
from . import qa_reading_list as qa

FILES = ("readinglist.py", "store.py")
PLAN = {
    "in_scope": ["Add a book by title and author", "List saved books", "Persist across restarts"],
    "out_of_scope": ["Search", "Removal", "ISBN", "Read status"],
    "acceptance_criteria": [
        "An interactive menu adds a book with its title and author",
        "The list action displays saved titles and authors",
        "A new process reads and displays books saved by a previous process",
    ],
}
DESIGN = {
    "interface": "Run readinglist.py; menu 1 adds, 2 lists, 3 exits.",
    "persistence": "The app uses a local JSON store. READINGLIST_FILE overrides the path for QA.",
}


def read_candidate(folder):
    sources = {}
    for name in FILES:
        fd = os.open(folder / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise ValueError(f"{name} is not a regular file")
            raw = file.read(32769)
        if len(raw) > 32768:
            raise ValueError(f"{name} exceeds the lab file limit")
        sources[name] = raw.decode("utf-8")
    return sources


def run(folder, reviewer, *, prompt=input, check=qa.check, expected_empty=None):
    try:
        sources = read_candidate(folder)
    except (OSError, ValueError) as exc:
        print(f"QA BLOCKED: could not load candidate: {exc}", file=sys.stderr)
        return 2

    fingerprint = hashlib.sha256(
        json.dumps(sources, sort_keys=True, ensure_ascii=True).encode("ascii")
    ).hexdigest()
    role = (ROLES_DIR / "reviewer.md").read_text(encoding="utf-8")
    print(f"Candidate {fingerprint[:12]}: {folder} ({', '.join(FILES)})", flush=True)
    plan = PLAN if expected_empty is None else {
        **PLAN,
        "acceptance_criteria": [
            *PLAN["acceptance_criteria"],
            f"Listing an empty reading list displays: {expected_empty}",
        ],
    }
    raw = reviewer(role, plan, DESIGN, dict(sources))
    try:
        review = json.loads(raw)
    except (ValueError, TypeError):
        print("QA BLOCKED: Reviewer did not return clean JSON. No code ran.", file=sys.stderr)
        return 2

    if review != {"verdict": "PASS", "blockers": []}:
        print("QA BLOCKED: Reviewer did not return PASS with no blockers. No code ran.", file=sys.stderr)
        print(json.dumps(review, indent=2, ensure_ascii=True))
        return 2

    if expected_empty is None:
        print("Reviewer PASS. QA will test add, list, and restart persistence only.")
    else:
        print("Reviewer PASS. QA will test the new empty message and restart persistence.")
    print("Execution uses a private copy, fresh data, and the macOS sandbox.")
    choice = prompt(f"Run sandboxed QA on candidate {fingerprint[:12]}? [y/N] ")
    if choice.strip().lower() != "y":
        print("Stopped. Candidate was not run.")
        return 0

    with TemporaryDirectory(prefix="readinglist-candidate-") as temp:
        snapshot = Path(temp)
        for name, code in sources.items():
            (snapshot / name).write_bytes(code.encode("utf-8"))
        try:
            if expected_empty is None:
                check(snapshot, sandboxed=True)
            else:
                check(snapshot, sandboxed=True, expected_empty=expected_empty)
        except qa.QAFailure as exc:
            print(f"QA FAIL: {exc}. Candidate needs rework.", file=sys.stderr)
            return 1

    tested = "add, list, and restart persistence"
    if expected_empty is not None:
        tested = "new empty message, plus " + tested
    print(f"QA PASS: {tested}. Ready for human review.")
    return 0


def main():
    import argparse
    from functools import partial

    from team_agents.agent_retry import AgentRetry
    from plan_release import ask_role
    from team_agents.reviewer_agent import ask_agent_reviewer, ask_reviewer, with_review_retry

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", action="store_true", help="use the artifact-reading Reviewer")
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    reviewer = ask_agent_reviewer if args.agents else ask_reviewer
    return run(args.candidate.expanduser().resolve(), with_review_retry(partial(reviewer, decide=ask_role), AgentRetry()))


if __name__ == "__main__":
    raise SystemExit(main())
