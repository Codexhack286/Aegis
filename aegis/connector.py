"""HTTP contract for project-owned agents and evaluators."""

import json
import os
import re
from time import perf_counter
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def endpoint():
    url = os.environ.get("AEGIS_CONNECTOR_URL", "")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.fragment:
        raise ValueError("Set AEGIS_CONNECTOR_URL to the trusted project's HTTP run endpoint.")
    return url


def load_spec(path, *, require_isolation=False):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Connector spec must be a JSON object.")
    if require_isolation and data.get("trial_isolation") != "fresh_per_request":
        raise ValueError("Paired execution requires trial_isolation=fresh_per_request in the connector spec.")
    revision, cases = data.get("revision"), data.get("cases")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("Connector spec needs a revision identifying the agent and evaluator.")
    if (not isinstance(cases, list) or not 1 <= len(cases) <= 100
            or not all(isinstance(case, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", case) for case in cases)
            or len(set(cases)) != len(cases)):
        raise ValueError("Connector spec needs 1–100 unique fixture IDs using letters, numbers, dashes or underscores.")
    rules = data.get("rules")
    if require_isolation and rules is None:
        raise ValueError("Paired execution requires independent evaluator rules in the connector spec.")
    if rules is not None:
        if not isinstance(rules, dict) or set(rules) != set(cases):
            raise ValueError("Connector rules must define exactly one rule set for each fixture.")
        for case, rule in rules.items():
            validate_rules(case, rule)
    return revision, cases, rules


def validate_rules(case, rule):
    list_fields = ("required_tool_order", "forbidden_tools", "unchanged_state_keys")
    if (not isinstance(rule, dict) or set(rule) - {"expected_state", "tool_prerequisites", *list_fields}
            or not isinstance(rule.get("expected_state", {}), dict)):
        raise ValueError(f"Invalid rule fields for fixture {case}.")
    for field in list_fields:
        values = rule.get(field, [])
        if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError(f"{case}.{field} must be a list of nonempty strings.")
    prerequisites = rule.get("tool_prerequisites", {})
    if not isinstance(prerequisites, dict) or any(
        not tool.strip() or not isinstance(required, list) or not required
        or any(not isinstance(name, str) or not name.strip() or name == tool for name in required)
        for tool, required in prerequisites.items()
    ):
        raise ValueError(f"{case}.tool_prerequisites must map tool names to nonempty lists of other tool names.")
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
    for tool, prerequisites in rule.get("tool_prerequisites", {}).items():
        seen = set()
        passed = True
        for item in tools:
            if item["status"] == "ok":
                if item["tool"] == tool and not seen.issuperset(prerequisites):
                    passed = False
                    break
                seen.add(item["tool"])
        checks.append({"kind": "tool_prerequisites", "subject": tool, "passed": passed})
    attempted = {item["tool"] for item in tools}
    checks.extend({"kind": "forbidden_tool", "subject": tool, "passed": tool not in attempted}
                  for tool in rule.get("forbidden_tools", []))
    checks.extend({"kind": "unchanged_state", "subject": key, "passed": matches(after, key, before[key])}
                  for key in protected)
    return checks


def run(case, strategy, identity, revision, rule=None):
    payload = {"version": 1, "case": case, "strategy": strategy, "identity": identity, "revision": revision}
    request = Request(endpoint(), json.dumps(payload).encode(), {"Content-Type": "application/json"})
    start = perf_counter()
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
    repair_turns = result.get("repair_turns")
    if repair_turns is not None and (type(repair_turns) is not int or repair_turns not in (0, 1)):
        raise ValueError("Connector repair_turns must be 0, 1 or null.")
    if result.get("summary") is not None and not isinstance(result["summary"], str):
        raise ValueError("Connector summary must be a string or null.")
    terminal = result.get("terminal", "finished")
    if not isinstance(terminal, str) or not terminal.strip():
        raise ValueError("Connector terminal status must be a nonempty string.")
    timing = result.get("timing_ms")
    phases = ("model", "tools", "orchestration")
    if timing is not None and (
        not isinstance(timing, dict) or any(
            type(timing.get(phase)) not in (int, float)
            or not 0 <= timing[phase] < float("inf")
            for phase in phases
        )
    ):
        raise ValueError("Connector timing_ms must contain finite nonnegative model, tools and orchestration durations.")
    report_timing = {phase: timing[phase] for phase in phases} if timing is not None else {}
    report = {
        "case": case, "strategy": strategy, "terminal": terminal,
        "invalid_calls": sum(item["status"] == "invalid" for item in tools),
        "blocked_calls": sum(item["status"] == "blocked" for item in tools),
        "tokens": tokens, "final_text": result.get("summary"),
        "before": result.get("before"), "after": result.get("after"), "tools": tools,
    }
    if repair_turns is not None:
        report["repair_turns"] = repair_turns
    if rule is None:
        report.update(success=result["success"] and not result["forbidden_mutation"],
                      forbidden_mutation=result["forbidden_mutation"])
    else:
        evaluation_started = perf_counter()
        checks = evaluate_rules(rule, report["before"], report["after"], tools)
        report.update(checks=checks, success=terminal == "finished" and all(check["passed"] for check in checks),
                      forbidden_mutation=any(check["kind"] == "unchanged_state" and not check["passed"]
                                             for check in checks))
        report_timing["evaluation"] = round((perf_counter() - evaluation_started) * 1000, 3)
    report["latency_ms"] = round((perf_counter() - start) * 1000, 2)
    if report_timing:
        report["timing_ms"] = report_timing
    return report
