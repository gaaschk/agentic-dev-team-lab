"""Architect prompt, design handoff, and design validation."""

import json

from . import ROLES_DIR
from .agent_retry import RetryableAgentError
from .model_config import resolve_model
from .role_agents import Artifact, run as run_agent


def architect_format():
    text_list = {"type": "array", "items": {"type": "string"}}
    properties = {
        "components": text_list,
        "persistence": {"type": "string"},
        "interface": {"type": "string"},
        "build_order": text_list,
        "verification": text_list,
        "risks": text_list,
        "blockers": text_list,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "technical_design",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


class InvalidDesign(RetryableAgentError):
    pass


def parse_design(raw_design):
    try:
        design = json.loads(raw_design)
    except (TypeError, json.JSONDecodeError):
        raise InvalidDesign("Architect returned no valid design. Nothing to approve.") from None

    properties = architect_format()["json_schema"]["schema"]["properties"]
    if not isinstance(design, dict) or set(design) != set(properties):
        raise InvalidDesign("Architect response does not match the design format.")
    blockers = design["blockers"]
    if not isinstance(blockers, list) or any(
        not isinstance(item, str) or not item.strip() for item in blockers
    ):
        raise InvalidDesign("Architect response has an invalid blockers field.")
    if blockers:
        raise SystemExit("Architect blocked the handoff: " + "; ".join(blockers))

    for field, spec in properties.items():
        value = design[field]
        if spec["type"] == "string":
            valid = isinstance(value, str) and bool(value.strip())
        else:
            valid = (
                isinstance(value, list)
                and (bool(value) or field in {"risks", "blockers"})
                and all(isinstance(item, str) and item.strip() for item in value)
            )
        if not valid:
            reason = "empty" if isinstance(value, str) and not value.strip() else "invalid"
            raise InvalidDesign(f"Architect response has an {reason} {field} field.")
    return design


def technical_design(failure, goal, plan, *reports, decide, artifact_runner=run_agent):
    architect = (ROLES_DIR / "architect.md").read_text(encoding="utf-8")
    task = (
        "Make concrete design choices for the approved product below. "
        "Do not restate the plan, leave core choices undecided, or write code. "
        "When there are no blockers, persistence and interface must describe "
        "specific choices in nonempty strings. Return only the requested JSON design."
    )

    artifacts = {
        "approved_plan": Artifact("Approved release scope and criteria", plan),
        "brief": Artifact("Original goal, for context only", goal),
    }
    required = {"approved_plan"}
    if reports:
        artifacts["specialist_advice"] = Artifact("Specialist advice within the approved scope", reports)
        required.add("specialist_advice")

    def request_design(instructions):
        return artifact_runner(
            role=architect, stage="Architect", task=instructions,
            artifacts=artifacts,
            required=required, output_format=architect_format(),
            model=resolve_model("LAB_ARCHITECT_MODEL"), decide=decide,
        )

    correction = f" Correct the prior validation failure: {failure}" if isinstance(failure, InvalidDesign) else ""
    return parse_design(request_design(task + correction))
