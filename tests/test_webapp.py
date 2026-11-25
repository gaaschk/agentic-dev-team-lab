import importlib.util
import os
from unittest import TestCase, skipUnless
from unittest.mock import patch

if importlib.util.find_spec("flask"):
    from webapp import operations_preflight


@skipUnless(importlib.util.find_spec("flask"), "optional Flask web UI dependency not installed")
class OperationsPreflightTests(TestCase):
    def test_reports_missing_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("Missing environment", operations_preflight())

    def test_reports_malformed_url_instead_of_crashing(self):
        env = {"SMART_ROUTER_URL": "router.example:443", "SMART_ROUTER_API_KEY": "key"}
        with patch.dict(os.environ, env, clear=True):
            self.assertIn("isn't a valid http(s) URL", operations_preflight())

    def test_reports_invalid_port_instead_of_crashing(self):
        env = {"SMART_ROUTER_URL": "http://example.com:notaport", "SMART_ROUTER_API_KEY": "key"}
        with patch.dict(os.environ, env, clear=True):
            self.assertIn("invalid port", operations_preflight())
