"""Render an agent's structured JSON output for people: a Markdown document
on disk, and HTML for the web page. Proposed files stay as code.

A generic renderer, not a per-document template: every role returns a dict
(or, for Implementer, free-form text was dropped in favor of structured
files), so one renderer covers all of them without hand-written sections
that drift out of sync with each role's schema.
"""


import re
from html import escape


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


def _is_file(item):
    return isinstance(item, dict) and set(item) == {"path", "content"}


def _code_block(file):
    content = str(file["content"])
    # A fence longer than any inside the file, so its own backticks can't end it.
    longest = max((len(run) for run in re.findall(r"`{3,}", content)), default=2)
    fence = "`" * (longest + 1)
    return f"**{file['path']}**\n\n{fence}\n{content.rstrip()}\n{fence}"


def _render_list(items, level):
    if not items:
        return "_(none)_"
    if all(_is_file(item) for item in items):
        return "\n\n".join(_code_block(item) for item in items)
    lines = []
    for item in items:
        if isinstance(item, dict):
            fields = [f"**{_title(k)}**: {v}" for k, v in item.items()]
            lines.append("- " + "\n  ".join(f + "  " for f in fields).rstrip())
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


def to_html(value, level=2):
    """HTML for any stage result. Everything from a model is escaped."""
    if _is_file(value):
        return (f"<h4>{escape(str(value['path']))}</h4>"
                f"<pre><code>{escape(str(value['content']))}</code></pre>")
    if isinstance(value, dict):
        heading = f"h{min(level, 6)}"
        return "".join(f"<{heading}>{escape(_title(k))}</{heading}>{to_html(v, level + 1)}"
                       if level < 4 else
                       f"<div><strong>{escape(_title(k))}:</strong> {to_html(v, level + 1)}</div>"
                       for k, v in value.items())
    if value is None or isinstance(value, (list, tuple)) and not value:
        return "<p><em>(none)</em></p>"
    if isinstance(value, (list, tuple)):
        if all(_is_file(item) for item in value):
            return "".join(to_html(item) for item in value)
        # Records inside lists use compact labelled lines rather than headings.
        return "<ul>" + "".join(f"<li>{to_html(item, max(level, 4))}</li>" for item in value) + "</ul>"
    return f'<span class="text">{escape(str(value))}</span>'
