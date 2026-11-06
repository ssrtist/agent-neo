"""whatsapp_web.py — Web app automation for WhatsApp Web (https://web.whatsapp.com/send/?phone=...).

Automates 1:1 WhatsApp LLM chat via Playwright over Chrome DevTools Protocol (CDP)
using agent-neo's dedicated Microsoft Edge profile (%LOCALAPPDATA%\\agent-neo-edge on port 9365).
Supports offscreen, visible, and headless window modes, automatic QR code sign-in window elevation
when device linking is required, contact typing detection, message stabilization, and multi-tab sub-agents.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, Any

import copilot_web as web
import neo_edge as edge

WHATSAPP_PHONE = "19175123390"
WHATSAPP_WEB_BASE = "https://web.whatsapp.com"
WHATSAPP_WEB_URL = f"https://web.whatsapp.com/send/?phone={WHATSAPP_PHONE}"

WHATSAPP_QR_SELECTORS = [
    'canvas[aria-label*="Scan" i]',
    'div[data-ref]',
    'div[data-testid="qrcode"]',
    '[data-testid="link-device-with-phone-number-code"]',
]

WHATSAPP_INPUT_SELECTORS = [
    '#main footer div[contenteditable="true"][role="textbox"]',
    '#main div[data-testid="conversation-compose-box-input"]',
    '#main footer div[contenteditable="true"]',
    '#main footer p[contenteditable="true"]',
    '#main footer [contenteditable="true"]',
    '#main div[title*="Type a message" i][contenteditable="true"]',
    '#main div[data-tab="10"][contenteditable="true"]',
    'footer div[contenteditable="true"][role="textbox"]',
    'div[data-testid="conversation-compose-box-input"]',
    'footer div[contenteditable="true"]',
    'footer p[contenteditable="true"]',
    'footer [contenteditable="true"]',
]

WHATSAPP_SUBMIT_SELECTORS = [
    '#main button[data-testid="send"]',
    '#main span[data-testid="send"]',
    '#main button[aria-label*="Send" i]',
    '#main span[data-icon="send"]',
    'button[data-testid="send"]',
    'span[data-testid="send"]',
    'button[aria-label*="Send" i]',
    'span[data-icon="send"]',
]

WHATSAPP_INCOMING_SELECTORS = [
    '#main div[data-testid="msg-container"]:has([data-testid="tail-in"], [data-icon="tail-in"])',
    '#main div[data-testid="msg-container"]:not(:has([data-testid="tail-out"], [data-icon="tail-out"], [data-icon*="check"], [data-testid*="check"]))',
    '#main div.message-in',
    '#main [class*="message-in"]',
    'div[data-testid="msg-container"]:has([data-testid="tail-in"], [data-icon="tail-in"])',
    'div.message-in',
    '[class*="message-in"]',
]

WHATSAPP_MAIN_SELECTORS = [
    '#main',
    'div[data-testid="conversation-panel-wrapper"]',
]

WHATSAPP_TYPING_SELECTORS = [
    '#main header span[title*="typing" i]',
    '#main header span:has-text("typing...")',
    'span[title*="typing" i]',
    'span:has-text("typing...")',
]

WHATSAPP_HEADER_SELECTORS = [
    '#main header [data-testid="conversation-info-header-chat-title"]',
    '#main header span[title]',
    '#main header div[role="button"] span',
    '#main header',
]

WHATSAPP_START_CHAT_SELECTORS = [
    'a#action-button',
    'a:has-text("use WhatsApp Web")',
    'button:has-text("Continue to chat")',
]


def _is_whatsapp_url(url: str) -> bool:
    u = (url or "").lower()
    return "web.whatsapp.com" in u or "wa.me" in u


ACTION_TYPES = ("tool", "tools", "final", "ask_user", "step")


def is_dummy_template_example(obj: Any) -> bool:
    """Detect documentation dummy tool examples from BASE_SYSTEM_INSTRUCTIONS."""
    if not isinstance(obj, dict):
        return False
    args = obj.get("arguments")
    if isinstance(args, dict):
        if args.get("name") == "skill-name":
            return True
        if args.get("path") in ("relative/path.txt", "relative/path"):
            return True
        if args.get("command") == "command text":
            return True
    if obj.get("type") == "final" and obj.get("message") == "answer to the user":
        return True
    return False


def clean_whatsapp_web_text(raw_text: str) -> str:
    """Clean WhatsApp UI timestamps, directional marks, and strip out JUST the action JSON."""
    if not raw_text:
        return ""

    text = (raw_text or "").strip()
    # Remove LTR/RTL and zero-width formatting marks
    text = re.sub(r"[\u200e\u200f\u200b\u202a-\u202e]", "", text)
    # Strip "WhatsApp said:" preamble if present
    text = re.sub(r"^WhatsApp said\s*:?\s*", "", text, flags=re.IGNORECASE)

    # Strip trailing timestamps like "10:15 AM", "10:15 AM", "10:15", "Edited 10:15 AM", and checkmarks
    text = re.sub(
        r"(?:\n|\s)*(?:Edited\s+)?\b\d{1,2}:\d{2}(?:[\s\u202f]*(?:AM|PM|am|pm))?\s*(?:✓|✔)?\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    if "{" not in text:
        return text

    # 1. First check markdown code fences ```json ... ```
    for m in re.finditer(r"```(?:json|JSON)?[ \t]*\r?\n?(.*?)\r?\n?```", text, re.DOTALL):
        fence_content = m.group(1).strip()
        decoder = json.JSONDecoder()
        idx = fence_content.find("{")
        while idx >= 0:
            try:
                obj, end = decoder.raw_decode(fence_content, idx)
                if isinstance(obj, dict) and obj.get("type") in ACTION_TYPES and not is_dummy_template_example(obj):
                    return fence_content[idx:end].strip()
                idx = fence_content.find("{", end)
            except ValueError:
                idx = fence_content.find("{", idx + 1)

    # 2. Scan balanced objects in the full text
    decoder = json.JSONDecoder()
    candidates = []
    idx = text.find("{")
    while idx >= 0:
        try:
            obj, end = decoder.raw_decode(text, idx)
            if isinstance(obj, dict) and obj.get("type") in ACTION_TYPES:
                candidates.append((idx, end, obj))
            idx = text.find("{", end)
        except ValueError:
            idx = text.find("{", idx + 1)

    real_candidates = [(start, end, obj) for start, end, obj in candidates if not is_dummy_template_example(obj)]

    if real_candidates:
        start, end, _ = real_candidates[-1]
        return text[start:end].strip()

    if candidates:
        start, end, _ = candidates[-1]
        return text[start:end].strip()

    return text


class WhatsAppWebDriver(web.WebCopilotDriver):
    """Automation driver for WhatsApp Web LLM 1:1 chat over CDP."""

    backend_name = "whatsapp"

    def __init__(
        self,
        port: int = edge.DEFAULT_CDP_PORT,
        phone: str = WHATSAPP_PHONE,
        contact: str | None = None,
        url: str | None = None,
        headless: bool = False,
        force_fresh: bool = True,
        browser_mode: str = "offscreen",
        notify: Callable[[str], None] | None = None,
        profile_dir: str | None = None,
    ):
        self.phone = phone or WHATSAPP_PHONE
        self.contact = contact or ""
        self._active_chat_label: str | None = None
        target_url = url or f"https://web.whatsapp.com/send/?phone={self.phone}"
        super().__init__(
            port=port,
            url=target_url,
            headless=headless,
            force_fresh=force_fresh,
            browser_mode=browser_mode,
            notify=notify,
            profile_dir=profile_dir,
        )

    def _ready_url(self, url: str) -> bool:
        return _is_whatsapp_url(url)

    def _own_page_url(self, url: str) -> bool:
        u = (url or "").lower()
        return "whatsapp.com" in u or "wa.me" in u

    def _say(self, text: str) -> None:
        if self.notify:
            try:
                self.notify(text)
                return
            except Exception:  # noqa: BLE001
                pass
        print(f"  [whatsapp backend] {text}")

    def _is_qr_page(self, page) -> bool:
        for sel in WHATSAPP_QR_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def _get_header_title(self, page) -> str:
        """Extract the display text / title of the open chat in #main header."""
        for sel in WHATSAPP_HEADER_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    txt = (loc.inner_text() or "").strip()
                    title = (loc.get_attribute("title") or "").strip()
                    cand = title if title else txt
                    if cand:
                        return cand
            except Exception:
                continue
        return ""

    def _is_active_chat(self, page) -> bool:
        """Verify that #main is open and belongs to the intended phone number or contact."""
        try:
            main = page.locator("#main").first
            if not main.is_visible():
                return False

            # 1. Check header title against digits, contact name, or learned label
            header_title = self._get_header_title(page)
            if header_title:
                target_digits = re.sub(r"\D", "", self.phone)
                header_digits = re.sub(r"\D", "", header_title)

                # Match on last 10 phone digits (e.g. 9175123390)
                if target_digits and target_digits[-10:] in header_digits:
                    return True

                # Match on configured contact name (e.g. "Chibo")
                if self.contact and self.contact.lower() in header_title.lower():
                    return True

                # Match on dynamically learned chat label
                if self._active_chat_label and self._active_chat_label.lower() in header_title.lower():
                    return True

            # 2. Check DOM message container attributes for WhatsApp JID (e.g. 19175123390@c.us)
            target_short = self.phone[-10:] if len(self.phone) >= 10 else self.phone
            try:
                if page.locator(f'#main [data-id*="{target_short}"]').count() > 0:
                    return True
                if page.locator(f'#main [data-pre-plain-text*="{target_short}"]').count() > 0:
                    return True
            except Exception:
                pass

            # 3. Check current page URL
            curr_url = page.url or ""
            if f"phone={self.phone}" in curr_url:
                if header_title and not self._active_chat_label:
                    self._active_chat_label = header_title
                return True
        except Exception:
            pass
        return False

    def _ensure_active_chat(self, page, timeout_ms: int = 15000) -> bool:
        """Ensure #main is focused on the intended conversation, re-routing to it if needed."""
        if self._is_active_chat(page):
            return True

        self._say(f"Active chat is not {self.phone} ({self.contact or 'unlabeled'}); routing to target chat...")
        try:
            page.goto(self.url, wait_until="domcontentloaded")
        except Exception:
            pass

        start = time.monotonic()
        while (time.monotonic() - start) * 1000 < timeout_ms:
            self._check_cancel("while routing to target WhatsApp chat")
            # Click any intermediate 'Continue to chat' buttons
            for sel in WHATSAPP_START_CHAT_SELECTORS:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible():
                        btn.click()
                        time.sleep(0.8)
                except Exception:
                    pass

            # Check if #main has appeared and dynamically learn the active contact label
            try:
                if page.locator("#main").first.is_visible():
                    title = self._get_header_title(page)
                    if title:
                        self._active_chat_label = title
                        self._say(f"Locked active WhatsApp chat label: '{title}' ({self.phone})")
                        return True
            except Exception:
                pass

            if self._is_active_chat(page):
                return True
            time.sleep(0.5)

        # Fallback: search phone number or contact in WhatsApp sidebar search box
        search_terms = [self.phone]
        if self.contact and self.contact not in search_terms:
            search_terms.append(self.contact)
        for term in search_terms:
            try:
                search_box = page.locator('#side div[contenteditable="true"]').first
                if search_box.is_visible():
                    search_box.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    search_box.fill(term)
                    time.sleep(1.0)
                    result = page.locator(f'#side [role="listitem"]:has-text("{term[-10:]}")').first
                    if not result.is_visible():
                        result = page.locator(f'#side [role="listitem"]:has-text("{term}")').first
                    if result.is_visible():
                        result.click()
                        time.sleep(1.0)
                        title = self._get_header_title(page)
                        if title:
                            self._active_chat_label = title
                        if self._is_active_chat(page):
                            return True
            except Exception:
                pass

        return self._is_active_chat(page)

    def _is_chat_ready(self, page) -> bool:
        for sel in WHATSAPP_MAIN_SELECTORS + WHATSAPP_INPUT_SELECTORS[:2]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def _is_contact_typing(self, page) -> bool:
        for sel in WHATSAPP_TYPING_SELECTORS:
            try:
                if page.locator(sel).first.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def _wait_for_sign_in(self, page, timeout_seconds: int) -> None:
        """Handle WhatsApp Web loading and QR code device linking."""
        start_wait = time.monotonic()
        qr_prompted = False

        while time.monotonic() - start_wait < timeout_seconds:
            self._check_cancel("while waiting for WhatsApp Web")

            # Check if intermediate "Continue to chat" button is present
            for sel in WHATSAPP_START_CHAT_SELECTORS:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible():
                        btn.click()
                        time.sleep(1.0)
                        break
                except Exception:  # noqa: BLE001
                    pass

            # Check if chat is already ready
            if self._is_chat_ready(page):
                if qr_prompted and self.browser_mode == "offscreen" and self._launched:
                    self._set_window(page, onscreen=False)
                    self._say("WhatsApp sign-in complete. Moving Edge window off-screen.")
                return

            # Check if QR code is showing
            if self._is_qr_page(page):
                if not qr_prompted:
                    qr_prompted = True
                    if self.browser_mode == "offscreen" and self._launched:
                        self._set_window(page, onscreen=True)
                    self._say("WhatsApp sign-in needed: scan the QR code in the Edge window with your phone.")

            time.sleep(1.0)

        if not self._is_chat_ready(page):
            raise TimeoutError(
                f"WhatsApp Web did not reach ready state within {timeout_seconds}s. "
                "Ensure your phone is linked to WhatsApp Web."
            )

    def open_child(self):
        self.connect()
        child = WhatsAppWebDriver(
            port=self.port,
            phone=self.phone,
            contact=self.contact,
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
        """Locate the WhatsApp Web chat input with retry, verifying target chat is active."""
        page = self.connect()
        self._ensure_active_chat(page)
        start = time.monotonic()
        while (time.monotonic() - start) * 1000 < timeout_ms:
            self._check_cancel("while looking for the WhatsApp chat input")

            # Click any "Continue to chat" or "Starting chat" if stuck
            for sel in WHATSAPP_START_CHAT_SELECTORS:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible():
                        btn.click()
                        time.sleep(1.0)
                except Exception:  # noqa: BLE001
                    pass

            for selector in WHATSAPP_INPUT_SELECTORS:
                try:
                    loc = page.locator(selector).first
                    if loc.is_visible():
                        return loc
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.6)

        combined = ", ".join(WHATSAPP_INPUT_SELECTORS[:2])
        page.wait_for_selector(combined, timeout=timeout_ms)
        return page.locator(combined).first

    def _clear_composer(self, page, input_box=None) -> None:
        """Ensure the WhatsApp composer input is completely cleared."""
        try:
            if input_box is None:
                input_box = self.find_input(timeout_ms=2000)
            val = ""
            try:
                val = input_box.inner_text().strip()
            except Exception:  # noqa: BLE001
                try:
                    val = input_box.input_value().strip()
                except Exception:  # noqa: BLE001
                    pass
            if val:
                input_box.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                time.sleep(0.1)
        except Exception:  # noqa: BLE001
            pass

    def new_conversation(self) -> None:
        """Start or refresh the WhatsApp conversation with recipient lock."""
        page = self.connect()
        self._ensure_active_chat(page)
        self._clear_composer(page)

    def _extract_bubble_text(self, bubble) -> str:
        """Extract message text from a message bubble, avoiding timestamp footer and quoted messages."""
        try:
            js_extract = """(node) => {
                const clone = node.cloneNode(true);
                const quoted = clone.querySelectorAll('[data-testid="quoted-message"], [class*="quoted"], [aria-label*="Quoted" i]');
                quoted.forEach(el => el.remove());
                const footers = clone.querySelectorAll('div[data-testid="msg-meta"], span[data-testid="msg-meta"], [class*="copyable-text-inner-footer"]');
                footers.forEach(el => el.remove());
                const textEl = clone.querySelector('span.selectable-text, .copyable-text') || clone;
                return textEl.innerText.trim();
            }"""
            txt = bubble.evaluate(js_extract)
            if txt:
                return txt
        except Exception:  # noqa: BLE001
            pass
        try:
            selectable = bubble.locator("span.selectable-text, .copyable-text").first
            if selectable.is_visible():
                text = selectable.inner_text().strip()
                if text:
                    return text
        except Exception:  # noqa: BLE001
            pass
        try:
            return bubble.inner_text().strip()
        except Exception:  # noqa: BLE001
            return ""

    def _send_message(self, prompt: str, timeout_seconds: int, status_callback, cancel_check) -> str:
        from neo_core import parse_action

        page = self.connect()
        self._ensure_active_chat(page)
        input_box = self.find_input()
        self._check_cancel("before the prompt was sent")

        incoming_loc = page.locator(", ".join(WHATSAPP_INCOMING_SELECTORS))
        prior_count = incoming_loc.count()
        prior_last_text = ""
        if prior_count > 0:
            try:
                prior_last_text = self._extract_bubble_text(incoming_loc.nth(prior_count - 1))
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

        # Submit via Send button or Enter
        submitted = False
        for s_selector in WHATSAPP_SUBMIT_SELECTORS:
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

        # Ensure draft was submitted
        for _ in range(5):
            time.sleep(0.4)
            draft = ""
            try:
                draft = input_box.inner_text().strip()
            except Exception:  # noqa: BLE001
                try:
                    draft = input_box.input_value().strip()
                except Exception:  # noqa: BLE001
                    pass
            if not draft:
                break
            try:
                page.keyboard.press("Enter")
            except Exception:  # noqa: BLE001
                pass

        if status_callback:
            status_callback("Prompt submitted in WhatsApp, waiting for LLM reply...")

        start_time = time.monotonic()
        last_seen_text = ""
        stable_count = 0

        while time.monotonic() - start_time < timeout_seconds:
            time.sleep(1.0)
            if cancel_check and cancel_check():
                raise web._cancelled_exc()("cancelled while waiting for WhatsApp reply")

            # Verify active chat invariant during polling
            if not self._is_active_chat(page):
                self._ensure_active_chat(page, timeout_ms=5000)

            is_typing = self._is_contact_typing(page)
            if is_typing and status_callback:
                status_callback("agent-neo is working (contact is typing)...")

            current_bubbles = page.locator(", ".join(WHATSAPP_INCOMING_SELECTORS))
            current_count = current_bubbles.count()

            new_text_parts = []
            if current_count > prior_count:
                for i in range(prior_count, current_count):
                    try:
                        part = self._extract_bubble_text(current_bubbles.nth(i))
                        if part:
                            new_text_parts.append(part)
                    except Exception:  # noqa: BLE001
                        pass
            elif current_count > 0:
                cand_text = self._extract_bubble_text(current_bubbles.nth(current_count - 1))
                if cand_text and cand_text != prior_last_text:
                    new_text_parts.append(cand_text)

            if not new_text_parts:
                continue

            raw_combined = "\n\n".join(new_text_parts)
            cleaned = clean_whatsapp_web_text(raw_combined)
            if not cleaned:
                continue

            # Check if this is a complete, valid JSON action
            parsed = parse_action(cleaned)
            if parsed and parsed.get("type") in ("tool", "final", "ask_user", "step"):
                if status_callback:
                    status_callback("WhatsApp action received.")
                self._clear_composer(page, input_box)
                return cleaned

            # Plain text stabilization check
            if cleaned == last_seen_text and not is_typing:
                stable_count += 1
                if stable_count >= 2:
                    if status_callback:
                        status_callback("WhatsApp reply received and stabilized.")
                    self._clear_composer(page, input_box)
                    return cleaned
            else:
                last_seen_text = cleaned
                stable_count = 0

        if last_seen_text:
            self._clear_composer(page, input_box)
            return last_seen_text

        raise TimeoutError(f"WhatsApp LLM did not produce a response within {timeout_seconds}s.")
