"""copilot_ui.py — UI Automation client for Microsoft 365 Copilot & Windows Copilot desktop apps.

Controls Microsoft 365 Copilot desktop app (M365Copilot.exe / Copilot.exe / WebView2)
via Windows UI Automation (pywinauto backend='uia'):
- Locates and focuses the running 'Microsoft 365 Copilot' or 'Copilot' window
- Pastes multiline prompt text via clipboard into 'Message Copilot' / 'Ask Copilot'
- Submits via Enter key or Send button
- Waits for response generation to complete
- Extracts clean response text (stripping UI preambles and sensitivity headers)
- Supports starting a New Chat
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

try:
    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys
except ImportError:  # pragma: no cover - optional when only web/custom backends are used
    Desktop = None  # type: ignore[assignment]

    def send_keys(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("pywinauto is required for the desktop backend (pip install pywinauto)")

import win32clipboard
import win32con


_EXCLUDED_WINDOW_HINTS = ("edge", "chrome", "firefox", "visual studio", "agent neo", "agent-neo", "m365 chat agent", "agent365")


def _is_consumer_copilot_window_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    low = t.lower()
    if any(ex in low for ex in _EXCLUDED_WINDOW_HINTS):
        return False
    return low == "copilot" or low.startswith("copilot ") or low == "microsoft copilot"


def get_copilot_window():
    """Locate the top-level Microsoft 365 Copilot or Windows Copilot app window."""
    if Desktop is None:
        raise RuntimeError("pywinauto is required for the desktop backend (pip install pywinauto)")
    desktop = Desktop(backend="uia")
    windows = desktop.windows(title_re=r".*Microsoft 365 Copilot.*")
    if windows:
        return windows[0]
    for w in desktop.windows():
        if "Microsoft 365 Copilot" in (w.window_text() or ""):
            return w
    for w in desktop.windows():
        if _is_consumer_copilot_window_title(w.window_text() or ""):
            return w
    raise RuntimeError("Copilot desktop window ('Microsoft 365 Copilot' or 'Copilot') not found. Please open the app first.")


def is_copilot_running() -> bool:
    """Check if Microsoft 365 Copilot or Windows Copilot desktop window is currently open."""
    try:
        get_copilot_window()
        return True
    except Exception:
        return False


def focus_copilot(win=None):
    """Bring the M365 Copilot window to the foreground."""
    if win is None:
        win = get_copilot_window()
    try:
        win.set_focus()
    except Exception:
        pass
    return win


_INPUT_NAMES = ("Message Copilot", "Ask Copilot", "Talk to Copilot", "Ask anything")


def find_message_input(app_window=None):
    """Find the Copilot composer Edit element (Enterprise M365 or Consumer Windows Copilot)."""
    if app_window is None:
        app_window = get_copilot_window()

    descendants = app_window.descendants()
    for d in descendants:
        if d.element_info.control_type == "Edit" and (d.element_info.name or "") in _INPUT_NAMES:
            return d
    for d in descendants:
        nm = d.element_info.name or ""
        if d.element_info.control_type == "Edit" and ("Copilot" in nm or "Ask anything" in nm):
            return d
    raise RuntimeError("Could not find 'Message Copilot' / 'Ask Copilot' edit box in Copilot window.")


def set_clipboard_text(text: str) -> None:
    """Safely put unicode text on the Windows clipboard."""
    for attempt in range(5):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            win32clipboard.CloseClipboard()
            return
        except Exception:
            time.sleep(0.1)
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def _is_copilot_message_element(d: Any) -> bool:
    cls = d.element_info.class_name or ""
    nm = d.element_info.name or ""
    if "fai-CopilotMessage" in cls and len(nm) > 40:
        return True
    if "ai-message" in cls.lower() and len(nm) > 0:
        return True
    if d.element_info.control_type == "Group" and nm.startswith("Copilot said:"):
        return True
    return False


def get_message_elements(app_window=None) -> list[Any]:
    """Retrieve all real Copilot response message bubble groups."""
    if app_window is None:
        app_window = get_copilot_window()
    descendants = app_window.descendants()
    return [d for d in descendants if _is_copilot_message_element(d)]


def clean_copilot_text(raw_text: str) -> str:
    """Clean UI headers, disclaimer labels, and metadata from raw response text."""
    text = raw_text.strip()
    if text.startswith("Copilot said:"):
        text = text[len("Copilot said:"):].strip()

    # Remove reasoning header e.g. "Reasoned for 39s"
    text = re.sub(r"^Reasoned for \d+s\s*", "", text, flags=re.IGNORECASE)

    # Remove sensitivity label disclaimer
    text = re.sub(
        r"Sensitivity label not applied\. The generated file does not contain a label and may require (?:manual labeling before saving or sharing to protect the data\.|classification\.)\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def extract_message_text(message_element: Any) -> str:
    """Extract clean formatted text from a message element."""
    raw_name = message_element.element_info.name or ""
    clean = clean_copilot_text(raw_name)
    if clean:
        return clean

    parts: list[str] = []
    for c in message_element.descendants():
        if c.element_info.control_type == "Text":
            nm = c.element_info.name or ""
            if nm and not nm.startswith("Copilot said:"):
                parts.append(nm)
    return clean_copilot_text("\n".join(parts))


def click_new_chat(app_window=None) -> bool:
    """Click the 'New chat' / 'New topic' control to clear context and start fresh."""
    if app_window is None:
        app_window = get_copilot_window()
    focus_copilot(app_window)
    for d in app_window.descendants():
        nm = (d.element_info.name or "").lower()
        if any(label in nm for label in ("new chat", "new topic", "start new chat")):
            try:
                d.click_input()
                time.sleep(1.0)
                return True
            except Exception:
                pass
    return False


STUCK_PROMPT_WARNING = ("Prompt still in Copilot input box; please click Send or press Enter in M365 Copilot.")
SEND_RETRIES = 3
_PLACEHOLDERS = {"", "message copilot", "ask copilot", "talk to copilot", "ask anything"}


def read_input_text(edit: Any) -> str | None:
    """The composer's current text, or None when UIA cannot read it (then nothing is retried: no double send)."""
    for getter in (lambda: edit.get_value(), lambda: edit.iface_value.CurrentValue, lambda: edit.window_text()):
        try:
            value = getter()
        except Exception:  # noqa: BLE001
            continue
        if value is not None:
            return str(value)
    return None


def input_cleared(edit: Any) -> bool:
    text = read_input_text(edit)
    return text is None or text.strip().lower() in _PLACEHOLDERS


def has_focus(edit: Any) -> bool:
    try:
        return bool(edit.has_keyboard_focus())
    except Exception:  # noqa: BLE001
        return True   # unknown: do not block the retry on it


def click_send(app_window: Any) -> bool:
    """Click the composer's Send button; False when it is not found or the click fails."""
    try:
        for b in app_window.descendants():
            if b.element_info.control_type == "Button" and (b.element_info.name or "") == "Send":
                b.click_input()
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def submit_and_verify(app_window: Any, edit: Any, prompt: str = "", status_callback: Callable[[str], None] | None = None,
                      *, retries: int = SEND_RETRIES, settle: float = 1.0, sleep: Callable[[float], None] = time.sleep,
                      keys: Callable[[str], None] | None = None) -> bool:
    """Send (button, else Enter), then check the composer emptied; retry with focus up to `retries` times.

    Returns True once the input is clear (also when the user sent it by hand meanwhile). After the last retry it
    emits STUCK_PROMPT_WARNING and returns False; the caller keeps waiting for a reply, so a manual send still works.
    """
    keys = keys or send_keys
    if not click_send(app_window):
        keys("{ENTER}")
    sleep(settle)
    if input_cleared(edit):
        return True
    for attempt in range(1, retries + 1):
        if status_callback:
            status_callback(f"Prompt not sent yet (input box not empty); retry {attempt}/{retries} with focus...")
        focus_copilot(app_window)
        try:
            edit.set_focus()
        except Exception:  # noqa: BLE001
            pass
        sleep(0.3)
        if input_cleared(edit):
            return True
        if not (has_focus(edit) and click_send(app_window)):
            keys("{ENTER}")
        sleep(settle)
        if input_cleared(edit):
            return True
    if status_callback:
        status_callback(STUCK_PROMPT_WARNING)
    return False


class DesktopCopilotDriver:
    """Automation driver for the running M365 Copilot / Windows Copilot desktop app."""

    def __init__(self):
        self.window = get_copilot_window()

    def new_conversation(self) -> None:
        """Reset conversation via 'New chat'."""
        self.window = get_copilot_window()
        click_new_chat(self.window)
        time.sleep(1.5)

    def send_message(
        self,
        prompt: str,
        timeout_seconds: int = 180,
        status_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        """Send prompt to the M365 Copilot desktop app and wait for the response.

        cancel_check() is polled while waiting; True stops waiting (the desktop app keeps
        generating: there is no reliable Stop control to press) and raises Cancelled.
        """
        self.window = get_copilot_window()
        focus_copilot(self.window)

        # Record message count before sending
        prior_messages = get_message_elements(self.window)
        prior_count = len(prior_messages)
        prior_last_text = (prior_messages[-1].element_info.name or "") if prior_messages else ""

        # Focus edit box and paste text
        edit = find_message_input(self.window)
        edit.set_focus()
        time.sleep(0.3)

        # Clear any residual draft
        send_keys("^a{BACKSPACE}")
        time.sleep(0.2)

        # Copy prompt to clipboard and paste
        set_clipboard_text(prompt)
        send_keys("^v")
        time.sleep(0.4)

        # Plan v0.5 D3: submit, then make sure the composer really emptied (retry with focus up to 3 times).
        submit_and_verify(self.window, edit, prompt, status_callback)

        if status_callback:
            status_callback("Prompt submitted, waiting for Copilot generation...")

        # Poll for new response
        start_time = time.monotonic()
        last_seen_text = ""
        stable_count = 0

        while time.monotonic() - start_time < timeout_seconds:
            time.sleep(0.8)
            if cancel_check and cancel_check():
                try:
                    from neo_events import Cancelled
                except Exception:  # noqa: BLE001
                    Cancelled = RuntimeError  # noqa: N806
                raise Cancelled("cancelled while waiting for the desktop Copilot reply")
            current_messages = get_message_elements(self.window)

            if len(current_messages) > prior_count:
                latest_elem = current_messages[-1]
            elif current_messages and (current_messages[-1].element_info.name or "") != prior_last_text:
                latest_elem = current_messages[-1]
            else:
                continue

            current_text = extract_message_text(latest_elem)

            # Check for completion buttons ('Copy Response', 'I like something')
            buttons = [c.element_info.name or "" for c in latest_elem.descendants() if c.element_info.control_type == "Button"]
            is_complete = "Copy Response" in buttons or "I like something" in buttons

            if is_complete and len(current_text) > 0:
                if status_callback:
                    status_callback("Response complete (Copy Response indicator).")
                return current_text

            if current_text and current_text == last_seen_text:
                stable_count += 1
                if stable_count >= 3:
                    if status_callback:
                        status_callback("Response complete (text stabilized).")
                    return current_text
            else:
                last_seen_text = current_text
                stable_count = 0

        if last_seen_text:
            return last_seen_text
        raise TimeoutError(f"M365 Copilot desktop app did not respond within {timeout_seconds}s.")

    def close(self) -> None:
        """No persistent background socket to close for desktop UIA."""
        pass


if __name__ == "__main__":
    print("Testing M365 Copilot Desktop Driver...")
    driver = DesktopCopilotDriver()
    print("Connected to window:", driver.window.window_text())
    msgs = get_message_elements(driver.window)
    print(f"Current message count: {len(msgs)}")
    if msgs:
        print("Latest message preview:\n", extract_message_text(msgs[-1])[:200])
