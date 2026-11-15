"""The CLI renderer: --help (acceptance 1), REPL with the fake backend, settings refusal."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

from tests.support import REPO, Scratch, clean_env

from neo_events import read_session, validate_event

CLI = str(REPO / "neo.py")


def run_cli(args, stdin: str = "", env=None, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, *args], input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=timeout, cwd=str(REPO))


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("cli")
        self.home = self.s.path / "home"
        self.ws = self.s.path / "ws"
        self.home.mkdir()
        self.ws.mkdir()
        (self.ws / "a.txt").write_text("alpha\n", encoding="utf-8")
        self.env = clean_env()
        self.env["NEO_HOME"] = str(self.home)
        self.env.pop("AGENT_NEO_HOME", None)
        self.env.pop("NEO_FAKE_SCRIPT", None)
        self.env.pop("AGENT_NEO_FAKE_SCRIPT", None)

    def tearDown(self) -> None:
        self.s.cleanup()

    def test_help_shows_default_copilot_and_browser_mode(self) -> None:
        out = run_cli(["--help"], env=self.env).stdout
        self.assertIn("--backend {copilot,gemini,deepseek,whatsapp,desktop,auto,custom,web}", out)
        self.assertIn("default: copilot", out)
        self.assertIn("--browser-mode {offscreen,visible,headless}", out)
        self.assertNotIn("default: auto", out)
        self.assertNotIn("fake", out.lower())                 # the test-only flag stays hidden
        for flag in ("--workspace", "--port", "--step-chunk", "--hard-ceiling", "--yolo", "--new-session-only"):
            self.assertIn(flag, out)

    def test_version(self) -> None:
        out = run_cli(["--version"], env=self.env).stdout.strip()
        from neo_version import version_string
        self.assertEqual(out, version_string())

    def _script(self) -> str:
        p = self.s.path / "script.json"
        p.write_text(json.dumps({"scenarios": [{"match": ".", "replies": [
            {"type": "tool", "name": "write_file", "arguments": {"path": "n.txt", "content": "n"}, "thought": "secret plan"},
            {"type": "final", "message": "All done."},
        ]}]}), encoding="utf-8")
        return str(p)

    def test_repl_turn_with_fake_backend(self) -> None:
        stdin = "do it\ny\n/steps 3\n/steps\n/yolo\n/workspace\n/quit\n"
        r = run_cli(["-w", str(self.ws), "--fake-backend", self._script()], stdin, env=self.env)
        out = r.stdout
        self.assertEqual(r.returncode, 0, r.stderr)
        for expected in (
            "[Step 1/10 (Ceiling: 255)] Waiting for model...",
            "[Model raw reply]",
            "Approve tool call? write ",
            "[Tool result: write_file]",
            "=== FINAL ANSWER ===\nAll done.",
            "Step chunk updated to 3 steps (ceiling: 255).",
            "Current limits: safety chunk = 3 steps, ceiling = 255 steps.",
            "[yolo] YOLO mode ENABLED (auto-approving all tools)",
            f"Current workspace: {self.ws.resolve()}",
            "Commands: /new, /fresh, /switch-account, /backend <copilot|gemini|deepseek|whatsapp|desktop|auto|custom>, /workspace [path], /skills, /agents, /agent <name> <task>, /steps [N], /yolo, /quit",
            "Exiting agent-neo...",
        ):
            self.assertIn(expected, out)
        self.assertNotIn("(thinking)", out)                       # show_thinking defaults to off
        self.assertTrue((self.ws / "n.txt").exists())
        logs = list((self.home / "sessions").glob("*.jsonl"))
        self.assertEqual(len(logs), 1)
        events = read_session(logs[0])
        for e in events:
            validate_event(e)
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "turn_start")
        self.assertEqual(types[-1], "turn_end")
        self.assertIn("approval_request", types)
        statuses = [e["text"] for e in events if e["type"] == "status"]
        self.assertTrue(all(a != b for a, b in zip(statuses, statuses[1:])), "status heartbeats not de-duplicated")

    def test_show_thinking_flag(self) -> None:
        r = run_cli(["-w", str(self.ws), "--fake-backend", self._script(), "--show-thinking"], "do it\nn\n/quit\n",
                    env=self.env)
        self.assertIn("(thinking) secret plan", r.stdout)

    def test_malformed_settings_refuse_to_start(self) -> None:
        (self.home / "neo.toml").write_text("step_chunk = [\n", encoding="utf-8")
        r = run_cli(["-w", str(self.ws), "--fake-backend"], "/quit\n", env=self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("settings error", r.stderr)
        self.assertIn("neo.toml", r.stderr)
        self.assertNotIn("you>", r.stdout)

    def test_agents_slash_commands_and_switch_account_fake_backend(self) -> None:
        agents_dir = self.ws / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "reviewer.md").write_text(
            "---\nname: reviewer\ndescription: Read-only auditor\ntools: Read, Grep\n---\nCheck bugs carefully.\n",
            encoding="utf-8",
        )
        script_path = self.s.path / "agent_script.json"
        script_path.write_text(
            json.dumps({"scenarios": [{"match": ".", "replies": [{"type": "final", "message": "Audit complete."}]}]}),
            encoding="utf-8",
        )
        r = run_cli(
            ["-w", str(self.ws), "--fake-backend", str(script_path)],
            "/agents\n/switch-account\n/agent reviewer inspect repo\n/quit\n",
            env=self.env,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Discovered 1 agents: reviewer", r.stdout)
        self.assertIn("reviewer: Read-only auditor [tools: grep_search, read_file]", r.stdout)
        self.assertIn("does not support /switch-account.", r.stdout)
        self.assertIn("Audit complete.", r.stdout)


if __name__ == "__main__":
    unittest.main()
