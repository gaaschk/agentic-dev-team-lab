"""Workflow-engine boundary. Agent backends are selected separately."""

from typing import Callable, Mapping, Protocol, Sequence

from workflows.runner import Approval, Step, run


class WorkflowRuntime(Protocol):
    def __call__(
        self, workflow: Sequence[Step | Approval], initial: Mapping[str, object], *,
        retry: object, prompt: Callable[[str], str], emit: Callable[[str], None],
    ) -> Mapping[str, object]:
        """Run the declared workflow without changing its handoff and approval rules."""


class OpenAIWorkflow:
    """Use SDK agents for role steps; preserve deterministic host gates."""

    from .openai_runtime import decide as _decide, run_artifacts as _run_artifacts

    decide = staticmethod(_decide)
    artifact_runner = staticmethod(_run_artifacts)
    review_runtime = "openai"

    def __call__(self, workflow, initial, **controls):
        return run(workflow, initial, **controls)


class CrewAIWorkflow:
    """Run release steps through a CrewAI Flow with shared host gates."""

    from .crewai_runtime import decide as _decide, run_artifacts as _run_artifacts

    decide = staticmethod(_decide)
    artifact_runner = staticmethod(_run_artifacts)
    review_runtime = "crewai"

    def __call__(self, workflow, initial, **controls):
        from .crewai_runtime import run_flow
        return run_flow(workflow, initial, **controls)


WORKFLOW_RUNTIMES: dict[str, WorkflowRuntime] = {
    "native": run, "openai": OpenAIWorkflow(), "crewai": CrewAIWorkflow(),
}
