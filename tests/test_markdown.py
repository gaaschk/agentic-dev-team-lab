from unittest import TestCase

from team_agents.markdown import render_document, to_html, to_markdown


class MarkdownTests(TestCase):
    def test_renders_scalars_lists_and_nested_dicts_as_headings(self):
        data = {
            "release_goal": "Ship it.",
            "in_scope": ["Add notes", "List notes"],
            "out_of_scope": [],
            "owner": {"name": "Pat", "team": "Platform"},
        }
        rendered = to_markdown(data)
        self.assertIn("## Release Goal", rendered)
        self.assertIn("Ship it.", rendered)
        self.assertIn("## In Scope", rendered)
        self.assertIn("- Add notes", rendered)
        self.assertIn("## Out Of Scope", rendered)
        self.assertIn("_(none)_", rendered)
        self.assertIn("### Name", rendered)
        self.assertIn("Pat", rendered)

    def test_renders_a_list_of_dicts_as_bulleted_summaries(self):
        data = {"checks": [{"name": "Restart", "expected": "Book appears"}]}
        rendered = to_markdown(data)
        self.assertIn("**Name**: Restart", rendered)
        self.assertIn("**Expected**: Book appears", rendered)

    def test_render_document_titles_a_dict_result(self):
        doc = render_document("Product Lead", {"release_goal": "Ship it."})
        self.assertTrue(doc.startswith("# Product Lead\n\n"))
        self.assertIn("Ship it.", doc)

    def test_render_document_passes_through_plain_text(self):
        doc = render_document("Stopped", "Stopped. Architect not called.")
        self.assertEqual(doc, "# Stopped\n\nStopped. Architect not called.\n")

    def test_proposed_files_stay_as_code(self):
        files = {"files": [{"path": "a.md", "content": "```\nx = 1\n```\n"}]}
        self.assertIn("**a.md**\n\n````\n```\nx = 1\n```\n````", to_markdown(files))
        self.assertIn("<h4>a.md</h4><pre><code>```\nx = 1", to_html(files))

    def test_html_is_readable_and_escapes_model_text(self):
        page = to_html({"release_goal": "<script>alert(1)</script>",
                        "checks": [{"name": "Restart", "<b>": "x"}], "open_questions": []})
        self.assertIn("<h2>Release Goal</h2>", page)
        self.assertNotIn("<script>", page)
        self.assertNotIn("<b>", page)
        self.assertIn("<strong>Name:</strong>", page)
        self.assertIn("(none)", page)
        self.assertNotIn("{", page)
