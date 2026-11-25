"""Run deterministic checks against trusted, in-memory loan fixtures only."""

import argparse
from copy import deepcopy


def check(submit):
    """Check only the public contract, including behavior on replay."""
    failures = []
    draft = {
        "application_id": "SYN-001", "applicant_ref": "SYN-APPLICANT", "requested_cents": 10000,
        "status": "DRAFT", "submission_events": 0,
    }
    expected = {**draft, "status": "SUBMITTED", "submission_events": 1}
    actual, errors = submit(deepcopy(draft))
    if actual != expected or errors:
        failures.append("Valid draft must become SUBMITTED and create exactly one event.")

    incomplete = {**draft, "applicant_ref": "", "requested_cents": None}
    unchanged, errors = submit(deepcopy(incomplete))
    if unchanged != incomplete or sorted(errors) != ["applicant_ref", "requested_cents"]:
        failures.append("Incomplete draft must report both missing fields without changing state.")

    # Use a known-good submitted state so failure in the first case cannot hide a replay bug.
    repeated, errors = submit(deepcopy(expected))
    if repeated != expected or errors:
        failures.append("Retrying a submitted application must leave its event count unchanged.")
    return failures


def fixture(app, *, duplicate_event=False):
    """A local test double, not the implementation the agent team produces."""
    if app["status"] == "SUBMITTED" and not duplicate_event:
        return dict(app), ()
    missing = tuple(field for field in ("applicant_ref", "requested_cents") if not app[field])
    if missing:
        return dict(app), missing
    return {**app, "status": "SUBMITTED", "submission_events": app["submission_events"] + 1}, ()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--broken", action="store_true", help="simulate creating a duplicate event")
    args = cli.parse_args()
    failures = check(lambda app: fixture(app, duplicate_event=args.broken))
    if failures:
        for failure in failures:
            print(f"QA FAIL: {failure}")
        return 1
    print("QA PASS: valid transition, missing-field handling, and replay safety.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
