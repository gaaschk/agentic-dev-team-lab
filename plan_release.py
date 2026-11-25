import argparse
import http.client
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from team_agents.agent_retry import AgentRetry, RetryableAgentError, TransientTimeout
from team_agents.model_config import resolve_model
from team_agents.role_agents import run as native_artifacts
from examples import EXAMPLES, load_brief
from runtimes import select_reviewer
from runtimes.workflow import WORKFLOW_RUNTIMES
from workflows.release import release_workflow


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def ask_role(role, goal, *, response_format=None, model=None, stage="Model"):
    request_id = str(uuid4())
    started = time.monotonic()

    def diagnostic(reason):
        return f"{stage}: {reason} after {time.monotonic() - started:.1f}s (request ID: {request_id})."
    base = os.environ["SMART_ROUTER_URL"].rstrip("/")
    url = base.removesuffix("/v1") + "/v1/chat/completions"
    body = {
        "model": model or resolve_model(),
        "agent": False,
        "stream": False,
        "messages": [
            {"role": "system", "content": role},
            {"role": "user", "content": goal},
        ],
    }
    if response_format is not None:
        body["response_format"] = response_format

    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["SMART_ROUTER_API_KEY"],
            "Content-Type": "application/json",
            "User-Agent": "agentic-dev-team-lab/0.1",
            "X-Smart-Router-Memory": "off",
            "X-Request-ID": request_id,
        },
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=120) as response:
            if response.headers.get("X-Smart-Router-Memory") != "off":
                raise SystemExit(diagnostic("router did not confirm memory is off"))
            if response.headers.get("X-Request-ID") != request_id:
                raise SystemExit(diagnostic("router did not echo the request ID"))
            data = json.load(response)
            answer = data["choices"][0]["message"].get("content")
            print(diagnostic("response received"), file=sys.stderr)
            return answer
    except HTTPError as exc:
        error = RetryableAgentError if exc.code in {408, 429, 500, 502, 503, 504} else SystemExit
        raise error(diagnostic(f"router returned HTTP {exc.code}")) from None
    except TimeoutError:
        raise TransientTimeout(diagnostic("request timed out")) from None
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TransientTimeout(diagnostic("connection timed out")) from None
        raise RetryableAgentError(diagnostic(f"connection failed ({type(exc.reason).__name__})")) from None
    except (ConnectionError, http.client.HTTPException) as exc:
        raise RetryableAgentError(diagnostic(f"response interrupted ({type(exc).__name__})")) from None
    except (ValueError, KeyError, IndexError, AttributeError, TypeError):
        raise RetryableAgentError(diagnostic("router returned a malformed response")) from None


def main():
    parser = argparse.ArgumentParser(description="Plan, design, and review a product brief.")
    parser.add_argument("--workflow-runtime", choices=WORKFLOW_RUNTIMES, default="native",
                        help="runtime for the release workflow (default: native)")
    brief = parser.add_mutually_exclusive_group(required=True)
    brief.add_argument("prompt", nargs="?", help="custom product brief")
    brief.add_argument("--example", choices=EXAMPLES, help="use a bundled product brief")
    args = parser.parse_args()

    goal = load_brief(args.example) if args.example else args.prompt
    if not goal.strip():
        parser.error("prompt cannot be blank")

    engine = WORKFLOW_RUNTIMES[args.workflow_runtime]
    reviewer = select_reviewer(getattr(engine, "review_runtime", "native"), decide=ask_role)

    workflow = release_workflow(
        decide=getattr(engine, "decide", ask_role), review=reviewer,
        artifact_runner=getattr(engine, "artifact_runner", native_artifacts),
    )
    engine(workflow, {"goal": goal}, retry=AgentRetry(), prompt=input, emit=print)


if __name__ == "__main__":
    main()
