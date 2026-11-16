"""neo_sessions.py: per-engine session index, auto-resume lookup, and history helpers.

Stores metadata in ``<home>/sessions/index.json`` alongside the append-only ``<id>.jsonl`` event
logs written by ``neo_events.SessionLog``. Stdlib only, import-safe.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from neo_events import read_session

INDEX_VERSION = 1
_BACKEND_ALIASES = {"web": "copilot"}


def normalize_backend(backend: str | None) -> str:
    b = (backend or "").strip().lower()
    return _BACKEND_ALIASES.get(b, b)


def _clean_line(text: str, limit: int = 120) -> str:
    collapsed = re.sub(r"\s+", " ", (text or "")).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def max_turn_number(events: list[dict[str, Any]]) -> int:
    top = 0
    for ev in events:
        t = ev.get("turn")
        if isinstance(t, int) and not isinstance(t, bool) and t > top:
            top = t
    return top


def build_fallback_summary(events: list[dict[str, Any]], max_chars: int = 1800) -> str:
    """Build a compact transcript digest from recorded events for fallback checkpoint seeding."""
    turns: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for ev in events:
        t = ev.get("turn")
        if not isinstance(t, int) or isinstance(t, bool):
            continue
        if t not in turns:
            turns[t] = {"user": "", "final": "", "tools": []}
            order.append(t)
        rec = turns[t]
        etype = ev.get("type")
        if etype == "turn_start" and not rec["user"]:
            rec["user"] = _clean_line(str(ev.get("text", "")), 220)
        elif etype == "tool_call":
            tname = str(ev.get("name", "")).strip()
            if tname and tname not in rec["tools"]:
                rec["tools"].append(tname)
        elif etype == "final":
            rec["final"] = _clean_line(str(ev.get("message", "")), 360)

    if not order:
        return ""

    # Prefer the most recent turns so the checkpoint fits comfortably inside max_chars.
    lines_rev: list[str] = []
    used = 0
    for t in reversed(order):
        rec = turns[t]
        parts = [f"Turn {t}:"]
        if rec["user"]:
            parts.append(f"User asked: {rec['user']}")
        if rec["tools"]:
            parts.append(f"Tools used: {', '.join(rec['tools'][:6])}")
        if rec["final"]:
            parts.append(f"Assistant answered: {rec['final']}")
        block = " | ".join(parts)
        if used + len(block) + 1 > max_chars and lines_rev:
            break
        lines_rev.append(block)
        used += len(block) + 1
    lines_rev.reverse()
    return "\n".join(lines_rev)


def _derive_record_from_jsonl(path: Path) -> dict[str, Any] | None:
    """Best-effort metadata reconstruction from a standalone .jsonl session log."""
    try:
        events = read_session(path)
    except Exception:  # noqa: BLE001
        return None
    if not events:
        return None
    first_ts = events[0].get("ts")
    last_ts = events[-1].get("ts")
    created_at = int(first_ts) if isinstance(first_ts, int) else int(path.stat().st_mtime * 1000)
    updated_at = int(last_ts) if isinstance(last_ts, int) else created_at
    title = ""
    workspace = ""
    turn_count = 0
    for ev in events:
        if ev.get("type") == "turn_start":
            turn_count += 1
            if not title:
                title = _clean_line(str(ev.get("text", "")), 120)
            if not workspace and isinstance(ev.get("workspace"), str):
                workspace = ev["workspace"]
    if turn_count == 0 and not title:
        return None
    return {
        "id": path.stem,
        "backend": "unknown",
        "workspace": workspace,
        "title": title or path.stem,
        "created_at": created_at,
        "updated_at": updated_at,
        "turn_count": turn_count,
        "chat_url": "",
        "summary": build_fallback_summary(events, max_chars=900),
    }


class SessionStore:
    """Thread-safe index of Agent Neo sessions stored in ``<home>/sessions/index.json``."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.sessions_dir = self.home / "sessions"
        self.index_path = self.sessions_dir / "index.json"
        self._lock = threading.RLock()

    def make_session_id(self) -> str:
        with self._lock:
            base = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            cand = base
            n = 1
            data = self._read_index_unlocked()
            existing = data.get("sessions", {})
            while cand in existing or (self.sessions_dir / f"{cand}.jsonl").exists():
                cand = f"{base}-{n}"
                n += 1
            return cand

    def _read_index_unlocked(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return {"version": INDEX_VERSION, "sessions": {}}
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"version": INDEX_VERSION, "sessions": {}}
        if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), dict):
            return {"version": INDEX_VERSION, "sessions": {}}
        return {"version": INDEX_VERSION, "sessions": dict(raw["sessions"])}

    def _write_index_unlocked(self, data: dict[str, Any]) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.sessions_dir / f"index.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.index_path)

    def upsert(
        self,
        session_id: str,
        *,
        backend: str | None = None,
        workspace: str | Path | None = None,
        title: str | None = None,
        turn_count: int | None = None,
        chat_url: str | None = None,
        summary: str | None = None,
        created_at: int | None = None,
        updated_at: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time() * 1000)
        with self._lock:
            data = self._read_index_unlocked()
            sessions = data["sessions"]
            rec = dict(sessions.get(session_id) or {})
            rec.setdefault("id", session_id)
            rec.setdefault("backend", "copilot")
            rec.setdefault("workspace", "")
            rec.setdefault("title", "")
            rec.setdefault("created_at", created_at if created_at is not None else now)
            rec.setdefault("updated_at", updated_at if updated_at is not None else now)
            rec.setdefault("turn_count", 0)
            rec.setdefault("chat_url", "")
            rec.setdefault("summary", "")

            if backend is not None and str(backend).strip():
                rec["backend"] = normalize_backend(str(backend))
            if workspace is not None:
                rec["workspace"] = str(workspace)
            if title is not None and str(title).strip():
                rec["title"] = _clean_line(str(title), 120)
            if turn_count is not None:
                rec["turn_count"] = max(int(turn_count), int(rec.get("turn_count", 0)))
            if chat_url is not None and str(chat_url).strip():
                rec["chat_url"] = str(chat_url).strip()
            if summary is not None and str(summary).strip():
                rec["summary"] = str(summary).strip()[:1800]
            rec["updated_at"] = updated_at if updated_at is not None else now

            sessions[session_id] = rec
            self._write_index_unlocked(data)
            return dict(rec)

    def record_turn_start(self, session_id: str, *, backend: str, workspace: str | Path,
                          user_text: str, turn: int | None = None) -> dict[str, Any]:
        with self._lock:
            existing = self._read_index_unlocked()["sessions"].get(session_id, {})
            title = existing.get("title") or _clean_line(user_text, 120)
            next_count = max(int(existing.get("turn_count", 0)) + 1, int(turn or 0))
            return self.upsert(
                session_id,
                backend=backend,
                workspace=str(workspace),
                title=title,
                turn_count=next_count,
            )

    def record_turn_end(self, session_id: str, *, backend: str, workspace: str | Path,
                        chat_url: str | None = None, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        summary = build_fallback_summary(events) if events is not None else None
        return self.upsert(
            session_id,
            backend=backend,
            workspace=str(workspace),
            chat_url=chat_url if chat_url else None,
            summary=summary,
        )

    def _all_records_unlocked(self, include_unindexed: bool = True) -> list[dict[str, Any]]:
        data = self._read_index_unlocked()
        sessions = dict(data["sessions"])
        records: dict[str, dict[str, Any]] = {}
        for sid, rec in sessions.items():
            if not isinstance(rec, dict):
                continue
            item = dict(rec)
            item.setdefault("id", sid)
            if (self.sessions_dir / f"{sid}.jsonl").is_file() or int(item.get("turn_count", 0)) > 0:
                records[sid] = item

        if include_unindexed and self.sessions_dir.is_dir():
            for path in self.sessions_dir.glob("*.jsonl"):
                sid = path.stem
                if sid in records:
                    continue
                derived = _derive_record_from_jsonl(path)
                if derived is not None:
                    records[sid] = derived

        ordered = sorted(
            records.values(),
            key=lambda r: (int(r.get("updated_at", 0)), str(r.get("id", ""))),
            reverse=True,
        )
        return ordered

    def list_sessions(self, backend: str | None = None, limit: int = 50,
                      include_empty: bool = False) -> list[dict[str, Any]]:
        norm = normalize_backend(backend) if backend else ""
        with self._lock:
            items = self._all_records_unlocked(include_unindexed=True)
        out: list[dict[str, Any]] = []
        for rec in items:
            if not include_empty and int(rec.get("turn_count", 0)) <= 0 and not rec.get("chat_url"):
                continue
            if norm and normalize_backend(rec.get("backend")) != norm:
                continue
            out.append(dict(rec))
            if limit > 0 and len(out) >= limit:
                break
        return out

    def latest_for_backend(self, backend: str) -> dict[str, Any] | None:
        items = self.list_sessions(backend=backend, limit=1, include_empty=False)
        return items[0] if items else None

    def latest_by_backend(self, backends: tuple[str, ...] = ("copilot", "gemini", "deepseek", "desktop", "custom")) -> dict[str, dict[str, Any] | None]:
        out: dict[str, dict[str, Any] | None] = {}
        for b in backends:
            out[normalize_backend(b)] = self.latest_for_backend(b)
        return out

    def get_session(self, session_id_or_prefix: str) -> dict[str, Any] | None:
        key = (session_id_or_prefix or "").strip()
        if not key:
            return None
        with self._lock:
            items = self._all_records_unlocked(include_unindexed=True)
        for rec in items:
            if rec.get("id") == key:
                return dict(rec)
        matches = [dict(rec) for rec in items if str(rec.get("id", "")).startswith(key)]
        if len(matches) == 1:
            return matches[0]
        return None

    def load_events(self, session_id: str) -> list[dict[str, Any]]:
        path = self.sessions_dir / f"{session_id}.jsonl"
        if not path.is_file():
            return []
        return read_session(path)
