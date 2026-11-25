"""Minimal web UI for the release workflow. One run at a time, native runtime only.

Run: .venv/bin/python -m pip install -r requirements-web.txt
     .venv/bin/python webapp.py
"""

import threading

from flask import Flask, jsonify, render_template_string, request

from plan_release import ask_role
from team_agents.agent_retry import AgentRetry
from team_agents.role_agents import run as native_artifacts
from team_agents.reviewer_agent import ask_agent_reviewer
from workflows.release import release_workflow
from workflows.runner import run as run_workflow

app = Flask(__name__)


class WebIO:
    """Stands in for input()/print() so the runner can pause on a browser click."""

    def __init__(self):
        self.lock = threading.Lock()
        self.log = []
        self.pending_prompt = None
        self.answer_event = threading.Event()
        self.answer = ""
        self.done = False

    def emit(self, text):
        with self.lock:
            self.log.append(text)

    def prompt(self, text):
        with self.lock:
            self.pending_prompt = text
        self.answer_event.wait()
        self.answer_event.clear()
        with self.lock:
            self.pending_prompt = None
        return self.answer

    def answer_prompt(self, value):
        self.answer = value
        self.answer_event.set()


run_state = {"io": None, "thread": None}


def start_run(goal):
    io = WebIO()
    run_state["io"] = io

    def worker():
        try:
            workflow = release_workflow(
                decide=ask_role,
                review=lambda *a: ask_agent_reviewer(*a, decide=ask_role),
                artifact_runner=native_artifacts,
            )
            run_workflow(workflow, {"goal": goal}, retry=AgentRetry(), prompt=io.prompt, emit=io.emit)
        except SystemExit as exc:
            io.emit(str(exc))
        finally:
            io.done = True

    thread = threading.Thread(target=worker, daemon=True)
    run_state["thread"] = thread
    thread.start()


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
  <button onclick="startRun()">Start</button>
</div>
<pre id="log"></pre>
<div id="approval" style="display:none">
  <p id="approval-text"></p>
  <button onclick="answer('y')">Approve</button>
  <button onclick="answer('n')">Reject</button>
</div>
<script>
async function startRun() {
  const goal = document.getElementById('goal').value;
  await fetch('/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({goal})});
  document.getElementById('start').style.display = 'none';
  poll();
}
async function answer(value) {
  await fetch('/answer', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({value})});
  document.getElementById('approval').style.display = 'none';
}
async function poll() {
  const res = await fetch('/state');
  const state = await res.json();
  document.getElementById('log').textContent = state.log.join('\\n');
  if (state.pending_prompt) {
    document.getElementById('approval-text').textContent = state.pending_prompt;
    document.getElementById('approval').style.display = 'block';
  } else {
    document.getElementById('approval').style.display = 'none';
  }
  if (!state.done) setTimeout(poll, 1000);
}
</script>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/start", methods=["POST"])
def start():
    goal = request.get_json()["goal"]
    start_run(goal)
    return jsonify(ok=True)


@app.route("/state")
def state():
    io = run_state["io"]
    if io is None:
        return jsonify(log=[], pending_prompt=None, done=True)
    with io.lock:
        return jsonify(log=list(io.log), pending_prompt=io.pending_prompt, done=io.done)


@app.route("/answer", methods=["POST"])
def answer():
    value = request.get_json()["value"]
    run_state["io"].answer_prompt(value)
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
