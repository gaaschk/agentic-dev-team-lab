"""Minimal web UI for the release workflow. One run at a time, native runtime only.

Run: .venv/bin/python -m pip install -r requirements-web.txt
     .venv/bin/python webapp.py
"""

import sys
import threading

from flask import Flask, jsonify, render_template_string, request

from plan_release import ask_role
from team_agents.agent_retry import AgentRetry
from team_agents.role_agents import run as native_artifacts
from team_agents.reviewer_agent import ask_agent_reviewer
from workflows.release import release_workflow
from workflows.runner import run as run_workflow

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
        self.pending_prompt = None
        self.pending_id = 0
        self.answer_event = threading.Event()
        self.answer = ""
        self.done = False

    def emit(self, text):
        with self.lock:
            self.log.append(text)

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


def start_run(goal, *, autonomous=False):
    """Refuse to start a second run while one is still in progress."""
    with run_lock:
        current = run_state["io"]
        if current is not None and not current.done:
            return False
        io = WebIO()
        run_state["io"] = io

    def worker():
        _thread_io[threading.get_ident()] = io
        try:
            workflow = release_workflow(
                decide=ask_role,
                review=lambda *a: ask_agent_reviewer(*a, decide=ask_role),
                artifact_runner=native_artifacts,
            )
            retry = AgentRetry(prompt=io.prompt, emit=io.emit, auto_retries=2)
            approve = make_approval_prompt(io, autonomous)
            run_workflow(workflow, {"goal": goal}, retry=retry, prompt=approve, emit=io.emit)
        except SystemExit as exc:
            io.emit(str(exc))
        finally:
            del _thread_io[threading.get_ident()]
            io.done = True

    threading.Thread(target=worker, daemon=True).start()
    return True


PAGE = """
<!doctype html>
<title>Agentic Dev Team Lab</title>
<style>
  body { font-family: monospace; max-width: 860px; margin: 2rem auto; }
  pre { white-space: pre-wrap; background: #f4f4f4; padding: 1rem; }
  textarea { width: 100%; height: 4rem; }
  button { padding: 0.5rem 1rem; margin-right: 0.5rem; }
</style>
<h1>Agentic Dev Team Lab</h1>
<div id="start">
  <textarea id="goal" placeholder="Describe what you want built..."></textarea><br>
  <label><input type="checkbox" id="autonomous"> Run autonomously (auto-approve plan &amp; design gates)</label><br>
  <button onclick="startRun()">Start</button>
</div>
<pre id="log"></pre>
<div id="approval" style="display:none">
  <p id="approval-text"></p>
  <div id="approval-buttons"></div>
</div>
<script>
let pendingId = null;
let answering = false;

async function startRun() {
  const startButton = document.querySelector('#start button');
  startButton.disabled = true;
  const goal = document.getElementById('goal').value;
  const autonomous = document.getElementById('autonomous').checked;
  const res = await fetch('/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({goal, autonomous})});
  const body = await res.json();
  if (!body.ok) {
    startButton.disabled = false;
    alert('A run is already in progress.');
    return;
  }
  document.getElementById('start').style.display = 'none';
  poll();
}
async function answer(value) {
  if (answering || pendingId === null) return;
  answering = true;
  document.querySelectorAll('#approval-buttons button').forEach(b => b.disabled = true);
  await fetch('/answer', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({value, prompt_id: pendingId})});
  document.getElementById('approval').style.display = 'none';
  answering = false;
}
function renderButtons(text) {
  const isRetry = text.includes('[r] Retry');
  const buttons = isRetry
    ? [['r', 'Retry once'], ['a', 'Retry all'], ['s', 'Stop']]
    : [['y', 'Approve'], ['n', 'Reject']];
  const container = document.getElementById('approval-buttons');
  container.innerHTML = '';
  for (const [value, label] of buttons) {
    const b = document.createElement('button');
    b.textContent = label;
    b.onclick = () => answer(value);
    container.appendChild(b);
  }
}
async function poll() {
  const res = await fetch('/state');
  const state = await res.json();
  document.getElementById('log').textContent = state.log.join('\\n');
  if (state.pending_prompt) {
    pendingId = state.pending_id;
    document.getElementById('approval-text').textContent = state.pending_prompt;
    renderButtons(state.pending_prompt);
    document.getElementById('approval').style.display = 'block';
  } else {
    pendingId = null;
    document.getElementById('approval').style.display = 'none';
  }
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
    started = start_run(body["goal"], autonomous=bool(body.get("autonomous")))
    return jsonify(ok=started), (200 if started else 409)


@app.route("/state")
def state():
    io = run_state["io"]
    if io is None:
        return jsonify(log=[], pending_prompt=None, pending_id=None, done=True)
    with io.lock:
        return jsonify(log=list(io.log), pending_prompt=io.pending_prompt,
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
