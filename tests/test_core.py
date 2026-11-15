"""The core loop driven by the fake driver, without any browser (plan v0.4 acceptance 2)."""
from __future__ import annotations

import json
import threading
import time
import unittest
from unittest import mock

from tests.support import Recorder, Scratch, ScriptedApprover, final, make_agent, script, tool

from neo_core import Agent, run_shell_command
from neo_events import Cancelled
from neo_fake import FakeChatDriver, FakeCopilotDriver
from neo_settings import Settings


class CoreLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("core")
        self.ws = self.s.path
        (self.ws / "a.txt").write_text("alpha\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.s.cleanup()

    # ---- tool -> approval -> final
    def test_tool_approval_final(self) -> None:
        appr = ScriptedApprover(True)
        agent, drv, rec = make_agent(self.ws, tool("write_file", path="out.txt", content="hi"), final("wrote it"),
                                     approver=appr)
        self.assertEqual(agent.run_turn("write out.txt"), "final")
        self.assertEqual((self.ws / "out.txt").read_text(encoding="utf-8"), "hi")
        self.assertEqual(rec.types()[0], "turn_start")
        self.assertEqual(rec.types()[-2:], ["final", "turn_end"])
        req = rec.of("approval_request")
        self.assertEqual(len(req), 1)
        self.assertEqual((req[0]["kind"], req[0]["tool"]), ("tool", "write_file"))
        self.assertEqual(appr.requests[0]["id"], req[0]["id"])
        res = rec.of("approval_result")[0]
        self.assertEqual((res["id"], res["answer"], res["auto"]), (req[0]["id"], True, False))
        tr = rec.of("tool_result")[0]
        self.assertTrue(tr["ok"])
        self.assertEqual(rec.of("final")[0]["message"], "wrote it")
        self.assertEqual(rec.of("turn_end")[0]["outcome"], "final")
        # Order: tool_call before approval before tool_result before the next step.
        t = rec.types()
        self.assertLess(t.index("tool_call"), t.index("approval_request"))
        self.assertLess(t.index("approval_result"), t.index("tool_result"))
        # The tool result went back to the model as untrusted data.
        self.assertIn("TOOL: write_file", drv.prompts[1])
        self.assertIn("untrusted data", drv.prompts[1])
        self.assertTrue(drv.prompts[0].endswith("USER REQUEST:\nwrite out.txt"))

    def test_denied_tool_is_reported_not_run(self) -> None:
        agent, drv, rec = make_agent(self.ws, tool("delete_file", path="a.txt"), final("ok"),
                                     approver=ScriptedApprover(False))
        self.assertEqual(agent.run_turn("delete a"), "final")
        self.assertTrue((self.ws / "a.txt").exists())
        tr = rec.of("tool_result")[0]
        self.assertFalse(tr["ok"])
        self.assertEqual(tr["preview"], "DENIED BY USER")

    def test_read_only_tools_need_no_approval(self) -> None:
        appr = ScriptedApprover()
        agent, drv, rec = make_agent(self.ws, tool("read_file", path="a.txt"), final("read"), approver=appr)
        agent.run_turn("read a")
        self.assertEqual(appr.requests, [])
        self.assertIn("alpha", rec.of("tool_result")[0]["preview"])

    def test_path_escape_is_an_error_result(self) -> None:
        agent, drv, rec = make_agent(self.ws, tool("read_file", path="..\\..\\Windows\\win.ini"), final("x"))
        agent.run_turn("escape")
        tr = rec.of("tool_result")[0]
        self.assertFalse(tr["ok"])
        self.assertIn("escapes the agent-neo workspace", tr["preview"])

    def test_yolo_auto_approves_with_event(self) -> None:
        appr = ScriptedApprover()
        agent, drv, rec = make_agent(self.ws, tool("write_file", path="y.txt", content="y"), final("done"),
                                     approver=appr, auto_approve=True)
        agent.run_turn("yolo write")
        self.assertEqual(appr.requests, [])
        res = rec.of("approval_result")[0]
        self.assertTrue(res["auto"])
        self.assertTrue((self.ws / "y.txt").exists())

    def test_yolo_still_asks_for_protected_paths(self) -> None:
        appr = ScriptedApprover(False, False, False)
        agent, drv, rec = make_agent(
            self.ws,
            tool("write_file", path=".agent-neo/neo.toml", content="auto_approve = true\n"),
            tool("write_file", path=".neo/neo.toml", content="auto_approve = true\n"),
            tool("write_file", path=".git/config", content="[core]\n"),
            final("done"),
            approver=appr,
            auto_approve=True,
        )
        agent.run_turn("write protected")
        self.assertEqual(len(appr.requests), 3)
        self.assertFalse((self.ws / ".agent-neo" / "neo.toml").exists())
        self.assertFalse((self.ws / ".neo" / "neo.toml").exists())
        self.assertFalse((self.ws / ".git" / "config").exists())

    # ---- v0.10: turn-reduction & bug fixes
    def test_batch_tool_execution(self) -> None:
        batch = {
            "type": "tools",
            "calls": [
                {"name": "list_dir", "arguments": {"path": "."}},
                {"name": "read_file", "arguments": {"path": "a.txt"}},
            ],
        }
        agent, drv, rec = make_agent(self.ws, batch, final("done batch"))
        self.assertEqual(agent.run_turn("inspect workspace"), "final")
        tool_results = rec.of("tool_result")
        self.assertEqual(len(tool_results), 2)
        self.assertEqual(tool_results[0]["name"], "list_dir")
        self.assertEqual(tool_results[1]["name"], "read_file")
        self.assertTrue(tool_results[0]["ok"])
        self.assertTrue(tool_results[1]["ok"])
        self.assertIn("DIRECTORY:", tool_results[0]["preview"])
        self.assertIn("alpha", tool_results[1]["preview"])

    def test_batch_tool_stops_early_on_denial(self) -> None:
        batch = {
            "type": "tools",
            "calls": [
                {"name": "delete_file", "arguments": {"path": "a.txt"}},
                {"name": "write_file", "arguments": {"path": "b.txt", "content": "beta"}},
            ],
        }
        # Deny the first tool
        agent, drv, rec = make_agent(self.ws, batch, final("done after stop"), approver=ScriptedApprover(False))
        self.assertEqual(agent.run_turn("try delete and write"), "final")
        tool_results = rec.of("tool_result")
        self.assertEqual(len(tool_results), 1)
        self.assertFalse(tool_results[0]["ok"])
        self.assertFalse((self.ws / "b.txt").exists())
        self.assertIn("NOTE: Remaining tools in the batch were NOT executed", drv.prompts[1])

    def test_read_file_line_ranges(self) -> None:
        sample = (self.ws / "multiline.txt")
        sample.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
        agent, drv, rec = make_agent(self.ws, tool("read_file", path="multiline.txt", start_line=2, end_line=4), final("got lines"))
        agent.run_turn("read slice")
        res = rec.of("tool_result")[0]["preview"]
        self.assertIn("lines 2-4 of 5", res)
        self.assertIn("2 | line2", res)
        self.assertIn("3 | line3", res)
        self.assertIn("4 | line4", res)
        self.assertNotIn("1 | line1", res)

    def test_grep_search(self) -> None:
        (self.ws / "searchme.py").write_text("def find_target_fn():\n    return 42\n", encoding="utf-8")
        agent, drv, rec = make_agent(self.ws, tool("grep_search", pattern="find_target_fn"), final("found"))
        agent.run_turn("search")
        res = rec.of("tool_result")[0]["preview"]
        self.assertIn("searchme.py:1: def find_target_fn()", res)

    def test_line_endings_preserved_on_edit(self) -> None:
        crlf_file = (self.ws / "crlf.txt")
        crlf_file.write_bytes(b"line1\r\nline2\r\nline3\r\n")
        agent, drv, rec = make_agent(self.ws, tool("edit_file", path="crlf.txt", old="line2", new="line_two"), final("edited"), approver=ScriptedApprover(True))
        agent.run_turn("edit crlf")
        raw = crlf_file.read_bytes()
        self.assertEqual(raw, b"line1\r\nline_two\r\nline3\r\n")
        self.assertNotIn(b"\r\r\n", raw)

    def test_shell_timeout_returns_error_instead_of_exception(self) -> None:
        # Command that times out with 0.1s timeout
        import neo_core as core
        res = core.run_shell_command("Start-Sleep -Seconds 5", self.ws, timeout=0.5)
        parsed = json.loads(res)
        self.assertEqual(parsed["exit_code"], -1)
        self.assertIn("timed out after 0.5 seconds", parsed["stderr"])

    def test_multi_hunk_edit_atomic_success(self) -> None:
        target = self.ws / "code.py"
        target.write_text("def a():\n    return 1\ndef b():\n    return 2\n", encoding="utf-8")
        edits = [
            {"old": "return 1", "new": "return 10"},
            {"old": "return 2", "new": "return 20"},
        ]
        agent, drv, rec = make_agent(self.ws, tool("edit_file", path="code.py", edits=edits), final("done"), approver=ScriptedApprover(True))
        agent.run_turn("edit both")
        self.assertEqual(target.read_text(encoding="utf-8"), "def a():\n    return 10\ndef b():\n    return 20\n")
        self.assertIn("2 hunks applied", rec.of("tool_result")[0]["preview"])

    def test_multi_hunk_edit_atomic_failure_rolls_back(self) -> None:
        target = self.ws / "code2.py"
        initial = "def a():\n    return 1\ndef b():\n    return 2\n"
        target.write_text(initial, encoding="utf-8")
        edits = [
            {"old": "return 1", "new": "return 10"},
            {"old": "NON_EXISTENT", "new": "broken"},
        ]
        agent, drv, rec = make_agent(self.ws, tool("edit_file", path="code2.py", edits=edits), final("done"), approver=ScriptedApprover(True))
        agent.run_turn("edit with failure")
        self.assertFalse(rec.of("tool_result")[0]["ok"])
        self.assertIn("EDIT FAILED (hunk #2 of 2)", rec.of("tool_result")[0]["preview"])
        self.assertEqual(target.read_text(encoding="utf-8"), initial)  # atomic rollback, return 1 was not changed

    def test_background_shell_and_task_lifecycle(self) -> None:
        agent, drv, rec = make_agent(
            self.ws,
            tool("run_shell", command="Write-Output 'bg_started'; Start-Sleep -Seconds 10", background=True),
            tool("read_task", task_id="task_1"),
            tool("kill_task", task_id="task_1"),
            final("done bg"),
            approver=ScriptedApprover(True, True, True),
        )
        agent.run_turn("bg turn")
        results = rec.of("tool_result")
        self.assertEqual(len(results), 3)
        self.assertIn("task_1", results[0]["preview"])
        self.assertIn("task_1", results[1]["preview"])
        self.assertIn("killed", results[2]["preview"])

    def test_web_fetch_html_cleaning_and_json(self) -> None:
        # Mock urllib.request.urlopen to test fetch_url
        from unittest.mock import MagicMock, patch
        html_content = b"<html><head><style>body {color:red}</style></head><body><h1>Title</h1><p>Paragraph text</p><script>alert(1)</script></body></html>"
        resp_mock = MagicMock()
        resp_mock.read.return_value = html_content
        resp_mock.headers.get.return_value = "text/html"
        resp_mock.headers.get_content_charset.return_value = "utf-8"
        resp_mock.__enter__.return_value = resp_mock

        with patch("urllib.request.urlopen", return_value=resp_mock):
            import neo_core
            res = neo_core.fetch_url("http://example.com")
            self.assertIn("Title", res)
            self.assertIn("Paragraph text", res)
            self.assertNotIn("alert(1)", res)
            self.assertNotIn("color:red", res)

    def test_ask_user_mid_turn(self) -> None:
        agent, drv, rec = make_agent(
            self.ws,
            tool("ask_user", question="Choose flavor", options=["vanilla", "chocolate"]),
            final("done flavor"),
            approver=lambda req: "chocolate" if req.get("kind") == "question" else True,
        )
        agent.run_turn("flavor turn")
        results = rec.of("tool_result")
        self.assertEqual(len(results), 1)
        self.assertIn("USER ANSWER: chocolate", results[0]["preview"])
        self.assertIn("TOOL: ask_user", drv.prompts[1])
        self.assertIn("USER ANSWER: chocolate", drv.prompts[1])

    def test_gui_session_approve_question(self) -> None:
        import neo_gui
        session = neo_gui.GuiSession(self.ws, {}, None, lambda ev: None)
        p = neo_gui.PendingApproval({"id": "q1", "kind": "question", "question": "Flavor?", "options": ["A", "B"]})
        session.pending["q1"] = p
        self.assertTrue(session.approve("q1", "B"))
        self.assertEqual(p.answer, "B")
        self.assertTrue(p.done.is_set())

        p2 = neo_gui.PendingApproval({"id": "q2", "kind": "question", "question": "Flavor?", "options": ["A", "B"]})
        session.pending["q2"] = p2
        self.assertTrue(session.approve("q2", False))
        self.assertIs(p2.answer, False)
        self.assertTrue(p2.done.is_set())

    def test_compact_checkpoint_restarts_conversation(self) -> None:
        agent, drv, rec = make_agent(self.ws, final("after compaction"))
        ckpt = agent.compact("initial user request")
        self.assertTrue(bool(ckpt))
        first_prompt = agent.first_prompt("continue task")
        self.assertIn("RESUMED FROM PREVIOUS COMPACTED CONVERSATION CHECKPOINT:", first_prompt)
        self.assertIn("continue task", first_prompt)

    # ---- thought
    def test_thought_event_only_when_present(self) -> None:
        agent, drv, rec = make_agent(self.ws, {**tool("read_file", path="a.txt"), "thought": "check a first"},
                                     final("done"))
        agent.run_turn("x")
        thoughts = rec.of("thought")
        self.assertEqual([t["text"] for t in thoughts], ["check a first"])

    # ---- protocol errors
    def test_invalid_json_asks_again(self) -> None:
        agent, drv, rec = make_agent(self.ws, "this is not json", {"type": "dance"}, final("ok"))
        self.assertEqual(agent.run_turn("x"), "final")
        errs = rec.of("error")
        self.assertEqual(len(errs), 2)
        self.assertFalse(any(e["fatal"] for e in errs))
        self.assertIn("not valid protocol JSON", drv.prompts[1])
        self.assertIn("Invalid action type", drv.prompts[2])

    # ---- cancel
    def test_cancel_while_waiting_for_reply(self) -> None:
        agent, drv, rec = make_agent(self.ws, {"reply": final("late"), "delay": 10})
        threading.Timer(0.4, agent.cancel.set).start()
        t0 = time.monotonic()
        self.assertEqual(agent.run_turn("slow"), "cancelled")
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual(rec.of("turn_end")[0]["outcome"], "cancelled")
        self.assertEqual(rec.of("final"), [])

    def test_cancel_while_approval_pending(self) -> None:
        holder: dict = {}

        def approver(req):
            holder["agent"].cancel.set()   # the user presses Stop while the card is open
            raise Cancelled("stopped at the approval card")

        agent, drv, rec = make_agent(self.ws, tool("write_file", path="c.txt", content="c"), final("x"),
                                     approver=approver)
        holder["agent"] = agent
        self.assertEqual(agent.run_turn("write c"), "cancelled")
        self.assertFalse((self.ws / "c.txt").exists())
        self.assertEqual(rec.of("tool_result"), [])

    def test_cancel_after_approval_still_stops(self) -> None:
        holder: dict = {}

        def approver(req):
            holder["agent"].cancel.set()
            return True                     # approved, but Stop arrived at the same time

        agent, drv, rec = make_agent(self.ws, tool("write_file", path="d.txt", content="d"), final("x"),
                                     approver=approver)
        holder["agent"] = agent
        self.assertEqual(agent.run_turn("write d"), "cancelled")
        self.assertFalse((self.ws / "d.txt").exists())

    def test_next_turn_after_cancel_works(self) -> None:
        agent, drv, rec = make_agent(self.ws, final("one"))
        agent.cancel.set()                   # a stale Stop from before the turn must not cancel it
        self.assertEqual(agent.run_turn("x"), "final")

    def test_run_shell_cancel_kills_process(self) -> None:
        flag = {"stop": False}
        threading.Timer(1.0, lambda: flag.update(stop=True)).start()
        t0 = time.monotonic()
        with self.assertRaises(Cancelled):
            run_shell_command("Start-Sleep -Seconds 30", self.ws, lambda: flag["stop"])
        self.assertLess(time.monotonic() - t0, 10)

    # ---- step threshold + ceiling
    def _reads(self, n: int):
        return [tool("read_file", path="a.txt") for _ in range(n)]

    def test_step_threshold_stop(self) -> None:
        appr = ScriptedApprover(0)
        agent, drv, rec = make_agent(self.ws, *self._reads(5), final("x"), approver=appr, step_chunk=2)
        self.assertEqual(agent.run_turn("loop"), "cancelled")
        self.assertEqual([r["kind"] for r in appr.requests], ["steps"])
        self.assertEqual(appr.requests[0]["step"], 2)
        self.assertEqual(len(drv.prompts), 2)

    def test_step_threshold_continue(self) -> None:
        appr = ScriptedApprover(2, 10)
        agent, drv, rec = make_agent(self.ws, *self._reads(5), final("done"), approver=appr, step_chunk=2)
        self.assertEqual(agent.run_turn("loop"), "final")
        self.assertEqual([(r["kind"], r["step"]) for r in appr.requests], [("steps", 2), ("steps", 4)])
        limits = [e["limit"] for e in rec.of("step_start")]
        self.assertEqual(limits, [2, 2, 4, 4, 14, 14])

    def test_step_threshold_yolo_auto_extends(self) -> None:
        appr = ScriptedApprover()
        agent, drv, rec = make_agent(self.ws, *self._reads(5), final("done"), approver=appr, step_chunk=2,
                                     auto_approve=True)
        self.assertEqual(agent.run_turn("loop"), "final")
        self.assertEqual(appr.requests, [])
        self.assertTrue(any("[yolo]" in e["text"] for e in rec.of("status")))

    def test_hard_ceiling_stop_and_continue(self) -> None:
        appr = ScriptedApprover(False)
        agent, drv, rec = make_agent(self.ws, *self._reads(3), final("x"), approver=appr, hard_ceiling=2)
        self.assertEqual(agent.run_turn("loop"), "ceiling")
        self.assertEqual([r["kind"] for r in appr.requests], ["ceiling"])

        appr = ScriptedApprover(True)
        agent, drv, rec = make_agent(self.ws, *self._reads(3), final("done"), approver=appr, hard_ceiling=2,
                                     step_chunk=10)
        self.assertEqual(agent.run_turn("loop"), "final")
        # The pending tool result is sent after the continue (0.3.0 dropped it).
        self.assertIn("TOOL: read_file", drv.prompts[2])

    # ---- driver failures
    def test_driver_error_retried_once(self) -> None:
        agent, drv, rec = make_agent(self.ws, {"error": "UI glitch"}, final("ok"))
        self.assertEqual(agent.run_turn("x"), "final")
        self.assertTrue(any("retrying once" in e["text"] for e in rec.of("status")))
        self.assertEqual(drv.prompts[0], drv.prompts[1])        # the same prompt was re-sent

    def test_driver_error_twice_ends_turn_with_error(self) -> None:
        agent, drv, rec = make_agent(self.ws, {"error": "down"}, {"error": "still down"})
        self.assertEqual(agent.run_turn("x"), "error")
        err = [e for e in rec.of("error") if e["fatal"]]
        self.assertEqual(len(err), 1)
        self.assertIn("still down", err[0]["message"])
        self.assertEqual(rec.of("turn_end")[0]["outcome"], "error")


class SubagentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("sub")
        self.ws = self.s.path

    def tearDown(self) -> None:
        self.s.cleanup()

    def test_disabled_hidden_and_refused(self) -> None:
        agent, drv, rec = make_agent(self.ws, tool("run_subagent", task="t"), final("x"))
        agent.run_turn("delegate")
        self.assertNotIn("run_subagent", drv.prompts[0])
        tr = rec.of("tool_result")[0]
        self.assertFalse(tr["ok"])
        self.assertIn("run_subagent is disabled", tr["preview"])
        self.assertEqual(rec.of("subagent_start"), [])
        self.assertEqual(drv.children, [])

    def _sub_script(self):
        return {
            "scenarios": [
                {"match": "^count", "replies": [tool("read_file", path="n.txt"), final("child says 3")]},
                {"match": ".", "replies": [tool("run_subagent", task="count the lines", context="file n.txt"),
                                           final("parent done")]},
            ],
        }

    def test_enabled_runs_child_in_separate_conversation(self) -> None:
        (self.ws / "n.txt").write_text("1\n2\n3\n", encoding="utf-8")
        drv = FakeCopilotDriver(self._sub_script())
        agent, drv, rec = make_agent(self.ws, driver=drv, enable_subagents=True)
        self.assertEqual(agent.run_turn("please delegate"), "final")
        self.assertIn('"name":"run_subagent"', drv.prompts[0])
        self.assertEqual(len(drv.children), 1)
        child = drv.children[0]
        self.assertTrue(child.closed)
        self.assertIn("SUB-AGENT", child.prompts[0])
        self.assertIn("TASK:\ncount the lines", child.prompts[0])
        self.assertIn("CONTEXT FROM THE PARENT AGENT:\nfile n.txt", child.prompts[0])
        self.assertNotIn('"name":"run_subagent"', child.prompts[0])      # no nesting advertised
        # The parent conversation never saw the child's prompts; it got the child's final message.
        self.assertEqual(len(drv.prompts), 2)
        self.assertIn("SUB-AGENT FINAL MESSAGE:\nchild says 3", drv.prompts[1])
        t = rec.types()
        self.assertEqual(t.count("subagent_start"), 1)
        self.assertEqual(t.count("subagent_end"), 1)
        self.assertLess(t.index("subagent_start"), t.index("subagent_event"))
        self.assertEqual(rec.of("subagent_end")[0]["outcome"], "final")
        inner = [e["event"]["type"] for e in rec.of("subagent_event")]
        self.assertIn("tool_result", inner)
        self.assertIn("final", inner)
        self.assertEqual(rec.of("final")[-1]["message"], "parent done")

    def test_child_budget_and_no_nesting(self) -> None:
        sc = {"scenarios": [
            {"match": "^loop", "replies": [tool("run_subagent", task="deeper")] + [tool("read_file", path="x")] * 5},
            {"match": ".", "replies": [tool("run_subagent", task="loop forever"), final("parent done")]},
        ]}
        drv = FakeCopilotDriver(sc)
        appr = ScriptedApprover()
        agent, drv, rec = make_agent(self.ws, driver=drv, approver=appr, enable_subagents=True,
                                     subagent_max_steps=3)
        self.assertEqual(agent.run_turn("go"), "final")
        inner = [e["event"] for e in rec.of("subagent_event")]
        nested = [e for e in inner if e["type"] == "tool_result" and e["name"] == "run_subagent"]
        self.assertIn("nested sub-agents are not allowed", nested[0]["preview"])
        self.assertEqual(len([e for e in inner if e["type"] == "step_start"]), 3)
        self.assertEqual(rec.of("subagent_end")[0]["outcome"], "ceiling")
        self.assertEqual(appr.requests, [])                  # no step/ceiling prompts inside a child
        self.assertIn("ERROR: the sub-agent ended without a final answer", drv.prompts[1])

    def test_yolo_off_mid_run_reaches_a_running_child(self) -> None:
        # code review r1: the child used to snapshot yolo when it started
        sc = {"scenarios": [
            {"match": "^write", "replies": [tool("write_file", path="w1.txt", content="1"),
                                            tool("write_file", path="w2.txt", content="2"), final("written")]},
            {"match": ".", "replies": [tool("run_subagent", task="write two"), final("ok")]},
        ]}
        appr = ScriptedApprover(False)
        agent, drv, rec = make_agent(self.ws, driver=FakeCopilotDriver(sc), approver=appr, enable_subagents=True,
                                     auto_approve=True)

        def sink(event):
            rec(event)
            inner = event.get("event") or {}
            if event["type"] == "subagent_event" and inner.get("type") == "approval_result" and inner.get("auto"):
                agent.auto_approve = False      # the user clicks yolo off while the child runs
        agent.sink = sink
        agent.run_turn("go")
        self.assertTrue((self.ws / "w1.txt").exists())
        self.assertEqual([(r["kind"], r.get("tool")) for r in appr.requests], [("tool", "write_file")])
        self.assertFalse((self.ws / "w2.txt").exists())

    def test_child_approval_goes_to_the_same_approver(self) -> None:
        sc = {"scenarios": [
            {"match": "^write", "replies": [tool("write_file", path="w.txt", content="w"), final("written")]},
            {"match": ".", "replies": [tool("run_subagent", task="write w"), final("ok")]},
        ]}
        appr = ScriptedApprover(True)
        agent, drv, rec = make_agent(self.ws, driver=FakeCopilotDriver(sc), approver=appr, enable_subagents=True)
        agent.run_turn("go")
        self.assertEqual([r["kind"] for r in appr.requests], ["tool"])
        self.assertTrue((self.ws / "w.txt").exists())

    def test_cancel_inside_child_cancels_parent(self) -> None:
        sc = {"scenarios": [
            {"match": "^slow", "replies": [{"reply": final("late"), "delay": 10}]},
            {"match": ".", "replies": [tool("run_subagent", task="slow task"), final("never")]},
        ]}
        agent, drv, rec = make_agent(self.ws, driver=FakeCopilotDriver(sc), enable_subagents=True)
        threading.Timer(0.5, agent.cancel.set).start()
        self.assertEqual(agent.run_turn("go"), "cancelled")
        self.assertEqual(rec.of("subagent_end")[0]["outcome"], "cancelled")
        self.assertTrue(drv.children[0].closed)

    def test_backend_without_child_support_restarts_parent_context(self) -> None:
        class NoChildDriver:
            """A desktop-like driver: one conversation at a time, no open_child; answers by prompt content."""

            def __init__(self):
                self.prompts = []
                self.new_conversations = 0

            def send_message(self, prompt, timeout_seconds=180, status_callback=None):
                self.prompts.append(prompt)
                if "TASK:\nchild task" in prompt:
                    return json.dumps(final("child result"))
                if "SUB-AGENT FINAL MESSAGE" in prompt:
                    return json.dumps(final("parent done"))
                return json.dumps(tool("run_subagent", task="child task"))

            def new_conversation(self):
                self.new_conversations += 1

            def close(self):
                pass

        drv = NoChildDriver()
        rec = Recorder()
        agent = Agent(drv, self.ws, Settings(enable_subagents=True), rec, ScriptedApprover(), skills={})
        self.assertEqual(agent.run_turn("go"), "final")
        self.assertEqual(drv.new_conversations, 2)           # one for the child, one to leave it
        parent_next = drv.prompts[2]
        self.assertIn("You are the reasoning component", parent_next)
        self.assertIn("USER REQUEST:\ngo", parent_next)
        self.assertIn("restarted after a sub-agent ran", parent_next)
        self.assertIn("SUB-AGENT FINAL MESSAGE:\nchild result", parent_next)


class FakeDriverTests(unittest.TestCase):
    def test_script_selection_and_fallback(self) -> None:
        d = FakeCopilotDriver(script(final("a"), match="^hello"))
        self.assertEqual(json.loads(d.send_message("You are the reasoning component...\nUSER REQUEST:\nhello"))["message"], "a")
        self.assertEqual(json.loads(d.send_message("TOOL: x"))["message"], "fallback")
        # A tool result that quotes "USER REQUEST:" does not restart the script.
        d2 = FakeCopilotDriver(script(final("1"), final("2")))
        d2.send_message("You are the reasoning component\nUSER REQUEST:\nx")
        self.assertEqual(json.loads(d2.send_message("RESULT:\nUSER REQUEST:\nx"))["message"], "2")


class TaskAndWakeupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("tasks")
        self.ws = self.s.path / "ws"
        self.ws.mkdir()
        self.agents: list[Agent] = []

    def tearDown(self) -> None:
        for ag in self.agents:
            ag.close()
        self.s.cleanup()

    def test_background_shell_marks_ok_and_does_not_abort_batch(self) -> None:
        batch = {
            "type": "tools",
            "calls": [
                {"name": "run_shell", "arguments": {"command": "Write-Output 'ok_bg'; Start-Sleep -Seconds 5", "background": True}},
                {"name": "list_tasks", "arguments": {}},
            ],
        }
        agent, drv, rec = make_agent(self.ws, batch, final("batch done"), approver=ScriptedApprover(True))
        self.agents.append(agent)
        self.assertEqual(agent.run_turn("batch bg"), "final")
        results = rec.of("tool_result")
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["ok"], results[0]["preview"])
        self.assertTrue(results[1]["ok"], results[1]["preview"])
        self.assertIn("task_1", results[1]["preview"])

    def test_wait_task_early_exit_timeout_and_cancel(self) -> None:
        import neo_core as core

        agent, drv, rec = make_agent(
            self.ws,
            tool("run_shell", command="Write-Output 'finished_fast'", background=True),
            tool("wait_task", task_id="task_1", timeout=15),
            final("waited"),
            approver=ScriptedApprover(True),
        )
        self.agents.append(agent)
        self.assertEqual(agent.run_turn("wait fast"), "final")
        wait_res = json.loads(rec.of("tool_result")[1]["preview"])
        self.assertFalse(wait_res["timed_out"])
        self.assertEqual(wait_res["exit_code"], 0)
        self.assertIn("finished_fast", wait_res["stdout"])

        # Timeout case
        tm = core.TaskManager(self.ws)
        try:
            started = tm.start_task("Start-Sleep -Seconds 10")
            timed = tm.wait_task(started["task_id"], timeout=0.3)
            self.assertTrue(timed["timed_out"])
            self.assertEqual(timed["status"], "running")

            # Cancel case
            cancelled = True
            with self.assertRaises(Cancelled):
                tm.wait_task(started["task_id"], timeout=5, cancel_check=lambda: cancelled)
        finally:
            tm.kill_all()

    def test_task_errors_mark_ok_false_and_kill_all_no_deadlock(self) -> None:
        agent, drv, rec = make_agent(
            self.ws,
            tool("read_task", task_id="missing_task"),
            tool("kill_task", task_id="missing_task"),
            tool("run_shell", command="Start-Sleep -Seconds 10", background=True),
            final("checked"),
            approver=ScriptedApprover(True),
        )
        self.agents.append(agent)
        self.assertEqual(agent.run_turn("err checks"), "final")
        results = rec.of("tool_result")
        self.assertFalse(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertTrue(results[2]["ok"])

        # kill_all must not deadlock and must terminate running task
        agent.task_manager.kill_all()
        tasks = agent.task_manager.list_tasks()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertNotEqual(tasks[0]["status"], "running")

    def test_set_workspace_updates_task_manager_root(self) -> None:
        other = self.s.path / "other"
        other.mkdir()
        agent, _, _ = make_agent(self.ws, final("ok"))
        self.agents.append(agent)
        agent.set_workspace(other)
        self.assertEqual(agent.task_manager.root, other.resolve())

    def test_clean_powershell_env_strips_ps7_paths(self) -> None:
        import neo_core as core

        dirty = {
            "PATH": r"C:\Windows\System32",
            "PSModulePath": (
                r"C:\Program Files\PowerShell\7\Modules;"
                r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.7.0.0_x64__8wekyb3d8bbwe\Modules;"
                r"C:\WINDOWS\system32\WindowsPowerShell\v1.0\Modules"
            ),
        }
        cleaned = core._clean_powershell_env(dirty)
        self.assertEqual(cleaned["PSModulePath"], r"C:\WINDOWS\system32\WindowsPowerShell\v1.0\Modules")

    def test_run_shell_per_call_timeout_override(self) -> None:
        agent, drv, rec = make_agent(
            self.ws,
            tool("run_shell", command="Start-Sleep -Seconds 5", timeout=0.3),
            final("done"),
            approver=ScriptedApprover(True),
            shell_timeout=60,
        )
        self.agents.append(agent)
        self.assertEqual(agent.run_turn("timeout override"), "final")
        res = rec.of("tool_result")[0]
        self.assertFalse(res["ok"])
        self.assertIn("timed out after 0.3 seconds", res["preview"])

    def test_watch_task_and_timer_reactive_wakeup(self) -> None:
        wakeups: list[dict] = []
        fired = threading.Event()

        def on_wakeup(payload: dict) -> None:
            wakeups.append(payload)
            fired.set()

        drv = FakeCopilotDriver(script(
            tool("run_shell", command="Write-Output 'build_ok'", background=True),
            tool("watch_task", task_id="task_1", prompt="Continue to M3", poll_interval=0.2),
            final("watching now"),
        ))
        rec = Recorder()
        agent = Agent(drv, self.ws, Settings(), rec, ScriptedApprover(True), skills={}, on_wakeup=on_wakeup)
        agent.retry_delay = 0.01
        self.agents.append(agent)

        self.assertEqual(agent.run_turn("start watch"), "final")
        self.assertTrue(fired.wait(timeout=10), "watch_task wakeup did not fire")
        self.assertEqual(len(wakeups), 1)
        self.assertIn("task_1 exited (0)", wakeups[0]["prompt"])
        self.assertIn("build_ok", wakeups[0]["prompt"])
        self.assertIn("Continue to M3", wakeups[0]["prompt"])

        # Timer watch + unwatch_task cancellation
        cancelled_w = agent.task_manager.watch_task(prompt="never fire", delay_seconds=5)
        unwatch_res = agent.task_manager.unwatch_task(watch_id=cancelled_w["watch_id"])
        self.assertEqual(unwatch_res["cancelled"], [cancelled_w["watch_id"]])

        fired.clear()
        timer_w = agent.task_manager.watch_task(prompt="timer ping", delay_seconds=0.2)
        self.assertEqual(timer_w["status"], "watching")
        self.assertTrue(fired.wait(timeout=5), "timer watch did not fire")
        self.assertIn("timer elapsed", wakeups[-1]["reason"])
        self.assertIn("timer ping", wakeups[-1]["prompt"])

    def test_discover_agents_precedence_and_claude_frontmatter(self) -> None:
        from neo_core import discover_agents, discover_skills

        home = self.s.path / "fake_home"
        # Lowest tier: ~/.agent-neo/agents and inactive engine ~/.gemini/agents
        (home / ".agent-neo" / "agents").mkdir(parents=True)
        (home / ".agent-neo" / "agents" / "shared.md").write_text(
            "---\nname: shared\ndescription: from neo home\ntools: Read\n---\nNeo home.\n",
            encoding="utf-8",
        )
        # Active engine home: ~/.copilot/agents overrides ~/.agent-neo/agents
        (home / ".copilot" / "agents").mkdir(parents=True)
        (home / ".copilot" / "agents" / "shared.md").write_text(
            "---\nname: shared\ndescription: from copilot home\ntools: Read, Grep\n---\nCopilot home.\n",
            encoding="utf-8",
        )
        # Workspace root agents/ overrides home
        (self.ws / "agents").mkdir(parents=True)
        (self.ws / "agents" / "reviewer.agent.md").write_text(
            "---\n"
            "name: reviewer\n"
            "description: Strict reviewer\n"
            "tools:\n"
            "  - Read\n"
            "  - Grep\n"
            "  - Glob\n"
            "  - Bash\n"
            "disallowedTools: Bash\n"
            "maxTurns: 4\n"
            "model: sonnet\n"
            "---\n"
            "Review code carefully without editing.\n",
            encoding="utf-8",
        )
        # README without frontmatter must be ignored
        (self.ws / "agents" / "README.md").write_text("# Agents folder\n", encoding="utf-8")
        # Workspace .agent-neo/agents overrides root agents/
        (self.ws / ".agent-neo" / "agents").mkdir(parents=True)
        (self.ws / ".agent-neo" / "agents" / "override.md").write_text(
            "---\ndescription: WS override\ntools: Write, Edit\n---\nBody text.\n",
            encoding="utf-8",
        )

        agents = discover_agents(self.ws, backend="copilot", home=home)
        self.assertEqual(agents["shared"]["description"], "from copilot home")
        self.assertIn("reviewer", agents)
        self.assertNotIn("README", agents)
        rev = agents["reviewer"]
        self.assertEqual(rev["max_turns"], 4)
        self.assertEqual(rev["model"], "sonnet")
        self.assertEqual(rev["allowed_tools"], frozenset({"read_file", "grep_search", "list_dir"}))
        self.assertIn("override", agents)
        self.assertEqual(agents["override"]["allowed_tools"], frozenset({"write_file", "edit_file"}))

        # DeepSeek engine home check for both discover_skills and discover_agents
        (home / ".deepseek" / "skills" / "ds-skill").mkdir(parents=True)
        (home / ".deepseek" / "skills" / "ds-skill" / "SKILL.md").write_text(
            "---\nname: ds-skill\ndescription: DeepSeek skill\n---\nBody\n",
            encoding="utf-8",
        )
        (home / ".deepseek" / "agents").mkdir(parents=True)
        (home / ".deepseek" / "agents" / "shared.md").write_text(
            "---\nname: shared\ndescription: from deepseek home\ntools: Read\n---\nDS home.\n",
            encoding="utf-8",
        )
        with mock.patch("pathlib.Path.home", return_value=home):
            ds_skills = discover_skills(self.ws, backend="deepseek")
            self.assertIn("ds-skill", ds_skills)
        ds_agents = discover_agents(self.ws, backend="deepseek", home=home)
        self.assertEqual(ds_agents["shared"]["description"], "from deepseek home")

    def test_persona_subagent_and_direct_agent_turn_enforce_allowed_tools(self) -> None:
        from neo_core import discover_agents

        (self.ws / "note.txt").write_text("hello world\n", encoding="utf-8")
        (self.ws / "agents").mkdir(parents=True)
        (self.ws / "agents" / "reader.md").write_text(
            "---\nname: reader\ndescription: Read only agent\ntools: Read\nmaxTurns: 5\n---\nOnly read files.\n",
            encoding="utf-8",
        )

        # 1. Parent invokes run_subagent {"agent": "reader", ...}; child tries write_file (blocked) then read_file (allowed)
        parent_drv = FakeCopilotDriver(script(
            tool("run_subagent", task="Inspect note", agent="reader"),
            final("parent done"),
        ))
        child_drv = FakeCopilotDriver(script(
            tool("write_file", path="evil.txt", content="nope"),
            tool("read_file", path="note.txt"),
            final("child read hello world"),
        ))
        rec = Recorder()
        s = Settings(enable_subagents=True, auto_approve=True)
        agent = Agent(parent_drv, self.ws, s, rec, ScriptedApprover(True))
        agent.retry_delay = 0.01
        self.agents.append(agent)

        with mock.patch("neo_core.open_child", return_value=(child_drv, True)), \
                mock.patch("neo_core.close_child"):
            self.assertEqual(agent.run_turn("delegate to reader"), "final")

        self.assertIn("AVAILABLE SUB-AGENTS", parent_drv.prompts[0])
        self.assertIn("- reader: Read only agent [tools: read_file]", parent_drv.prompts[0])
        self.assertIn("ACTIVE AGENT PERSONA (reader):", child_drv.prompts[0])
        self.assertIn("ALLOWED TOOLS ONLY: read_file.", child_drv.prompts[0])
        self.assertFalse((self.ws / "evil.txt").exists())
        child_events = [e["event"] for e in rec.of("subagent_event")]
        child_results = [e for e in child_events if e["type"] == "tool_result"]
        self.assertFalse(child_results[0]["ok"])
        self.assertIn("not permitted", child_results[0]["preview"])
        self.assertTrue(child_results[1]["ok"])

        # 2. Direct user /agent turn works even when enable_subagents=False and cleans up persona after turn
        direct_drv = FakeCopilotDriver(script(
            tool("write_file", path="evil2.txt", content="nope"),
            final("checked"),
        ))
        rec2 = Recorder()
        agent2 = Agent(
            direct_drv, self.ws, Settings(enable_subagents=False, auto_approve=True),
            rec2, ScriptedApprover(True),
            agents=discover_agents(self.ws, home=self.s.path / "nohome"),
        )
        agent2.retry_delay = 0.01
        self.agents.append(agent2)
        self.assertEqual(agent2.run_agent_turn("reader", "check note.txt"), "final")
        self.assertFalse((self.ws / "evil2.txt").exists())
        self.assertIn("ACTIVE AGENT PERSONA (reader):", direct_drv.prompts[0])
        self.assertIsNone(agent2.persona)
        self.assertIsNone(agent2.allowed_tools)
        self.assertEqual(agent2.hard_ceiling, 255)

        # 3. Unknown subagent returns clean error
        bad_drv = FakeCopilotDriver(script(
            tool("run_subagent", task="do stuff", agent="nonexistent"),
            final("handled"),
        ))
        rec3 = Recorder()
        agent3 = Agent(bad_drv, self.ws, Settings(enable_subagents=True), rec3, ScriptedApprover(True))
        agent3.retry_delay = 0.01
        self.agents.append(agent3)
        self.assertEqual(agent3.run_turn("unknown persona"), "final")
        err_res = rec3.of("tool_result")[0]
        self.assertFalse(err_res["ok"])
        self.assertIn("Unknown sub-agent persona", err_res["preview"])


if __name__ == "__main__":
    unittest.main()
