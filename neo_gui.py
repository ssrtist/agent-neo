#!/usr/bin/env python3
"""neo_gui.py: the desktop GUI of Agent Neo (agent-neo).

A pytauri-wheel window (Tauri + WebView2, Python only) over the same agent core as the CLI.
The frontend is plain HTML/CSS/ES modules in gui/; the Tauri config is in tauri/.

    pythonw neo_gui.py [--workspace DIR] [--backend B] [--browser-mode M]
    run-gui.cmd [same options]

One worker thread owns the chat driver and runs every agent job (Playwright's sync objects
are bound to the thread that created them); the UI never blocks. Core events reach the page
through the pytauri emitter on the "neo:event" channel; GUI-only messages use the same
channel with types gui_state / gui_notice.

Test hooks (never needed in normal use):
    --fake-backend [SCRIPT.json] or NEO_FAKE_SCRIPT         scripted fake driver, no browser
    NEO_TEST_NO_OPEN=1                                      open_external records instead of opening
    NEO_HOME / WEBVIEW2_USER_DATA_FOLDER                    isolated settings, logs and WebView2 profile
"""
# NOTE: no `from __future__ import annotations` here: pytauri resolves the command annotations with
# inspect.signature(eval_str=True), which cannot see the Body model defined inside main().

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from neo_core import Agent, discover_skills
from neo_events import Cancelled, SessionLog, dedupe_status, fanout, public
from neo_settings import (
    BACKEND_ALIASES,
    BACKENDS,
    BROWSER_MODES,
    Settings,
    SettingsError,
    custom_options,
    home_settings_path,
    load_settings,
    neo_home,
    workspace_settings_path,
)
from neo_version import __version__, version_string

REPO = Path(__file__).resolve().parent
GUI_DIR = REPO / "gui"
TAURI_DIR = REPO / "tauri"
EVENT_CHANNEL = "neo:event"
WEB_BACKENDS = ("copilot", "web", "gemini", "whatsapp", "auto", "custom")

# open_external: workspace files of these (document-like) types open with their default app directly; every other
# type asks first (code review r1: a denylist missed .chm/.msc/... that run code when opened). Reveal never asks.
OPEN_DIRECT_EXTS = {
    ".txt", ".md", ".markdown", ".log", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".xml", ".diff", ".patch", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".html", ".htm",
}
SAFE_SCHEMES = {"http", "https", "mailto"}


def _is_unc(text: str) -> bool:
    """UNC / device paths (\\\\host\\share, //host/share, \\\\?\\UNC\\...): touching them can send NTLM credentials."""
    t = text.replace("/", "\\")
    return t.startswith("\\\\")


def _test_no_open() -> bool:
    return os.environ.get("NEO_TEST_NO_OPEN") == "1" or os.environ.get("AGENT_NEO_TEST_NO_OPEN") == "1"


def _log_error(text: str) -> None:
    try:
        home = neo_home()
        home.mkdir(parents=True, exist_ok=True)
        with (home / "gui-errors.log").open("a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
    except Exception:  # noqa: BLE001
        pass


class PendingApproval:
    def __init__(self, request: dict[str, Any]) -> None:
        self.request = request
        self.answer: Any = None
        self.done = threading.Event()


class GuiSession:
    """Everything the window talks to. Thread-safe; agent work runs on one worker thread."""

    def __init__(self, workspace: Path, cli_overrides: dict[str, Any], fake_script: str | None,
                 emit: Callable[[dict[str, Any]], None]) -> None:
        self.emit_raw = emit
        self.lock = threading.RLock()
        self.jobs: queue.Queue = queue.Queue()
        self.cli_overrides = cli_overrides
        self.fake_script = fake_script
        self.workspace = workspace.resolve()
        self.settings = Settings()
        self.settings_error = ""
        self.settings_warnings: list[str] = []
        self.backend = "copilot"
        self.browser_mode = "offscreen"
        self.yolo = False
        self.step_chunk = 10
        self.show_thinking = False
        self.running = False
        self.busy = ""                    # a non-turn job in progress (fresh, backend switch)
        self.queued_prompt: str | None = None
        self.wakeup_queue: list[str] = []
        self.driver: Any = None
        self.agent: Agent | None = None
        self.pending: dict[str, PendingApproval] = {}
        self.opened: list[dict[str, Any]] = []   # NEO_TEST_NO_OPEN record
        self._shut_down = False
        self.log = SessionLog(neo_home())
        self._sink = dedupe_status(fanout(self._to_page, self.log.write))
        self._load_settings(initial=True)
        self.thread = threading.Thread(target=self._worker, name="neo-worker", daemon=True)
        self.thread.start()

    # ---------------------------------------------------------------- plumbing
    def _to_page(self, event: dict[str, Any]) -> None:
        self.emit_raw(public(event))

    def notice(self, text: str, level: str = "info") -> None:
        self.emit_raw({"type": "gui_notice", "text": text, "level": level})

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {
                "type": "gui_state",
                "version": __version__,
                "title": version_string(),
                "workspace": str(self.workspace),
                "backend": self.backend,
                "browser_mode": self.browser_mode,
                "yolo": self.yolo,
                "step_chunk": self.step_chunk,
                "hard_ceiling": self.settings.hard_ceiling,
                "show_thinking": self.show_thinking,
                "enable_subagents": self.settings.enable_subagents,
                "running": self.running,
                "busy": self.busy,
                "fake": self.fake_script is not None,
                "settings_error": self.settings_error,
                "settings_warnings": list(self.settings_warnings),
                "settings_files": [str(home_settings_path()), str(workspace_settings_path(self.workspace))],
                "pending": [p.request for p in self.pending.values()],
                "backends": list(BACKENDS),
                "browser_modes": list(BROWSER_MODES),
            }

    def push_state(self) -> None:
        self.emit_raw(self.state())

    def _load_settings(self, initial: bool = False, workspace: Path | None = None, keep_session: bool = False) -> bool:
        """Load neo.toml for the workspace. keep_session (a workspace switch) keeps the session toggles yolo and
        steps, as the CLI's /workspace does, so switching into a repo whose toml says auto_approve never turns it on."""
        ws = workspace or self.workspace
        try:
            settings, warnings = load_settings(ws, self.cli_overrides)
        except SettingsError as exc:
            with self.lock:
                self.settings_error = str(exc)
            if not initial:
                self.notice(f"Settings error: {exc}", "error")
            return False
        backend_kept = ""
        with self.lock:
            self.settings = settings
            self.settings_error = ""
            self.settings_warnings = warnings
            if self.driver is None:
                self.backend = settings.backend
            elif settings.backend != self.backend:
                # The header must name the backend that turns really go to; a driver is only swapped by /backend.
                backend_kept = settings.backend
            self.browser_mode = settings.browser_mode
            if not keep_session:
                self.yolo = settings.auto_approve
                self.step_chunk = settings.step_chunk
            self.show_thinking = settings.show_thinking
            if self.agent is not None:
                self.agent.set_settings(settings)
                self.agent.auto_approve, self.agent.step_chunk = self.yolo, self.step_chunk
        for w in warnings:
            if not initial:
                if keep_session and "auto_approve = true set by the WORKSPACE" in w:
                    w = (f"{w.split(': auto_approve')[0]}: sets auto_approve = true; NOT applied on a workspace switch "
                         f"(yolo stays {'on' if self.yolo else 'off'}).")
                self.notice(w, "warn")
        if backend_kept:
            self.notice(f"Settings say backend = {backend_kept}; this session stays on {self.backend} (the active backend). "
                        f"Use /backend {backend_kept} to switch.", "warn")
        return True

    # ---------------------------------------------------------------- approvals (worker thread blocks here)
    def approver(self, request: dict[str, Any]) -> Any:
        p = PendingApproval(request)
        rid = request["id"]
        with self.lock:
            self.pending[rid] = p
        try:
            while not p.done.wait(0.1):
                if self.agent is not None and self.agent.cancel.is_set():
                    raise Cancelled("cancelled while an approval was pending")
            return p.answer
        finally:
            with self.lock:
                self.pending.pop(rid, None)

    def approve(self, rid: str, answer: Any) -> bool:
        with self.lock:
            p = self.pending.get(rid)
        if p is None:
            return False
        kind = p.request.get("kind")
        if kind == "steps":
            try:
                p.answer = max(0, int(answer))
            except (TypeError, ValueError):
                p.answer = 0
        elif kind == "question":
            if answer is False or answer is None:
                p.answer = False
            else:
                p.answer = str(answer).strip()
        else:
            p.answer = bool(answer)
        p.done.set()
        return True

    # ---------------------------------------------------------------- commands (called from the IPC thread)
    def send(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        if text.startswith("/"):
            return self.slash(text)
        with self.lock:
            if self.running or self.busy:
                self.queued_prompt = text
                self.notice(f"Prompt queued for next turn: {text[:60]}{'…' if len(text) > 60 else ''}")
                return {"ok": True, "queued": True}
            if self.settings_error:
                return {"ok": False, "error": "settings", "message": self.settings_error}
            self.running = True
        self.jobs.put(("turn", text))
        self.push_state()
        return {"ok": True}

    def steer(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        with self.lock:
            if not self.running:
                return self.send(f"[Steering]: {text}")
            agent = self.agent
        if agent is not None:
            agent.pending_steering = text
            agent.cancel.set()
        self.queued_prompt = f"[Steering]: {text}"
        self.notice(f"Steering intervention received: {text[:60]}{'…' if len(text) > 60 else ''}")
        return {"ok": True, "steered": True}

    def cancel(self) -> dict[str, Any]:
        with self.lock:
            agent = self.agent
            running = self.running
        if agent is not None and running:
            agent.cancel.set()
        return {"ok": True, "running": running}

    def queue_job(self, job: tuple, busy: str) -> dict[str, Any]:
        with self.lock:
            if self.running or self.busy:
                return {"ok": False, "error": "busy"}
            self.busy = busy
        self.jobs.put(job)
        self.push_state()
        return {"ok": True}

    def browse_workspace(self) -> dict[str, Any]:
        """Open a native folder picker dialog and set the workspace."""
        selected = None

        if sys.platform == "darwin":
            try:
                script = (
                    f'POSIX path of (choose folder with prompt "Select Agent Neo Workspace Folder" '
                    f'default location POSIX file {json.dumps(str(self.workspace))})'
                )
                res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
                if res.returncode == 0 and res.stdout.strip():
                    selected = res.stdout.strip()
            except Exception:
                pass
        else:
            import tkinter as tk
            from tkinter import filedialog

            def _pick():
                nonlocal selected
                try:
                    root = tk.Tk()
                    root.withdraw()
                    root.attributes("-topmost", True)
                    selected = filedialog.askdirectory(
                        initialdir=str(self.workspace),
                        title="Select Agent Neo Workspace Folder",
                    )
                    root.destroy()
                except Exception:
                    pass

            t = threading.Thread(target=_pick)
            t.start()
            t.join(timeout=120)

        if selected and os.path.isdir(selected):
            return self.set_workspace(selected)
        return {"ok": False, "cancelled": True}

    def set_workspace(self, path: str) -> dict[str, Any]:
        target = Path(os.path.expandvars(path.strip().strip('"').strip("'"))).expanduser()
        if not target.is_absolute():
            target = self.workspace / target
        target = target.resolve()
        if not target.is_dir():
            self.notice(f"Folder does not exist: {target}", "error")
            return {"ok": False, "error": "not a folder"}
        with self.lock:
            if self.running or self.busy:
                return {"ok": False, "error": "busy"}
        if not self._load_settings(workspace=target, keep_session=True):
            return {"ok": False, "error": "settings"}
        with self.lock:
            self.workspace = target
            self.wakeup_queue.clear()
            if self.agent is not None:
                self.agent.close()
                self.agent.set_workspace(target)
        n = len(self.agent.skills) if self.agent else len(discover_skills(target))
        self.notice(f"Workspace changed to: {target} ({n} skills)")
        self.push_state()
        return {"ok": True}

    def set_backend(self, backend: str) -> dict[str, Any]:
        norm = BACKEND_ALIASES.get((backend or "").strip().lower(), backend)
        if norm not in BACKENDS:
            return {"ok": False, "error": f"unknown backend {backend!r}"}
        return self.queue_job(("backend", norm), "switching backend")

    def set_option(self, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if "show_thinking" in body:
                self.show_thinking = bool(body["show_thinking"])
            if "yolo" in body:
                was = self.yolo
                self.yolo = bool(body["yolo"])
                if self.agent is not None:
                    self.agent.auto_approve = self.yolo
                yolo_on = self.yolo and not was
            else:
                yolo_on = False
            if "step_chunk" in body:
                try:
                    n = int(body["step_chunk"])
                except (TypeError, ValueError):
                    n = 0
                if n > 0:
                    self.step_chunk = n
                    if self.agent is not None:
                        self.agent.step_chunk = n
            if "browser_mode" in body and body["browser_mode"] in BROWSER_MODES:
                self.browser_mode = body["browser_mode"]
        if yolo_on:
            self.notice("Auto-approve (yolo) is ON: tool calls now run without asking.")
        self.push_state()
        return {"ok": True}

    def reload_settings(self) -> dict[str, Any]:
        with self.lock:
            if self.running or self.busy:
                return {"ok": False, "error": "busy"}
        old_mode = self.browser_mode
        ok = self._load_settings()
        if ok:
            self.notice("Settings reloaded." + (" Browser mode changed: it applies to the next fresh browser (/fresh or "
                                                "a backend switch)." if old_mode != self.browser_mode else ""))
        self.push_state()
        return {"ok": ok}

    def open_settings(self) -> dict[str, Any]:
        path = home_settings_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / "neo.example.toml", path)
            self.notice(f"Created {path} from neo.example.toml.")
        return self._open_path(path, reveal=False, record_kind="settings")

    def open_external(self, target: str, kind: str = "", reveal: bool = False, confirmed: bool = False) -> dict[str, Any]:
        """Open a link outside the app, behind a strict allowlist (plan v0.4 D7, section 5 Links)."""
        target = (target or "").strip()
        if not target:
            return {"ok": False, "error": "empty"}
        scheme = urlparse(target).scheme.lower()
        if kind == "url" or (scheme in SAFE_SCHEMES and kind != "file"):
            if scheme not in SAFE_SCHEMES:
                self.notice(f"Not opened: only http, https and mailto links open from agent-neo ({target[:120]}).", "warn")
                return {"ok": False, "error": "scheme"}
            return self._launch(
                {"kind": "url", "target": target},
                lambda: os.startfile(target) if hasattr(os, "startfile") else subprocess.Popen(["open", target]),  # noqa: S606
            )
        # a file path: file:///C:/..., C:\..., or relative to the workspace
        path_text = target
        if scheme == "file":
            parsed = urlparse(target)
            if parsed.netloc and parsed.netloc.lower() != "localhost":
                self.notice("Not opened: network (UNC) file links do not open from agent-neo.", "warn")
                return {"ok": False, "error": "unc"}
            raw_path = unquote(parsed.path)
            path_text = raw_path.lstrip("/").replace("/", "\\") if os.name == "nt" else raw_path
        elif scheme and len(scheme) > 1:
            self.notice(f"Not opened: unsupported link type '{scheme}:'.", "warn")
            return {"ok": False, "error": "scheme"}
        if _is_unc(path_text):
            # checked lexically, before resolve()/exists() can reach the network
            self.notice("Not opened: network (UNC) paths do not open from agent-neo.", "warn")
            return {"ok": False, "error": "unc"}
        p = Path(path_text)
        if not p.is_absolute():
            p = self.workspace / p
        try:
            p = p.resolve()
        except OSError:
            return {"ok": False, "error": "bad path"}
        if _is_unc(str(p)):   # e.g. a junction or mapped path that resolves to a share
            self.notice("Not opened: network (UNC) paths do not open from agent-neo.", "warn")
            return {"ok": False, "error": "unc"}
        if not p.exists():
            self.notice(f"Not found: {p}", "warn")
            return {"ok": False, "error": "missing"}
        inside = p == self.workspace or self.workspace in p.parents
        risky = p.is_file() and p.suffix.lower() not in OPEN_DIRECT_EXTS and not reveal
        if (not inside or risky) and not confirmed:
            why = "outside the workspace" if not inside else f"a '{p.suffix or 'no extension'}' file, which may run code when opened"
            return {"ok": False, "confirm": True, "path": str(p), "reveal": reveal,
                    "message": f"{p}\n\nThis is {why}. Open it anyway?"}
        return self._open_path(p, reveal=reveal)

    def _open_path(self, p: Path, reveal: bool, record_kind: str = "file") -> dict[str, Any]:
        if reveal:
            return self._launch(
                {"kind": record_kind, "target": str(p), "reveal": True},
                lambda: subprocess.Popen(["explorer.exe", f"/select,{p}"])
                if os.name == "nt"
                else subprocess.Popen(["open", "-R", str(p)]),
            )
        return self._launch(
            {"kind": record_kind, "target": str(p), "reveal": False},
            lambda: os.startfile(str(p)) if hasattr(os, "startfile") else subprocess.Popen(["open", str(p)]),  # noqa: S606
        )

    def _launch(self, record: dict[str, Any], action: Callable[[], Any]) -> dict[str, Any]:
        if _test_no_open():
            with self.lock:
                self.opened.append(record)
            return {"ok": True, "test": True, **record}
        try:
            action()
        except OSError as exc:
            self.notice(f"Could not open {record['target']}: {exc}", "error")
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **record}

    def slash(self, text: str) -> dict[str, Any]:
        parts = text.split(maxsplit=1)
        cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
        if cmd == "/new":
            return self.queue_job(("new_chat",), "starting a new chat")
        if cmd == "/fresh":
            return self.queue_job(("fresh",), "recycling the browser")
        if cmd in ("/workspace", "/cd"):
            if not arg:
                self.notice(f"Current workspace: {self.workspace}  (usage: /workspace <path>)")
                return {"ok": True}
            return self.set_workspace(arg)
        if cmd == "/backend":
            norm = BACKEND_ALIASES.get(arg.lower(), arg.lower())
            if norm not in BACKENDS:
                self.notice("Usage: /backend <copilot|gemini|whatsapp|desktop|auto|custom>", "warn")
                return {"ok": False}
            return self.set_backend(norm)
        if cmd == "/steps":
            if arg.isdigit() and int(arg) > 0:
                self.set_option({"step_chunk": int(arg)})
                self.notice(f"Step chunk updated to {self.step_chunk} steps (ceiling: {self.settings.hard_ceiling}).")
            else:
                self.notice(f"Current limits: safety chunk = {self.step_chunk} steps, ceiling = "
                            f"{self.settings.hard_ceiling} steps. Usage: /steps <num>")
            return {"ok": True}
        if cmd == "/yolo":
            self.set_option({"yolo": not self.yolo})
            self.notice("[yolo] YOLO mode " + ("ENABLED (auto-approving all tools)" if self.yolo else "DISABLED (manual approval)"),
                        "warn" if self.yolo else "info")
            return {"ok": True}
        if cmd == "/skills":
            skills = self.agent.skills if self.agent else discover_skills(self.workspace)
            self.notice(f"Skills ({len(skills)}): " + (", ".join(skills) or "none"))
            return {"ok": True}
        if cmd == "/tasks":
            if self.agent is None:
                self.notice("No active tasks or watches.")
                return {"ok": True}
            info = self.agent.task_manager.list_tasks()
            t_lines = [f"{t['task_id']} [{t['status']}] pid={t['pid']}: {t['command']}" for t in info["tasks"]]
            w_lines = [f"{w['watch_id']} -> {w['task_id'] or f'timer {w['delay_seconds']}s'}: {w['prompt']}" for w in info["watches"]]
            summary = "Tasks: " + ("; ".join(t_lines) or "none") + " | Watches: " + ("; ".join(w_lines) or "none")
            self.notice(summary)
            return {"ok": True}
        if cmd == "/watch":
            wparts = arg.split(maxsplit=1)
            if self.agent is None:
                self.notice("Start a turn first before watching a task.", "warn")
                return {"ok": False}
            if len(wparts) < 2 or not wparts[1].strip():
                self.notice("Usage: /watch <task_id|Ns> <follow-up prompt>", "warn")
                return {"ok": False}
            target_spec, wprompt = wparts[0].strip(), wparts[1].strip()
            if target_spec.endswith("s") and target_spec[:-1].replace(".", "", 1).isdigit():
                res = self.agent.task_manager.watch_task(prompt=wprompt, delay_seconds=float(target_spec[:-1]))
            else:
                res = self.agent.task_manager.watch_task(prompt=wprompt, task_id=target_spec)
            if "error" in res:
                self.notice(res["error"], "warn")
                return {"ok": False, **res}
            self.notice(f"Watching ({res['watch_id']}): will resume when {target_spec} triggers.")
            return {"ok": True, **res}
        if cmd == "/unwatch":
            with self.lock:
                self.wakeup_queue.clear()
            if self.agent is None:
                self.notice("No active watches.")
                return {"ok": True}
            if arg.startswith("task_"):
                res = self.agent.task_manager.unwatch_task(task_id=arg)
            elif arg:
                res = self.agent.task_manager.unwatch_task(watch_id=arg)
            else:
                res = self.agent.task_manager.unwatch_task()
            if "error" in res:
                self.notice(res["error"], "warn")
                return {"ok": False, **res}
            self.notice(f"Cancelled watches: {', '.join(res.get('cancelled', [])) or 'none'}")
            return {"ok": True, **res}
        self.notice("Commands: /new, /fresh, /workspace [path], /backend <copilot|gemini|whatsapp|desktop|auto|custom>, /steps [N], /yolo, /skills, /tasks, /watch, /unwatch",
                    "info" if cmd == "/help" else "warn")
        return {"ok": cmd == "/help"}

    # ---------------------------------------------------------------- worker thread
    def _notify_driver(self, text: str) -> None:
        self.notice(text, "warn")
        if self.agent is not None and self.running:
            self.agent.emit("status", text=text, level="warn")

    def _make_driver(self, backend: str, fresh: bool = True) -> Any:
        if self.fake_script is not None:
            from neo_fake import FakeChatDriver, load_script
            return FakeChatDriver(load_script(self.fake_script or None))
        from chat_driver import get_driver
        drv = get_driver(backend, new_session_only=not fresh, port=self.settings.cdp_port,
                         browser_mode=self.browser_mode, notify=self._notify_driver,
                         profile_dir=self.settings.browser_profile_path or None,
                         phone=getattr(self.settings, "whatsapp_phone", None),
                         contact=getattr(self.settings, "whatsapp_contact", None),
                         custom=custom_options(self.settings))
        if type(drv).__name__ == "DesktopCopilotDriver":
            self.notice("Desktop backend: it takes the keyboard focus and the clipboard while it works. "
                        "Do not type elsewhere during a turn.", "warn")
        return drv

    def _on_wakeup(self, payload: dict[str, Any]) -> None:
        prompt = payload.get("prompt", "")
        if not prompt:
            return
        wid = payload.get("watch_id", "")
        reason = payload.get("reason", "watch triggered")
        queued_only = False
        with self.lock:
            if self._shut_down:
                return
            if self.running or self.busy:
                self.wakeup_queue.append(prompt)
                queued_only = True
            else:
                self.running = True
                self.jobs.put(("turn", prompt))
        if queued_only:
            self.notice(f"Wakeup queued ({wid}: {reason})")
        else:
            self.notice(f"Reactive wakeup ({wid}): {reason} — resuming...")
            self.push_state()

    def _ensure_agent(self) -> Agent:
        if self.driver is None:
            self.driver = self._make_driver(self.backend)
        if self.agent is None:
            self.agent = Agent(self.driver, self.workspace, self.settings, self._sink, self.approver,
                               on_wakeup=self._on_wakeup)
        self.agent.driver = self.driver
        self.agent.auto_approve, self.agent.step_chunk = self.yolo, self.step_chunk
        return self.agent

    def _close_driver(self) -> None:
        if self.driver is not None:
            try:
                self.driver.close()
            except Exception:  # noqa: BLE001
                _log_error(traceback.format_exc())
            self.driver = None

    def _worker(self) -> None:
        while True:
            job = self.jobs.get()
            kind = job[0]
            try:
                if kind == "shutdown":
                    if self.agent is not None:
                        self.agent.close()
                    self._close_driver()
                    return
                if kind == "turn":
                    try:
                        agent = self._ensure_agent()
                    except Exception as exc:  # noqa: BLE001
                        self.notice(f"Could not start the {self.backend} backend: {type(exc).__name__}: {exc}", "error")
                        continue
                    agent.run_turn(job[1])
                elif kind == "new_chat":
                    with self.lock:
                        self.wakeup_queue.clear()
                    if self.agent is not None:
                        self.agent.close()
                    if self.driver is not None:
                        self.driver.new_conversation()
                    self.notice("Started a new conversation topic.")
                elif kind == "fresh":
                    with self.lock:
                        self.wakeup_queue.clear()
                    if self.agent is not None:
                        self.agent.close()
                    if self.backend in WEB_BACKENDS and self.fake_script is None:
                        self._close_driver()
                        self.driver = self._make_driver(self.backend, fresh=True)
                        self.notice(f"Fresh {type(self.driver).__name__} ready (the browser starts with the next prompt).")
                    else:
                        if self.driver is not None:
                            self.driver.new_conversation()
                        self.notice("Started fresh conversation.")
                elif kind == "backend":
                    with self.lock:
                        self.wakeup_queue.clear()
                    if self.agent is not None:
                        self.agent.task_manager.clear_watches()
                    new = self._make_driver(job[1])
                    self._close_driver()
                    self.driver = new
                    with self.lock:
                        self.backend = job[1]
                    self.notice(f"Switched backend to {type(new).__name__}.")
            except Exception as exc:  # noqa: BLE001 - a failed job must not kill the worker
                _log_error(traceback.format_exc())
                self.notice(f"{kind} failed: {type(exc).__name__}: {exc}", "error")
            finally:
                with self.lock:
                    if kind == "turn":
                        self.running = False
                        next_p = None
                        if self.queued_prompt:
                            next_p = self.queued_prompt
                            self.queued_prompt = None
                        elif self.wakeup_queue:
                            next_p = self.wakeup_queue.pop(0)
                        if next_p:
                            self.running = True
                            self.jobs.put(("turn", next_p))
                    else:
                        self.busy = ""
                if kind != "shutdown":
                    self.push_state()

    def shutdown(self, timeout: float = 20.0) -> None:
        with self.lock:
            if self._shut_down:
                return
            self._shut_down = True
        if self.agent is not None:
            self.agent.cancel.set()
        for p in list(self.pending.values()):
            p.done.set()
        self.jobs.put(("shutdown",))
        self.thread.join(timeout)
        if self.thread.is_alive():
            # The worker is stuck (e.g. Edge start-up or sign-in). Playwright objects belong to that thread, so do not
            # touch them; end the Edge this driver launched by its dedicated profile instead (code review r1).
            drv = self.driver
            if drv is not None and getattr(drv, "_launched", False) and getattr(drv, "browser_mode", "") in ("offscreen", "headless") \
                    and hasattr(drv, "end_own_edge"):
                try:
                    drv.end_own_edge()      # agent-neo's own Edge profile only (neo_edge)
                except Exception:  # noqa: BLE001
                    pass


# ==================================================================== pytauri wiring


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="neo_gui.py", description=f"{version_string()}: desktop GUI")
    p.add_argument("--version", action="version", version=version_string())
    p.add_argument("-w", "--workspace", default=os.environ.get("NEO_ROOT") or os.environ.get("AGENT_NEO_ROOT") or os.getcwd())
    p.add_argument("--backend", choices=[*BACKENDS, *BACKEND_ALIASES.keys()], default=None)
    p.add_argument("--browser-mode", choices=list(BROWSER_MODES), default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--custom-url", default=None, metavar="URL")
    p.add_argument("--fake-backend", nargs="?", const="", default=None, help=argparse.SUPPRESS)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fake = (
        args.fake_backend
        if args.fake_backend is not None
        else (os.environ.get("NEO_FAKE_SCRIPT") or os.environ.get("AGENT_NEO_FAKE_SCRIPT"))
    )
    overrides = {"backend": args.backend, "browser_mode": args.browser_mode, "cdp_port": args.port, "custom_url": args.custom_url}

    from anyio.from_thread import start_blocking_portal
    from pydantic import RootModel
    from pytauri import AppHandle, Commands
    from pytauri.ffi import Emitter
    from pytauri_wheel.lib import builder_factory, context_factory

    class Body(RootModel[Any]):
        pass

    holder: dict[str, Any] = {"handle": None, "backlog": []}
    hlock = threading.Lock()

    def emit(payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        with hlock:
            handle = holder["handle"]
            if handle is None:
                holder["backlog"].append(data)
                return
        try:
            Emitter.emit_str(handle, EVENT_CHANNEL, data)
        except Exception:  # noqa: BLE001 - the window may be closing
            _log_error(traceback.format_exc())

    def bind(handle: Any) -> None:
        with hlock:
            if holder["handle"] is not None:
                return
            holder["handle"] = handle
            backlog, holder["backlog"] = holder["backlog"], []
        for data in backlog:
            try:
                Emitter.emit_str(handle, EVENT_CHANNEL, data)
            except Exception:  # noqa: BLE001
                pass

    session = GuiSession(Path(args.workspace), overrides, fake, emit)
    commands = Commands()

    def body_dict(body: Body) -> dict[str, Any]:
        return body.root if isinstance(body.root, dict) else {}

    @commands.command()
    async def get_state(body: Body, app_handle: AppHandle) -> Body:
        bind(app_handle)
        return Body(session.state())

    @commands.command()
    async def send(body: Body, app_handle: AppHandle) -> Body:
        bind(app_handle)
        return Body(session.send(str(body_dict(body).get("text", ""))))

    @commands.command()
    async def steer(body: Body, app_handle: AppHandle) -> Body:
        bind(app_handle)
        return Body(session.steer(str(body_dict(body).get("text", ""))))

    @commands.command()
    async def browse_workspace(body: Body, app_handle: AppHandle) -> Body:
        return Body(session.browse_workspace())

    @commands.command()
    async def cancel(body: Body, app_handle: AppHandle) -> Body:
        bind(app_handle)
        return Body(session.cancel())

    @commands.command()
    async def approve(body: Body, app_handle: AppHandle) -> Body:
        b = body_dict(body)
        return Body({"ok": session.approve(str(b.get("id", "")), b.get("answer"))})

    @commands.command()
    async def new_chat(body: Body, app_handle: AppHandle) -> Body:
        return Body(session.queue_job(("new_chat",), "starting a new chat"))

    @commands.command()
    async def fresh(body: Body, app_handle: AppHandle) -> Body:
        return Body(session.queue_job(("fresh",), "recycling the browser"))

    @commands.command()
    async def set_workspace(body: Body, app_handle: AppHandle) -> Body:
        return Body(session.set_workspace(str(body_dict(body).get("path", ""))))

    @commands.command()
    async def set_backend(body: Body, app_handle: AppHandle) -> Body:
        return Body(session.set_backend(str(body_dict(body).get("backend", ""))))

    @commands.command()
    async def set_option(body: Body, app_handle: AppHandle) -> Body:
        return Body(session.set_option(body_dict(body)))

    @commands.command()
    async def open_external(body: Body, app_handle: AppHandle) -> Body:
        b = body_dict(body)
        return Body(session.open_external(str(b.get("target", "")), str(b.get("kind", "")),
                                          bool(b.get("reveal")), bool(b.get("confirmed"))))

    @commands.command()
    async def reload_settings(body: Body, app_handle: AppHandle) -> Body:
        return Body(session.reload_settings())

    @commands.command()
    async def open_settings(body: Body, app_handle: AppHandle) -> Body:
        return Body(session.open_settings())

    @commands.command()
    async def test_opened(body: Body, app_handle: AppHandle) -> Body:
        """Test hook: what open_external would have opened (only with NEO_TEST_NO_OPEN=1)."""
        if not _test_no_open():
            return Body([])
        with session.lock:
            return Body(list(session.opened))

    window = {
        "label": "main",
        "title": version_string(),
        "width": 1100,
        "height": 820,
        "minWidth": 560,
        "minHeight": 420,
        "dragDropEnabled": False,   # Tauri's OS file-drop handler otherwise swallows in-page HTML5 drag-and-drop
    }
    tauri_config = {"version": __version__, "app": {"windows": [window]}}
    from pytauri.ffi import RunEvent

    trace = os.environ.get("NEO_GUI_TRACE") or os.environ.get("AGENT_NEO_GUI_TRACE")
    if trace:
        with open(trace, "a", encoding="utf-8") as fh:
            fh.write(f"window config: {json.dumps(window, sort_keys=True)}\n")
    def on_run(app_handle: Any, event: Any) -> None:  # must never raise (pytauri runs it on the event loop)
        try:
            if trace and type(event).__name__ != "RunEvent_MainEventsCleared":
                with open(trace, "a", encoding="utf-8") as fh:
                    fh.write(f"{type(event).__name__} {getattr(event, 'label', '')} {type(getattr(event, 'event', None)).__name__}\n")
            if isinstance(event, RunEvent.ExitRequested) and event.code is None:
                # Measured with pytauri-wheel 0.8.0: closing the last window raises ExitRequested(code=None) but
                # run_return() never returns on its own, and AppHandle.exit() ends the process without returning
                # here either. So shut the session down (closes the Edge the web driver launched) first.
                session.shutdown()
                if trace:
                    with open(trace, "a", encoding="utf-8") as fh:
                        fh.write(f"shutdown complete (worker alive: {session.thread.is_alive()})\n")
                app_handle.exit(0)
        except BaseException:  # noqa: BLE001
            pass

    code = 0
    try:
        with start_blocking_portal("asyncio") as portal:
            app = builder_factory().build(
                context=context_factory(TAURI_DIR, tauri_config=tauri_config),
                invoke_handler=commands.generate_handler(portal),
            )
            code = app.run_return(on_run)
    finally:
        session.shutdown()
    return int(code or 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - pythonw has no console: keep the traceback
        _log_error(traceback.format_exc())
        raise
