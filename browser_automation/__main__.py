"""CLI entrypoint — python -m browser_automation <verb>.

Verbs: launch | status | stop | recycle. Human-readable by default; --json on every
verb for machine consumption.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import browser, runtime


def _emit(obj: dict, as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(human)


def cmd_launch(args: argparse.Namespace) -> int:
    v = browser.ensure_cdp_browser(
        browser=args.browser, port=args.port, user_data_dir=args.user_data_dir,
        start_url=args.start_url, browser_path=args.browser_path,
        wait_seconds=args.wait_seconds, force_fresh=args.force_fresh)
    udd = browser.resolve_user_data_dir(args.browser, args.user_data_dir)
    out = {
        "ok": True, "browser": args.browser, "port": args.port,
        "user_data_dir": udd, "start_url": args.start_url,
        "cdp_version": v,
        "webSocketDebuggerUrl": v.get("webSocketDebuggerUrl"),
        "endpoint": f"http://127.0.0.1:{args.port}/json/version",
    }
    _emit(out, args.json,
          f"CDP {args.browser} ready on port {args.port}: {v.get('Browser', 'unknown')}\n"
          f"  endpoint : http://127.0.0.1:{args.port}/json/version\n"
          f"  profile  : {udd}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    v = browser.cdp_version(args.port)
    ready = v is not None
    out = {
        "ready": ready, "port": args.port,
        "browser": (v or {}).get("Browser"),
        "webSocketDebuggerUrl": (v or {}).get("webSocketDebuggerUrl"),
        "endpoint": f"http://127.0.0.1:{args.port}/json/version",
    }
    if ready:
        _emit(out, args.json,
              f"READY on port {args.port}: {v.get('Browser', 'unknown')}\n"
              f"  {v.get('webSocketDebuggerUrl')}")
    else:
        _emit(out, args.json, f"NOT READY: no CDP endpoint on port {args.port}.")
    return 0 if ready else 1


def cmd_stop(args: argparse.Namespace) -> int:
    udd = browser.resolve_user_data_dir(args.browser, args.user_data_dir)
    browser.kill_cdp_browser(args.browser, user_data_dir=args.user_data_dir, port=args.port)
    still = browser.cdp_port_ready(args.port)
    out = {"ok": not still, "port": args.port, "user_data_dir": udd, "still_ready": still}
    _emit(out, args.json,
          f"Stopped instance for '{udd}' on port {args.port}. "
          f"Port ready now: {still}.")
    return 0 if not still else 1


def cmd_recycle(args: argparse.Namespace) -> int:
    v = browser.ensure_cdp_browser(
        browser=args.browser, port=args.port, user_data_dir=args.user_data_dir,
        start_url=args.start_url, browser_path=args.browser_path,
        wait_seconds=args.wait_seconds, force_fresh=True)
    udd = browser.resolve_user_data_dir(args.browser, args.user_data_dir)
    out = {"ok": True, "browser": args.browser, "port": args.port,
           "user_data_dir": udd, "cdp_version": v,
           "webSocketDebuggerUrl": v.get("webSocketDebuggerUrl")}
    _emit(out, args.json,
          f"Recycled a fresh {args.browser} on port {args.port}: {v.get('Browser', 'unknown')}")
    return 0


def _add_common(p: argparse.ArgumentParser, *, url: bool = False) -> None:
    p.add_argument("--browser", default=runtime.DEFAULT_BROWSER,
                   choices=runtime.known_browsers(),
                   help=f"Browser to drive (default: {runtime.DEFAULT_BROWSER}).")
    p.add_argument("--port", type=int, default=runtime.CDP_PORT,
                   help=f"CDP remote-debugging port (default: {runtime.CDP_PORT}).")
    p.add_argument("--user-data-dir", default=None,
                   help="Isolated profile dir (default: per-browser dir under %%LOCALAPPDATA%%).")
    p.add_argument("--browser-path", default=None,
                   help="Explicit browser executable path (default: auto-resolve).")
    p.add_argument("--wait-seconds", type=int, default=runtime.WAIT_SECONDS,
                   help=f"Seconds to wait for CDP to come up (default: {runtime.WAIT_SECONDS}).")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    if url:
        p.add_argument("--start-url", default="about:blank",
                       help="Initial URL to open (default: about:blank).")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m browser_automation",
        description="Launch/manage an interactive, CDP-enabled automation browser "
                    "(Edge default, Chrome optional) with extensions suppressed at launch.")
    sub = ap.add_subparsers(dest="verb", required=True)

    p_launch = sub.add_parser("launch",
                              help="Ensure a CDP browser is listening (launch if needed).")
    _add_common(p_launch, url=True)
    p_launch.add_argument("--force-fresh", action="store_true",
                          help="Recycle any existing instance (kill + relaunch) first.")
    p_launch.set_defaults(func=cmd_launch)

    p_status = sub.add_parser("status", help="Report whether a CDP endpoint is ready.")
    _add_common(p_status)
    p_status.set_defaults(func=cmd_status)

    p_stop = sub.add_parser("stop", help="Kill the dedicated instance for a profile/port.")
    _add_common(p_stop)
    p_stop.set_defaults(func=cmd_stop)

    p_recycle = sub.add_parser("recycle",
                               help="Kill + relaunch a fresh instance (force_fresh).")
    _add_common(p_recycle, url=True)
    p_recycle.set_defaults(func=cmd_recycle)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
