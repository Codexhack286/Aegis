import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from aegis import benchmark, connector, regression
from aegis.core import CASES, CONNECTOR_STRATEGIES, STRATEGIES
from aegis.experiment import Experiment
from aegis.runner import MODEL_PROVIDERS, execute


def run(args):
    result = execute(args)
    print(json.dumps(result, indent=2))
    return 0 if result["success"] else 1

def smoke(args):
    experiment = Experiment()
    counts = {strategy: {"assignments": 0, "success_submissions": 0} for strategy in STRATEGIES}
    for index in range(args.count):
        identity = f"{args.batch}-{index:04d}"
        strategy = experiment.expose(identity)
        if index == 0 and experiment.resolve(identity).value != strategy:
            raise ValueError("Flagsmith returned a different strategy for the same smoke identity.")
        counts[strategy]["assignments"] += 1
        success = index % 2 == 0
        if success:
            experiment.success(identity)
            counts[strategy]["success_submissions"] += 1
        print(json.dumps({"identity": identity, "strategy": strategy, "success_submitted": success}), flush=True)
    print(json.dumps({"expected_dashboard_counts": counts}))
    return 0 if all(arm["assignments"] and arm["success_submissions"] for arm in counts.values()) else 1


def main():
    default_db = os.environ.get("AEGIS_DB", "aegis.sqlite3")
    parser = argparse.ArgumentParser(description="Evaluate helpdesk agent strategies.")
    commands = parser.add_subparsers(dest="mode", required=True)
    for mode in ("offline", "live", "model-test"):
        command = commands.add_parser(mode)
        command.add_argument("--case", choices=CASES, default="password-reset")
        command.add_argument("--db", default=default_db)
        if mode == "live":
            command.add_argument("--identity", required=True)
            command.add_argument("--model", required=True)
            command.add_argument("--provider", choices=MODEL_PROVIDERS, default="ollama")
            command.set_defaults(strategy=None)
        else:
            command.add_argument("--strategy", choices=STRATEGIES, required=True)
            command.set_defaults(identity=None)
            if mode == "model-test":
                command.add_argument("--model", required=True)
            else:
                command.set_defaults(model=None)
    command = commands.add_parser("flagsmith-smoke")
    command.add_argument("--batch", required=True, help="Fresh prefix in a dedicated smoke-test experiment.")
    command.add_argument("--count", type=int, default=40)
    command = commands.add_parser("connector-check", help="Check one fixture without a Flagsmith experiment.")
    command.add_argument("spec", type=Path)
    command.add_argument("--case", required=True)
    command.add_argument("--strategy", choices=CONNECTOR_STRATEGIES, required=True)
    command = commands.add_parser("case-export", help="Export sanitized helpdesk evidence from the ledger.")
    command.add_argument("identity")
    command.add_argument("--db", default=default_db)
    command = commands.add_parser("case-regrade", help="Evaluate a portable bundle offline.")
    command.add_argument("bundle", type=Path)
    command = commands.add_parser("case-compare", help="Run both strategies on isolated fixtures.")
    command.add_argument("--case", action="append", required=True)
    command.add_argument("--repeats", type=int, default=1)
    command.add_argument("--baseline", choices=STRATEGIES, default="direct")
    command.add_argument("--candidate", choices=CONNECTOR_STRATEGIES[1:], default="plan_validate")
    source = command.add_mutually_exclusive_group()
    source.add_argument("--model", help="Exact LM Studio model ID. Omit for scripted runs.")
    source.add_argument("--connector-spec", type=Path, help="Connector spec declaring fresh state per trial.")
    command.add_argument("--format", choices=("json", "markdown"), default="json")
    command = commands.add_parser("benchmark-prepare", help="Freeze the workload and review thresholds.")
    command.add_argument("manifest")
    command.add_argument("--batch", required=True)
    command.add_argument("--repeats", type=int, default=1)
    command.add_argument("--seed", type=int, default=42)
    command.add_argument("--live", action="store_true")
    command.add_argument("--model")
    command.add_argument("--provider", choices=(*MODEL_PROVIDERS, "connector"), default="ollama")
    command.add_argument("--connector-spec", type=Path, help="External project's fixture IDs and revision.")
    command.add_argument("--model-file", type=Path, help="GGUF loaded in LM Studio.")
    command.add_argument("--minimum-per-arm", type=int, default=10)
    command.add_argument("--minimum-gain-points", type=float, default=5)
    command.add_argument("--max-latency-ratio", type=float, default=1.5)
    command.add_argument("--max-token-ratio", type=float, default=1.5)
    for name in ("benchmark-run", "benchmark-review", "benchmark-decision"):
        command = commands.add_parser(name)
        command.add_argument("manifest")
        command.add_argument("--db", default=default_db)
        if name == "benchmark-run":
            command.add_argument("--model-file", type=Path)
            command.add_argument("--progress", action="store_true", help="Print each completed identity to stderr.")
        if name != "benchmark-run":
            command.add_argument("--flagsmith-counts", type=Path)
        if name == "benchmark-decision":
            command.add_argument("--output", required=True)
            command.add_argument("--decision", choices=("hold", "reject", "expand"), required=True)
            command.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        if args.mode == "case-export":
            print(json.dumps(regression.from_ledger(args.db, args.identity), indent=2))
            return 0
        if args.mode == "case-regrade":
            try:
                result = regression.regrade(regression.load(args.bundle))
            except (OSError, ValueError) as error:
                print(f"Aegis: {error}", file=sys.stderr)
                return 2
            print(json.dumps(result, indent=2))
            return 0 if result["success"] else 1
        if args.mode == "case-compare":
            if not 1 <= args.repeats <= 20:
                parser.error("--repeats must be between 1 and 20")
            try:
                cases = list(dict.fromkeys(args.case))
                if args.connector_spec is None and any(case not in CASES for case in cases):
                    raise ValueError(f"Choose a built-in fixture: {', '.join(CASES)}.")
                result = regression.compare(cases, args.repeats, args.model, spec=args.connector_spec,
                                            arms=(args.baseline, args.candidate))
            except (OSError, RuntimeError, ValueError) as error:
                print(f"Aegis: {error}", file=sys.stderr)
                return 2
            print(regression.markdown(result) if args.format == "markdown" else json.dumps(result, indent=2))
            return {"pass": 0, "regression": 1, "insufficient_evidence": 2}[result["verdict"]]
        if args.mode == "connector-check":
            revision, cases, rules = connector.load_spec(args.spec)
            if args.case not in cases:
                raise ValueError("The fixture is not listed in the connector spec.")
            result = connector.run(args.case, args.strategy, f"connector-check-{uuid4()}",
                                   revision, rules[args.case] if rules else None)
            print(json.dumps(result, indent=2))
            return 0 if result["success"] else 1
        if args.mode == "flagsmith-smoke":
            if args.count < 4:
                parser.error("--count must be at least 4")
            return smoke(args)
        if args.mode.startswith("benchmark-"):
            if args.mode == "benchmark-prepare":
                result = benchmark.prepare(args)
            elif args.mode == "benchmark-run":
                result = benchmark.run_batch(args)
            else:
                counts = json.loads(args.flagsmith_counts.read_text(encoding="utf-8")) if args.flagsmith_counts else None
                result = benchmark.review(benchmark.load(args.manifest), args.db, counts)
                if args.mode == "benchmark-decision":
                    result = benchmark.record_decision(args.output, result, args.decision, args.reason)
            print(json.dumps(result, indent=2))
            return 0
        return run(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Aegis: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

