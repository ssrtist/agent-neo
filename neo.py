#!/usr/bin/env python3
"""Agent Neo (agent-neo / neo): a local coding agent driven by Copilot, Gemini, DeepSeek, and WhatsApp.

This is the command-line renderer. The agent loop lives in neo_core.py and is shared
with the GUI (neo_gui.py); this file only prints events and asks at the terminal.

Supports multiple backends:
  - copilot  : Controls M365 Copilot (https://m365.cloud.microsoft/chat) or Consumer Copilot (https://copilot.microsoft.com) in Edge via Playwright over CDP (default; alias: web)
  - gemini   : Controls Google Gemini (https://gemini.google.com/app) in Edge via Playwright over CDP
  - deepseek : Controls DeepSeek Chat (https://chat.deepseek.com) in Edge via Playwright over CDP
  - whatsapp : Controls WhatsApp Web (https://web.whatsapp.com/send/?phone=...) in Edge via Playwright over CDP
  - desktop  : Controls M365Copilot.exe / Copilot.exe desktop app via UI Automation (pywinauto)
  - auto     : Automatically detects running desktop app, falling back to copilot web
  - custom   : Controls any web chat page (custom_url) with a pre-harness checklist

Usage:
  python neo.py [--backend copilot|gemini|deepseek|whatsapp|desktop|auto|custom] [--workspace <dir>]
  python neo.py --version

Version: see neo_version.py (single source of truth).
Settings: neo.toml in the agent-neo home and in <workspace>\\.agent-neo\\ (see README).

Environment:
  NEO_ROOT / AGENT_NEO_ROOT : default workspace root
  NEO_HOME / AGENT_NEO_HOME : agent-neo home folder (default ~/.agent-neo): settings and session logs
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
from pathlib import Path
from typing import Any

from neo_core import (  # noqa: F401 - parse_action/build_system_prompt/BASE_SYSTEM_INSTRUCTIONS re-exported
    BASE_SYSTEM_INSTRUCTIONS,
    Agent,
    build_system_prompt,
    discover_agents,
    discover_skills,
    parse_action,
)
from neo_events import Cancelled, SessionLog, dedupe_status, fanout
from neo_sessions import SessionStore, build_fallback_summary, max_turn_number, normalize_backend
from neo_settings import BACKEND_ALIASES, BACKENDS, BROWSER_MODES, SettingsError, custom_options, load_settings, neo_home
from neo_version import __version__, version_string  # noqa: F401

WEB_BACKENDS = ("copilot", "web", "gemini", "deepseek", "whatsapp", "auto", "custom")


def _default_root() -> Path:
    """Workspace root from NEO_ROOT / AGENT_NEO_ROOT, else the cwd."""
    value = os.environ.get("NEO_ROOT") or os.environ.get("AGENT_NEO_ROOT")
    return Path(value or Path.cwd()).resolve()


ROOT = _default_root()
DEFAULT_STEP_CHUNK = 10
MAX_SESSION_STEPS = 255


def die(message: str) -> None:
    raise SystemExit(message)


def make_driver(backend: str, settings: Any, new_session_only: bool, fake_script: str | None, notify=None):
    """The chat driver for a backend (the hidden --fake-backend replaces it with the scripted fake)."""
    if fake_script is not None:
        from neo_fake import FakeChatDriver, load_script
        return FakeChatDriver(load_script(fake_script or None))
    from chat_driver import get_driver
    return get_driver(backend, new_session_only=new_session_only, port=settings.cdp_port,
                      browser_mode=settings.browser_mode, notify=notify,
                      profile_dir=getattr(settings, "browser_profile_path", "") or None,
                      phone=getattr(settings, "whatsapp_phone", None),
                      contact=getattr(settings, "whatsapp_contact", None),
                      custom=custom_options(settings))


# --------------------------------------------------------------------------- terminal renderer


class CliRenderer:
    """Prints core events in the terminal format."""

    def __init__(self, show_thinking: bool = False, out=None) -> None:
        self.show_thinking = show_thinking
        self.out = out or sys.stdout
        self._dots = 0
        self._spinning = False

    def _print(self, text: str = "") -> None:
        self._end_spinner()
        print(text, file=self.out)

    def _end_spinner(self) -> None:
        if self._spinning:
            self.out.write("\n")
            self._spinning = False

    def __call__(self, event: dict[str, Any], prefix: str = "") -> None:
        t = event.get("type")
        if t == "step_start":
            self._dots = 0
            self._print(f"\n{prefix}[Step {event['step']}/{event['limit']} (Ceiling: {event['ceiling']})] Waiting for model...")
        elif t == "status":
            text = event.get("text", "")
            if "is working" in text.lower():
                self._dots += 1
                dots = "." * ((self._dots - 1) % 5 + 1)
                self.out.write(f"\r{prefix}  -> agent-neo is working{dots:<6}")
                self.out.flush()
                self._spinning = True
            else:
                self._print(f"{prefix}  -> {text}")
        elif t == "reply":
            self._print(f"\n{prefix}[Model raw reply]\n{event.get('_full', event.get('text', ''))}\n")
        elif t == "thought":
            if self.show_thinking:
                self._print(f"{prefix}  (thinking) {event.get('text', '')}")
        elif t == "tool_result":
            self._print(f"{prefix}[Tool result: {event['name']}]\n{event.get('_full', event.get('preview', ''))}\n")
        elif t == "approval_result" and event.get("auto") and event.get("kind") == "tool":
            self._print(f"\n{prefix}[yolo] Auto-approving tool call: {event.get('summary', '')}")
        elif t == "final":
            self._print(f"\n{prefix}=== FINAL ANSWER ===" if not prefix else f"\n{prefix}=== SUB-AGENT FINAL ===")
            self._print(event.get("message", ""))
        elif t == "error":
            if event.get("fatal"):
                self._print(f"\n{prefix}[error] {event.get('message', '')}")
            else:
                self._print(f"{prefix}  -> {event.get('message', '')}")
        elif t == "subagent_start":
            self._print(f"\n[Sub-agent {event['sub_id']}] started: {event.get('task', '')}")
        elif t == "subagent_event":
            inner = event.get("event") or {}
            if inner.get("type") not in ("turn_start", "turn_end"):
                self(inner, prefix=f"  [{event['sub_id']}] ")
        elif t == "subagent_end":
            self._print(f"[Sub-agent {event['sub_id']}] ended: {event.get('outcome', '')}")
        elif t == "turn_end":
            self._end_spinner()
            if event.get("outcome") == "cancelled" and not prefix:
                self._print("Turn stopped.")


class CliApprover:
    """Terminal prompts for the core's approval requests."""

    def __call__(self, request: dict[str, Any]) -> Any:
        kind = request.get("kind")
        try:
            if kind == "tool":
                answer = input(f"\nApprove tool call? {request.get('summary', '')}\n[y/N] ").strip().lower()
                return answer in {"y", "yes"}
            if kind == "question":
                q = request.get("question", request.get("summary", ""))
                opts = request.get("options") or []
                print(f"\n[?] {q}")
                if opts:
                    for i, opt in enumerate(opts, 1):
                        print(f"  {i}. {opt}")
                    ans = input(f"Enter choice (1-{len(opts)}) or type answer: ").strip()
                    if ans.isdigit() and 1 <= int(ans) <= len(opts):
                        return opts[int(ans) - 1]
                    return ans if ans else opts[0]
                return input("Your answer: ").strip()
            if kind == "steps":
                chunk = request.get("chunk", DEFAULT_STEP_CHUNK)
                print(f"\n[Safety Threshold] Completed {request.get('step')} autonomous tool steps.")
                resp = input(f"Continue execution? [Y (+{chunk} steps) / <number> / abort]: ").strip().lower()
                if resp in {"", "y", "yes"}:
                    return chunk
                if resp.isdigit() and int(resp) > 0:
                    return int(resp)
                return 0
            if kind == "ceiling":
                print(f"\n[Hard Ceiling Reached] Task has reached the maximum allowable {request.get('ceiling')} steps limit.")
                cont = input(f"Hit hard ceiling ({request.get('ceiling')} steps). Force continue another batch? [y/N]: ")
                return cont.strip().lower() in {"y", "yes"}
        except EOFError:
            return False if kind != "steps" else 0
        except KeyboardInterrupt:
            raise Cancelled("interrupted at the prompt") from None
        return False


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neo.py",
        description=f"{version_string()}: local coding agent driving Copilot, Gemini, DeepSeek, and WhatsApp",
        epilog="Settings: neo.toml in the agent-neo home (NEO_HOME, default ~/.agent-neo) and in "
               "<workspace>/.agent-neo/. Command-line flags override both.",
    )
    parser.add_argument("--version", action="version", version=version_string(),
                        help="Show the Agent Neo version and exit")
    parser.add_argument("--backend", choices=[*BACKENDS, *BACKEND_ALIASES.keys()], default=None,
                        help="Chat automation backend (default: copilot, unless neo.toml sets 'backend')")
    parser.add_argument("--browser-mode", choices=list(BROWSER_MODES), default=None,
                        help="web backend window: offscreen (default; shown only if sign-in needs you), "
                             "visible, or headless (experimental)")
    parser.add_argument("-w", "--workspace", default=str(ROOT),
                        help="Workspace root path (default: NEO_ROOT, else current directory)")
    parser.add_argument("--custom-url", default=None, metavar="URL",
                        help="chat page for --backend custom (default: neo.toml [custom_backend] url)")
    parser.add_argument("--whatsapp-phone", default=None, metavar="PHONE",
                        help="Target phone number for --backend whatsapp (default: 19175123390)")
    parser.add_argument("--whatsapp-contact", default=None, metavar="NAME",
                        help="Optional contact display name for --backend whatsapp verification")
    parser.add_argument("--port", type=int, default=None, help="Edge CDP port for web backend (default: 9365)")
    parser.add_argument("--step-chunk", type=int, default=None,
                        help=f"Dynamic threshold of sequential tool steps before pausing (default: {DEFAULT_STEP_CHUNK})")
    parser.add_argument("--hard-ceiling", type=int, default=None,
                        help=f"Hard ceiling of maximum allowed tool steps per task loop (default: {MAX_SESSION_STEPS})")
    parser.add_argument("--shell-timeout", type=int, default=None,
                        help="Default foreground timeout in seconds for run_shell commands (default: 120)")
    parser.add_argument("--yolo", "--auto-approve", dest="yolo", action="store_true", default=None,
                        help="Auto-approve all tool calls without prompting (YOLO mode)")
    parser.add_argument("--show-thinking", dest="show_thinking", action="store_true", default=None,
                        help="Print the model's optional one-line 'thought' for each step")
    parser.add_argument("--enable-subagents", dest="enable_subagents", action="store_true", default=None,
                        help="Offer the run_subagent tool (off by default)")
    parser.add_argument("--new-session-only", "--reuse-browser", dest="new_session_only", action="store_true",
                        help="Reuse existing browser instance and only start a new chat session (default is a fresh browser instance)")
    parser.add_argument("--resume", nargs="?", const="last", default=None, metavar="SESSION_ID",
                        help="Resume a previous session by ID (or latest for the active backend)")
    parser.add_argument("--no-resume", dest="no_resume", action="store_true",
                        help="Start a brand-new session instead of auto-resuming the last session for the backend")
    parser.add_argument("--fake-backend", nargs="?", const="", default=None, metavar="SCRIPT.json",
                        help=argparse.SUPPRESS)
    return parser


def cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "backend": args.backend,
        "browser_mode": args.browser_mode,
        "cdp_port": args.port,
        "custom_url": args.custom_url,
        "whatsapp_phone": args.whatsapp_phone,
        "whatsapp_contact": args.whatsapp_contact,
        "step_chunk": args.step_chunk,
        "hard_ceiling": args.hard_ceiling,
        "shell_timeout": args.shell_timeout,
        "auto_approve": args.yolo,
        "show_thinking": args.show_thinking,
        "enable_subagents": args.enable_subagents,
    }


def main() -> None:
    global ROOT
    parser = build_parser()
    args = parser.parse_args()
    fake_script = (
        args.fake_backend
        if args.fake_backend is not None
        else (os.environ.get("NEO_FAKE_SCRIPT") or os.environ.get("AGENT_NEO_FAKE_SCRIPT"))
    )

    ROOT = Path(args.workspace).resolve()
    overrides = cli_overrides(args)
    try:
        settings, warnings = load_settings(ROOT, overrides)
    except SettingsError as exc:
        die(f"agent-neo: settings error: {exc}")
    print(version_string())
    for w in warnings:
        print(f"[settings] warning: {w}")
    new_session_only = args.new_session_only
    print(f"Workspace: {ROOT}")
    print(f"Limits: safety chunk = {settings.step_chunk} steps, hard ceiling = {settings.hard_ceiling} steps")
    if settings.auto_approve:
        print("[yolo] Auto-approval enabled for all tool calls.")
    if new_session_only:
        print("[browser] Mode: Reusing existing browser (new session only).")
    else:
        print("[browser] Mode: Fresh browser instance by default.")
    if settings.backend in WEB_BACKENDS:
        print(f"[browser] Window: {settings.browser_mode}")
    if settings.enable_subagents:
        print("[subagents] run_subagent tool enabled.")

    renderer = CliRenderer(settings.show_thinking)
    store = SessionStore(neo_home())
    session_state: dict[str, Any] = {
        "id": store.make_session_id(),
        "log": None,
        "backend": settings.backend,
    }
    session_state["log"] = SessionLog(neo_home(), stamp=session_state["id"])

    def record_and_log(event: dict[str, Any]) -> None:
        session_state["log"].write(event)
        etype = event.get("type")
        if etype == "turn_start":
            store.record_turn_start(
                session_state["id"],
                backend=session_state["backend"],
                workspace=ROOT,
                user_text=str(event.get("text", "")),
                turn=event.get("turn"),
            )
        elif etype == "turn_end":
            chat_url = driver.current_chat_url() if hasattr(driver, "current_chat_url") else None
            events = store.load_events(session_state["id"])
            store.record_turn_end(
                session_state["id"],
                backend=session_state["backend"],
                workspace=ROOT,
                chat_url=chat_url,
                events=events,
            )

    sink = fanout(renderer, dedupe_status(record_and_log))

    def notify(text: str) -> None:
        renderer({"type": "status", "text": text})

    wakeup_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def on_wakeup(payload: dict[str, Any]) -> None:
        wakeup_queue.put(payload)

    current_backend = settings.backend
    driver = make_driver(current_backend, settings, new_session_only, fake_script, notify)
    agent = Agent(driver, ROOT, settings, sink, CliApprover(), on_wakeup=on_wakeup)

    def rotate_session() -> None:
        session_state["id"] = store.make_session_id()
        session_state["log"] = SessionLog(neo_home(), stamp=session_state["id"])
        agent.reset_session()

    def apply_resume(rec: dict[str, Any], *, open_remote: bool = True) -> None:
        global ROOT
        nonlocal current_backend, driver
        rec_backend = normalize_backend(rec.get("backend"))
        if rec_backend in BACKENDS and rec_backend != current_backend:
            if fake_script is None:
                new_drv = make_driver(rec_backend, agent.settings, new_session_only, fake_script, notify)
                driver.close()
                driver = new_drv
                agent.driver = driver
            current_backend = rec_backend
            session_state["backend"] = current_backend
            agent.skills = discover_skills(ROOT, backend=current_backend)
            agent.agents = discover_agents(ROOT, backend=current_backend)

        saved_ws = str(rec.get("workspace") or "").strip()
        if saved_ws:
            target_dir = Path(saved_ws)
            if target_dir.is_dir() and target_dir.resolve() != ROOT:
                try:
                    new_settings, warns = load_settings(target_dir.resolve(), overrides)
                    for w in warns:
                        print(f"[settings] warning: {w}")
                    yolo, chunk = agent.auto_approve, agent.step_chunk
                    ROOT = target_dir.resolve()
                    agent.set_settings(new_settings)
                    agent.auto_approve, agent.step_chunk = yolo, chunk
                    agent.set_workspace(ROOT)
                    print(f"[session] Switched workspace to: {ROOT}")
                except SettingsError as exc:
                    print(f"[session] Keeping {ROOT} (settings error in {target_dir}: {exc})")
            elif not target_dir.is_dir():
                print(f"[session] Saved workspace missing ({target_dir}); keeping {ROOT}")

        chat_url = str(rec.get("chat_url") or "").strip()
        remote_resumed = False
        if open_remote and chat_url and hasattr(driver, "open_chat_url"):
            remote_resumed = bool(driver.open_chat_url(chat_url))

        events = store.load_events(rec["id"])
        fallback_summary = str(rec.get("summary") or "").strip() or build_fallback_summary(events)
        session_state["id"] = rec["id"]
        session_state["log"] = SessionLog(neo_home(), stamp=rec["id"])
        agent.restore_session(
            last_turn=max_turn_number(events),
            remote_resumed=remote_resumed,
            fallback_summary=fallback_summary,
        )
        mode_note = "web thread" if remote_resumed else "local transcript + summary seed"
        print(f"[session] Resumed {rec['id']} [{current_backend}] ({rec.get('turn_count', 0)} turns, {mode_note}): {rec.get('title', '')}")

    # Auto-resume latest session for copilot/gemini/deepseek (or explicit --resume) unless --no-resume
    resume_target = args.resume
    if resume_target is None and not args.no_resume and fake_script is None and normalize_backend(current_backend) in ("copilot", "gemini", "deepseek"):
        auto_rec = store.latest_for_backend(current_backend)
        if auto_rec is not None:
            apply_resume(auto_rec, open_remote=True)
    elif resume_target is not None:
        if resume_target.lower() == "last":
            rec = store.latest_for_backend(current_backend)
            if rec is None:
                print(f"[session] No previous {current_backend} session found; starting fresh.")
            else:
                apply_resume(rec, open_remote=True)
        else:
            rec = store.get_session(resume_target)
            if rec is None:
                print(f"[session] Session {resume_target!r} not found; starting fresh.")
            else:
                apply_resume(rec, open_remote=True)

    print(f"Discovered {len(agent.skills)} skills: {', '.join(agent.skills.keys()) if agent.skills else 'none'}")
    print(f"Discovered {len(agent.agents)} agents: {', '.join(agent.agents.keys()) if agent.agents else 'none'}")
    print(f"Active backend: {driver.__class__.__name__}")
    print("Commands: /new, /fresh, /switch-account, /backend <copilot|gemini|deepseek|whatsapp|desktop|auto|custom>, /workspace [path], /skills, /agents, /agent <name> <task>, /steps [N], /yolo, /quit")
    print("Task commands: /tasks, /watch <task_id|Ns> <prompt>, /unwatch [id]")
    print("Session commands: /sessions [backend], /resume [id|last]")

    try:
        while True:
            try:
                user_text, is_wakeup = _next_cli_input(agent, wakeup_queue)
            except (EOFError, KeyboardInterrupt):
                print("\nExiting agent-neo...")
                break

            if is_wakeup:
                run_turn_with_ctrl_c(agent, user_text)
                continue

            if not user_text:
                continue

            if user_text in {"/quit", "/exit"}:
                print("Exiting agent-neo...")
                break

            if user_text == "/new":
                _drain_queue(wakeup_queue)
                agent.close()
                rotate_session()
                driver.new_conversation()
                print("Started a new conversation topic.")
                continue

            if user_text == "/fresh":
                _drain_queue(wakeup_queue)
                agent.close()
                rotate_session()
                if current_backend in WEB_BACKENDS and fake_script is None:
                    print("Recycling browser and launching a fresh instance...")
                    driver.close()
                    driver = make_driver(current_backend, agent.settings, False, fake_script, notify)
                    agent.driver = driver
                    print(f"Fresh {driver.__class__.__name__} ready.")
                else:
                    driver.new_conversation()
                    print("Started fresh conversation.")
                continue

            if user_text == "/compact":
                print("Compacting conversation history...")
                ckpt = agent.compact()
                print("Conversation compacted! Structured checkpoint preserved for next turn.")
                continue

            if user_text == "/yolo":
                agent.auto_approve = not agent.auto_approve
                state = "ENABLED (auto-approving all tools)" if agent.auto_approve else "DISABLED (manual approval)"
                print(f"[yolo] YOLO mode {state}")
                continue

            if user_text.startswith(("/workspace", "/cd")):
                parts = user_text.split(maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    target_dir = Path(parts[1].strip().strip('"').strip("'")).expanduser().resolve()
                    if target_dir.is_dir():
                        try:
                            new_settings, warns = load_settings(target_dir, overrides)
                        except SettingsError as exc:
                            print(f"Error: {exc}. Workspace not changed.")
                            continue
                        for w in warns:
                            print(f"[settings] warning: {w}")
                        yolo, chunk = agent.auto_approve, agent.step_chunk
                        ROOT = target_dir
                        _drain_queue(wakeup_queue)
                        agent.close()
                        agent.set_settings(new_settings)
                        agent.auto_approve, agent.step_chunk = yolo, chunk   # session toggles survive
                        agent.set_workspace(ROOT)
                        print(f"Workspace changed to: {ROOT}")
                        print(f"Discovered {len(agent.skills)} skills in new workspace.")
                    else:
                        print(f"Error: Directory does not exist: {target_dir}")
                else:
                    print(f"Current workspace: {ROOT}")
                    print("Usage: /workspace <path> (or /cd <path>) to change.")
                continue

            if user_text.startswith("/steps"):
                parts = user_text.split()
                if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > 0:
                    agent.step_chunk = int(parts[1])
                    print(f"Step chunk updated to {agent.step_chunk} steps (ceiling: {agent.hard_ceiling}).")
                else:
                    print(f"Current limits: safety chunk = {agent.step_chunk} steps, ceiling = {agent.hard_ceiling} steps.")
                    print("Usage: /steps <num> to change chunk size.")
                continue

            if user_text in {"/switch-account", "/logout"}:
                _drain_queue(wakeup_queue)
                agent.close()
                if not hasattr(driver, "switch_account"):
                    print(f"{driver.__class__.__name__} does not support /switch-account.")
                    continue
                try:
                    driver.switch_account()
                    print("Account switched and signed in.")
                except Cancelled:
                    print("Account switch cancelled.")
                except Exception as exc:
                    print(f"Account switch failed: {exc}")
                continue

            if user_text == "/skills":
                agent.skills = discover_skills(ROOT, backend=current_backend)
                print(f"Skills ({len(agent.skills)}):")
                for k, v in agent.skills.items():
                    print(f"  - {k}: {v['description']}")
                continue

            if user_text == "/agents":
                agent.agents = discover_agents(ROOT, backend=current_backend)
                print(f"Agents ({len(agent.agents)}):")
                for k, v in agent.agents.items():
                    atools = v.get("allowed_tools")
                    tools_str = ", ".join(sorted(atools)) if atools is not None else "all"
                    print(f"  - {k}: {v['description']} [tools: {tools_str}]")
                continue

            if user_text == "/agent" or user_text.startswith("/agent "):
                parts = user_text.split(maxsplit=2)
                if len(parts) < 3 or not parts[2].strip():
                    print("Usage: /agent <name> <task>")
                    continue
                agent.agents = discover_agents(ROOT, backend=current_backend)
                spec = agent.resolve_agent(parts[1])
                if spec is None:
                    avail = ", ".join(sorted(agent.agents)) or "none"
                    print(f"Unknown agent {parts[1]!r}. Available: {avail}")
                    continue
                run_turn_with_ctrl_c(agent, parts[2].strip(), agent_name=spec["name"])
                continue

            if user_text == "/tasks":
                info = agent.task_manager.list_tasks()
                print(f"Tasks ({len(info['tasks'])}):")
                for t in info["tasks"]:
                    print(f"  - {t['task_id']} [{t['status']}] pid={t['pid']}: {t['command']}")
                print(f"Watches ({len(info['watches'])}):")
                for w in info["watches"]:
                    target = w["task_id"] or f"timer {w['delay_seconds']}s"
                    print(f"  - {w['watch_id']} -> {target}: {w['prompt']}")
                continue

            if user_text.startswith("/watch"):
                parts = user_text.split(maxsplit=2)
                if len(parts) < 3 or not parts[2].strip():
                    print("Usage: /watch <task_id|Ns> <follow-up prompt>")
                    continue
                target_spec, wprompt = parts[1].strip(), parts[2].strip()
                if target_spec.endswith("s") and target_spec[:-1].replace(".", "", 1).isdigit():
                    res = agent.task_manager.watch_task(prompt=wprompt, delay_seconds=float(target_spec[:-1]))
                else:
                    res = agent.task_manager.watch_task(prompt=wprompt, task_id=target_spec)
                if "error" in res:
                    print(f"Error: {res['error']}")
                else:
                    print(f"Watching ({res['watch_id']}): will resume when {target_spec} triggers.")
                continue

            if user_text.startswith("/unwatch"):
                _drain_queue(wakeup_queue)
                parts = user_text.split(maxsplit=1)
                arg = parts[1].strip() if len(parts) > 1 else ""
                if arg.startswith("task_"):
                    res = agent.task_manager.unwatch_task(task_id=arg)
                elif arg:
                    res = agent.task_manager.unwatch_task(watch_id=arg)
                else:
                    res = agent.task_manager.unwatch_task()
                if "error" in res:
                    print(f"Error: {res['error']}")
                else:
                    print(f"Cancelled watches: {', '.join(res.get('cancelled', [])) or 'none'}")
                continue

            if user_text.startswith("/backend"):
                parts = user_text.split()
                target_b = BACKEND_ALIASES.get(parts[1].lower(), parts[1].lower()) if len(parts) > 1 else ""
                if target_b in BACKENDS:
                    try:
                        _drain_queue(wakeup_queue)
                        agent.task_manager.clear_watches()
                        new_drv = make_driver(target_b, agent.settings, new_session_only, fake_script, notify)
                        driver.close()
                        driver = new_drv
                        agent.driver = driver
                        current_backend = target_b
                        session_state["backend"] = current_backend
                        rotate_session()
                        agent.skills = discover_skills(ROOT, backend=current_backend)
                        agent.agents = discover_agents(ROOT, backend=current_backend)
                        print(f"Switched backend to {driver.__class__.__name__}")
                    except Exception as exc:
                        print(f"Failed to switch backend: {exc}")
                else:
                    print("Usage: /backend <copilot|gemini|deepseek|whatsapp|desktop|auto|custom>")
                continue

            if user_text.startswith("/sessions"):
                parts = user_text.split(maxsplit=1)
                filt = normalize_backend(parts[1]) if len(parts) > 1 and parts[1].strip() else None
                items = store.list_sessions(backend=filt, limit=15)
                if not items:
                    print("No saved sessions found.")
                else:
                    print(f"Saved sessions ({len(items)}):")
                    for idx, s in enumerate(items, start=1):
                        ws_label = Path(s["workspace"]).name if s.get("workspace") else ""
                        print(f"  {idx}. {s['id']} [{s.get('backend', '?')}] ({s.get('turn_count', 0)} turns, {ws_label}): {s.get('title', '')}")
                continue

            if user_text.startswith("/resume"):
                parts = user_text.split(maxsplit=1)
                target_arg = parts[1].strip() if len(parts) > 1 else "last"
                _drain_queue(wakeup_queue)
                agent.close()
                rec = None
                if not target_arg or target_arg.lower() == "last":
                    rec = store.latest_for_backend(current_backend)
                elif target_arg.isdigit():
                    items = store.list_sessions(limit=50)
                    idx = int(target_arg) - 1
                    if 0 <= idx < len(items):
                        rec = items[idx]
                if rec is None and target_arg and target_arg.lower() != "last":
                    rec = store.get_session(target_arg)
                if rec is None:
                    print(f"Session not found: {target_arg!r}")
                else:
                    apply_resume(rec, open_remote=True)
                continue

            run_turn_with_ctrl_c(agent, user_text)
    finally:
        agent.close()
        driver.close()


def _drain_queue(q: queue.Queue[Any]) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def _next_cli_input(agent: Agent, wakeup_queue: queue.Queue[dict[str, Any]]) -> tuple[str, bool]:
    """Wait for either user stdin or a reactive wakeup payload when watches are active."""
    try:
        payload = wakeup_queue.get_nowait()
        print(f"\n[wakeup] {payload.get('reason', 'watch triggered')} — resuming turn...")
        return payload["prompt"], True
    except queue.Empty:
        pass

    watches = agent.task_manager.list_tasks()["watches"]
    if not watches or not sys.stdin.isatty():
        return input("\nyou> ").strip(), False

    print("\nyou> ", end="", flush=True)
    if os.name == "nt":
        import msvcrt
        import time

        buf: list[str] = []
        while True:
            if not buf:
                try:
                    payload = wakeup_queue.get_nowait()
                    print(f"\n[wakeup] {payload.get('reason', 'watch triggered')} — resuming turn...")
                    return payload["prompt"], True
                except queue.Empty:
                    pass
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    print()
                    return "".join(buf).strip(), False
                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch in ("\x08", "\x7f"):
                    if buf:
                        buf.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                elif ch in ("\x00", "\xe0"):
                    msvcrt.getwch()  # consume extended key code
                else:
                    buf.append(ch)
                    sys.stdout.write(ch)
                    sys.stdout.flush()
            else:
                time.sleep(0.1)
    else:  # pragma: no cover
        import select

        while True:
            try:
                payload = wakeup_queue.get_nowait()
                print(f"\n[wakeup] {payload.get('reason', 'watch triggered')} — resuming turn...")
                return payload["prompt"], True
            except queue.Empty:
                pass
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if ready:
                line = sys.stdin.readline()
                if line == "":
                    raise EOFError
                return line.strip(), False


def run_turn_with_ctrl_c(agent: Agent, user_text: str, *, agent_name: str | None = None) -> None:
    """Ctrl+C during a turn stops the turn (cooperative cancel); a second Ctrl+C forces it."""
    import signal

    hits = 0

    def handler(signum, frame):  # noqa: ARG001
        nonlocal hits
        hits += 1
        if hits == 1:
            agent.cancel.set()
            print("\nStopping the turn... (press Ctrl+C again to force)")
        else:
            raise KeyboardInterrupt

    try:
        old = signal.signal(signal.SIGINT, handler)
    except ValueError:  # not the main thread
        old = None
    try:
        if agent_name:
            agent.run_agent_turn(agent_name, user_text)
        else:
            agent.run_turn(user_text)
    except KeyboardInterrupt:
        print("\nTurn interrupted (Ctrl+C).")
    finally:
        if old is not None:
            signal.signal(signal.SIGINT, old)


if __name__ == "__main__":
    main()
