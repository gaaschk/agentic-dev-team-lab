"""Reviewer inputs, structured verdict, and shared validation."""

import json

from . import ROLES_DIR
from .agent_retry import RetryableAgentError
from .model_config import resolve_model
from .role_agents import Artifact, run


BLOCKER = {
    "type": "object",
    "properties": {field: {"type": "string"} for field in ("file", "evidence", "consequence")},
    "required": ["file", "evidence", "consequence"],
    "additionalProperties": False,
}
REVIEW_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "review_verdict",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["PASS", "CHANGES_REQUIRED"]},
                "blockers": {"type": "array", "items": BLOCKER},
            },
            "required": ["verdict", "blockers"],
            "additionalProperties": False,
        },
    },
}

def review_artifacts(plan, design, proposal):
    return {
        "criteria": Artifact("Approved plan and design", {"plan": plan, "design": design}),
        "candidate": Artifact("Candidate source or preview", proposal),
    }


def reviewed_preview(reviewer, plan, design, proposal):
    role = (ROLES_DIR / "reviewer.md").read_text(encoding="utf-8")
    return parse_review(reviewer(role, plan, design, proposal))


def ask_reviewer(role, plan, design, proposal, *, decide):
    payload = {
        "approved_plan": plan,
        "approved_design": design,
        "implementation_proposal": proposal,
    }
    task = (
        "Review this candidate only against the approved requirements. "
        "Report actual blockers only, not style suggestions or unrequested improvements. "
        "Return the JSON verdict and blockers. For each blocker, include its file, code evidence "
        "and concrete consequence. Do not rewrite or execute it.\n\n" + json.dumps(payload)
    )
    return decide(
        role, task, response_format=REVIEW_FORMAT,
        model=resolve_model("LAB_REVIEW_MODEL"), stage="Reviewer",
    )


def ask_agent_reviewer(role, plan, design, proposal, *, decide, artifact_runner=run):
    return artifact_runner(
        role=role, stage="Reviewer",
        task="Review the exact proposal against the approved plan and design. "
             "Report only blocking defects, with code evidence and consequence.",
        artifacts=review_artifacts(plan, design, proposal),
        required={"criteria", "candidate"}, output_format=REVIEW_FORMAT,
        model=resolve_model("LAB_REVIEW_MODEL"), decide=decide,
    )


def parse_review(raw, who="Reviewer"):
    try:
        result = json.loads(raw)
    except (TypeError, ValueError):
        raise RetryableAgentError(f"{who} did not return clean JSON.") from None
    if (not isinstance(result, dict) or set(result) != {"verdict", "blockers"}
            or result["verdict"] not in ("PASS", "CHANGES_REQUIRED")
            or not isinstance(result["blockers"], list)
            or any(not isinstance(item, dict)
                   or set(item) != {"file", "evidence", "consequence"}
                   or any(not isinstance(value, str) or not value.strip()
                          for value in item.values())
                   for item in result["blockers"])
            or (result["verdict"] == "PASS") == bool(result["blockers"])):
        raise RetryableAgentError(f"{who} response does not match the verdict format.")
    return result


def with_review_retry(reviewer, retry):
    """Adapter for the standalone reading-list example, which expects JSON text."""
    def review(*args):
        def attempt(_failure):
            raw = reviewer(*args)
            parse_review(raw)
            return raw

        return retry.run("Reviewer", attempt)

    return review
