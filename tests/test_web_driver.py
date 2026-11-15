"""Web driver behaviour that needs no browser (code review round 1): Stop before submit, sign-in wait, reused Edge."""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest import mock

import tests.support  # noqa: F401  (puts the repo on sys.path)

import copilot_web as web
from neo_events import Cancelled

LOGIN = "https://login.microsoftonline.com/common/oauth2"
CHAT = "https://m365.cloud.microsoft/chat"


def _raise(*_a, **_k):
    raise TimeoutError("not yet")


class WebDriverTests(unittest.TestCase):
    def _driver(self, **kw) -> web.WebCopilotDriver:
        d = web.WebCopilotDriver(browser_mode="offscreen", **kw)
        d.windows = []
        d._set_window = lambda page, onscreen: d.windows.append(onscreen)
        return d

    def test_sign_in_wait_honours_stop_and_never_shows_the_window(self) -> None:
        d = self._driver()
        d._launched = True
        d._cancel_check = lambda: True
        page = SimpleNamespace(url=LOGIN, wait_for_url=_raise)
        with mock.patch.object(web, "SSO_ONSCREEN_AFTER_SECONDS", 1), mock.patch.object(web, "SSO_INTERACTIVE_WAIT_SECONDS", 1):
            t0 = time.monotonic()
            with self.assertRaises(Cancelled):
                d._wait_for_sign_in(page, 60)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertNotIn(True, d.windows)      # Stop pressed: the sign-in window was never brought on-screen

    def test_stop_before_submit_never_types_the_prompt(self) -> None:
        d = self._driver()
        typed = []
        box = SimpleNamespace(click=lambda: None, fill=lambda t: typed.append(t), type=lambda t: typed.append(t),
                              inner_text=lambda: "")
        page = SimpleNamespace(locator=lambda *_a: SimpleNamespace(count=lambda: 0), keyboard=SimpleNamespace(press=lambda *_a: None))
        d.connect = lambda *a, **k: page
        d.find_input = lambda *a, **k: box
        with self.assertRaises(Cancelled):
            d.send_message("do not send me", cancel_check=lambda: True)
        self.assertEqual(typed, [])
        self.assertIsNone(d._cancel_check)     # cleared after the call

    def _connect(self, already: bool) -> list:
        d = self._driver(force_fresh=False)
        page = SimpleNamespace(url=CHAT, is_closed=lambda: False)
        context = SimpleNamespace(pages=[page])
        brow = SimpleNamespace(contexts=[context])
        pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda *_a: brow))
        with mock.patch.object(web.browser, "cdp_version", return_value={"Browser": "Edg"} if already else None), \
                mock.patch.object(web.edge, "ensure_profile_edge", return_value=None), \
                mock.patch.object(web, "sync_playwright", return_value=SimpleNamespace(start=lambda: pw)):
            d._wait_for_sign_in = lambda *_a: None
            d.connect()
        return [d._launched, d.windows]

    def test_offscreen_parks_only_an_edge_it_launched(self) -> None:
        self.assertEqual(self._connect(already=False), [True, [False]])   # launched: parked off-screen
        self.assertEqual(self._connect(already=True), [False, []])        # reused (--reuse-browser): left alone

    # ---- code review round 2
    def test_close_after_stop_during_launch_ends_our_edge(self) -> None:
        for edge_up, launched, calls in ((True, True, 1), (False, True, 0), (True, False, 0)):
            d = self._driver()
            d._launched, d._edge_up = launched, edge_up       # Stop came before Playwright connected: _browser is None
            with mock.patch.object(web.edge, "kill_profile_edge") as kill, mock.patch.object(web.browser, "kill_cdp_browser") as shared_kill:
                d.close()
            self.assertEqual(kill.call_count, calls, (edge_up, launched))
            if calls:
                self.assertTrue(web.edge.same_path(kill.call_args.args[0], web.edge.default_profile_dir()))
            shared_kill.assert_not_called()     # never the shared browser-automation profile

    def test_interrupted_sign_in_runs_again_on_the_next_turn(self) -> None:
        d = self._driver()
        d._launched = True
        d._page = SimpleNamespace(url=LOGIN, is_closed=lambda: False, wait_for_url=_raise)
        d._signed_in = False                                  # a Stop interrupted the first wait
        with mock.patch.object(web, "SSO_ONSCREEN_AFTER_SECONDS", 0.2), mock.patch.object(web, "SSO_INTERACTIVE_WAIT_SECONDS", 0.2):
            with self.assertRaises(TimeoutError):
                d.connect()
        self.assertIn(True, d.windows)                         # the sign-in window was shown again

    def test_sign_in_never_moves_a_reused_edge(self) -> None:
        d = self._driver()
        d._launched = False
        page = SimpleNamespace(url=LOGIN, wait_for_url=_raise)
        with mock.patch.object(web, "SSO_ONSCREEN_AFTER_SECONDS", 0.2), mock.patch.object(web, "SSO_INTERACTIVE_WAIT_SECONDS", 0.2):
            d._wait_for_sign_in(page, 60)
        self.assertEqual(d.windows, [])

    def test_sign_in_detects_ready_url_in_secondary_tab(self) -> None:
        d = self._driver()
        d._launched = True
        tab1 = SimpleNamespace(url=LOGIN, wait_for_url=_raise)
        tab2 = SimpleNamespace(url="https://m365.cloud.microsoft/chat?es=SSR", wait_for_url=_raise)
        d._context = SimpleNamespace(pages=[tab1, tab2])
        with mock.patch.object(web, "SSO_ONSCREEN_AFTER_SECONDS", 0.05), mock.patch.object(web, "SSO_INTERACTIVE_WAIT_SECONDS", 0.2):
            d._wait_for_sign_in(tab1, 60)
        self.assertIs(d._page, tab2)

    def test_recognises_both_enterprise_m365_and_consumer_copilot_urls(self) -> None:
        d = self._driver()
        self.assertTrue(d._ready_url("https://m365.cloud.microsoft/chat"))
        self.assertTrue(d._ready_url("https://copilot.microsoft.com/"))
        self.assertTrue(d._own_page_url("https://copilot.microsoft.com/chats/123"))
        self.assertFalse(d._ready_url("https://login.live.com/oauth20_authorize.srf"))
        self.assertTrue(web._is_login_url("https://accounts.google.com/v3/signin"))
        self.assertTrue(web._is_login_url("https://login.microsoftonline.com/common/oauth2"))
        self.assertTrue(web._is_login_url("https://chat.deepseek.com/sign_in"))

    def test_switch_account_headless_refused(self) -> None:
        d = self._driver()
        d.browser_mode = "headless"
        with self.assertRaises(RuntimeError):
            d.switch_account()

    def test_switch_account_clears_only_engine_domains_and_surfaces_window(self) -> None:
        import deepseek_web as deepseek

        d = self._driver()
        d._launched = True
        d._signed_in = True
        deleted: list[dict] = []
        cleared_origins: list[str] = []
        evaluated: list[str] = []
        navigated: list[str] = []

        class FakeCdp:
            def send(self, method: str, params=None):
                if method == "Network.getAllCookies":
                    return {"cookies": [
                        {"name": "copilot_tok", "domain": ".copilot.microsoft.com", "path": "/"},
                        {"name": "ms_auth", "domain": "login.microsoftonline.com", "path": "/"},
                        {"name": "gem_sid", "domain": ".gemini.google.com", "path": "/"},
                        {"name": "other", "domain": ".example.com", "path": "/"},
                    ]}
                if method == "Network.deleteCookies":
                    deleted.append(params)
                elif method == "Storage.clearDataForOrigin":
                    cleared_origins.append(params["origin"])
                return {}

            def detach(self) -> None:
                pass

        # Page transitions: initially on login URL, then back to ready chat URL on poll
        urls = iter([LOGIN, LOGIN, "https://m365.cloud.microsoft/chat"])
        page = SimpleNamespace(
            url=LOGIN,
            is_closed=lambda: False,
            goto=lambda u, **_kw: navigated.append(u),
            evaluate=lambda js: evaluated.append(js),
        )
        # Override _live_url progression cleanly
        d._context = SimpleNamespace(
            pages=[page],
            new_cdp_session=lambda _p: FakeCdp(),
        )
        d._page = page
        d._connect_page = lambda **_kw: page
        d._live_url = lambda _p: next(urls, "https://m365.cloud.microsoft/chat")

        with mock.patch.object(web.time, "sleep", return_value=None):
            ok = d.switch_account(timeout_seconds=10)

        self.assertTrue(ok)
        self.assertTrue(d._signed_in)
        self.assertEqual(navigated, [d.url])
        deleted_names = [c["name"] for c in deleted]
        self.assertEqual(deleted_names, ["copilot_tok", "ms_auth"])
        self.assertNotIn("gem_sid", deleted_names)
        self.assertNotIn("other", deleted_names)
        self.assertNotIn("https://gemini.google.com", cleared_origins)
        self.assertTrue(any("localStorage.clear()" in s for s in evaluated))
        # Window shown on-screen then re-parked off-screen
        self.assertEqual(d.windows, [True, False])
        self.assertIn("deepseek.com", deepseek.DeepSeekWebDriver()._auth_domains())


if __name__ == "__main__":
    unittest.main()
