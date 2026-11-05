"""deepseek_web.py — Web app automation for DeepSeek Chat (https://chat.deepseek.com/).

Automates DeepSeek web chat via Playwright over Chrome DevTools Protocol (CDP)
using agent-neo's dedicated Microsoft Edge profile (%LOCALAPPDATA%\\agent-neo-edge on port 9365).
Supports offscreen, visible, and headless window modes, automatic sign-in window elevation
when redirected to chat.deepseek.com/sign_in or accounts.google.com, cooperative Stop
cancellation, and multi-tab sub-agents. Leaves DeepSeek's UI toggles (DeepThink R1 / Search)
untouched as-is in the browser profile.
"""
from __future__ import annotations

import re
import time
from typing import Callable

import copilot_web as web
import neo_edge as edge

DEEPSEEK_WEB_URL = "https://chat.deepseek.com/"

DEEPSEEK_INPUT_SELECTORS = [
    "textarea#chat-input",
    "#chat-input",
    'textarea[placeholder*="DeepSeek" i]',
    'textarea[placeholder*="Message" i]',
    "textarea",
    'div[contenteditable="true"]',
]

DEEPSEEK_SUBMIT_SELECTORS = [
    'div[role="button"][aria-label*="Send" i]',
    'button[aria-label*="Send" i]',
    'button[data-testid*="send" i]',
    "div.ds-icon-button[role='button']:not([aria-disabled='true'])",
]

DEEPSEEK_STOP_SELECTORS = [
    'div[role="button"][aria-label*="Stop" i]',
    'button[aria-label*="Stop" i]',
    'button:has-text("Stop")',
]

DEEPSEEK_MESSAGE_SELECTORS = [
    "div.ds-markdown",
    ".ds-markdown",
    '[class*="ds-markdown"]',
    '[data-message-author-role="assistant"]',
]

DEEPSEEK_NEW_CHAT_SELECTORS = [
    'div:has-text("New chat")[role="button"]',
    'button:has-text("New chat")',
    'a:has-text("New chat")',
    '[aria-label*="New chat" i]',
]


def _is_deepseek_login_url(url: str) -> bool:
    u = (url or "").lower()
    return (
        "chat.deepseek.com/sign_in" in u
        or "chat.deepseek.com/login" in u
        or "accounts.google.com" in u
    )


def _is_deepseek_url(url: str) -> bool:
    u = (url or "").lower()
    return "chat.deepseek.com" in u and not _is_deepseek_login_url(u)


def clean_deepseek_web_text(raw_text: str) -> str:
    """Clean DeepSeek UI preambles, <think> blocks, and action labels from response text."""
    text = (raw_text or "").strip()
    text = re.sub(r"^DeepSeek said\s*:?\s*", "", text, flags=re.IGNORECASE)
    # Convert <think>...</think> blocks into a single-line preamble before the JSON so parse_action() captures it as 'thought'
    def _collapse_think(m: re.Match) -> str:
        inner = " ".join((m.group(1) or "").split())
        return (inner[:280] + "\n") if inner else ""

    text = re.sub(r"<think>(.*?)</think>\s*", _collapse_think, text, flags=re.DOTALL | re.IGNORECASE)
    # Strip "Thought for X seconds" header if present
    text = re.sub(r"^Thought for \d+\s*(?:seconds?|s)\s*", "", text, flags=re.IGNORECASE)
    # Strip trailing UI action labels (one or more, e.g. "Copy\nRegenerate")
    text = re.sub(
        r"(?:\n\s*(?:Copy|Regenerate|Good response|Bad response|Search)\s*)+$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


class DeepSeekWebDriver(web.WebCopilotDriver):
    """Automation driver for DeepSeek web chat (https://chat.deepseek.com/) over CDP."""

    backend_name = "deepseek"

    def __init__(
        self,
        port: int = edge.DEFAULT_CDP_PORT,
        url: str = DEEPSEEK_WEB_URL,
        headless: bool = False,
        force_fresh: bool = True,
        browser_mode: str = "offscreen",
        notify: Callable[[str], None] | None = None,
        profile_dir: str | None = None,
    ):
        super().__init__(
            port=port,
            url=url or DEEPSEEK_WEB_URL,
            headless=headless,
            force_fresh=force_fresh,
            browser_mode=browser_mode,
            notify=notify,
            profile_dir=profile_dir,
        )

    def _ready_url(self, url: str) -> bool:
        return _is_deepseek_url(url)

    def _own_page_url(self, url: str) -> bool:
        return "deepseek.com" in (url or "").lower()

    def _say(self, text: str) -> None:
        if self.notify:
            try:
                self.notify(text)
                return
            except Exception:  # noqa: BLE001
                pass
        print(f"  [deepseek backend] {text}")

    def open_child(self):
        self.connect()
        child = DeepSeekWebDriver(
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
        """Locate the DeepSeek chat input textarea with retry."""
        page = self.connect()
        start = time.monotonic()
        while (time.monotonic() - start) * 1000 < timeout_ms:
            self._check_cancel("while looking for the DeepSeek chat input")
            for selector in DEEPSEEK_INPUT_SELECTORS:
                try:
                    loc = page.locator(selector).first
                    if loc.is_visible():
                        return loc
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.6)

        combined = ", ".join(DEEPSEEK_INPUT_SELECTORS[:3])
        page.wait_for_selector(combined, timeout=timeout_ms)
        return page.locator(combined).first

    def _clear_composer(self, page, input_box=None) -> None:
        """Ensure the DeepSeek composer input is completely cleared."""
        try:
            if input_box is None:
                input_box = self.find_input(timeout_ms=2000)
            val = ""
            try:
                val = input_box.input_value().strip()
            except Exception:  # noqa: BLE001
                val = input_box.inner_text().strip()
            if val:
                input_box.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                time.sleep(0.1)
        except Exception:  # noqa: BLE001
            pass

    def new_conversation(self) -> None:
        """Start a fresh DeepSeek conversation."""
        page = self.connect()
        clicked = False
        for selector in DEEPSEEK_NEW_CHAT_SELECTORS:
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
        return page.locator(", ".join(DEEPSEEK_MESSAGE_SELECTORS))

    def _is_generating(self, page) -> bool:
        for stop_sel in DEEPSEEK_STOP_SELECTORS:
            try:
                if page.locator(stop_sel).first.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def _stop_generation(self, page) -> None:
        for stop_sel in DEEPSEEK_STOP_SELECTORS:
            try:
                stop_btn = page.locator(stop_sel).first
                if stop_btn.is_visible():
                    stop_btn.click(timeout=2000)
                    return
            except Exception:  # noqa: BLE001
                pass

    def _composer_draft(self, input_box) -> str:
        for getter in (lambda: input_box.input_value(), lambda: input_box.inner_text()):
            try:
                return (getter() or "").strip()
            except Exception:  # noqa: BLE001
                continue
        return ""

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

        input_box.click()
        time.sleep(0.2)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        time.sleep(0.2)

        try:
            input_box.fill(prompt)
        except Exception:  # noqa: BLE001
            try:
                page.keyboard.insert_text(prompt)
            except Exception:  # noqa: BLE001
                input_box.type(prompt)
        time.sleep(0.3)
        self._check_cancel("before the prompt was sent")

        # DeepSeek's textarea submits cleanly on Enter (Shift+Enter adds a newline)
        submitted = False
        for s_selector in DEEPSEEK_SUBMIT_SELECTORS[:3]:
            try:
                btn = page.locator(s_selector).first
                if btn.is_visible() and not btn.is_disabled():
                    btn.click()
                    submitted = True
                    break
            except Exception:  # noqa: BLE001
                pass

        if not submitted:
            page.keyboard.press("Enter")

        for _ in range(5):
            time.sleep(0.4)
            if not self._composer_draft(input_box):
                break
            try:
                page.keyboard.press("Enter")
            except Exception:  # noqa: BLE001
                pass

        if status_callback:
            status_callback("Prompt submitted in browser, waiting for DeepSeek generation...")

        start_time = time.monotonic()
        saw_stop_button = False
        last_seen_text = ""
        stable_count = 0

        while time.monotonic() - start_time < timeout_seconds:
            time.sleep(1.0)
            if cancel_check and cancel_check():
                self._stop_generation(page)
                raise web._cancelled_exc()("cancelled while DeepSeek was generating")

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

            cleaned_text = clean_deepseek_web_text(raw_text)
            if not cleaned_text:
                continue

            if saw_stop_button and not generating and len(cleaned_text) > 0:
                if status_callback:
                    status_callback("Generation complete (DeepSeek Stop button disappeared).")
                self._clear_composer(page, input_box)
                return cleaned_text

            if cleaned_text == last_seen_text and not generating:
                stable_count += 1
                if stable_count >= 3:
                    if status_callback:
                        status_callback("Generation complete (DeepSeek text stabilized).")
                    self._clear_composer(page, input_box)
                    return cleaned_text
            else:
                last_seen_text = cleaned_text
                stable_count = 0

        if last_seen_text:
            self._clear_composer(page, input_box)
            return last_seen_text

        raise TimeoutError(f"DeepSeek web app did not produce a response within {timeout_seconds}s.")
