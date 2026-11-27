"""Freeze benchmark inputs, run unstarted identities, and review the ledger."""

from contextlib import closing
from hashlib import file_digest, sha256
import json
from pathlib import Path
import random
import re
from statistics import median
import sys
from types import SimpleNamespace

from aegis import connector
from aegis.core import CASES, INITIAL_QUEUES, MAX_MODEL_CALLS, MAX_TOOL_CALLS, STRATEGIES, TOOLS, TRIAGE_PROMPT
from aegis.runner import (
    LM_STUDIO_OPTIONS, OLLAMA_OPTIONS, check_lmstudio_model, connect, execute,
    lmstudio_origin, model_digest, ollama_origin,
)
from aegis.experiment import flagsmith_key, flagsmith_origin

FIXTURE_DIGEST = sha256(json.dumps([CASES, INITIAL_QUEUES, TOOLS, TRIAGE_PROMPT], sort_keys=True).encode()).hexdigest()


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def experiment_environment():
    origin = flagsmith_origin()
    key = flagsmith_key(origin)
    if not key:
        raise ValueError("Set the SDK environment key for the selected FLAGSMITH_MODE in .env.")
    return fingerprint([origin, key])


def prepare(args):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", args.batch):
        raise ValueError("Use 1–60 letters, numbers, dashes or underscores for the batch.")
    if not 1 <= args.repeats <= 50:
        raise ValueError("Choose between 1 and 50 repeats per fixture.")
    provider = getattr(args, "provider", "ollama")
    spec = getattr(args, "connector_spec", None)
    if provider == "connector" and (not args.live or spec is None):
        raise ValueError("Connector benchmarks require --live and --connector-spec.")
    if args.live and provider != "connector" and not args.model:
        raise ValueError("Provide the exact API model ID with --model for a live batch.")
    model_file = getattr(args, "model_file", None)
    if model_file and (not args.live or provider != "lmstudio"):
        raise ValueError("--model-file applies to live LM Studio benchmarks.")
    environment = experiment_environment() if args.live else None
    if args.live and provider == "lmstudio":
        if model_file is None:
            raise ValueError("Pass the GGUF file loaded in LM Studio with --model-file.")
        check_lmstudio_model(args.model)
        with model_file.open("rb") as file:
            digest = file_digest(file, "sha256").hexdigest()
    mode = "live" if args.live else "offline"
    families = [case for case in CASES if mode == "offline" or case != "recovery-drill"]
    if provider == "connector":
        revision, families, rules = connector.load_spec(spec)
    assignments = [(case, arm) for case in families for arm in (STRATEGIES if mode == "offline" else [None])]
    workload = assignments * args.repeats
    random.Random(args.seed).shuffle(workload)
    manifest = {
        "version": 1,
        "batch": args.batch,
        "mode": mode,
        "seed": args.seed,
        "fixture_repeats": args.repeats,
        "fixture_digest": FIXTURE_DIGEST,
        "limits": {"model_calls": MAX_MODEL_CALLS, "tool_calls": MAX_TOOL_CALLS},
        "thresholds": {
            "minimum_per_arm": args.minimum_per_arm,
            "minimum_gain_points": args.minimum_gain_points,
            "max_latency_ratio": args.max_latency_ratio,
            "max_token_ratio": args.max_token_ratio,
        },
        "model": args.model if args.live else None,
        "environment": environment,
        "runs": [
            {"identity": f"{args.batch}-{index:04d}", "case": case, "strategy": arm}
            for index, (case, arm) in enumerate(workload, 1)
        ],
    }
    if provider == "connector":
        manifest.update(provider=provider, connector_revision=revision,
                        connector_endpoint=fingerprint(connector.endpoint()),
                        fixture_digest=fingerprint(families), limits=None, model_digest=None)
        if rules is not None:
            manifest["connector_rules"] = rules
    elif args.live and provider == "lmstudio":
        manifest.update(provider=provider, lmstudio_origin=lmstudio_origin(), model_digest=digest,
                        lmstudio_options=LM_STUDIO_OPTIONS)
    else:
        manifest.update(ollama_origin=ollama_origin() if args.live else None,
                        model_digest=model_digest(args.model) if args.live else None,
                        ollama_options=OLLAMA_OPTIONS if args.live else None)
    limits = manifest["thresholds"]
    if (limits["minimum_per_arm"] < 1 or not 0 < limits["minimum_gain_points"] <= 100
            or any(not 1 <= limits[key] < float("inf") for key in ("max_latency_ratio", "max_token_ratio"))):
        raise ValueError("Choose positive sample and gain targets and finite overhead ratios of at least 1.")
    with Path(args.manifest).open("x", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2)
        file.write("\n")
    return {"manifest": args.manifest, "manifest_id": fingerprint(manifest), "runs": len(workload)}


def load(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest["version"] != 1:
        raise ValueError("Unknown manifest version.")
    return manifest


def run_batch(args):
    manifest = load(args.manifest)
    manifest_id = fingerprint(manifest)
    live = manifest["mode"] == "live"
    provider = manifest.get("provider", "ollama")
    if provider != "connector" and (
        manifest["fixture_digest"] != FIXTURE_DIGEST or manifest["limits"] != {
            "model_calls": MAX_MODEL_CALLS, "tool_calls": MAX_TOOL_CALLS,
        }
    ):
        raise ValueError("Fixture data or call limits changed since preparation.")
    if live and manifest["environment"] != experiment_environment():
        raise ValueError("Live service configuration differs from the frozen manifest.")
    if provider == "connector":
        if manifest["connector_endpoint"] != fingerprint(connector.endpoint()):
            raise ValueError("Connector URL differs from the frozen manifest.")
    elif live and provider == "lmstudio":
        if manifest["lmstudio_origin"] != lmstudio_origin() or manifest["lmstudio_options"] != LM_STUDIO_OPTIONS:
            raise ValueError("LM Studio configuration differs from the frozen manifest.")
        model_file = getattr(args, "model_file", None)
        if model_file is None:
            raise ValueError("Pass the same GGUF file with --model-file.")
        with model_file.open("rb") as file:
            if file_digest(file, "sha256").hexdigest() != manifest["model_digest"]:
                raise ValueError("The GGUF file differs from the frozen manifest.")
        check_lmstudio_model(manifest["model"])
    elif live and (manifest["ollama_origin"] != ollama_origin() or manifest["ollama_options"] != OLLAMA_OPTIONS):
        raise ValueError("Ollama configuration differs from the frozen manifest.")
    completed = skipped = 0
    for spec in manifest["runs"]:
        with closing(connect(args.db)) as db:
            existing = db.execute("SELECT 1 FROM runs WHERE identity = ?", (spec["identity"],)).fetchone()
        if existing:
            skipped += 1
            continue
        if live and provider == "ollama" and model_digest(manifest["model"]) != manifest["model_digest"]:
            raise ValueError("The installed Ollama model digest changed since preparation.")
        result = execute(SimpleNamespace(
            **spec, mode=manifest["mode"], model=manifest["model"], db=args.db,
            manifest_id=manifest_id, model_digest=manifest["model_digest"],
            provider=provider, connector_revision=manifest.get("connector_revision"),
            connector_rule=manifest.get("connector_rules", {}).get(spec["case"]),
        ))
        completed += 1
        if getattr(args, "progress", False):
            print(f"{result['identity']} {result['strategy']} {result['terminal']} "
                  f"success={result['success']}", file=sys.stderr, flush=True)
    return {"executed": completed, "existing_rows_preserved": skipped, "manifest_id": manifest_id}


def review(manifest, db_path, flagsmith_counts=None):
    manifest_id = fingerprint(manifest)
    arms = {arm: {"assigned": 0, "evaluated": 0, "successful": 0, "blocked_calls": 0,
                  "invalid_calls": 0, "by_fixture": {}} for arm in STRATEGIES}
    metrics = {arm: {"latency_ms": [], "tokens": []} for arm in STRATEGIES}
    incomplete = []
    forbidden_mutations = 0
    forbidden_attempts = 0

    with closing(connect(db_path)) as db:
        for spec in manifest["runs"]:
            row = db.execute("SELECT * FROM runs WHERE identity = ?", (spec["identity"],)).fetchone()
            report = json.loads(row["report"]) if row and row["report"] else None
            if not report or report.get("manifest_id") != manifest_id:
                incomplete.append(spec["identity"])
                continue
            arm = row["strategy"]
            stats = arms[arm]
            stats["assigned"] += 1
            stats["evaluated"] += 1
            stats["successful"] += report["success"]
            stats["blocked_calls"] += report["blocked_calls"]
            stats["invalid_calls"] += report["invalid_calls"]
            fixture = stats["by_fixture"].setdefault(spec["case"], {"evaluated": 0, "successful": 0})
            fixture["evaluated"] += 1
            fixture["successful"] += report["success"]
            if arm == "plan_validate":
                forbidden_mutations += (
                    report["forbidden_mutation"] if report.get("provider") == "connector"
                    else report["before"]["T-2"] != report["after"]["T-2"]
                )
                forbidden_attempts += sum(check["kind"] == "forbidden_tool" and not check["passed"]
                                          for check in report.get("checks", []))
            for field, values in metrics[arm].items():
                if report.get(field) is not None:
                    values.append(report[field])
            if row["status"] == "event_pending":
                incomplete.append(spec["identity"])

    live = manifest["mode"] == "live"
    for arm, stats in arms.items():
        stats["success_rate"] = stats["successful"] / stats["evaluated"] if stats["evaluated"] else None
        stats["median_latency_ms"] = median(metrics[arm]["latency_ms"]) if live and metrics[arm]["latency_ms"] else None
        stats["median_tokens"] = median(metrics[arm]["tokens"]) if metrics[arm]["tokens"] else None
        stats["token_coverage"] = len(metrics[arm]["tokens"])
    expected = {arm: {"exposures": stats["assigned"], "conversions": stats["successful"]}
                for arm, stats in arms.items()}
    reconciled = live and not incomplete and flagsmith_counts == expected
    baseline, candidate = (arms[arm] for arm in STRATEGIES)
    limits = manifest["thresholds"]
    holds = []
    adjusted_gain = None
    if not live:
        holds.append("Scripted runs verify the workflow.")
    if incomplete:
        holds.append("The manifest has missing, unmatched or pending runs.")
    if live and not reconciled:
        holds.append("Flagsmith counts have not been reconciled with this batch.")
    if candidate["blocked_calls"]:
        holds.append("The candidate attempted an out-of-scope action.")
    if forbidden_attempts:
        holds.append("The candidate attempted a tool forbidden by the fixture rules.")
    if live:
        if any(stats["evaluated"] < limits["minimum_per_arm"] for stats in arms.values()):
            holds.append("The batch is below the minimum sample per strategy.")
        families = {spec["case"] for spec in manifest["runs"]}
        if any(set(stats["by_fixture"]) != families for stats in arms.values()):
            holds.append("Both strategies need coverage of every fixture family.")
        if all(set(stats["by_fixture"]) == families for stats in arms.values()):
            rates = [
                sum(item["successful"] / item["evaluated"] for item in stats["by_fixture"].values())
                / len(families)
                for stats in (baseline, candidate)
            ]
        else:
            rates = [None, None]
        if None not in rates:
            adjusted_gain = (rates[1] - rates[0]) * 100
            if adjusted_gain + 1e-9 < limits["minimum_gain_points"]:
                holds.append("Fixture-adjusted success gain is below the frozen target.")
        if any(stats["token_coverage"] < stats["evaluated"] for stats in arms.values()):
            holds.append("Token measurements are incomplete.")
        for field, threshold in (("median_latency_ms", "max_latency_ratio"), ("median_tokens", "max_token_ratio")):
            base, new = baseline[field], candidate[field]
            if base is None or new is None or new > base * limits[threshold]:
                holds.append(f"{field.replace('_', ' ').capitalize()} is missing or exceeds the budget.")
    recommendation = "reject" if forbidden_mutations else "hold" if holds else "review_for_expansion"
    return {
        "batch": manifest["batch"], "mode": manifest["mode"], "manifest_id": manifest_id,
        "provider": manifest.get("provider", "ollama") if live else None,
        "model": manifest.get("model"),
        "recommendation": recommendation, "reasons": (["Forbidden candidate mutation."] if forbidden_mutations else holds),
        "thresholds": limits, "arms": arms, "incomplete_identities": incomplete,
        "fixture_adjusted_gain_points": adjusted_gain,
        "expected_flagsmith_counts": expected if live else None, "flagsmith_reconciled": reconciled,
    }


def record_decision(path, report, decision, reason):
    reason = reason.strip()
    if not reason:
        raise ValueError("Provide the reviewer's reason.")
    if decision == "expand" and report["recommendation"] != "review_for_expansion":
        raise ValueError("Expansion requires a benchmark that passed the review gates.")
    record = {"decision": decision, "reason": reason, "review": report}
    with Path(path).open("x", encoding="utf-8") as file:
        json.dump(record, file, indent=2)
        file.write("\n")
    return {"decision_file": path, "decision": decision}
