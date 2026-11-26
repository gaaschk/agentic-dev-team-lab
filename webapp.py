"""Minimal web UI for the release workflow. One run at a time, native runtime only.

Run: .venv/bin/python -m pip install -r requirements-web.txt
     .venv/bin/python webapp.py
"""

import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template_string, request

from plan_release import ask_role
from team_agents.agent_retry import AgentRetry
from team_agents.implementer_agent import is_safe_relative_path
from team_agents.markdown import render_document, to_html
from team_agents.operations import Operations, list_routes
from team_agents.workbench import CONTEXT_LIMIT, Team, clip
from team_agents.role_agents import run as native_artifacts
from team_agents.reviewer_agent import ask_agent_reviewer
from workflows.managed import SECURITY_REVIEW, assemble, planning_workflow
from workflows.release import release_workflow
from workflows.runner import Step, run as run_workflow

app = Flask(__name__)

# The roles report what they're doing (which agent, which tool call) via
# print()/stderr. Tee the current worker thread's output into its WebIO log
# too, so the page shows it, without touching every role module.
_real_stdout, _real_stderr = sys.stdout, sys.stderr
_thread_io = {}


class _TeeStream:
    def __init__(self, real):
        self.real = real

    def write(self, text):
        self.real.write(text)
        io = _thread_io.get(threading.get_ident())
        if io and text.strip():
            io.emit(text.rstrip())

    def flush(self):
        self.real.flush()


sys.stdout = _TeeStream(_real_stdout)
sys.stderr = _TeeStream(_real_stderr)


class WebIO:
    """Stands in for input()/print() so the runner can pause on a browser click."""

    def __init__(self):
        self.lock = threading.Lock()
        self.log = []
        self.stages = []
        self.pending_prompt = None
        self.pending_id = 0
        self.answer_event = threading.Event()
        self.answer = ""
        self.done = False

    def emit(self, text):
        with self.lock:
            self.log.append(text)

    def seed_stages(self, names):
        with self.lock:
            self.stages = [{"name": name, "status": "pending", "output": None} for name in names]

    def add_stages(self, names):
        with self.lock:
            self.stages += [{"name": name, "status": "pending", "output": None} for name in names]

    def start_stage(self, name):
        with self.lock:
            for stage in self.stages:
                if stage["name"] == name and stage["status"] == "pending":
                    stage["status"] = "running"
                    return
            self.stages.append({"name": name, "status": "running", "output": None})

    def finish_stage(self, name, output, ok=True, result=None):
        with self.lock:
            for stage in reversed(self.stages):
                if stage["name"] == name and stage["status"] == "running":
                    stage["status"] = "done" if ok else "failed"
                    stage["output"] = output
                    # What the page shows; a failed stage has only its message.
                    stage["html"] = to_html(result if ok and result is not None else output)
                    return

    def prompt(self, text):
        with self.lock:
            self.pending_id += 1
            self.pending_prompt = text
            self.answer_event.clear()
        self.answer_event.wait()
        with self.lock:
            self.pending_prompt = None
        return self.answer

    def answer_prompt(self, prompt_id, value):
        """Ignore an answer that doesn't match the currently open gate (stale click or poll)."""
        with self.lock:
            if self.pending_prompt is None or prompt_id != self.pending_id:
                return False
            self.answer = value
        self.answer_event.set()
        return True


run_state = {"io": None}
run_lock = threading.Lock()


def make_approval_prompt(io, autonomous):
    """Auto-approve the plan/design gates when autonomous mode is on; retries still ask."""

    def prompt(text):
        if autonomous and text.startswith("Approve"):
            io.emit(f"[autonomous] {text.strip()} y")
            return "y"
        return io.prompt(text)

    return prompt


def make_retry_prompt(io, autonomous, ops):
    """In an autonomous run the operations agent decides a stage failure
    (retry, reroute, or stop) instead of blocking on a human click."""

    def prompt(text):
        if autonomous:
            with io.lock:
                recent = io.log[-8:]
            return ops.stage_failed(recent)
        return io.prompt(text)

    return prompt


def build_runtime(io, autonomous, *, ask, routes=(), sleep=time.sleep):
    """Compose the retry layers for one run. Interactive: the PR #4 behavior
    (one scoped timeout retry, then the human decides), with no silent
    per-call retries. Autonomous: the operations agent is the only retry
    decider, at both the call and the stage level, so budgets don't stack."""
    ops = Operations(ask, emit=io.emit, routes=routes, sleep=sleep, autonomous=autonomous,
                     advisor=lambda role, task, **kw: ask(role, task, timeout=30, **kw))
    team = Team(ops, emit=io.emit)
    retry = AgentRetry(prompt=make_retry_prompt(io, autonomous, ops), emit=io.emit,
                       auto_retries=0 if autonomous else 1, ask_again=autonomous)
    return ops, team, retry


def operations_preflight():
    """The operations agent's environment check: catch misconfiguration
    before a run burns time on it, instead of a mid-run crash or timeout."""
    missing = [v for v in ("SMART_ROUTER_URL", "SMART_ROUTER_API_KEY") if not os.environ.get(v)]
    if missing:
        return f"[operations] Missing environment: {', '.join(missing)}. Set them and restart the server."

    url = os.environ["SMART_ROUTER_URL"]
    parsed = urlparse(url)
    try:
        host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        return f"[operations] SMART_ROUTER_URL has an invalid port ({exc})."
    if parsed.scheme not in ("http", "https") or not host:
        return f"[operations] SMART_ROUTER_URL ({url!r}) isn't a valid http(s) URL with a host."
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError as exc:
        return f"[operations] Can't reach the Smart AI Router at {host}:{port} ({exc})."
    return None


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "stage"


def write_stage_document(run_dir, name, result):
    """Save a stage's output as a human-readable .md, and the raw .json if it's structured."""
    run_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(name)
    (run_dir / f"{slug}.md").write_text(render_document(name, result), encoding="utf-8")
    if isinstance(result, (dict, list)):
        (run_dir / f"{slug}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def write_implementation_files(run_dir, result, emit, folder="implementation"):
    """Materialize the Implementer's proposed files under run_dir/<folder>/.
    Every path was already validated by parse_files(); re-check and resolve
    here too, so a write can never land outside the run's own directory."""
    run_dir = run_dir.resolve()
    base = (run_dir / folder).resolve()
    base.mkdir(parents=True, exist_ok=True)
    for file in result.get("files", []):
        if not is_safe_relative_path(file["path"]):
            emit(f"[operations] Skipped unsafe file path: {file['path']!r}")
            continue
        target = (base / file["path"]).resolve()
        if target != base and base not in target.parents:
            emit(f"[operations] Skipped file path escaping the output directory: {file['path']!r}")
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file["content"], encoding="utf-8")
        except OSError as exc:
            emit(f"[operations] Could not write {file['path']!r}: {exc}")
            continue
        emit(f"[operations] Wrote {target.relative_to(run_dir)}")


def make_output_writer(run_dir, io):
    """The runner's on_output. Proposed code goes to draft/, and reaches
    implementation/ only after every blocking review in the run passes."""
    seen = {"proposal": None, "reviewed": False}

    def on_output(name, result, rendered, ok):
        io.finish_stage(name, rendered, ok, result)
        if not run_dir:
            return
        try:
            if not ok or result is None:
                write_stage_document(run_dir, name, rendered)
                return
            write_stage_document(run_dir, name, result)
            if name == "Implementer":
                seen["proposal"] = result
                write_implementation_files(run_dir, result, io.emit, folder="draft")
            elif name == "Reviewer":
                seen["reviewed"] = result["verdict"] == "PASS"
                if not seen["reviewed"]:
                    io.emit("[operations] Review requires changes: the code stays in draft/.")
            with io.lock:
                security = any(stage["name"] == SECURITY_REVIEW for stage in io.stages)
            # A blocked security review arrives with ok=False, so never gets here.
            if name == (SECURITY_REVIEW if security else "Reviewer") and seen["reviewed"]:
                write_implementation_files(run_dir, seen["proposal"], io.emit)
        except OSError as exc:
            io.emit(f"[operations] Could not write output for {name}: {exc}")

    return on_output


def run_record(io):
    """What agents are told about a finished run when recording lessons."""
    with io.lock:
        return {
            "stages": [{"name": s["name"], "status": s["status"],
                        "output": clip(s["output"] or "", CONTEXT_LIMIT)} for s in io.stages],
            "recent_log": io.log[-15:],
        }


def start_run(goal, *, autonomous=False, output_dir=None):
    """Refuse to start a second run while one is still in progress, or if the
    operations agent's environment check finds something a run can't survive."""
    with run_lock:
        current = run_state["io"]
        if current is not None and not current.done:
            return False, "A run is already in progress."

        error = operations_preflight()
        if error:
            return False, error

        io = WebIO()
        run_state["io"] = io

    # A short random suffix, not just the second-resolution timestamp, keeps two
    # runs started in the same second from colliding into one output directory.
    run_dir = Path(output_dir) / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}" \
        if output_dir else None

    on_output = make_output_writer(run_dir, io)

    def worker():
        _thread_io[threading.get_ident()] = io
        if run_dir:
            io.emit(f"[operations] Writing this run's output to {run_dir}")
        team = None
        try:
            ops, team, retry = build_runtime(io, autonomous, ask=ask_role, routes=list_routes())
            workflow = release_workflow(
                decide=team,
                review=lambda *a: ask_agent_reviewer(*a, decide=team),
                artifact_runner=native_artifacts,
            )
            controls = dict(retry=retry, prompt=make_approval_prompt(io, autonomous), emit=io.emit,
                            on_start=io.start_stage, on_output=on_output)
            io.seed_stages(["Project Manager"])
            state = run_workflow(planning_workflow(decide=team), {"goal": goal}, **controls)
            nodes = assemble(state["approved_team"], workflow, decide=team,
                             artifact_runner=native_artifacts)
            io.add_stages([node.name for node in nodes if isinstance(node, Step)])
            run_workflow(nodes, state, **controls)
        except SystemExit as exc:
            io.emit(str(exc))
        finally:
            try:
                if team and team.notes and not team.memory_down:
                    io.emit("[team] Agents are recording lessons from this run…")
                    team.reflect(run_record(io))
            except Exception as exc:
                io.emit(f"[team] Couldn't record lessons ({exc}).")
            finally:
                del _thread_io[threading.get_ident()]
                io.done = True

    threading.Thread(target=worker, daemon=True).start()
    return True, None


PAGE = """
<!doctype html>
<title>Agentic Dev Team Lab</title>
<style>
  body { font-family: monospace; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }
  textarea, input[type=text] { width: 100%; font-family: inherit; }
  textarea { height: 4rem; }
  button { padding: 0.4rem 0.9rem; margin-right: 0.5rem; font-family: inherit; }
  label { display: block; margin: 0.4rem 0; }
  h1 { margin-bottom: 0.5rem; }

  #gatebar {
    display: none; position: sticky; top: 0; z-index: 10; background: #fff3cd;
    border: 1px solid #e0c36a; border-radius: 4px; padding: 0.75rem 1rem;
    margin-bottom: 1rem; align-items: center; justify-content: space-between; gap: 1rem;
  }
  #gatebar.stop { background: #f8d7da; border-color: #e0a1a8; }

  .layout { display: flex; gap: 1rem; align-items: flex-start; }
  #agent-list { list-style: none; margin: 0; padding: 0; width: 210px; flex: none; }
  #agent-list li { margin-bottom: 0.35rem; }
  .agent-row {
    width: 100%; text-align: left; border: 1px solid #ddd; border-radius: 4px;
    background: #fff; padding: 0.4rem 0.6rem; cursor: pointer; display: flex;
    justify-content: space-between; gap: 0.5rem;
  }
  .agent-row[disabled] { color: #999; cursor: default; }
  .agent-row.selected { border-color: #555; background: #f0f0f0; }
  .agent-row .dot { width: 1.2em; display: inline-block; }
  .agent-row.status-running .dot { color: #b58900; }
  .agent-row.status-done .dot { color: #2a2; }
  .agent-row.status-failed .dot { color: #c33; }
  .agent-row .status { color: #777; font-size: 0.85em; }

  .detail-panel { flex: 1; min-width: 0; border: 1px solid #ddd; border-radius: 4px; }
  .detail-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; background: #fafafa;
  }
  .detail-header label { display: inline-flex; align-items: center; gap: 0.3em; margin: 0; }
  #detail-output {
    padding: 0 1.25rem 1rem; max-height: 70vh; overflow-y: auto;
    font-family: system-ui, sans-serif; line-height: 1.5; overflow-wrap: anywhere;
  }
  #detail-output h2 { font-size: 1.15rem; margin: 1.2rem 0 0.4rem; }
  #detail-output h3 { font-size: 1rem; margin: 0.9rem 0 0.3rem; }
  #detail-output li { margin-bottom: 0.5rem; }
  #detail-output .text { white-space: pre-wrap; }
  #detail-output pre {
    background: #f4f4f4; padding: 0.75rem; overflow-x: auto; overflow-wrap: normal;
  }

  details#log-details { margin-top: 1rem; }
  summary { cursor: pointer; padding: 0.3rem 0; }
  #log { max-height: 40vh; overflow-y: auto; background: #f4f4f4; padding: 0.75rem; margin: 0.4rem 0 0; }
</style>
<h1>Agentic Dev Team Lab</h1>

<div id="gatebar" role="alert">
  <span id="gatebar-text"></span>
  <span id="gatebar-buttons"></span>
</div>

<div id="start">
  <textarea id="goal" placeholder="Describe what you want built..."></textarea>
  <label><input type="checkbox" id="autonomous"> Run autonomously (auto-approve plan &amp; design gates)</label>
  <label>Write outputs to (optional, e.g. ./runs):
    <input type="text" id="output_dir" placeholder="leave blank to not write files">
  </label>
  <button onclick="startRun()">Start</button>
</div>

<div class="layout">
  <ul id="agent-list"></ul>
  <div class="detail-panel">
    <div class="detail-header">
      <span id="detail-title">Select an agent</span>
      <span>
        <button id="copy-btn" style="display:none">Copy</button>
        <label><input type="checkbox" id="follow-checkbox" checked> follow run</label>
      </span>
    </div>
    <div id="detail-output" tabindex="0"></div>
  </div>
</div>

<details id="log-details">
  <summary id="log-summary">Activity log</summary>
  <pre id="log"></pre>
</details>

<script>
let stages = [];
let selected = null;
let following = true;
let pendingId = null;
let answering = false;
let lastLogLength = 0;
let lastRenderedKey = null;
let lastRenderedOutput = null;

async function startRun() {
  const startButton = document.querySelector('#start button');
  startButton.disabled = true;
  const goal = document.getElementById('goal').value;
  const autonomous = document.getElementById('autonomous').checked;
  const output_dir = document.getElementById('output_dir').value;
  const res = await fetch('/start', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({goal, autonomous, output_dir})});
  const body = await res.json();
  if (!body.ok) {
    startButton.disabled = false;
    alert(body.error || 'A run is already in progress.');
    return;
  }
  document.getElementById('start').style.display = 'none';
  poll();
}

async function answer(value) {
  if (answering || pendingId === null) return;
  answering = true;
  document.querySelectorAll('#gatebar-buttons button').forEach(b => b.disabled = true);
  await fetch('/answer', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({value, prompt_id: pendingId})});
  answering = false;
}

function renderGateButtons(text) {
  const isRetry = text.includes('[r] Retry');
  const buttons = isRetry
    ? [['r', 'Retry once'], ['a', 'Retry all'], ['s', 'Stop']]
    : [['y', 'Approve'], ['n', 'Reject']];
  const container = document.getElementById('gatebar-buttons');
  container.innerHTML = '';
  for (const [value, label] of buttons) {
    const b = document.createElement('button');
    b.textContent = label;
    b.onclick = () => answer(value);
    container.appendChild(b);
  }
  return buttons;
}

function renderGate(state) {
  const bar = document.getElementById('gatebar');
  if (state.pending_prompt) {
    if (pendingId !== state.pending_id) {
      pendingId = state.pending_id;
      document.getElementById('gatebar-text').textContent = state.pending_prompt;
      const buttons = renderGateButtons(state.pending_prompt);
      bar.classList.toggle('stop', buttons.some(([v]) => v === 's'));
      bar.style.display = 'flex';
      document.title = '(!) Approval needed';
      const firstButton = document.querySelector('#gatebar-buttons button');
      if (firstButton) firstButton.focus();
    }
  } else {
    pendingId = null;
    bar.style.display = 'none';
    document.title = 'Agentic Dev Team Lab';
  }
}

function statusGlyph(status) {
  return {pending: '\\u25cb', running: '\\u25cf', done: '\\u2713', failed: '\\u2717'}[status] || '\\u25cb';
}

function ensureRows(names) {
  const list = document.getElementById('agent-list');
  if (list.children.length === names.length) return;
  list.innerHTML = '';
  for (const name of names) {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.className = 'agent-row';
    btn.dataset.name = name;
    btn.innerHTML = '<span><span class="dot"></span><span class="name"></span></span><span class="status"></span>';
    btn.onclick = () => selectStage(name, true);
    li.appendChild(btn);
    list.appendChild(li);
  }
  list.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const rows = Array.from(document.querySelectorAll('.agent-row'));
    const idx = rows.indexOf(document.activeElement);
    const next = e.key === 'ArrowDown' ? Math.min(idx + 1, rows.length - 1) : Math.max(idx - 1, 0);
    rows[next].focus();
  });
}

function renderList() {
  for (const stage of stages) {
    const row = document.querySelector(`.agent-row[data-name="${stage.name}"]`);
    if (!row) continue;
    row.querySelector('.name').textContent = stage.name;
    row.querySelector('.dot').textContent = statusGlyph(stage.status);
    row.querySelector('.status').textContent = stage.status;
    row.className = 'agent-row status-' + stage.status + (stage.name === selected ? ' selected' : '');
    row.disabled = stage.status === 'pending';
    row.setAttribute('aria-current', stage.name === selected ? 'true' : 'false');
  }
}

function selectStage(name, manual) {
  selected = name;
  if (manual) {
    following = false;
    document.getElementById('follow-checkbox').checked = false;
  }
  renderList();
  renderDetail();
}

function renderDetail() {
  const stage = stages.find(s => s.name === selected);
  document.getElementById('detail-title').textContent =
    selected ? `${selected} \\u00b7 ${stage ? stage.status : ''}` : 'Select an agent';
  const hasOutput = stage && stage.output !== null && stage.output !== undefined;
  document.getElementById('copy-btn').style.display = hasOutput ? 'inline-block' : 'none';
  const raw = hasOutput ? stage.output : '(no output yet)';
  if (lastRenderedKey === selected && lastRenderedOutput === raw) return;
  lastRenderedKey = selected;
  lastRenderedOutput = raw;
  const panel = document.getElementById('detail-output');
  // stage.html is built and escaped by the server (team_agents/markdown.py).
  if (hasOutput) panel.innerHTML = stage.html; else panel.textContent = raw;
}

function autoFollow() {
  if (!following) return;
  const runningStage = stages.find(s => s.status === 'running');
  const target = runningStage
    ? runningStage.name
    : [...stages].reverse().find(s => s.status !== 'pending')?.name;
  if (target && target !== selected) {
    selected = target;
    renderList();
    renderDetail();
  }
}

function appendLog(newLines) {
  if (!newLines.length) return;
  const pre = document.getElementById('log');
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  pre.textContent += (pre.textContent ? '\\n' : '') + newLines.join('\\n');
  document.getElementById('log-summary').textContent = 'Activity log \\u2014 ' + newLines[newLines.length - 1];
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}

document.getElementById('copy-btn').onclick = () => {
  navigator.clipboard.writeText(document.getElementById('detail-output').innerText);
};
document.getElementById('follow-checkbox').onchange = (e) => {
  following = e.target.checked;
  if (following) autoFollow();
};

async function poll() {
  const res = await fetch('/state');
  const state = await res.json();
  stages = state.stages;
  ensureRows(stages.map(s => s.name));
  renderList();
  autoFollow();
  renderDetail();
  if (state.log.length > lastLogLength) {
    appendLog(state.log.slice(lastLogLength));
    lastLogLength = state.log.length;
  }
  renderGate(state);
  if (!state.done) setTimeout(poll, 1000);
}

async function attachIfRunning() {
  const res = await fetch('/state');
  const state = await res.json();
  if (!state.done) {
    document.getElementById('start').style.display = 'none';
    poll();
  }
}
attachIfRunning();
</script>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/start", methods=["POST"])
def start():
    body = request.get_json()
    output_dir = body.get("output_dir")
    if output_dir is not None and not isinstance(output_dir, str):
        return jsonify(ok=False, error="output_dir must be a string"), 400
    output_dir = (output_dir or "").strip() or None
    started, error = start_run(body["goal"], autonomous=bool(body.get("autonomous")),
                                output_dir=output_dir)
    return jsonify(ok=started, error=error), (200 if started else 409)


@app.route("/state")
def state():
    io = run_state["io"]
    if io is None:
        return jsonify(log=[], stages=[], pending_prompt=None, pending_id=None, done=True)
    with io.lock:
        return jsonify(log=list(io.log), stages=list(io.stages), pending_prompt=io.pending_prompt,
                        pending_id=io.pending_id, done=io.done)


@app.route("/answer", methods=["POST"])
def answer():
    body = request.get_json()
    io = run_state["io"]
    if io is None:
        return jsonify(ok=False), 409
    accepted = io.answer_prompt(body["prompt_id"], body["value"])
    return jsonify(ok=accepted), (200 if accepted else 409)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
