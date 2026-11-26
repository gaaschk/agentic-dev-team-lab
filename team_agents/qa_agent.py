"""Propose acceptance checks from approved artifacts; never execute them."""

import json

from . import ROLES_DIR
from .agent_retry import RetryableAgentError
from .model_config import resolve_model
from .role_agents import Artifact, run

FIELDS = ("name", "setup", "action", "expected")
QA_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "qa_test_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "checks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "criterion_id": {"type": "integer"},
                            **{field: {"type": "string"} for field in FIELDS},
                        },
                        "required": ["criterion_id", *FIELDS],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["checks"],
            "additionalProperties": False,
        },
    },
}


def parse_plan(raw, count):
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise RetryableAgentError("QA did not return a clean JSON test plan.") from None
    if not isinstance(data, dict) or set(data) != {"checks"} or not isinstance(data["checks"], list):
        raise RetryableAgentError("QA returned an invalid test plan.")
    covered = set()
    limit = max(12, count * 3)
    if len(data["checks"]) > limit:
        raise RetryableAgentError("QA proposed too many checks.")
    for check in data["checks"]:
        if (not isinstance(check, dict)
                or set(check) != {"criterion_id", *FIELDS}
                or type(check["criterion_id"]) is not int
                or not 1 <= check["criterion_id"] <= count
                or any(not isinstance(check[field], str) or not check[field].strip()
                       for field in FIELDS)):
            raise RetryableAgentError("QA returned a check with invalid fields or criterion ID.")
        covered.add(check["criterion_id"])
    missing = sorted(set(range(1, count + 1)) - covered)
    if missing:
        ids = ", ".join(map(str, missing))
        raise RetryableAgentError(f"QA did not propose checks for approved criteria: {ids}.")
    return data


def request_plan(plan, design, ux_spec=None, *, decide, failure=None, artifact_runner=run):
    criteria = [
        {"criterion_id": number, "requirement": text}
        for number, text in enumerate(plan["acceptance_criteria"], start=1)
    ]
    if not criteria:
        raise SystemExit("QA needs at least one approved acceptance criterion. Nothing was executed.")
    role = (ROLES_DIR / "qa.md").read_text(encoding="utf-8")
    artifacts = {
        "requirements": Artifact("Approved scope, numbered criteria, and public interface", {
            "in_scope": plan["in_scope"],
            "out_of_scope": plan["out_of_scope"],
            "criteria": criteria,
            "public_interface": design["interface"],
        })
    }

    if ux_spec is not None:
        artifacts["ux_spec"] = Artifact("UX advice, not additional requirements", ux_spec)

    task = (
        "Propose at least one black-box check per numbered approved criterion. "
        f"Return no more than {max(12, len(criteria) * 3)} checks. "
        "Describe the checks as plain language, not scripts. No tests are being run."
    )
    if failure is not None:
        task += " Prior failure: " + str(failure).split(" (request ID:", 1)[0]
    raw = artifact_runner(
        role=role, stage="QA", task=task, artifacts=artifacts,
        required=set(artifacts), output_format=QA_FORMAT,
        model=resolve_model("LAB_QA_MODEL"), decide=decide,
    )
    return parse_plan(raw, len(criteria))

def propose(plan, design, *, decide, retry):
    return retry.run("QA", lambda failure: request_plan(plan, design, decide=decide, failure=failure))
