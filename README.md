# Agentic Dev Team Lab

This project is a reusable AI software team. It takes a product brief and
produces a release plan, technical design, independent QA plan, implementation
preview, and code review. The reading-list app is an example, not part of the
team's runtime.

## Run the team

Run from the project root with Python 3.10 or newer. The live workflow uses
`SMART_ROUTER_URL` and `SMART_ROUTER_API_KEY` from the environment. Keep the key
out of the project.

```sh
python3 -B plan_release.py "Build a local CLI that saves short notes and lists them after restart."
```

Pass either a quoted prompt or one bundled example, not both:

```sh
python3 -B plan_release.py --example loan-submission
python3 -B plan_release.py --example reading-list
```

1. The **Product Lead** reads your brief and proposes release scope and acceptance criteria.
2. After you approve the plan, the **Architect** proposes a design. Approve it separately.
3. **QA** reads the approved scope, numbered acceptance criteria, and public
   interface, but not the implementation. It proposes at least one black-box
   check per criterion: setup, action, and observable expected result.
4. The **Implementer** previews the proposed files. The runner does not save or run them.
5. The **Reviewer** reads the exact preview and approved criteria and reports code blockers.

QA plans are **not test results**. The generic workflow never runs model-proposed
commands or code. Actual pass/fail evidence must come from a separately wired,
deterministic check. The [reading-list example](examples/reading_list/README.md)
shows that separation with its own sandboxed runner and execution approval.

The baseline runner is linear, synchronous, and in memory. It doesn't save
checkpoints or resume after the process exits. No agent decides whether to
bypass an approval or execute a proposal.

## Web UI (v1, minimal)

`webapp.py` runs the same native workflow behind a single-page Flask UI:
type a goal, approve/reject the plan and design in the browser, and watch
each role's output stream in. One run at a time, native runtime only.

```sh
./run.sh
```

`run.sh` creates `.venv` and installs `requirements-web.txt` on first run,
then starts the server every time after. Then open http://127.0.0.1:5050.
`SMART_ROUTER_URL`/`SMART_ROUTER_API_KEY` still have to be set in the
environment, same as the CLI.

## Retry or stop

On a timeout, temporary router failure, or invalid agent response, choose
`r` to retry once, `a` to retry once automatically for this and later failures in
this run, or `s` (or Enter) to stop. A failed retry ends the run. This does not
override a real blocker or substitute for an approval.

The lab sends `X-Smart-Router-Memory: off` and requires acknowledgment. This
disables GBrain retrieval and save-back for these requests, not usage logging.
Each call prints elapsed time and an ID you can find in the router's
`[request_trace]` logs.

## Layout

- `plan_release.py`: CLI and router adapter.
- `workflows/release.py`: declares the team order, named handoffs, and approval gates.
- `workflows/runner.py`: runs those steps and enforces approvals and retry limits.
- `team_agents/`: one Python module per role (`product_lead_agent.py`,
  `architect_agent.py`, `qa_agent.py`, `implementer_agent.py`, and
  `reviewer_agent.py`), plus shared helpers and Markdown prompts under `roles/`.
- `examples/reading_list/`: sample contract, Reviewer fixtures, one bounded
  Implementer exercise, and deterministic reading-list QA.
- [`examples/loan_submission/`](examples/loan_submission/README.md): fictional state transitions and duplicate-submission checks.
- `tests/`: regression tests for the lab and examples. Run
  `python3 -B -m unittest discover -s tests -q` from the project root.

Set `DEFAULT_MODEL` to choose the route for every role, for example:

```sh
DEFAULT_MODEL=smart-auto python3 -B plan_release.py "Build a local notes CLI."
```

A role-specific setting wins: `LAB_PRODUCT_MODEL`, `LAB_ARCHITECT_MODEL`,
`LAB_QA_MODEL`, `LAB_IMPLEMENTER_MODEL`, or `LAB_REVIEW_MODEL`. If neither setting
is set, the role uses `smart-auto`. Empty values count as unset. This also
applies to the standalone reading-list Implementer and Reviewer exercises.

The original scripted loan exercise is archived separately at
`../agentic-dev-team-lab-archive/scripted-loan`.

## Choose a workflow runtime

The native runtime remains the default and has no third-party dependencies.
`--workflow-runtime openai` runs all five roles with the OpenAI Agents SDK and
includes QA planning automatically. The host still enforces both approval
gates, artifact validation, and bounded retries. The workflow only previews
implementation, never writes or runs it. SDK trace export is disabled; model
requests go to your configured Smart AI Router.

Install the optional SDK in a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-openai.txt
```

Run the whole preview-only workflow with `smart-auto`:

```sh
DEFAULT_MODEL=smart-auto .venv/bin/python -B plan_release.py --workflow-runtime openai --example loan-submission
```

On the Mac with the router project's SDK already installed, you can use
`~/ProjectHome/smart-ai-router/.venv/bin/python` instead. Set
`SMART_ROUTER_URL` and `SMART_ROUTER_API_KEY` as usual. Unset any
`LAB_*_MODEL` role overrides if you want all five roles to use the same route.

To compare just the Reviewer backends, run the separate fixture command:

```sh
LAB_REVIEW_MODEL=smart-sonnet .venv/bin/python -B -m examples.reading_list.review_fixtures --runtime both
```

The runtime registry is in `runtimes/workflow.py`; all runtimes consume the
shared workflow definition in `workflows/release.py`. The SDK Reviewer can
only read the approved criteria and candidate in memory, not execute anything.

### CrewAI

The CrewAI runtime uses a `Flow` to sequence the same workflow definition and
one bounded, read-only Crew task for each role. The shared host gate still
requires explicit plan and design approvals. CrewAI uses the lab's existing
router transport, including the memory opt-out and request ID checks. It
doesn't execute the generated preview or export traces.

CrewAI and the pinned OpenAI Agents SDK require incompatible versions of the
`openai` package, so install CrewAI in a **separate** environment (Python 3.12
was used for testing):

```sh
python3.12 -m venv .venv-crewai
.venv-crewai/bin/python -m pip install -r requirements-crewai.txt
.venv-crewai/bin/python -B plan_release.py --workflow-runtime crewai --example reading-list
```

To compare Reviewer outcomes with identical fixtures and the same router
model pool, run each from its own environment:

```sh
LAB_REVIEW_MODEL=smart-sonnet .venv-crewai/bin/python -B -m examples.reading_list.review_fixtures --runtime crewai
LAB_REVIEW_MODEL=smart-sonnet .venv/bin/python -B -m examples.reading_list.review_fixtures --runtime openai
```

Add `--loan-regression` to either command to test the draft that was incorrectly
treated as an already-submitted application.
