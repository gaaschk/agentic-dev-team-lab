# Reading-list example

Run these commands from the project root. This example is not imported by the
reusable workflow.

## Ask the team for a plan

```sh
python3 -B plan_release.py --example reading-list
```

This prints an independent QA plan but does not run tests or write a candidate.
The team is free to propose a public interface. The exercises below target the
older, specific menu-driven fixture, not any newly generated reading-list design.

## Review and test a saved interactive candidate

A candidate folder contains `readinglist.py` and `store.py`, with menu choices
1 to add, 2 to list, and 3 to quit. Open the exact files in the Reviewer and,
if it returns a clean pass, decide whether to run deterministic checks:

```sh
python3 -B -m examples.reading_list.qa_gate --agents /path/to/candidate
```

The execution prompt names the fingerprint of the snapshot being tested. On
approval, the runner uses a private copy, fresh data, and the macOS sandbox.
No inherited credentials and no fallback to unrestricted execution.

## Other exercises

- `python3 -B -m examples.reading_list.implementer_agent /path/to/candidate`
  inspects the menu-driven fixture. With `--edit`, it can change exactly one
  message in a new scratch copy, then hands off to Reviewer and the same QA
  approval. Its instructions are in `implementer_agent.md`.
- `python3 -B -m examples.reading_list.review_fixtures` checks the native
  artifact-reading Reviewer on two fixed examples, one of which is missing an
  import. Use `--runtime both` with the optional SDK installed to compare the
  same fixed candidates. Neither Reviewer runs either example.
