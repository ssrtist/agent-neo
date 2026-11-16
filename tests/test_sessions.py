"""Tests for per-engine session persistence, auto-resume, thread URL handling, and GUI splash/history."""
from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from tests.support import Scratch

import copilot_web
import deepseek_web
import gemini_web
import neo_core
import neo_events
import neo_gui
import neo_sessions


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("sessions")
        self.home = self.s.path / "home"
        self.home.mkdir()
        self.store = neo_sessions.SessionStore(self.home)

    def tearDown(self) -> None:
        self.s.cleanup()

    def _write_sample_log(self, sid: str, user_text: str, final_text: str, turn: int = 1) -> None:
        log = neo_events.SessionLog(self.home, stamp=sid)
        f = neo_events.EventFactory()
        f.turn = turn
        log.write(f.make("turn_start", text=user_text, workspace="C:/ws"))
        log.write(f.make("final", message=final_text, step=1))
        log.write(f.make("turn_end", outcome="final"))

    def test_per_engine_latest_and_filtering(self) -> None:
        self._write_sample_log("s-cop", "Copilot task", "Copilot done")
        self.store.upsert("s-cop", backend="copilot", workspace="C:/ws1", title="Copilot task",
                          turn_count=1, chat_url="https://copilot.microsoft.com/chats/c1", updated_at=1000)

        self._write_sample_log("s-gem-1", "Gemini old", "Old done")
        self.store.upsert("s-gem-1", backend="gemini", workspace="C:/ws1", title="Gemini old",
                          turn_count=1, chat_url="https://gemini.google.com/app/old123", updated_at=2000)

        self._write_sample_log("s-gem-2", "Gemini new", "New done")
        self.store.upsert("s-gem-2", backend="gemini", workspace="C:/ws2", title="Gemini new",
                          turn_count=2, chat_url="https://gemini.google.com/app/new456", updated_at=3000)

        self._write_sample_log("s-ds", "DeepSeek task", "DS done")
        self.store.upsert("s-ds", backend="deepseek", workspace="C:/ws3", title="DeepSeek task",
                          turn_count=1, chat_url="https://chat.deepseek.com/a/chat/s/ds789", updated_at=2500)

        # Empty 0-turn session must not shadow the real latest Copilot session
        self.store.upsert("s-empty", backend="copilot", workspace="C:/ws1", turn_count=0, updated_at=9999)

        self.assertEqual(self.store.latest_for_backend("web")["id"], "s-cop")
        self.assertEqual(self.store.latest_for_backend("gemini")["id"], "s-gem-2")
        self.assertEqual(self.store.latest_for_backend("deepseek")["id"], "s-ds")
        self.assertIsNone(self.store.latest_for_backend("desktop"))

        all_items = self.store.list_sessions()
        self.assertEqual([x["id"] for x in all_items], ["s-gem-2", "s-ds", "s-gem-1", "s-cop"])
        self.assertEqual([x["id"] for x in self.store.list_sessions(backend="gemini")], ["s-gem-2", "s-gem-1"])
        self.assertEqual(self.store.get_session("s-ds")["chat_url"], "https://chat.deepseek.com/a/chat/s/ds789")

    def test_unindexed_jsonl_discovered_and_summarized(self) -> None:
        self._write_sample_log("20261115-100000-000001", "Inspect bug", "Fixed bug", turn=2)
        items = self.store.list_sessions()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "20261115-100000-000001")
        self.assertEqual(items[0]["backend"], "unknown")
        self.assertEqual(items[0]["turn_count"], 1)
        events = self.store.load_events("20261115-100000-000001")
        self.assertEqual(neo_sessions.max_turn_number(events), 2)
        summary = neo_sessions.build_fallback_summary(events)
        self.assertIn("Inspect bug", summary)
        self.assertIn("Fixed bug", summary)


class CoreRestoreTests(unittest.TestCase):
    def test_remote_resume_skips_full_system_prompt_once(self) -> None:
        s = Scratch("core_restore")
        try:
            agent = neo_core.Agent(MagicMock(), s.path, neo_core.Settings(), lambda _e: None)
            agent.restore_session(last_turn=3, remote_resumed=True, fallback_summary="ignored")
            self.assertEqual(agent.events.turn, 3)

            first = agent.first_prompt("continue work")
            self.assertNotIn("BASE_SYSTEM_INSTRUCTIONS", first)
            self.assertNotIn("To call a tool:", first)
            self.assertIn("Resumed Agent Neo session", first)
            self.assertIn("continue work", first)

            # Subsequent context-loss / reset restores full system prompt
            second = agent.first_prompt("after context lost")
            self.assertIn("To call a tool:", second)
        finally:
            s.cleanup()

    def test_fallback_resume_seeds_compact_checkpoint(self) -> None:
        s = Scratch("core_fallback")
        try:
            agent = neo_core.Agent(MagicMock(), s.path, neo_core.Settings(), lambda _e: None)
            agent.restore_session(last_turn=2, remote_resumed=False, fallback_summary="Turn 1: User asked: hello")
            prompt = agent.first_prompt("next step")
            self.assertIn("RESUMED FROM PREVIOUS COMPACTED CONVERSATION CHECKPOINT:", prompt)
            self.assertIn("Turn 1: User asked: hello", prompt)
            self.assertIn("CURRENT USER REQUEST / NEXT STEP:\nnext step", prompt)
        finally:
            s.cleanup()


class DriverThreadUrlTests(unittest.TestCase):
    def test_thread_url_detection_per_engine(self) -> None:
        cop = copilot_web.WebCopilotDriver()
        self.assertFalse(cop._is_thread_url("https://copilot.microsoft.com/"))
        self.assertFalse(cop._is_thread_url("https://m365.cloud.microsoft/chat"))
        self.assertTrue(cop._is_thread_url("https://copilot.microsoft.com/chats/abc12345"))
        self.assertTrue(cop._is_thread_url("https://m365.cloud.microsoft/chat?conversationId=thread_99"))

        gem = gemini_web.GeminiWebDriver()
        self.assertFalse(gem._is_thread_url("https://gemini.google.com/app"))
        self.assertTrue(gem._is_thread_url("https://gemini.google.com/app/1a2b3c4d5e"))
        self.assertTrue(gem._is_thread_url("https://gemini.google.com/gem/coding/9988aabb"))

        ds = deepseek_web.DeepSeekWebDriver()
        self.assertFalse(ds._is_thread_url("https://chat.deepseek.com/"))
        self.assertTrue(ds._is_thread_url("https://chat.deepseek.com/a/chat/s/f1e2d3c4-5678"))

    def test_open_chat_url_success_and_expired_fallback(self) -> None:
        gem = gemini_web.GeminiWebDriver()
        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://gemini.google.com/app/abc12345"
        gem.connect = MagicMock(return_value=page)
        gem.find_input = MagicMock(return_value=MagicMock())
        gem._clear_composer = MagicMock()

        self.assertEqual(gem.current_chat_url(), None)  # _page not attached yet
        gem._page = page
        self.assertEqual(gem.current_chat_url(), "https://gemini.google.com/app/abc12345")

        self.assertTrue(gem.open_chat_url("https://gemini.google.com/app/abc12345"))
        # Simulate expired/deleted thread bouncing back to bare /app
        page.url = "https://gemini.google.com/app"
        self.assertFalse(gem.open_chat_url("https://gemini.google.com/app/deleted99"))
        # Reject foreign URLs
        self.assertFalse(gem.open_chat_url("https://evil.example.com/app/12345"))


class GuiSessionSplashAndResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("gui_splash")
        self.ws1 = self.s.path / "ws1"
        self.ws2 = self.s.path / "ws2"
        self.ws1.mkdir()
        self.ws2.mkdir()
        self.home = self.s.path / "home"
        self.home.mkdir()
        self._orig_env = dict(__import__("os").environ)
        __import__("os").environ["NEO_HOME"] = str(self.home)

    def tearDown(self) -> None:
        import os
        os.environ.clear()
        os.environ.update(self._orig_env)
        self.s.cleanup()

    def test_splash_state_flags_and_auto_resume_switches_workspace(self) -> None:
        events: list[dict] = []
        sess = neo_gui.GuiSession(self.ws1, {}, None, events.append)
        try:
            self.assertTrue(sess.state()["show_splash"])

            # Seed a previous Gemini session in ws2
            sid = "20261115-120000-000001"
            log = neo_events.SessionLog(self.home, stamp=sid)
            f = neo_events.EventFactory()
            f.turn = 1
            log.write(f.make("turn_start", text="Explain architecture", workspace=str(self.ws2)))
            log.write(f.make("final", message="Here is the architecture.", step=1))
            log.write(f.make("turn_end", outcome="final"))
            sess.store.upsert(
                sid,
                backend="gemini",
                workspace=str(self.ws2),
                title="Explain architecture",
                turn_count=1,
                chat_url="https://gemini.google.com/app/gem777",
                updated_at=5000,
            )

            fake_drv = MagicMock()
            fake_drv.open_chat_url.return_value = True
            sess._make_driver = MagicMock(return_value=fake_drv)

            res = sess.enter_engine("gemini", mode="resume_last")
            self.assertTrue(res.get("ok"))

            deadline = time.time() + 5.0
            while time.time() < deadline:
                if any(e.get("type") == "gui_session_loaded" for e in events) and not sess.busy:
                    break
                time.sleep(0.05)

            loaded = [e for e in events if e.get("type") == "gui_session_loaded"]
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0]["resumed"])
            self.assertTrue(loaded[0]["remote_resumed"])
            self.assertEqual(loaded[0]["session_id"], sid)
            self.assertEqual(sess.workspace, self.ws2.resolve())
            self.assertEqual(sess.backend, "gemini")
            self.assertFalse(sess.state()["show_splash"])
            self.assertEqual(sess.agent.events.turn, 1)
            self.assertTrue(sess.agent._remote_resumed)
            fake_drv.open_chat_url.assert_called_once_with("https://gemini.google.com/app/gem777")

            # Return to splash and start a brand-new Gemini chat instead
            events.clear()
            self.assertTrue(sess.return_to_splash()["ok"])
            self.assertTrue(sess.state()["show_splash"])

            self.assertTrue(sess.enter_engine("gemini", mode="new")["ok"])
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if any(e.get("type") == "gui_session_loaded" for e in events) and not sess.busy:
                    break
                time.sleep(0.05)
            loaded_new = [e for e in events if e.get("type") == "gui_session_loaded"]
            self.assertEqual(len(loaded_new), 1)
            self.assertFalse(loaded_new[0]["resumed"])
            self.assertEqual(loaded_new[0]["events"], [])
            self.assertNotEqual(sess.session_id, sid)
        finally:
            sess.shutdown()

    def test_explicit_backend_or_fake_skips_splash(self) -> None:
        s1 = neo_gui.GuiSession(self.ws1, {"backend": "deepseek"}, None, lambda _e: None)
        try:
            self.assertFalse(s1.state()["show_splash"])
        finally:
            s1.shutdown()

        s2 = neo_gui.GuiSession(self.ws1, {}, "", lambda _e: None)
        try:
            self.assertFalse(s2.state()["show_splash"])
        finally:
            s2.shutdown()


if __name__ == "__main__":
    unittest.main()
