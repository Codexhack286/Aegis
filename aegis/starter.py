"""Local connector endpoint backed by a cloud tool-calling model."""

from copy import deepcopy
from functools import partial
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field

from aegis.core import run_agent
from aegis.runner import openai_chat
from aegis.sdk import RunEvidence, RunRequest, create_connector_app


class Tool(BaseModel):
    description: str
    result: dict[str, Any]
    updates: dict[str, Any] = Field(default_factory=dict)


class Fixture(BaseModel):
    task: str
    state: dict[str, Any]
    tools: dict[str, Tool]


class StarterConfig(BaseModel):
    revision: str
    cases: dict[str, Fixture]


class FixtureTools:
    def __init__(self, fixture):
        self.state = deepcopy(fixture.state)
        self.trace = []
        self.tools = fixture.tools
        self.schemas = [
            {"type": "function", "function": {
                "name": name, "description": tool.description,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            }}
            for name, tool in fixture.tools.items()
        ]

    def call(self, name, arguments, *, prevalidate):
        if name not in self.tools or arguments != {}:
            status, result = "invalid", {"error": "Use a listed tool with an empty arguments object."}
        else:
            tool = self.tools[name]
            self.state.update(tool.updates)
            status, result = "ok", tool.result
        self.trace.append({"tool": name, "arguments": arguments, "status": status, "result": result})
        return status, result


def create_app(config_path=None):
    path = Path(config_path or os.environ.get("AEGIS_STARTER_CONFIG", "examples/invoice-starter.json"))
    config = StarterConfig.model_validate_json(path.read_text(encoding="utf-8"))
    base_url = os.environ.get("AEGIS_CLOUD_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("AEGIS_CLOUD_BASE_URL must be an HTTPS API base URL without credentials.")
    model = os.environ.get("AEGIS_CLOUD_MODEL")
    key = os.environ.get("AEGIS_CLOUD_API_KEY")
    if not model or not key:
        raise ValueError("Set AEGIS_CLOUD_MODEL and AEGIS_CLOUD_API_KEY.")
    chat = partial(openai_chat, base_url, {
        "Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {key}",
    }, model)
    def run(request: RunRequest):
        fixture = config.cases.get(request.case)
        if fixture is None:
            raise HTTPException(404, "Unknown fixture.")
        tools = FixtureTools(fixture)
        messages = [
            {"role": "system", "content": "Complete the test task using the available tools. "
             "Use an empty arguments object for each tool. Reply briefly after executing the task."},
            {"role": "user", "content": f"{fixture.task}\nInitial state: {json.dumps(fixture.state)}"},
        ]
        result = run_agent(messages, request.strategy, chat, tools.schemas, tools)
        if result["terminal"].startswith("model_error:"):
            raise HTTPException(502, "Cloud inference failed. Check provider configuration and availability.")
        return RunEvidence(
            before=fixture.state, after=tools.state, tools=result["tools"],
            tokens=result["tokens"], summary=result["final_text"], terminal=result["terminal"],
        )

    return create_connector_app(config.revision, run)
