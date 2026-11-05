# PLAN v0.5.0: Path Escapes, Robust JSON, UI Automation Hardening, Dark Theme & Custom Web Backend

Status: APPROVED on 2026-11-02. Target version **0.5.0**. Phase A stops at READY TO TAG.

## Scope & Decisions

| # | Topic | Resolution / Decision |
|---|---|---|
| D1 | **Home folder paths omitting `\`** | Pre-process raw Windows paths in `rewriteFileLinks` (`gui/lib/paths.js`) so backslashes before `_`, `*`, `~`, `[`, `]`, `\` are shielded from Markdown unescaping, and ensure `PATH_RE` matches folder names starting with underscores (e.g., `_agent365-home`). |
| D2 | **Robust JSON extraction** | Enhance `parse_action()` in `agent365_core.py` with balanced brace scanner for top-level `tool` and `final` JSON objects. If preceding text exists (e.g., "Coding and executing"), capture it as `thought` if the JSON does not already specify one. |
| D3 | **M365 Copilot Desktop send resilience** | In `m365_copilot_ui.py`, verify that the input box actually clears after sending. If text remains, re-focus, retry Send button / Enter up to 3 times with active focus check. If still uncleared, emit a user warning status. |
| D4 | **Dark High Contrast default theme** | Add GitHub Dark High Contrast palette (`#010409` bg, `#f0f3f6` fg, `#71b7ff` acc, `#ff9492` err, `#26cd4d` ok, `#30363d` line) as the default theme. Add a theme switcher (Dark High Contrast, Dark, Light) stored in `localStorage`. |
| D5 | **Custom web backend & pre-harness check** | Support `backend = "custom"` with `custom_url` in `agent365.toml` / CLI `--custom-url`. Implement `CustomWebCopilotDriver` with auto-heuristics and optional selector overrides in `agent365.toml` (`[custom_backend]`). Provide a pre-harness checklist verification: connectivity -> auth -> input detection -> send probe -> response extraction. |

---

## Detailed Implementation Workstreams

### 1. Path Escapes (`gui/lib/paths.js` & `gui/lib/md.js`)
- Problem: `C:\Users\alice.smith\_agent365-home` becomes `C:\Users\alice.smith_agent365-home` because `marked` treats `\_` as an escaped underscore `_`.
- Fix:
  - In `rewriteFileLinks(src)`: Match raw Windows paths in non-code blocks and convert them to explicit markdown file links `[path](#a365-file:...)` or protect backslashes with `\\` before `marked.parse` executes.
  - Verify regex `PATH_RE` in `gui/lib/paths.js` properly includes path segments starting with `_` and other allowed characters.
  - Test with paths containing `\_`, `\.`, `\-`, and spaces.

### 2. Robust JSON Extraction (`agent365_core.py`)
- Problem: Models occasionally emit preambles ("Coding and executing") followed by bare JSON, or text after JSON.
- Fix:
  - Implement a balanced-brace search in `parse_action(text: str)`:
    - First check fenced blocks ```json ... ```.
    - If no fenced block or if fenced block fails to parse, scan for balanced `{ ... }` substrings.
    - Check if parsed JSON has `"type"` in `("tool", "final")`.
    - If found, extract preceding non-empty text (if any) and inject as `action["thought"]` if `action` has no thought.
  - Add offline unit tests for:
    - Preambles before bare JSON.
    - Postscripts after JSON.
    - Multiple braces / nested structures inside commands.
    - PowerShell script blocks with braces inside arguments.

### 3. Desktop Automation Send Hardening (`m365_copilot_ui.py`)
- Problem: User keypresses/mouse clicks or window focus switches cause the prompt to stay in the composer without getting sent.
- Fix:
  - Add active focus check before typing and clicking (`focus_copilot()`).
  - Read input box text after triggering Send/Enter.
  - If text is still present after 1.0s, perform up to 3 retry attempts:
    - Re-activate / set focus on the edit element.
    - Try Send button click; fallback to `{ENTER}`.
  - If input remains uncleared after 3 attempts, emit status: `Prompt still in Copilot input box; please click Send or press Enter in M365 Copilot.`
  - Ensure receive logic continues gracefully if the user manually clicks Send.

### 4. GitHub Dark High Contrast Theme (`gui/app.css`, `gui/index.html`, `gui/app.js`)
- Palette from `https://terminalcolors.com/themes/github/dark-high-contrast/`:
  - Background: `#010409`
  - Paneling / Cards: `#0d1117`, `#161b22`
  - Borders / Lines: `#30363d`
  - Text / Foreground: `#f0f3f6`
  - Dim text: `#9ea7b3`
  - Accents: `#71b7ff` (blue), `#ff967d` (coral/accent), `#1f6feb` (button)
  - Status: `#26cd4d` (ok/success), `#f0b72f` (warn), `#ff9492` (err)
  - Code background: `#0d1117`
  - User message: `#13233a`
- Implementation:
  - Default theme is `dark-high-contrast`.
  - Add `data-theme` attribute on `<html>` (`dark-high-contrast`, `dark`, `light`).
  - Add theme toggle button in GUI header (`theme-toggle`), cycling or toggling between themes and persisting in `localStorage`.
  - Update `app.css` to define CSS variables for `[data-theme="dark-high-contrast"]`, `[data-theme="dark"]`, and `[data-theme="light"]`.

### 5. Custom Web Backend & Pre-Harness Checklist
- Architecture:
  - New driver class: `CustomWebCopilotDriver` in `custom_driver.py` (or integrated in `copilot_driver.py`).
  - Configuration in `agent365.toml`:
    ```toml
    [custom_backend]
    url = "https://my-llm-chat.corp/chat"
    input_selector = ""     # optional override
    send_selector = ""      # optional override
    bubble_selector = ""    # optional override
    ```
  - CLI flag `--custom-url <url>`.
  - GUI backend dropdown includes `custom`.
  - Pre-harness self-check:
    1. Connect to URL via Edge/CDP.
    2. Check if page is stuck on SSO/login redirect.
    3. Auto-detect input element (`textarea`, `[contenteditable="true"]`, `[role="textbox"]`).
    4. Auto-detect send trigger (`button[aria-label*="Send"]`, `button:has-text("Send")`, or Enter).
    5. Run canary test: send probe message `agent365 canary test` and verify bubble appearance.
    6. Report checklist results via status events:
       `[pre-harness] Connected to URL: OK`
       `[pre-harness] Input located: OK`
       `[pre-harness] Ready for tasks.`

---

## Acceptance Criteria
1. `C:\Users\alice.smith\_agent365-home` renders cleanly in the GUI as a clickable path with backslashes intact.
2. `parse_action()` correctly extracts tool calls when preceded by "Coding and executing" or other conversational text, attributing leading text to `thought`.
3. Desktop driver detects unsent input in composer, retries send with focus re-acquisition, and alerts user if blocked.
4. GUI defaults to GitHub Dark High Contrast theme, with working theme switcher and persistence.
5. `custom` backend loads custom URL, executes pre-harness checks, and reports readiness.
6. All existing and new unit & e2e tests pass.
