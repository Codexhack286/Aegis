from contextlib import closing
import json
import os
from pathlib import Path
from statistics import median
from types import SimpleNamespace

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import ClientDisconnect

from aegis import benchmark, regression
from aegis.core import CASES, STRATEGIES
from aegis.experiment import flagsmith_key, flagsmith_key_name, flagsmith_origin
from aegis.runner import MODEL_PROVIDERS, connect, execute

DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"
MAX_RUN_BODY = 16_384


def error_response(message, status_code=400):
    return JSONResponse({"error": message}, status_code=status_code)


def unpack(row):
    record = dict(row)
    record["report"] = json.loads(record["report"]) if record["report"] else None
    return record


def summarize(arm, rows):
    assigned = [row for row in rows if row["strategy"] == arm]
    reports = [row["report"] for row in assigned if row["report"]]
    return {
        "strategy": arm,
        "assigned": len(assigned),
        "evaluated": len(reports),
        "successful": sum(report["success"] for report in reports),
        "blocked_calls": sum(report["blocked_calls"] for report in reports),
        "invalid_calls": sum(report["invalid_calls"] for report in reports),
        "median_latency_ms": median(report["latency_ms"] for report in reports) if reports else None,
    }


def create_app(db_path=None):
    load_dotenv(Path.cwd() / ".env", encoding="utf-8-sig", override=False)
    app = FastAPI(title="Aegis API")
    db_path = db_path or os.environ.get("AEGIS_DB", "aegis.sqlite3")

    @app.get("/api/workspace")
    def workspace(mode: str = "offline", benchmark_only: bool = False):
        if mode not in ("offline", "live"):
            return error_response("Choose offline or live.")
        with closing(connect(db_path)) as db:
            rows = [unpack(row) for row in db.execute(
                "SELECT * FROM runs WHERE mode = ? ORDER BY rowid DESC", (mode,),
            )]
        if benchmark_only:
            manifest_path = os.environ.get("AEGIS_MANIFEST")
            if not manifest_path:
                return error_response("Set AEGIS_MANIFEST to scope the workspace.")
            try:
                manifest = benchmark.load(manifest_path)
                manifest_id = benchmark.fingerprint(manifest)
                identities = {entry["identity"] for entry in manifest["runs"]}
                rows = [row for row in rows if row["identity"] in identities and (
                    row["report"] is None or row["report"].get("manifest_id") == manifest_id
                )]
            except (OSError, ValueError, KeyError, TypeError):
                return error_response("Could not read the configured benchmark manifest.")
        recent = []
        for row in rows[:50]:
            report = row["report"] or {}
            recent.append({
                **{key: row[key] for key in ("identity", "case_id", "strategy", "status", "model")},
                "provider": report.get("provider", "ollama" if row["mode"] == "live" else None),
                "manifest_id": report.get("manifest_id"),
                "success": report.get("success"),
                "latency_ms": report.get("latency_ms"),
            })
        try:
            origin = flagsmith_origin()
            configuration_error = None if flagsmith_key(origin) else (
                f"Set {flagsmith_key_name(origin)} in .env and restart the dashboard backend."
            )
        except ValueError as error:
            configuration_error = str(error)
        return {
            "cases": list(CASES), "runs": recent, "run_count": len(rows),
            "arms": [summarize(arm, rows) for arm in STRATEGIES],
            "live_configured": configuration_error is None,
            "live_configuration_error": configuration_error,
        }

    @app.get("/api/runs/{identity}")
    def detail(identity: str):
        with closing(connect(db_path)) as db:
            row = db.execute("SELECT * FROM runs WHERE identity = ?", (identity,)).fetchone()
        if row is None:
            return error_response("Run not found.", 404)
        return unpack(row)

    @app.get("/api/runs/{identity}/case-bundle")
    def case_bundle(identity: str):
        try:
            bundle = regression.from_ledger(db_path, identity)
            return {"bundle": bundle, "grade": regression.regrade(bundle)}
        except ValueError as error:
            return error_response(str(error), 404 if str(error) == "Run not found." else 422)

    @app.get("/api/benchmark-review")
    def benchmark_review():
        manifest_path = os.environ.get("AEGIS_MANIFEST")
        if not manifest_path:
            return {"review": None}
        try:
            manifest = benchmark.load(manifest_path)
            counts_path = os.environ.get("AEGIS_FLAGSMITH_COUNTS")
            counts = json.loads(Path(counts_path).read_text(encoding="utf-8")) if counts_path else None
            return {"review": benchmark.review(manifest, db_path, counts)}
        except (OSError, ValueError, KeyError, TypeError) as error:
            return error_response(f"Benchmark review unavailable. {error}")

    @app.post("/api/runs", status_code=201)
    async def start(request: Request):
        body = bytearray()
        try:
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_RUN_BODY:
                    return error_response("Run configuration exceeds 16 KiB.", 413)
                body.extend(chunk)
            data = json.loads(body)
        except (ValueError, ClientDisconnect):
            return error_response("Provide a JSON run configuration.")
        if not isinstance(data, dict):
            return error_response("Provide a JSON run configuration.")
        mode, case = data.get("mode"), data.get("case")
        if mode not in ("offline", "live") or not isinstance(case, str) or case not in CASES:
            return error_response("Choose a valid mode and ticket fixture.")
        strategy = data.get("strategy")
        provider = data.get("provider", "ollama")
        identity = model = None
        if mode == "offline":
            if strategy not in STRATEGIES:
                return error_response("Choose a strategy for the scripted run.")
        else:
            if provider not in MODEL_PROVIDERS:
                return error_response("Choose Ollama or LM Studio.")
            identity, model = data.get("identity"), data.get("model")
            if not all(isinstance(value, str) and value.strip() and len(value) <= 128
                       for value in (identity, model)):
                return error_response("Provide a run identity and the exact API model ID.")
            if not all(char.isalnum() or char in "-_." for char in identity):
                return error_response("Use letters, numbers, dots, dashes or underscores in the identity.")
        try:
            return await run_in_threadpool(execute, SimpleNamespace(
                mode=mode, case=case, strategy=strategy, identity=identity, model=model, db=db_path,
                provider=provider,
            ))
        except (OSError, RuntimeError, ValueError) as error:
            return error_response(str(error))

    @app.get("/", include_in_schema=False)
    def dashboard():
        if not (DIST / "index.html").exists():
            return error_response("Start Vite or build the dashboard in ui/.", 503)
        return FileResponse(DIST / "index.html")

    @app.get("/assets/{name:path}", include_in_schema=False)
    def assets(name: str):
        directory = (DIST / "assets").resolve()
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            return error_response("Asset not found.", 404)
        return FileResponse(path)

    return app
