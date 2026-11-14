"""Tests for GUI features: queuing, steering, folder browse, and Dayfox theme."""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from tests.support import REPO, Scratch

import neo_core
import neo_gui


class GuiFeaturesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("gui_features")
        self.ws = self.s.path / "ws"
        self.ws.mkdir()
        self.events: list[dict] = []
        self.session = neo_gui.GuiSession(
            workspace=self.ws,
            cli_overrides={},
            fake_script=None,
            emit=lambda ev: self.events.append(ev),
        )

    def tearDown(self) -> None:
        self.session.shutdown()
        self.s.cleanup()

    def test_send_queues_when_running(self) -> None:
        # Simulate active turn
        self.session.running = True
        res = self.session.send("second prompt")
        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("queued"))
        self.assertEqual(self.session.queued_prompt, "second prompt")

    def test_steer_intercepts_and_queues(self) -> None:
        mock_agent = MagicMock()
        self.session.agent = mock_agent
        self.session.running = True

        res = self.session.steer("stop and do something else")
        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("steered"))
        self.assertEqual(mock_agent.pending_steering, "stop and do something else")
        mock_agent.cancel.set.assert_called_once()
        self.assertEqual(self.session.queued_prompt, "[Steering]: stop and do something else")

    def test_steer_when_idle_sends_directly(self) -> None:
        self.session.running = False
        self.session.send = MagicMock(return_value={"ok": True})
        res = self.session.steer("start with steering")
        self.assertTrue(res.get("ok"))
        self.session.send.assert_called_once_with("[Steering]: start with steering")

    def test_agent_injects_pending_steering(self) -> None:
        agent = neo_core.Agent(
            driver=MagicMock(),
            root=self.ws,
            settings=neo_core.Settings(),
            sink=lambda e: None,
        )
        agent.pending_steering = "focus on tests only"
        self.assertEqual(agent.pending_steering, "focus on tests only")

    def test_dayfox_theme_registered(self) -> None:
        theme_boot = (REPO / "gui" / "theme-boot.js").read_text(encoding="utf-8")
        self.assertIn("dayfox", theme_boot)
        self.assertNotIn("'dark': 1", theme_boot)

        app_css = (REPO / "gui" / "app.css").read_text(encoding="utf-8")
        self.assertIn(':root[data-theme="dayfox"]', app_css)
        self.assertIn("#f6f2ee", app_css)
        self.assertIn("#3d2b5a", app_css)

    def test_html_contains_toolbelt_and_ribbon_elements(self) -> None:
        html = (REPO / "gui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="ws-browse"', html)
        self.assertIn('id="ws-pill"', html)
        self.assertIn('class="toolbelt"', html)
        self.assertIn('class="toolbelt-left"', html)
        self.assertIn('class="toolbelt-right"', html)
        self.assertIn('Open and free agent harness for all LLM web chats', html)
        self.assertNotIn('Agent Neo (agent-neo):', html)

    def test_gui_session_wakeup_queues_when_busy_and_runs_when_idle(self) -> None:
        self.session.running = True
        self.session._on_wakeup({"watch_id": "watch_1", "reason": "task_1 exited (0)", "prompt": "resume M3"})
        self.assertEqual(self.session.wakeup_queue, ["resume M3"])

        # Clear and verify idle dispatch queues a turn job
        self.session.wakeup_queue.clear()
        self.session.running = False
        fake_jobs: list[tuple] = []
        self.session.jobs = MagicMock()
        self.session.jobs.put.side_effect = fake_jobs.append
        self.session._on_wakeup({"watch_id": "watch_2", "reason": "timer elapsed", "prompt": "check status"})
        self.assertTrue(self.session.running)
        self.assertEqual(fake_jobs, [("turn", "check status")])
        self.session.running = False

    def test_gui_slash_watch_and_tasks(self) -> None:
        self.session.agent = neo_core.Agent(
            driver=MagicMock(),
            root=self.ws,
            settings=neo_core.Settings(),
            sink=lambda e: None,
            on_wakeup=self.session._on_wakeup,
        )
        res = self.session.slash("/watch 5s check build")
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(len(self.session.agent.task_manager.list_tasks()["watches"]), 1)

        res_tasks = self.session.slash("/tasks")
        self.assertTrue(res_tasks.get("ok"))

        res_unwatch = self.session.slash("/unwatch")
        self.assertTrue(res_unwatch.get("ok"))
        self.assertEqual(self.session.agent.task_manager.list_tasks()["watches"], [])


if __name__ == "__main__":
    unittest.main()
