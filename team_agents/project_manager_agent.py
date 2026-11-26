"""Assemble a reviewable team without giving a model execution authority."""

import json

from .agent_retry import RetryableAgentError
from .model_config import resolve_model
from .profiles import CREW_PHASES, advisory_profiles, instructions_for

LIFECYCLES = {
    "lean": "Direct release path; use only optional advisory work justified by risk.",
    "discovery-first": "Resolve product uncertainty with at least one discovery specialist.",
    "verification-first": "Address verification risk with at least one assurance specialist before the preview.",
}
def available_slots():
    profiles = advisory_profiles()
    return {
        phase: {key: profile.goal for key, profile in profiles.items()
                if profile.crew_phase in (None, phase)}
        for phase in CREW_PHASES
    }


def response_format(slots):
    # One named array per phase prevents duplicate crew blocks. An unused crew
    # is simply an empty array, not a malformed or partially specified object.
    phases = {}
    for phase in CREW_PHASES:
        agents = sorted(slots[phase])
        agent_field = {"type": "string"}
        if agents:
            agent_field["enum"] = agents
        phases[phase] = {
            "type": "array",
            **({} if agents else {"maxItems": 0}),
            "items": {
                "type": "object",
                "properties": {"agent": agent_field, "task": {"type": "string"}},
                "required": ["agent", "task"],
                "additionalProperties": False,
            },
        }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "managed_release_plan",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "sdlc": {"type": "string", "enum": list(LIFECYCLES)},
                    "rationale": {"type": "string"},
                    "crews": {"type": "object", "properties": phases, "required": list(CREW_PHASES),
                              "additionalProperties": False},
                },
                "required": ["sdlc", "rationale", "crews"],
                "additionalProperties": False,
            },
        },
    }


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _invalid(reason):
    raise RetryableAgentError(f"Project Manager plan: {reason}.")


def _unique_fields(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            _invalid("JSON repeats a field; each crew phase must appear once")
        obj[key] = value
    return obj


def validate(raw, *, slots=None):
    if slots is None:
        slots = available_slots()
    try:
        plan = json.loads(raw, object_pairs_hook=_unique_fields)
    except (TypeError, ValueError):
        raise RetryableAgentError("Project Manager did not return clean plan JSON.") from None
    if not isinstance(plan, dict) or set(plan) != {"sdlc", "rationale", "crews"}:
        _invalid("expected exactly sdlc, rationale, and crews")
    if not _text(plan["sdlc"]) or plan["sdlc"] not in LIFECYCLES:
        _invalid("sdlc must be lean, discovery-first, or verification-first")
    if not _text(plan["rationale"]) or len(plan["rationale"]) > 1500:
        _invalid("rationale must be nonempty and at most 1500 characters")
    crews = plan["crews"]
    if not isinstance(crews, dict) or set(crews) != set(CREW_PHASES):
        _invalid("crews must contain exactly discovery, assurance, and readiness arrays")
    seen = set()
    for phase in CREW_PHASES:
        members = crews[phase]
        if not isinstance(members, list):
            _invalid(f"crews.{phase} must be an array of assignments")
        for i, member in enumerate(members, start=1):
            field = f"crews.{phase}, assignment {i}"
            if not isinstance(member, dict) or set(member) != {"agent", "task"}:
                _invalid(f"{field} must contain exactly agent and task")
            agent, task = member["agent"], member["task"]
            if not _text(agent) or agent not in slots[phase]:
                _invalid(f"{field} needs an agent ID from the {phase} roster")
            if agent in seen:
                _invalid(f"{field} repeats an agent; each agent may appear only once")
            if not _text(task) or len(task) > 800:
                _invalid(f"{field} needs a nonempty task of at most 800 characters")
            seen.add(agent)
    required_phase = {"discovery-first": "discovery", "verification-first": "assurance"}.get(plan["sdlc"])
    if required_phase and not crews[required_phase]:
        _invalid(f"{plan['sdlc']} requires at least one assignment in crews.{required_phase}")
    return plan


def project_plan(failure, goal, *, decide):
    available = available_slots()
    task = (
        "Select an SDLC and every advisory specialist this brief needs, and no others: consider the whole "
        "roster. The core crew always includes "
        "Product Lead, Architect, QA, Implementer, and Reviewer. Discovery happens before product planning; "
        "assurance happens after QA plans tests and before the Implementer; readiness happens after the Reviewer. "
        "Return a crews object with exactly three keys: discovery, assurance, readiness. "
        "Use an empty array for any unused phase. Each used phase has one "
        "object with agent and task per chosen specialist. Use only the exact agent IDs shown; do not repeat agents. "
        "Discovery-first requires discovery; verification-first requires assurance. Return only JSON."
        "\n\n" + json.dumps({"brief": goal, "lifecycles": LIFECYCLES, "available_roles": available})
    )
    if failure:
        task += "\nCorrect the prior failure: " + str(failure).split(" (request ID:", 1)[0]
    raw = decide(instructions_for("project_manager"), task,
                 stage="Project Manager", model=resolve_model("LAB_PROJECT_MANAGER_MODEL"), response_format=response_format(available))
    return validate(raw, slots=available)
