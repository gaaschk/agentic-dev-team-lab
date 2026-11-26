"""Operations agent: keeps individual model calls healthy during a run.

Works at the single-call level, not the stage level: every model call here
is read-only, so repeating one call never repeats other work. A stage-level
retry would re-run every call the stage already made."""

import os
import sys
import time

from .agent_retry import RetryableAgentError, TransientTimeout
from .model_config import resolve_model


def fallback_models():
    return [m.strip() for m in os.getenv("LAB_FALLBACK_MODELS", "").split(",") if m.strip()]


class Operations:
    def __init__(self, decide, *, emit=None, attempts=3, fallbacks=None, sleep=time.sleep):
        self.decide = decide
        self.emit = emit or (lambda text: print(text, file=sys.stderr))
        self.attempts = attempts
        self.fallbacks = fallback_models() if fallbacks is None else list(fallbacks)
        self.sleep = sleep
        self.demoted = set()  # routes that timed out this run; skipped while others remain

    def _routes(self, model):
        routes = list(dict.fromkeys([model, *self.fallbacks]))
        healthy = [r for r in routes if r not in self.demoted]
        return healthy or routes

    def __call__(self, role, task, *, model=None, stage="Model", **kwargs):
        model = model or resolve_model()
        for attempt in range(1, self.attempts + 1):
            route = self._routes(model)[0]
            try:
                return self.decide(role, task, model=route, stage=stage, **kwargs)
            except RetryableAgentError as failure:
                # Anything decide() raises as retryable is transport-level
                # (timeout, 429/5xx, dropped connection): safe to repeat.
                if isinstance(failure, TransientTimeout) and len(self._routes(model)) > 1:
                    self.demoted.add(route)
                if attempt == self.attempts:
                    self.emit(f"[operations] {stage}: giving up after {attempt} attempts ({failure})")
                    raise
                delay = 2 ** attempt
                self.emit(f"[operations] {failure} Retrying {stage} on {self._routes(model)[0]} in {delay}s "
                          f"(attempt {attempt + 1}/{self.attempts}).")
                self.sleep(delay)
