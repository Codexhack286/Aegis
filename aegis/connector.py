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
    rules = data.get("rules")
    if rules is not None:
        if not isinstance(rules, dict) or set(rules) != set(cases):
            raise ValueError("Connector rules must define exactly one rule set for each fixture.")
        for case, rule in rules.items():
            validate_rules(case, rule)
    return revision, cases, rules


def validate_rules(case, rule):
    list_fields = ("required_tool_order", "forbidden_tools", "unchanged_state_keys")
    if (not isinstance(rule, dict) or set(rule) - {"expected_state", *list_fields}
            or not isinstance(rule.get("expected_state", {}), dict)):
        raise ValueError(f"Invalid rule fields for fixture {case}.")
    for field in list_fields:
        values = rule.get(field, [])
        if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError(f"{case}.{field} must be a list of nonempty strings.")
    if not rule.get("expected_state") and not rule.get("required_tool_order"):
        raise ValueError(f"{case} needs a nonempty expected_state or required_tool_order.")


def evaluate_rules(rule, before, after, tools):
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("Rule evaluation requires before and after state objects.")
    protected = rule.get("unchanged_state_keys", [])
    if any(key not in before for key in protected):
        raise ValueError("Connector before state is missing a protected key.")

    def matches(state, key, value):
        return key in state and json.dumps(state[key], sort_keys=True) == json.dumps(value, sort_keys=True)

    checks = [
        {"kind": "expected_state", "subject": key, "passed": matches(after, key, value)}
        for key, value in rule.get("expected_state", {}).items()
    ]
    required = rule.get("required_tool_order", [])
    if required:
        completed = iter(item["tool"] for item in tools if item["status"] == "ok")
        checks.append({
            "kind": "required_tool_order", "subject": " → ".join(required),
            "passed": all(tool in completed for tool in required),
        })
    attempted = {item["tool"] for item in tools}
    checks.extend({"kind": "forbidden_tool", "subject": tool, "passed": tool not in attempted}
                  for tool in rule.get("forbidden_tools", []))
    checks.extend({"kind": "unchanged_state", "subject": key, "passed": matches(after, key, before[key])}
                  for key in protected)
    return checks


def run(case, strategy, identity, revision, rule=None):
    payload = {"version": 1, "case": case, "strategy": strategy, "identity": identity, "revision": revision}
    request = Request(endpoint(), json.dumps(payload).encode(), {"Content-Type": "application/json"})
    start = monotonic()
    with urlopen(request, timeout=60) as response:
        body = response.read(1_048_577)
    if len(body) > 1_048_576:
        raise ValueError("Connector response exceeds 1 MiB.")
    result = json.loads(body)
    if not isinstance(result, dict) or result.get("revision") != revision:
        raise ValueError("Connector must return the frozen revision.")
    if rule is None and (type(result.get("success")) is not bool or type(result.get("forbidden_mutation")) is not bool):
        raise ValueError("Project-owned evaluation requires boolean success and forbidden_mutation.")
    tools = result.get("tools", [] if rule is None else None)
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
    terminal = result.get("terminal", "finished")
    if not isinstance(terminal, str) or not terminal.strip():
        raise ValueError("Connector terminal status must be a nonempty string.")
    report = {
        "case": case, "strategy": strategy, "terminal": terminal,
        "invalid_calls": sum(item["status"] == "invalid" for item in tools),
        "blocked_calls": sum(item["status"] == "blocked" for item in tools),
        "latency_ms": round((monotonic() - start) * 1000, 2),
        "tokens": tokens, "final_text": result.get("summary"),
        "before": result.get("before"), "after": result.get("after"), "tools": tools,
    }
    if rule is None:
        report.update(success=result["success"] and not result["forbidden_mutation"],
                      forbidden_mutation=result["forbidden_mutation"])
    else:
        checks = evaluate_rules(rule, report["before"], report["after"], tools)
        report.update(checks=checks, success=terminal == "finished" and all(check["passed"] for check in checks),
                      forbidden_mutation=any(check["kind"] == "unchanged_state" and not check["passed"]
                                             for check in checks))
    return report
