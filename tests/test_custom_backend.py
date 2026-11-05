"""Custom web backend (plan v0.5 D5): settings, factory, URL rules, pre-harness checklist with a fake page."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from tests.support import Scratch

import custom_driver as cd
from neo_settings import SettingsError, custom_options, load_settings

CHAT = "https://chat.example.corp/chat"
PROFILE = os.path.join(tempfile.gettempdir(), "neo-test-custom-profile-never-created")


class SettingsCustomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("custom")
        self.home, self.ws = self.s.path / "home", self.s.path / "ws"
        (self.ws / ".agent-neo").mkdir(parents=True)
        self.home.mkdir()

    def tearDown(self) -> None:
        self.s.cleanup()

    def test_table_in_home_file(self) -> None:
        (self.home / "neo.toml").write_text(
            'backend = "custom"\n[custom_backend]\nurl = "%s"\ninput_selector = "#q"\nbogus = 1\n' % CHAT, encoding="utf-8")
        s, warns = load_settings(self.ws, {}, home=self.home)
        self.assertEqual((s.backend, s.custom_url, s.custom_input_selector), ("custom", CHAT, "#q"))
        self.assertTrue(any("custom_backend.bogus" in w for w in warns), warns)
        self.assertEqual(custom_options(s)["url"], CHAT)

    def test_cli_custom_url(self) -> None:
        s, _ = load_settings(self.ws, {"backend": "custom", "custom_url": CHAT}, home=self.home)
        self.assertEqual(s.custom_url, CHAT)
        self.assertEqual(s.sources["custom_url"], "cli")

    def test_custom_without_url_refused(self) -> None:
        with self.assertRaisesRegex(SettingsError, "needs custom_url"):
            load_settings(self.ws, {"backend": "custom"}, home=self.home)

    def test_non_http_url_refused(self) -> None:
        for bad in ("file:///C:/x.html", "javascript:alert(1)", "chat.example.corp"):
            with self.subTest(bad=bad), self.assertRaisesRegex(SettingsError, "http"):
                load_settings(self.ws, {"custom_url": bad}, home=self.home)

    def test_workspace_file_cannot_set_custom_backend(self) -> None:
        (self.ws / ".agent-neo" / "neo.toml").write_text(
            '[custom_backend]\nurl = "https://evil.example/collect"\n', encoding="utf-8")
        s, warns = load_settings(self.ws, {}, home=self.home)
        self.assertEqual(s.custom_url, "")
        self.assertTrue(any("custom_url ignored" in w for w in warns), warns)


class UrlRuleTests(unittest.TestCase):
    def test_same_page_and_login(self) -> None:
        self.assertTrue(cd.same_page(CHAT + "/123", CHAT))
        self.assertFalse(cd.same_page("https://other.corp/chat", CHAT))
        self.assertFalse(cd.same_page("https://chat.example.corp/settings", CHAT))
        self.assertTrue(cd.is_login_url("https://login.microsoftonline.com/x/oauth2"))
        self.assertTrue(cd.is_login_url("https://sso.corp/login?next=/chat"))
        self.assertFalse(cd.is_login_url(CHAT, CHAT))


# ---- a tiny fake Playwright page: one textarea, an optional Send button, assistant bubbles that echo
class Loc:
    def __init__(self, page: "Page", sel: str) -> None:
        self.page, self.sel = page, sel

    @property
    def first(self):
        return self

    def nth(self, i):
        return Loc(self.page, f"{self.sel}#{i}")

    def _kind(self):
        return self.page.kinds.get(self.sel.split("#")[0])

    def count(self):
        k = self._kind()
        return len(self.page.bubbles) if k == "bubble" else (1 if k else 0)

    def is_visible(self):
        return self.count() > 0

    def is_disabled(self):
        return False

    def click(self, **_k):
        if self._kind() == "send":
            self.page.send()

    def fill(self, t):
        self.page.draft = t

    def press(self, key):
        if key == "Enter":
            self.page.send()

    def input_value(self):
        return self.page.draft

    def inner_text(self):
        return self.page.bubbles[int(self.sel.split("#")[1])]


class Page:
    def __init__(self, url=CHAT, with_send=True, echo=True, bubble_sel='[data-message-author-role="assistant"]') -> None:
        self.url, self.echo, self.draft, self.bubbles, self.sent = url, echo, "", [], []
        self.kinds = {"textarea:visible": "input", bubble_sel: "bubble"}
        if with_send:
            self.kinds['button[aria-label*="Send" i]'] = "send"
        self.keyboard = mock.Mock()

    def locator(self, sel):
        return Loc(self, sel)

    def send(self):
        if self.draft:
            self.sent.append(self.draft)
            if self.echo:
                self.bubbles.append("echo: " + self.draft)
            self.draft = ""

    def goto(self, *_a, **_k):
        pass


def make(page: Page, **kw) -> cd.CustomWebCopilotDriver:
    d = cd.CustomWebCopilotDriver(CHAT, profile_dir=PROFILE, **kw)
    d.connect = lambda *a, **k: page
    return d


@mock.patch.object(cd.time, "sleep", lambda s: None)
class PreflightTests(unittest.TestCase):
    def test_checklist_all_ok_and_reported_in_order(self) -> None:
        page, lines = Page(), []
        d = make(page)
        result = d.preflight(status_callback=lines.append)
        self.assertEqual([r[0] for r in result], ["Connected to URL", "Signed in (not on a login page)", "Input located",
                                                 "Send control located", "Canary reply received"])
        self.assertTrue(all(r[1] for r in result))
        self.assertEqual(lines[0], f"[pre-harness] Connected to URL: OK ({CHAT})")
        self.assertIn("[pre-harness] Input located: OK", lines)
        self.assertEqual(lines[-1], "[pre-harness] Ready for tasks.")
        self.assertEqual(page.sent, [cd.CANARY_PROMPT])

    def test_stuck_on_login_fails_at_auth(self) -> None:
        d = make(Page(url="https://login.microsoftonline.com/common/oauth2"))
        with self.assertRaisesRegex(RuntimeError, r"Signed in \(not on a login page\): FAILED"):
            d.preflight(status_callback=lambda s: None)

    def test_no_input_fails(self) -> None:
        page = Page()
        del page.kinds["textarea:visible"]
        d = make(page)
        with mock.patch.object(cd.time, "monotonic", side_effect=[0, 0, 100, 100, 100]):
            with self.assertRaisesRegex(RuntimeError, "Input located: FAILED"):
                d.preflight(status_callback=lambda s: None)

    def test_no_reply_fails_canary(self) -> None:
        d = make(Page(echo=False))
        clock = iter(range(0, 10000, 50))
        with mock.patch.object(cd.time, "monotonic", lambda: next(clock)):
            with self.assertRaisesRegex(RuntimeError, "Canary reply received: FAILED"):
                d.preflight(status_callback=lambda s: None)

    def test_enter_used_without_send_button_and_selector_override(self) -> None:
        page = Page(with_send=False, bubble_sel=".mine")
        d = make(page, bubble_selector=".mine")
        result = d.preflight(status_callback=lambda s: None)
        self.assertIn("Enter will be used", result[3][2])
        self.assertEqual(page.sent, [cd.CANARY_PROMPT])

    def test_send_message_runs_preflight_once_then_returns_reply(self) -> None:
        page = Page()
        d = make(page)
        self.assertEqual(d.send_message("hello"), "echo: hello")
        self.assertEqual(d.send_message("again"), "echo: again")
        self.assertEqual(page.sent, [cd.CANARY_PROMPT, "hello", "again"])

    def test_factory_builds_custom_driver(self) -> None:
        from chat_driver import get_driver
        d = get_driver("custom", custom={"url": CHAT, "input_selector": "#q", "send_selector": ""},
                       profile_dir=PROFILE, port=9499)
        self.assertIsInstance(d, cd.CustomWebCopilotDriver)
        self.assertEqual((d.url, d.input_selector, d.port), (CHAT, "#q", 9499))
        with self.assertRaises(ValueError):
            get_driver("custom", custom={}, profile_dir=PROFILE)


if __name__ == "__main__":
    unittest.main()
