"""Desktop send resilience (plan v0.5 D3): fake UIA elements, no desktop app needed."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import copilot_ui as ui
except Exception as exc:  # noqa: BLE001  (pywinauto missing)
    ui = None
    _why = str(exc)


class FakeEdit:
    def __init__(self, text: str, clears_after: int | None) -> None:
        self.text, self.clears_after, self.sends, self.focus_calls = text, clears_after, 0, 0

    def get_value(self) -> str:
        return self.text

    def set_focus(self) -> None:
        self.focus_calls += 1

    def has_keyboard_focus(self) -> bool:
        return True

    def sent(self) -> None:
        self.sends += 1
        if self.clears_after is not None and self.sends >= self.clears_after:
            self.text = ""


class FakeWindow:
    def __init__(self, edit: FakeEdit, with_button: bool = True) -> None:
        self.edit, self.focus_calls = edit, 0
        btn = SimpleNamespace(element_info=SimpleNamespace(control_type="Button", name="Send"), click_input=edit.sent)
        self._kids = [btn] if with_button else []

    def descendants(self):
        return self._kids

    def set_focus(self) -> None:
        self.focus_calls += 1


@unittest.skipIf(ui is None, "pywinauto not importable")
class SubmitTests(unittest.TestCase):
    def run_submit(self, clears_after, with_button=True):
        edit = FakeEdit("hello", clears_after)
        win = FakeWindow(edit, with_button)
        keys, status = [], []

        def press(k):
            keys.append(k)
            edit.sent()
        ok = ui.submit_and_verify(win, edit, "hello", status.append, sleep=lambda s: None, keys=press)
        return ok, edit, win, keys, status

    def test_first_send_clears(self) -> None:
        ok, edit, win, keys, status = self.run_submit(1)
        self.assertTrue(ok)
        self.assertEqual((edit.sends, win.focus_calls, keys, status), (1, 0, [], []))

    def test_retry_refocuses_then_clears(self) -> None:
        ok, edit, win, _k, status = self.run_submit(3)
        self.assertTrue(ok)
        self.assertEqual(edit.sends, 3)
        self.assertEqual(win.focus_calls, 2)
        self.assertEqual(edit.focus_calls, 2)
        self.assertNotIn(ui.STUCK_PROMPT_WARNING, status)

    def test_stuck_warns_after_three_retries(self) -> None:
        ok, edit, win, _k, status = self.run_submit(None)
        self.assertFalse(ok)
        self.assertEqual(edit.sends, 1 + 3)
        self.assertEqual(win.focus_calls, 3)
        self.assertEqual(status[-1], ui.STUCK_PROMPT_WARNING)
        self.assertIn("Prompt still in Copilot input box", status[-1])

    def test_enter_fallback_without_button(self) -> None:
        ok, _e, _w, keys, _s = self.run_submit(1, with_button=False)
        self.assertTrue(ok)
        self.assertEqual(keys, ["{ENTER}"])

    def test_manual_send_during_retry_is_not_resent(self) -> None:
        edit = FakeEdit("hello", None)
        win = FakeWindow(edit)

        def user_clicks(_s):
            if win.focus_calls:
                edit.text = ""   # the user pressed Send by hand
        ok = ui.submit_and_verify(win, edit, "hello", None, sleep=user_clicks, keys=lambda k: None)
        self.assertTrue(ok)
        self.assertEqual(edit.sends, 1)

    def test_unreadable_input_is_not_retried(self) -> None:
        class Blind(FakeEdit):
            def get_value(self):
                raise RuntimeError("no value pattern")
        edit = Blind("x", None)
        win = FakeWindow(edit)
        self.assertTrue(ui.submit_and_verify(win, edit, "x", None, sleep=lambda s: None, keys=lambda k: None))
        self.assertEqual(edit.sends, 1)

    def test_placeholder_counts_as_empty(self) -> None:
        self.assertTrue(ui.input_cleared(FakeEdit("Message Copilot", None)))
        self.assertTrue(ui.input_cleared(FakeEdit("Ask Copilot", None)))

    def test_consumer_copilot_window_title_matching(self) -> None:
        self.assertTrue(ui._is_consumer_copilot_window_title("Copilot"))
        self.assertTrue(ui._is_consumer_copilot_window_title("Microsoft Copilot"))
        self.assertFalse(ui._is_consumer_copilot_window_title("Copilot - Microsoft Edge"))
        self.assertFalse(ui._is_consumer_copilot_window_title("Agent Neo (agent-neo) 0.9.0"))
        self.assertFalse(ui._is_consumer_copilot_window_title("M365 Chat Agent (agent365) 0.5.0"))


if __name__ == "__main__":
    unittest.main()
