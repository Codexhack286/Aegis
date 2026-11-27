"""Types and a FastAPI adapter for project-owned Aegis connectors."""

from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator


class RunRequest(BaseModel):
    version: Literal[1]
    case: str
    strategy: Literal["direct", "plan_validate", "plan_validate_repair"]
    identity: str
    revision: str


class ToolCall(BaseModel):
    tool: str
    status: Literal["ok", "invalid", "blocked"]
    arguments: Any = None
    result: Any = None


class RunEvidence(BaseModel):
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    tools: list[ToolCall] = Field(default_factory=list)
    tokens: int | None = Field(default=None, ge=0)
    repair_turns: Literal[0, 1] | None = None
    summary: str | None = None
    terminal: str = "finished"
    success: bool | None = None
    forbidden_mutation: bool | None = None

    @model_validator(mode="after")
    def check_verdict(self):
        if (self.success is None) != (self.forbidden_mutation is None):
            raise ValueError("Set both verdict fields for project-owned evaluation.")
        return self


def create_connector_app(revision: str, handler: Callable[[RunRequest], RunEvidence]) -> FastAPI:
    """Serve a synchronous project handler through the version 1 connector contract."""
    if not revision.strip():
        raise ValueError("A connector needs a nonempty revision.")
    app = FastAPI(title="Aegis connector")

    @app.post("/run")
    def run(request: RunRequest):
        if request.revision != revision:
            raise HTTPException(409, "Connector revision differs from the request.")
        evidence = handler(request)
        return {"revision": revision, **evidence.model_dump(mode="json", exclude_none=True)}

    return app
