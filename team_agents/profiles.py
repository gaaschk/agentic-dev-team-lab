"""Discover agents from Markdown. Definitions describe work, never grant tools."""

from dataclasses import dataclass
import re

from . import ROLES_DIR

CONFIG_DIR = ROLES_DIR.parent.parent / "config"
# These names have typed handoffs rather than the general advisory report.
WORKFLOW_ROLES = frozenset({"product_lead", "architect", "qa", "implementer", "reviewer", "project_manager", "operations"})
CREW_PHASES = ("discovery", "assurance", "readiness")
ROLE_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
SECTION = re.compile(r"(?m)^##[ \t]+([^\n]+)[ \t]*$")


@dataclass(frozen=True)
class Profile:
    role: str
    goal: str
    persona: str
    capabilities: tuple[str, ...]
    markdown: str
    crew_phase: str | None

    def instructions(self):
        return (
            "Role capabilities are responsibilities, not permissions. Follow the workflow's "
            "input and output contract and do not claim actions you did not take.\n\n"
            + self.markdown
        )


def read_profile(path):
    text = path.read_text(encoding="utf-8").strip()
    if not text or not ROLE_ID.fullmatch(path.stem):
        raise ValueError(f"Invalid agent definition: {path.name}. Use a nonempty snake_case Markdown file.")
    lines = text.splitlines()
    title = re.fullmatch(r"#\s+(.+)", lines[0])
    role = title.group(1).strip() if title else path.stem.replace("_", " ").title()
    body = "\n".join(lines[1:] if title else lines).strip()
    if not role or not body:
        raise ValueError(f"Missing instructions in {path.name}.")
    sections = SECTION.split(body)
    parts = {name.strip().casefold(): value.strip() for name, value in zip(sections[1::2], sections[2::2])}
    capabilities = tuple(line[2:].strip() for line in parts.get("capabilities", "").splitlines()
                         if line.startswith("- ") and line[2:].strip())
    goal = parts.get("goal") or (body.split("\n\n", 1)[0] if not body.startswith("#") else role)
    phase = parts.get("crew phase")
    if phase is not None and phase not in CREW_PHASES:
        raise ValueError(f"Invalid Crew Phase in {path.name}.")
    return Profile(role, goal, parts.get("persona", ""), capabilities, text, phase)


def load_profiles():
    return {
        path.stem: read_profile(path)
        for path in sorted(ROLES_DIR.glob("*.md"))
        if path.is_file() and not path.is_symlink()
    }


def advisory_profiles():
    return {key: profile for key, profile in load_profiles().items() if key not in WORKFLOW_ROLES}


def instructions_for(key):
    # Never allow an arbitrary path or an indirect fetch.
    if not isinstance(key, str) or not ROLE_ID.fullmatch(key):
        raise ValueError("Invalid agent ID.")
    path = ROLES_DIR / f"{key}.md"
    if path.is_symlink() or not path.is_file():
        raise KeyError(key)
    return read_profile(path).instructions()
