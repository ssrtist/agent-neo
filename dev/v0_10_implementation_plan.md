# Implementation Plan - Agent Neo v0.10

## 1. Objectives & Overview
Implement the **Turn-Reduction Pack**, resolve the **3 discovered runtime issues**, and introduce **Dynamic Multi-Engine Skills Discovery** prioritizing native engines.

---

## 2. Scope & Technical Architecture

### A. Fixes for Discovered Issues
1. **CRLF Line-Ending Preservation**:
   - In `neo_core.py` (`edit_file` and `write_file`): Detect original line endings (CRLF vs LF) by reading the file in binary mode or checking newline style before saving. Preserve existing style when replacing.
   - For new files, default to UTF-8 without BOM with system standard or preserve workspace conventions.
2. **Explicit Truncation Notice on `read_file`**:
   - Instead of silent slice `[:100_000]`, if the file content exceeds character/byte limits, append an explicit marker:
     ```
     [TRUNCATED: Showing first 100000 characters of <N> total characters. Use start_line/end_line to inspect specific sections.]
     ```
3. **Graceful Shell Timeout**:
   - In `neo_core.run_shell_command`: Catch `subprocess.TimeoutExpired` rather than letting it abort the turn uncaught. Return a structured error string:
     ```
     ERROR: Command timed out after 120 seconds. Partial output before termination:
     <captured output>
     ```

---

### B. Turn-Reduction Pack
1. **Batch Tool Protocol (`tools`)**:
   - Update `parse_action()` and the prompt to support:
     ```json
     {"type": "tools", "calls": [{"name": "read_file", "arguments": {...}}, {"name": "run_shell", "arguments": {...}}]}
     ```
   - Retain full backward-compatibility with single tool calls `{"type": "tool", "name": ..., "arguments": ...}`.
   - In `Agent._loop()`, execute each call sequentially in a single turn. Stop early if a tool returns an error or is denied by the user. Emit separate `tool_call` and `tool_result` events for each call.
2. **Read-Only Inspection Suite (No Approval Needed)**:
   - `list_dir`: `{"path": "relative/path"}` lists directory contents (files, dirs, sizes). Does not require approval.
   - `grep_search`: `{"pattern": "regex/string", "path": "optional relative/dir"}` scans text files with path and line numbers. Does not require approval.
   - `read_file` line offsets: Support optional arguments `"start_line": int, "end_line": int` with line number prefixes (`12 | ...`).

---

### C. Engine-Prioritized Multi-Tier Skills Discovery
Currently `discover_skills(root)` looks at a hardcoded list without considering the active backend.

**New Priority Rules**:
1. **Workspace Skills** (Highest Priority — overrides user-level skills):
   - `<workspace>/.agent-neo/skills/`
   - `<workspace>/.neo/skills/`
   - `<workspace>/.claude/skills/`
   - `<workspace>/.agents/skills/`
   - `<workspace>/skills/`
2. **User Home Skills (Engine-Prioritized)**:
   - Active engine's **native profile skills** first:
     - When `backend == "copilot"` or `"desktop"`: `~/.copilot/skills/`
     - When `backend == "gemini"`: `~/.gemini/skills/` (also checks `~/.antigravity/skills/` if present)
     - When `backend == "deepseek"`: `~/.deepseek/skills/`
   - Agent Neo's own user home skills:
     - `~/.agent-neo/skills/`
     - `~/.neo/skills/`
   - Fallback engine order for remaining skills:
     1. Copilot (`~/.copilot/skills/`)
     2. Gemini (`~/.gemini/skills/`)
     3. DeepSeek (`~/.deepseek/skills/`)
     4. Other agents (`~/.claude/skills/`, `~/.agents/skills/`, `~/.codex/skills/`)
3. **Deduplication**: If a skill with the same name exists in multiple folders, the higher-priority folder wins.

---

## 3. Verification Plan
- Unit tests in `tests/test_core.py` and `tests/test_protocol.py` for batch tool calling, line endings, timeout recovery, and truncation warning.
- Unit tests for `discover_skills()` verifying priority ordering when different backends are passed.
- Run full test suite: `python -m unittest discover -s tests -t .`
- Run negative controls: `python tests/negative_controls.py`.
