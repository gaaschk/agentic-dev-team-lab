"""One bounded retry for a recoverable agent failure, with a run-local preference."""

import sys


class RetryableAgentError(SystemExit):
    """A transport or response failure, not a blocker or an approval decision."""


class TransientTimeout(RetryableAgentError):
    """A model/network timeout: the only failure the operations agent may
    retry automatically, and only for a stage explicitly marked safe to repeat."""


class AgentRetry:
    def __init__(self, *, prompt=None, emit=None, auto_retries=0):
        self.retry_all = False
        self.prompt = prompt
        self.emit = emit or (lambda text: print(text, file=sys.stderr))
        self.auto_retries_left = auto_retries

    def run(self, stage, attempt, *, stopped=None, auto_retryable=False):
        """Run a safe operation; the operations agent may silently retry once,
        for the whole run, a timeout on a stage marked safe to repeat. Anything
        else recoverable (malformed output, HTTP errors) only asks a human."""
        try:
            return attempt(None)
        except RetryableAgentError as failure:
            self.emit(str(failure))
            if auto_retryable and self.auto_retries_left > 0 and isinstance(failure, TransientTimeout):
                self.auto_retries_left -= 1
                self.emit(f"[operations] {stage} timed out. Auto-retrying (run's one automatic retry).")
                try:
                    return attempt(failure)
                except RetryableAgentError as retry_failure:
                    failure = retry_failure
                    self.emit(str(failure))
            if not self.retry_all:
                while True:
                    try:
                        choice = (self.prompt or input)(
                            f"{stage} failed. [r] Retry once, [a] Retry all for this run, "
                            "[s] Stop (default): "
                        ).strip().lower()
                    except EOFError:
                        choice = ""
                    if choice in {"r", "y", "a", "s", "n", ""}:
                        break
                if choice in {"s", "n", ""}:
                    raise SystemExit(stopped or f"Stopped. {stage} not retried.") from None
                self.retry_all = choice == "a"
            self.emit(f"Retrying {stage} (one retry).")
            return attempt(failure)
