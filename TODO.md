# Agent Neo (agent-neo) — To-Dos & Roadmap

## Planned Features

- [x] **LLM Sub-Agents (`run_subagent`)**: shipped in 0.4.0, opt-in (`enable_subagents`), one at a time, separate conversation, own step budget, no nesting.
- [ ] **Parallel sub-agents** (several tabs or headless sessions at once).
- [ ] **Specialised sub-agent personas** (exploration, documentation research, test execution).
- [ ] **GUI conversation history browser**, settings editor UI, installer (out of scope for 0.4.0).

- [ ] **MCP (Model Context Protocol) Server Integration**
  - Allow agent-neo to bridge external MCP tools directly into the agent-neo tool registry.

- [ ] **Enhanced File Operations**
  - Add directory listing / glob search tool (`list_files` / `search_files`) to the agent-neo tool protocol.
