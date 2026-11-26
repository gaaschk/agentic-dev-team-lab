"""Product Lead prompt, brief handoff, and release-plan validation."""

import json

from . import ROLES_DIR
from .agent_retry import RetryableAgentError
from .model_config import resolve_model
from .role_agents import Artifact, run as run_agent
from .specialists import ROSTER


def _valid_team(team):
    return (
        isinstance(team, list)
        and all(isinstance(member, dict) and set(member) == {"name", "reason"}
                and member["name"] in ROSTER
                and isinstance(member["reason"], str) and member["reason"].strip()
                for member in team)
        and len({member["name"] for member in team}) == len(team)
    )


def product_plan(_failure, goal, *, decide, artifact_runner=run_agent):
    list_fields = ("in_scope", "out_of_scope", "acceptance_criteria", "open_questions")
    properties = {"release_goal": {"type": "string"}}
    for field in list_fields:
        properties[field] = {"type": "array", "items": {"type": "string"}}
    member = {"name": {"type": "string", "enum": list(ROSTER)}, "reason": {"type": "string"}}
    properties["specialists"] = {"type": "array", "items": {
        "type": "object", "properties": member, "required": list(member),
        "additionalProperties": False,
    }}

    plan_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "release_plan",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }

    product_lead = (ROLES_DIR / "product_lead.md").read_text(encoding="utf-8")
    raw_plan = artifact_runner(
        role=product_lead, stage="Product Lead",
        task="Propose the smallest useful release for the product brief. Do not write code.",
        artifacts={"brief": Artifact("Requester goal and constraints", goal)},
        required={"brief"}, output_format=plan_format,
        model=resolve_model("LAB_PRODUCT_MODEL"), decide=decide,
    )
    if not isinstance(raw_plan, str) or not raw_plan.strip():
        raise RetryableAgentError("Product Lead returned no plan. Nothing to approve.")

    try:
        plan = json.loads(raw_plan)
    except json.JSONDecodeError as exc:
        raise RetryableAgentError(f"Product Lead returned invalid JSON: {exc.msg}") from None

    if (
        not isinstance(plan, dict)
        or set(plan) != set(properties)
        or not isinstance(plan["release_goal"], str)
        or not plan["release_goal"].strip()
        or not plan["acceptance_criteria"]
        or any(
            not isinstance(plan[field], list)
            or not all(isinstance(item, str) and item.strip() for item in plan[field])
            for field in list_fields
        )
        or not _valid_team(plan["specialists"])
    ):
        raise RetryableAgentError("Product Lead response does not match the plan format.")
    return plan
