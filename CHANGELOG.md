# Changelog

All notable changes to Agent Neo (agent-neo; formerly M365 Chat Agent / agent365) are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). The version lives in `neo_version.py`.

## [0.11.0] - 2026-11-05

Multi-Hunk File Editing, Background Shell Tasks, Mid-Turn User Questions, Direct Web Fetch, and Context Compaction.

### Added
- **Multi-Hunk File Editing (`edit_file`)**: Accepts an `edits` list of `{"old": "...", "new": "..."}` hunks with atomic all-or-nothing pre-validation. If any hunk fails or matches ambiguously, no changes are written to disk.
- **Background Shell Tasks (`run_shell` + `read_task` + `kill_task`)**: Running shell commands with `"background": true` launches session-scoped asynchronous background processes. Output is non-blocking and drained via `read_task`; processes can be terminated with `kill_task` or auto-cleaned on session reset.
- **Mid-Turn `ask_user` Tool**: Allows models to prompt the user with interactive questions and multiple-choice options mid-turn without ending the turn. Supports interactive choices in terminal CLI, tactile option cards in GUI, and automatic first-choice selection in YOLO mode.
- **Direct `web_fetch` Tool**: Safely fetches HTTP and HTTPS endpoints (including local dev servers like `http://localhost:3000`), automatically cleans HTML to readable markdown text, and formats JSON.
- **Context Compaction (`/compact`)**: Condenses conversation history into a structured state checkpoint (Goal, Files Touched, Key Findings, Next Steps) and recycles the web chat tab cleanly before re-seeding the next turn.

### Fixed
- **Instant Live CDP Sign-In Detection**: Updated `_wait_for_sign_in` in `copilot_web.py` to query live `window.location.href` over CDP and inspect `/json/list` endpoints directly. Fixes an issue where Playwright's cached in-memory `page.url` was not updated during external browser navigations, causing the harness to wait the full 300s timeout before acknowledging login.
- **Auto-Navigation from M365 Landing Portal**: If corporate SSO completes and redirects to the default M365 portal landing page (`https://m365.cloud.microsoft/landingv2` or root), the driver automatically navigates directly to `/chat`.
- **Dismiss Obsolete Sign-In Warning Notices**: Updated `appendNotice` in `gui/app.js` to automatically clear the `"Sign-in needed: the Edge window is now on screen..."` warning banner the moment login is detected.
- **Multi-Tab Sign-In Detection**: Updated `_wait_for_sign_in` in `copilot_web.py` to scan all tabs in `self._context.pages`, immediately detecting when an SSO flow redirects to a new tab or replaces the active page target without waiting for the 300s timeout.
- **Persistent Auth Token Renewal**: Removed `--disable-background-networking` from `EXTENSION_SUPPRESSION_FLAGS` in `browser_automation/browser.py` so Edge can refresh OAuth/SSO tokens in the background without forcing re-login across sessions.
- **GUI Question Card Rendering & String Answers**: Fixed `askCard` in `gui/app.js` and `GuiSession.approve` in `neo_gui.py` falling back to hard ceiling controls and boolean coercion when models invoke `ask_user`. Rendered dedicated option pill buttons, an interactive text answer box, and preserved user answer strings across IPC.

## [0.10.0] - 2026-11-04

Turn-Reduction Pack, Bug Fixes, and Multi-Tier Engine-Prioritized Skills Discovery.

### Added
- **Batch Tool Calling (`tools`)**: Protocol support for `{"type":"tools","calls":[{"name":...,"arguments":...}, ...]}` allowing models to execute multiple tools in a single turn. Execution halts immediately if any tool in the batch fails or is denied.
- **Read-Only Exploration Suite**: Added auto-approved `list_dir` (directory listing with sizes) and `grep_search` (pure Python regex scanner with line numbers and file limits).
- **Line Ranges in `read_file`**: Added optional `start_line` and `end_line` arguments with line-numbered output (`12 | ...`).
- **Engine-Prioritized Skills Discovery**: `discover_skills()` searches active engine native profile skills first (`.copilot/skills` for Copilot/Desktop, `.gemini/skills` for Gemini, `.deepseek/skills` for DeepSeek), followed by Agent Neo global skills (`.agent-neo/skills`, `.neo/skills`), and fallback engines in order (`.copilot` -> `.gemini` -> `.deepseek` -> `.claude` / `.agents`). Workspace skills always take precedence.

### Fixed
- **CRLF Line-Ending Preservation**: `edit_file` and `write_file` detect existing newline conventions and preserve CRLF in binary mode without generating spurious git diffs.
- **Read Truncation Transparency**: Appends explicit `[TRUNCATED: Showing first 100,000 characters of N total...]` banner when content is truncated.
- **Graceful Shell Timeout**: Caught `subprocess.TimeoutExpired` in `run_shell_command` returning a clean JSON error response with partial stdout/stderr instead of raising an uncaught exception.

## [0.9.0] - 2026-11-04

Rebrand to **Agent Neo** (`agent-neo`, CLI alias `neo`) and first-class DeepSeek web chat support.

### Added
- **DeepSeek Web backend (`deepseek`)**: drives `https://chat.deepseek.com/` in the shared dedicated Edge profile ([deepseek_web.py](deepseek_web.py)). `<think>` reasoning blocks become the step's `thought`; "Thought for N seconds" headers and trailing UI labels (Copy, Regenerate, ...) are stripped. Sign-in detection covers `chat.deepseek.com/sign_in` and Google sign-in.
- Launchers `neo.cmd` and `agent-neo.cmd`.
- Environment variables accept both `NEO_*` and `AGENT_NEO_*` forms (`HOME`, `ROOT`, `BROWSER_PROFILE`, `FAKE_SCRIPT`, `GUI_TRACE`, `TEST_NO_OPEN`).
- Tests: `tests/test_deepseek_driver.py` and a DeepSeek negative control.

### Changed
- **Renamed** every module: `m365_chat_agent.py` -> `neo.py`, `agent365_gui.py` -> `neo_gui.py`, `agent365_{core,events,settings,edge,fake,version}.py` -> `neo_*.py`, `copilot_driver.py` -> `chat_driver.py`, `m365_copilot_web.py` -> `copilot_web.py`, `m365_copilot_ui.py` -> `copilot_ui.py`, `agent365.example.toml` -> `neo.example.toml`.
- Settings: `~/.agent-neo/neo.toml` and `<workspace>/.agent-neo/neo.toml` (`agent-neo.toml` and `.neo/` also accepted). Protected workspace paths: `.agent-neo`, `.neo`, `.git`.
- Dedicated browser profile: `%LOCALAPPDATA%\agent-neo-edge` (CDP port 9365 unchanged).
- Backend `web` is now spelled `copilot` (still the default; `web` accepted as an alias). It continues to target M365 Copilot and consumer Copilot; `desktop` continues to target `M365Copilot.exe` and `Copilot.exe`.
- GUI: window title `Agent Neo (agent-neo) 0.9.0`; `localStorage` keys `neo.theme` / `neo.contentZoom`; Tauri identifier `org.agentneo.app`.
- CLI renderer says `Waiting for model...` / `[Model raw reply]` instead of naming Copilot.

### Removed
- The Microsoft Graph backend (`graph`) and its MSAL token cache.

### Migration
- Old `~/.agent365` settings, sessions and the `agent365-edge` browser profile are not read. Copy `agent365.toml` to `~/.agent-neo/neo.toml`; you will need to sign in once in the new Edge profile.

## [0.5.0] - 2026-11-02

Path backslash preservation, robust JSON extraction, desktop send resilience, GitHub Dark High Contrast default theme, and custom web chat backend support. Plan: [docs/PLAN-v0.5.md](docs/PLAN-v0.5.md).

### Added
- **Custom Web Backend (`custom`)**: Point agent365 at any web LLM chat UI via `backend = "custom"` with `custom_url` in `agent365.toml` or `--custom-url <url>` on the CLI. Includes an automatic pre-harness verification checklist (`[pre-harness] Connected -> Signed in -> Input located -> Send control located -> Canary reply received -> Ready`) with auto-heuristics for standard chat interfaces and optional selector overrides (`[custom_backend]` in `agent365.toml`).
- **GitHub Dark High Contrast default theme**: New default theme matching the GitHub Dark High Contrast palette (`#010409` background, `#f0f3f6` text, `#71b7ff` accent, `#ff9492` error, `#26cd4d` success). Includes a header theme switcher cycling between `dark-high-contrast`, `dark`, and `light`, persisted in `localStorage`.
- **Pre-send and post-send composer verification**: The desktop automation driver (`m365_copilot_ui.py`) verifies that the composer actually cleared after triggering Send. If text remains, it re-acquires focus and retries sending up to 3 times, warning the user if the prompt stays stuck.

### Changed
- **Path escapes**: Raw Windows paths containing underscores or special symbols (e.g. `C:\Users\alice.smith\_agent365-home`) are shielded before Markdown parsing, preventing `marked` from swallowing backslashes.
- **Robust JSON parsing**: `parse_action()` now scans for balanced `{ ... }` bracket pairs to extract valid tool calls or final answers even when preceded or followed by conversational chatter (e.g. "Coding and executing"). Any leading preamble is captured into the step's `thought` stream.

## [0.4.0] - 2026-10-30

A desktop GUI, harness settings, an off-screen browser and opt-in sub-agents. Plan:
[docs/PLAN-v0.4.md](docs/PLAN-v0.4.md) (its *As built* section records what was built and tested).

### Added
- **Desktop GUI** (`run-gui.cmd`, `agent365_gui.py`, `gui/`): pytauri-wheel 0.8.0 + WebView2, no
  Node or Rust build. One window: your request and the final answer always in full,
  everything in between rolled up into one expandable live status line per turn, sub-agent
  activity nested under its step, inline approval / step-limit cards that never collapse while
  pending, Markdown with highlighted code blocks and copy buttons, Stop, slash commands, light/dark
  following the OS. Links open outside the app through an allow-list (web and mail links in the
  default browser/mail client, workspace documents with their default app, Ctrl+click reveals;
  other file types and anything outside the workspace ask first; UNC paths are refused). Links and
  file paths drag onto the composer as chips. Vendored marked, DOMPurify and highlight.js
  (`gui/vendor/README.md`); no runtime network fetches; a Content-Security-Policy blocks inline
  script and outside connections.
- **Agent core** (`agent365_core.py`, `agent365_events.py`): the run loop is UI-agnostic. It emits
  structured events, asks an injected approver, and supports cooperative cancel. The CLI and GUI
  render the same events, and every session is logged to `~/.agent365/sessions/<timestamp>.jsonl`.
- **Settings** (`agent365.toml`, `agent365.example.toml`): home and workspace files, then
  command-line flags. Unknown keys warn; malformed files are refused with the file and key named.
  `AGENT365_HOME` moves the home folder. `environment_awareness` (default on) tells the model how
  it is being driven; `custom_instructions_file` appends your instructions.
- **Off-screen browser** (web backend, `--browser-mode offscreen|visible|headless`): off-screen is
  the default; the window comes on-screen only while sign-in needs you.
- **Sub-agents** (`run_subagent`, off by default: `enable_subagents`, `--enable-subagents`): one
  at a time, in a separate conversation (web: a second tab), with their own step budget
  (`subagent_max_steps`), the same approvals, no nesting.
- **Thinking lines**: an optional `thought` field in any reply, hidden by default
  (`show_thinking`, `--show-thinking`, GUI toggle).
- `requirements.txt`; `--fake-backend` (scripted test driver); tests: offline unit tests, GUI
  e2e, negative controls for both, and a one-turn live smoke.

### Changed
- The CLI's default backend is now `web` (was `auto`; `auto` is still available).
- Ctrl+C during a CLI turn cancels the turn instead of ending the program.
- The web driver honours Stop while Edge starts, during sign-in and before typing, and closes an
  Edge it launched if it is stopped half-way.

### Security
- Model HTML in answers is sanitised beyond DOMPurify's defaults: no forms, inputs, labels,
  styles, ids, names or images, and only highlighting classes, so an answer cannot imitate or
  operate the app's own controls.
- A workspace `agent365.toml` that sets `auto_approve = true` is flagged; a workspace switch never
  turns yolo on. A workspace `custom_instructions_file` must be inside the workspace.

## [0.3.0] - 2026-10-30

First versioned release. 0.3.0 is the starting version and covers everything built so far.

### Added
- **Versioning:** `agent365_version.py` holds the version (`__version__`) and nothing else
  defines it. `m365_chat_agent.py --version` prints `M365 Chat Agent (agent365) 0.3.0`, and the
  version is also printed when the agent starts.
- **Backends** (`--backend`, and `/backend` to switch inside a session):
  - `desktop`: drives the `M365Copilot.exe` desktop app through Windows UI Automation (`pywinauto`).
  - `web`: drives `https://m365.cloud.microsoft/chat` in Edge through Playwright over CDP
    (`--port`, default 9366). The CDP side is handled by `browser_automation`, which starts Edge
    with extensions turned off and keeps corporate SSO. It starts a fresh browser by default;
    `--new-session-only` / `--reuse-browser` reuses the open one.
  - `graph`: Microsoft Graph Copilot Chat API. Needs an Entra app (`M365_TENANT_ID`,
    `M365_CLIENT_ID`) and caches its token in `~/.agent365_token.json`.
  - `auto` (default): uses `desktop` when the desktop app is running, otherwise `web`.
- **Tool protocol** (one JSON object per reply): `read_file`, `write_file`, `edit_file`
  (exactly one match is required), `delete_file`, `run_shell` (PowerShell on Windows, 120 s
  timeout) and `load_skill`. File paths cannot leave the workspace root.
- **Approval:** you approve each write, edit, delete or shell call. `--yolo` / `--auto-approve`
  or `/yolo` approves them all automatically.
- **Skills:** finds `SKILL.md` skills in `.claude/skills/`, `.agents/skills/` and `skills/` in the
  workspace, plus `~/.copilot/skills/`. `/skills` lists them and `load_skill` loads one.
- **Workspace:** `-w` / `--workspace` sets it (default: `AGENT365_ROOT`, then the deprecated
  `HARNESS_ROOT`, then the current directory). `/workspace [path]` and `/cd [path]` show or
  change it during a session.
- **Step limits:** the agent pauses for confirmation every 10 tool steps (`--step-chunk`,
  `/steps N`) and stops at 255 steps (`--hard-ceiling`).
- **REPL commands:** `/new` (new conversation), `/fresh` (restart the browser for `web`/`auto`,
  otherwise start a new conversation), `/quit` and `/exit`.
- **Status line:** shows `agent365 is working...` while Copilot replies.

### Changed
- Renamed the project to **M365 Chat Agent** (code name **agent365**). The entry point is now
  `m365_chat_agent.py`. Two old names still work as fallbacks in 0.3.0: the `HARNESS_ROOT` setting and
  the `~/.m365_copilot_harness_token.json` token cache file.
