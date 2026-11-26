"""Render an agent's structured JSON output as a human-readable Markdown document.

A generic renderer, not a per-document template: every role returns a dict
(or, for Implementer, free-form text was dropped in favor of structured
files), so one renderer covers all of them without hand-written sections
that drift out of sync with each role's schema.
"""


def _title(key):
    return str(key).replace("_", " ").replace("-", " ").strip().title()


def _render_value(value, level):
    if isinstance(value, dict):
        return to_markdown(value, level)
    if isinstance(value, list):
        return _render_list(value, level)
    if value is None:
        return "_(none)_"
    return str(value)


def _render_list(items, level):
    if not items:
        return "_(none)_"
    lines = []
    for item in items:
        if isinstance(item, dict):
            summary = "; ".join(f"**{_title(k)}**: {v}" for k, v in item.items())
            lines.append(f"- {summary}")
        elif isinstance(item, list):
            nested = _render_list(item, level)
            indented = "\n".join("  " + line for line in nested.splitlines())
            lines.append(f"-\n{indented}")
        else:
            lines.append(f"- {item}")
    return "\n".join(lines)


def to_markdown(data, level=2):
    """Render a dict as nested Markdown headings; lists become bullets."""
    parts = []
    for key, value in data.items():
        parts.append("#" * min(level, 6) + " " + _title(key))
        parts.append(_render_value(value, level + 1))
    return "\n\n".join(parts)


def render_document(title, result):
    """A full Markdown document for one stage's result, whatever shape it is."""
    if isinstance(result, dict):
        body = to_markdown(result, 2)
    elif isinstance(result, list):
        body = _render_list(result, 2)
    else:
        body = str(result).strip()
    return f"# {title}\n\n{body}\n"
