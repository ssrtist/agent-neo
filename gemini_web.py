"""gemini_web.py — Web app automation for Google Gemini (https://gemini.google.com/app).

Automates Google Gemini web chat via Playwright over Chrome DevTools Protocol (CDP)
using agent-neo's dedicated Microsoft Edge profile (%LOCALAPPDATA%\\agent-neo-edge on port 9365).
Supports offscreen, visible, and headless window modes, automatic sign-in window elevation
when redirected to accounts.google.com, cooperative Stop cancellation, and multi-tab sub-agents.
"""
from __future__ import annotations

import re
import time
from typing import Callable

import copilot_web as web
import neo_edge as edge

GEMINI_WEB_URL = "https://gemini.google.com/app"

GEMINI_INPUT_SELECTORS = [
    'div.ql-editor[contenteditable="true"]',
    "rich-textarea div.ql-editor",
    "div.ql-editor",
    'rich-textarea [contenteditable="true"]',
    '[role="textbox"][aria-label*="prompt" i]',
    '[role="textbox"][aria-label*="Gemini" i]',
    'div[contenteditable="true"]',
]

GEMINI_SUBMIT_SELECTORS = [
    'button[aria-label="Send message"]',
    'button[aria-label*="Send message" i]',
    "button.send-button",
    'button[aria-label*="Send" i]',
    'button[mattooltip*="Send" i]',
]

GEMINI_STOP_SELECTORS = [
    'button[aria-label*="Stop response" i]',
    'button[aria-label*="Stop" i]',
    "button.stop-button",
]

GEMINI_ACTION_SELECTORS = [
    'button[aria-label*="Copy" i]',
    'button[aria-label*="Good response" i]',
    'button[aria-label*="Bad response" i]',
    'button[aria-label*="Modify response" i]',
    'button[aria-label*="Share" i]',
]

GEMINI_MESSAGE_SELECTORS = [
    "model-response message-content",
    "message-content",
    ".model-response-text",
]

GEMINI_NEW_CHAT_SELECTORS = [
    'a[aria-label*="New chat" i]',
    'button[aria-label*="New chat" i]',
    '[data-test-id="expanded-button"]',
    'a:has-text("New chat")',
    'button:has-text("New chat")',
]


def _is_gemini_url(url: str) -> bool:
    u = (url or "").lower()
    return "gemini.google.com" in u and not _is_google_login_url(u)


def _is_google_login_url(url: str) -> bool:
    u = (url or "").lower()
    return "accounts.google.com" in u or "workspace.google.com" in u


def clean_gemini_web_text(raw_text: str) -> str:
    """Strip Gemini web UI preambles and action labels from response text."""
    text = (raw_text or "").strip()
    text = re.sub(r"^(?:Gemini said|Model said)\s*:?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^Show thinking\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\n\s*(?:Show drafts|Thumb up|Thumb down|Modify response|Share & export| Copy |Google Search)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def _dismiss_gemini_popups(page) -> None:
    """Dismiss onboarding tooltips or CDK backdrops that can intercept clicks."""
    try:
        page.evaluate(
            """() => {
                const backdrop = document.querySelector('.cdk-overlay-backdrop');
                if (backdrop) backdrop.click();
                const tooltip = document.querySelector('.gem-discovery-tooltip-container');
                if (tooltip) tooltip.click();
            }"""
        )
    except Exception:  # noqa: BLE001
        pass


class GeminiWebDriver(web.WebCopilotDriver):
    """Automation driver for Google Gemini web chat (https://gemini.google.com/app) over CDP."""

    backend_name = "gemini"

    def __init__(
        self,
        port: int = edge.DEFAULT_CDP_PORT,
        url: str = GEMINI_WEB_URL,
        headless: bool = False,
        force_fresh: bool = True,
        browser_mode: str = "offscreen",
        notify: Callable[[str], None] | None = None,
        profile_dir: str | None = None,
    ):
        super().__init__(
            port=port,
            url=url or GEMINI_WEB_URL,
            headless=headless,
            force_fresh=force_fresh,
            browser_mode=browser_mode,
            notify=notify,
            profile_dir=profile_dir,
        )

    def _ready_url(self, url: str) -> bool:
        return _is_gemini_url(url)

    def _own_page_url(self, url: str) -> bool:
        return "gemini.google.com" in (url or "").lower()

    def _say(self, text: str) -> None:
        if self.notify:
            try:
                self.notify(text)
                return
            except Exception:  # noqa: BLE001
                pass
        print(f"  [gemini backend] {text}")

    def open_child(self):
        self.connect()
        child = GeminiWebDriver(
            port=self.port,
            url=self.url,
            force_fresh=False,
            browser_mode=self.browser_mode,
            notify=self.notify,
            profile_dir=self.profile_dir,
        )
        child._pw, child._browser, child._context = self._pw, self._browser, self._context
        child._owner = False
        if self.browser_mode == "offscreen":
            page = self._new_background_page(self.url)
            child._set_window(page, onscreen=False)
        else:
            page = self._context.new_page()
            page.goto(self.url, wait_until="domcontentloaded")
        child._page = page
        child._signed_in = True
        try:
            child.find_input(timeout_ms=45000)
        except Exception:
            self.close_child(child)
            raise
        return child, True

    def find_input(self, timeout_ms: int = 45000):
        """Locate the Gemini Quill chat input editor with retry."""
        page = self.connect()
        start = time.monotonic()
        while (time.monotonic() - start) * 1000 < timeout_ms:
            self._check_cancel("while looking for the Gemini chat input")
            _dismiss_gemini_popups(page)
            for selector in GEMINI_INPUT_SELECTORS:
                try:
                    loc = page.locator(selector).first
                    if loc.is_visible():
                        return loc
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.6)

        combined = ", ".join(GEMINI_INPUT_SELECTORS[:3])
        page.wait_for_selector(combined, timeout=timeout_ms)
        return page.locator(combined).first

    def new_conversation(self) -> None:
        """Start a fresh Gemini conversation."""
        page = self.connect()
        _dismiss_gemini_popups(page)
        clicked = False
        for selector in GEMINI_NEW_CHAT_SELECTORS:
            try:
                loc = page.locator(selector).first
                if loc.is_visible():
                    loc.click()
                    clicked = True
                    time.sleep(1.2)
                    break
            except Exception:  # noqa: BLE001
                pass

        if not clicked:
            page.goto(self.url, wait_until="domcontentloaded")
            time.sleep(1.5)

        self._clear_composer(page)

    def _bubble_locator(self, page):
        return page.locator(", ".join(GEMINI_MESSAGE_SELECTORS))

    def _is_generating(self, page) -> bool:
        for stop_sel in GEMINI_STOP_SELECTORS:
            try:
                if page.locator(stop_sel).first.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def _stop_generation(self, page) -> None:
        for stop_sel in GEMINI_STOP_SELECTORS:
            try:
                stop_btn = page.locator(stop_sel).first
                if stop_btn.is_visible():
                    stop_btn.click(timeout=2000)
                    return
            except Exception:  # noqa: BLE001
                pass

    def _has_action_bar(self, page, target_bubble) -> bool:
        """Check if Gemini response action buttons (Copy, Good response, Share) are visible."""
        if target_bubble is not None and hasattr(target_bubble, "locator"):
            for sel in GEMINI_ACTION_SELECTORS[:2]:
                try:
                    btn = target_bubble.locator(sel).first
                    if btn.is_visible():
                        return True
                except Exception:  # noqa: BLE001
                    pass
                try:
                    btn = target_bubble.locator(
                        f"xpath=ancestor-or-self::model-response//{sel.replace('button', '')}"
                    ).first
                    if btn.is_visible():
                        return True
                except Exception:  # noqa: BLE001
                    pass
        if hasattr(page, "locator"):
            for sel in GEMINI_ACTION_SELECTORS[:2]:
                try:
                    btn = page.locator(sel).last
                    if btn.is_visible():
                        return True
                except Exception:  # noqa: BLE001
                    pass
        return False

    def _send_message(self, prompt: str, timeout_seconds: int, status_callback, cancel_check) -> str:
        page = self.connect()
        input_box = self.find_input()
        self._check_cancel("before the prompt was sent")

        prior_bubbles = self._bubble_locator(page)
        prior_count = prior_bubbles.count()
        prior_last_text = ""
        if prior_count > 0:
            try:
                prior_last_text = prior_bubbles.nth(prior_count - 1).inner_text().strip()
            except Exception:  # noqa: BLE001
                pass

        _dismiss_gemini_popups(page)
        input_box.click()
        time.sleep(0.2)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        time.sleep(0.2)

        # Enter text into Quill editor; insert_text + type final character ensures Quill's input event fires
        try:
            input_box.fill(prompt)
        except Exception:  # noqa: BLE001
            try:
                if len(prompt) > 1:
                    page.keyboard.insert_text(prompt[:-1])
                    page.keyboard.type(prompt[-1])
                else:
                    page.keyboard.insert_text(prompt)
            except Exception:  # noqa: BLE001
                input_box.type(prompt)
        time.sleep(0.4)
        self._check_cancel("before the prompt was sent")

        submitted = False
        for _ in range(5):
            for s_selector in GEMINI_SUBMIT_SELECTORS:
                try:
                    btn = page.locator(s_selector).first
                    if btn.is_visible() and not btn.is_disabled():
                        btn.click()
                        submitted = True
                        break
                except Exception:  # noqa: BLE001
                    pass
            if submitted:
                break
            time.sleep(0.3)

        if not submitted:
            page.keyboard.press("Enter")

        for _ in range(5):
            time.sleep(0.4)
            try:
                current_draft = input_box.inner_text().strip()
            except Exception:  # noqa: BLE001
                current_draft = ""
            if not current_draft:
                break
            try:
                send_btn = page.locator(", ".join(GEMINI_SUBMIT_SELECTORS[:3])).first
                if send_btn.is_visible() and not send_btn.is_disabled():
                    send_btn.click()
                else:
                    page.keyboard.press("Enter")
            except Exception:  # noqa: BLE001
                pass

        if status_callback:
            status_callback("Prompt submitted in browser, waiting for Gemini generation...")

        start_time = time.monotonic()
        saw_stop_button = False
        last_seen_text = ""
        stable_count = 0

        while time.monotonic() - start_time < timeout_seconds:
            time.sleep(1.0)
            if cancel_check and cancel_check():
                self._stop_generation(page)
                raise web._cancelled_exc()("cancelled while Gemini was generating")

            generating = self._is_generating(page)
            if generating:
                saw_stop_button = True
                if status_callback:
                    status_callback("agent-neo is working...")

            current_bubbles = self._bubble_locator(page)
            current_count = current_bubbles.count()

            target_bubble = None
            if current_count > prior_count:
                target_bubble = current_bubbles.nth(current_count - 1)
            elif current_count > 0:
                cand = current_bubbles.nth(current_count - 1)
                try:
                    if cand.inner_text().strip() != prior_last_text:
                        target_bubble = cand
                except Exception:  # noqa: BLE001
                    pass

            if target_bubble is None:
                continue

            raw_text = ""
            try:
                raw_text = target_bubble.inner_text().strip()
            except Exception:  # noqa: BLE001
                pass

            cleaned_text = clean_gemini_web_text(raw_text)
            if not cleaned_text:
                continue

            has_action = self._has_action_bar(page, target_bubble)

            is_valid_json = False
            try:
                import neo_core
                neo_core.parse_action(cleaned_text)
                is_valid_json = True
            except Exception:
                pass

            if not generating and len(cleaned_text) > 0:
                # If action bar appeared and JSON is already valid/complete, generation & DOM are 100% complete
                if has_action and is_valid_json:
                    if status_callback:
                        status_callback("Generation complete (Gemini action bar visible & JSON valid).")
                    self._clear_composer(page, input_box)
                    return cleaned_text

                if cleaned_text == last_seen_text:
                    stable_count += 1
                    # With action bar: 1 stable cycle (1s) settles any trailing token;
                    # Without action bar: require 2 cycles for valid JSON, 3 cycles otherwise
                    required_stable = 1 if has_action else (2 if is_valid_json else 3)
                    if stable_count >= required_stable:
                        if status_callback:
                            status_callback("Generation complete (Gemini response stabilized).")
                        self._clear_composer(page, input_box)
                        return cleaned_text
                else:
                    last_seen_text = cleaned_text
                    stable_count = 0
            else:
                last_seen_text = cleaned_text
                stable_count = 0

        if last_seen_text:
            self._clear_composer(page, input_box)
            return last_seen_text

        raise TimeoutError(f"Gemini web app did not produce a response within {timeout_seconds}s.")
