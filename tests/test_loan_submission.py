import contextlib
import io
import json
import sys
from unittest import TestCase
from unittest.mock import patch

import plan_release
from examples.loan_submission import BRIEF, CRITERIA
from examples.loan_submission.acceptance import check, fixture


class LoanSubmissionTests(TestCase):
    def test_acceptance_checks_distinguish_good_and_broken_fixtures(self):
        self.assertEqual(check(fixture), [])
        self.assertEqual(check(lambda app: fixture(app, duplicate_event=True)), [
            "Retrying a submitted application must leave its event count unchanged."
        ])

    def test_acceptance_checks_do_not_confuse_submission_with_approval(self):
        def auto_approve(app):
            result, errors = fixture(app)
            if app["status"] == "DRAFT" and not errors:
                result["status"] = "APPROVED"
            return result, errors
        self.assertEqual(check(auto_approve), [
            "Valid draft must become SUBMITTED and create exactly one event."
        ])

    def test_team_hands_all_loan_criteria_to_qa_before_implementation(self):
        plan = {
            "release_goal": "Submit synthetic loan drafts for review.",
            "in_scope": ["Draft validation", "Submission", "Replay"],
            "out_of_scope": ["Credit decisions", "Disbursement"],
            "acceptance_criteria": list(CRITERIA),
            "open_questions": [],
        }
        design = {
            "components": ["Pure state transition"],
            "persistence": "Caller holds state in memory.",
            "interface": "submit(application) returns updated application and error fields.",
            "build_order": ["Validate draft, then transition or return unchanged."],
            "verification": ["Exercise valid draft, missing fields, and replay."],
            "risks": ["Duplicate events"],
            "blockers": [],
        }
        scenarios = [
            ("Valid draft", "Set complete DRAFT.", "Submit once.", "SUBMITTED with one event."),
            ("Missing fields", "Use a draft missing the amount.", "Submit once.", "Return error; no state change."),
            ("Replay", "Use a submitted application with one event.", "Submit again.", "Still one event."),
        ]
        qa_plan = {"checks": [
            dict(criterion_id=i, name=name, setup=setup, action=action, expected=expected)
            for i, (name, setup, action, expected) in enumerate(scenarios, 1)
        ]}
        candidate = {"files": [{"path": "submission.py",
                                 "content": "Review fixture: creates another event on retry."}]}
        verdict = {"verdict": "CHANGES_REQUIRED", "blockers": [{
            "file": "submission.py", "evidence": "Increments the event count on replay.",
            "consequence": "The same application creates a duplicate event.",
        }]}
        replies = [
            {"action": "read:brief"}, plan,
            {"action": "read:approved_plan"}, {"action": "submit"}, design,
            {"action": "read:requirements"}, qa_plan,
            candidate, {"action": "read:criteria"}, verdict,
        ]
        with (
            patch.object(sys, "argv", ["plan_release.py", "--example", "loan-submission"]),
            patch.object(plan_release, "ask_role",
                         side_effect=[r if isinstance(r, str) else json.dumps(r) for r in replies]) as router,
            patch("builtins.input", side_effect=["y", "y"]) as prompt,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            plan_release.main()
        self.assertEqual(prompt.call_count, 2)
        calls = router.call_args_list
        product_call = next(c for c in calls if c.kwargs["stage"] == "Product Lead")
        self.assertEqual(json.loads(product_call.args[1].rsplit("\n\n", 1)[1])["brief"], BRIEF)
        qa_call = next(c for c in calls if c.kwargs["stage"] == "QA")
        self.assertLess(calls.index(qa_call), next(
            i for i, c in enumerate(calls) if c.kwargs["stage"] == "Implementer"
        ))
        obs = json.loads(qa_call.args[1].rsplit("\n\n", 1)[1])["requirements"]
        self.assertEqual(obs["criteria"], [
            {"criterion_id": i, "requirement": criterion}
            for i, criterion in enumerate(CRITERIA, 1)
        ])
        self.assertNotIn(candidate["files"][0]["content"], qa_call.args[1])
        self.assertIn('"verdict": "CHANGES_REQUIRED"', output.getvalue())
        self.assertIn("QA plan only. No tests have run.", output.getvalue())
