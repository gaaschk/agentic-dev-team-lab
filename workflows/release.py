"""Release workflow: the order, named handoffs, and approval rules."""

import json
from functools import partial

from team_agents.architect_agent import technical_design
from team_agents.implementer_agent import implement
from team_agents.product_lead_agent import product_plan
from team_agents.qa_agent import request_plan as request_qa
from team_agents.reviewer_agent import reviewed_preview
from team_agents.role_agents import run as run_agent

from .runner import Approval, Step


def pretty(data):
    return json.dumps(data, indent=2)


def release_workflow(*, decide, review, artifact_runner=run_agent):
    def qa(failure, plan, design):
        return request_qa(plan, design, decide=decide, failure=failure, artifact_runner=artifact_runner)

    def review_preview(_failure, plan, design, proposal):
        return reviewed_preview(review, plan, design, proposal)

    return (
        Step("Product Lead", ("goal",), "plan",
             partial(product_plan, decide=decide, artifact_runner=artifact_runner), render=pretty),
        Approval("plan", "approved_plan", "Approve this plan? [y/N] ",
                 "Stopped. Architect not called."),
        Step("Architect", ("goal", "approved_plan"), "design",
             partial(technical_design, decide=decide, artifact_runner=artifact_runner),
             before="\nHanding the approved plan to the Architect…\n",
             render=lambda result: "\n" + pretty(result),
             retry_stop="Stopped. Implementer not called."),
        Approval("design", "approved_design", "Approve this design? [y/N] ",
                 "Stopped. Implementer not called."),
        Step("QA", ("approved_plan", "approved_design"), "qa_plan", qa,
             before="\nPlanning independent QA checks before implementation…\n",
             render=lambda result: pretty(result) + "\nQA plan only. No tests have run."),
        Step("Implementer", ("goal", "approved_plan", "approved_design"), "proposal",
             partial(implement, decide=decide),
             before="\nImplementation preview only. No files will be written.\n"),
        Step("Reviewer", ("approved_plan", "approved_design", "proposal"), "review",
             review_preview, before="\nReviewing the implementation preview…\n", render=pretty),
    )
