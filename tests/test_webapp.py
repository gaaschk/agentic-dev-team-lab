import importlib.util
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
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

    def test_refuses_to_write_outside_the_run_directory(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            emitted = []
            result = {"files": [{"path": "../escape.py", "content": "x"}]}
            self.write_implementation_files(run_dir, result, emit=emitted.append)
            self.assertFalse((run_dir.parent / "escape.py").exists())
            self.assertTrue(any("unsafe" in text for text in emitted))
