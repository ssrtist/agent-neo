"""neo_fake.py: a scripted, browser-free chat driver for tests and GUI e2e (plan v0.4 acceptance 2-3).

Script (JSON):
    {
      "scenarios": [
        {"match": "regex searched in the user request / sub-agent task",
         "replies": [ <reply>, ... ]}
      ],
      "fallback": "text replied when no scripted reply is left"
    }
A <reply> is a string (sent verbatim), a JSON object (sent as compact JSON), or
    {"reply": <string|object>, "delay": <seconds>, "error": "<raise RuntimeError with this text>"}

A turn starts when a prompt carries "USER REQUEST:" or "TASK:"; the first scenario whose
``match`` is found in the text after that marker supplies the replies for that turn. While a
reply is "generating" (``delay``), the driver reports "agent-neo is working..." and honours
cancel_check like the real web driver.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from neo_events import Cancelled

DEMO_SCRIPT: dict[str, Any] = {
    "scenarios": [
        {
            "match": "(?i)write|approve",
            "replies": [
                {"reply": {"type": "tool", "name": "write_file", "arguments": {"path": "demo-note.txt", "content": "hello from the fake backend\n"},
                           "thought": "The user wants a file written; that needs approval."}, "delay": 0.6},
                {"reply": {"type": "final", "message": "Wrote **demo-note.txt** (if you approved it)."}, "delay": 0.4},
            ],
        },
        {
            "match": ".",
            "replies": [
                {"reply": {"type": "tool", "name": "run_shell", "arguments": {"command": "Get-ChildItem -Name"},
                           "thought": "List the workspace first."}, "delay": 0.6},
                {"reply": {"type": "final", "message": (
                    "Fake backend answer.\n\n| Item | Value |\n|---|---|\n| link | https://example.com/docs |\n\n"
                    "```powershell\nGet-ChildItem -Name\n```\n")}, "delay": 0.4},
            ],
        },
    ],
    "fallback": "{\"type\":\"final\",\"message\":\"(fake backend) no scripted reply left\"}",
}

_TURN_MARKERS = ("USER REQUEST:\n", "TASK:\n")


def load_script(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return DEMO_SCRIPT
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("scenarios", []), list):
        raise ValueError(f"{path}: fake script must be an object with a 'scenarios' list")
    return data


class FakeChatDriver:
    """Plays a script. ``prompts`` records every prompt received (tests read it)."""

    def __init__(self, script: dict[str, Any] | None = None, *, name: str = "main", log: list | None = None,
                 lock: threading.Lock | None = None) -> None:
        self.script = script if script is not None else DEMO_SCRIPT
        self.name = name
        self.prompts: list[str] = []
        self.log = log if log is not None else []   # shared with children: (driver name, prompt)
        self._lock = lock or threading.Lock()
        self._queue: list[Any] = []
        self.new_conversations = 0
        self.closed = False
        self.children: list[FakeChatDriver] = []

    # ---- driver interface
    def new_conversation(self) -> None:
        self.new_conversations += 1
        self._queue = []

    def close(self) -> None:
        self.closed = True

    def open_child(self) -> tuple["FakeChatDriver", bool]:
        child = FakeChatDriver(self.script, name=f"{self.name}/child{len(self.children) + 1}", log=self.log, lock=self._lock)
        self.children.append(child)
        return child, True

    def close_child(self, child: "FakeChatDriver") -> None:
        child.close()

    def send_message(self, prompt: str, timeout_seconds: int = 180,
                     status_callback: Callable[[str], None] | None = None,
                     cancel_check: Callable[[], bool] | None = None) -> str:
        with self._lock:
            self.prompts.append(prompt)
            self.log.append((self.name, prompt))
        self._maybe_start_turn(prompt)
        item = self._queue.pop(0) if self._queue else self.script.get("fallback", "")
        delay, error, reply = 0.0, None, item
        if isinstance(item, dict) and ("reply" in item or "delay" in item or "error" in item):
            delay = float(item.get("delay", 0) or 0)
            error = item.get("error")
            reply = item.get("reply", "")
        if status_callback:
            status_callback("Prompt submitted (fake backend), waiting for the scripted reply...")
        end = time.monotonic() + delay
        while time.monotonic() < end:
            if cancel_check and cancel_check():
                raise Cancelled("cancelled while waiting for the fake reply")
            if status_callback:
                status_callback("agent-neo is working...")
            time.sleep(min(0.1, max(0.0, end - time.monotonic())))
        if cancel_check and cancel_check():
            raise Cancelled("cancelled while waiting for the fake reply")
        if error:
            self._errored_prompt = prompt
            raise RuntimeError(str(error))
        self._errored_prompt = None
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        if status_callback:
            status_callback("Generation complete (fake backend).")
        return text

    # ---- script selection
    def _maybe_start_turn(self, prompt: str) -> None:
        if "You are the reasoning component" not in prompt:
            return  # a tool-result / retry prompt continues the current turn
        if "restarted after a sub-agent ran" in prompt:
            return  # the core re-sent the system prompt after a lost context: same turn
        if prompt == getattr(self, "_errored_prompt", None):
            return  # the core retried the same first prompt after a driver error
        self._turn_prompt = prompt
        for marker in _TURN_MARKERS:
            idx = prompt.rfind(marker)
            if idx < 0:
                continue
            request = prompt[idx + len(marker):]
            for scenario in self.script.get("scenarios", []):
                if re.search(scenario.get("match", "."), request):
                    self._queue = list(scenario.get("replies", []))
                    return
            self._queue = []
            return


FakeCopilotDriver = FakeChatDriver
