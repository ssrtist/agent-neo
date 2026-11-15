"""chat_driver.py — Unified driver abstraction for Copilot, Gemini, DeepSeek, WhatsApp & Custom chat engines.

Provides a common interface supporting:
1. 'copilot' — Controls M365 Copilot (https://m365.cloud.microsoft/chat) or Consumer Copilot (https://copilot.microsoft.com) in Edge via Playwright over CDP (also accepts alias 'web').
2. 'gemini'  — Controls Google Gemini (https://gemini.google.com/app) in Edge via Playwright over CDP.
3. 'deepseek'— Controls DeepSeek Web Chat (https://chat.deepseek.com/) in Edge via Playwright over CDP.
4. 'whatsapp'— Controls WhatsApp Web (https://web.whatsapp.com/send/?phone=...) in Edge via Playwright over CDP.
5. 'desktop' — Controls M365Copilot.exe / Copilot.exe via Windows UI Automation (pywinauto).
6. 'auto'    — Automatically detects running desktop app, falling back to Copilot web automation.
7. 'custom'  — Any web chat page (custom_url), with a pre-harness checklist (custom_driver.py).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


class ChatDriver(ABC):
    """Abstract interface for Agent Neo conversation backends."""

    @abstractmethod
    def send_message(
        self,
        prompt: str,
        timeout_seconds: int = 180,
        status_callback: Callable[[str], None] | None = None,
    ) -> str:
        """Send prompt and wait for assistant reply."""
        pass

    @abstractmethod
    def new_conversation(self) -> None:
        """Reset conversation or start a fresh topic."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Clean up resources."""
        pass


CopilotDriver = ChatDriver


def get_driver(backend: str = "auto", new_session_only: bool = False, custom: dict | None = None, **kwargs) -> ChatDriver:
    """Factory to acquire the requested ChatDriver.

    Args:
        backend: 'copilot' (or 'web'), 'gemini', 'deepseek', 'whatsapp', 'desktop', 'auto' or 'custom'.
        custom: for 'custom': url, input_selector, send_selector, bubble_selector.
        new_session_only: If True, reuses existing browser instance and only starts a new
                          chat conversation.
                          If False (default), launches a fresh browser instance.
        port / browser_mode / notify / profile_dir: web backend options (ignored by the desktop backend;
                          a None value means the driver default).
    """
    selected = backend.lower().strip()
    if selected == "web":
        selected = "copilot"
    force_fresh = kwargs.pop("force_fresh", not new_session_only)
    web_kwargs = {k: kwargs.pop(k) for k in ("port", "browser_mode", "notify", "url", "headless", "profile_dir") if k in kwargs}
    web_kwargs = {k: v for k, v in web_kwargs.items() if v is not None}
    wa_kwargs = {k: kwargs.pop(k) for k in ("phone", "contact") if k in kwargs}
    wa_kwargs = {k: v for k, v in wa_kwargs.items() if v is not None}

    if selected == "desktop":
        from copilot_ui import DesktopCopilotDriver
        driver = DesktopCopilotDriver()
        if new_session_only:
            try:
                driver.new_conversation()
            except Exception:
                pass
        return driver

    if selected == "copilot":
        from copilot_web import WebCopilotDriver
        driver = WebCopilotDriver(force_fresh=force_fresh, **web_kwargs)
        if new_session_only:
            try:
                driver.new_conversation()
            except Exception:
                pass
        return driver

    if selected == "gemini":
        from gemini_web import GeminiWebDriver
        driver = GeminiWebDriver(force_fresh=force_fresh, **web_kwargs)
        if new_session_only:
            try:
                driver.new_conversation()
            except Exception:
                pass
        return driver

    if selected == "deepseek":
        from deepseek_web import DeepSeekWebDriver
        driver = DeepSeekWebDriver(force_fresh=force_fresh, **web_kwargs)
        if new_session_only:
            try:
                driver.new_conversation()
            except Exception:
                pass
        return driver

    if selected == "whatsapp":
        from whatsapp_web import WhatsAppWebDriver
        if not getattr(WhatsAppWebDriver, "enabled", False):
            raise RuntimeError("WhatsAppWebDriver is currently disabled.")
        driver = WhatsAppWebDriver(force_fresh=force_fresh, **web_kwargs, **wa_kwargs, **kwargs)
        if new_session_only:
            try:
                driver.new_conversation()
            except Exception:
                pass
        return driver

    if selected == "auto":
        try:
            from copilot_ui import DesktopCopilotDriver, is_copilot_running
            desktop_running = is_copilot_running()
        except Exception:
            desktop_running = False
        if desktop_running:
            print("[chat_driver] Detected running Microsoft Copilot desktop app -> using desktop backend.")
            driver = DesktopCopilotDriver()
            if new_session_only:
                try:
                    driver.new_conversation()
                except Exception:
                    pass
            return driver

        from copilot_web import WebCopilotDriver
        mode_str = "reusing existing browser, new session only" if new_session_only else "fresh browser"
        print(f"[chat_driver] Desktop app not detected; using copilot web backend ({mode_str}).")
        driver = WebCopilotDriver(force_fresh=force_fresh, **web_kwargs)
        if new_session_only:
            try:
                driver.new_conversation()
            except Exception:
                pass
        return driver

    if selected == "custom":
        from custom_driver import CustomWebCopilotDriver
        opts = {k: v for k, v in (custom or {}).items() if v}
        url = opts.pop("url", "")
        web_kwargs.pop("url", None)
        return CustomWebCopilotDriver(url, force_fresh=force_fresh, **opts, **web_kwargs)

    raise ValueError(
        f"Unknown backend {backend!r}. Choose from: 'copilot', 'gemini', 'deepseek', 'whatsapp', 'desktop', 'auto', 'custom'."
    )
