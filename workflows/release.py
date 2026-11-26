"""Release workflow: the order, named handoffs, and approval rules."""

import json
from functools import partial

from team_agents.architect_agent import technical_design
from team_agents.implementer_agent import implement
from team_agents.product_lead_agent import product_plan
from team_agents.qa_agent import request_plan as request_qa
from team_agents.reviewer_agent import reviewed_preview
from team_agents.role_agents import run as run_agent
from team_agents.specialists import SECURITY, UX, security_review, selected, ux_design

from .runner import Approval, Step


def pretty(data):
    return json.dumps(data, indent=2)


def release_workflow(*, decide, review, artifact_runner=run_agent):
    def qa(failure, plan, design, ux_spec):
        return request_qa(plan, design, ux_spec, decide=decide, failure=failure,
                          artifact_runner=artifact_runner)

    def review_preview(_failure, plan, design, proposal):
        return reviewed_preview(review, plan, design, proposal)

    return (
        Step("Product Lead", ("goal",), "plan",
             partial(product_plan, decide=decide, artifact_runner=artifact_runner),
             before="\nAsking the Product Lead for a release plan…\n", render=pretty,
             auto_retryable=True),
        Approval("plan", "approved_plan", "Approve this plan? [y/N] ",
                 "Stopped. Architect not called."),
        Step(UX, ("goal", "approved_plan"), "ux_spec",
             partial(ux_design, decide=decide, artifact_runner=artifact_runner),
             before="\nAsking the UX Designer to shape the experience…\n", render=pretty,
             auto_retryable=True, when=lambda _goal, plan: selected(plan, UX)),
        Step("Architect", ("goal", "approved_plan", "ux_spec"), "design",
             partial(technical_design, decide=decide, artifact_runner=artifact_runner),
             before="\nHanding the approved plan to the Architect…\n",
             render=lambda result: "\n" + pretty(result),
             retry_stop="Stopped. Implementer not called.", auto_retryable=True),
        Approval("design", "approved_design", "Approve this design? [y/N] ",
                 "Stopped. Implementer not called."),
        Step("QA", ("approved_plan", "approved_design", "ux_spec"), "qa_plan", qa,
             before="\nPlanning independent QA checks before implementation…\n",
             render=lambda result: pretty(result) + "\nQA plan only. No tests have run.",
             auto_retryable=True),
        Step("Implementer", ("goal", "approved_plan", "approved_design", "ux_spec"), "proposal",
             partial(implement, decide=decide),
             before="\nAsking the Implementer to propose file paths and contents…\n",
             render=pretty, auto_retryable=True),
        Step("Reviewer", ("approved_plan", "approved_design", "proposal"), "review",
             review_preview, before="\nReviewing the implementation preview…\n", render=pretty,
             auto_retryable=True),
        Step(SECURITY, ("approved_plan", "approved_design", "proposal"), "security_review",
             partial(security_review, decide=decide, artifact_runner=artifact_runner),
             before="\nAsking the Security Reviewer to check the implementation…\n", render=pretty,
             auto_retryable=True, when=lambda plan, *_: selected(plan, SECURITY)),
    )
