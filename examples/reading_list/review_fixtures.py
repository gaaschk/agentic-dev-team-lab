"""Live Reviewer check with fixed reading-list candidates. Never runs candidate code."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from team_agents import ROLES_DIR
from plan_release import ask_role
from team_agents.reviewer_agent import parse_review
from runtimes import REVIEW_RUNTIMES, select_reviewer

FILE = "reading_list.py"
PLAN = {
    "in_scope": ["Add books by title and author", "List saved books", "Persist across restarts"],
    "out_of_scope": ["Search", "Removal", "ISBN", "Read status"],
    "acceptance_criteria": [
        "add TITLE AUTHOR stores a book",
        "list prints the saved title and author, including in a fresh process",
    ],
}
DESIGN = {"interface": "add TITLE AUTHOR and list", "persistence": "books.json in the working directory"}

CLEAN = '''import argparse
import json
from pathlib import Path

def main():
    cli = argparse.ArgumentParser()
    commands = cli.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("title")
    add.add_argument("author")
    commands.add_parser("list")
    args = cli.parse_args()

    path = Path("books.json")
    if path.exists():
        with path.open(encoding="utf-8") as source:
            books = json.load(source)
    else:
        books = []

    if args.command == "add":
        books.append({"title": args.title, "author": args.author})
        path.write_text(json.dumps(books), encoding="utf-8")
    else:
        for book in books:
            print(book["title"] + " by " + book["author"])

if __name__ == "__main__":
    main()
'''
BROKEN = CLEAN.replace("import json\n", "", 1)

def grade(result, *, broken):
    if not broken:
        return result["verdict"] == "PASS" and not result["blockers"]
    if result["verdict"] != "CHANGES_REQUIRED" or len(result["blockers"]) != 1:
        return False
    blocker = result["blockers"][0]
    evidence = blocker["evidence"].casefold()
    consequence = blocker["consequence"].casefold()
    return (
        blocker["file"] == FILE
        and "json" in evidence
        and ("import" in evidence or "undefined" in evidence or "not defined" in evidence)
        and ("nameerror" in consequence or "not defined" in consequence or "undefined" in consequence)
    )


def compare_one(name, *, loan=False):
    reviewer = select_reviewer(name, decide=ask_role)
    role = (ROLES_DIR / "reviewer.md").read_text(encoding="utf-8")
    cases = [
        ("Eligible candidate", PLAN, DESIGN, {FILE: CLEAN}, partial(grade, broken=False)),
        ("Missing-import candidate", PLAN, DESIGN, {FILE: BROKEN}, partial(grade, broken=True)),
    ]
    if loan:
        from examples.loan_submission import review_fixture as regression
        cases = [(
            "Loan draft mistaken for a replay", regression.PLAN, regression.DESIGN,
            {regression.FILE: regression.CANDIDATE}, regression.detected,
        )]
    passed = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reviewer, role, plan, design, source) for _, plan, design, source, _ in cases]
        for (label, _, _, _, check), future in zip(cases, futures):
            try:
                result = parse_review(future.result())
                ok = check(result)
                print(f"{label}: {result['verdict']} ({'as expected' if ok else 'unexpected'})")
                for blocker in result["blockers"]:
                    print(f"  {blocker['file']}: {blocker['evidence']} {blocker['consequence']}")
            except (SystemExit, ValueError) as exc:
                ok = False
                print(f"{label}: {exc}")
            passed = passed and ok
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", choices=(*REVIEW_RUNTIMES, "both"), default="native")
    parser.add_argument("--loan-regression", action="store_true",
                        help="review the known premature-replay defect instead")
    args = parser.parse_args()
    names = ("native", "openai") if args.runtime == "both" else (args.runtime,)
    passed = []
    for name in names:
        print(f"\nReviewer runtime: {name}", flush=True)
        passed.append(compare_one(name, loan=args.loan_regression))
    return 0 if all(passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
