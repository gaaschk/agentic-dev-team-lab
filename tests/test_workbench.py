import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from team_agents.workbench import Team


def step(tool, path="", content="", notes=""):
    return json.dumps({"tool": tool, "path": path, "content": content, "command": "", "notes": notes})


class FakeDecide:
    """Answers memory steps from a script, and final answers with 'final'."""

    def __init__(self, memory_steps):
        self.memory_steps = list(memory_steps)
        self.calls = []

    def __call__(self, role, task, *, stage, **kwargs):
        self.calls.append((stage, task, kwargs))
        if stage.endswith(" memory"):
            return self.memory_steps.pop(0)
        return "final"


class TeamTests(TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        patcher = patch.dict(os.environ, {"LAB_AGENTS_HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_agent_reads_memory_and_its_notes_reach_the_final_task(self):
        (Path(self.home) / "architect").mkdir()
        (Path(self.home) / "architect" / "lessons.md").write_text("Keep designs to one module.")
        decide = FakeDecide([step("read_file", path="lessons.md"),
                             step("decide", notes="Prefer a single module.")])
        team = Team(decide, emit=lambda t: None, shell=False)
        self.assertEqual(team("role", "Design it.", stage="Architect", model="m"), "final")
        memory_task = json.loads(decide.calls[1][1])
        self.assertIn("one module", memory_task["observations"][0]["result"])
        self.assertIn("Prefer a single module.", decide.calls[-1][1])

    def test_memory_check_runs_once_per_stage_even_on_retry(self):
        decide = FakeDecide([step("decide", notes="n")])
        team = Team(decide, emit=lambda t: None, shell=False)
        team("role", "t", stage="QA", model="m")
        team("role", "t", stage="QA", model="m")
        self.assertEqual([c[0] for c in decide.calls], ["QA memory", "QA", "QA"])

    def test_tool_choice_calls_are_passed_through_untouched(self):
        decide = FakeDecide([])
        team = Team(decide, emit=lambda t: None, shell=False)
        team("role", "t", stage="Architect tool choice", model="m")
        self.assertEqual(decide.calls, [("Architect tool choice", "t", {"model": "m"})])

    def test_reflection_writes_lessons_to_each_agents_own_memory(self):
        decide = FakeDecide([
            step("decide"),
            step("write_file", path="memory.md", content="Plan was approved."),
            step("decide"),
        ])
        team = Team(decide, emit=lambda t: None, shell=False)
        team("role", "t", stage="Product Lead", model="m")
        team.reflect({"stages": [{"name": "Product Lead", "status": "done"}]})
        self.assertEqual((Path(self.home) / "product-lead" / "memory.md").read_text(),
                         "Plan was approved.")

    def test_a_failing_memory_check_does_not_block_the_agent(self):
        def decide(role, task, *, stage, **kwargs):
            if stage.endswith(" memory"):
                raise SystemExit("router down")
            return "final"
        log = []
        team = Team(decide, emit=log.append, shell=False)
        self.assertEqual(team("role", "t", stage="Reviewer", model="m"), "final")
        self.assertTrue(any("Skipped memory check" in line for line in log))

    def test_one_memory_failure_turns_memory_off_for_the_run(self):
        memory_calls = []

        def decide(role, task, *, stage, **kwargs):
            if stage.endswith(" memory"):
                memory_calls.append((stage, kwargs.get("timeout")))
                raise SystemExit("recovery gave up")
            return "final"

        team = Team(decide, emit=lambda t: None, shell=False)
        for stage in ("Product Lead", "Architect", "QA"):
            self.assertEqual(team("role", "t", stage=stage, model="m"), "final")
        team.reflect({})
        self.assertEqual(memory_calls, [("Product Lead memory", 90)])

    def test_reflection_that_writes_nothing_is_reported(self):
        decide = FakeDecide([step("decide"), json.dumps({"tool": "decide", "path": "",
                                                          "content": "", "command": ""})])
        log = []
        team = Team(decide, emit=log.append, shell=False)
        team("role", "t", stage="QA", model="m")
        team.reflect({})
        self.assertIn("[QA] Recorded nothing to memory this run.", log)
        reflect_format = decide.calls[-1][2]["response_format"]["json_schema"]["schema"]
        self.assertNotIn("notes", reflect_format["properties"])

    def test_a_failed_write_does_not_count_as_recorded_memory(self):
        decide = FakeDecide([step("decide"),
                             step("write_file", path="../escape.md", content="lost"),
                             step("decide")])
        log = []
        team = Team(decide, emit=log.append, shell=False)
        team("role", "t", stage="QA", model="m")
        team.reflect({})
        self.assertIn("[QA] Recorded nothing to memory this run.", log)

