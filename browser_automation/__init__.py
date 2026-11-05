"""browser_automation — launch an interactive, CDP-enabled browser for automation.

Public API (import path: ``browser_automation.browser``):

    EXTENSION_SUPPRESSION_FLAGS   flags splatted into every launch
    resolve_browser_path(...)     locate msedge.exe / chrome.exe
    cdp_port_ready(port)          poll http://127.0.0.1:<port>/json/version
    cdp_version(port)             return the /json/version payload (Browser string, ws url)
    ensure_cdp_browser(...)       launch-if-not-listening, force_fresh recycle
    kill_cdp_browser(...)         kill the dedicated instance for a user-data-dir
"""
from __future__ import annotations

from . import runtime  # noqa: F401  (re-exported for convenience)
from .browser import (  # noqa: F401
    EXTENSION_SUPPRESSION_FLAGS,
    cdp_port_ready,
    cdp_version,
    ensure_cdp_browser,
    kill_cdp_browser,
    resolve_browser_path,
    resolve_user_data_dir,
)

__all__ = [
    "runtime",
    "EXTENSION_SUPPRESSION_FLAGS",
    "cdp_port_ready",
    "cdp_version",
    "ensure_cdp_browser",
    "kill_cdp_browser",
    "resolve_browser_path",
    "resolve_user_data_dir",
]
