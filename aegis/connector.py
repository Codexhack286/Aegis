"""HTTP contract for project-owned agents and evaluators."""

import json
import os
import re
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def endpoint():
    url = os.environ.get("AEGIS_CONNECTOR_URL", "")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.fragment:
        raise ValueError("Set AEGIS_CONNECTOR_URL to the trusted project's HTTP run endpoint.")
    return url


def load_spec(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Connector spec must be a JSON object.")
    revision, cases = data.get("revision"), data.get("cases")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("Connector spec needs a revision identifying the agent and evaluator.")
    if (not isinstance(cases, list) or not 1 <= len(cases) <= 100
            or not all(isinstance(case, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", case) for case in cases)
            or len(set(cases)) != len(cases)):
        raise ValueError("Connector spec needs 1–100 unique fixture IDs using letters, numbers, dashes or underscores.")
    return revision, cases


def run(case, strategy, identity, revision):
    payload = {"version": 1, "case": case, "strategy": strategy, "identity": identity, "revision": revision}
    request = Request(endpoint(), json.dumps(payload).encode(), {"Content-Type": "application/json"})
    start = monotonic()
    with urlopen(request, timeout=60) as response:
        body = response.read(1_048_577)
    if len(body) > 1_048_576:
        raise ValueError("Connector response exceeds 1 MiB.")
    result = json.loads(body)
    if (not isinstance(result, dict) or result.get("revision") != revision
            or type(result.get("success")) is not bool or type(result.get("forbidden_mutation")) is not bool):
        raise ValueError("Connector must return the frozen revision, success and forbidden_mutation.")
    tools = result.get("tools", [])
    if not isinstance(tools, list) or not all(
        isinstance(item, dict) and isinstance(item.get("tool"), str)
        and item.get("status") in ("ok", "invalid", "blocked") for item in tools
    ):
        raise ValueError("Connector tools must contain a tool name and an ok, invalid or blocked status.")
    tokens = result.get("tokens")
    if tokens is not None and (type(tokens) is not int or tokens < 0):
        raise ValueError("Connector tokens must be a nonnegative integer or null.")
    if result.get("summary") is not None and not isinstance(result["summary"], str):
        raise ValueError("Connector summary must be a string or null.")
    return {
        "case": case, "strategy": strategy, "terminal": "finished",
        "success": result["success"] and not result["forbidden_mutation"],
        "forbidden_mutation": result["forbidden_mutation"],
        "invalid_calls": sum(item["status"] == "invalid" for item in tools),
        "blocked_calls": sum(item["status"] == "blocked" for item in tools),
        "latency_ms": round((monotonic() - start) * 1000, 2),
        "tokens": tokens, "final_text": result.get("summary"),
        "before": result.get("before"), "after": result.get("after"), "tools": tools,
    }
