"""custom_driver.py: drive ANY web chat page as agent-neo's model (plan v0.5 D5, backend = "custom").

It reuses the web backend's Edge handling (agent-neo's own profile and CDP port, off-screen window, sign-in
window shown only when needed, Stop) and replaces the provider-specific parts: the page URL, how the composer,
the Send control and the reply bubbles are found. Each can be pinned with a CSS selector in the HOME
neo.toml ([custom_backend] input_selector / send_selector / bubble_selector); otherwise heuristics.

Before the first task, ``preflight()`` runs the pre-harness checklist and reports each step as a status line:
    1. connect to the URL   2. not stuck on a sign-in page   3. input located   4. send control located
    5. canary: send "neo canary test" and see a reply bubble appear   -> "[pre-harness] Ready for tasks."
"""
from __future__ import annotations

import time
from typing import Any, Callable
from urllib.parse import urlsplit

import copilot_web as web

CANARY_PROMPT = "neo canary test"
INPUT_HEURISTICS = [
    "textarea:visible",
    '[contenteditable="true"]:visible',
    '[role="textbox"]:visible',
    'input[type="text"]:visible',
]
SEND_HEURISTICS = [
    'button[aria-label*="Send" i]',
    'button[title*="Send" i]',
    'button:has-text("Send")',
    'button[type="submit"]',
    '[data-testid*="send" i]',
]
BUBBLE_HEURISTICS = [
    '[data-message-author-role="assistant"]',
    '[data-role="assistant"]',
    '[class*="assistant" i]',
    '[class*="bot" i][class*="message" i]',
    '[class*="message" i][class*="response" i]',
    '[role="article"]',
    '[class*="message" i]',
]
STOP_HEURISTICS = [
    'button[aria-label*="Stop" i]',
    'button:has-text("Stop")',
]
LOGIN_HINTS = ("login.microsoftonline.com", "login.live.com", "login.microsoft.com", "accounts.google.com",
               "/oauth2/", "/saml", "/sso", "/signin", "/sign-in", "/sign_in", "/login", "okta.com", "pingone.com", "auth0.com")


def is_login_url(url: str, target: str = "") -> bool:
    """True when url looks like an SSO / sign-in page rather than the target chat page."""
    u = (url or "").lower()
    if target and same_page(url, target):
        return False
    return any(h in u for h in LOGIN_HINTS)


def same_page(url: str, target: str) -> bool:
    """url is on the target chat page: same host, and the path starts with the target's path."""
    a, b = urlsplit(url or ""), urlsplit(target or "")
    if not a.hostname or a.hostname != b.hostname:
        return False
    tp = b.path.rstrip("/")
    return a.path.rstrip("/").startswith(tp) if tp else True


class CustomWebCopilotDriver(web.WebCopilotDriver):
    """A generic web-chat driver with selector overrides and a pre-harness checklist."""

    backend_name = "custom"

    def __init__(self, url: str, *, input_selector: str = "", send_selector: str = "", bubble_selector: str = "",
                 notify: Callable[[str], None] | None = None, **kwargs: Any) -> None:
        if not url or urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("custom backend needs custom_url (an http(s) URL): neo.toml [custom_backend] url "
                             "or --custom-url")
        super().__init__(url=url, notify=notify, **kwargs)
        self.input_selector = (input_selector or "").strip()
        self.send_selector = (send_selector or "").strip()
        self.bubble_selector = (bubble_selector or "").strip()
        self.preflight_done = False
        self.checklist: list[tuple[str, bool, str]] = []

    # ---- URL hooks (see WebCopilotDriver._ready_url)
    def _ready_url(self, url: str) -> bool:
        return same_page(url, self.url) and not is_login_url(url)

    def _own_page_url(self, url: str) -> bool:
        return urlsplit(url or "").hostname == urlsplit(self.url).hostname

    def _auth_domains(self) -> tuple[str, ...]:
        host = (urlsplit(self.url).hostname or "").strip().lower()
        return (host,) if host else ()

    def _say(self, text: str) -> None:
        if self.notify:
            try:
                self.notify(text)
                return
            except Exception:  # noqa: BLE001
                pass
        print(f"  [custom backend] {text}")

    def open_child(self):
        # Generic pages have no reliable second-tab story: same as desktop, a new conversation (parent context lost).
        self.new_conversation()
        return self, False

    def close_child(self, child) -> None:
        if child is not self:
            child.close()

    # ---- element discovery
    def _first_visible(self, page, selectors: list[str]):
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    return loc, sel
            except Exception:  # noqa: BLE001 - a bad selector must not stop the search
                pass
        return None, ""

    def _input_selectors(self) -> list[str]:
        return [self.input_selector] if self.input_selector else INPUT_HEURISTICS

    def _send_selectors(self) -> list[str]:
        return [self.send_selector] if self.send_selector else SEND_HEURISTICS

    def _bubble_selectors(self) -> list[str]:
        return [self.bubble_selector] if self.bubble_selector else BUBBLE_HEURISTICS

    def find_input(self, timeout_ms: int = 45000):
        page = self.connect()
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            loc, _sel = self._first_visible(page, self._input_selectors())
            if loc is not None:
                return loc
            if time.monotonic() >= deadline:
                raise RuntimeError("custom backend: no chat input found on the page"
                                   + (f" (input_selector {self.input_selector!r})" if self.input_selector else
                                      "; set [custom_backend] input_selector in neo.toml"))
            self._check_cancel("while looking for the chat input")
            time.sleep(0.5)

    def find_send(self, page):
        loc, _sel = self._first_visible(page, self._send_selectors())
        return loc

    def bubble_count(self, page) -> tuple[int, str]:
        """(count, selector) for the first reply-bubble selector that matches anything."""
        for sel in self._bubble_selectors():
            try:
                n = page.locator(sel).count()
            except Exception:  # noqa: BLE001
                continue
            if n:
                return n, sel
        return 0, (self.bubble_selector or "")

    def new_conversation(self) -> None:
        page = self.connect()
        page.goto(self.url, wait_until="domcontentloaded")
        time.sleep(1.0)

    @staticmethod
    def _input_text(box) -> str:
        for get in (lambda: box.input_value(), lambda: box.inner_text()):
            try:
                return (get() or "").strip()
            except Exception:  # noqa: BLE001
                continue
        return ""

    def _submit(self, page, box, prompt: str) -> None:
        box.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        try:
            box.fill(prompt)
        except Exception:  # noqa: BLE001
            try:
                page.keyboard.insert_text(prompt)
            except Exception:  # noqa: BLE001
                box.type(prompt)
        self._check_cancel("before the prompt was sent")
        btn = self.find_send(page)
        if btn is not None and not btn.is_disabled():
            btn.click()
        else:
            box.press("Enter")
        for _ in range(3):   # same resilience as D3: the composer must empty
            time.sleep(0.4)
            if not self._input_text(box):
                return
            btn = self.find_send(page)
            if btn is not None and not btn.is_disabled():
                btn.click()
            else:
                box.press("Enter")
        if self._input_text(box):
            self._say("Prompt still in the chat input box; please click Send or press Enter in the custom chat page.")

    def _wait_reply(self, page, prior: int, timeout_seconds: float, status_callback=None, cancel_check=None,
                    stable_needed: int = 3) -> str:
        start, last, stable = time.monotonic(), "", 0
        while time.monotonic() - start < timeout_seconds:
            time.sleep(0.8)
            if cancel_check and cancel_check():
                loc, _ = self._first_visible(page, STOP_HEURISTICS)
                if loc is not None:
                    try:
                        loc.click(timeout=2000)
                    except Exception:  # noqa: BLE001
                        pass
                raise web._cancelled_exc()("cancelled while the custom chat was generating")
            n, sel = self.bubble_count(page)
            if n <= prior or not sel:
                continue
            try:
                text = page.locator(sel).nth(n - 1).inner_text().strip()
            except Exception:  # noqa: BLE001
                continue
            busy = self._first_visible(page, STOP_HEURISTICS)[0] is not None
            if text and text == last and not busy:
                stable += 1
                if stable >= stable_needed:
                    if status_callback:
                        status_callback("Generation complete (custom backend, text stabilized).")
                    return text
            else:
                last, stable = text, 0
        if last:
            return last
        raise TimeoutError(f"the custom chat page did not produce a reply within {timeout_seconds:.0f}s")

    def _send_message(self, prompt: str, timeout_seconds: int, status_callback, cancel_check) -> str:
        if not self.preflight_done:
            self.preflight(status_callback=status_callback)
        page = self.connect()
        box = self.find_input()
        self._check_cancel("before the prompt was sent")
        prior, _sel = self.bubble_count(page)
        self._submit(page, box, prompt)
        if status_callback:
            status_callback("Prompt submitted to the custom chat page, waiting for the reply...")
        return self._wait_reply(page, prior, timeout_seconds, status_callback, cancel_check)

    # ---- the pre-harness checklist (plan v0.5 D5)
    def preflight(self, status_callback: Callable[[str], None] | None = None, canary: bool = True,
                  canary_timeout: float = 90) -> list[tuple[str, bool, str]]:
        """Run the checklist; raise RuntimeError naming the first failed step. Returns [(step, ok, detail)]."""
        self.checklist = []

        def report(step: str, ok: bool, detail: str = "") -> None:
            self.checklist.append((step, ok, detail))
            line = f"[pre-harness] {step}: {'OK' if ok else 'FAILED'}" + (f" ({detail})" if detail else "")
            (status_callback or self._say)(line)
            if not ok:
                raise RuntimeError(line)

        try:
            page = self.connect()
        except Exception as exc:  # noqa: BLE001
            report("Connected to URL", False, f"{self.url}: {type(exc).__name__}: {exc}")
        report("Connected to URL", True, self.url)
        url = getattr(page, "url", "")
        report("Signed in (not on a login page)", not is_login_url(url, self.url), url)
        try:
            box = self.find_input(timeout_ms=20000)
        except Exception as exc:  # noqa: BLE001
            report("Input located", False, str(exc))
        report("Input located", True)
        btn = self.find_send(page)
        report("Send control located", True, "button" if btn is not None else "none found; Enter will be used")
        if canary:
            prior, _sel = self.bubble_count(page)
            self._submit(page, box, CANARY_PROMPT)
            try:
                reply = self._wait_reply(page, prior, canary_timeout, None, self._cancel_check, stable_needed=2)
            except TimeoutError:
                report("Canary reply received", False, "no new reply bubble; set [custom_backend] bubble_selector")
            report("Canary reply received", bool(reply), reply[:60].replace("\n", " "))
            try:   # start the real task in a fresh conversation, not after the canary
                self.new_conversation()
            except Exception:  # noqa: BLE001
                pass
        self.preflight_done = True
        (status_callback or self._say)("[pre-harness] Ready for tasks.")
        return self.checklist
