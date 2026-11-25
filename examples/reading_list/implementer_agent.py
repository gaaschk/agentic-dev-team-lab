"""Bounded, read-only tool loop for the reading-list lab."""

import argparse
import json
import os
from pathlib import Path
import stat
from tempfile import mkdtemp

from team_agents.agent_retry import AgentRetry, RetryableAgentError
from team_agents.model_config import resolve_model
from plan_release import ask_role

ALLOWED = {"readinglist.py", "store.py"}
STEP_LIMIT = 4
TASK = "Find where to change the message shown when the reading list is empty."
OLD_MESSAGE = "Your reading list is empty."
NEW_MESSAGE = "No books yet. Choose 1 to add one."
EDIT_ACTION = "replace_empty_message"
ROLE_PATH = Path(__file__).with_suffix(".md")


def action_format(options):
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "implementer_action",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": options},
                    "answer": {"type": "string"},
                },
                "required": ["action", "answer"],
                "additionalProperties": False,
            },
        },
    }


def read_candidate_file(root, name):
    fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError("expected a regular candidate file")
        data = file.read(32769)
    if len(data) > 32768:
        raise ValueError("candidate file exceeds lab size limit")
    return data.decode("utf-8")


def run(root, *, decide=ask_role, apply_edit=None, retry=None):
    """Apply only a runner-supplied edit; never accept paths or code from the model."""
    role = ROLE_PATH.read_text(encoding="utf-8")
    model = resolve_model("LAB_IMPLEMENTER_MODEL")
    observations = {}
    edited = False
    for turn in range(1, STEP_LIMIT + 1):
        unread = ALLOWED - observations.keys()
        options = ["read_file:" + name for name in sorted(unread)]
        if apply_edit is not None and "readinglist.py" in observations and not edited:
            options.insert(0, EDIT_ACTION)
        if edited:
            options = ["finish"]
        elif observations and apply_edit is None:
            options.insert(0, "finish")
        context = {
            "task": TASK if apply_edit is None else (
                "Change only the empty-list message to " + json.dumps(NEW_MESSAGE)
                + ". Read the relevant file first, then use replace_empty_message and finish."
            ),
            "edit_applied": edited,
            "turn": turn,
            "available_actions": options,
            "observations": observations,
        }
        def choose(_failure):
            raw = decide(role, json.dumps(context), response_format=action_format(options),
                         model=model, stage="Implementer agent")
            try:
                step = json.loads(raw)
            except (TypeError, ValueError):
                raise RetryableAgentError("Model did not return a JSON action.") from None
            if (not isinstance(step, dict) or set(step) != {"action", "answer"}
                    or not isinstance(step["answer"], str)
                    or step["action"] not in options):
                raise RetryableAgentError("Model returned an action not allowed on this turn.")
            action, answer = step["action"], step["answer"]
            if action == "finish" and not answer.strip():
                raise RetryableAgentError("Model finished with an empty answer.")
            if action != "finish" and answer.strip():
                raise RetryableAgentError("Tool action included an unexpected answer.")
            return action, answer

        # Retry the model decision, not the tool. An already applied edit is never replayed.
        action, answer = choose(None) if retry is None else retry.run("Implementer agent", choose)
        if action == "finish":
            print(answer)
            return answer
        if action == EDIT_ACTION:
            observations["readinglist.py"] = apply_edit(observations["readinglist.py"])
            edited = True
            print("Tool: replace_empty_message(readinglist.py)", flush=True)
            continue
        name = action.split(":", 1)[1]
        try:
            observations[name] = read_candidate_file(root, name)
        except (OSError, ValueError):
            raise SystemExit(f"Stopped: could not read allowed candidate file {name}.") from None
        print(f"Tool: read_file({name})", flush=True)
    raise SystemExit("Stopped: step limit reached.")


def create_edit(source, *, decide=ask_role, retry=None):
    sources = {name: read_candidate_file(source, name) for name in sorted(ALLOWED)}
    scratch = Path(mkdtemp(prefix="readinglist-agent-")).resolve()
    for name, content in sources.items():
        (scratch / name).write_bytes(content.encode("utf-8"))

    def apply_edit(observed):
        old = 'print(' + json.dumps(OLD_MESSAGE) + ')'
        new = 'print(' + json.dumps(NEW_MESSAGE) + ')'
        if observed != sources["readinglist.py"] or observed.count(old) != 1:
            raise SystemExit("Stopped: expected exactly one unmodified empty-message print.")
        updated = observed.replace(old, new, 1)
        (scratch / "readinglist.py").write_bytes(updated.encode("utf-8"))
        return updated

    print(f"Scratch candidate: {scratch}", flush=True)
    run(scratch, decide=decide, apply_edit=apply_edit, retry=retry)
    return scratch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--edit", action="store_true", help="allow one specific edit in a new scratch copy")
    args = parser.parse_args()
    source = args.candidate.expanduser().resolve()
    retry = AgentRetry()
    if not args.edit:
        run(source, retry=retry)
        return 0

    from functools import partial
    from team_agents.reviewer_agent import ask_reviewer, with_review_retry
    from .qa_gate import run as run_qa_gate

    scratch = create_edit(source, retry=retry)
    return run_qa_gate(scratch, with_review_retry(partial(ask_reviewer, decide=ask_role), retry), expected_empty=NEW_MESSAGE)


if __name__ == "__main__":
    raise SystemExit(main())
