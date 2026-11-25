import importlib.util
import os
import sys
from unittest import TestCase, skipUnless
from unittest.mock import patch


@skipUnless(importlib.util.find_spec("flask"), "optional Flask web UI dependency not installed")
class OperationsPreflightTests(TestCase):
    @classmethod
    def setUpClass(cls):
        # webapp replaces sys.stdout/stderr with tee wrappers at import time;
        # restore the originals right after so other tests aren't affected.
        real_stdout, real_stderr = sys.stdout, sys.stderr
        import webapp
        sys.stdout, sys.stderr = real_stdout, real_stderr
        cls.operations_preflight = staticmethod(webapp.operations_preflight)

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
