"""Gemini Web driver offline tests: factory, URL/sign-in detection, response cleaning, and send/cancel."""
from __future__ import annotations

import unittest
from unittest import mock
from types import SimpleNamespace

import tests.support  # noqa: F401  (puts the repo on sys.path)

import neo_edge as edge
import chat_driver
import gemini_web as gemini
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

    @property
    def last(self):
        return self._items[-1] if self._items else self

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


class GeminiDriverTests(unittest.TestCase):
    def test_factory_builds_gemini_driver_with_shared_profile_and_port(self) -> None:
        drv = chat_driver.get_driver("gemini")
        self.assertIsInstance(drv, gemini.GeminiWebDriver)
        self.assertEqual(drv.url, "https://gemini.google.com/app")
        self.assertEqual(drv.port, 9365)
        self.assertTrue(edge.same_path(drv.profile_dir, edge.default_profile_dir()))

    def test_url_and_login_detection(self) -> None:
        drv = gemini.GeminiWebDriver()
        self.assertTrue(drv._ready_url("https://gemini.google.com/app"))
        self.assertTrue(drv._ready_url("https://gemini.google.com/app/abc123"))
        self.assertTrue(drv._own_page_url("https://gemini.google.com/app"))
        self.assertFalse(drv._ready_url("https://accounts.google.com/v3/signin"))
        self.assertTrue(gemini._is_google_login_url("https://accounts.google.com/ServiceLogin"))
        self.assertFalse(drv._ready_url("https://m365.cloud.microsoft/chat"))

    def test_clean_gemini_web_text_strips_ui_headers_and_footers(self) -> None:
        raw = 'Gemini said\nShow thinking\n{"type":"final","message":"hello"}\nShow drafts'
        self.assertEqual(gemini.clean_gemini_web_text(raw), '{"type":"final","message":"hello"}')
        raw2 = 'Model said: {"type":"tool","name":"read_file","arguments":{"path":"a.txt"}}\nThumb up'
        self.assertEqual(
            gemini.clean_gemini_web_text(raw2),
            '{"type":"tool","name":"read_file","arguments":{"path":"a.txt"}}',
        )

    def test_send_message_submits_and_returns_stabilized_reply(self) -> None:
        drv = gemini.GeminiWebDriver()
        editor = _FakeLocator(visible=True)
        send_btn = _FakeLocator(visible=True, disabled=False)
        stop_btn = _FakeLocator(visible=False)
        bubble = SimpleNamespace(inner_text=lambda: 'Gemini said\n{"type":"final","message":"ok"}')
        calls = {"n": 0}

        def locator(sel: str):
            if "message-content" in sel or "model-response" in sel:
                calls["n"] += 1
                # first call is prior_bubbles (0), subsequent calls have the new reply bubble
                return _FakeLocator([] if calls["n"] == 1 else [bubble])
            if "Send" in sel or "send-button" in sel:
                return send_btn
            if "Stop" in sel or "stop-button" in sel:
                return stop_btn
            return editor

        page = SimpleNamespace(
            locator=locator,
            keyboard=SimpleNamespace(press=lambda *_a: None, insert_text=lambda *_a: None, type=lambda *_a: None),
            evaluate=lambda *_a: None,
        )
        drv.connect = lambda *_a, **_k: page
        drv.find_input = lambda *_a, **_k: editor
        with unittest.mock.patch.object(gemini.time, "sleep", lambda *_a: None):
            reply = drv.send_message("hi")
        self.assertEqual(reply, '{"type":"final","message":"ok"}')
        self.assertEqual(editor.filled, ["hi"])
        self.assertGreaterEqual(send_btn.clicked, 1)

    def test_has_action_bar_detection(self) -> None:
        drv = gemini.GeminiWebDriver()
        # No action bar
        empty_page = SimpleNamespace(locator=lambda _s: _FakeLocator([], visible=False))
        self.assertFalse(drv._has_action_bar(empty_page, None))

        # Action bar with Copy button visible on page
        copy_page = SimpleNamespace(locator=lambda s: _FakeLocator([SimpleNamespace(is_visible=lambda: True)], visible=True) if "Copy" in s else _FakeLocator([], visible=False))
        self.assertTrue(drv._has_action_bar(copy_page, None))

    def test_stop_before_submit_never_types_in_gemini(self) -> None:
        drv = gemini.GeminiWebDriver()
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
