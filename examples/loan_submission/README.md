# Fictional loan submission example

The reading list exercises local persistence. This example exercises a
state change, invalid input, and a replay: a repeated submission must not create
a second event. It uses synthetic IDs and no credit decisions, money movement,
or external integrations. It is not a lending application.

From the project root, ask the same reusable team to plan it:

```sh
DEFAULT_MODEL=smart-auto python3 -B plan_release.py --example loan-submission
```

Look for separate QA checks for a valid draft, an incomplete draft, and an
application that has already been submitted. The agent run only proposes tests
and code; it does not write files or execute either.

To see what deterministic evidence looks like, run the checks against the
bundled, trusted test doubles:

```sh
python3 -B -m examples.loan_submission.acceptance
python3 -B -m examples.loan_submission.acceptance --broken
```

The first command should pass. The second should report a duplicate-event
failure and exit with code 1. Neither command loads or runs generated code.
