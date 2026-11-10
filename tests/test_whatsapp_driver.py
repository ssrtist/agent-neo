"""WhatsApp Web driver offline tests: factory, URL detection, response cleaning, and send/cancel."""
from __future__ import annotations

import unittest
from unittest import mock
from types import SimpleNamespace

import tests.support  # noqa: F401  (puts the repo on sys.path)

import chat_driver
import neo_edge as edge
import whatsapp_web as whatsapp
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

    def get_attribute(self, _attr: str) -> str:
        return ""


class WhatsAppDriverTests(unittest.TestCase):
    def test_whatsapp_driver_disabled_by_default(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            chat_driver.get_driver("whatsapp")
        self.assertIn("disabled", str(ctx.exception).lower())

    def test_connect_raises_when_disabled(self) -> None:
        drv = whatsapp.WhatsAppWebDriver()
        with self.assertRaises(RuntimeError) as ctx:
            drv.connect()
        self.assertIn("disabled", str(ctx.exception).lower())

    @mock.patch.object(whatsapp.WhatsAppWebDriver, "enabled", True)
    def test_factory_builds_whatsapp_driver_with_shared_profile_and_port(self) -> None:
        drv = chat_driver.get_driver("whatsapp")
        self.assertIsInstance(drv, whatsapp.WhatsAppWebDriver)
        self.assertEqual(drv.url, "https://web.whatsapp.com/send/?phone=19175123390")
        self.assertEqual(drv.phone, "19175123390")
        self.assertEqual(drv.port, 9365)
        self.assertTrue(edge.same_path(drv.profile_dir, edge.default_profile_dir()))

    @mock.patch.object(whatsapp.WhatsAppWebDriver, "enabled", True)
    def test_factory_custom_phone(self) -> None:
        drv = chat_driver.get_driver("whatsapp", phone="1234567890")
        self.assertIsInstance(drv, whatsapp.WhatsAppWebDriver)
        self.assertEqual(drv.phone, "1234567890")
        self.assertEqual(drv.url, "https://web.whatsapp.com/send/?phone=1234567890")

    def test_url_and_login_detection(self) -> None:
        drv = whatsapp.WhatsAppWebDriver()
        self.assertTrue(drv._ready_url("https://web.whatsapp.com/send/?phone=19175123390"))
        self.assertTrue(drv._ready_url("https://wa.me/19175123390"))
        self.assertTrue(drv._own_page_url("https://web.whatsapp.com/"))
        self.assertTrue(drv._own_page_url("https://wa.me/19175123390"))
        self.assertTrue(whatsapp._is_whatsapp_url("https://web.whatsapp.com/"))
        self.assertTrue(whatsapp._is_whatsapp_url("https://wa.me/19175123390"))
        self.assertFalse(drv._ready_url("https://m365.cloud.microsoft/chat"))
        self.assertFalse(drv._ready_url("https://chat.deepseek.com/"))

    def test_clean_whatsapp_web_text_strips_timestamps_and_preambles(self) -> None:
        raw = 'WhatsApp said: {"type":"final","message":"hello"}\n10:15 AM'
        self.assertEqual(whatsapp.clean_whatsapp_web_text(raw), '{"type":"final","message":"hello"}')

        raw_narrow = '{"type":"final","message":"hello"}\n10:15\u202fPM ✓'
        self.assertEqual(whatsapp.clean_whatsapp_web_text(raw_narrow), '{"type":"final","message":"hello"}')

        raw_edited = '{"type":"final","message":"hello"}\nEdited 9:02 am'
        self.assertEqual(whatsapp.clean_whatsapp_web_text(raw_edited), '{"type":"final","message":"hello"}')

        raw_tool = '\u200e{"thought":"check status","type":"tool","name":"read_file","arguments":{"path":"a.txt"}}\u200f\n11:45 AM'
        cleaned = whatsapp.clean_whatsapp_web_text(raw_tool)
        action = parse_action(cleaned)
        self.assertEqual(action["name"], "read_file")
        self.assertEqual(action["thought"], "check status")

    def test_clean_whatsapp_web_text_strips_echoed_system_instructions(self) -> None:
        raw_echo = (
            "You are the reasoning component of agent-neo (Agent Neo), a local coding agent.\n"
            "You may ask agent-neo for one tool at a time, using the agent-neo tool protocol below. "
            "Respond with ONLY one JSON object, with no markdown.\n\n"
            "To call a tool:\n"
            '{"type":"tool","name":"load_skill","arguments":{"name":"skill-name"}}\n'
            '{"type":"tool","name":"read_file","arguments":{"path":"relative/path.txt"}}\n'
            '{"type":"tool","name":"write_file","arguments":{"path":"relative/path.txt","content":"full content"}}\n'
            '{"type":"tool","name":"edit_file","arguments":{"path":"relative/path.txt","old":"exact old text","new":"replacement"}}\n'
            '{"type":"tool","name":"delete_file","arguments":{"path":"relative/path.txt"}}\n'
            '{"type":"tool","name":"run_shell","arguments":{"command":"command text"}}\n'
            "Additional t\n"
            '{"type":"final","message":"Hi! Ready when you are — give me a task and I\'ll drive it."}'
        )
        cleaned = whatsapp.clean_whatsapp_web_text(raw_echo)
        self.assertEqual(
            cleaned,
            '{"type":"final","message":"Hi! Ready when you are — give me a task and I\'ll drive it."}',
        )
        action = parse_action(cleaned)
        self.assertEqual(action["type"], "final")
        self.assertIn("Ready when you are", action["message"])

    def test_clean_whatsapp_web_text_extracts_just_the_json_from_complex_message(self) -> None:
        raw_complex = (
            "Hello! I am your AI assistant in WhatsApp.\n\n"
            "```json\n"
            '{"type":"final","message":"Hello! Ready — give me a task and I\'ll start working through it."}\n'
            "```\n\n"
            "Feel free to reply whenever ready.\n"
            "11:30 AM ✓"
        )
        cleaned = whatsapp.clean_whatsapp_web_text(raw_complex)
        self.assertEqual(
            cleaned,
            '{"type":"final","message":"Hello! Ready — give me a task and I\'ll start working through it."}',
        )
        action = parse_action(cleaned)
        self.assertEqual(action["type"], "final")
        self.assertIn("Hello! Ready", action["message"])

        # Without code fences (plain raw conversational wrapper)
        raw_plain_wrapped = (
            "Hey Kenny, here is my response:\n"
            '{"type":"final","message":"Hello! Ready — give me a task and I\'ll start working through it."}\n'
            "Let me know if this works!\n"
            "11:30 AM"
        )
        cleaned_plain = whatsapp.clean_whatsapp_web_text(raw_plain_wrapped)
        self.assertEqual(
            cleaned_plain,
            '{"type":"final","message":"Hello! Ready — give me a task and I\'ll start working through it."}',
        )
        action_plain = parse_action(cleaned_plain)
        self.assertEqual(action_plain["type"], "final")

    def test_send_message_submits_and_returns_stabilized_reply(self) -> None:
        drv = whatsapp.WhatsAppWebDriver()
        editor = _FakeLocator(visible=True)
        send_btn = _FakeLocator(visible=True, disabled=False)
        bubble = SimpleNamespace(
            inner_text=lambda: '{"type":"final","message":"ok"}\n10:15 AM',
            locator=lambda *_a: _FakeLocator(visible=False),
        )
        calls = {"n": 0}

        def locator(sel: str):
            if "message-in" in sel:
                calls["n"] += 1
                return _FakeLocator([] if calls["n"] == 1 else [bubble])
            if "send" in sel.lower():
                return send_btn
            if "typing" in sel:
                return _FakeLocator(visible=False)
            return editor

        page = SimpleNamespace(
            locator=locator,
            keyboard=SimpleNamespace(press=lambda *_a: None, insert_text=lambda *_a: None, type=lambda *_a: None),
            evaluate=lambda *_a: None,
            url=drv.url,
            goto=lambda *_a, **_k: None,
        )
        drv.connect = lambda *_a, **_k: page
        drv.find_input = lambda *_a, **_k: editor
        with mock.patch.object(whatsapp.time, "sleep", lambda *_a: None):
            reply = drv.send_message("hi")
        self.assertEqual(reply, '{"type":"final","message":"ok"}')
        self.assertEqual(editor.filled, ["hi"])
        self.assertGreaterEqual(send_btn.clicked, 1)

    def test_recipient_lock_and_active_chat_detection(self) -> None:
        drv = whatsapp.WhatsAppWebDriver(phone="19175123390", contact="Agent Bot")

        # Case A: Header matches target phone digits (+1 (917) 512-3390)
        h_match_phone = SimpleNamespace(
            is_visible=lambda: True,
            inner_text=lambda: "+1 (917) 512-3390",
            get_attribute=lambda _a: "",
        )
        page_a = SimpleNamespace(
            locator=lambda sel: _FakeLocator([h_match_phone] if "header" in sel else [_FakeLocator(visible=True)]),
            url="https://web.whatsapp.com/",
        )
        self.assertTrue(drv._is_active_chat(page_a))

        # Case B: Header matches contact name
        h_match_name = SimpleNamespace(
            is_visible=lambda: True,
            inner_text=lambda: "Agent Bot",
            get_attribute=lambda _a: "Agent Bot",
        )
        page_b = SimpleNamespace(
            locator=lambda sel: _FakeLocator([h_match_name] if "header" in sel else [_FakeLocator(visible=True)]),
            url="https://web.whatsapp.com/",
        )
        self.assertTrue(drv._is_active_chat(page_b))

        # Case C: Header is a completely different contact
        h_diff = SimpleNamespace(
            is_visible=lambda: True,
            inner_text=lambda: "Mom (+1 (212) 555-9999)",
            get_attribute=lambda _a: "",
        )
        def loc_c(sel):
            if "header" in sel:
                return _FakeLocator([h_diff])
            if sel == "#main":
                return _FakeLocator(visible=True)
            return _FakeLocator([])

        page_c = SimpleNamespace(locator=loc_c, url="https://web.whatsapp.com/")
        self.assertFalse(drv._is_active_chat(page_c))

        # Case D: Dynamic learned label for any future arbitrary chat name (unconfigured contact)
        drv_future = whatsapp.WhatsAppWebDriver(phone="447123456789")
        self.assertEqual(drv_future.contact, "")
        h_future = SimpleNamespace(
            is_visible=lambda: True,
            inner_text=lambda: "London AI Assistant",
            get_attribute=lambda _a: "London AI Assistant",
        )
        def loc_d(sel):
            if "header" in sel:
                return _FakeLocator([h_future])
            if sel == "#main":
                return _FakeLocator(visible=True)
            return _FakeLocator([])

        page_d = SimpleNamespace(locator=loc_d, url="https://web.whatsapp.com/")
        self.assertFalse(drv_future._is_active_chat(page_d))
        # After dynamic label learning:
        drv_future._active_chat_label = "London AI Assistant"
        self.assertTrue(drv_future._is_active_chat(page_d))

    def test_ensure_active_chat_reroutes_when_wrong_chat_open(self) -> None:
        drv = whatsapp.WhatsAppWebDriver(phone="19175123390")
        gotos = []
        is_active = [False, True]

        def fake_goto(url, **_kw):
            gotos.append(url)

        page = SimpleNamespace(
            locator=lambda _s: _FakeLocator(visible=False),
            url="https://web.whatsapp.com/",
            goto=fake_goto,
        )
        drv._is_active_chat = lambda _p: is_active.pop(0) if is_active else True
        with mock.patch.object(whatsapp.time, "sleep", lambda *_a: None):
            res = drv._ensure_active_chat(page, timeout_ms=5000)
        self.assertTrue(res)
        self.assertEqual(gotos, [drv.url])

    def test_stop_before_submit_never_types_in_whatsapp(self) -> None:
        drv = whatsapp.WhatsAppWebDriver()
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
