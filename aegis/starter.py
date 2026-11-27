"""Local connector endpoint backed by a cloud tool-calling model."""

from copy import deepcopy
from functools import partial
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from aegis.core import run_agent
from aegis.runner import lmstudio, openai_chat
from aegis.sdk import RunEvidence, RunRequest, create_connector_app


class RequiredToolResult(BaseModel):
    tool: str
    result: dict[str, Any] = Field(min_length=1)


class Tool(BaseModel):
    description: str
    result: dict[str, Any]
    updates: dict[str, Any] = Field(default_factory=dict)
    append: dict[str, Any] = Field(default_factory=dict)
    first_response: dict[str, Any] | None = None
    requires_result: RequiredToolResult | None = None
    disabled: bool = False


class RepairCondition(BaseModel):
    tool: str
    pending_state: dict[str, Any] = Field(min_length=1)
    evidence_tool: str
    evidence_result: dict[str, Any] = Field(min_length=1)


class Fixture(BaseModel):
    task: str
    state: dict[str, Any]
    tools: dict[str, Tool]
    repair: RepairCondition | None = None

    @model_validator(mode="after")
    def validate_tool_configuration(self):
        targets = {key for tool in self.tools.values() for key in tool.append}
        if any(not isinstance(self.state.get(key), list) for key in targets) or any(
            targets.intersection(tool.updates) for tool in self.tools.values()
        ):
            raise ValueError("Append targets must be lists in initial state with no replacement updates.")
        for name, tool in self.tools.items():
            required = tool.requires_result
            if required and (required.tool == name or required.tool not in self.tools
                             or self.tools[required.tool].disabled):
                raise ValueError("Tool result requirements must reference another enabled fixture tool.")
        if self.repair and (
            self.repair.tool not in self.tools or self.repair.evidence_tool not in self.tools
            or self.tools[self.repair.tool].disabled or self.tools[self.repair.evidence_tool].disabled
            or self.repair.tool == self.repair.evidence_tool
            or self.repair.pending_state.keys() - self.state.keys()
        ):
            raise ValueError("Repair requires distinct fixture tools and known pending state keys.")
        return self


class StarterConfig(BaseModel):
    revision: str
    cases: dict[str, Fixture]


class FixtureTools:
    def __init__(self, fixture):
        self.state = deepcopy(fixture.state)
        self.trace = []
        self.tools = fixture.tools
        self.committed = set()
        self.repair = fixture.repair
        self.schemas = [
            {"type": "function", "function": {
                "name": name, "description": tool.description,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            }}
            for name, tool in fixture.tools.items()
        ]

    def has_tool_result(self, name, expected):
        latest = next((call for call in reversed(self.trace) if call["tool"] == name), None)
        return (
            latest is not None and latest["status"] == "ok"
            and json.dumps(latest["result"], sort_keys=True) == json.dumps(expected, sort_keys=True)
        )

    def available_tools(self):
        return [schema for schema in self.schemas if self.tool_is_available(schema["function"]["name"])]

    def tool_is_available(self, name):
        tool = self.tools[name]
        required = tool.requires_result
        return not tool.disabled and (required is None or self.has_tool_result(required.tool, required.result))

    def pending_repair_tool(self):
        rule = self.repair
        if rule is None or not all(self.state[key] == value for key, value in rule.pending_state.items()):
            return None
        if self.has_tool_result(rule.evidence_tool, rule.evidence_result):
            return rule.tool
        return None

    def call(self, name, arguments, *, prevalidate):
        tool = self.tools.get(name)
        if tool is None or arguments != {}:
            status, result = "invalid", {"error": "Use a listed tool with an empty arguments object."}
        elif not self.tool_is_available(name):
            status, result = "blocked", {"error": "This tool is unavailable for the current task or state."}
        else:
            result = tool.result
            if tool.first_response is None or name not in self.committed:
                self.state.update(deepcopy(tool.updates))
                for key, entry in tool.append.items():
                    self.state[key].append(deepcopy(entry))
                if tool.first_response is not None:
                    self.committed.add(name)
                    result = tool.first_response
            status = "ok"
        self.trace.append({"tool": name, "arguments": arguments, "status": status, "result": result})
        return status, result


def create_app(config_path=None, *, chat=None):
    path = Path(config_path or os.environ.get("AEGIS_STARTER_CONFIG", "examples/invoice/starter.json"))
    config = StarterConfig.model_validate_json(path.read_text(encoding="utf-8"))
    chat = chat if chat is not None else configured_chat()
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
        result = run_agent(messages, request.strategy, chat, tools.schemas, tools,
                           repair=tools.pending_repair_tool, available_tools=tools.available_tools)
        if result["terminal"].startswith("model_error:"):
            raise HTTPException(502, "Inference failed. Check provider configuration and availability.")
        return RunEvidence(
            before=fixture.state, after=tools.state, tools=result["tools"],
            tokens=result["tokens"], summary=result["final_text"], terminal=result["terminal"],
            repair_turns=result["repair_turns"],
        )

    return create_connector_app(config.revision, run)


def configured_chat():
    local_model = os.environ.get("AEGIS_STARTER_LMSTUDIO_MODEL")
    if local_model:
        return partial(lmstudio, local_model)
    base_url = os.environ.get("AEGIS_CLOUD_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("AEGIS_CLOUD_BASE_URL must be an HTTPS API base URL without credentials.")
    model = os.environ.get("AEGIS_CLOUD_MODEL")
    key = os.environ.get("AEGIS_CLOUD_API_KEY")
    if not model or not key:
        raise ValueError("Set AEGIS_CLOUD_MODEL and AEGIS_CLOUD_API_KEY.")
    return partial(openai_chat, base_url, {
        "Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {key}",
    }, model)
