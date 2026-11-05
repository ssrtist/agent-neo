"""Event schema round trip, session log, status de-duplication (plan v0.4 section 1)."""
from __future__ import annotations

import json
import unittest

from tests.support import Scratch

from neo_events import (
    SCHEMA,
    EventFactory,
    SessionLog,
    dedupe_status,
    fanout,
    public,
    read_session,
    truncate,
    truncate_args,
    validate_event,
)

SAMPLES = {
    "turn_start": {"text": "list files", "workspace": "C:/ws"},
    "step_start": {"step": 1, "limit": 10, "ceiling": 255},
    "status": {"text": "agent-neo is working..."},
    "thought": {"text": "look first", "step": 1},
    "reply": {"text": "{}", "length": 2, "step": 1},
    "tool_call": {"name": "run_shell", "args": {"command": "dir"}, "summary": "dir", "step": 1},
    "tool_result": {"name": "run_shell", "ok": True, "preview": "{}", "length": 2, "step": 1},
    "subagent_start": {"sub_id": "sub-1-1", "task": "t", "context": "", "step": 1},
    "subagent_event": {"sub_id": "sub-1-1", "event": {"v": 1, "type": "status", "ts": 1, "seq": 1, "turn": 1, "text": "x"}},
    "subagent_end": {"sub_id": "sub-1-1", "outcome": "final", "message": "m"},
    "approval_request": {"id": "a1", "kind": "tool", "summary": "write x", "tool": "write_file", "args": {}},
    "approval_result": {"id": "a1", "kind": "tool", "answer": True, "auto": False},
    "final": {"message": "**done**", "step": 2},
    "error": {"message": "boom", "fatal": True},
    "turn_end": {"outcome": "final"},
}


class EventSchemaTests(unittest.TestCase):
    def test_every_type_has_a_sample(self) -> None:
        self.assertEqual(set(SAMPLES), set(SCHEMA))

    def test_round_trip_through_json(self) -> None:
        f = EventFactory()
        f.turn = 3
        for etype, fields in SAMPLES.items():
            with self.subTest(etype=etype):
                ev = f.make(etype, **fields)
                validate_event(ev)
                back = json.loads(json.dumps(ev))
                validate_event(back)
                self.assertEqual(back, ev)
                self.assertEqual((back["v"], back["type"], back["turn"]), (1, etype, 3))
        seqs = [f.make("status", text=str(i))["seq"] for i in range(3)]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), 3)

    def test_validation_rejects_bad_events(self) -> None:
        f = EventFactory()
        good = f.make("step_start", step=1, limit=2, ceiling=3)
        bad_cases = [
            {**good, "type": "nope"},
            {k: v for k, v in good.items() if k != "limit"},
            {**good, "step": "1"},
            {**good, "step": True},                      # bool is not an int here
            {k: v for k, v in good.items() if k != "seq"},
            f.make("turn_end", outcome="finished"),       # not a known outcome
            f.make("approval_request", id="x", kind="maybe", summary=""),
        ]
        for bad in bad_cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_event(bad)

    def test_public_strips_private_fields_recursively(self) -> None:
        ev = {"type": "subagent_event", "_x": 1, "event": {"type": "reply", "_full": "secret-ish", "text": "t"}}
        out = public(ev)
        self.assertNotIn("_x", out)
        self.assertNotIn("_full", out["event"])
        self.assertIn("_full", ev["event"])               # the original is not mutated

    def test_truncation(self) -> None:
        self.assertEqual(truncate("abc", 5), "abc")
        t = truncate("x" * 50, 10)
        self.assertTrue(t.startswith("x" * 10))
        self.assertIn("50 chars", t)
        args = truncate_args({"content": "y" * 5000, "n": 3}, limit=100)
        self.assertLess(len(args["content"]), 200)
        self.assertEqual(args["n"], 3)


class SessionLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("events")

    def tearDown(self) -> None:
        self.s.cleanup()

    def test_log_round_trip_lazy_and_public(self) -> None:
        log = SessionLog(self.s.path, stamp="t1")
        self.assertFalse(log.path.exists())               # nothing written before the first event
        f = EventFactory()
        f.turn = 1
        evs = [f.make("turn_start", text="hi"), f.make("reply", text="r", length=1, _full="FULL"),
               f.make("turn_end", outcome="final")]
        for e in evs:
            log.write(e)
        self.assertEqual(log.path, self.s.path / "sessions" / "t1.jsonl")
        back = read_session(log.path)
        self.assertEqual([e["type"] for e in back], ["turn_start", "reply", "turn_end"])
        self.assertNotIn("_full", back[1])
        for e in back:
            validate_event(e)

    def test_dedupe_status_and_fanout(self) -> None:
        got: list[dict] = []
        sink = dedupe_status(got.append)
        f = EventFactory()
        for text in ("a", "working", "working", "working", "b", "working"):
            sink(f.make("status", text=text))
        sink(f.make("step_start", step=1, limit=1, ceiling=1))
        sink(f.make("status", text="working"))
        self.assertEqual([e["text"] for e in got if e["type"] == "status"], ["a", "working", "b", "working", "working"])

        def broken(_e):
            raise RuntimeError("renderer bug")

        seen: list[dict] = []
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            fanout(broken, None, seen.append)(f.make("status", text="x"))
        self.assertEqual(len(seen), 1)                    # a failing sink does not stop the others
        self.assertIn("renderer bug", err.getvalue())     # ...and is reported, not swallowed


if __name__ == "__main__":
    unittest.main()
