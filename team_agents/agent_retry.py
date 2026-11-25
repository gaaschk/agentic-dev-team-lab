"""One bounded retry for a recoverable agent failure, with a run-local preference."""

import sys


class RetryableAgentError(SystemExit):
    """A transport or response failure, not a blocker or an approval decision."""


class AgentRetry:
    def __init__(self):
        self.retry_all = False

    def run(self, stage, attempt, *, stopped=None):
        """Run a safe operation; pass the first failure to its one possible retry."""
        try:
            return attempt(None)
        except RetryableAgentError as failure:
            print(str(failure), file=sys.stderr)
            if not self.retry_all:
                while True:
                    try:
                        choice = input(
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
            print(f"Retrying {stage} (one retry).", file=sys.stderr)
            return attempt(failure)
