"""copilot_web.py — Web app automation for Microsoft 365 Copilot & Consumer Copilot.

Automates M365 Copilot (https://m365.cloud.microsoft/chat) and Consumer Copilot
(https://copilot.microsoft.com/) via Playwright over Chrome DevTools Protocol (CDP)
using Microsoft Edge with corporate Single Sign-On (SSO).
"""
from __future__ import annotations

import os
import re
import sys
import time
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

    # URL hooks: M365 or Consumer Copilot chat by default; gemini_web.py, deepseek_web.py and custom_driver.py override them.
    def _ready_url(self, url: str) -> bool:
        return _is_chat_url(url)

    def _own_page_url(self, url: str) -> bool:
        u = (url or "").lower()
        return "cloud.microsoft" in u or "copilot.microsoft.com" in u

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
                cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "normal"}})
                if onscreen:
                    bounds = {"left": 100, "top": 100, "width": WINDOW_SIZE[0], "height": WINDOW_SIZE[1]}
                else:
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

    def connect(self, timeout_seconds: int = 60):
        """Ensure Edge is running with CDP enabled, then connect Playwright."""
        if self._page is not None and not self._page.is_closed():
            if not self._signed_in:
                # a sign-in wait was interrupted (Stop): run it again, so the window is shown when sign-in needs it
                self._wait_for_sign_in(self._page, timeout_seconds)
                self._signed_in = True
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
        self._signed_in = False
        self._wait_for_sign_in(target_page, timeout_seconds)
        self._signed_in = True
        return self._page

    def _wait_for_sign_in(self, page, timeout_seconds: int) -> None:
        """Handle corporate SSO redirects. offscreen: show the window if sign-in needs the user."""
        start_wait = time.monotonic()
        limit = SSO_ONSCREEN_AFTER_SECONDS if self.browser_mode == "offscreen" else timeout_seconds
        while time.monotonic() - start_wait < limit:
            self._check_cancel("while waiting for sign-in")
            candidates = [page] if _page_alive(page) else []
            if self._context and hasattr(self._context, "pages"):
                candidates.extend(p for p in self._context.pages if p is not page and _page_alive(p))
            for cand in candidates:
                if self._ready_url(cand.url):
                    self._page = cand
                    return
                if _is_login_url(cand.url):
                    print("  [web backend] Completing corporate SSO login redirect in Edge...")
                    try:
                        cand.wait_for_url("**/chat*", timeout=2000)
                        if self._ready_url(cand.url):
                            self._page = cand
                            return
                    except Exception:  # noqa: BLE001
                        pass
            time.sleep(1.0)
        candidates = [page] if _page_alive(page) else []
        if self._context and hasattr(self._context, "pages"):
            candidates.extend(p for p in self._context.pages if p is not page and _page_alive(p))
        for cand in candidates:
            if self._ready_url(cand.url):
                self._page = cand
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
            candidates = [page] if _page_alive(page) else []
            if self._context and hasattr(self._context, "pages"):
                candidates.extend(p for p in self._context.pages if p is not page and _page_alive(p))
            for cand in candidates:
                if self._ready_url(cand.url):
                    self._page = cand
                    self._set_window(cand, onscreen=False)
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

    def _clear_composer(self, page, input_box=None) -> None:
        """Ensure the chat composer input is completely cleared."""
        try:
            if input_box is None:
                input_box = self.find_input(timeout_ms=2000)
            if input_box.inner_text().strip():
                input_box.click()
                page.keyboard.press("Control+A")
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
        page.keyboard.press("Control+A")
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

            # Extract message text
            raw_text = ""
            try:
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
