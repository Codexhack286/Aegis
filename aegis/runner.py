from contextlib import closing
import json
import os
import sqlite3
from urllib.request import Request, urlopen
from uuid import uuid4

from aegis import connector
from aegis.core import CASES, INITIAL_QUEUES, run_case
from aegis.experiment import Experiment


SCHEMA = """CREATE TABLE IF NOT EXISTS runs (
    identity TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    case_id TEXT NOT NULL,
    strategy TEXT,
    model TEXT,
    status TEXT NOT NULL,
    report TEXT
)"""
OLLAMA_OPTIONS = {"temperature": 0, "num_predict": 512}
LM_STUDIO_OPTIONS = {"temperature": 0, "max_tokens": 512}
MODEL_PROVIDERS = ("ollama", "lmstudio")


def ollama_origin():
    return os.environ.get("OLLAMA_ORIGIN", "http://127.0.0.1:11434").rstrip("/")


def lmstudio_origin():
    return os.environ.get("LM_STUDIO_ORIGIN", "http://127.0.0.1:1234").rstrip("/").removesuffix("/v1")


def lmstudio_headers():
    headers = {"Content-Type": "application/json"}
    if token := os.environ.get("LM_STUDIO_API_KEY"):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def check_lmstudio_model(model):
    request = Request(f"{lmstudio_origin()}/v1/models", headers=lmstudio_headers())
    with urlopen(request, timeout=10) as response:
        models = json.load(response)["data"]
    if not any(entry["id"] == model for entry in models):
        raise ValueError("LM Studio did not list this model. Check the exact API model ID.")


def model_digest(model):
    with urlopen(f"{ollama_origin()}/api/tags", timeout=10) as response:
        models = json.load(response)["models"]
    for entry in models:
        if entry["name"] == model and entry.get("digest"):
            return entry["digest"]
    raise ValueError("Use the exact installed Ollama model tag, including its suffix.")


def connect(path):
    db = sqlite3.connect(path)
    db.execute(SCHEMA)
    db.row_factory = sqlite3.Row
    return db


def ollama(model, messages, tools):
    payload = json.dumps({
        "model": model, "messages": messages, "tools": tools,
        "stream": False, "options": OLLAMA_OPTIONS,
    }).encode()
    request = Request(f"{ollama_origin()}/api/chat", payload, {"Content-Type": "application/json"})
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def lmstudio(model, messages, tools):
    return openai_chat(f"{lmstudio_origin()}/v1", lmstudio_headers(), model, messages, tools)


def openai_chat(base_url, headers, model, messages, tools):
    history = [
        {"role": "tool", "content": message["content"], "tool_call_id": message["tool_call_id"]}
        if message["role"] == "tool" else message
        for message in messages
    ]
    payload = {"model": model, "messages": history, "stream": False, **LM_STUDIO_OPTIONS}
    if tools:
        payload["tools"] = tools
    request = Request(
        f"{base_url}/chat/completions", json.dumps(payload).encode(), headers,
    )
    with urlopen(request, timeout=60) as response:
        result = json.load(response)
    usage = result.get("usage") or {}
    return {
        "message": result["choices"][0]["message"],
        "prompt_eval_count": usage.get("prompt_tokens"),
        "eval_count": usage.get("completion_tokens"),
    }


def scripted(case, strategy="direct"):
    queue = CASES[case][1]
    sequence = [("search_knowledge_base", {"query": "routing"})]
    if case == "recovery-drill":
        sequence.append(("assign_ticket", {"ticket_id": "T-1", "queue": "payments"}))
    if case != "recovery-drill" or strategy == "plan_validate":
        if INITIAL_QUEUES.get(case) != queue:
            sequence.append(("assign_ticket", {"ticket_id": "T-1", "queue": queue}))
        sequence.append(("add_internal_note", {"ticket_id": "T-1", "note": "Routing verified against the knowledge base."}))
    calls = iter(sequence)

    def chat(messages, tools):
        if not tools:
            return {"message": {"role": "assistant", "content": "Look up routing, assign, then note."}}
        call = next(calls, None)
        if call is None:
            return {"message": {"role": "assistant", "content": "Ticket routed."}}
        name, arguments = call
        return {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": name, "arguments": arguments}},
        ]}}

    return chat


def execute(args):
    provider = getattr(args, "provider", "ollama") if args.mode == "live" else None
    if args.mode == "model-test":
        provider = "lmstudio"
    if provider == "lmstudio" and args.mode == "live" and not getattr(args, "manifest_id", None):
        check_lmstudio_model(args.model)
    experiment = Experiment() if args.mode == "live" else None
    identity = args.identity or f"{args.mode}-{uuid4()}"
    with closing(connect(args.db)) as db:
        with db:
            try:
                db.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, NULL, ?, 'started', NULL)",
                    (identity, args.mode, args.case, args.model),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("Identity already in the ledger. Inspect its run before replaying.") from error
        flag = experiment.expose_flag(identity) if experiment else None
        strategy = flag.value if flag is not None else args.strategy
        with db:
            db.execute("UPDATE runs SET strategy = ? WHERE identity = ?", (strategy, identity))
        if provider == "connector":
            try:
                result = connector.run(args.case, strategy, identity, args.connector_revision,
                                       getattr(args, "connector_rule", None))
            except (OSError, ValueError):
                with db:
                    db.execute("UPDATE runs SET status = 'connector_error' WHERE identity = ?", (identity,))
                raise
        else:
            if args.mode == "offline":
                chat = scripted(args.case, strategy)
            else:
                inference = lmstudio if provider == "lmstudio" else ollama

                def chat(messages, tools):
                    return inference(args.model, messages, tools)

            result = run_case(args.case, strategy, chat)
        result.update(identity=identity, mode=args.mode, model=args.model)
        if flag is not None:
            result["experiment_id"] = flag.experiment.id
        if provider:
            result["provider"] = provider
        if getattr(args, "manifest_id", None):
            result.update(manifest_id=args.manifest_id, model_digest=args.model_digest)
        status = "event_pending" if experiment and result["success"] else "completed"
        with db:
            db.execute(
                "UPDATE runs SET status = ?, report = ? WHERE identity = ?",
                (status, json.dumps(result), identity),
            )
        if status == "event_pending":
            experiment.success(identity)
            status = "event_submitted_unverified"
            with db:
                db.execute("UPDATE runs SET status = ? WHERE identity = ?", (status, identity))
    result["status"] = status
    return result



