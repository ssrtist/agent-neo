"""runtime.py — defaults for browser-automation.

Everything is overridable by env var or CLI flag. There is no auth/TLS story here: the only
network call is a localhost HTTP poll of the CDP endpoint (``/json/version``), so no WinTrust
SSLContext is required (unlike the REST skills). A plain requests session is used.
"""
from __future__ import annotations

import os

import requests

# --- CDP --------------------------------------------------------------------#
# Dedicated default port for the automation browser so it does not collide with standard
# DevTools ports (such as 9222). Each caller can pass its own --port; two purposes must not
# share a port. Override with BROWSER_AUTOMATION_CDP_PORT or --port.
CDP_PORT = int(os.environ.get("BROWSER_AUTOMATION_CDP_PORT", "9365"))

# Default browser when none is specified. Edge is the corporate default.
DEFAULT_BROWSER = os.environ.get("BROWSER_AUTOMATION_BROWSER", "edge").lower()

# How long (seconds) to wait for the CDP endpoint to come up after a launch.
WAIT_SECONDS = int(os.environ.get("BROWSER_AUTOMATION_WAIT_SECONDS", "30"))

_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))


# --- Browser registry -------------------------------------------------------#
# Each browser: the launch process image name (for targeted kills) + the ordered list of
# candidate install paths to probe. Both browsers are Chromium and take identical CDP +
# extension-suppression flags.
BROWSERS: dict[str, dict[str, object]] = {
    "edge": {
        "proc_name": "msedge.exe",
        "env_path": "BROWSER_AUTOMATION_EDGE_PATH",
        "candidates": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            os.path.join(_LOCALAPPDATA, r"Microsoft\Edge\Application\msedge.exe"),
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ],
        # Dedicated, isolated profile so it never collides with the user's real browser.
        "user_data_dir": os.path.join(_LOCALAPPDATA, "agent-neo-edge"),
    },
    "chrome": {
        "proc_name": "chrome.exe",
        "env_path": "BROWSER_AUTOMATION_CHROME_PATH",
        "candidates": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.join(_LOCALAPPDATA, r"Google\Chrome\Application\chrome.exe"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ],
        "user_data_dir": os.path.join(_LOCALAPPDATA, "agent-neo-chrome"),
    },
}


def known_browsers() -> list[str]:
    return list(BROWSERS.keys())


# Plain session — CDP endpoint is localhost HTTP (no TLS / no corporate proxy path).
SESSION = requests.Session()
