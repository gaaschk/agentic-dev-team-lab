from unittest import TestCase
from unittest.mock import patch

from team_agents.model_config import resolve_model


class ModelConfigTests(TestCase):
    def test_precedence(self):
        with patch.dict("os.environ", {
            "DEFAULT_MODEL": "smart-sonnet",
            "LAB_QA_MODEL": "smart-auto",
            "LAB_REVIEW_MODEL": " ",
        }, clear=True):
            self.assertEqual(resolve_model("LAB_QA_MODEL"), "smart-auto")
            self.assertEqual(resolve_model("LAB_REVIEW_MODEL"), "smart-sonnet")
            self.assertEqual(resolve_model("LAB_PRODUCT_MODEL"), "smart-sonnet")
            self.assertEqual(resolve_model(), "smart-sonnet")

    def test_unset_defaults_to_smart_auto(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(resolve_model(), "smart-auto")
            self.assertEqual(resolve_model("LAB_IMPLEMENTER_MODEL"), "smart-auto")
