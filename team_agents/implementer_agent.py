"""Implementer prompt and preview-only handoff."""

import json

from . import ROLES_DIR
from .agent_retry import RetryableAgentError
from .model_config import resolve_model


def implement(_failure, goal, plan, design, *, decide):
    implementer = (ROLES_DIR / "implementer.md").read_text(encoding="utf-8")
    implementation_request = json.dumps({
        "goal": goal,
        "approved_plan": plan,
        "approved_design": design,
    })
    proposal = decide(
        implementer,
        "Propose file paths and file contents for the approved product below. "
        "Return an implementation preview, not instructions for the router.\n\n"
        + implementation_request,
        model=resolve_model("LAB_IMPLEMENTER_MODEL"), stage="Implementer",
    )
    if not isinstance(proposal, str) or not proposal.strip():
        raise RetryableAgentError("Implementer returned no proposal.")
    return proposal
