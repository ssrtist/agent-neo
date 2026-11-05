#!/usr/bin/env python3
"""Agent Neo (agent-neo / neo): a local coding agent driven by Copilot, Gemini, and DeepSeek.

This is the command-line renderer. The agent loop lives in neo_core.py and is shared
with the GUI (neo_gui.py); this file only prints events and asks at the terminal.

Supports multiple backends:
  - copilot  : Controls M365 Copilot (https://m365.cloud.microsoft/chat) or Consumer Copilot (https://copilot.microsoft.com) in Edge via Playwright over CDP (default; alias: web)
  - gemini   : Controls Google Gemini (https://gemini.google.com/app) in Edge via Playwright over CDP
  - deepseek : Controls DeepSeek Web Chat (https://chat.deepseek.com/) in Edge via Playwright over CDP
  - desktop  : Controls M365Copilot.exe / Copilot.exe desktop app via UI Automation (pywinauto)
  - auto     : Automatically detects running desktop app, falling back to copilot web
  - custom   : Controls any web chat page (custom_url) with a pre-harness checklist

Usage:
  python neo.py [--backend copilot|gemini|deepseek|desktop|auto|custom] [--workspace <dir>]
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
import sys
from pathlib import Path
from typing import Any

from neo_core import (  # noqa: F401 - parse_action/build_system_prompt/BASE_SYSTEM_INSTRUCTIONS re-exported
    BASE_SYSTEM_INSTRUCTIONS,
    Agent,
    build_system_prompt,
    discover_skills,
    parse_action,
)
from neo_events import Cancelled, SessionLog, dedupe_status, fanout
from neo_settings import BACKEND_ALIASES, BACKENDS, BROWSER_MODES, SettingsError, custom_options, load_settings, neo_home
from neo_version import __version__, version_string  # noqa: F401

WEB_BACKENDS = ("copilot", "web", "gemini", "deepseek", "auto", "custom")


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
        description=f"{version_string()}: local coding agent driving Copilot, Gemini, and DeepSeek",
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
    parser.add_argument("--port", type=int, default=None, help="Edge CDP port for web backend (default: 9365)")
    parser.add_argument("--step-chunk", type=int, default=None,
                        help=f"Dynamic threshold of sequential tool steps before pausing (default: {DEFAULT_STEP_CHUNK})")
    parser.add_argument("--hard-ceiling", type=int, default=None,
                        help=f"Hard ceiling of maximum allowed tool steps per task loop (default: {MAX_SESSION_STEPS})")
    parser.add_argument("--yolo", "--auto-approve", dest="yolo", action="store_true", default=None,
                        help="Auto-approve all tool calls without prompting (YOLO mode)")
    parser.add_argument("--show-thinking", dest="show_thinking", action="store_true", default=None,
                        help="Print the model's optional one-line 'thought' for each step")
    parser.add_argument("--enable-subagents", dest="enable_subagents", action="store_true", default=None,
                        help="Offer the run_subagent tool (off by default)")
    parser.add_argument("--new-session-only", "--reuse-browser", dest="new_session_only", action="store_true",
                        help="Reuse existing browser instance and only start a new chat session (default is a fresh browser instance)")
    parser.add_argument("--fake-backend", nargs="?", const="", default=None, metavar="SCRIPT.json",
                        help=argparse.SUPPRESS)
    return parser


def cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "backend": args.backend,
        "browser_mode": args.browser_mode,
        "cdp_port": args.port,
        "custom_url": args.custom_url,
        "step_chunk": args.step_chunk,
        "hard_ceiling": args.hard_ceiling,
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
    log = SessionLog(neo_home())
    sink = fanout(renderer, dedupe_status(log.write))

    def notify(text: str) -> None:
        renderer({"type": "status", "text": text})

    current_backend = settings.backend
    driver = make_driver(current_backend, settings, new_session_only, fake_script, notify)
    agent = Agent(driver, ROOT, settings, sink, CliApprover())
    print(f"Discovered {len(agent.skills)} skills: {', '.join(agent.skills.keys()) if agent.skills else 'none'}")
    print(f"Active backend: {driver.__class__.__name__}")
    print("Commands: /new, /fresh, /backend <copilot|gemini|deepseek|desktop|auto|custom>, /workspace [path], /skills, /steps [N], /yolo, /quit")

    try:
        while True:
            try:
                user_text = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting agent-neo...")
                break

            if not user_text:
                continue

            if user_text in {"/quit", "/exit"}:
                print("Exiting agent-neo...")
                break

            if user_text == "/new":
                driver.new_conversation()
                print("Started a new conversation topic.")
                continue

            if user_text == "/fresh":
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

            if user_text == "/skills":
                agent.skills = discover_skills(ROOT)
                print(f"Skills ({len(agent.skills)}):")
                for k, v in agent.skills.items():
                    print(f"  - {k}: {v['description']}")
                continue

            if user_text.startswith("/backend"):
                parts = user_text.split()
                target_b = BACKEND_ALIASES.get(parts[1].lower(), parts[1].lower()) if len(parts) > 1 else ""
                if target_b in BACKENDS:
                    try:
                        new_drv = make_driver(target_b, agent.settings, new_session_only, fake_script, notify)
                        driver.close()
                        driver = new_drv
                        agent.driver = driver
                        current_backend = target_b
                        print(f"Switched backend to {driver.__class__.__name__}")
                    except Exception as exc:
                        print(f"Failed to switch backend: {exc}")
                else:
                    print("Usage: /backend <copilot|gemini|deepseek|desktop|auto|custom>")
                continue

            run_turn_with_ctrl_c(agent, user_text)
    finally:
        driver.close()


def run_turn_with_ctrl_c(agent: Agent, user_text: str) -> None:
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
        agent.run_turn(user_text)
    except KeyboardInterrupt:
        print("\nTurn interrupted (Ctrl+C).")
    finally:
        if old is not None:
            signal.signal(signal.SIGINT, old)


if __name__ == "__main__":
    main()
