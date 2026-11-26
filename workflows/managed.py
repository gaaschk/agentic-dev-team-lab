"""The Project Manager picks specialists; the host decides where they sit."""

from dataclasses import replace
from functools import partial

from team_agents.advisory import request_report
from team_agents.markdown import to_markdown
from team_agents.model_config import resolve_model
from team_agents.profiles import advisory_profiles
from team_agents.project_manager_agent import project_plan
from team_agents.reviewer_agent import REVIEW_FORMAT, parse_review, review_artifacts
from team_agents.role_agents import run as run_agent

from .release import pretty
from .runner import Approval, Step


SECURITY_AGENT = "cso"  # choosing this specialist adds a blocking code review
SECURITY_REVIEW = "Security Review"
_SECURITY_TASK = (
    "Now review the exact proposal for security defects only; do not edit or execute it. "
    "Block only on defects that someone could exploit, or that expose or destroy data, in "
    "the approved use. Hardening ideas, style, and missing features are never blockers. "
    "For each blocker give the file, the relevant expression, and the consequence. "
    "Return PASS with no blockers if there is none."
)


def security_review(_failure, plan, design, proposal, *, decide, artifact_runner=run_agent):
    """CHANGES_REQUIRED ends the run: a real blocker, so never retried."""
    profile = advisory_profiles()[SECURITY_AGENT]
    raw = artifact_runner(
        role=profile.instructions(), stage=profile.role, task=_SECURITY_TASK,
        artifacts=review_artifacts(plan, design, proposal),
        required={"criteria", "candidate"}, output_format=REVIEW_FORMAT,
        model=resolve_model(f"LAB_{SECURITY_AGENT.upper()}_MODEL"), decide=decide,
    )
    result = parse_review(raw)
    if result["verdict"] != "PASS":
        raise SystemExit("Blocked by the security review.\n\n" + to_markdown(result))
    return result


def planning_workflow(*, decide):
    return (
        Step("Project Manager", ("goal",), "team_plan", partial(project_plan, decide=decide),
             before="\nAsking the Project Manager to assemble the team…\n", render=pretty,
             auto_retryable=True),
        Approval("team_plan", "approved_team", "Approve this team? [y/N] ",
                 "Stopped. No delivery work was started."),
    )


def assemble(team_plan, release, *, decide, artifact_runner=run_agent):
    """Insert the chosen specialists around the core release. Discovery advice
    reaches the Product Lead and Architect, and assurance advice reaches the
    Implementer; readiness runs after review. None of it changes the approval
    gates. Choosing the security officer also adds a blocking code review."""
    profiles = advisory_profiles()

    def phase(name, inputs):
        steps, outputs = [], ()
        for index, assignment in enumerate(team_plan["crews"][name], start=1):
            key, task = assignment["agent"], assignment["task"]
            title, needed = profiles[key].role, inputs + outputs

            def action(_failure, *values, key=key, task=task, needed=needed):
                evidence = [{"name": n, "contents": v} for n, v in zip(needed, values)]
                return request_report(key, task, evidence, decide=decide)

            output = f"{name}_{index}"
            steps.append(Step(title, needed, output, action, before=f"\nRunning {title}…\n",
                              render=pretty, auto_retryable=True))
            outputs += (output,)
        return steps, outputs

    discovery, found = phase("discovery", ("goal",))
    assurance, assured = phase("assurance", ("approved_plan", "approved_design", "qa_plan"))
    readiness, _ = phase("readiness", ("approved_plan", "qa_plan", "proposal", "review"))
    nodes = list(discovery)
    for node in release:
        name = getattr(node, "name", None)
        if name in ("Product Lead", "Architect"):
            node = replace(node, inputs=node.inputs + found)
        elif name == "Implementer":
            nodes += assurance
            node = replace(node, inputs=node.inputs + assured)
        nodes.append(node)
    if any(member["agent"] == SECURITY_AGENT
           for members in team_plan["crews"].values() for member in members):
        nodes.append(Step(SECURITY_REVIEW, ("approved_plan", "approved_design", "proposal"),
                          "security_review",
                          partial(security_review, decide=decide, artifact_runner=artifact_runner),
                          before="\nRunning the security code review…\n", render=pretty,
                          auto_retryable=True))
    return (*nodes, *readiness)
