#!/usr/bin/env python3
"""Live smoke of agent-neo 0.4 (plan v0.4 acceptance 4): the GUI with the REAL web backend, off-screen Edge.

    .venv\\Scripts\\python.exe tests\\e2e\\live_smoke.py [--timeout 300]

Manual, run once per release (it uses the dedicated Copilot Edge profile and the live M365 Copilot):
  * refuses to start if anything listens on CDP 9365 or a dedicated-profile Edge is already running;
  * launches the GUI through run-gui.cmd with an isolated NEO_HOME / WebView2 profile / scratch workspace;
  * sends "list the files in this workspace"; approves only read-only shell commands (Get-ChildItem / dir / ls /
    Get-Content / type / Get-Item, no pipes or redirection), denies anything else;
  * samples the dedicated Edge's windows every 0.5 s and reports whether any was ever on-screen;
  * closes the GUI (which closes the Edge it launched) and checks that no dedicated-profile Edge is left.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gui_e2e as g  # noqa: E402

PROFILE_MARK = "agent-neo-edge"
READ_ONLY = re.compile(r"^\s*(Get-ChildItem|gci|dir|ls|Get-Content|gc|type|cat|Get-Item|gi)\b[^|;&><`]*$", re.I)


def dedicated_edge_pids() -> list[int]:
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{PROFILE_MARK}*' }} | ForEach-Object {{ $_.ProcessId }}")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    return [int(x) for x in out.split()]


def virtual_screen() -> tuple[int, int, int, int]:
    import win32api
    import win32con
    x, y = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN), win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    return x, y, x + win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN), y + win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)


class EdgeWatch(threading.Thread):
    """Samples the dedicated Edge's visible top-level windows; records every on-screen sighting."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.samples = 0
        self.windows_seen = 0
        self.onscreen: list[tuple[float, str, tuple]] = []
        self.offscreen_rects: set[tuple] = set()

    def run(self) -> None:
        import win32gui
        import win32process
        vx0, vy0, vx1, vy1 = virtual_screen()
        pids: list[int] = []
        last_pid_scan = 0.0
        while not self.stop.is_set():
            if time.time() - last_pid_scan > 3:
                pids, last_pid_scan = dedicated_edge_pids(), time.time()
            found = []

            def cb(h, _):
                if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] in pids:
                    found.append((h, win32gui.GetWindowText(h), win32gui.GetWindowRect(h)))
            try:
                win32gui.EnumWindows(cb, None)
            except Exception:  # noqa: BLE001
                pass
            self.samples += 1
            for h, title, (l, t, r, b) in found:
                if r - l < 50 or b - t < 50:
                    continue  # tooltips / helper windows
                self.windows_seen += 1
                if l < vx1 and r > vx0 and t < vy1 and b > vy0 and not win32gui.IsIconic(h):
                    self.onscreen.append((time.time(), title, (l, t, r, b)))
                else:
                    self.offscreen_rects.add((l, t, r, b))
            time.sleep(0.5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--port", type=int, default=9484, help="WebView2 CDP port of the GUI (not the Edge's 9365)")
    a = ap.parse_args()

    # ---- preconditions: never attach to, or recycle, someone else's browser
    if not g.port_free(9365):
        print("ABORT: something listens on CDP 9365 (another agent-neo / chat Edge instance)")
        return 2
    if dedicated_edge_pids():
        print(f"ABORT: a dedicated-profile Edge is already running: {dedicated_edge_pids()}")
        return 2
    print("precondition: CDP 9365 free and no dedicated-profile Edge running", flush=True)

    run = g.Run(g.DEFAULT_REPO, a.port, keep=True)
    run.prepare()
    (run.ws / ".agent-neo" / "neo.toml").write_text('backend = "web"\nbrowser_mode = "offscreen"\n', encoding="utf-8")
    for name in ("alpha.txt", "beta.md", "gamma.json"):
        (run.ws / name).write_text(name + "\n", encoding="utf-8")
    # no fake backend: launch run-gui.cmd without --fake-backend
    env = run.env()
    cmd = ["cmd.exe", "/c", str(run.repo / "run-gui.cmd"), "--workspace", str(run.ws)]
    watch = EdgeWatch()
    watch.start()
    t0 = time.time()
    subprocess.run(cmd, env=env, cwd=str(run.base), check=True, timeout=30)
    deadline = time.time() + 30
    while time.time() < deadline and not run.pids:
        time.sleep(1.0)
        run.pids = g.find_gui_pids(str(run.ws))
    deadline = time.time() + 90
    while time.time() < deadline and g.port_free(a.port):
        time.sleep(0.5)
    report: dict = {"scratch": str(run.base)}
    approvals: list[str] = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{a.port}")
            page = [p for c in browser.contexts for p in c.pages][0]
            page.wait_for_function("() => window.__neo && window.__neo.state && window.__neo.state.version", timeout=30000)
            st = page.evaluate("window.__neo.state")
            report["gui"] = {"title": g.window_title_of(run.pids), "backend": st["backend"], "browser_mode": st["browser_mode"],
                             "fake": st["fake"]}
            page.fill("[data-testid=prompt]", "list the files in this workspace")
            page.press("[data-testid=prompt]", "Enter")
            end = time.time() + a.timeout
            while time.time() < end:
                done = page.evaluate("() => { const t = window.__neo.model.turns.get(1); return !!t && !t.running; }")
                if done:
                    break
                pend = page.evaluate("() => { const t = window.__neo.model.turns.get(1); return t && t.pending; }")
                if pend and pend.get("id") not in approvals:
                    approvals.append(pend["id"])
                    cmdline = (pend.get("args") or {}).get("command", "")
                    ok = pend.get("kind") == "tool" and pend.get("tool") == "run_shell" and bool(READ_ONLY.match(cmdline or ""))
                    if pend.get("kind") == "tool" and pend.get("tool") in ("read_file",):
                        ok = True
                    print(f"approval {pend.get('kind')}/{pend.get('tool')}: {cmdline!r} -> {'APPROVE' if ok else 'DENY'}", flush=True)
                    page.click("[data-testid=approve]" if ok else ("[data-testid=deny]" if pend.get("kind") == "tool"
                                                                    else "[data-testid=stop-steps], [data-testid=stop-ceiling]"))
                time.sleep(0.5)
            turn = page.evaluate("() => { const t = window.__neo.model.turns.get(1); return t && {outcome: t.outcome, final: t.final,"
                                 " errors: t.errors, steps: t.steps.length, line: document.querySelector('[data-testid=steps-line]')?.innerText}; }")
            notices = page.evaluate("() => window.__neo.model.items.filter(i => i.kind === 'notice').map(i => i.level + ': ' + i.text)")
            report["turn"] = turn
            report["notices"] = notices
            report["seconds"] = round(time.time() - t0, 1)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        edge_before_close = dedicated_edge_pids()
        run.shutdown()
        time.sleep(3)
        leftover = dedicated_edge_pids()
        deadline = time.time() + 15
        while leftover and time.time() < deadline:
            time.sleep(1)
            leftover = dedicated_edge_pids()
        watch.stop.set()
        watch.join(5)
        report["edge_pids_before_close"] = edge_before_close
        report["edge_left_after_close"] = leftover
        if leftover:  # only possible leftovers are the ones this run launched (precondition: none were running)
            for pid in leftover:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        report["edge_watch"] = {"samples": watch.samples, "window_sightings": watch.windows_seen,
                                "onscreen_sightings": len(watch.onscreen), "first_onscreen": watch.onscreen[:3],
                                "offscreen_rects": sorted(watch.offscreen_rects)[:5]}
        report["approvals"] = len(approvals)
        report["checks"] = [{"check": n, "ok": ok, "detail": d} for n, ok, d in run.results]
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    final = (report.get("turn") or {}).get("final") or ""
    names = [n for n in ("alpha.txt", "beta.md", "gamma.json") if n in final]
    print(f"file names in the final answer: {names} (model compliance, not app behaviour)")
    ok = ((report.get("turn") or {}).get("outcome") == "final" and bool(final.strip())
          and not report["edge_left_after_close"] and not report.get("error"))
    print(f"\nLIVE SMOKE: {'PASS' if ok else 'FAIL'}; Edge on-screen sightings: {len(watch.onscreen)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
