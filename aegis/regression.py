"""Portable projected evidence for the built-in helpdesk evaluator."""

import json
import sqlite3
from statistics import median
from contextlib import closing
from functools import partial
from pathlib import Path
from uuid import uuid4

from aegis import connector
from aegis.core import CASES, QUEUES, REPAIR_STRATEGY, STRATEGIES, TOOL_PARAMETERS, run_case, ticket_checks
from aegis.runner import check_lmstudio_model, lmstudio, scripted

POLICY = "helpdesk-v1"
MAX_BUNDLE_BYTES = 16_384


def export_bundle(report):
    if not isinstance(report, dict) or report.get("provider") == "connector":
        raise ValueError("A completed built-in helpdesk run is required.")
    try:
        case, strategy = report["case"], report["strategy"]
        before, after = report["before"], report["after"]
        calls = report["tools"]
        terminal = report["terminal"]
        notes = after["T-1"]["notes"]
        if (
            not isinstance(terminal, str) or not terminal or not isinstance(notes, list)
            or not all(isinstance(note, str) for note in notes)
            or not isinstance(before["T-2"], dict) or not isinstance(after["T-2"], dict)
            or not isinstance(calls, list) or not all(
                isinstance(call["tool"], str) and call["status"] in ("ok", "invalid", "blocked")
                and (call["status"] != "ok" or call["tool"] in TOOL_PARAMETERS) for call in calls
            )
        ):
            raise ValueError
        bundle = {
            "version": 1, "policy": POLICY, "case": case, "strategy": strategy,
            "evidence": {
                "finished": terminal == "finished",
                "initial_queue": before["T-1"]["queue"],
                "final_queue": after["T-1"]["queue"],
                "has_note": bool(notes),
                "unrelated_unchanged": before["T-2"] == after["T-2"],
                "successful_tools": [call["tool"] for call in calls if call["status"] == "ok"],
            },
        }
        regrade(bundle)
        return bundle
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Insufficient evidence for a helpdesk case bundle.") from error


def from_ledger(path, identity):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as db:
            row = db.execute("SELECT report FROM runs WHERE identity = ?", (identity,)).fetchone()
    except sqlite3.OperationalError as error:
        raise ValueError("Could not read the run ledger.") from error
    if row is None:
        raise ValueError("Run not found.")
    return export_bundle(json.loads(row[0]) if row[0] else None)


def load(path):
    with path.open("rb") as source:
        data = source.read(MAX_BUNDLE_BYTES + 1)
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("Case bundle exceeds 16 KiB.")
    return json.loads(data)


def regrade(bundle):
    try:
        if type(bundle["version"]) is not int or bundle["version"] != 1 or bundle["policy"] != POLICY:
            raise ValueError
        case, strategy = bundle["case"], bundle["strategy"]
        _, expected_queue = CASES[case]
        facts = bundle["evidence"]
        finished, has_note, unchanged = (facts[key] for key in ("finished", "has_note", "unrelated_unchanged"))
        queue, tools = facts["final_queue"], facts["successful_tools"]
        if (
            strategy not in STRATEGIES or not all(type(value) is bool for value in (finished, has_note, unchanged))
            or any(value not in (*QUEUES, "unassigned") for value in (queue, facts["initial_queue"]))
            or not isinstance(tools, list)
            or not all(isinstance(tool, str) and tool in TOOL_PARAMETERS for tool in tools)
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Insufficient evidence or unsupported case bundle format.") from error
    checks = ticket_checks(finished, queue, expected_queue, has_note, unchanged, tools)
    first_failure = next((check["rule"] for check in checks if not check["passed"]), None)
    return {
        "case": case, "strategy": strategy, "policy": POLICY,
        "success": first_failure is None, "first_failure": first_failure, "checks": checks,
    }


def compare(cases, repeats, model, *, spec=None, arms=STRATEGIES):
    if arms[0] == arms[1]:
        raise ValueError("Choose distinct baseline and candidate strategies.")
    if REPAIR_STRATEGY in arms and spec is None:
        raise ValueError("The repair strategy requires a connector spec.")
    revision = None
    if spec is not None:
        revision, supported, rules = connector.load_spec(spec, require_isolation=True)
        connector.endpoint()
        if any(case not in supported for case in cases):
            raise ValueError("Choose fixtures listed in the connector spec.")
    if model:
        check_lmstudio_model(model)
    rows = []
    for case in cases:
        outcomes = {strategy: [] for strategy in arms}
        initial_states = []
        for trial in range(repeats):
            order = arms if (len(rows) * repeats + trial) % 2 == 0 else reversed(arms)
            for strategy in order:
                if spec is not None:
                    outcome, before = connector_trial(case, strategy, revision, rules[case])
                    outcomes[strategy].append(outcome)
                    if before is not None:
                        initial_states.append(json.dumps(before, sort_keys=True))
                    continue
                chat = partial(lmstudio, model) if model else scripted(case, strategy)
                report = run_case(case, strategy, chat)
                bundle = export_bundle(report)
                grade = regrade(bundle)
                outcomes[strategy].append({
                    "success": None if report["terminal"].startswith("model_error:") else grade["success"],
                    "terminal": report["terminal"],
                    "first_failure": grade["first_failure"],
                    "bundle": bundle,
                })
        baseline, candidate = (outcomes[strategy] for strategy in arms)
        missing = any(run["success"] is None for runs in outcomes.values() for run in runs)
        control_passes = sum(run["success"] is True for run in baseline)
        candidate_passes = sum(run["success"] is True for run in candidate)
        state_mismatch = len(set(initial_states)) > 1
        forbidden = any(run.get("forbidden_mutation") for run in candidate)
        if forbidden:
            verdict = "regression"
        elif state_mismatch or missing or control_passes == candidate_passes == 0:
            verdict = "insufficient_evidence"
        else:
            verdict = "regression" if candidate_passes < control_passes else "pass"
        rows.append({"case": case, "verdict": verdict, "outcomes": outcomes, "state_mismatch": state_mismatch})
    verdicts = {row["verdict"] for row in rows}
    verdict = next(value for value in ("regression", "insufficient_evidence", "pass") if value in verdicts)
    return {
        "version": 1, "verdict": verdict, "model": model,
        "source": "connector" if spec is not None else "lmstudio" if model else "scripted",
        "connector_revision": revision,
        "arms": list(arms),
        "distinct_cases": len(rows), "repeats": repeats, "cases": rows,
    }


def connector_trial(case, strategy, revision, rule):
    try:
        report = connector.run(case, strategy, f"case-compare-{uuid4()}", revision, rule)
    except (OSError, ValueError):
        return {"success": None, "first_failure": "Connector evidence unavailable"}, None
    failed = list(dict.fromkeys(check["kind"] for check in report["checks"] if not check["passed"]))
    terminal = report["terminal"]
    return {
        "success": None if terminal == "connector_error" or terminal.startswith("model_error:") else report["success"],
        "first_failure": next(iter(failed), None) or ("Agent did not finish" if terminal != "finished" else None),
        "failed_checks": failed,
        "blocked_calls": report.get("blocked_calls"),
        "forbidden_mutation": report["forbidden_mutation"],
        "latency_ms": report.get("latency_ms"),
        "timing_ms": report.get("timing_ms"),
        "repair_turns": report.get("repair_turns"),
    }, report["before"]


def markdown(comparison):
    arms = comparison.get("arms", STRATEGIES)
    lines = [
        "# Aegis regression check", "",
        f"**{comparison['verdict'].replace('_', ' ').capitalize()}** across "
        f"{comparison['distinct_cases']} fixtures, {comparison['repeats']} trials per arm and fixture.", "",
        f"Source: {comparison['source']}", "",
        f"| Fixture | {arms[0]} passes | {arms[1]} passes | Decision |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in comparison["cases"]:
        counts = []
        for strategy in arms:
            outcomes = row["outcomes"][strategy]
            passed = sum(run["success"] is True for run in outcomes)
            available = sum(run["success"] is not None for run in outcomes)
            counts.append(f"{passed}/{available}")
        lines.append(f"| {row['case']} | {' | '.join(counts)} | {row['verdict'].replace('_', ' ')} |")
    for row in comparison["cases"]:
        if row["state_mismatch"]:
            lines.append(f"\n- `{row['case']}` returned different starting states.")
        for strategy in arms:
            outcomes = row["outcomes"][strategy]
            failures = dict.fromkeys(check for run in outcomes for check in [
                *run.get("failed_checks", []), run["first_failure"],
            ] if check)
            blocked = sum(bool(run.get("blocked_calls")) for run in outcomes)
            mutations = sum(bool(run.get("forbidden_mutation")) for run in outcomes)
            notes = []
            if failures:
                notes.append(f"failed checks: {', '.join(failures)}")
            if blocked:
                notes.append(f"trials with blocked calls: {blocked}")
            if mutations:
                notes.append(f"trials with protected-state mutations: {mutations}")
            if notes:
                lines.append(f"\n- `{row['case']}` / `{strategy}`. {'. '.join(notes)}.")
    if comparison["source"] == "connector":
        lines.extend([
            "", "Durations are medians in milliseconds over trials reporting each phase.",
            "Model timing includes the provider request and response. End-to-end timing also includes connector overhead.",
            "", "| Fixture | Strategy | End-to-end | Model | Tools | Orchestration | Evaluation | Repair turns |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        for row in comparison["cases"]:
            for strategy in arms:
                outcomes = row["outcomes"][strategy]
                latency = [run["latency_ms"] for run in outcomes if run.get("latency_ms") is not None]
                repairs = [run["repair_turns"] for run in outcomes if run.get("repair_turns") is not None]
                phase_values = []
                for phase in ("model", "tools", "orchestration", "evaluation"):
                    values = [(run.get("timing_ms") or {}).get(phase) for run in outcomes]
                    measured = [value for value in values if value is not None]
                    phase_values.append(f"{median(measured):.3f}" if measured else "-")
                lines.append(f"| {row['case']} | {strategy} | {median(latency) if latency else '-'} "
                             f"| {' | '.join(phase_values)} | {sum(repairs) if repairs else '-'} |")
    return "\n".join(lines)
