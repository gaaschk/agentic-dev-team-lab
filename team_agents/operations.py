"""Operations agent: reasons about runtime failures and decides how to recover.

Works at the single-call level, not the stage level: every model call here
is read-only, so repeating one call never repeats other work. The agent
chooses from a bounded set of actions; it can't run anything or touch files."""

import json
import os
import sys
import time
from urllib.request import Request, urlopen

from . import ROLES_DIR
from .agent_retry import RetryableAgentError
from .model_config import resolve_model

MAX_ATTEMPTS = 4
SLOW_SECONDS = 60
HISTORY_LIMIT = 20
DEFAULT_TIMEOUT = 120

DECISION_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "operations_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["retry", "switch_route", "give_up", "keep"]},
                "route": {"type": "string"},
                "wait_seconds": {"type": "integer"},
                "timeout_seconds": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["action", "route", "wait_seconds", "timeout_seconds", "reason"],
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


class Operations:
    def __init__(self, decide, *, advisor=None, routes=(), emit=None, sleep=time.sleep,
                 clock=time.monotonic):
        self.decide = decide
        self.advisor = advisor
        self.routes = list(routes)
        self.emit = emit or (lambda text: print(text, file=sys.stderr))
        self.sleep = sleep
        self.clock = clock
        self.history = []
        self.reroute = {}  # failing route -> replacement, for the rest of the run

    def route_for(self, model):
        seen = set()
        while model in self.reroute and model not in seen:
            seen.add(model)
            model = self.reroute[model]
        return model

    def think(self, situation):
        """Ask the operations model what to do; None if it can't answer usably."""
        if self.advisor is None:
            return None
        situation = {**situation, "history": self.history[-HISTORY_LIMIT:],
                     "available_routes": self.routes}
        try:
            raw = self.advisor((ROLES_DIR / "operations.md").read_text(encoding="utf-8"),
                               json.dumps(situation), response_format=DECISION_FORMAT,
                               model=resolve_model("LAB_OPERATIONS_MODEL"),
                               stage="Operations")
            decision = json.loads(raw)
            action = decision["action"]
        except (SystemExit, Exception) as exc:
            self.emit(f"[operations] Couldn't consult the operations model ({exc}); using the default policy.")
            return None
        if action == "switch_route" and decision["route"] not in self.routes:
            self.emit(f"[operations] Ignoring switch to unknown route {decision['route']!r}.")
            action = "retry"
        return {
            "action": action,
            "route": decision["route"],
            "wait": max(0, min(int(decision["wait_seconds"]), 120)),
            "timeout": max(15, min(int(decision["timeout_seconds"]), 600)),
            "reason": decision["reason"],
        }

    def switch(self, frm, to, reason):
        if to != frm:
            self.reroute[frm] = to
            self.emit(f"[operations] Switching {frm} -> {to} for the rest of this run: {reason}")

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
            if seconds >= SLOW_SECONDS:
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
