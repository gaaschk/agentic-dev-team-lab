import contextlib
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import plan_release
from team_agents.agent_retry import AgentRetry, RetryableAgentError
from team_agents.profiles import WORKFLOW_ROLES, advisory_profiles, instructions_for, load_profiles
from team_agents.project_manager_agent import available_slots
from team_agents.advisory import MAX_ARTIFACT_BYTES, main, read_artifact, run_specialist


class AgentProfilesTests(TestCase):
    def test_all_profiles_have_matching_role_instructions(self):
        core = {name: profile for name, profile in load_profiles().items() if name in WORKFLOW_ROLES}
        specialists = advisory_profiles()
        self.assertEqual(set(core), {"product_lead", "architect", "qa", "implementer", "reviewer", "project_manager", "operations"})
        self.assertEqual(set(specialists), set("""
            ceo cso debugger deploy_engineer design_engineer design_explorer design_partner
            designer_who_codes devex_lead documentation_author dx_tester eng_manager office_hours
            performance_engineer qa_lead qa_reporter release_engineer retro_facilitator senior_designer
            spec_author sre staff_engineer support_agent technical_writer
        """.split()))
        self.assertFalse(set(core) & set(specialists))
        for profiles in (core, specialists):
            for key, profile in profiles.items():
                text = instructions_for(key)
                self.assertIn(profile.persona, text)
                self.assertIn("responsibilities, not permissions", text)
                for capability in profile.capabilities:
                    self.assertIn(capability, text)

    def test_new_agent_only_needs_one_markdown_file(self):
        report = {"summary": "One observation.", "findings": [], "recommendations": [], "unknowns": []}
        with TemporaryDirectory() as root:
            roles = Path(root)
            (roles / "release_notes.md").write_text(
                "# Release Notes\n\nDraft a concise changelog.\n", encoding="utf-8",
            )
            with patch("team_agents.profiles.ROLES_DIR", roles):
                self.assertEqual(set(advisory_profiles()), {"release_notes"})
                self.assertIn("release_notes", available_slots()["readiness"])
                decide = Mock(return_value=json.dumps(report))
                self.assertEqual(run_specialist("release_notes", "Draft notes", [],
                                               decide=decide, retry=AgentRetry()), report)
                self.assertIn("Draft a concise changelog.", decide.call_args.args[0])

    def test_specialist_cli_passes_only_explicit_evidence_and_uses_selected_route(self):
        verdict = {"summary": "Improve the quickstart.", "findings": [], "recommendations": [], "unknowns": []}
        with TemporaryDirectory() as root:
            artifact = Path(root) / "guide.txt"
            artifact.write_text("Existing guide", encoding="utf-8")
            (Path(root) / "unrelated.txt").write_text("Never send this", encoding="utf-8")
            argv = ["team_agents", "--artifact", str(artifact), "technical_writer", "Review the guide."]
            with (patch.object(sys, "argv", argv),
                  patch.dict("os.environ", {"LAB_TECHNICAL_WRITER_MODEL": "test-model"}, clear=True),
                  patch.object(plan_release, "ask_role", return_value=json.dumps(verdict)) as decide,
                  contextlib.redirect_stdout(io.StringIO()) as output):
                main()
        role, task = decide.call_args.args
        self.assertIn("Technical Writer", role)
        self.assertEqual(json.loads(task.rsplit("\n\n", 1)[1])["evidence"], [
            {"name": "guide.txt", "contents": "Existing guide"}
        ])
        self.assertNotIn(str(artifact.parent), task)
        self.assertEqual(decide.call_args.kwargs["model"], "test-model")
        self.assertEqual(json.loads(output.getvalue()), verdict)

    def test_specialist_report_cannot_request_actions(self):
        decide = Mock(return_value='{"summary":"Ready","findings":[],"recommendations":[],"unknowns":[],"deploy":true}')
        with (patch("builtins.input", return_value="r"), contextlib.redirect_stderr(io.StringIO()),
              self.assertRaisesRegex(RetryableAgentError, "report format")):
            run_specialist("deploy_engineer", "Assess readiness.", [], decide=decide, retry=AgentRetry())
        self.assertEqual(decide.call_count, 2)

    def test_artifact_size_and_symlink_bounds(self):
        with TemporaryDirectory() as root:
            artifact = Path(root) / "large.txt"
            artifact.write_bytes(b"a" * (MAX_ARTIFACT_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "32 KiB"):
                read_artifact(artifact)
            link = Path(root) / "link.txt"
            link.symlink_to(artifact)
            with self.assertRaises(OSError):
                read_artifact(link)
