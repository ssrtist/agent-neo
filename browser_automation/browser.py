"""browser.py — CDP-browser launcher with extension suppression.

Launch a dedicated, CDP-enabled Chromium browser (Edge by default, Chrome optional) into an
isolated user-data-dir, with extensions suppressed at launch so no idle extension service-worker
stalls Playwright's ``connect_over_cdp`` handshake.
"""
from __future__ import annotations

import os
import subprocess
import time

from . import runtime

# Extensions are the root of the connect_over_cdp stall: idle corporate service-workers stop
# responding to Playwright's auto-attach. Suppress them (and their background pages) so a
# launched CDP browser presents a clean, attachable target set.
#
# CAVEAT: enterprise-managed / force-installed extensions (ExtensionInstallForcelist policy)
# can ignore --disable-extensions and still load.
EXTENSION_SUPPRESSION_FLAGS = [
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-extensions-with-background-pages",
]


# --------------------------------------------------------------------------- #
# resolution helpers
# --------------------------------------------------------------------------- #
def _browser_spec(browser: str) -> dict:
    key = (browser or runtime.DEFAULT_BROWSER).lower()
    spec = runtime.BROWSERS.get(key)
    if spec is None:
        raise ValueError(
            f"Unknown browser '{browser}'. Choose one of: {', '.join(runtime.known_browsers())}.")
    return spec


def resolve_browser_path(browser: str = "edge", browser_path: str | None = None) -> str:
    """Return the executable path for a browser, or raise a clean, actionable error.

    Order: explicit browser_path arg -> env override -> the ordered candidate list.
    """
    spec = _browser_spec(browser)
    if browser_path:
        if not os.path.exists(browser_path):
            raise RuntimeError(f"{browser} not found at supplied path '{browser_path}'.")
        return browser_path
    env_path = os.environ.get(str(spec["env_path"]))
    if env_path:
        if not os.path.exists(env_path):
            raise RuntimeError(
                f"{browser} not found at {spec['env_path']}='{env_path}'.")
        return env_path
    for cand in spec["candidates"]:  # type: ignore[union-attr]
        if os.path.exists(cand):
            return cand
    tried = "\n  ".join(str(c) for c in spec["candidates"])  # type: ignore[union-attr]
    raise RuntimeError(
        f"{browser.capitalize()} not found. Tried:\n  {tried}\n"
        f"Install {browser}, or set {spec['env_path']} to its executable path.")


def resolve_user_data_dir(browser: str = "edge", user_data_dir: str | None = None) -> str:
    if user_data_dir:
        return user_data_dir
    return str(_browser_spec(browser)["user_data_dir"])


# --------------------------------------------------------------------------- #
# CDP readiness
# --------------------------------------------------------------------------- #
def cdp_version(port: int = runtime.CDP_PORT, timeout: float = 2.0) -> dict | None:
    """Return the CDP /json/version payload, or None if the endpoint is not ready."""
    try:
        r = runtime.SESSION.get(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json() or {}
        return data if data.get("webSocketDebuggerUrl") else None
    except Exception:
        return None


def cdp_port_ready(port: int = runtime.CDP_PORT, timeout: float = 2.0) -> bool:
    return cdp_version(port, timeout) is not None


# --------------------------------------------------------------------------- #
# kill / recycle
# --------------------------------------------------------------------------- #
def kill_cdp_browser(browser: str = "edge", user_data_dir: str | None = None,
                     port: int = runtime.CDP_PORT, wait_seconds: int = 10) -> None:
    """Kill every browser process bound to this dedicated user-data-dir.

    Matches on the profile-directory leaf in the command line (e.g. agent-neo-edge) so it never
    touches the user's normal browser. Both msedge.exe and chrome.exe are considered so a stale
    instance of either is cleaned up. Waits for the CDP port to go quiet.
    """
    port = port or runtime.CDP_PORT
    user_data_dir = resolve_user_data_dir(browser, user_data_dir)
    leaf = os.path.basename(os.path.normpath(user_data_dir))
    proc_names = {str(spec["proc_name"]) for spec in runtime.BROWSERS.values()}
    name_filter = " -or ".join(f"$_.Name -eq '{n}'" for n in sorted(proc_names))
    ps = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ ({name_filter}) -and $_.CommandLine -like '*{leaf}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not cdp_port_ready(port):
            return
        time.sleep(0.5)


# --------------------------------------------------------------------------- #
# launch / ensure
# --------------------------------------------------------------------------- #
def ensure_cdp_browser(browser: str = "edge", port: int = runtime.CDP_PORT,
                       user_data_dir: str | None = None, start_url: str | None = None,
                       browser_path: str | None = None, wait_seconds: int = runtime.WAIT_SECONDS,
                       force_fresh: bool = False, extra_args: list[str] | None = None,
                       no_startup_window: bool = False) -> dict:
    """Ensure a dedicated CDP-enabled browser is listening; launch it if not.

    force_fresh recycles any existing instance first (kill + relaunch) so the connect always
    targets a freshly-launched browser — the one state where connect_over_cdp is reliable.
    SSO / profile cookies persist in the user-data-dir across the recycle.

    extra_args are added to a launch (they do not apply to a browser that is already running).
    no_startup_window launches without any window (``--no-startup-window``); the caller opens
    one over CDP. agent-neo uses it for the off-screen mode: a window created over CDP with
    ``background: true`` does not take the keyboard focus (measured 2026-10-30, Edge, Win11),
    while a normal launch always does.

    Returns the live /json/version payload once the endpoint is ready.
    """
    port = port or runtime.CDP_PORT
    browser = (browser or runtime.DEFAULT_BROWSER).lower()
    user_data_dir = resolve_user_data_dir(browser, user_data_dir)
    exe = resolve_browser_path(browser, browser_path)
    start_url = start_url or "about:blank"

    if force_fresh:
        kill_cdp_browser(browser, user_data_dir=user_data_dir, port=port)
    else:
        existing = cdp_version(port)
        if existing is not None:
            print(f"CDP {browser} already listening on port {port} "
                  f"({existing.get('Browser', 'unknown')}).")
            return existing

    os.makedirs(user_data_dir, exist_ok=True)
    print(f"Launching dedicated {browser} (CDP port {port}, profile '{user_data_dir}'). "
          f"If prompted, complete corporate SSO once in that browser window.")
    tail = ["--no-startup-window"] if no_startup_window else [start_url]
    subprocess.Popen([
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        *EXTENSION_SUPPRESSION_FLAGS,
        *(extra_args or []),
        *tail,
    ])

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(0.5)
        v = cdp_version(port)
        if v is not None:
            return v
    raise RuntimeError(
        f"Timed out waiting for {browser} CDP endpoint "
        f"http://127.0.0.1:{port}/json/version after {wait_seconds}s.")
