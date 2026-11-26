import json
from unittest import TestCase

from team_agents.agent_retry import AgentRetry
from team_agents.implementer_agent import implement
from team_agents.profiles import advisory_profiles
from team_agents.project_manager_agent import available_slots, response_format, validate
from team_agents.product_lead_agent import product_plan
from team_agents.workbench import agent_stages
from workflows.managed import assemble, planning_workflow
from workflows.release import release_workflow
from workflows.runner import Step, run

CHOSEN = ("cso", "debugger", "performance_engineer", "staff_engineer")


def team(**crews):
    return {"sdlc": "lean", "rationale": "Needed.",
            "crews": {"discovery": [], "assurance": [], "readiness": [], **crews}}


def members(*keys):
    return [{"agent": key, "task": f"Advise as {key}."} for key in keys]


class ManagedTeamTests(TestCase):
    def test_project_manager_sees_every_specialist_and_may_pick_any_number(self):
        slots = available_slots()
        self.assertEqual({key for phase in slots.values() for key in phase}, set(advisory_profiles()))
        plan = team(assurance=members(*CHOSEN))
        self.assertEqual(validate(json.dumps(plan)), plan)
        phases = response_format(slots)["json_schema"]["schema"]["properties"]["crews"]["properties"]
        self.assertNotIn("maxItems", phases["assurance"])

    def test_every_profiled_agent_gets_memory_and_tools(self):
        stages = agent_stages()
        self.assertIn("Project Manager", stages)
        self.assertTrue({p.role for p in advisory_profiles().values()} <= stages)
        self.assertNotIn("Operations", stages)

    def test_specialists_sit_around_the_core_release(self):
        plan = team(discovery=members("ceo"), assurance=members("cso", "debugger"),
                    readiness=members("sre"))
        nodes = assemble(plan, release_workflow(decide=None, review=None), decide=None)
        profiles = advisory_profiles()
        names = [node.name for node in nodes if isinstance(node, Step)]
        self.assertEqual(names, [profiles["ceo"].role, "Product Lead", "Architect", "QA",
                                 profiles["cso"].role, profiles["debugger"].role,
                                 "Implementer", "Reviewer", profiles["sre"].role])

    def test_chosen_team_runs_and_advice_reaches_the_core_roles(self):
        report = {"summary": "Advice.", "findings": [], "recommendations": [], "unknowns": []}
        seen = {}

        def decide(role, task, *, stage, **kwargs):
            seen[stage] = task
            if stage == "Project Manager":
                return json.dumps(team(discovery=members("ceo")))
            return json.dumps(report)

        state = run(planning_workflow(decide=decide), {"goal": "g"}, retry=AgentRetry(),
                    prompt=lambda text: "y", emit=lambda text: None)
        nodes = assemble(state["approved_team"], release_workflow(decide=decide, review=None),
                         decide=decide)
        state = run(nodes[:1], state, retry=AgentRetry(), prompt=None, emit=lambda text: None)
        self.assertEqual(state["discovery_1"], report)

        def runner(*, artifacts, **kwargs):
            seen["brief"] = artifacts["brief"].value
            return json.dumps({"release_goal": "g", "in_scope": [], "out_of_scope": [],
                               "acceptance_criteria": ["a"], "open_questions": []})

        product_plan(None, "g", state["discovery_1"], decide=decide, artifact_runner=runner)
        self.assertEqual(seen["brief"]["specialist_advice"], (report,))
        decide_files = lambda role, task, **kw: seen.update(impl=task) or json.dumps(
            {"files": [{"path": "a.py", "content": ""}]})
        implement(None, "g", {}, {}, report, decide=decide_files)
        self.assertIn('"specialist_advice"', seen["impl"])
