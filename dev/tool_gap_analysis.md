# Agent Neo: tool-calling gaps vs Copilot CLI and Antigravity CLI

## What Agent Neo has today (0.9.0, from `neo_core.execute_tool`)

| Tool | Behaviour | Needs approval? |
|:--|:--|:--|
| `read_file` | Whole file, silently cut off at 100,000 characters; no line ranges or line numbers | No |
| `write_file` | Overwrites the whole file | Yes |
| `edit_file` | One exact-match replacement (fails unless the text appears exactly once) | Yes |
| `delete_file` | Deletes one file | Yes |
| `run_shell` | PowerShell, blocks until done, hard 120 s timeout, no background processes | **Yes, every time, even read-only commands** |
| `load_skill` | Inlines a SKILL.md | No |
| `run_subagent` | Opt-in; one at a time; no nesting | Inherits |

Protocol limits: **one tool call per model reply**, JSON in plain text (web chats have no native function calling), tool output is pasted back into the chat whole.

> [!IMPORTANT]
> Where Agent Neo differs: every tool step is a full **web chat round trip** (typing into the page, waiting for the reply, scraping it), typically 10-60 s, and the chat composers limit how much text you can paste in. So the gaps that cut **round trips** and **pasted text** matter far more for us than for Copilot CLI or Antigravity CLI, which call model APIs directly.

## Gap table

Importance = value to Agent Neo specifically. Effort: S = under a day, M = 1-3 days, L = a week or more.

| # | Capability | Copilot CLI | Antigravity CLI | Agent Neo | Importance | Effort |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | **Several tool calls in one reply** (batching) | Yes, parallel | Yes, parallel | One per reply | 🔴 Critical | M |
| 2 | **Tool-output budgeting** (truncate, page, or spill to a file; tell the model it was cut) | Truncates large output, saves the rest to a temp file | Byte/line limits plus a hint to keep reading | 100 K silent cut-off; may exceed the chat's input limit | 🔴 Critical | S-M |
| 3 | **Read-only search tools**: `grep` (ripgrep), `glob`/find, `list_dir` | `grep`, `glob`, `view` on directories | List/search tools | Only through `run_shell`, which asks for approval every time | 🟠 High | S |
| 4 | **Line ranges and line numbers in `read_file`** | `view_range` | `StartLine`/`EndLine` | Whole file only | 🟠 High | S |
| 5 | **Finer approval rules**: allow-lists (e.g. `git status`, `Get-ChildItem`), "always allow this for the session", deny patterns | `--allow-tool` / `--deny-tool` patterns, approve-for-session | Permission policy | Yes / no per call, or yolo on everything | 🟠 High | M |
| 6 | **Multiple edits per call**, replace-all, clear diagnostics when a match fails (show the nearest match) | `str_replace` (made in parallel) | Multiple replacements per call | One exact match | 🟠 High | S |
| 7 | **Context management**: summarise and continue in a fresh chat when the conversation gets long | Auto-compacts, `/compact` | Automatic summary checkpoints | None (only the post-sub-agent restart) | 🟠 High | M |
| 8 | **Background / long-running shell**: async start, read output later, stdin, stop; per-call timeout | `bash` sessions with read/write/stop | `run_command` async plus `manage_task` | Blocking, 120 s fixed | 🟡 Medium | M |
| 9 | **To-do / plan tool** the model updates (survives a context reset) | `update_todo` | Task artifacts | None | 🟡 Medium | S |
| 10 | **Project instruction auto-load** (`AGENTS.md`, `.github/copilot-instructions.md`) and memory | Yes, plus `store_memory` | Rules and memory | Only `custom_instructions_file` | 🟡 Medium | S |
| 11 | **Resume a session** (`--resume`/`--continue`) | Yes | Yes | Logs exist, no resume | 🟡 Medium | M |
| 12 | **Ask-the-user tool** with choices, mid-turn | `ask_user` | `ask_question` | Model must end the turn to ask | 🟡 Medium | S |
| 13 | **Parallel / specialised sub-agents** (read-only explorer, code reviewer, custom agents) | `task` with agent types, background agents | Defined subagents, parallel, branched workspaces | One generic sub-agent at a time | 🟡 Medium | L |
| 14 | **MCP client** (use external MCP tools) | Built in (GitHub MCP preloaded) | Yes, eager and lazy loading | None | 🟡 Medium (strategic) | L |
| 15 | **Checkpoint / undo** of file changes | Via git | Via git | None | 🟡 Medium | M |
| 16 | `web_fetch` / web search | `web_fetch` | `search_web`, read URL | None | 🟢 Low: Copilot, Gemini and DeepSeek can browse on their own | S |
| 17 | Image / binary viewing | Images in `view` | Images and video in `read_file` | Text only | 🟢 Low | M-L |
| 18 | Hooks (pre/post tool), scheduling | Hooks | Hooks, `schedule` | None | 🟢 Low | M |
| 19 | `apply_patch` / unified diffs | Some models | No | No | 🟢 Low (web models format diffs unreliably) | M |

## Defects found while reviewing the code

> [!WARNING]
> - **Line endings get flipped on edit.** `edit_file`/`write_file` use `Path.read_text`/`write_text`, which on Windows read CRLF as LF and write LF as CRLF. Editing an LF file (most of this repo) rewrites **every line** as CRLF, which produces noisy diffs. Fix: keep the file's original newline style and BOM. 🟠 High, S.
> - **`read_file` truncates silently** at 100 K characters; the model thinks it saw the whole file. Covered by #2 and #4.
> - **The shell timeout surfaces as an exception**, not as a readable "timed out after 120 s, partial output: ..." result.

## Recommended next release (v0.10, "fewer round trips")

1. **Batch tool calls** (#1): accept `{"type":"tools","calls":[...]}`, run them in order, and stop at the first denial. Single calls stay valid, so prompts written for 0.3.0 still work.
2. **Output budget** (#2), checked per backend's composer limit (to measure, not assume, for M365 Copilot, consumer Copilot, Gemini and DeepSeek).
3. **Read-only toolset with no approval prompts** (#3, #4): `read_file` with line ranges and numbers, `list_dir`, `grep`, `glob`.
4. **Multi-edit plus line-ending preservation** (#6 and the defect fix).
5. **Session allow-list** (#5), starting with "always allow this exact command for this session" plus a built-in read-only allow-list.

Then v0.11: context compaction (#7), to-do tool (#9), AGENTS.md auto-load (#10), background shell (#8).
