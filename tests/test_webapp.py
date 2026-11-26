import importlib.util
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from unittest import TestCase, skipUnless
from unittest.mock import patch


def _import_webapp():
    # webapp replaces sys.stdout/stderr with tee wrappers at import time;
    # restore the originals right after so other tests aren't affected.
    real_stdout, real_stderr = sys.stdout, sys.stderr
    import webapp
    sys.stdout, sys.stderr = real_stdout, real_stderr
    return webapp


@skipUnless(importlib.util.find_spec("flask"), "optional Flask web UI dependency not installed")
class OperationsPreflightTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.operations_preflight = staticmethod(_import_webapp().operations_preflight)

    def test_reports_missing_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("Missing environment", self.operations_preflight())

    def test_reports_malformed_url_instead_of_crashing(self):
        env = {"SMART_ROUTER_URL": "router.example:443", "SMART_ROUTER_API_KEY": "key"}
        with patch.dict(os.environ, env, clear=True):
            self.assertIn("isn't a valid http(s) URL", self.operations_preflight())

    def test_reports_invalid_port_instead_of_crashing(self):
        env = {"SMART_ROUTER_URL": "http://example.com:notaport", "SMART_ROUTER_API_KEY": "key"}
        with patch.dict(os.environ, env, clear=True):
            self.assertIn("invalid port", self.operations_preflight())


@skipUnless(importlib.util.find_spec("flask"), "optional Flask web UI dependency not installed")
class StageDocumentWritingTests(TestCase):
    @classmethod
    def setUpClass(cls):
        webapp = _import_webapp()
        cls.write_stage_document = staticmethod(webapp.write_stage_document)
        cls.write_implementation_files = staticmethod(webapp.write_implementation_files)

    def test_writes_markdown_and_json_for_a_dict_result(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.write_stage_document(run_dir, "Product Lead", {"release_goal": "Ship it."})
            self.assertIn("Ship it.", (run_dir / "product-lead.md").read_text())
            self.assertEqual(json.loads((run_dir / "product-lead.json").read_text()),
                              {"release_goal": "Ship it."})

    def test_writes_only_markdown_for_plain_text(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.write_stage_document(run_dir, "Stopped", "Stopped. Architect not called.")
            self.assertTrue((run_dir / "stopped.md").exists())
            self.assertFalse((run_dir / "stopped.json").exists())

    def test_materializes_proposed_files_under_implementation(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            result = {"files": [{"path": "src/app.py", "content": "print(1)"}]}
            self.write_implementation_files(run_dir, result, emit=lambda text: None)
            written = run_dir / "implementation" / "src" / "app.py"
            self.assertEqual(written.read_text(), "print(1)")

    def test_code_is_published_only_after_every_blocking_review_passes(self):
        webapp = _import_webapp()
        proposal = {"files": [{"path": "app.py", "content": "print(1)"}]}
        passed = {"verdict": "PASS", "blockers": []}
        changes = {"verdict": "CHANGES_REQUIRED", "blockers": []}
        for review, security, published in (
                (passed, None, True), (changes, None, False),
                (passed, "pass", True), (passed, "block", False), (changes, "pass", False)):
            with TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                io = webapp.WebIO()
                io.seed_stages(["Implementer", "Reviewer"]
                               + (["Security Review"] if security else []))
                write = webapp.make_output_writer(run_dir, io)
                write("Implementer", proposal, "", True)
                self.assertTrue((run_dir / "draft" / "app.py").exists())
                self.assertFalse((run_dir / "implementation").exists())
                write("Reviewer", review, "", True)
                if security == "pass":
                    self.assertFalse((run_dir / "implementation").exists())
                    write("Security Review", passed, "", True)
                elif security == "block":
                    write("Security Review", None, "Blocked.", False)
                self.assertEqual((run_dir / "implementation" / "app.py").exists(), published,
                                 (review["verdict"], security))

    def test_the_team_gets_the_proposal_even_when_nothing_is_kept(self):
        webapp = _import_webapp()
        with TemporaryDirectory() as tmp:
            shared = Path(tmp).resolve()
            write = webapp.make_output_writer(None, webapp.WebIO(), shared)
            write("Implementer", {"files": [{"path": "app.py", "content": "x"}]}, "", True)
            self.assertEqual((shared / "proposal" / "app.py").read_text(), "x")

    def test_a_failed_document_write_does_not_lose_the_proposal(self):
        """Regression: publishing then crashed the run on a missing proposal."""
        webapp = _import_webapp()
        proposal = {"files": [{"path": "app.py", "content": "print(1)"}]}
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            io = webapp.WebIO()
            write = webapp.make_output_writer(run_dir, io)
            with patch.object(webapp, "write_stage_document", side_effect=OSError("disk full")):
                write("Implementer", proposal, "", True)
            write("Reviewer", {"verdict": "PASS", "blockers": []}, "", True)
            self.assertTrue((run_dir / "implementation" / "app.py").exists())

    def test_refuses_to_write_outside_the_run_directory(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            emitted = []
            result = {"files": [{"path": "../escape.py", "content": "x"}]}
            self.write_implementation_files(run_dir, result, emit=emitted.append)
            self.assertFalse((run_dir.parent / "escape.py").exists())
            self.assertTrue(any("unsafe" in text for text in emitted))

    def test_logs_and_continues_past_a_write_error_for_one_file(self):
        """Regression: a path that collides with an existing directory (or any
        other OSError) must not crash the run — skip that file, write the rest."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "implementation").mkdir()
            (run_dir / "implementation" / "blocked").mkdir()
            emitted = []
            result = {"files": [
                {"path": "blocked", "content": "x"},
                {"path": "ok.py", "content": "print(1)"},
            ]}
            self.write_implementation_files(run_dir, result, emit=emitted.append)
            self.assertTrue(any("Could not write" in text for text in emitted))
            self.assertEqual((run_dir / "implementation" / "ok.py").read_text(), "print(1)")


@skipUnless(importlib.util.find_spec("flask"), "optional Flask web UI dependency not installed")
class StartRunOutputErrorTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.webapp = _import_webapp()

    def test_a_disk_error_is_logged_and_does_not_stop_the_run(self):
        """Regression: on_output previously let an OSError from the filesystem
        writer propagate out of the workflow runner, aborting the whole run
        (and leaving io.done set with later stages never having run)."""
        webapp = self.webapp
        with TemporaryDirectory() as tmp, \
             patch.object(webapp, "write_stage_document", side_effect=OSError("disk full")):
            io = webapp.WebIO()
            io.seed_stages(["Product Lead"])
            io.start_stage("Product Lead")
            run_dir = Path(tmp) / "run"

            def on_output(name, result, rendered, ok):
                io.finish_stage(name, rendered, ok)
                if not run_dir:
                    return
                try:
                    if ok and result is not None:
                        webapp.write_stage_document(run_dir, name, result)
                except OSError as exc:
                    io.emit(f"[operations] Could not write output for {name}: {exc}")

            on_output("Product Lead", {"release_goal": "x"}, '{"release_goal": "x"}', True)
            self.assertTrue(any("Could not write output" in line for line in io.log))
            self.assertEqual(io.stages[0]["status"], "done")

    def test_failed_stage_output_is_still_written_to_disk(self):
        webapp = self.webapp
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            webapp.write_stage_document(run_dir, "Architect", "Stopped. Implementer not called.")
            self.assertIn("Stopped", (run_dir / "architect.md").read_text())

    def test_autonomous_retry_gate_is_decided_by_operations_not_a_human(self):
        webapp = self.webapp
        io = webapp.WebIO()
        io.emit("QA did not return clean JSON.")
        ops = type("Ops", (), {"stage_failed": lambda self, log: ("s" if log else "r")})()
        with patch.object(io, "prompt", side_effect=AssertionError("must not block")):
            self.assertEqual(webapp.make_retry_prompt(io, True, ops)("QA failed."), "s")
        with patch.object(io, "prompt", return_value="r") as human:
            self.assertEqual(webapp.make_retry_prompt(io, False, ops)("QA failed."), "r")
            human.assert_called_once()

    def test_run_record_keeps_whole_outputs_and_marks_any_cut(self):
        """Regression: outputs were silently cut at 1500 characters, so QA
        recorded criteria it never saw as uncovered."""
        webapp = self.webapp
        io = webapp.WebIO()
        io.seed_stages(["QA", "Implementer"])
        for name, output in (("QA", "x" * 5000 + "criterion 3"),
                             ("Implementer", "y" * (webapp.CONTEXT_LIMIT + 1))):
            io.start_stage(name)
            io.finish_stage(name, output, True)
        qa, implementer = (s["output"] for s in webapp.run_record(io)["stages"])
        self.assertTrue(qa.endswith("criterion 3"))
        self.assertIn("truncated", implementer)

    def test_output_dir_must_be_a_string(self):
        client = self.webapp.app.test_client()
        res = client.post("/start", json={"goal": "x", "output_dir": 123})
        self.assertEqual(res.status_code, 400)


def _ops_decision(action):
    return json.dumps({"tool": "decide", "path": "", "content": "", "command": "", "action": action,
                       "route": "", "wait_seconds": 0, "timeout_seconds": 60, "reason": "test"})


@skipUnless(importlib.util.find_spec("flask"), "optional Flask web UI dependency not installed")
class RetryLayeringTests(TestCase):
    """Count primary model calls through the real runtime composition, since
    testing each retry layer alone can't catch them multiplying."""

    @classmethod
    def setUpClass(cls):
        cls.webapp = _import_webapp()

    def setUp(self):
        patcher = patch.dict(os.environ, {"LAB_AGENTS_HOME": mkdtemp()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_stage(self, *, autonomous, failure, advisor=(), human=None):
        from workflows.runner import Step, run
        webapp = self.webapp
        counts = {"primary": 0, "primary_before_prompt": None}
        always = advisor if isinstance(advisor, str) else None
        advisor = [] if always else list(advisor)

        def ask(role, task, *, stage, **kwargs):
            if stage == "Operations":
                return always or advisor.pop(0)
            if stage.endswith(" memory"):
                return json.dumps({"tool": "decide", "path": "", "content": "", "command": "",
                                   "notes": ""})
            counts["primary"] += 1
            raise failure

        io = webapp.WebIO()
        if human:
            def prompt(text):
                counts["primary_before_prompt"] = counts["primary"]
                return human
            io.prompt = prompt
        _, team, retry = webapp.build_runtime(io, autonomous, ask=ask, sleep=lambda s: None)
        step = Step("QA", ("goal",), "out",
                    lambda _failure, goal: team("role", goal, stage="QA", model="m"),
                    auto_retryable=True)
        with self.assertRaises(SystemExit):
            run((step,), {"goal": "g"}, retry=retry, prompt=lambda t: "y", emit=io.emit)
        self.assertEqual(advisor, [], "every scripted operations decision should be used")
        return counts

    def test_interactive_503_reaches_the_human_after_one_call(self):
        from team_agents.agent_retry import RetryableAgentError
        counts = self.run_stage(autonomous=False, failure=RetryableAgentError("HTTP 503."), human="s")
        self.assertEqual(counts["primary_before_prompt"], 1)
        self.assertEqual(counts["primary"], 1)

    def test_interactive_timeout_keeps_the_single_scoped_retry(self):
        from team_agents.agent_retry import TransientTimeout
        counts = self.run_stage(autonomous=False, failure=TransientTimeout("slow."), human="s")
        self.assertEqual(counts["primary_before_prompt"], 2)

    def test_autonomous_retries_are_exactly_what_operations_decides(self):
        from team_agents.agent_retry import TransientTimeout
        counts = self.run_stage(autonomous=True, failure=TransientTimeout("slow."), advisor=[
            _ops_decision("retry"), _ops_decision("retry"), _ops_decision("give_up"),  # call level
            _ops_decision("give_up"),                                                  # stage gate
        ])
        self.assertEqual(counts["primary"], 3)

    def test_autonomous_stage_retry_does_not_stack_a_second_budget(self):
        from team_agents.agent_retry import RetryableAgentError
        counts = self.run_stage(autonomous=True, failure=RetryableAgentError("HTTP 503."), advisor=[
            _ops_decision("give_up"),   # call level, first stage attempt
            _ops_decision("retry"),     # stage gate: retry the stage
            _ops_decision("give_up"),   # call level, second stage attempt
            _ops_decision("give_up"),   # stage gate: stop
        ])
        self.assertEqual(counts["primary"], 2)

    def test_an_advisor_that_always_retries_is_stopped_by_the_run_wide_limit(self):
        """Regression: per-call and per-stage ceilings used to reset on every
        stage replay and compound (25 x 26 = 650 calls)."""
        from team_agents.agent_retry import TransientTimeout
        from team_agents.operations import MAX_FAILURES
        counts = self.run_stage(autonomous=True, failure=TransientTimeout("slow."),
                                advisor=_ops_decision("retry"))
        self.assertEqual(counts["primary"], MAX_FAILURES)
