"""Execute a declared linear workflow without depending on any agent or router."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Step:
    name: str
    inputs: tuple[str, ...]
    output: str
    action: Callable
    before: str = ""
    render: Callable = str
    retry_stop: str | None = None


@dataclass(frozen=True)
class Approval:
    artifact: str
    output: str
    prompt: str
    stopped: str


def prepare(workflow, initial):
    """Validate all handoffs first and isolate the initial artifacts."""
    workflow = tuple(workflow)
    known = set(initial)
    for node in workflow:
        needed = node.inputs if isinstance(node, Step) else (node.artifact,)
        if set(needed) - known or node.output in known:
            raise ValueError(f"Invalid workflow handoff for {node.output}.")
        known.add(node.output)

    return workflow, deepcopy(initial)


def execute_node(node, state, *, retry, prompt, emit, on_start=None, on_output=None):
    """Only a human approval creates an approved artifact."""
    if isinstance(node, Approval):
        try:
            approved = prompt(node.prompt).strip().lower() == "y"
        except EOFError:
            approved = False
        if not approved:
            raise SystemExit(node.stopped)
        state[node.output] = deepcopy(state[node.artifact])
        return

    if node.before:
        emit(node.before)
    if on_start:
        on_start(node.name)

    def attempt(failure):
        inputs = (deepcopy(state[key]) for key in node.inputs)
        return node.action(failure, *inputs)

    try:
        result = retry.run(node.name, attempt, stopped=node.retry_stop)
    except SystemExit as failure:
        if on_output:
            on_output(node.name, str(failure))
        raise
    state[node.output] = result
    rendered = node.render(result)
    if on_output:
        on_output(node.name, rendered)
    else:
        emit(rendered)


def run(workflow, initial, *, retry, prompt, emit, on_start=None, on_output=None):
    workflow, state = prepare(workflow, initial)
    for node in workflow:
        execute_node(node, state, retry=retry, prompt=prompt, emit=emit,
                      on_start=on_start, on_output=on_output)
    return state
