"""Bundled example briefs, loaded only when selected."""

from importlib import import_module

EXAMPLES = {
    "loan-submission": "examples.loan_submission",
    "reading-list": "examples.reading_list",
}


def load_brief(name):
    return import_module(EXAMPLES[name]).BRIEF
