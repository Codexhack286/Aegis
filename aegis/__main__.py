import argparse
import json
from pathlib import Path
import sys

from aegis import benchmark
from aegis.core import CASES, STRATEGIES
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
    parser = argparse.ArgumentParser(description="Evaluate helpdesk agent strategies.")
    commands = parser.add_subparsers(dest="mode", required=True)
    for mode in ("offline", "live", "model-test"):
        command = commands.add_parser(mode)
        command.add_argument("--case", choices=CASES, default="password-reset")
        command.add_argument("--db", default="aegis.sqlite3")
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
        command.add_argument("--db", default="aegis.sqlite3")
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

