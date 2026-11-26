"""Optional specialists the Product Lead can add to a release's team."""

import json

from . import ROLES_DIR
from .agent_retry import RetryableAgentError
from .model_config import resolve_model
from .reviewer_agent import REVIEW_FORMAT, parse_review, review_artifacts
from .role_agents import Artifact, run as run_agent

UX = "UX Designer"
SECURITY = "Security Reviewer"
ROSTER = (UX, SECURITY)

_FIELDS = ("flows", "states", "accessibility", "requirements")
UX_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "ux_spec",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {field: {"type": "array", "items": {"type": "string"}}
                           for field in _FIELDS},
            "required": list(_FIELDS),
            "additionalProperties": False,
        },
    },
}


def selected(plan, name):
    return any(member["name"] == name for member in plan["specialists"])


def parse_ux_spec(raw):
    try:
        spec = json.loads(raw)
    except (TypeError, ValueError):
        raise RetryableAgentError("UX Designer did not return clean JSON.") from None
    if (not isinstance(spec, dict) or set(spec) != set(_FIELDS)
            or any(not isinstance(spec[field], list)
                   or not all(isinstance(item, str) and item.strip() for item in spec[field])
                   for field in _FIELDS)
            or not spec["flows"]):
        raise RetryableAgentError("UX Designer response does not match the UX spec format.")
    return spec


def ux_design(_failure, goal, plan, *, decide, artifact_runner=run_agent):
    raw = artifact_runner(
        role=(ROLES_DIR / "ux_designer.md").read_text(encoding="utf-8"), stage=UX,
        task="Describe the user experience of the approved release. Do not write code. "
             "Return only the requested JSON.",
        artifacts={
            "approved_plan": Artifact("Approved release scope and criteria", plan),
            "brief": Artifact("Original goal, for context only", goal),
        },
        required={"approved_plan"}, output_format=UX_FORMAT,
        model=resolve_model("LAB_UX_MODEL"), decide=decide,
    )
    return parse_ux_spec(raw)


def security_review(_failure, plan, design, proposal, *, decide, artifact_runner=run_agent):
    """CHANGES_REQUIRED ends the run: a real blocker, so never retried."""
    raw = artifact_runner(
        role=(ROLES_DIR / "security_reviewer.md").read_text(encoding="utf-8"), stage=SECURITY,
        task="Review the exact proposal for security defects. "
             "Report only blocking defects, with code evidence and consequence.",
        artifacts=review_artifacts(plan, design, proposal),
        required={"criteria", "candidate"}, output_format=REVIEW_FORMAT,
        model=resolve_model("LAB_SECURITY_MODEL"), decide=decide,
    )
    result = parse_review(raw, SECURITY)
    if result["verdict"] != "PASS":
        raise SystemExit("Blocked by the Security Reviewer:\n" + json.dumps(result, indent=2))
    return result
