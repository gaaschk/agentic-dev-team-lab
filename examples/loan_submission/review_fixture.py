"""A small version of the early-return defect missed by the live Reviewer."""

from . import CRITERIA

FILE = "submission.py"
PLAN = {
    "in_scope": ["Validate a draft", "Submit it once", "Leave replay unchanged"],
    "out_of_scope": ["Credit decisions", "Disbursement", "External integrations"],
    "acceptance_criteria": list(CRITERIA),
}
DESIGN = {
    "interface": "Application is a dict; submit(application) returns (application, errors).",
    "persistence": "Application records are kept in memory.",
}
CANDIDATE = '''RECORDS = {}

def create_draft(application_id, applicant_ref, requested_cents):
    draft = dict(application_id=application_id, applicant_ref=applicant_ref,
                 requested_cents=requested_cents, status="DRAFT", submission_events=0)
    RECORDS[application_id] = draft
    return draft

def submit(application):
    existing = RECORDS.get(application["application_id"])
    if existing:
        return existing, []
    missing = [key for key in ("applicant_ref", "requested_cents") if not application[key]]
    if missing:
        return application, missing
    updated = dict(application, status="SUBMITTED", submission_events=1)
    RECORDS[application["application_id"]] = updated
    return updated, []
'''


def detected(verdict):
    """Credit the actual premature replay, not an unrelated flagged defect."""
    if verdict["verdict"] != "CHANGES_REQUIRED":
        return False
    for blocker in verdict["blockers"]:
        evidence, consequence = (blocker[k].lower() for k in ("evidence", "consequence"))
        if (blocker["file"] == FILE
                and any(term in evidence for term in ("existing", "records", "early return"))
                and any(term in consequence for term in ("draft", "submission", "submit", "transition"))):
            return True
    return False
