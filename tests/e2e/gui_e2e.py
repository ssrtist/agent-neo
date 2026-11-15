#!/usr/bin/env python3
"""GUI end-to-end test of agent-neo 0.4 (plan v0.4 acceptance 3), against the fake driver.

    .venv\\Scripts\\python.exe tests\\e2e\\gui_e2e.py [--repo DIR] [--port 9482] [--keep] [--json OUT]

Launches the GUI through run-gui.cmd (pythonw, windowless) with an isolated NEO_HOME,
WEBVIEW2_USER_DATA_FOLDER, workspace and CDP port, drives the page with Playwright over CDP and
prints one line per named check. Links are never really opened (NEO_TEST_NO_OPEN=1: the
open_external command records instead, read back through the test_opened command). At the end
the window is closed (WM_CLOSE), and only the process tree this run started is killed if it
does not exit. Exit code 0 only when every check passed.

``--repo`` points at another checkout (tests/negative_controls_gui.py runs mutated copies).
Not part of `unittest discover` (it starts a real window and takes about a minute).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parent.parent

SCRIPT = {
    "scenarios": [
        {"match": "^list", "replies": [
            {"reply": {"type": "tool", "name": "run_shell", "arguments": {"command": "Get-ChildItem -Name"},
                       "thought": "THOUGHT-MARKER list the workspace first."}, "delay": 0.3},
            {"reply": {"type": "final", "thought": "THOUGHT-MARKER-2 done.", "message":
                "FINAL-MARKER here are the files.\n\n| Item | Value |\n|---|---|\n| docs | https://example.com/docs |\n\n"
                "See [the readme](readme.txt) and `C:\\Windows\\win.ini`.\n\n```powershell\nGet-ChildItem -Name\n```\n\n"
                "INJECT ![beacon](__BEACON__/md-img) <img src=\"__BEACON__/raw-img\"> "
                "<span style=\"color: rgb(255, 0, 0)\">STYLED-MARKER</span>\n\n<style>.askcard{display:none}</style>\n\n"
                "<div class=\"modal\"><div class=\"box\"><p class=\"text\">PHISH-MARKER session expired</p></div></div>\n\n"
                "<span id=\"stop\" name=\"stop\"></span><span id=\"hint\">HINT-HIJACK</span>\n\n"
                "<label for=\"yolo\">LABEL-MARKER click anywhere here</label>\n"},
             "delay": 0.3},
        ]},
        {"match": "^deny", "replies": [
            {"reply": {"type": "tool", "name": "run_shell", "arguments": {"command": "Remove-Item readme.txt"}}, "delay": 0.2},
            {"reply": {"type": "final", "message": "DENY-FINAL the user said no."}, "delay": 0.2},
        ]},
        {"match": "^slow", "replies": [{"reply": {"type": "final", "message": "SLOW-FINAL should never show"}, "delay": 30}]},
        {"match": "^two", "replies": [
            {"reply": {"type": "tool", "name": "run_shell", "arguments": {"command": "Write-Output one"}}, "delay": 0.2},
            {"reply": {"type": "tool", "name": "run_shell", "arguments": {"command": "Write-Output two"}}, "delay": 0.2},
            {"reply": {"type": "final", "message": "TWO-FINAL"}, "delay": 0.2},
        ]},
        {"match": "^delegate", "replies": [
            {"reply": {"type": "tool", "name": "run_subagent", "arguments": {"task": "child: say hello", "context": "test"}}, "delay": 0.2},
            {"reply": {"type": "final", "message": "PARENT-FINAL after the sub-agent"}, "delay": 0.2},
        ]},
        {"match": "^child", "replies": [
            {"reply": {"type": "final", "message": "CHILD-FINAL hello"}, "delay": 0.2},
        ]},
        {"match": "^drag", "replies": [{"reply": {"type": "final", "message": "DRAG-FINAL got it"}, "delay": 0.2}]},
    ],
    "fallback": "{\"type\":\"final\",\"message\":\"(fake) no scripted reply left\"}",
}

STRIP_ENV = ("COPILOT_AGENT_SESSION_ID", "COPILOT_CUSTOM_INSTRUCTIONS_DIRS")


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class Beacon:
    """A local HTTP server that records every request: anything the page loads from it is a leak."""

    def __init__(self) -> None:
        import http.server
        hits = self.hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                hits.append(self.path)
                self.send_response(204)
                self.end_headers()
            do_POST = do_GET  # noqa: N815

            def log_message(self, *a):  # noqa: D401
                pass
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Run:
    def __init__(self, repo: Path, port: int, keep: bool) -> None:
        self.repo = repo
        self.port = port
        self.keep = keep
        self.base = Path(tempfile.mkdtemp(prefix="neo-e2e-")).resolve()  # resolve: %TEMP% can be an 8.3 path
        self.home = self.base / "home"
        self.wv2 = self.base / "wv2"
        self.ws = self.base / "ws"
        self.results: list[tuple[str, bool, str]] = []
        self.pids: list[int] = []   # the venv launcher and its interpreter child

    # ------------------------------------------------------------ checks
    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, bool(ok), detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
        return bool(ok)

    # ------------------------------------------------------------ setup / launch
    def prepare(self) -> None:
        for d in (self.home, self.wv2, self.ws / ".agent-neo", self.ws / "sub"):
            d.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            # Python 3.12+ mkdtemp() strips inherited ACLs via mode 0o700 (OWNER RIGHTS);
            # when run elevated, WebView2 child processes run under the user SID and need inherited access.
            subprocess.run(["icacls", str(self.base), "/reset", "/T", "/Q"], capture_output=True)
            user = os.environ.get("USERNAME", "").strip()
            if user:
                subprocess.run(["icacls", str(self.base), "/grant", f"{user}:(OI)(CI)F", "/Q"], capture_output=True)
        (self.ws / "readme.txt").write_text("read me\n", encoding="utf-8")
        (self.ws / "sub" / "note.md").write_text("# note\n", encoding="utf-8")
        (self.ws / "help.chm").write_bytes(b"not really a chm")
        # an unknown key: its warning must be shown at startup
        (self.ws / ".agent-neo" / "neo.toml").write_text("enable_subagents = true\ncolour = \"blue\"\n", encoding="utf-8")
        # a second workspace whose toml turns yolo on: a switch into it must not
        self.ws2 = self.base / "ws2"
        (self.ws2 / ".agent-neo").mkdir(parents=True, exist_ok=True)
        (self.ws2 / ".agent-neo" / "neo.toml").write_text("auto_approve = true\nbackend = \"desktop\"\n", encoding="utf-8")
        self.beacon = Beacon()
        (self.base / "script.json").write_text(json.dumps(SCRIPT).replace("__BEACON__", self.beacon.url), encoding="utf-8")

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
        env.update({
            "NEO_HOME": str(self.home),
            "WEBVIEW2_USER_DATA_FOLDER": str(self.wv2),
            "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={self.port}",
            "NEO_TEST_NO_OPEN": "1",
            "NEO_GUI_TRACE": str(self.base / "run-events.txt"),
            "PYTHONIOENCODING": "utf-8",
        })
        env.pop("NEO_FAKE_SCRIPT", None)
        env.pop("NEO_ROOT", None)
        return env

    def launch(self) -> None:
        if not port_free(self.port):
            raise RuntimeError(f"CDP port {self.port} is in use; refusing to attach to someone else's browser")
        cmd = ["cmd.exe", "/c", str(self.repo / "run-gui.cmd"), "--fake-backend", str(self.base / "script.json"),
               "--workspace", str(self.ws)]
        subprocess.run(cmd, env=self.env(), cwd=str(self.base), check=True, timeout=30)
        deadline = time.time() + 30
        while time.time() < deadline and not self.pids:
            time.sleep(1.0)
            self.pids = find_gui_pids(str(self.ws))
        if not self.pids:
            raise RuntimeError("the GUI process did not start (no pythonw neo_gui.py with this workspace)")
        deadline = time.time() + 90
        while time.time() < deadline and port_free(self.port):
            if not any_alive(self.pids):
                raise RuntimeError("the GUI exited during start-up; see " + str(self.home / "gui-errors.log"))
            time.sleep(0.5)
        if port_free(self.port):
            raise RuntimeError("WebView2 CDP port never opened")

    def shutdown(self) -> None:
        if not self.pids:
            return
        self.pids = sorted(set(self.pids) | set(find_gui_pids(str(self.ws))))
        closed = close_window_of(self.pids)
        deadline = time.time() + 25
        while time.time() < deadline and any_alive(self.pids):
            time.sleep(0.5)
        if any_alive(self.pids):
            for pid in self.pids:   # only this run's processes (matched by its scratch workspace)
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            self.check("GUI exits on window close", False, "had to kill the process tree")
        else:
            self.check("GUI exits on window close", closed, "WM_CLOSE" if closed else "window not found")
        trace = self.base / "run-events.txt"
        text = trace.read_text(encoding="utf-8", errors="replace") if trace.exists() else ""
        self.check("window close shuts the session down (worker stopped)", "shutdown complete (worker alive: False)" in text,
                   text.strip().splitlines()[-1] if text.strip() else "no trace")
        # Tauri's own OS drop handler swallows in-page drag-and-drop. CDP drags bypass the OS, so
        # the behaviour cannot be seen from here; check the window config the app actually built instead.
        cfg = next((ln for ln in text.splitlines() if ln.startswith("window config: ")), "")
        self.check("window built with dragDropEnabled=false (config, from the trace)", '"dragDropEnabled": false' in cfg,
                   cfg[:120] or "no window config traced")
    # ------------------------------------------------------------ the test
    def run(self) -> int:
        from playwright.sync_api import sync_playwright
        real_home = Path.home() / ".agent-neo"
        before = snapshot(real_home)
        self.prepare()
        try:
            self.launch()
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
                page = [p for c in browser.contexts for p in c.pages][0]
                self.scenarios(page, browser)
                browser.close()
        except Exception as exc:  # noqa: BLE001
            self.check("harness", False, f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        finally:
            self.shutdown()
            if getattr(self, "beacon", None):
                self.beacon.close()
            errlog = self.home / "gui-errors.log"
            self.check("no GUI errors logged", not errlog.exists(),
                       errlog.read_text(encoding="utf-8", errors="replace")[-400:] if errlog.exists() else "")
            self.check("real ~/.agent-neo untouched", snapshot(real_home) == before)
            sessions = list((self.home / "sessions").glob("*.jsonl")) if (self.home / "sessions").exists() else []
            self.check("session log written in the isolated home", bool(sessions), f"{len(sessions)} file(s)")
        failed = [r for r in self.results if not r[1]]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed; scratch: {self.base}")
        return 1 if failed else 0

    def scenarios(self, page, browser) -> None:
        ipc: list[str] = []
        page.on("request", lambda r: ipc.append(r.headers.get("pyfunc", "")) if "ipc.localhost" in r.url else None)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.wait_for_function("() => window.__neo && window.__neo.state && window.__neo.state.version", timeout=30000)
        version = page.evaluate("window.__neo.state.version")
        title = window_title_of(self.pids)
        self.check("window title shows the version", title == f"Agent Neo (agent-neo) {version}" and version == TITLE_VERSION,
                   repr(title))
        self.check("fake backend badge shown", page.is_visible("#fake"))
        page.wait_for_timeout(300)
        self.check("startup settings warnings shown", "unknown key 'colour'" in page.inner_text("body"))

        def turn_done(n: int) -> None:
            page.wait_for_function(
                f"() => {{ const t = window.__neo.model.turns.get({n}); return t && !t.running && !window.__neo.state.running; }}",
                timeout=30000)

        def send(text: str) -> None:
            page.fill("[data-testid=prompt]", text)
            page.press("[data-testid=prompt]", "Enter")

        def turn(n: int):
            return page.locator(f"section.turn[data-turn='{n}']")

        def opened() -> list:
            return page.evaluate("window.__TAURI__.pytauri.pyInvoke('test_opened', {})")

        # ---- turn 1: tool call -> approval card -> approve -> final
        send("list the files in this workspace")
        page.wait_for_selector("[data-testid=askcard]", state="attached", timeout=20000)  # visibility is its own check
        t1 = turn(1)
        self.check("request visible while the turn runs", t1.locator("[data-testid=user-msg]").is_visible()
                   and "list the files" in t1.locator("[data-testid=user-msg]").inner_text())
        self.check(
            "Stop shown and switch-account disabled while running",
            page.is_visible("[data-testid=stop]")
            and not page.is_disabled("[data-testid=send]")
            and page.is_disabled("[data-testid=switch-account]"),
        )
        card = t1.locator("[data-testid=askcard]")
        self.check("approval card visible while steps are collapsed",
                   card.is_visible() and not t1.locator("[data-testid=steps]").evaluate("d => d.open")
                   and card.evaluate("c => c.closest('details') === null"))
        live = t1.locator("[data-testid=steps-line]").inner_text()
        self.check("live line names the step, tool and wait", "Step 1/10" in live and "run_shell" in live and "approval" in live, live)
        page.click("[data-testid=approve]")
        turn_done(1)
        self.check("approval card gone after answering", t1.locator("[data-testid=askcard]").count() == 0)
        final = t1.locator("[data-testid=final]")
        self.check("final answer visible", final.is_visible() and "FINAL-MARKER" in final.inner_text())
        self.check("final is outside the rolled-up steps", final.evaluate("f => f.closest('details') === null"))
        self.check("request still visible after the turn", t1.locator("[data-testid=user-msg]").is_visible())
        # model output is untrusted: raw <style>/<img>/style= are dropped, a markdown image becomes a link, nothing loads
        page.wait_for_timeout(500)
        self.check("answer HTML sanitised: no <style>, <img> or style= from the model",
                   final.locator("style, img, [style]").count() == 0 and "STYLED-MARKER" in final.inner_text()
                   and final.locator("a.art-url", has_text="beacon (image)").count() == 1,
                   f"{final.locator('style, img, [style]').count()} element(s)")
        self.check("model HTML cannot borrow the app's classes (no fake modal overlay)",
                   "PHISH-MARKER" in final.inner_text() and final.locator(".modal, .box, [class='text']").count() == 0
                   and page.evaluate("() => [...document.querySelectorAll('[data-testid=final] *')].every("
                                     "e => getComputedStyle(e).position !== 'fixed')"),
                   f"{final.locator('.modal, .box').count()} borrowed")
        self.check("nothing loaded from the answer (no image beacon)", self.beacon.hits == [], repr(self.beacon.hits))
        final.get_by_text("LABEL-MARKER").click()
        page.wait_for_timeout(500)
        self.check("clicking an answer's <label for=yolo> does not touch the yolo button",
                   page.evaluate("window.__neo.state.yolo") is False and final.locator("label, [for]").count() == 0,
                   f"yolo={page.evaluate('window.__neo.state.yolo')}")
        page.evaluate("(u) => { new Image().src = u + '/csp-img'; fetch(u + '/csp-fetch', {mode: 'no-cors'}).catch(() => {}); }",
                      self.beacon.url)
        page.wait_for_timeout(800)
        self.check("CSP blocks remote loads from the page", not [h for h in self.beacon.hits if h.startswith("/csp-")],
                   repr(self.beacon.hits))
        steps = t1.locator("[data-testid=steps]")
        self.check("one rolled-up steps element per turn", steps.count() == 1)
        line = t1.locator("[data-testid=steps-line]")
        box = line.bounding_box() or {"height": 999}
        lh = line.evaluate("e => parseFloat(getComputedStyle(e).lineHeight) || 18")
        self.check("steps rolled up to one line", "\n" not in line.inner_text() and box["height"] <= lh * 1.5
                   and not steps.evaluate("d => d.open") and not t1.locator("[data-testid=step]").first.is_visible(),
                   f"{line.inner_text()!r}, h={box['height']:.0f}")
        self.check("thinking hidden by default", page.locator("[data-testid=thought]").count() == 0
                   and "THOUGHT-MARKER" not in page.inner_text("body"))
        t1.locator("[data-testid=steps] > summary").click()
        self.check("steps expand on click", steps.evaluate("d => d.open") and t1.locator("[data-testid=step]").count() == 2
                   and t1.locator("[data-testid=step]").first.is_visible())
        t1.locator("[data-testid=step]").first.locator(":scope > summary").click()
        body = t1.locator("[data-testid=step]").first.inner_text()
        self.check("a step expands to its args, approval and result", "Get-ChildItem -Name" in body and "approved" in body
                   and "readme.txt" in body, body[:120].replace("\n", " | "))
        page.click("[data-testid=thinking]")
        page.wait_for_function("() => window.__neo.state.show_thinking === true")
        self.check("thinking toggle shows thought lines", page.locator("[data-testid=thought]").count() == 2
                   and "THOUGHT-MARKER" in t1.inner_text())
        page.click("[data-testid=thinking]")
        page.wait_for_function("() => window.__neo.state.show_thinking === false")
        self.check("thinking toggle hides them again", page.locator("[data-testid=thought]").count() == 0)
        self.check("expanded state survives a re-render", steps.evaluate("d => d.open"))
        self.check("markdown table and highlighted code", final.locator("table").count() == 1
                   and final.locator("code.hljs span.hljs-built_in").count() >= 1 and final.locator(".codewrap .codetools").count() == 1)

        # ---- links route to open_external (intercepted)
        url_before = page.url
        n_pages = sum(len(c.pages) for c in browser.contexts)
        ipc.clear()
        final.locator("a.art-url").first.click()
        page.wait_for_timeout(500)
        rec = opened()
        self.check("http link click routes to open_external", "open_external" in ipc
                   and rec[-1:] == [{"kind": "url", "target": "https://example.com/docs"}], json.dumps(rec[-1:]))
        self.check("the page did not navigate or open a window", page.url == url_before
                   and sum(len(c.pages) for c in browser.contexts) == n_pages)
        final.locator("a.art-file", has_text="the readme").click()
        page.wait_for_timeout(400)
        rec = opened()
        self.check("workspace file link opens the file", rec[-1:] == [{"kind": "file", "target": str(self.ws / "readme.txt"), "reveal": False}],
                   json.dumps(rec[-1:]))
        final.locator("a.art-file", has_text="the readme").click(modifiers=["Control"])
        page.wait_for_timeout(400)
        rec = opened()
        self.check("Ctrl+click reveals in Explorer", rec[-1:] == [{"kind": "file", "target": str(self.ws / "readme.txt"), "reveal": True}],
                   json.dumps(rec[-1:]))
        count = len(rec)
        final.locator("a.art-file", has_text="win.ini").click()
        try:
            page.wait_for_selector("[data-testid=confirm]", timeout=5000)
            asked = True
        except Exception:
            asked = False
        if asked:
            page.click("[data-testid=confirm-no]")
        page.wait_for_timeout(300)
        self.check("outside-workspace file asks first; Cancel opens nothing", asked and len(opened()) == count,
                   "" if asked else "no confirm dialog")
        count = len(opened())
        final.locator("a.art-file", has_text="win.ini").click()
        page.wait_for_selector("[data-testid=confirm]", timeout=5000)
        page.click("[data-testid=confirm-yes]")
        page.wait_for_timeout(400)
        rec = opened()
        self.check("confirmed outside file opens", len(rec) == count + 1 and rec[-1]["target"].lower() == r"c:\windows\win.ini")
        r = page.evaluate("window.__TAURI__.pytauri.pyInvoke('open_external', {target: 'javascript:alert(1)', kind: 'url'})")
        r2 = page.evaluate("window.__TAURI__.pytauri.pyInvoke('open_external', {target: 'ms-settings:privacy', kind: ''})")
        self.check("unsafe schemes refused", r.get("ok") is False and r2.get("ok") is False and len(opened()) == count + 1,
                   f"{r} {r2}")
        unc = [page.evaluate("(t) => window.__TAURI__.pytauri.pyInvoke('open_external', {target: t, kind: 'file'})", t)
               for t in ("\\\\127.0.0.1\\no-such-share\\x.txt", "//127.0.0.1/no-such-share/x.txt", "file://127.0.0.1/no-such-share/x.txt")]
        self.check("UNC paths refused before touching the network", all(x.get("error") == "unc" for x in unc)
                   and len(opened()) == count + 1, json.dumps(unc))
        chm = page.evaluate("window.__TAURI__.pytauri.pyInvoke('open_external', {target: 'help.chm', kind: 'file'})")
        txt = page.evaluate("window.__TAURI__.pytauri.pyInvoke('open_external', {target: 'sub/note.md', kind: 'file'})")
        self.check("workspace file of a non-document type asks first; a document opens", chm.get("confirm") is True
                   and txt.get("ok") is True and len(opened()) == count + 2, f"{chm} {txt}")

        # ---- drag a link into the composer -> chip, focus back in the composer
        page.click("[data-testid=conversation]", position={"x": 5, "y": 5})
        self.check("(precondition) composer not focused before the drag", page.evaluate("document.activeElement.id") != "prompt")
        final.locator("a.art-url").first.drag_to(page.locator("[data-testid=prompt]"))
        page.wait_for_timeout(300)
        chips = page.locator("[data-testid=chip]")
        self.check("drag link -> chip in the composer", chips.count() == 1 and chips.first.get_attribute("data-kind") == "url"
                   and "example.com/docs" in chips.first.inner_text(), f"{chips.count()} chip(s)")
        self.check("focus restored to the composer after the drop", page.evaluate("document.activeElement.id") == "prompt"
                   and page.evaluate("document.activeElement.selectionStart === document.activeElement.value.length"))
        # a workspace file link carries its path (a bare <a> drag would only carry its href)
        final.locator("a.art-file", has_text="the readme").drag_to(page.locator("[data-testid=prompt]"))
        page.wait_for_timeout(300)
        fchip = page.locator("[data-testid=chip][data-kind=file]")
        self.check("drag file link -> file chip with its path", chips.count() == 2 and fchip.count() == 1
                   and "readme.txt" in fchip.first.inner_text(), f"{chips.count()} chip(s)")
        # external drops (synthetic: an OS drag cannot be scripted): a text file from Explorer, a URL, plain text
        page.evaluate("""() => {
            const fire = (dt) => document.getElementById('composer').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
            let dt = new DataTransfer(); dt.items.add(new File(['alpha beta'], 'notes.txt', {type: 'text/plain'})); fire(dt);
            dt = new DataTransfer(); dt.setData('text/uri-list', 'https://example.org/x'); fire(dt);
            dt = new DataTransfer(); dt.setData('text/plain', 'PLAIN-DROP'); fire(dt);
        }""")
        page.wait_for_timeout(300)
        kinds = sorted(chips.nth(i).get_attribute("data-kind") for i in range(chips.count()))  # the file is read async
        self.check("external drops: file -> chip, URL -> chip, text -> inserted", kinds == ["file", "filetext", "url", "url"]
                   and "PLAIN-DROP" in page.input_value("[data-testid=prompt]"), f"{kinds}")
        page.fill("[data-testid=prompt]", "drag test")
        page.press("[data-testid=prompt]", "Enter")
        turn_done(2)
        u2 = turn(2).locator("[data-testid=user-msg]").inner_text()
        self.check("chips expand into the sent prompt", "Context items attached by the user:" in u2 and "- https://example.com/docs" in u2
                   and "alpha beta" in u2 and "@" + str(self.ws / "readme.txt") in u2 and chips.count() == 0, u2[:160].replace("\n", " | "))

        # ---- deny
        send("deny this removal")
        page.wait_for_selector("section.turn[data-turn='3'] [data-testid=askcard]", timeout=20000)
        page.click("[data-testid=deny]")
        turn_done(3)
        t3 = turn(3)
        t3.locator("[data-testid=steps] > summary").click()
        t3.locator("[data-testid=step]").first.locator(":scope > summary").click()
        self.check("deny: tool not run, result says denied", (self.ws / "readme.txt").exists()
                   and "DENIED BY USER" in t3.locator("[data-testid=step]").first.inner_text()
                   and "denied" in t3.locator("[data-testid=step]").first.inner_text())

        # ---- stop a running turn
        send("slow request")
        page.wait_for_function("() => window.__neo.state.running === true", timeout=10000)
        page.wait_for_timeout(800)
        self.check("an answer's HTML cannot take over the Stop button (ids stripped)",
                   page.locator("[data-testid=final] [id]").count() == 0 and page.is_visible("[data-testid=stop]")
                   and "HINT-HIJACK" not in page.inner_text("#hint"),
                   f"{page.locator('[data-testid=final] [id]').count()} id(s) in answers")
        page.click("[data-testid=stop]", timeout=5000)
        turn_done(4)
        t4 = turn(4)
        self.check("Stop cancels the turn", "Stopped" in t4.inner_text() and "SLOW-FINAL" not in t4.inner_text()
                   and page.is_enabled("[data-testid=send]"), t4.locator("[data-testid=steps-line]").inner_text())

        # ---- step threshold card via /steps 1
        # type-ahead race: text typed while a slash command's reply is in flight must survive the reply. Typing in the
        # same JS task as the Enter makes the ordering deterministic (the reply cannot arrive before it).
        page.evaluate("""() => {
            const ta = document.getElementById('prompt');
            ta.value = '/steps 1';
            ta.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));
            ta.value = 'TYPED-AHEAD';
        }""")
        page.wait_for_function("() => window.__neo.state.step_chunk === 1", timeout=5000)
        page.wait_for_timeout(600)
        self.check("a slash command's reply does not wipe text typed after it",
                   page.input_value("[data-testid=prompt]") == "TYPED-AHEAD", repr(page.input_value("[data-testid=prompt]")))
        send("two steps please")
        page.wait_for_selector("section.turn[data-turn='5'] [data-testid=askcard][data-kind=tool]", timeout=20000)
        page.click("[data-testid=approve]")
        page.wait_for_selector("section.turn[data-turn='5'] [data-testid=askcard][data-kind=steps]", timeout=20000)
        self.check("step-threshold card shown", "Continue" in turn(5).locator("[data-testid=askcard]").inner_text())
        page.click("[data-testid=continue]")
        page.wait_for_selector("section.turn[data-turn='5'] [data-testid=askcard][data-kind=tool]", timeout=20000)
        page.click("[data-testid=approve]")
        page.wait_for_selector("section.turn[data-turn='5'] [data-testid=askcard][data-kind=steps]", timeout=20000)
        page.click("[data-testid=stop-steps]")
        turn_done(5)
        self.check("Stop at the threshold ends the turn without a final", turn(5).locator("[data-testid=final]").count() == 0
                   and "Stopped" in turn(5).inner_text())
        send("/steps 10")
        page.wait_for_function("() => window.__neo.state.step_chunk === 10", timeout=5000)

        # ---- sub-agent nests under its step
        send("delegate to a helper")
        turn_done(6)
        t6 = turn(6)
        t6.locator("[data-testid=steps] > summary").click()
        t6.locator("[data-testid=step]").first.locator(":scope > summary").click()
        sub = t6.locator("[data-testid=subagent]")
        self.check("sub-agent activity nests under its run_subagent step", sub.count() == 1
                   and "CHILD-FINAL" in sub.inner_text() and "PARENT-FINAL" in t6.locator("[data-testid=final]").inner_text())

        # ---- slash commands and notices
        send("/yolo")
        page.wait_for_function("() => window.__neo.state.yolo === true", timeout=5000)
        self.check("/yolo toggles and reports", "YOLO mode ENABLED" in page.inner_text("[data-testid=conversation]"))
        send("/yolo")
        page.wait_for_function("() => window.__neo.state.yolo === false", timeout=5000)
        page.locator("#yolo").click()
        page.wait_for_function("() => window.__neo.state.yolo === true", timeout=5000)
        page.wait_for_timeout(300)
        self.check("the header yolo button turning yolo ON posts a notice",
                   "Auto-approve (yolo) is ON" in page.inner_text("[data-testid=conversation]"))
        page.locator("#yolo").click()
        page.wait_for_function("() => window.__neo.state.yolo === false", timeout=5000)
        backend_before = page.evaluate("window.__neo.state.backend")
        r = page.evaluate("(p) => window.__TAURI__.pytauri.pyInvoke('set_workspace', {path: p})", str(self.ws2))
        page.wait_for_timeout(400)
        st = page.evaluate("window.__neo.state")
        self.check("a workspace switch keeps yolo off even if that workspace's toml turns it on",
                   r.get("ok") is True and st["workspace"].lower() == str(self.ws2).lower() and st["yolo"] is False
                   and "NOT applied on a workspace switch" in page.inner_text("[data-testid=conversation]"), f"{r} yolo={st['yolo']}")
        self.check("a workspace switch with a live driver keeps the header on the active backend and says so",
                   backend_before != "desktop" and st["backend"] == backend_before
                   and "Settings say backend = desktop" in page.inner_text("[data-testid=conversation]"),
                   f"before={backend_before} after={st['backend']}")
        page.evaluate("(p) => window.__TAURI__.pytauri.pyInvoke('set_workspace', {path: p})", str(self.ws))

        # ---- chat content zoom (docs/PLAN-zoom.md)
        z = lambda: page.evaluate('window.__neo.zoom')
        lvl = lambda: page.inner_text('[data-testid=zoom-level]')
        css = lambda sel, prop: page.evaluate('([s, p]) => getComputedStyle(document.querySelector(s))[p]', [sel, prop])
        head_fs = css('.convhead .title', 'fontSize')
        dpr = page.evaluate('window.devicePixelRatio')
        self.check('zoom defaults to 100%', z() == 100 and lvl() == '100%', lvl())
        tips = (page.get_attribute('#zoom-out', 'title') or '') + (page.get_attribute('#zoom-in', 'title') or '')
        self.check('zoom buttons named, with shortcut tooltips', page.get_attribute('#zoom-out', 'aria-label') == 'Zoom out'
                   and page.get_attribute('#zoom-in', 'aria-label') == 'Zoom in' and 'Ctrl+-' in tips and 'Ctrl+=' in tips, tips)
        page.focus('[data-testid=prompt]')
        page.keyboard.press('Control+Equal')
        self.check('Ctrl+= zooms in (focus in the composer)', z() == 110 and lvl() == '110%', lvl())
        page.keyboard.press('Control+Shift+Equal')
        self.check('Ctrl++ zooms in', z() == 120, str(z()))
        page.keyboard.press('Control+Minus')
        self.check('Ctrl+- zooms out', z() == 110, str(z()))
        for _ in range(8):
            page.keyboard.press('Control+Equal')
        self.check('zoom clamps at 150%; + disabled', z() == 150 and page.is_disabled('[data-testid=zoom-in]')
                   and page.is_enabled('[data-testid=zoom-out]'), str(z()))
        cz, pf, hf = css('.conv > *', 'zoom'), css('#prompt', 'fontSize'), css('.convhead .title', 'fontSize')
        self.check('content scales, header and chrome stay 100%', cz == '1.5' and pf == '19.5px' and hf == head_fs
                   and css('.convhead', 'zoom') == '1' and css('.composer', 'zoom') == '1', f'content={cz} prompt={pf} head={hf}')
        self.check('native WebView zoom suppressed', page.evaluate('window.devicePixelRatio') == dpr)
        page.click('[data-testid=zoom-out]')
        self.check('toolbar - steps 10%', z() == 140 and lvl() == '140%', lvl())
        page.keyboard.press('Control+Digit0')
        self.check('Ctrl+0 resets to 100%', z() == 100 and lvl() == '100%', lvl())
        for _ in range(5):
            page.keyboard.press('Control+Minus')
        self.check('zoom clamps at 80%; - disabled', z() == 80 and page.is_disabled('[data-testid=zoom-out]'), str(z()))
        self.check('percentage label is not a button', page.evaluate("document.getElementById('zoom-level').tagName") == 'SPAN')
        page.keyboard.press('Control+Digit0')
        for _ in range(3):
            page.keyboard.press('Control+Equal')
        page.reload()
        page.wait_for_function('() => window.__neo && window.__neo.zoom !== undefined', timeout=30000)
        self.check('zoom persists across reload', z() == 130 and lvl() == '130%', lvl())
        bad = []
        for v in ('135', 'abc', '160', '70', ''):
            page.evaluate("(v) => localStorage.setItem('neo.contentZoom', v)", v)
            page.reload()
            page.wait_for_function('() => window.__neo && window.__neo.zoom !== undefined', timeout=30000)
            if z() != 100:
                bad.append(repr(v) + '->' + str(z()))
        self.check('invalid saved zoom normalises to 100%', not bad, '; '.join(bad))

        # ---- v0.5 D4: colour theme (default GitHub Dark High Contrast, header dropdown, persisted)
        page.evaluate("localStorage.removeItem('neo.theme')")
        page.reload()
        page.wait_for_function('() => window.__neo && window.__neo.state', timeout=30000)
        th = lambda: page.evaluate("document.documentElement.getAttribute('data-theme')")
        bg = lambda: css('body', 'backgroundColor')
        self.check('theme defaults to Dark High Contrast (#010409 bg, #f0f3f6 fg)',
                   th() == 'dark-high-contrast' and bg() == 'rgb(1, 4, 9)' and css('body', 'color') == 'rgb(240, 243, 246)'
                   and page.input_value('[data-testid=theme-toggle]') == 'dark-high-contrast', f'{th()} {bg()}')
        cssvar = lambda v: page.evaluate("(v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()", v)
        self.check('high-contrast accent/line/ok/err colours', [cssvar(v) for v in ('--acc', '--line', '--ok', '--err')]
                   == ['#71b7ff', '#30363d', '#26cd4d', '#ff9492'], cssvar('--acc'))
        opts = page.evaluate("[...document.querySelectorAll('#theme-toggle option')].map(o => o.value)")
        self.check('theme switcher offers the three themes', opts == ['dark-high-contrast', 'light', 'dayfox'], str(opts))
        page.select_option('[data-testid=theme-toggle]', 'light')
        self.check('switch to light applies at once', th() == 'light' and bg() == 'rgb(247, 248, 250)', bg())
        page.select_option('[data-testid=theme-toggle]', 'dayfox')
        page.reload()
        page.wait_for_function('() => window.__neo && window.__neo.state', timeout=30000)
        self.check('theme persists across reload', th() == 'dayfox' and bg() == 'rgb(246, 242, 238)'
                   and page.input_value('[data-testid=theme-toggle]') == 'dayfox', f'{th()} {bg()}')
        page.evaluate("localStorage.setItem('neo.theme', 'neon')")
        page.reload()
        page.wait_for_function('() => window.__neo && window.__neo.state', timeout=30000)
        self.check('invalid saved theme falls back to Dark High Contrast', th() == 'dark-high-contrast', th())
        hdr = page.evaluate("[...document.querySelector('.convhead').children].map(e => e.id || e.className || e.tagName)")
        self.check(
            'header and toolbelt controls are properly grouped (ribbon + toolbelt-left)',
            page.evaluate("document.querySelector('.convhead .zoom-control').parentElement.className") == 'convhead'
            and page.evaluate("document.getElementById('thinking').parentElement.className") == 'toolbelt-left'
            and page.evaluate("document.getElementById('switch-account').parentElement.className") == 'toolbelt-left',
            str(hdr),
        )

        # ---- v0.5 D1: a raw Windows path renders with every backslash and as one file link
        home = r"C:\Users\alice.smith\_neo-home"
        r = page.evaluate("""async (p) => { const m = await import('./lib/md.js'); const d = document.createElement('div');
            d.innerHTML = m.renderMd('Home: ' + p + '. ok'); m.linkify(d, ''); const a = d.querySelector('a[data-art^="file|"]');
            return { text: d.textContent, art: a && a.getAttribute('data-art'), label: a && a.textContent }; }""", home)
        self.check('raw path with \\_ keeps its backslash and links whole', home in r['text'] and r['art'] == 'file|' + home
                   and r['label'] == home, str(r))
        page.evaluate("localStorage.removeItem('neo.theme')")   # leave the scratch profile at the default
        # ---- v0.5 D5: the backend dropdown offers the custom web backend
        bk = page.evaluate("[...document.querySelectorAll('#backend option')].map(o => o.value)")
        self.check('backend dropdown includes deepseek, custom and whatsapp', 'custom' in bk and 'copilot' in bk and 'deepseek' in bk and 'whatsapp' in bk, str(bk))

        self.check("no page errors", not errors, "; ".join(errors)[:300])


# ---------------------------------------------------------------- Windows helpers
def find_gui_pids(marker: str) -> list[int]:
    """Every python(w) running neo_gui.py with this marker in its command line."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*neo_gui.py*' } | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        pid, _, cmdline = line.partition("\t")
        if marker in cmdline:
            pids.append(int(pid))
    return pids


def pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def any_alive(pids: list[int]) -> bool:
    return any(pid_alive(p) for p in pids)


def _windows_of(pids: list[int]) -> list[int]:
    import win32gui
    import win32process
    found: list[int] = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] in pids:
            found.append(h)
    win32gui.EnumWindows(cb, None)
    return found


def window_title_of(pids: list[int]) -> str:
    import win32gui
    for h in _windows_of(pids):
        t = win32gui.GetWindowText(h)
        if t:
            return t
    return ""


def close_window_of(pids: list[int]) -> bool:
    import win32con
    import win32gui
    hs = _windows_of(pids)
    for h in hs:
        win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
    return bool(hs)


def snapshot(folder: Path) -> list[tuple[str, int, int]]:
    if not folder.exists():
        return []
    return sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file())


TITLE_VERSION = ""


def main() -> int:
    global TITLE_VERSION
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--port", type=int, default=9482)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    ns: dict = {}
    exec((repo / "neo_version.py").read_text(encoding="utf-8"), ns)
    TITLE_VERSION = ns["__version__"]
    run = Run(repo, a.port, a.keep)
    code = run.run()
    if a.json:
        Path(a.json).write_text(json.dumps([{"check": n, "ok": ok, "detail": d} for n, ok, d in run.results], indent=1),
                                encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
