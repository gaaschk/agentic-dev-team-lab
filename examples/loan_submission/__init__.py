"""Fictional loan-submission exercise. The core workflow does not import it."""

CRITERIA = (
    "A complete draft becomes SUBMITTED, never APPROVED, and creates exactly one submission event.",
    "An incomplete draft reports the missing fields without changing status or creating events.",
    "Submitting an already submitted application returns its existing state without creating another event.",
)

BRIEF = (
    "Build a toy loan-application submission module for synthetic data only. "
    "A draft has an application ID, applicant reference, and requested amount in integer cents. "
    "For this exercise expose a Python function submit(application) -> (application, errors), "
    "where application also tracks status and submission event count. "
    + " ".join(CRITERIA)
    + " Do not build credit decisions, disbursement, identity checks, databases, or external integrations."
)
