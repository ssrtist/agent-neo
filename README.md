# Agent Neo (agent-neo)

**Version 0.9.0** - see [CHANGELOG.md](CHANGELOG.md). The version lives in one place, [neo_version.py](neo_version.py); check it with `python neo.py --version`.

**Agent Neo** (code name **agent-neo**, CLI alias **neo**; formerly *M365 Chat Agent / agent365*) is a local coding and tool agent driven by consumer and enterprise AI chat apps through swappable backends. It has a command-line REPL and a desktop GUI; both run the same agent core.

## Backends

| Backend | Target | How |
|:--|:--|:--|
| `copilot` (default; alias `web`) | Microsoft 365 Copilot (`https://m365.cloud.microsoft/chat`) or consumer Copilot (`https://copilot.microsoft.com`) | Playwright over CDP in a dedicated Edge profile |
| `gemini` | Google Gemini (`https://gemini.google.com/app`) | Same dedicated Edge profile |
| `deepseek` | DeepSeek Chat (`https://chat.deepseek.com/`) | Same dedicated Edge profile; `<think>` reasoning becomes the step's thought |
| `whatsapp` | WhatsApp Web (`https://web.whatsapp.com/send/?phone=...`) | Same dedicated Edge profile (experimental, disabled by default) |
| `desktop` | `M365Copilot.exe` or Windows `Copilot.exe` desktop app | Windows UI Automation (`pywinauto`); takes keyboard focus and clipboard during turns |
| `auto` | `desktop` when a Copilot desktop app is open, else `copilot` | |
| `custom` | Any web chat UI by URL | Pre-harness validation checklist with a canary prompt |

All web backends share the dedicated Edge profile `%LOCALAPPDATA%\agent-neo-edge` on CDP port `9365`, with extensions suppressed via `browser_automation`. By default the Edge window runs **off-screen** (see *Browser modes*). The legacy Microsoft Graph backend (`graph`) was removed in 0.9.0.

---

## Workspace Structure

- [neo.py](neo.py), [neo.cmd](neo.cmd), [agent-neo.cmd](agent-neo.cmd): Command-line REPL; a thin renderer over the agent core.
- [neo_core.py](neo_core.py): UI-agnostic agent core: run loop, tool protocol (`read_file`, `write_file`, `edit_file`, `delete_file`, `run_shell`, `load_skill`, and `run_subagent` when enabled), approvals through an injected approver, cooperative cancel, session log.
- [neo_events.py](neo_events.py): The event schema shared by the CLI, the GUI and the session log.
- [neo_settings.py](neo_settings.py): `neo.toml` loading (home + workspace + CLI flags); [neo.example.toml](neo.example.toml) documents every key.
- [neo_gui.py](neo_gui.py), [run-gui.cmd](run-gui.cmd), `gui/`, `tauri/`: the desktop GUI (pytauri-wheel + WebView2; plain HTML/CSS/JS, vendored libraries in `gui/vendor/`).
- [neo_fake.py](neo_fake.py): Scripted fake chat driver for tests (`--fake-backend`).
- [neo_edge.py](neo_edge.py): Lifecycle of agent-neo's own Edge profile (never touches other profiles or your real Edge).
- [chat_driver.py](chat_driver.py): Unified driver abstraction (`ChatDriver`, `get_driver()`).
- [copilot_web.py](copilot_web.py): Playwright / CDP driver for M365 and consumer Copilot web.
- [copilot_ui.py](copilot_ui.py): Windows UI Automation driver for `M365Copilot.exe` and `Copilot.exe`.
- [gemini_web.py](gemini_web.py): Playwright / CDP driver for Google Gemini.
- [deepseek_web.py](deepseek_web.py): Playwright / CDP driver for DeepSeek Chat.
- [custom_driver.py](custom_driver.py): Custom web chat driver and pre-harness checklist.
- [neo_version.py](neo_version.py): Single source of truth for the app version (`__version__`).
- [CHANGELOG.md](CHANGELOG.md): Release history. [requirements.txt](requirements.txt): runtime dependencies.
- `browser_automation/`: CDP lifecycle manager ensuring Edge starts cleanly with corporate SSO and extensions suppressed.
- `tests/`: offline unit tests, the GUI e2e, negative controls and the live smoke (see *Tests*).

---

## Install

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Usage

### Desktop GUI
```powershell
.\run-gui.cmd                                   # copilot backend, off-screen Edge
.\run-gui.cmd --workspace "C:\path\to\repo" --backend copilot --browser-mode visible
```
One window: the conversation and a composer, with a slim header (backend, workspace, browser mode, yolo, thinking, settings, new chat).
- Your request and the final answer are always shown in full. Everything in between is rolled up into one live status line per turn (for example `Step 4/10 · run_shell · running…`); click it to see every step, and click a step to see its arguments and result. Sub-agent activity nests under its `run_subagent` step.
- Approvals and step-limit prompts appear as an inline card and are never collapsed while they wait for you.
- Answers are Markdown (code blocks with a copy button, tables, file paths). HTML from the model is sanitised: no scripts, forms, ids, styles or images (images are shown as links).
- **Links open outside the app**: `http(s)`/`mailto` in your default browser or mail client; a workspace file opens with its default app (Ctrl+click reveals it in Explorer). Only document types open directly; anything else (for example `.chm`, `.exe`), and any file outside the workspace, asks first. UNC paths are refused.
- **Drag to the composer**: drag any link or file path from the conversation onto the composer to attach it as a chip; the cursor goes back to the composer. Files from Explorer, URLs and plain text can be dropped too. Chips expand to plain text when sent.
- Composer: Enter sends, Shift+Enter is a new line, Stop cancels the running turn. Slash commands: `/new`, `/fresh`, `/switch-account`, `/workspace`, `/backend`, `/steps`, `/yolo`, `/skills`, `/agents`, `/agent`.
- Settings: *Open settings file* opens `~/.agent-neo/neo.toml`; *Reload* applies it. A workspace switch keeps the session's yolo and step setting, and never re-labels a backend that is already running (use `/backend`).

### Show the Version
```powershell
.\.venv\Scripts\python.exe neo.py --version
# Agent Neo (agent-neo) 0.9.0
```

### Run the CLI (default backend: copilot, off-screen Edge)
```powershell
.\.venv\Scripts\python.exe neo.py
```

### Run with a Specific Workspace Folder
```powershell
.\.venv\Scripts\python.exe neo.py -w "C:\path\to\target\repo"
# Or using the long flag:
.\.venv\Scripts\python.exe neo.py --workspace "C:\path\to\target\repo"
```

### Run with Auto-Approve (YOLO Mode)
```powershell
.\.venv\Scripts\python.exe neo.py --yolo
```

### Run Specifically with Desktop App
```powershell
.\.venv\Scripts\python.exe neo.py --backend desktop --yolo
```

### Run Specifically with a Web Backend (Edge over CDP)
```powershell
# Fresh browser instance by default (off-screen):
.\.venv\Scripts\python.exe neo.py --backend copilot --port 9365
# Show the browser window, or try headless (experimental; SSO may fail):
.\.venv\Scripts\python.exe neo.py --backend copilot --browser-mode visible

# Or reuse existing browser window and only start a new chat session:
.\.venv\Scripts\python.exe neo.py --backend copilot --port 9365 --new-session-only
```
Gemini and DeepSeek use the same flags:
```powershell
.\\.venv\\Scripts\\python.exe neo.py --backend gemini
.\\.venv\\Scripts\\python.exe neo.py --backend deepseek
```
A fresh start ends every Edge running with the dedicated automation profile (`%LOCALAPPDATA%\agent-neo-edge`), whichever port it uses.

### Browser modes (web backends)
- `offscreen` (default): the dedicated Edge runs with its window off-screen, so you cannot click into it by mistake; the profile and its SSO cookies are the usual ones. If sign-in needs you, the window is brought on-screen (CDP `Browser.setWindowBounds`), you are told, and it goes off-screen again once the chat page loads. An Edge that agent-neo did not launch (`--new-session-only`) is never moved.
- `visible`: the 0.3.0 behaviour.
- `headless`: experimental (`--headless=new` on the saved profile). If sign-in fails, agent-neo says so and suggests `offscreen`.

### Settings: `neo.toml`
Copy [neo.example.toml](neo.example.toml) to `~/.agent-neo/neo.toml` (your defaults) and/or `<workspace>/.agent-neo/neo.toml` (per workspace). Precedence, lowest first: built-in defaults < home file < workspace file < command-line flags. An unknown key is ignored with a warning; a malformed file (bad TOML, a wrong type or value) is refused with an error naming the file and key. Keys: `enable_subagents`, `subagent_max_steps`, `show_thinking`, `environment_awareness`, `custom_instructions_file`, `backend`, `browser_mode`, `cdp_port`, `browser_profile_dir`, `step_chunk`, `hard_ceiling`, `auto_approve`, and `[custom_backend]` (`url`, `input_selector`, `send_selector`, `bubble_selector`).
- A workspace file that sets `auto_approve = true` is flagged with a warning; on a workspace switch it is not applied (yolo stays as it was).
- A `custom_instructions_file` set by a workspace file must be inside that workspace.
- `NEO_HOME` (or `AGENT_NEO_HOME`) overrides `~/.agent-neo` (settings and session logs).

### Sub-agents (opt-in)
With `enable_subagents = true` (or `--enable-subagents`) the model gets a `run_subagent {"task", "context"}` tool. A sub-agent runs in a separate chat conversation (web: a second tab in the same Edge), one at a time, with its own step budget (`subagent_max_steps`, default 10), the same approval rules (yolo follows the parent's live setting) and no nesting. Its final message is the tool result. When disabled, the tool is not advertised and a call returns an error. On the desktop backend the parent conversation may not be restorable after a sub-agent; this is a known limitation.

### Thinking lines
The model may add an optional `thought` field to any reply. It is hidden unless `show_thinking = true`, `--show-thinking`, or the GUI's *thinking* toggle.

### Session log
Every CLI and GUI session appends its events to `~/.agent-neo/sessions/<timestamp>.jsonl` (no secrets; tool output previews are truncated).

### Environment & Files
- `NEO_ROOT` / `AGENT_NEO_ROOT`: default workspace root when `-w/--workspace` is not given.
- `NEO_HOME` / `AGENT_NEO_HOME`: settings and session-log folder (default `~/.agent-neo`).
- `NEO_BROWSER_PROFILE` / `AGENT_NEO_BROWSER_PROFILE`: override the dedicated Edge profile folder.

### Safety Thresholds & Iteration Bounds
Matches GitHub Copilot CLI's agent execution guardrails:
- **Sequential Tool Chunk Gating (default: 10 steps):** After every 10 autonomous tool actions, execution pauses for confirmation (`[Y (+10 steps) / <number> / abort]`) to prevent runaway loops. In `--yolo` mode, chunks auto-advance while reporting status.
- **Hard Ceiling (default: 255 steps):** Enforces an agentic session maximum iteration ceiling of 255 steps before requiring explicit continuation.
- Configurable via `--step-chunk <num>` and `--hard-ceiling <num>` CLI flags, or `step_chunk` / `hard_ceiling` in `neo.toml`.

---

## Interactive REPL Commands

- `you> <your task>`: Prompts the chat model and executes any tool calls returned in JSON protocol.
- `you> /workspace [path]` (or `/cd [path]`): Displays current workspace or switches to another workspace folder on the fly.
- `you> /new`: Starts a new conversation / topic in the current browser.
- `you> /fresh`: Recycles the browser process and launches a brand new browser instance.
- `you> /switch-account` (or `/logout`): Clears cookies & site storage only for the active web chat engine, navigates to its login page on-screen, and waits for sign-in.
- `you> /yolo`: Toggles auto-approval on/off dynamically during a session.
- `you> /steps [N]`: Shows current limits or updates the sequential step chunk size on the fly (e.g. `/steps 20`).
- `you> /skills`: Lists discovered skills across workspace and user/engine homes.
- `you> /agents`: Lists discovered Claude/Copilot-compatible sub-agent personas across workspace (`agents/`, `.github/agents/`, `.claude/agents/`, `.agent-neo/agents/`) and user/engine homes (`~/.copilot/agents/`, etc.).
- `you> /agent <name> <task>`: Runs one turn directly under a named agent persona and its enforced tool allowlist.
- `you> /backend <copilot|gemini|deepseek|whatsapp|desktop|auto|custom>`: Dynamically switch backends without restarting.
- `you> /quit` or `/exit`: Exits agent-neo.
- Ctrl+C while a turn runs cancels that turn (cooperatively) and returns to the prompt.

---

## Tests

All tests are isolated: they use scratch `NEO_HOME` and `WEBVIEW2_USER_DATA_FOLDER` folders and never touch `~/.agent-neo` or your Edge profile (except the live smoke).
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .     # offline unit tests (fake driver, no browser)
.\.venv\Scripts\python.exe tests\negative_controls.py              # proves the offline checks can fail (mutations)
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe tests\e2e\gui_e2e.py   # GUI e2e: real GUI, fake driver, Playwright over CDP 9482
.\.venv\Scripts\python.exe tests\negative_controls_gui.py [--only N,N]           # GUI mutations, about 1 minute each
.\.venv\Scripts\python.exe tests\e2e\live_smoke.py                 # ONE live turn: copilot backend, off-screen, real Edge profile
```
The live smoke refuses to start if anything listens on CDP 9365 or an Edge with the dedicated profile (`agent-neo-edge`) is running.

---

## Roadmap & To-Dos

See [TODO.md](TODO.md) for active backlog items:
- **Parallel sub-agents**: sub-agents run one at a time in 0.4.0.
- **MCP Server Integration**: Bridge local/remote MCP server tools into the agent-neo tool registry.
- **Directory & Workspace Search Tools**: Add `list_files` / `search_files` to agent-neo tools.
