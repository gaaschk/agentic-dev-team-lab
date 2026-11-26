"""The Project Manager picks specialists; the host decides where they sit."""

from dataclasses import replace
from functools import partial

from team_agents.advisory import request_report
from team_agents.profiles import advisory_profiles
from team_agents.project_manager_agent import project_plan

from .release import pretty
from .runner import Approval, Step


def planning_workflow(*, decide):
    return (
        Step("Project Manager", ("goal",), "team_plan", partial(project_plan, decide=decide),
             before="\nAsking the Project Manager to assemble the team…\n", render=pretty,
             auto_retryable=True),
        Approval("team_plan", "approved_team", "Approve this team? [y/N] ",
                 "Stopped. No delivery work was started."),
    )


def assemble(team_plan, release, *, decide):
    """Insert the chosen specialists around the core release. Discovery advice
    reaches the Product Lead and assurance advice reaches the Implementer;
    readiness runs after review. None of it changes the approval gates."""
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
        if name == "Product Lead":
            node = replace(node, inputs=node.inputs + found)
        elif name == "Implementer":
            nodes += assurance
            node = replace(node, inputs=node.inputs + assured)
        nodes.append(node)
    return (*nodes, *readiness)
