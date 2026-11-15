"""DeepSeek Web driver offline tests: factory, URL/sign-in detection, response & <think> cleaning, and send/cancel."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import tests.support  # noqa: F401  (puts the repo on sys.path)

import chat_driver
import deepseek_web as deepseek
import neo_edge as edge
from neo_core import parse_action
from neo_events import Cancelled


class _FakeLocator:
    def __init__(self, items=None, visible: bool = False, disabled: bool = False):
        self._items = list(items or [])
        self._visible = visible
        self._disabled = disabled
        self.clicked = 0
        self.filled: list[str] = []

    @property
    def first(self):
        return self._items[0] if self._items else self

    def count(self) -> int:
        return len(self._items)

    def nth(self, idx: int):
        return self._items[idx]

    def is_visible(self) -> bool:
        return self._visible

    def is_disabled(self) -> bool:
        return self._disabled

    def click(self, **_kw) -> None:
        self.clicked += 1

    def fill(self, text: str) -> None:
        self.filled.append(text)

    def inner_text(self) -> str:
        return ""


class DeepSeekDriverTests(unittest.TestCase):
    def test_factory_builds_deepseek_driver_with_shared_profile_and_port(self) -> None:
        drv = chat_driver.get_driver("deepseek")
        self.assertIsInstance(drv, deepseek.DeepSeekWebDriver)
        self.assertEqual(drv.url, "https://chat.deepseek.com/")
        self.assertEqual(drv.port, 9365)
        self.assertTrue(edge.same_path(drv.profile_dir, edge.default_profile_dir()))

    def test_url_and_login_detection(self) -> None:
        drv = deepseek.DeepSeekWebDriver()
        self.assertTrue(drv._ready_url("https://chat.deepseek.com/"))
        self.assertTrue(drv._ready_url("https://chat.deepseek.com/a/chat/s/123"))
        self.assertTrue(drv._own_page_url("https://chat.deepseek.com/"))
        self.assertFalse(drv._ready_url("https://chat.deepseek.com/sign_in"))
        self.assertFalse(drv._ready_url("https://accounts.google.com/v3/signin"))
        self.assertTrue(deepseek._is_deepseek_login_url("https://chat.deepseek.com/sign_in"))
        self.assertTrue(deepseek._is_deepseek_login_url("https://accounts.google.com/ServiceLogin"))
        self.assertFalse(drv._ready_url("https://m365.cloud.microsoft/chat"))

    def test_clean_deepseek_web_text_strips_ui_headers_footers_and_converts_think_tags(self) -> None:
        raw = 'DeepSeek said\nThought for 12 seconds\n{"type":"final","message":"hello"}\nCopy\nRegenerate'
        self.assertEqual(deepseek.clean_deepseek_web_text(raw), '{"type":"final","message":"hello"}')
        raw_think = '<think>\nLet me check the directory first.\n</think>\n{"type":"tool","name":"read_file","arguments":{"path":"a.txt"}}'
        cleaned = deepseek.clean_deepseek_web_text(raw_think)
        action = parse_action(cleaned)
        self.assertEqual(action["name"], "read_file")
        self.assertEqual(action["thought"], "Let me check the directory first.")

    def test_send_message_submits_and_returns_stabilized_reply(self) -> None:
        drv = deepseek.DeepSeekWebDriver()
        editor = _FakeLocator(visible=True)
        send_btn = _FakeLocator(visible=True, disabled=False)
        stop_btn = _FakeLocator(visible=False)
        bubble = SimpleNamespace(inner_text=lambda: 'Thought for 4 seconds\n{"type":"final","message":"ok"}')
        calls = {"n": 0}

        def locator(sel: str):
            if "ds-markdown" in sel or "assistant" in sel:
                calls["n"] += 1
                return _FakeLocator([] if calls["n"] == 1 else [bubble])
            if "Send" in sel or "send" in sel:
                return send_btn
            if "Stop" in sel or "stop" in sel:
                return stop_btn
            return editor

        page = SimpleNamespace(
            locator=locator,
            keyboard=SimpleNamespace(press=lambda *_a: None, insert_text=lambda *_a: None, type=lambda *_a: None),
            evaluate=lambda *_a: None,
        )
        drv.connect = lambda *_a, **_k: page
        drv.find_input = lambda *_a, **_k: editor
        with unittest.mock.patch.object(deepseek.time, "sleep", lambda *_a: None):
            reply = drv.send_message("hi")
        self.assertEqual(reply, '{"type":"final","message":"ok"}')
        self.assertEqual(editor.filled, ["hi"])
        self.assertGreaterEqual(send_btn.clicked, 1)

    def test_stop_before_submit_never_types_in_deepseek(self) -> None:
        drv = deepseek.DeepSeekWebDriver()
        editor = _FakeLocator(visible=True)
        page = SimpleNamespace(
            locator=lambda *_a: _FakeLocator([]),
            keyboard=SimpleNamespace(press=lambda *_a: None),
            evaluate=lambda *_a: None,
        )
        drv.connect = lambda *_a, **_k: page
        drv.find_input = lambda *_a, **_k: editor
        with self.assertRaises(Cancelled):
            drv.send_message("do not send", cancel_check=lambda: True)
        self.assertEqual(editor.filled, [])


if __name__ == "__main__":
    unittest.main()
