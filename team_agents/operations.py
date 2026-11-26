"""Operations agent: investigates runtime problems, decides how to recover,
and keeps its own persistent memory of what worked.

Recovery works at the single-call level: every model call is read-only, so
repeating one never repeats other work. The agent has a persistent workspace
(.operations/ by default) where it may read and write files in whatever
format it chooses, and, only when LAB_OPERATIONS_SHELL=1, run shell commands."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from . import ROLES_DIR
from .agent_retry import RetryableAgentError
from .implementer_agent import is_safe_relative_path
from .model_config import resolve_model

MAX_ATTEMPTS = 4
MAX_TOOL_STEPS = 8
SLOW_SECONDS = 60
HISTORY_LIMIT = 20
DEFAULT_TIMEOUT = 120
COMMAND_TIMEOUT = 60
OUTPUT_LIMIT = 6000
DEFAULT_WORKSPACE = Path(__file__).resolve().parents[1] / ".operations"

TOOL_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "operations_step",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string",
                         "enum": ["list_files", "read_file", "write_file", "run_command", "decide"]},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "command": {"type": "string"},
                "action": {"type": "string", "enum": ["retry", "switch_route", "give_up", "keep"]},
                "route": {"type": "string"},
                "wait_seconds": {"type": "integer"},
                "timeout_seconds": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["tool", "path", "content", "command", "action", "route",
                         "wait_seconds", "timeout_seconds", "reason"],
            "additionalProperties": False,
        },
    },
}


def list_routes():
    """The router's offered routes, or [] if it can't be asked."""
    try:
        base = os.environ["SMART_ROUTER_URL"].rstrip("/").removesuffix("/v1")
        request = Request(base + "/v1/models",
                          headers={"Authorization": "Bearer " + os.environ["SMART_ROUTER_API_KEY"],
                                   "User-Agent": "agentic-dev-team-lab/0.1"})
        with urlopen(request, timeout=10) as response:
            return [m["id"] for m in json.load(response)["data"]]
    except Exception:
        return []


def _clip(text):
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + "\n…(truncated)"


class Operations:
    def __init__(self, decide, *, advisor=None, routes=(), emit=None, sleep=time.sleep,
                 clock=time.monotonic, workspace=None, shell=None):
        self.decide = decide
        self.advisor = advisor
        self.routes = list(routes)
        self.emit = emit or (lambda text: print(text, file=sys.stderr))
        self.sleep = sleep
        self.clock = clock
        self.workspace = Path(workspace or os.getenv("LAB_OPERATIONS_HOME") or DEFAULT_WORKSPACE)
        self.shell = os.getenv("LAB_OPERATIONS_SHELL") == "1" if shell is None else shell
        self.history = []
        self.reroute = {}  # failing route -> replacement, for the rest of the run

    # --- tools -------------------------------------------------------------

    def _workspace_path(self, path):
        if not is_safe_relative_path(path):
            raise ValueError(f"path must be relative and inside the workspace: {path!r}")
        base = self.workspace.resolve()
        target = (base / path).resolve()
        if base not in target.parents:
            raise ValueError(f"path escapes the workspace: {path!r}")
        return target

    def use_tool(self, step):
        tool = step["tool"]
        try:
            if tool == "list_files":
                files = sorted(str(p.relative_to(self.workspace)) for p in self.workspace.rglob("*")
                               if p.is_file())
                return "\n".join(files[:200]) or "(workspace is empty)"
            if tool == "read_file":
                return _clip(self._workspace_path(step["path"]).read_text(encoding="utf-8"))
            if tool == "write_file":
                target = self._workspace_path(step["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(step["content"], encoding="utf-8")
                return f"wrote {len(step['content'])} characters to {step['path']}"
            if tool == "run_command":
                if not self.shell:
                    return "shell commands are disabled (the operator can set LAB_OPERATIONS_SHELL=1)"
                done = subprocess.run(step["command"], shell=True, cwd=self.workspace,
                                      capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
                return _clip(f"exit {done.returncode}\n{done.stdout}{done.stderr}")
        except subprocess.TimeoutExpired:
            return f"command timed out after {COMMAND_TIMEOUT}s"
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            return f"error: {exc}"
        return f"unknown tool {tool!r}"

    # --- reasoning ---------------------------------------------------------

    def think(self, situation):
        """Let the operations model investigate with its tools, then decide.
        None if it can't produce a usable decision."""
        if self.advisor is None:
            return None
        self.workspace.mkdir(parents=True, exist_ok=True)
        role = (ROLES_DIR / "operations.md").read_text(encoding="utf-8")
        context = {**situation, "history": self.history[-HISTORY_LIMIT:],
                   "available_routes": self.routes, "shell_enabled": self.shell,
                   "workspace": str(self.workspace)}
        observations = []
        for _ in range(MAX_TOOL_STEPS):
            try:
                raw = self.advisor(role, json.dumps({**context, "observations": observations}),
                                   response_format=TOOL_FORMAT,
                                   model=self.route_for(resolve_model("LAB_OPERATIONS_MODEL")),
                                   stage="Operations")
                step = json.loads(raw)
                tool = step["tool"]
            except (SystemExit, Exception) as exc:
                self.emit(f"[operations] Couldn't consult the operations model ({exc}); using the default policy.")
                return None
            if tool == "decide":
                return self._decision(step)
            label = step["command"] if tool == "run_command" else step["path"]
            result = self.use_tool(step)
            self.emit(f"[operations] {tool}({label}) -> {result.splitlines()[0] if result else ''}")
            observations.append({"tool": tool, "input": label, "result": result})
        self.emit(f"[operations] No decision after {MAX_TOOL_STEPS} tool steps; using the default policy.")
        return None

    def _decision(self, step):
        action = step["action"]
        if action == "switch_route" and step["route"] not in self.routes:
            self.emit(f"[operations] Ignoring switch to unknown route {step['route']!r}.")
            action = "retry"
        return {
            "action": action,
            "route": step["route"],
            "wait": max(0, min(int(step["wait_seconds"]), 120)),
            "timeout": max(15, min(int(step["timeout_seconds"]), 600)),
            "reason": step["reason"],
        }

    def switch(self, frm, to, reason):
        if to != frm:
            self.reroute[frm] = to
            self.emit(f"[operations] Switching {frm} -> {to} for the rest of this run: {reason}")

    def route_for(self, model):
        seen = set()
        while model in self.reroute and model not in seen:
            seen.add(model)
            model = self.reroute[model]
        return model

    # --- the wrapped model call --------------------------------------------

    def __call__(self, role, task, *, model=None, stage="Model", **kwargs):
        primary = model or resolve_model()
        timeout = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            route = self.route_for(primary)
            started = self.clock()
            try:
                extra = {"timeout": timeout} if timeout else {}
                answer = self.decide(role, task, model=route, stage=stage, **kwargs, **extra)
            except RetryableAgentError as failure:
                # decide() raises retryable errors only for transport problems
                # (timeouts, 429/5xx, dropped connections): safe to repeat.
                seconds = round(self.clock() - started, 1)
                self.history.append({"stage": stage, "route": route, "seconds": seconds,
                                     "outcome": str(failure)})
                if attempt == MAX_ATTEMPTS:
                    self.emit(f"[operations] {stage}: giving up after {attempt} attempts.")
                    self.think({"situation": "gave_up", "stage": stage,
                                "note": "Record what was tried and that it failed."})
                    raise
                decision = self.think({
                    "situation": "call_failed", "stage": stage, "route": route,
                    "error": str(failure), "seconds": seconds,
                    "timeout_used": timeout or DEFAULT_TIMEOUT,
                    "attempt": attempt, "max_attempts": MAX_ATTEMPTS,
                }) or {"action": "retry", "route": route, "wait": 2 ** attempt,
                       "timeout": timeout or DEFAULT_TIMEOUT, "reason": "default backoff"}
                if decision["action"] == "give_up":
                    self.emit(f"[operations] {stage}: giving up — {decision['reason']}")
                    raise
                if decision["action"] == "switch_route":
                    self.switch(route, decision["route"], decision["reason"])
                timeout = decision["timeout"]
                self.emit(f"[operations] {stage} failed on {route}: {decision['reason']} "
                          f"Retrying on {self.route_for(primary)} in {decision['wait']}s "
                          f"(timeout {timeout}s, attempt {attempt + 1}/{MAX_ATTEMPTS}).")
                self.sleep(decision["wait"])
                continue
            seconds = round(self.clock() - started, 1)
            self.history.append({"stage": stage, "route": route, "seconds": seconds, "outcome": "ok"})
            if attempt > 1:
                self.think({"situation": "recovered", "stage": stage, "route": route,
                            "seconds": seconds,
                            "note": "Your recovery worked. Record what worked in your memory."})
            elif seconds >= SLOW_SECONDS:
                decision = self.think({"situation": "slow_success", "stage": stage, "route": route,
                                       "seconds": seconds})
                if decision and decision["action"] == "switch_route":
                    self.switch(route, decision["route"], decision["reason"])
            return answer

    def stage_failed(self, recent_log):
        """Decide the stage-level retry gate in an autonomous run: 'r' or 's'."""
        decision = self.think({"situation": "stage_failed", "recent_log": recent_log[-8:]})
        if decision is None:
            return "r"
        if decision["action"] == "give_up":
            self.emit(f"[operations] Stopping the run: {decision['reason']}")
            return "s"
        if decision["action"] == "switch_route":
            failing = next((h["route"] for h in reversed(self.history)), None)
            if failing:
                self.switch(failing, decision["route"], decision["reason"])
        self.emit(f"[operations] Retrying the stage: {decision['reason']}")
        return "r"
