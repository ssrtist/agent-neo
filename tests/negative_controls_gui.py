"""Negative controls for the GUI e2e (builder rule: every check must be shown able to fail).

Copies the working tree (with gui/, without .git; .venv is a junction to the real one) to a scratch
folder, applies one mutation at a time, runs tests/e2e/gui_e2e.py --repo <copy> and expects the named
checks to FAIL. Never edits the real repo. Each run starts a real (fake-backend) window: ~1 minute each.

    .venv\\Scripts\\python.exe tests\\negative_controls_gui.py [--only N,N] [--port 9483]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (description, file, old, new, checks that must fail)
MUTATIONS = [
    ("steps rendered expanded", "gui/app.js",
     "  if (openKeys.has(key)) d.open = true;", "  d.open = true;",
     ["steps rolled up to one line"]),
    ("thinking lines always rendered", "gui/app.js",
     "    if (st.show_thinking) for (const t of s.thoughts)", "    for (const t of s.thoughts)",
     ["thinking hidden by default"]),
    ("approval card put inside the collapsed steps", "gui/app.js",
     "  if (t.pending) parts.push(askCard(t.pending));", "  if (t.pending) parts[parts.length - 1].append(askCard(t.pending));",
     ["approval card visible while steps are collapsed"]),
    ("link click not routed to open_external", "gui/app.js",
     "  if (i > 0) openLink(art.slice(0, i), art.slice(i + 1), e.ctrlKey);", "  if (i > 0) void 0;",
     ["http link click routes to open_external"]),
    ("focus not restored after a chip drop", "gui/app.js",
     "  if (c) { if (e.shiftKey) insertAtCaret(plainRef(c)); else { addChip(c); focusEnd(); } return; }",
     "  if (c) { if (e.shiftKey) insertAtCaret(plainRef(c)); else { addChip(c); } return; }",
     ["focus restored to the composer after the drop"]),
    ("drag payload not set on links", "gui/app.js",
     "  if (a) { const c = ctxForLink(a); if (c) setDrag(e, c); }", "  if (a) { /* no payload */ }",
     ["drag file link -> file chip with its path"]),
    ("slash reply wipes type-ahead (race fix reverted)", "gui/app.js",
     "    if (ta.value === raw) ta.value = '';", "    ta.value = '';",
     ["a slash command's reply does not wipe text typed after it"]),
    ("unsafe URL schemes allowed", "neo_gui.py",
     "            if scheme not in SAFE_SCHEMES:\n", "            if False:\n",
     ["unsafe schemes refused"]),
    ("outside-workspace files open without asking", "neo_gui.py",
     "        if (not inside or risky) and not confirmed:", "        if risky and not confirmed:",
     ["outside-workspace file asks first; Cancel opens nothing"]),
    ("window close does not end the app (exit workaround removed)", "neo_gui.py",
     "                app_handle.exit(0)\n", "                pass\n",
     ["GUI exits on window close"]),
    ("window title without the version", "neo_gui.py",
     '        "label": "main",\n        "title": version_string(),', '        "label": "main",\n        "title": "Agent Neo",',
     ["window title shows the version"]),
    ("dragDropEnabled left on", "neo_gui.py",
     '        "dragDropEnabled": False,', '        "dragDropEnabled": True,',
     ["window built with dragDropEnabled=false (config, from the trace)"]),
    # ---- code review round 1 fixes
    ("model HTML not sanitised (FORBID lists dropped)", "gui/lib/md.js",
     "  const out = DOMPurify.sanitize(html, opts).replace(", "  const out = DOMPurify.sanitize(html, { ADD_ATTR: ['target'] }).replace(",
     ["answer HTML sanitised: no <style>, <img> or style= from the model"]),
    ("CSP meta tag removed", "gui/index.html",
     '  <meta http-equiv="Content-Security-Policy"', '  <meta name="x-no-csp"',
     ["CSP blocks remote loads from the page"]),
    ("UNC paths not refused", "neo_gui.py",
     '    t = text.replace("/", "\\\\")\n    return t.startswith("\\\\\\\\")', '    return False',
     ["UNC paths refused before touching the network"]),
    ("document allow-list bypassed", "neo_gui.py",
     "p.suffix.lower() not in OPEN_DIRECT_EXTS and not reveal", "False and not reveal",
     ["workspace file of a non-document type asks first; a document opens"]),
    ("workspace switch re-applies the toml's yolo", "neo_gui.py",
     "self._load_settings(workspace=target, keep_session=True)", "self._load_settings(workspace=target, keep_session=False)",
     ["a workspace switch keeps yolo off even if that workspace's toml turns it on"]),
    ("startup settings warnings not shown", "gui/app.js",
     "let shownStartupWarnings = false;", "let shownStartupWarnings = true;",
     ["startup settings warnings shown"]),
    ("model HTML keeps the app's classes (review r2)", "gui/lib/md.js",
     "  if (data.attrName !== 'class') return;", "  return;",
     ["model HTML cannot borrow the app's classes (no fake modal overlay)"]),
    ("model HTML keeps ids (review r3)", "gui/lib/md.js",
     "'poster', 'id', 'name', 'for'],", "'poster', 'for'],",
     ["an answer's HTML cannot take over the Stop button (ids stripped)"]),
    ("model <label for> kept (review r4)", "gui/lib/md.js",
     "      'label', 'output'],\n    FORBID_ATTR: ['style', 'data-art', 'srcset', 'background', 'poster', 'id', 'name', 'for'],",
     "      ],\n    FORBID_ATTR: ['style', 'data-art', 'srcset', 'background', 'poster', 'id', 'name'],",
     ["clicking an answer's <label for=yolo> does not touch the yolo button"]),
    ("no notice when the header turns yolo on (review r4)", "neo_gui.py",
     "        if yolo_on:\n            self.notice(", "        if False:\n            self.notice(",
     ["the header yolo button turning yolo ON posts a notice"]),
    ("workspace switch re-labels the live backend (review r5)", "neo_gui.py",
     "            if self.driver is None:\n                self.backend = settings.backend",
     "            if True:\n                self.backend = settings.backend",
     ["a workspace switch with a live driver keeps the header on the active backend and says so"]),
    # ---- v0.5.0
    ("D4 default theme back to dark", "gui/theme-boot.js",
     "DEF = 'dark-high-contrast';", "DEF = 'dark';",
     ["theme defaults to Dark High Contrast (#010409 bg, #f0f3f6 fg)",
      "invalid saved theme falls back to Dark High Contrast"]),
    ("D4 theme choice not saved", "gui/theme-boot.js",
     "    try { localStorage.setItem(KEY, v); }", "    try { }",
     ["theme persists across reload"]),
    ("D1 raw path protection removed (GUI)", "gui/lib/paths.js",
     "    : protectRawPaths(part.replace(", "    : ((x) => x)(part.replace(",
     ["raw path with \\_ keeps its backslash and links whole"]),
]


def copy_repo(dst: Path) -> None:
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__", "node_modules"))
    subprocess.run(["cmd", "/c", "mklink", "/J", str(dst / ".venv"), str(REPO / ".venv")], check=True, capture_output=True)


def remove_copy(dst: Path) -> None:
    if (dst / ".venv").exists():
        os.rmdir(dst / ".venv")   # removes the junction only, never the real .venv
    shutil.rmtree(dst, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated 1-based mutation numbers")
    ap.add_argument("--port", type=int, default=9483)
    a = ap.parse_args()
    picks = {int(x) for x in a.only.split(",") if x.strip()}
    failures = 0
    ran = 0
    with tempfile.TemporaryDirectory(prefix="neo-negctl-gui-") as tmp:
        for n, (desc, rel, old, new, checks) in enumerate(MUTATIONS, 1):
            if picks and n not in picks:
                continue
            ran += 1
            work = Path(tmp).resolve() / "repo"
            if work.exists():
                remove_copy(work)
            copy_repo(work)
            try:
                target = work / rel
                text = target.read_text(encoding="utf-8")
                if text.count(old) != 1:
                    print(f"BROKEN CONTROL  {n}. {desc}: pattern found {text.count(old)} times in {rel}", flush=True)
                    failures += 1
                    continue
                target.write_text(text.replace(old, new), encoding="utf-8")
                out = Path(tmp) / f"result-{n}.json"
                r = subprocess.run([sys.executable, str(work / "tests" / "e2e" / "gui_e2e.py"), "--repo", str(work),
                                    "--port", str(a.port), "--json", str(out)], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=600)
                results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
                by_name = {x["check"]: x["ok"] for x in results}
                caught = [c for c in checks if by_name.get(c) is False]
                missing = [c for c in checks if c not in by_name]
                others = sorted(x["check"] for x in results if not x["ok"] and x["check"] not in checks)
                ok = r.returncode != 0 and len(caught) == len(checks)
                print(f"{'ok (fails)' if ok else 'NOT CAUGHT'}  {n}. {desc}  -> failed: {caught or '-'}"
                      + (f"; not reached: {missing}" if missing else "") + (f"; also failed: {others}" if others else ""),
                      flush=True)
                if not ok:
                    failures += 1
                    print(r.stdout[-1500:])
            finally:
                remove_copy(work)
    print(f"\n{ran - failures}/{ran} mutations caught")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
