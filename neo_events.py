"""neo_events.py: the one event schema shared by the CLI renderer, the GUI and the session log.

Every event is a JSON-serialisable dict:
    {"v": 1, "type": <type>, "ts": <epoch ms>, "seq": <n>, "turn": <n or None>, ...fields}

Keys that start with "_" are in-process extras (e.g. a tool result's full text for the CLI);
``public()`` strips them, and only public events are logged or sent to the GUI.

Stdlib only, import-safe.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1

TURN_OUTCOMES = ("final", "cancelled", "ceiling", "error")
APPROVAL_KINDS = ("tool", "steps", "ceiling")

# type -> {field: type or tuple of types}. Fields not listed are allowed (optional extras).
_NUM = (int, float)
SCHEMA: dict[str, dict[str, Any]] = {
    "turn_start": {"text": str},
    "step_start": {"step": int, "limit": int, "ceiling": int},
    "status": {"text": str},
    "thought": {"text": str},
    "reply": {"text": str, "length": int},
    "tool_call": {"name": str, "args": dict, "summary": str},
    "tool_result": {"name": str, "ok": bool, "preview": str, "length": int},
    "subagent_start": {"sub_id": str, "task": str},
    "subagent_event": {"sub_id": str, "event": dict},
    "subagent_end": {"sub_id": str, "outcome": str, "message": str},
    "approval_request": {"id": str, "kind": str, "summary": str},
    "approval_result": {"id": str, "kind": str, "answer": (str, int, bool), "auto": bool},
    "final": {"message": str},
    "error": {"message": str, "fatal": bool},
    "turn_end": {"outcome": str},
}
EVENT_TYPES = tuple(SCHEMA)

PREVIEW_CHARS = 2000      # tool result preview kept in events / the log
ARG_CHARS = 2000          # each string argument of a tool call
REPLY_CHARS = 4000        # the model's raw reply kept in events


class Cancelled(Exception):
    """The user stopped the running turn (raised by the core, drivers and approvers)."""


def truncate(text: str, limit: int) -> str:
    text = text if isinstance(text, str) else str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated: {len(text)} chars in total]"


def truncate_args(args: Any, limit: int = ARG_CHARS) -> dict[str, Any]:
    """A copy of tool arguments safe to log/show: long strings are cut, non-dicts are wrapped."""
    if not isinstance(args, dict):
        return {"value": truncate(json.dumps(args, ensure_ascii=False, default=str), limit)}
    out: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str):
            out[str(key)] = truncate(value, limit)
        elif isinstance(value, (int, float, bool)) or value is None:
            out[str(key)] = value
        else:
            out[str(key)] = truncate(json.dumps(value, ensure_ascii=False, default=str), limit)
    return out


def public(event: dict[str, Any]) -> dict[str, Any]:
    """The event without in-process extras (keys starting with '_'), recursively for sub-agent events."""
    out = {k: v for k, v in event.items() if not k.startswith("_")}
    if out.get("type") == "subagent_event" and isinstance(out.get("event"), dict):
        out["event"] = public(out["event"])
    return out


def validate_event(event: Any) -> None:
    """Raise ValueError when an event does not match the schema."""
    if not isinstance(event, dict):
        raise ValueError(f"event must be a dict, got {type(event).__name__}")
    etype = event.get("type")
    if etype not in SCHEMA:
        raise ValueError(f"unknown event type {etype!r}")
    for key, want in (("v", int), ("ts", int), ("seq", int)):
        if not isinstance(event.get(key), want) or isinstance(event.get(key), bool):
            raise ValueError(f"{etype}: '{key}' must be {want.__name__}")
    if event.get("turn") is not None and (not isinstance(event["turn"], int) or isinstance(event["turn"], bool)):
        raise ValueError(f"{etype}: 'turn' must be int or None")
    for key, want in SCHEMA[etype].items():
        if key not in event:
            raise ValueError(f"{etype}: missing field '{key}'")
        value = event[key]
        wants = want if isinstance(want, tuple) else (want,)
        if want is int and isinstance(value, bool):
            raise ValueError(f"{etype}: '{key}' must be int")
        if not isinstance(value, wants):
            raise ValueError(f"{etype}: '{key}' must be {'/'.join(w.__name__ for w in wants)}")
    if etype in ("turn_end", "subagent_end") and event["outcome"] not in TURN_OUTCOMES:
        raise ValueError(f"{etype}: outcome must be one of {TURN_OUTCOMES}")
    if etype in ("approval_request", "approval_result") and event["kind"] not in APPROVAL_KINDS:
        raise ValueError(f"{etype}: kind must be one of {APPROVAL_KINDS}")
    if etype == "subagent_event":
        validate_event(event["event"])
    json.dumps(public(event))  # must be JSON-serialisable


class EventFactory:
    """Stamps events with schema version, time, a sequence number and the current turn."""

    def __init__(self) -> None:
        self._seq = 0
        self._lock = threading.Lock()
        self.turn: int | None = None

    def make(self, etype: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            seq = self._seq
        event = {"v": SCHEMA_VERSION, "type": etype, "ts": int(time.time() * 1000), "seq": seq, "turn": self.turn}
        event.update(fields)
        return event


Sink = Callable[[dict[str, Any]], None]


class SessionLog:
    """Appends public events to <home>/sessions/<timestamp>.jsonl (created on the first event)."""

    def __init__(self, home: Path, stamp: str | None = None) -> None:
        self.dir = Path(home) / "sessions"
        self.path = self.dir / f"{stamp or datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.jsonl"
        self._lock = threading.Lock()

    def write(self, event: dict[str, Any]) -> None:
        line = json.dumps(public(event), ensure_ascii=False)
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def read_session(path: Path) -> list[dict[str, Any]]:
    """Read a session log back (used by tests and as fixtures)."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def dedupe_status(sink: Sink) -> Sink:
    """Drop a status event whose text repeats the previous status (the poll loop's
    "agent-neo is working..." heartbeat), so logs and the GUI see each state once."""
    last: dict[str, Any] = {"key": None}

    def _sink(event: dict[str, Any]) -> None:
        inner, scope = event, None
        if event.get("type") == "subagent_event" and isinstance(event.get("event"), dict):
            inner, scope = event["event"], event.get("sub_id")
        if inner.get("type") == "status":
            key = (event.get("turn"), scope, inner.get("text"))
            if key == last["key"]:
                return
            last["key"] = key
        else:
            last["key"] = None
        sink(event)

    return _sink


def fanout(*sinks: Sink | None) -> Sink:
    """One sink that calls each given sink; a failing sink never stops the others or the agent."""
    real = [s for s in sinks if s is not None]

    def _sink(event: dict[str, Any]) -> None:
        for s in real:
            try:
                s(event)
            except Exception as exc:  # noqa: BLE001 - a renderer bug must not kill the agent loop
                try:
                    sys.stderr.write(f"[agent-neo] event sink failed: {type(exc).__name__}: {exc}\n")
                except Exception:  # noqa: BLE001 - pythonw has no stderr
                    pass

    return _sink
