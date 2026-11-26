"""Implementer prompt and structured file-write proposal."""

import json
import re
from pathlib import PurePosixPath

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")

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

    Backslashes and drive letters are rejected outright rather than validated
    as path separators: a path is checked with PurePosixPath, but written to
    disk with the platform Path, and on Windows both a backslash and a drive
    prefix like "C:" mean something to the second that they don't to the
    first — "C:/evil.txt" isn't absolute by POSIX rules, but WindowsPath
    treats it as drive-rooted, so joining it onto run_dir replaces run_dir
    entirely instead of nesting inside it."""
    if not path or not isinstance(path, str) or "\\" in path or _WINDOWS_DRIVE.match(path):
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


def implement(_failure, goal, plan, design, *reports, decide):
    implementer = (ROLES_DIR / "implementer.md").read_text(encoding="utf-8")
    implementation_request = json.dumps({
        "goal": goal,
        "approved_plan": plan,
        "approved_design": design,
        # Advice from specialists; it never widens the approved scope.
        **({"specialist_advice": reports} if reports else {}),
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
