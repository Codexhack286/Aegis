"""Run the live invoice demo from the repository root."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from dotenv import load_dotenv

from aegis import connector, regression
from aegis.runner import check_lmstudio_model, lmstudio_origin


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "examples" / "invoice" / "rules.json"
CONFIG = SPEC.with_name("starter.json")
CASES = ["invoice-approval", "invoice-receipt-mismatch", "invoice-retry"]
ARMS = ("plan_validate", "plan_validate_repair")
EXIT_CODES = {"pass": 0, "regression": 1, "insufficient_evidence": 2}


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@contextmanager
def serve(app, title, port, log_path, env):
    origin = f"http://127.0.0.1:{port}"
    command = [sys.executable, "-m", "uvicorn", app, "--factory",
               "--host", "127.0.0.1", "--port", str(port)]
    loopback = build_opener(ProxyHandler({}))
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        try:
            deadline = monotonic() + 12
            while process.poll() is None and monotonic() < deadline:
                try:
                    with loopback.open(origin + "/openapi.json", timeout=1) as response:
                        if json.load(response).get("info", {}).get("title") == title and process.poll() is None:
                            break
                except (OSError, ValueError):
                    pass
                sleep(0.2)
            else:
                raise RuntimeError(f"Could not start {title} on port {port}. See {log_path}.")
            yield origin
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def save_comparison(directory, name, comparison):
    save_json(directory / f"{name}.json", comparison)
    (directory / f"{name}.md").write_text(regression.markdown(comparison) + "\n", encoding="utf-8")
    print(f"{name}: {comparison['verdict'].replace('_', ' ')}", flush=True)


def run_flagsmith(args, directory, batch_id, env):
    manifest = directory / "manifest.json"
    db = directory / "runs.sqlite3"
    commands = [
        ("prepare", ["benchmark-prepare", str(manifest), "--batch", batch_id, "--live",
                     "--provider", "connector", "--connector-spec", str(SPEC), "--model", args.model,
                     "--repeats", str(args.repeats)]),
        ("run", ["benchmark-run", str(manifest), "--db", str(db), "--progress"]),
        ("review", ["benchmark-review", str(manifest), "--db", str(db)]),
    ]
    for name, options in commands:
        path = directory / f"flagsmith-{name}.json"
        print(f"Flagsmith {name}…", flush=True)
        with path.open("w", encoding="utf-8") as output:
            result = subprocess.run([sys.executable, "-m", "aegis", *options],
                                    cwd=ROOT, env=env, stdout=output, check=False)
        if result.returncode:
            raise RuntimeError(f"Flagsmith {name} exited {result.returncode}. Demo files are in {directory}.")
    print("Flagsmith events submitted. Compare observed counts in Flagsmith before using the release review.", flush=True)


def run(args):
    load_dotenv(ROOT / ".env", encoding="utf-8-sig", override=False)
    if args.serve and not (ROOT / "ui" / "dist" / "index.html").is_file():
        raise ValueError("Build the dashboard first with npm --prefix ui run build.")
    if args.flagsmith:
        from aegis.benchmark import experiment_environment

        experiment_environment()
    try:
        check_lmstudio_model(args.model)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"LM Studio preflight failed at {lmstudio_origin()}: {error}") from error

    batch_id = f"invoice-demo-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
    directory = ROOT / "demo-runs" / batch_id
    directory.mkdir(parents=True)
    print(f"Demo files: {directory}", flush=True)
    os.environ["AEGIS_CONNECTOR_URL"] = f"http://127.0.0.1:{args.connector_port}/run"
    env = {**os.environ, "AEGIS_STARTER_LMSTUDIO_MODEL": args.model, "AEGIS_STARTER_CONFIG": str(CONFIG)}
    revision, _, rules = connector.load_spec(SPEC, require_isolation=True)

    with serve("aegis.starter:create_app", "Aegis connector", args.connector_port, directory / "connector.log", env):
        print("Running the invoice retry against LM Studio…", flush=True)
        retry = connector.run("invoice-retry", "direct", batch_id + "-retry", revision, rules["invoice-retry"])
        save_json(directory / "retry-trace.json", retry)
        print(f"Retry trace: {'pass' if retry['success'] else 'failed'}", flush=True)
        print(f"Running {len(CASES) * len(ARMS) * args.repeats} paired trials…", flush=True)
        comparison = regression.compare(CASES, args.repeats, None, spec=SPEC, arms=ARMS)
        save_comparison(directory, "comparison", comparison)
        if args.flagsmith and comparison["verdict"] != "insufficient_evidence":
            run_flagsmith(args, directory, batch_id, env)

    outage = regression.compare(["invoice-retry"], 1, None, spec=SPEC, arms=ARMS)
    save_comparison(directory, "outage", outage)
    outcomes = outage["cases"][0]["outcomes"]
    if outage["verdict"] != "insufficient_evidence" or any(
        trial["success"] is not None for trials in outcomes.values() for trial in trials
    ):
        raise RuntimeError("The connector still returned evidence after shutdown.")

    exit_code = EXIT_CODES[comparison["verdict"]] or int(not retry["success"])
    if args.serve:
        dashboard_env = {**env, "AEGIS_DB": str(directory / "runs.sqlite3"),
                         "AEGIS_MANIFEST": str(directory / "manifest.json") if (directory / "flagsmith-review.json").is_file() else "",
                         "AEGIS_FLAGSMITH_COUNTS": ""}
        with serve("aegis.web:create_app", "Aegis API", args.dashboard_port, directory / "dashboard.log", dashboard_env) as origin:
            print(f"Dashboard: {origin}/#compare — import comparison.json and outage.json from the demo folder.", flush=True)
            if dashboard_env["AEGIS_MANIFEST"]:
                print(f"Flagsmith batch: {origin}/#live — open Review your benchmark under run history.", flush=True)
            print("Press Ctrl+C to stop the demo dashboard.", flush=True)
            try:
                while True:
                    sleep(1)
            except KeyboardInterrupt:
                pass
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen2.5-3b-instruct", help="Exact ID from the LM Studio API.")
    parser.add_argument("--repeats", type=int, default=2, choices=range(1, 21), metavar="N")
    parser.add_argument("--connector-port", type=int, default=9010)
    parser.add_argument("--dashboard-port", type=int, default=5010)
    parser.add_argument("--serve", action="store_true", help="Serve the built dashboard after the run.")
    parser.add_argument("--flagsmith", action="store_true", help="Send events to the configured demo experiment.")
    args = parser.parse_args()
    if not 1 <= args.connector_port <= 65535 or not 1 <= args.dashboard_port <= 65535:
        parser.error("Choose ports between 1 and 65535.")
    if args.serve and args.connector_port == args.dashboard_port:
        parser.error("Choose different connector and dashboard ports.")
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Live demo: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
