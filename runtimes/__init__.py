"""Select the artifact strategy for a standalone role benchmark or workflow."""

from functools import partial

from team_agents.reviewer_agent import ask_agent_reviewer


REVIEW_RUNTIMES = ("native", "openai", "crewai")


def select_reviewer(name, *, decide):
    if name == "native":
        return partial(ask_agent_reviewer, decide=decide)
    if name == "openai":
        from .openai_runtime import run_tool_artifacts as run_artifacts
    elif name == "crewai":
        from .crewai_runtime import run_artifacts
    else:
        raise ValueError(f"Unknown Reviewer runtime: {name}")
    return partial(ask_agent_reviewer, decide=decide, artifact_runner=run_artifacts)
