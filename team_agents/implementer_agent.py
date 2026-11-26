"""Implementer prompt and structured file-write proposal."""

import json
from pathlib import PurePosixPath

from . import ROLES_DIR
from .agent_retry import RetryableAgentError
from .model_config import resolve_model

FILE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "implementation_files",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["files"],
            "additionalProperties": False,
        },
    },
}


def is_safe_relative_path(path):
    """Reject anything that isn't a plain relative path inside the output directory.

    Backslashes are rejected outright rather than validated as path separators:
    a path is checked with PurePosixPath, but written to disk with the
    platform Path, and on Windows a backslash means something to the second
    that it doesn't to the first — accepting one here would let a path like
    "src\\..\\escape.py" pass this check and still escape on write."""
    if not path or not isinstance(path, str) or "\\" in path:
        return False
    posix = PurePosixPath(path)
    if posix.is_absolute():
        return False
    parts = posix.parts
    return bool(parts) and not any(part in (".", "..") for part in parts)


def parse_files(raw):
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise RetryableAgentError("Implementer did not return clean JSON.") from None
    if (not isinstance(data, dict) or set(data) != {"files"} or not isinstance(data["files"], list)
            or not data["files"]):
        raise RetryableAgentError("Implementer returned no files.")
    for file in data["files"]:
        if (not isinstance(file, dict) or set(file) != {"path", "content"}
                or not isinstance(file["content"], str)
                or not is_safe_relative_path(file["path"])):
            raise RetryableAgentError("Implementer proposed an invalid or unsafe file path.")
    return data


def implement(_failure, goal, plan, design, *, decide):
    implementer = (ROLES_DIR / "implementer.md").read_text(encoding="utf-8")
    implementation_request = json.dumps({
        "goal": goal,
        "approved_plan": plan,
        "approved_design": design,
    })
    raw = decide(
        implementer,
        "Propose file paths and file contents for the approved product below. "
        "Each path must be relative, inside the project, and contain no '..' segment.\n\n"
        + implementation_request,
        response_format=FILE_FORMAT,
        model=resolve_model("LAB_IMPLEMENTER_MODEL"), stage="Implementer",
    )
    return parse_files(raw)
