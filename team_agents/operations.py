"""Operations agent: investigates runtime problems, decides how to recover,
and keeps its own persistent memory of what worked.

Recovery works at the single-call level: every model call is read-only, so
repeating one never repeats other work. Its workspace and tools come from
the shared Workbench (team_agents/workbench.py)."""

import json
import os
import sys
import time
from urllib.request import Request, urlopen

from . import ROLES_DIR
from .agent_retry import RetryableAgentError
from .model_config import resolve_model
from .workbench import Workbench, home_for, step_format

# Run-wide hard stop: no retry path can reset these. The agent decides how
# many retries a failure is worth within them; they aren't a retry count.
RECOVERY_SECONDS = 1800  # failed-call time plus retry waits; LAB_OPERATIONS_RECOVERY_SECONDS
MAX_FAILURES = 100  # failed model calls plus failed stages (e.g. invalid output)
FALLBACK_ATTEMPTS = 3  # only when the operations model can't be consulted
SLOW_SECONDS = 60
HISTORY_LIMIT = 20
DEFAULT_TIMEOUT = 120

TOOL_FORMAT = step_format("operations_step", {
    "action": {"type": "string", "enum": ["retry", "switch_route", "give_up", "keep"]},
    "route": {"type": "string"},
    "wait_seconds": {"type": "integer"},
    "timeout_seconds": {"type": "integer"},
    "reason": {"type": "string"},
})


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


class Operations:
    def __init__(self, decide, *, advisor=None, routes=(), emit=None, sleep=time.sleep,
                 clock=time.monotonic, workspace=None, shell=None, autonomous=True):
        self.decide = decide
        self.advisor = advisor
        self.routes = list(routes)
        self.emit = emit or (lambda text: print(text, file=sys.stderr))
        self.sleep = sleep
        self.clock = clock
        self.bench = Workbench(workspace or home_for("operations"), shell=shell)
        self.workspace = self.bench.workspace
        self.autonomous = autonomous
        self.stage_retries = 0
        self.stage_failures = 0
        self.recovery_seconds = 0.0
        self.recovery_limit = float(os.getenv("LAB_OPERATIONS_RECOVERY_SECONDS") or RECOVERY_SECONDS)
        self.history = []
        self.reroute = {}  # failing route -> replacement, for the rest of the run

    def use_tool(self, step):
        return self.bench.use_tool(step)

    def think(self, situation):
        """Let the operations model investigate with its tools, then decide.
        None if it can't produce a usable decision."""
        if self.advisor is None:
            return None
        role = (ROLES_DIR / "operations.md").read_text(encoding="utf-8")
        failures = sum(1 for h in self.history if h["outcome"] != "ok")
        context = {**situation, "history": self.history[-HISTORY_LIMIT:],
                   "run_totals": {"model_calls": len(self.history), "failed_calls": failures,
                                  "failed_stages": self.stage_failures,
                                  "max_failures": MAX_FAILURES,
                                  "stage_retries": self.stage_retries,
                                  "recovery_seconds_used": round(self.recovery_seconds, 1),
                                  "recovery_seconds_limit": self.recovery_limit},
                   "available_routes": self.routes}

        def ask(system, task, **kwargs):
            return self.advisor(system, task, stage="Operations",
                                model=self.route_for(resolve_model("LAB_OPERATIONS_MODEL")), **kwargs)

        try:
            step, _ = self.bench.loop(ask, role, context, fmt=TOOL_FORMAT, emit=self.emit,
                                      label="operations")
        except (SystemExit, Exception) as exc:
            self.emit(f"[operations] Couldn't consult the operations model ({exc}); using the default policy.")
            return None
        if step is None:
            self.emit("[operations] Using the default policy.")
            return None
        return self._decision(step)

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
        attempt = 0
        call_started = self.clock()
        while True:
            attempt += 1
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
                if not self.autonomous:
                    raise  # someone is watching: the human retry gate decides
                self.recovery_seconds += seconds
                if self.exhausted():
                    self.emit(f"[operations] {stage}: run-wide recovery limit reached "
                              f"({self.limit_text()}); not retrying.")
                    raise
                decision = self.think({
                    "situation": "call_failed", "stage": stage, "route": route,
                    "error": str(failure), "seconds": seconds,
                    "timeout_used": timeout or DEFAULT_TIMEOUT,
                    "attempt": attempt,
                    "seconds_spent_on_this_call": round(self.clock() - call_started, 1),
                })
                if decision is None:
                    if attempt >= FALLBACK_ATTEMPTS:
                        self.emit(f"[operations] {stage}: giving up after {attempt} attempts "
                                  "(the operations model couldn't be consulted).")
                        raise
                    decision = {"action": "retry", "route": route, "wait": 2 ** attempt,
                                "timeout": timeout or DEFAULT_TIMEOUT, "reason": "default backoff"}
                if decision["action"] == "give_up":
                    self.emit(f"[operations] {stage}: giving up — {decision['reason']}")
                    raise
                if decision["action"] == "switch_route":
                    self.switch(route, decision["route"], decision["reason"])
                timeout = decision["timeout"]
                self.emit(f"[operations] {stage} failed on {route}: {decision['reason']} "
                          f"Retrying on {self.route_for(primary)} in {decision['wait']}s "
                          f"(timeout {timeout}s, attempt {attempt + 1}).")
                self.recovery_seconds += decision["wait"]
                if self.exhausted():
                    self.emit(f"[operations] {stage}: the {decision['wait']}s wait would reach the "
                              f"run-wide recovery limit ({self.limit_text()}); not retrying.")
                    raise
                self.sleep(decision["wait"])
                continue
            seconds = round(self.clock() - started, 1)
            self.history.append({"stage": stage, "route": route, "seconds": seconds, "outcome": "ok"})
            if attempt > 1:
                self.think({"situation": "recovered", "stage": stage, "route": route,
                            "seconds": seconds, "attempts": attempt,
                            "note": "Your recovery worked. Record what worked in your memory."})
            elif seconds >= SLOW_SECONDS:
                decision = self.think({"situation": "slow_success", "stage": stage, "route": route,
                                       "seconds": seconds})
                if decision and decision["action"] == "switch_route":
                    self.switch(route, decision["route"], decision["reason"])
            return answer

    def failures(self):
        failed_calls = sum(1 for h in self.history if h["outcome"] != "ok")
        return failed_calls + self.stage_failures

    def exhausted(self):
        return self.recovery_seconds >= self.recovery_limit or self.failures() >= MAX_FAILURES

    def limit_text(self):
        return (f"{self.recovery_seconds:.0f}s of {self.recovery_limit:.0f}s recovery time, "
                f"{self.failures()} of {MAX_FAILURES} failures")

    def stage_failed(self, recent_log):
        # A stage can fail with every model call succeeding (invalid output),
        # so stage failures count toward the run-wide limit too.
        self.stage_failures += 1
        """Decide the stage-level retry gate in an autonomous run: 'r' or 's'.
        The agent decides how many stage retries are worth it; without a usable
        decision, or at the run-wide recovery limit, the run stops cleanly."""
        if self.exhausted():
            self.emit(f"[operations] Stopping the run: run-wide recovery limit reached ({self.limit_text()}).")
            self.think({"situation": "gave_up", "recent_log": recent_log[-8:],
                        "note": "The run-wide recovery limit stopped this run. Record what was "
                                "tried and that it failed."})
            return "s"
        self.stage_retries += 1
        decision = self.think({"situation": "stage_failed", "recent_log": recent_log[-8:],
                               "stage_retries_this_run": self.stage_retries - 1})
        if decision is None:
            self.emit("[operations] Stopping the run: the operations model couldn't be consulted.")
            return "s"
        if decision["action"] == "give_up":
            self.emit(f"[operations] Stopping the run: {decision['reason']}")
            return "s"
        if decision["action"] == "switch_route":
            failing = next((h["route"] for h in reversed(self.history)), None)
            if failing:
                self.switch(failing, decision["route"], decision["reason"])
        self.emit(f"[operations] Retrying the stage: {decision['reason']}")
        return "r"
