"""Shared helpers for the agent-neo offline tests (stdlib unittest only)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from neo_core import Agent  # noqa: E402
from neo_events import validate_event  # noqa: E402
from neo_fake import FakeChatDriver, FakeCopilotDriver  # noqa: E402
from neo_settings import Settings  # noqa: E402


class Scratch:
    """A throwaway folder under %TEMP% (removed on cleanup)."""

    def __init__(self, tag: str) -> None:
        self.path = Path(tempfile.mkdtemp(prefix=f"neo-test-{tag}-"))

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def final(message: str, **extra: Any) -> dict[str, Any]:
    return {"type": "final", "message": message, **extra}


def tool(name: str, **arguments: Any) -> dict[str, Any]:
    return {"type": "tool", "name": name, "arguments": arguments}


def script(*replies: Any, match: str = ".") -> dict[str, Any]:
    return {"scenarios": [{"match": match, "replies": list(replies)}],
            "fallback": json.dumps(final("fallback"))}


class Recorder:
    """Event sink that validates every event against the schema and keeps it."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def __call__(self, event: dict[str, Any]) -> None:
        validate_event(event)
        json.dumps({k: v for k, v in event.items() if not k.startswith("_")})  # serialisable
        with self._lock:
            self.events.append(event)

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def of(self, etype: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == etype]


class ScriptedApprover:
    """Answers approval requests from a queue; records every request."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        return self.answers.pop(0) if self.answers else False


def make_agent(workspace: Path, *replies: Any, approver=None, settings: Settings | None = None,
               driver=None, **overrides: Any) -> tuple[Agent, FakeCopilotDriver, Recorder]:
    settings = settings or Settings()
    if overrides:
        from dataclasses import replace
        settings = replace(settings, **overrides)
    drv = driver or FakeCopilotDriver(script(*replies))
    rec = Recorder()
    agent = Agent(drv, workspace, settings, rec, approver or ScriptedApprover(), skills={})
    agent.retry_delay = 0.01
    return agent, drv, rec


def clean_env() -> dict[str, str]:
    """os.environ without the host Copilot session variables (builder rule: isolated homes)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("COPILOT_AGENT_SESSION_ID", "COPILOT_CUSTOM_INSTRUCTIONS_DIRS")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env
