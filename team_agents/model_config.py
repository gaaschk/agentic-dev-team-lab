"""Resolve model selection consistently for the workflow and examples."""

import os


def resolve_model(role_variable=None):
    override = os.getenv(role_variable, "").strip() if role_variable else ""
    return override or os.getenv("DEFAULT_MODEL", "").strip() or "smart-auto"
