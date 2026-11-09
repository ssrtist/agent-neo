"""neo_edge.py: agent-neo's dedicated Edge profile management.

agent-neo drives Edge with its own user-data-dir (default ``%LOCALAPPDATA%\\agent-neo-edge``) and its
own CDP port (default 9365), separate from the user's regular browser profile, so it never closes
or drives a browser that the user or another tool started.

The one rule this module exists for: **agent-neo only ever ends an Edge process whose
``--user-data-dir`` IS agent-neo's profile** (compared as a whole, normalised path; never a name or a
substring match, unlike ``browser_automation.kill_cdp_browser``, which matches the profile-folder name
anywhere in the command line). The ``browser_automation`` package handles process launch and CDP
readiness polling; agent-neo passes its profile and port explicitly and never calls its kill.

Stdlib + browser_automation only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Iterable, NamedTuple

from browser_automation import browser

DEFAULT_CDP_PORT = 9365
PROFILE_LEAF = "agent-neo-edge"
EDGE_PROCESS = "msedge.exe"


def _localappdata() -> str:
    return os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")


def default_profile_dir() -> str:
    """agent-neo's own Edge profile: %LOCALAPPDATA%\\agent-neo-edge."""
    return os.path.join(_localappdata(), PROFILE_LEAF)


def norm(path: str) -> str:
    """A path in one comparable form: absolute, normalised, case-folded, no trailing separator."""
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(path.strip().strip('"')))))


def same_path(a: str, b: str) -> bool:
    return norm(a) == norm(b)


def forbidden_profile_dirs() -> list[str]:
    """Profiles agent-neo must never use (it ends the Edge on its profile at a fresh start): the
    user's real Edge and Chrome User Data profiles."""
    la = _localappdata()
    dirs = [
        os.path.join(la, "Microsoft", "Edge", "User Data"),
        os.path.join(la, "Google", "Chrome", "User Data"),
    ]
    out: list[str] = []
    for d in dirs:
        if norm(d) not in (norm(x) for x in out):
            out.append(d)
    return out


def check_profile_dir(path: str) -> str | None:
    """None when path may be agent-neo's profile, else the reason it may not."""
    if not path or not path.strip():
        return "the browser profile folder is empty"
    p = norm(path)
    for bad in forbidden_profile_dirs():
        b = norm(bad)
        if p == b or p.startswith(b + os.sep) or b.startswith(p + os.sep):
            return f"{path} is (or contains, or is inside) a profile agent-neo must not use: {bad}"
    if p == norm(_localappdata()) or os.path.dirname(p) == p:
        return f"{path} is not a dedicated profile folder"
    return None


# ---------------------------------------------------------------- which processes are ours
_UDD = re.compile(
    r'"--user-data-dir=([^"]*)"'                 # "--user-data-dir=C:\a b"  (Windows quoting of the whole argument)
    r'|--user-data-dir="([^"]*)"'                # --user-data-dir="C:\a b"  (Chromium child processes)
    r'|--user-data-dir=([^"\s]+)'                # --user-data-dir=C:\a
    r'|--user-data-dir\s+(?:"([^"]*)"|([^"\s]+))',
    re.IGNORECASE)


def cmdline_user_data_dirs(cmdline: str) -> list[str]:
    """Every --user-data-dir value in a command line (quoted or not, '=' or a space)."""
    return [next(g for g in m.groups() if g is not None) for m in _UDD.finditer(cmdline or "")]


class Proc(NamedTuple):
    pid: int
    name: str
    cmdline: str


def is_profile_process(proc: Proc, profile_dir: str) -> bool:
    """An Edge process whose --user-data-dir is exactly profile_dir (every one it carries, at least one)."""
    if (proc.name or "").lower() != EDGE_PROCESS:
        return False
    dirs = cmdline_user_data_dirs(proc.cmdline)
    return bool(dirs) and all(same_path(d, profile_dir) for d in dirs)


def profile_pids(processes: Iterable[Proc], profile_dir: str) -> list[int]:
    return [p.pid for p in processes if is_profile_process(p, profile_dir)]


def list_edge_processes() -> list[Proc]:
    """Every msedge.exe with its command line (Windows, through CIM; POSIX via ps)."""
    if os.name != "nt":
        try:
            out = subprocess.run(["ps", "-ax", "-o", "pid=,args="], capture_output=True, text=True, timeout=15).stdout
        except Exception:  # noqa: BLE001
            return []
        procs: list[Proc] = []
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) < 2 or not parts[0].isdigit():
                continue
            pid, cmdline = int(parts[0]), parts[1]
            lower = cmdline.lower()
            if "microsoft edge" in lower or "google chrome" in lower or "msedge" in lower:
                procs.append(Proc(pid, EDGE_PROCESS, cmdline))
        return procs
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
          "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        return []
    if not out:
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    return [Proc(int(d["ProcessId"]), str(d.get("Name") or ""), str(d.get("CommandLine") or "")) for d in data]


def _stop_pids(pids: list[int]) -> None:
    if not pids:
        return
    if os.name != "nt":
        for p in pids:
            try:
                os.kill(int(p), 9)
            except Exception:  # noqa: BLE001
                pass
        return
    ps = f"Stop-Process -Id {','.join(str(int(p)) for p in pids)} -Force -ErrorAction SilentlyContinue"
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, timeout=30)
    except Exception:  # noqa: BLE001
        pass


def kill_profile_edge(profile_dir: str, port: int | None = None, wait_seconds: float = 10) -> list[int]:
    """End every Edge process on agent-neo's profile, and nothing else. Returns the PIDs it ended."""
    reason = check_profile_dir(profile_dir)
    if reason:
        raise ValueError(f"refusing to end Edge processes: {reason}")
    pids = profile_pids(list_edge_processes(), profile_dir)
    _stop_pids(pids)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        left = profile_pids(list_edge_processes(), profile_dir) if pids else []
        if not left and (port is None or not browser.cdp_port_ready(port, timeout=1)):
            break
        time.sleep(0.5)
    return pids


def port_owned_by_profile(port: int, profile_dir: str, processes: Iterable[Proc] | None = None) -> bool:
    """Some Edge on agent-neo's profile was started with --remote-debugging-port=port."""
    procs = list_edge_processes() if processes is None else list(processes)
    flag = re.compile(rf"--remote-debugging-port={int(port)}(?!\d)")
    return any(is_profile_process(p, profile_dir) and flag.search(p.cmdline) for p in procs)


class ForeignBrowserError(RuntimeError):
    """The CDP port answers, but not from agent-neo's own Edge profile."""


def ensure_profile_edge(profile_dir: str, port: int, *, force_fresh: bool, start_url: str | None = None,
                        extra_args: list[str] | None = None, no_startup_window: bool = False) -> dict | None:
    """Make sure agent-neo's own Edge (its profile, its port) is listening; launch it if not.

    force_fresh ends only the Edge on agent-neo's profile first. A CDP port answered by any other
    browser is refused (never driven, never ended). browser_automation never kills here: it is
    always called with force_fresh=False and agent-neo's profile.
    """
    reason = check_profile_dir(profile_dir)
    if reason:
        raise ValueError(f"agent-neo browser profile refused: {reason}")
    if force_fresh:
        kill_profile_edge(profile_dir, port)
    if browser.cdp_version(port) is not None and not port_owned_by_profile(port, profile_dir):
        raise ForeignBrowserError(
            f"CDP port {port} is in use by a browser that is not agent-neo's own Edge profile ({profile_dir}). "
            f"agent-neo will not drive or close it; set another cdp_port in neo.toml or --port.")
    return browser.ensure_cdp_browser(browser="edge", port=port, user_data_dir=profile_dir, start_url=start_url,
                                      force_fresh=False, extra_args=extra_args, no_startup_window=no_startup_window)
