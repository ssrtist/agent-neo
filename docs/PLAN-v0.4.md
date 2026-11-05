# PLAN v0.4.0: agent365 GUI (pytauri) + harness settings + off-screen browser

Status: APPROVED 2026-10-30. Target version **0.4.0**. Phase A stops at READY TO TAG.

## Decisions (2026-10-30)

| # | Decision |
|---|---|
| D1 | GUI = **Tauri via `pytauri-wheel` (Python only, no Rust toolchain)**. |
| D2 | **No Node/Svelte build.** Frontend is plain HTML/CSS/ES-module JS. |
| D3 | Sub-agents: **one at a time**, and the feature is **disabled by default** (`enable_subagents = false`). |
| D4 | Thinking line: model may emit an optional `thought` field; it is **hidden by default** (`show_thinking = false`, also a UI toggle). |
| D5 | Browser: **off-screen Edge is the default** for the web backend so the user cannot interfere by mistake; true headless is an opt-in experimental mode. |
| D6 | CLI is kept, and its **default backend becomes `web`** (was `auto`). `auto` stays selectable. |
| D7 | All links open **externally** (default browser / default app). No in-app browsing. |

## Scope

### 1. Shared agent core (refactor, no behaviour loss)
- Extract the run loop from `m365_chat_agent.py` into an importable core (e.g. `agent365_core.py`) that is UI-agnostic:
  - emits structured **events** to a sink (callable), never `print`/`input` directly;
  - asks for decisions through an injected **approver** (tool approval, step-threshold continue, hard-ceiling continue);
  - supports **cooperative cancel** (checked between steps and while polling for a reply where the driver allows).
- Event types (JSON-serialisable, one schema shared by CLI renderer, GUI and the session log):
  `turn_start` (user request), `step_start` (n/limit/ceiling), `status` (driver status text incl. "agent365 is working"), `thought`, `tool_call` (name, args summary), `tool_result` (name, ok/err, truncated preview + full length), `subagent_start` / `subagent_event` / `subagent_end`, `approval_request` / `approval_result`, `final` (markdown message), `error`, `turn_end` (outcome: final | cancelled | ceiling | error).
- The CLI becomes a thin renderer over the same core and keeps its current UX (spinner line, `/new /fresh /backend /workspace /cd /skills /steps /yolo /quit`, prompts).
- Protocol stays **backward compatible**: existing tool names and JSON shapes unchanged; `thought` is an optional additional key on any action object; `run_subagent` is only advertised in the system prompt when enabled.
- Each GUI/CLI session appends events to `~/.agent365/sessions/<timestamp>.jsonl` (no secrets; tool output previews truncated). Doubles as test fixtures.

### 2. Settings: `agent365.toml` (harness-level instructions)
- Read with stdlib `tomllib`. Precedence: built-in defaults < `~/.agent365/agent365.toml` < `<workspace>/.agent365/agent365.toml` < CLI flags. Unknown keys warn, malformed file **refuses with a clear error** (no silent fallback). Ship a commented `agent365.example.toml`.
- Initial keys:
  - `enable_subagents = false`
  - `show_thinking = false`
  - `environment_awareness = true` adds a system-prompt block telling the model the truth about its host: it is driven through a consumer AI chat UI automated in the user's own desktop session, every step is a full UI round-trip (slow), replies can be truncated or time out, transient UI/network failures happen and are retried by the harness, so it should keep each JSON reply compact, prefer few larger steps, avoid huge file dumps, and never assume state survived a failure without re-reading.
  - `custom_instructions_file = ""` (optional extra instructions appended verbatim).
  - `backend = "web"`, `browser_mode = "offscreen"` (`offscreen` | `visible` | `headless`), `cdp_port = 9366`.
  - `step_chunk = 10`, `hard_ceiling = 255`, `auto_approve = false`.
- GUI exposes a minimal settings view (or "Open settings file" + reload); no full settings editor needed in 0.4.

### 3. Sub-agents (only when enabled)
- New tool `run_subagent {"task": "...", "context": "..."}`: runs a child task in a **separate Copilot conversation** (web: a second tab in the same Edge profile; desktop/graph: new conversation, documented limitation if the parent conversation cannot be restored), sequentially (one at a time), with its own smaller step budget, same approval rules, no nested sub-agents. Returns the child's final message as the tool result.
- When disabled, the tool is not advertised and a call to it returns a clear error.

### 4. Off-screen browser (web backend)
- `offscreen` (default): launch the dedicated CDP Edge with an off-screen window position, same profile so SSO cookies persist. If sign-in needs user interaction (login page not clearing within a timeout), **bring the window on-screen** (CDP `Browser.setWindowBounds`, deterministic, no UI automation), tell the user via an event, and park it off-screen again once the chat page is reached.
- `visible`: today's behaviour. `headless`: experimental opt-in (`--headless=new` on the saved profile); if SSO fails, report clearly and suggest `offscreen`.
- CLI flag `--browser-mode {offscreen,visible,headless}`.
- Desktop backend remains available but the GUI warns that it takes focus and the clipboard.

### 5. GUI (pytauri-wheel)
- Entry `agent365_gui.py` (+ `run-gui.cmd` using `pythonw`), frontend in `gui/` (`index.html`, `app.js`, `app.css`, `vendor/`). Window title "M365 Chat Agent (agent365) 0.4.0". Single window: conversation + composer, nothing else beyond a slim header (backend, workspace, browser mode, settings, new chat).
- Window config must set **`dragDropEnabled: false`** (otherwise Tauri's OS drop handler swallows in-page drag-and-drop).
- Python ↔ JS: pytauri commands for `send`, `cancel`, `approve`, `new_chat`, `fresh`, `set_workspace`, `set_backend`, `open_external`, `reload_settings`; core events pushed to JS via the pytauri emitter. Agent runs on a worker thread; UI never blocks.
- **Conversation rendering**:
  - User requests and the final answer are **always fully displayed**.
  - Everything between them (steps, status, tool calls/results, sub-agent activity, approvals already answered) is **rolled up into one single-line live status** per turn (e.g. `Step 4/10 · run_shell · running…`) that expands on click to the full step list; each step expands to its tool args/result. Sub-agent activity nests under its `run_subagent` step.
  - Thinking lines are hidden unless `show_thinking` is on (or toggled in the UI).
  - Pending approvals / step-threshold prompts render as an inline card and are **never** collapsed while pending.
  - Markdown: marked + DOMPurify + highlight.js incl. code blocks with copy button, tables, file-path detection.
- **Links:** every link opens externally. `http(s)`/`mailto` go to the default browser/mail client; workspace file paths open with the default app (Ctrl+click = reveal in Explorer). Open via a Python command with a strict scheme/path allowlist; never execute arbitrary schemes or paths outside the workspace without confirmation.
- **Drag to composer:** any link (or file path) in the conversation is draggable; dropping it on the composer inserts a reference chip (plain-text expansion on send) and **puts the cursor back in the composer**. Plain external drops (text, URLs, Explorer files) also work.
- **Composer:** multi-line, Enter sends, Shift+Enter newline, Stop button while a turn runs (send disabled while running), slash commands mirror the CLI (`/new`, `/fresh`, `/workspace`, `/backend`, `/steps`, `/yolo`).
- Styling: CSS theme tokens (light/dark following the OS is enough for 0.4).
- Vendored JS libs (marked, DOMPurify, highlight.js): browser builds fetched once from upstream npm release tarballs, committed under `gui/vendor/` with their LICENSE files and versions recorded in `gui/vendor/README.md`. No runtime network fetches.
- Test isolation: honour an `AGENT365_HOME` override (settings, sessions) and set `WEBVIEW2_USER_DATA_FOLDER` for test instances so a test never touches the real profile.

### 6. Dependencies
- `pytauri-wheel` (0.8.0 for cp313 win_amd64) added to a `requirements.txt` alongside the existing deps (playwright, pywinauto, msal, pyyaml, requests). Install into the repo `.venv`.

## Out of scope for 0.4
Parallel sub-agents; persistent multi-conversation history browser in the GUI; installer/MSI; in-app browsing; settings editor UI beyond open-file + reload; Graph backend enablement.

## Acceptance (Phase A)
1. `python m365_chat_agent.py --help` shows default backend `web` and `--browser-mode`; CLI REPL behaviour unchanged otherwise.
2. Offline unit tests (stdlib `unittest`, new `tests/`): settings precedence + malformed-file refusal; protocol parsing incl. optional `thought`; sub-agent tool hidden/refused when disabled; event schema round-trip; core loop driven by a **fake driver** (tool call → approval → final, cancel, step threshold) without any browser.
3. GUI launches (`run-gui.cmd`) against a **fake driver mode** (`--fake-backend` or env) and the e2e checks pass: user request + final always visible, steps rolled up to one line and expandable, thinking hidden by default, approval card works, link click routes to `open_external` (intercepted, not actually opened in tests), drag link → chip in composer with focus restored.
4. One **live** smoke run with the web backend in `offscreen` mode, only after confirming no other agent365/Edge-CDP instance on port 9366 is in use: a read-only prompt ("list the files in this workspace") returns a final answer. Browser window must not appear on screen unless SSO needs it.
5. README + CHANGELOG `0.4.0` updated; `agent365_version.py` = `0.4.0`.
6. Stop at READY TO TAG.

## As built (Phase A, 2026-10-30)

Built by app-developer, **single stream**. Every part of the change goes through the core's event
schema: the core, the CLI renderer, the GUI and the tests all depend on it, so the files could not
be split cleanly between workers. Testing was split into separate harnesses instead (offline,
GUI e2e, GUI negative controls, live smoke), each with its own scratch home, WebView2 profile,
workspace and CDP port (9482 / 9483 / 9484).

### Built, per decision
1. **D1 (pytauri-wheel):** `agent365_gui.py` runs a pytauri-wheel 0.8.0 app (`tauri/tauri.conf.json`,
   built-in WebView2) from the repo `.venv`; `run-gui.cmd` starts it windowless with `pythonw`.
   No Rust toolchain was needed. The window is "M365 Chat Agent (agent365) 0.4.0" with
   `dragDropEnabled: false`. Python→JS events go through the pytauri emitter; the agent runs on a
   worker thread. Commands: `send`, `cancel`, `approve`, `new_chat`, `fresh`, `set_workspace`,
   `set_backend`, `set_option`, `open_external`, `reload_settings`, `open_settings`, `get_state`.
2. **D2 (no Node build):** `gui/` is plain HTML/CSS/ES modules (`gui/lib/model.js` for turn rollup,
   `md.js`, `dnd.js`, `highlight.js`, `paths.js`, `app.css` theme tokens, and `app.js`). marked 18.1.0,
   DOMPurify 3.4.16 and highlight.js 11.12.0 are vendored in `gui/vendor/` from upstream npm release
   tarballs, byte-identical to their tarballs (SHA-256 in `gui/vendor/README.md`;
   `.gitattributes` keeps git from changing their line endings).
3. **D3 (sub-agents, one at a time, off by default):** `run_subagent {"task","context"}` is advertised
   only when `enable_subagents` is on, and a call refuses with an error otherwise. The child runs in
   its own conversation (web: a second tab in the same Edge; desktop/graph: a new conversation,
   limitation documented in the README), with `subagent_max_steps` (default 10), the parent's approver,
   and no nesting. Its events nest under the parent step (`subagent_start/event/end`).
4. **D4 (thinking):** an optional `thought` key on any action; `show_thinking` (default false),
   `--show-thinking`, and a GUI toggle.
5. **D5 (off-screen Edge):** `browser_mode` `offscreen` (default) / `visible` / `headless`, with
   `--browser-mode`. Off-screen launches the dedicated Edge at -32000,-32000. If the sign-in page
   does not clear within 20 s, the window is brought on-screen with CDP `Browser.setWindowBounds`
   and an event says so; once the chat page loads, it is parked off-screen again. In the GUI,
   the desktop backend shows a focus/clipboard warning.
6. **D6 (CLI kept, default `web`):** `--help` shows `default: web` and `--browser-mode`. The CLI
   is a thin renderer over `agent365_core`, with the same commands and prompts as 0.3.0.
7. **D7 (links open externally):** `open_external` only accepts these targets:
   - `http(s)` and `mailto` links;
   - workspace paths, which open with their default app (Ctrl+click reveals the file in Explorer).

   Any of the following asks first: a path outside the workspace, or a file type that is not on
   the document allow-list (for example `.chm` or `.exe`). The e2e intercepts every open
   (`AGENT365_TEST_NO_OPEN=1`; `test_opened` reports what would have opened).

### Deviations and details (decided where the plan was silent)
- **Workspace toml precedence** is kept as planned (workspace > home), with these limits:
  - A workspace file that sets `auto_approve = true` produces a warning.
  - A **workspace switch** keeps the session's yolo and step setting, as the CLI's `/workspace` always did, so moving into a repo never turns yolo on.
  - A workspace `custom_instructions_file` must resolve inside that workspace (lexical check first, then after resolving, so junctions are covered).
  - Open question below.
- **A workspace switch or settings reload never re-labels a live backend**. The header keeps the backend that turns really use and a notice suggests `/backend X` (review r5).
- **Links:** UNC paths and `file://host` are refused. The document allow-list (`OPEN_DIRECT_EXTS`): txt, md, markdown, log, json, jsonl, csv, tsv, yaml, yml, toml, ini, cfg, conf, xml, diff, patch, pdf, png, jpg, jpeg, gif, bmp, webp, html, htm. Revealing in Explorer never asks. Relative Markdown links resolve against the workspace. Bare-name inline-code links were left out, because without a workspace file index they would guess.
- **Model HTML** is stricter than the DOMPurify defaults:
  - forbidden: forms, inputs, buttons, `label`, `output`, `style`, `id`, `name`, `for`, `srcset` and other media attributes;
  - classes: only `language-*` / `hljs*` are kept;
  - images render as links ("alt (image)").

  A `<meta>` CSP in `gui/index.html` blocks inline script and outside connections. A CSP in `tauri.conf.json` would be the usual place, but it panics pytauri-wheel 0.8.0 at start-up.
- **The header yolo button posts a notice when it turns yolo on** (a second safeguard; review r4).
- **highlight.js port is reduced:** no Web Worker, and code over 6000 characters stays plain text.
- **Extra event `reply`** (the raw model reply, truncated): it is logged but not rendered.
- **`subagent_max_steps` setting** added.
- **Status de-duplication** happens in the GUI and the log only.
- **A failed driver send is retried once.**
- **Hard-ceiling "continue"** keeps the pending tool result.
- **Each backend has its own sub-agent child** (web: a second tab).
- **CLI Ctrl+C during a turn** is a cooperative cancel.
- **Web driver Stop handling:**
  - Stop works while Edge starts, during sign-in and before typing.
  - If Stop interrupts sign-in, the next connect re-runs it.
  - `close()` ends an Edge the driver launched even before Playwright connected.
  - GUI shutdown ends the driver's own Edge when the worker is stuck.
  - An Edge the driver did not launch is never moved or parked.
- **pytauri-wheel exit work-around:** closing the last window raises `ExitRequested`, but `run_return` hangs. `on_run` therefore shuts the session down and calls `app_handle.exit(0)`. pytauri-wheel 0.8.0 also needs real (non-string) annotations, so `agent365_gui.py` has no `from __future__ import annotations`.
- **Window config trace:** `AGENT365_GUI_TRACE` writes the window config. The e2e uses it to check `dragDropEnabled=false`, which cannot be observed over CDP.

### Code review
| Round | Findings | Covering checks |
|---|---|---|
| 1 | 7: model HTML injection; workspace-toml auto_approve / custom file / switch; UNC in open_external; sub-agent yolo snapshot; Stop ignored during Edge start/sign-in/typing; off-screen parked a reused Edge; non-document files opened directly | `tests/test_web_driver.py`, settings/core tests, offline mutations, GUI mutations 13-18 |
| 2 | 5: Edge launched before Playwright connected not closed; interrupted sign-in not re-run; reused Edge moved during sign-in; custom file touched before lexical check; model HTML could borrow app classes (fake modal) | web-driver + settings tests, offline mutations, GUI mutation 19 |
| 3 | 1: `id` in model HTML shadowed `#stop` | e2e "ids stripped", GUI mutation 20 |
| 4 | 1: `<label for="yolo">` in an answer clicked the yolo button (+ yolo-on notice) | e2e label check, GUI mutations 21-22 |
| 5 | 1: a workspace switch / reload re-labelled the backend without swapping the driver | e2e backend check, GUI mutation 23 |
| 6 | **clean** | |

### Tests (2026-10-30)
| Suite | Result | Notes |
|---|---|---|
| Offline unit tests (`python -m unittest discover -s tests -t .`) | 67/67 OK | fake driver, no browser; isolated `AGENT365_HOME` |
| Offline negative controls (`tests/negative_controls.py`) | 26/26 caught | each mutation must make a named test fail |
| GUI e2e (`tests/e2e/gui_e2e.py`) | 58/58 | real GUI via `run-gui.cmd --fake-backend`, Playwright over WebView2 CDP 9482, scratch WebView2 profile/home/workspace, local beacon server; real `~/.agent365` checked untouched |
| GUI negative controls (`tests/negative_controls_gui.py`) | 23/23 caught (one full run) | one GUI run per mutation on a repo copy, port 9483 |
| CLI acceptance 1 | pass | `--help` shows `default: web` and `--browser-mode`; `--version` prints 0.4.0 |
| Live smoke (`tests/e2e/live_smoke.py`, acceptance 4) | manual per release | runs against the dedicated Edge profile |

Detector limits:
- `dragDropEnabled` is checked through the window config (trace), not by behaviour.
- The lexical UNC check cannot be told apart from the post-resolve re-check, so the GUI mutation disables both.

### Build notes
- Python 3.13, repo `.venv`, `pip install -r requirements.txt` (pytauri-wheel 0.8.0 cp313).
- Vendored JS was fetched once from upstream npm release tarballs.
- No package is built: the app runs from the repo.
- Isolation for every GUI run:
  - `AGENT365_HOME`, `WEBVIEW2_USER_DATA_FOLDER` and a workspace under `%TEMP%\agent365-e2e-*` / `agent365-negctl-*`;
  - `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>` for Playwright;
  - `AGENT365_TEST_NO_OPEN=1` to intercept external opens.
- `%TEMP%` is an 8.3 path here; resolve it before comparing paths.
- The venv `pythonw` is a launcher with a child process, and the window belongs to the child.
- The e2e closes the window with WM_CLOSE and checks that the process exits.
