"""One bounded retry for a recoverable agent failure, with a run-local preference."""

import sys


class RetryableAgentError(SystemExit):
    """A transport or response failure, not a blocker or an approval decision."""


class AgentRetry:
    def __init__(self, *, prompt=None, emit=None, auto_retries=0):
        self.retry_all = False
        self.prompt = prompt
        self.emit = emit or (lambda text: print(text, file=sys.stderr))
        self.auto_retries = auto_retries

    def run(self, stage, attempt, *, stopped=None):
        """Run a safe operation; an Operations agent absorbs transient failures
        (timeouts, transport errors) with silent retries before asking a human."""
        try:
            return attempt(None)
        except RetryableAgentError as failure:
            self.emit(str(failure))
            for n in range(self.auto_retries):
                self.emit(
                    f"[operations] {stage} looks transient. "
                    f"Auto-retrying ({n + 1}/{self.auto_retries})…"
                )
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
