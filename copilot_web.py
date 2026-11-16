"""copilot_web.py — Web app automation for Microsoft 365 Copilot & Consumer Copilot.

Automates M365 Copilot (https://m365.cloud.microsoft/chat) and Consumer Copilot
(https://copilot.microsoft.com/) via Playwright over Chrome DevTools Protocol (CDP)
using Microsoft Edge with corporate Single Sign-On (SSO).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from playwright.sync_api import sync_playwright

from browser_automation import browser, runtime

import neo_edge as edge

M365_COPILOT_WEB_URL = "https://m365.cloud.microsoft/chat"
CONSUMER_COPILOT_WEB_URL = "https://copilot.microsoft.com/"

CANDIDATE_INPUT_SELECTORS = [
    "#m365-chat-editor-target-element",
    "#userInput",
    'textarea[id="userInput"]',
    'span[role="textbox"][aria-label*="Message Copilot"]',
    '[role="textbox"][aria-label*="Message Copilot"]',
    '[role="textbox"][contenteditable="true"]',
    "span.fai-EditorInput__input",
    'textarea[placeholder*="Ask Copilot"]',
    'textarea[placeholder*="Message Copilot"]',
    'textarea[aria-label*="Message Copilot"]',
    'textarea[aria-label*="Ask Copilot"]',
    'div[contenteditable="true"][aria-label*="Copilot"]',
    'div[contenteditable="true"]',
    "textarea#searchbox",
    "textarea",
]

CANDIDATE_SUBMIT_SELECTORS = [
    'button[aria-label="Send"]',
    "button.fai-SendButton",
    'button[data-testid="submit-button"]',
    'button[aria-label*="Send"]',
    'button[aria-label*="Submit"]',
    'button[title*="Submit"]',
    'button[title*="Send"]',
]

CANDIDATE_STOP_SELECTORS = [
    'button[aria-label*="Stop generating"]',
    'button[aria-label*="Stop responding"]',
    'button[data-testid="stop-button"]',
    'button:has-text("Stop generating")',
    'button:has-text("Stop responding")',
    'button:has-text("Stop")',
]

CANDIDATE_MESSAGE_SELECTORS = [
    ".fai-CopilotMessage__content",
    '[class*="CopilotMessage__content"]',
    ".fai-CopilotMessage",
    '[class*="CopilotMessage"]',
    'div[role="article"][aria-labelledby*="copilot-message"]',
    '[data-content="ai-message"]',
    '[data-testid="ai-message"]',
]

CANDIDATE_NEW_CHAT_SELECTORS = [
    'a[aria-label*="New chat"]',
    'a:has-text("New chat")',
    'button[aria-label*="New chat"]',
    'button[data-testid="new-chat-button"]',
    'button:has-text("New chat")',
    'button[aria-label*="New topic"]',
    'button:has-text("New topic")',
    'a[aria-label*="New topic"]',
]


def clean_copilot_web_text(raw_text: str) -> str:
    """Clean UI headers, reasoning preambles, and sensitivity notes from web response."""
    text = raw_text.strip()
    if text.startswith("Copilot said:"):
        text = text[len("Copilot said:"):].strip()
    # Strip reasoning header, e.g. "Reasoned for 1s\n\n"
    text = re.sub(r"^Reasoned for \d+s\s*", "", text, flags=re.IGNORECASE)
    # Strip sensitivity classification disclaimer
    text = re.sub(
        r"Sensitivity label not applied\. The generated file does not contain a label and may require (?:manual labeling before saving or sharing to protect the data\.|classification\.)\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Strip trailing action button text if captured in inner_text
    text = re.sub(
        r"\n\s*(?:Share feedback|Copy Response|I like something|I don't like something|More options)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


BROWSER_MODES = ("offscreen", "visible", "headless")

# Off-screen mode (plan v0.4 D5). Measured 2026-10-30 (Edge, Win11): a window at -32000,-32000
# with native occlusion and renderer backgrounding disabled keeps document.visibilityState
# 'visible' and requestAnimationFrame running (~32 fps), so the web app streams normally.
OFFSCREEN_POS = (-32000, -32000)
WINDOW_SIZE = (1280, 900)
OFFSCREEN_LAUNCH_ARGS = [
    f"--window-position={OFFSCREEN_POS[0]},{OFFSCREEN_POS[1]}",
    f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}",
    "--disable-features=CalculateNativeWinOcclusion",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]
HEADLESS_LAUNCH_ARGS = ["--headless=new", f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}"]
SSO_ONSCREEN_AFTER_SECONDS = 20      # offscreen: login page not clearing for this long -> show the window
SSO_INTERACTIVE_WAIT_SECONDS = 300   # then wait this long for the user to finish signing in


def _is_chat_url(url: str) -> bool:
    u = (url or "").lower()
    if _is_login_url(u):
        return False
    return ("cloud.microsoft" in u and "chat" in u) or "copilot.microsoft.com" in u


def _is_login_url(url: str) -> bool:
    u = (url or "").lower()
    return (
        "login.microsoftonline.com" in u
        or "login.live.com" in u
        or "login.microsoft.com" in u
        or "accounts.google.com" in u
        or "chat.deepseek.com/sign_in" in u
        or "chat.deepseek.com/login" in u
    )


class _Cancelled(Exception):
    pass


def _cancelled_exc():
    try:
        from neo_events import Cancelled
        return Cancelled
    except Exception:  # noqa: BLE001 - module used standalone
        return _Cancelled


def _page_alive(p) -> bool:
    if p is None:
        return False
    try:
        return not p.is_closed() if callable(getattr(p, "is_closed", None)) else True
    except Exception:
        return False


class WebCopilotDriver:
    """Automation driver for M365 & Consumer Copilot in Microsoft Edge over CDP.

    browser_mode: ``offscreen`` (default; window parked at -32000,-32000 and brought on-screen
    only when sign-in needs the user), ``visible`` (0.3.0 behaviour) or ``headless``
    (experimental). notify(text) reports things the user must know (sign-in window shown).
    profile_dir: agent-neo's own Edge profile (default %LOCALAPPDATA%\\agent-neo-edge); the driver
    only ever launches, drives and ends the Edge on that profile (neo_edge).
    """

    def __init__(
        self,
        port: int = edge.DEFAULT_CDP_PORT,
        url: str = M365_COPILOT_WEB_URL,
        headless: bool = False,
        force_fresh: bool = True,
        browser_mode: str = "offscreen",
        notify: Callable[[str], None] | None = None,
        profile_dir: str | None = None,
    ):
        self.port = port or edge.DEFAULT_CDP_PORT
        self.profile_dir = profile_dir or edge.default_profile_dir()
        reason = edge.check_profile_dir(self.profile_dir)
        if reason:
            raise ValueError(f"agent-neo browser profile refused: {reason}")
        self.url = url
        self.browser_mode = "headless" if headless else (browser_mode or "offscreen")
        if self.browser_mode not in BROWSER_MODES:
            raise ValueError(f"browser_mode must be one of {BROWSER_MODES}, not {browser_mode!r}")
        self.headless = self.browser_mode == "headless"
        self.force_fresh = force_fresh
        self.notify = notify
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._owner = True          # False for a sub-agent tab sharing the parent's connection
        self._launched = False      # this driver launched Edge (offscreen/headless close it on exit)
        self._signed_in = False     # the cached page got past sign-in (False again if Stop interrupted the wait)
        self._edge_up = False       # ensure_cdp_browser returned: Edge is up on self.port
        self._cancel_check: Callable[[], bool] | None = None   # set for the duration of send_message

    # URL hooks: M365 or Consumer Copilot chat by default; gemini_web.py, deepseek_web.py, whatsapp_web.py and custom_driver.py override them.
    def _ready_url(self, url: str) -> bool:
        return _is_chat_url(url)

    def _own_page_url(self, url: str) -> bool:
        u = (url or "").lower()
        return "cloud.microsoft" in u or "copilot.microsoft.com" in u

    def _auth_domains(self) -> tuple[str, ...]:
        return ("cloud.microsoft", "copilot.microsoft.com", "microsoftonline.com", "live.com", "microsoft.com")

    @staticmethod
    def _domain_matches(cookie_domain: str, auth_domains: tuple[str, ...]) -> bool:
        d = (cookie_domain or "").lstrip(".").lower()
        if not d:
            return False
        return any(d == ad or d.endswith("." + ad) for ad in auth_domains)

    def _live_url(self, page: Any) -> str:
        """Query live URL from browser over CDP or fallback to cached property."""
        if not page:
            return ""
        if callable(getattr(page, "evaluate", None)):
            try:
                val = page.evaluate("() => window.location.href")
                if val and isinstance(val, str):
                    return val
            except Exception:
                pass
        return getattr(page, "url", "") or ""

    def _cdp_page_urls(self) -> list[str]:
        """Direct query to Chromium's /json/list endpoint to detect live page navigations."""
        if not self.port:
            return []
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=0.8)
            targets = json.loads(req.read().decode("utf-8"))
            return [t["url"] for t in targets if t.get("type") == "page" and "url" in t]
        except Exception:
            return []

    def _check_cancel(self, where: str) -> None:
        """Stop is honoured before the prompt is submitted too: while Edge starts, during sign-in, before typing."""
        if self._cancel_check is not None and self._cancel_check():
            raise _cancelled_exc()(f"cancelled {where}")

    def _say(self, text: str) -> None:
        if self.notify:
            try:
                self.notify(text)
                return
            except Exception:  # noqa: BLE001
                pass
        print(f"  [copilot backend] {text}")

    # ---- window placement (CDP, deterministic; no UI automation)
    def _browser_cdp(self):
        return self._browser.new_browser_cdp_session()

    def _set_window(self, page, *, onscreen: bool) -> None:
        if self.browser_mode != "offscreen" or page is None:
            return
        try:
            cdp = self._browser_cdp()
            page_cdp = page.context.new_cdp_session(page)
            try:
                target_id = page_cdp.send("Target.getTargetInfo")["targetInfo"]["targetId"]
                window_id = cdp.send("Browser.getWindowForTarget", {"targetId": target_id})["windowId"]
                if onscreen:
                    cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "normal"}})
                    bounds = {"left": 100, "top": 100, "width": WINDOW_SIZE[0], "height": WINDOW_SIZE[1]}
                    cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": bounds})
                elif sys.platform == "darwin":
                    cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "minimized"}})
                else:
                    cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "normal"}})
                    bounds = {"left": OFFSCREEN_POS[0], "top": OFFSCREEN_POS[1], "width": WINDOW_SIZE[0], "height": WINDOW_SIZE[1]}
                    cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": bounds})
            finally:
                for session in (page_cdp, cdp):
                    try:
                        session.detach()
                    except Exception:  # noqa: BLE001
                        pass
            if onscreen:
                page.bring_to_front()
        except Exception as exc:  # noqa: BLE001 - placement is best effort; never fail a turn on it
            self._say(f"Could not {'show' if onscreen else 'park'} the browser window: {exc}")

    def _new_background_page(self, url: str):
        """Open url in a new off-screen window without taking the keyboard focus."""
        cdp = self._browser_cdp()
        try:
            with self._context.expect_page(timeout=20000) as info:
                cdp.send("Target.createTarget", {"url": url, "newWindow": True, "background": True})
            page = info.value
        finally:
            try:
                cdp.detach()
            except Exception:  # noqa: BLE001
                pass
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:  # noqa: BLE001
            pass
        return page

    def _connect_page(self, timeout_seconds: int = 60):
        """Ensure Edge is running with CDP enabled and attach to an engine page without waiting for sign-in."""
        if self._page is not None and not self._page.is_closed():
            return self._page
        if not self._owner:
            raise RuntimeError("sub-agent tab was closed")

        offscreen = self.browser_mode == "offscreen"
        extra = OFFSCREEN_LAUNCH_ARGS if offscreen else HEADLESS_LAUNCH_ARGS if self.headless else []
        already = browser.cdp_version(self.port) is not None
        # set before the launch, so a shutdown that lands mid-launch still knows this Edge is ours
        self._launched = self.force_fresh or not already
        edge.ensure_profile_edge(
            self.profile_dir,
            self.port,
            start_url=self.url,
            force_fresh=self.force_fresh,
            extra_args=extra,
            no_startup_window=offscreen,
        )
        self._edge_up = True        # our CDP port answered: the Edge on it is the one this call started (or reused)
        self._check_cancel("while Edge was starting")

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()

        # Find existing tab or navigate
        target_page = None
        for p in self._context.pages:
            if self._ready_url(p.url):
                target_page = p
                break

        if not target_page:
            for p in self._context.pages:
                if self._own_page_url(p.url) or _is_login_url(p.url):
                    target_page = p
                    break

        if not target_page:
            if offscreen:
                target_page = self._new_background_page(self.url)
            else:
                target_page = self._context.new_page()
                target_page.goto(self.url, wait_until="domcontentloaded")
        if offscreen and self._launched:
            self._set_window(target_page, onscreen=False)   # park only a browser this driver launched
        elif offscreen:
            # code review r1: a reused Edge (--reuse-browser) is the user's; never hide it (close() would not restore it)
            self._say("Reusing an Edge that agent-neo did not start: its window is left where it is.")

        self._page = target_page
        return self._page

    def connect(self, timeout_seconds: int = 60):
        """Ensure Edge is running with CDP enabled, then connect Playwright."""
        already_open = self._page is not None and not self._page.is_closed()
        page = self._connect_page(timeout_seconds=timeout_seconds)
        if already_open:
            if not self._signed_in:
                # a sign-in wait was interrupted (Stop): run it again, so the window is shown when sign-in needs it
                self._wait_for_sign_in(page, timeout_seconds)
                self._signed_in = True
            return page
        self._signed_in = False
        self._wait_for_sign_in(page, timeout_seconds)
        self._signed_in = True
        return self._page

    def _clear_engine_session(self, page: Any) -> int:
        """Clear cookies and site storage only for this engine's auth/chat domains."""
        domains = self._auth_domains()
        cleared = 0
        cdp = None
        try:
            if self._context and callable(getattr(self._context, "new_cdp_session", None)):
                cdp = self._context.new_cdp_session(page)
                all_cookies = cdp.send("Network.getAllCookies").get("cookies", [])
                origins: set[str] = set()
                for c in all_cookies:
                    cdom = c.get("domain", "")
                    if self._domain_matches(cdom, domains):
                        try:
                            cdp.send("Network.deleteCookies", {
                                "name": c.get("name", ""),
                                "domain": cdom,
                                "path": c.get("path", "/"),
                            })
                            cleared += 1
                        except Exception:  # noqa: BLE001
                            pass
                        host = cdom.lstrip(".")
                        if host:
                            origins.add(f"https://{host}")
                live = self._live_url(page)
                if live.startswith(("http://", "https://")):
                    from urllib.parse import urlsplit
                    parts = urlsplit(live)
                    if parts.netloc:
                        origins.add(f"{parts.scheme}://{parts.netloc}")
                for d in domains:
                    if d:
                        origins.add(f"https://{d}")
                for origin in origins:
                    try:
                        cdp.send("Storage.clearDataForOrigin", {
                            "origin": origin,
                            "storageTypes": "cookies,local_storage,session_storage,indexeddb,cache_storage,service_workers",
                        })
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            if cdp is not None:
                try:
                    cdp.detach()
                except Exception:  # noqa: BLE001
                    pass

        if cleared == 0 and self._context and callable(getattr(self._context, "clear_cookies", None)):
            for d in domains:
                if not d:
                    continue
                try:
                    self._context.clear_cookies(domain=d)
                except TypeError:
                    break
                except Exception:  # noqa: BLE001
                    pass

        if callable(getattr(page, "evaluate", None)):
            try:
                page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch {} }")
            except Exception:  # noqa: BLE001
                pass
        return cleared

    @staticmethod
    def _has_login_prompt(page: Any) -> bool:
        """True when the current page displays a prominent Sign in / Log in control."""
        if not hasattr(page, "locator"):
            return False
        for sel in (
            'button:has-text("Sign in")',
            'a:has-text("Sign in")',
            'button:has-text("Log in")',
            'a:has-text("Log in")',
        ):
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def switch_account(self, timeout_seconds: int = 300) -> bool:
        """Log out of this web engine by clearing its cookies/storage and prompt the user to sign in."""
        if self.browser_mode == "headless":
            raise RuntimeError(
                "Cannot switch accounts in headless browser mode. "
                "Use browser_mode = \"offscreen\" or \"visible\" so the sign-in window can be shown."
            )
        page = self._connect_page(timeout_seconds=min(timeout_seconds, 60))
        self._clear_engine_session(page)
        self._signed_in = False
        try:
            page.goto(self.url, wait_until="domcontentloaded")
        except Exception:  # noqa: BLE001
            pass

        if self.browser_mode == "offscreen" and self._launched:
            self._set_window(page, onscreen=True)
        self._say(
            "Sign-in needed (switch account): the Edge window is now on screen. "
            "Complete sign-in there; it will move off-screen again when the chat page loads."
        )

        time.sleep(1.5)
        deadline = time.monotonic() + timeout_seconds
        saw_not_ready = False
        saw_login_prompt = False
        idle_ready_cycles = 0

        while time.monotonic() < deadline:
            self._check_cancel("while switching account")
            candidates = [page] if _page_alive(page) else []
            if self._context and hasattr(self._context, "pages"):
                candidates.extend(p for p in self._context.pages if p is not page and _page_alive(p))

            ready_cand = None
            any_not_ready = False
            for cand in candidates:
                u = self._live_url(cand)
                if _is_login_url(u) or not self._ready_url(u):
                    any_not_ready = True
                    saw_not_ready = True
                elif ready_cand is None:
                    ready_cand = cand

            if ready_cand is not None and not any_not_ready:
                has_prompt = self._has_login_prompt(ready_cand)
                if has_prompt:
                    saw_login_prompt = True
                    idle_ready_cycles = 0
                else:
                    idle_ready_cycles += 1
                if not has_prompt and (saw_not_ready or saw_login_prompt or idle_ready_cycles >= 3):
                    # Handle M365 landing page redirect to /chat
                    u = self._live_url(ready_cand)
                    if "cloud.microsoft" in u.lower() and not _is_login_url(u) and "chat" not in u.lower():
                        try:
                            ready_cand.goto(self.url, wait_until="domcontentloaded")
                        except Exception:  # noqa: BLE001
                            pass
                    self._page = ready_cand
                    self._signed_in = True
                    if self.browser_mode == "offscreen" and self._launched:
                        self._set_window(ready_cand, onscreen=False)
                        self._say("Signed in; the Edge window is off-screen again.")
                    else:
                        self._say("Signed in; account switch complete.")
                    self._clear_composer(ready_cand)
                    return True
            else:
                idle_ready_cycles = 0
            time.sleep(1.0)

        if self.browser_mode == "offscreen" and self._launched:
            self._set_window(page, onscreen=False)
        raise TimeoutError(f"Account sign-in was not completed within {timeout_seconds}s.")

    def _wait_for_sign_in(self, page, timeout_seconds: int) -> None:
        """Handle corporate SSO redirects. offscreen: show the window if sign-in needs the user."""
        start_wait = time.monotonic()
        limit = SSO_ONSCREEN_AFTER_SECONDS if self.browser_mode == "offscreen" else timeout_seconds

        def check_all() -> Any:
            candidates = [page] if _page_alive(page) else []
            if self._context and hasattr(self._context, "pages"):
                candidates.extend(p for p in self._context.pages if p is not page and _page_alive(p))
            for cand in candidates:
                cand_url = self._live_url(cand)
                if self._ready_url(cand_url):
                    return cand
                if "cloud.microsoft" in cand_url.lower() and not _is_login_url(cand_url) and "chat" not in cand_url.lower():
                    try:
                        cand.goto(self.url, wait_until="domcontentloaded")
                        if self._ready_url(self._live_url(cand)):
                            return cand
                    except Exception:
                        pass
                if _is_login_url(cand_url):
                    try:
                        cand.wait_for_url("**/chat*", timeout=1500)
                        if self._ready_url(self._live_url(cand)):
                            return cand
                    except Exception:  # noqa: BLE001
                        pass
            cdp_urls = self._cdp_page_urls()
            for u in cdp_urls:
                if self._ready_url(u):
                    for cand in candidates:
                        if self._ready_url(self._live_url(cand)):
                            return cand
                    if self._context and hasattr(self._context, "pages"):
                        for p in self._context.pages:
                            if _page_alive(p) and self._ready_url(self._live_url(p)):
                                return p
            return None

        while time.monotonic() - start_wait < limit:
            self._check_cancel("while waiting for sign-in")
            ready_page = check_all()
            if ready_page is not None:
                self._page = ready_page
                return
            time.sleep(1.0)

        ready_page = check_all()
        if ready_page is not None:
            self._page = ready_page
            return

        if self.browser_mode == "headless":
            raise RuntimeError(
                "Headless Edge could not reach M365 Copilot chat (sign-in did not complete without a window). "
                "Use browser_mode = \"offscreen\" (the default) so the sign-in window can be shown.")
        if self.browser_mode == "visible" or not self._launched:
            # visible: 0.3.0 behaviour, the user signs in in the visible window. A reused Edge is the user's own:
            # never move it on- or off-screen (close() would not put it back).
            return
        # offscreen: bring the window on-screen for the user, then park it again.
        win_page = page if _page_alive(page) else (self._context.pages[0] if (self._context and getattr(self._context, "pages", None)) else page)
        self._set_window(win_page, onscreen=True)
        self._say("Sign-in needed: the Edge window is now on screen. Complete sign-in there; "
                  "it will move off-screen again when the chat page loads.")
        deadline = time.monotonic() + SSO_INTERACTIVE_WAIT_SECONDS
        while time.monotonic() < deadline:
            ready_page = check_all()
            if ready_page is not None:
                self._page = ready_page
                self._set_window(ready_page, onscreen=False)
                self._say("Signed in; the Edge window is off-screen again.")
                return
            if self._cancel_check is not None and self._cancel_check():
                self._set_window(win_page, onscreen=False)
                self._check_cancel("while waiting for sign-in")
            time.sleep(1.0)
        raise TimeoutError(f"M365 sign-in was not completed within {SSO_INTERACTIVE_WAIT_SECONDS}s.")

    # ---- sub-agent conversations (plan v0.4 §3): a separate tab/window on the same connection
    def open_child(self):
        self.connect()
        child = WebCopilotDriver(port=self.port, url=self.url, force_fresh=False,
                                 browser_mode=self.browser_mode, notify=self.notify, profile_dir=self.profile_dir)
        child._pw, child._browser, child._context = self._pw, self._browser, self._context
        child._owner = False
        if self.browser_mode == "offscreen":
            page = self._new_background_page(self.url)
            child._set_window(page, onscreen=False)
        else:
            page = self._context.new_page()
            page.goto(self.url, wait_until="domcontentloaded")
        child._page = page
        child._signed_in = True     # same signed-in browser as the parent
        try:
            child.find_input(timeout_ms=45000)
        except Exception:
            self.close_child(child)
            raise
        return child, True

    def close_child(self, child: "WebCopilotDriver") -> None:
        child.close()

    def find_input(self, timeout_ms: int = 45000):
        """Locate the chat prompt input locator with retry."""
        page = self.connect()
        start = time.monotonic()
        while (time.monotonic() - start) * 1000 < timeout_ms:
            for selector in CANDIDATE_INPUT_SELECTORS:
                try:
                    loc = page.locator(selector).first
                    if loc.is_visible():
                        return loc
                except Exception:
                    pass
            time.sleep(0.8)

        # Fallback to waiting for primary selectors
        combined = ", ".join(CANDIDATE_INPUT_SELECTORS[:4])
        page.wait_for_selector(combined, timeout=timeout_ms)
        return page.locator(combined).first

    def new_conversation(self) -> None:
        """Start a new chat conversation."""
        page = self.connect()
        clicked = False
        for selector in CANDIDATE_NEW_CHAT_SELECTORS:
            try:
                loc = page.locator(selector).first
                if loc.is_visible():
                    loc.click()
                    clicked = True
                    time.sleep(1.5)
                    break
            except Exception:
                pass

        if not clicked:
            page.goto(self.url, wait_until="domcontentloaded")
            time.sleep(2.0)

        self._clear_composer(page)

    def _is_thread_url(self, url: str) -> bool:
        """True when *url* points to a specific Copilot conversation rather than the landing page."""
        if not url or not self._ready_url(url):
            return False
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path or ""
        if re.search(r"/chats?/[^/]{3,}", path, flags=re.IGNORECASE):
            return True
        qs = urllib.parse.parse_qs(parsed.query)
        return any(k.lower() in ("conversationid", "threadid", "chatid", "convid") and any(v.strip() for v in vals)
                   for k, vals in qs.items())

    def current_chat_url(self) -> str | None:
        """Return the active conversation URL if the browser is currently inside a specific chat thread."""
        page = self._page
        if page is None:
            return None
        try:
            if hasattr(page, "is_closed") and page.is_closed():
                return None
            url = str(getattr(page, "url", "") or "").strip()
        except Exception:  # noqa: BLE001
            return None
        return url if self._is_thread_url(url) else None

    def open_chat_url(self, url: str, timeout_seconds: int = 45) -> bool:
        """Navigate to a previously saved conversation URL. Returns False (falling back to new chat) if invalid/expired."""
        target = (url or "").strip()
        if not target or not self._own_page_url(target):
            return False
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme not in ("http", "https"):
            return False
        try:
            page = self.connect(timeout_seconds=timeout_seconds)
            if (getattr(page, "url", "") or "").strip() != target:
                page.goto(target, wait_until="domcontentloaded")
                time.sleep(1.2)
            cur = str(getattr(page, "url", "") or "")
            if not self._ready_url(cur):
                self._wait_for_sign_in(page, timeout_seconds)
            self.find_input(timeout_ms=15000)
            cur = str(getattr(page, "url", "") or "")
            if not self._is_thread_url(cur):
                return False
            self._clear_composer(page)
            return True
        except Exception:  # noqa: BLE001
            try:
                self.new_conversation()
            except Exception:  # noqa: BLE001
                pass
            return False

    def _clear_composer(self, page, input_box=None) -> None:
        """Ensure the chat composer input is completely cleared."""
        try:
            if input_box is None:
                input_box = self.find_input(timeout_ms=2000)
            if input_box.inner_text().strip():
                input_box.click()
                page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
                page.keyboard.press("Backspace")
                time.sleep(0.1)
        except Exception:
            pass

    def send_message(
        self,
        prompt: str,
        timeout_seconds: int = 180,
        status_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        """Send prompt to the M365 Copilot web app and wait for the response.

        cancel_check() is polled while waiting; when it returns True the driver clicks
        Copilot's Stop button (best effort) and raises Cancelled. It is also polled before the
        prompt is submitted (Edge start-up, sign-in, before typing), so Stop never sends it late.
        """
        self._cancel_check = cancel_check
        try:
            return self._send_message(prompt, timeout_seconds, status_callback, cancel_check)
        finally:
            self._cancel_check = None

    def _send_message(self, prompt: str, timeout_seconds: int, status_callback, cancel_check) -> str:
        page = self.connect()
        input_box = self.find_input()
        self._check_cancel("before the prompt was sent")

        # Count existing message bubbles before sending
        bubble_sel = ".fai-CopilotMessage, [role='article'][aria-labelledby*='copilot-message'], [data-content='ai-message'], [data-testid='ai-message']"
        prior_bubbles = page.locator(bubble_sel)
        prior_count = prior_bubbles.count()
        prior_last_text = ""
        if prior_count > 0:
            try:
                prior_last_text = prior_bubbles.nth(prior_count - 1).inner_text().strip()
            except Exception:
                pass

        # Focus and clean composer
        input_box.click()
        time.sleep(0.2)
        page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
        page.keyboard.press("Backspace")
        time.sleep(0.2)

        # Type prompt text into Lexical editor
        try:
            input_box.fill(prompt)
        except Exception:
            try:
                page.keyboard.insert_text(prompt)
            except Exception:
                input_box.type(prompt)
        time.sleep(0.4)
        self._check_cancel("before the prompt was sent")

        # Click Send button
        submitted = False
        for _ in range(5):
            for s_selector in CANDIDATE_SUBMIT_SELECTORS:
                try:
                    btn = page.locator(s_selector).first
                    if btn.is_visible() and not btn.is_disabled():
                        btn.click()
                        submitted = True
                        break
                except Exception:
                    pass
            if submitted:
                break
            time.sleep(0.3)

        if not submitted:
            page.keyboard.press("Enter")

        # Verify composer was cleared by submission; if not, retry click or Enter
        for _ in range(5):
            time.sleep(0.3)
            current_draft = input_box.inner_text().strip()
            if not current_draft:
                break
            try:
                send_btn = page.locator("button.fai-SendButton, button[aria-label='Send']").first
                if send_btn.is_visible():
                    send_btn.click()
                else:
                    page.keyboard.press("Enter")
            except Exception:
                pass

        if status_callback:
            status_callback("Prompt submitted in browser, waiting for Copilot generation...")

        start_time = time.monotonic()
        saw_stop_button = False
        last_seen_text = ""
        stable_count = 0

        while time.monotonic() - start_time < timeout_seconds:
            time.sleep(1.0)
            if cancel_check and cancel_check():
                self._stop_generation(page)
                raise _cancelled_exc()("cancelled while Copilot was generating")

            # Check if Stop generating button is visible
            for stop_sel in CANDIDATE_STOP_SELECTORS:
                try:
                    stop_btn = page.locator(stop_sel).first
                    if stop_btn.is_visible():
                        saw_stop_button = True
                        if status_callback:
                            status_callback("agent-neo is working...")
                        break
                except Exception:
                    pass

            # Check message bubble count
            current_bubbles = page.locator(bubble_sel)
            current_count = current_bubbles.count()

            # Target the latest message bubble
            target_bubble = None
            if current_count > prior_count:
                target_bubble = current_bubbles.nth(current_count - 1)
            elif current_count > 0:
                cand = current_bubbles.nth(current_count - 1)
                try:
                    if cand.inner_text().strip() != prior_last_text:
                        target_bubble = cand
                except Exception:
                    pass

            if target_bubble is None:
                continue

            # Check for completion indicator: Copy Response button on target bubble
            has_copy = False
            try:
                copy_btns = target_bubble.locator("button[aria-label='Copy Response'], button[aria-label*='Copy']")
                has_copy = copy_btns.count() > 0 and copy_btns.first.is_visible()
            except Exception:
                pass

            # Extract message text (prefer raw unrendered React Fiber text before Markdown/HTML sanitization)
            raw_text = ""
            try:
                if hasattr(target_bubble, "evaluate"):
                    fiber_text = target_bubble.evaluate(
                        """(node) => {
                            const target = node.querySelector('[data-testid="markdown-reply"]') || node;
                            const reactKey = Object.keys(target).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactProps'));
                            if (!reactKey) return '';
                            let cur = target[reactKey];
                            for (let depth = 0; depth < 14 && cur; depth++) {
                                const p = cur.memoizedProps || cur.pendingProps || {};
                                if (typeof p.replyMessage === 'string' && p.replyMessage.trim()) return p.replyMessage;
                                if (p.response && typeof p.response.text === 'string' && p.response.text.trim()) return p.response.text;
                                if (typeof p.legacyReplyMessage === 'string' && p.legacyReplyMessage.trim()) return p.legacyReplyMessage;
                                cur = cur.return;
                            }
                            return '';
                        }"""
                    )
                    if isinstance(fiber_text, str) and fiber_text.strip():
                        raw_text = fiber_text.strip()
                if not raw_text:
                    content_elem = target_bubble.locator(".fai-CopilotMessage__content").first
                    if content_elem.is_visible():
                        raw_text = content_elem.inner_text().strip()
                    else:
                        raw_text = target_bubble.inner_text().strip()
            except Exception:
                pass

            cleaned_text = clean_copilot_web_text(raw_text)

            # If still only reasoning header (cleaned text empty), keep waiting
            if not cleaned_text:
                continue

            # If Copy Response button is present, generation is 100% complete
            if has_copy and len(cleaned_text) > 0:
                is_still_generating = False
                for stop_sel in CANDIDATE_STOP_SELECTORS:
                    try:
                        if page.locator(stop_sel).first.is_visible():
                            is_still_generating = True
                            break
                    except Exception:
                        pass
                if not is_still_generating:
                    if status_callback:
                        status_callback("Generation complete (Copy Response indicator).")
                    self._clear_composer(page, input_box)
                    return cleaned_text

            # If Stop button disappeared and we have non-empty cleaned text
            if saw_stop_button:
                is_still_generating = False
                for stop_sel in CANDIDATE_STOP_SELECTORS:
                    try:
                        if page.locator(stop_sel).first.is_visible():
                            is_still_generating = True
                            break
                    except Exception:
                        pass
                if not is_still_generating and len(cleaned_text) > 0:
                    if status_callback:
                        status_callback("Generation complete (Stop button disappeared).")
                    self._clear_composer(page, input_box)
                    return cleaned_text

            # Stability fallback: text hasn't changed for 4+ seconds
            if cleaned_text and cleaned_text == last_seen_text:
                stable_count += 1
                if stable_count >= 4:
                    if status_callback:
                        status_callback("Generation complete (text stabilized).")
                    self._clear_composer(page, input_box)
                    return cleaned_text
            else:
                last_seen_text = cleaned_text
                stable_count = 0

        if last_seen_text:
            self._clear_composer(page, input_box)
            return last_seen_text

        raise TimeoutError(f"M365 Copilot web app did not produce a response within {timeout_seconds}s.")

    def _stop_generation(self, page) -> None:
        for stop_sel in CANDIDATE_STOP_SELECTORS:
            try:
                stop_btn = page.locator(stop_sel).first
                if stop_btn.is_visible():
                    stop_btn.click(timeout=2000)
                    return
            except Exception:  # noqa: BLE001
                pass

    def end_own_edge(self, wait_seconds: float = 3) -> None:
        """End the Edge on agent-neo's own profile (never any other Edge). Used when Playwright cannot close it."""
        try:
            edge.kill_profile_edge(self.profile_dir, self.port, wait_seconds=wait_seconds)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        """Close the Playwright connection (a sub-agent tab closes only its own page).

        In offscreen/headless mode the Edge this driver launched is closed too (CDP
        Browser.close): an invisible browser must not outlive the app. visible mode keeps
        0.3.0 behaviour (the window stays for the user).
        """
        if not self._owner:
            try:
                if self._page is not None and not self._page.is_closed():
                    self._page.close()
            except Exception:  # noqa: BLE001
                pass
            self._page = None
            return
        if self._browser is not None and self._launched and self.browser_mode in ("offscreen", "headless"):
            try:
                cdp = self._browser.new_browser_cdp_session()
                cdp.send("Browser.close")
            except Exception:  # noqa: BLE001 - the connection drops as the browser exits
                pass
        elif self._browser is None and self._launched and self._edge_up and self.browser_mode in ("offscreen", "headless"):
            # launched, but Stop/exit came before Playwright connected (code review r2): end it by agent-neo's own
            # profile (only one Edge can run on a profile, and this driver started it)
            self.end_own_edge()
            self._launched = False
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._pw = None


if __name__ == "__main__":
    print(f"Target URL: {M365_COPILOT_WEB_URL}")
    print("Testing WebCopilotDriver...")
    driver = WebCopilotDriver()
    try:
        page = driver.connect()
        print("Connected to page:", page.title(), page.url)
        inp = driver.find_input(5000)
        print("Found input element:", inp.is_visible())
    finally:
        driver.close()
