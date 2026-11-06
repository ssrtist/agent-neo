"""neo_settings.py: harness settings from neo.toml (and agent-neo.toml).

Precedence, lowest first:
    built-in defaults < ~/.agent-neo/neo.toml < <workspace>/.agent-neo/neo.toml < CLI flags

``NEO_HOME`` or ``AGENT_NEO_HOME`` overrides ``~/.agent-neo`` (tests use it so they never touch the real home).
If ``~/.neo`` exists and ``~/.agent-neo`` does not, ``~/.neo`` is used.
Inside a home or workspace config folder, ``neo.toml`` is preferred, with ``agent-neo.toml`` accepted if
``neo.toml`` is not present.
Unknown keys produce a warning; a malformed file (bad TOML, wrong type, bad value) is refused
with a ``SettingsError`` naming the file and key. There is no silent fallback.

Stdlib only (``tomllib``), import-safe.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

SETTINGS_FILE = "neo.toml"
ALT_SETTINGS_FILE = "agent-neo.toml"
BACKENDS = ("copilot", "gemini", "whatsapp", "desktop", "auto", "custom")
BACKEND_ALIASES = {"web": "copilot"}
BROWSER_MODES = ("offscreen", "visible", "headless")


class SettingsError(Exception):
    """A settings file (or override) is malformed; the message names the file and key."""


@dataclass(frozen=True)
class Settings:
    enable_subagents: bool = False
    show_thinking: bool = False
    environment_awareness: bool = True
    custom_instructions_file: str = ""
    backend: str = "copilot"
    browser_mode: str = "offscreen"
    whatsapp_phone: str = "19175123390"
    whatsapp_contact: str = ""
    # agent-neo's dedicated CDP port and Edge profile.
    cdp_port: int = 9365
    browser_profile_dir: str = ""      # "" = %LOCALAPPDATA%\agent-neo-edge; env NEO_BROWSER_PROFILE / AGENT_NEO_BROWSER_PROFILE overrides
    # backend = "custom" drives any web chat page. TOML: the [custom_backend] table (url, input_selector,
    # send_selector, bubble_selector) or top-level custom_url; HOME file or command line only (never a workspace file).
    custom_url: str = ""
    custom_input_selector: str = ""
    custom_send_selector: str = ""
    custom_bubble_selector: str = ""
    step_chunk: int = 10
    hard_ceiling: int = 255
    auto_approve: bool = False
    subagent_max_steps: int = 10
    # Where each value came from (key -> "default" | file path | "cli"), for diagnostics.
    sources: dict[str, str] = field(default_factory=dict, compare=False, repr=False)
    # The resolved custom-instructions file (absolute) or "" when unset.
    custom_instructions_path: str = field(default="", compare=False, repr=False)
    # agent-neo's Edge profile folder, resolved (absolute; the default when browser_profile_dir is "").
    browser_profile_path: str = field(default="", compare=False, repr=False)

    def public(self) -> dict[str, Any]:
        """The settings as a plain dict (no bookkeeping fields)."""
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in _BOOKKEEPING}


_BOOKKEEPING = {"sources", "custom_instructions_path", "browser_profile_path"}
KEYS: dict[str, type] = {f.name: type(getattr(Settings(), f.name)) for f in fields(Settings) if f.name not in _BOOKKEEPING}
_CHOICES: dict[str, tuple[str, ...]] = {"backend": BACKENDS, "browser_mode": BROWSER_MODES}
CUSTOM_TABLE = {"url": "custom_url", "input_selector": "custom_input_selector",
                "send_selector": "custom_send_selector", "bubble_selector": "custom_bubble_selector"}
CUSTOM_KEYS = frozenset(CUSTOM_TABLE.values())
_POSITIVE = {"cdp_port", "step_chunk", "hard_ceiling", "subagent_max_steps"}


def neo_home() -> Path:
    """The agent-neo home folder: NEO_HOME or AGENT_NEO_HOME, else ~/.agent-neo (or ~/.neo if it exists; not created here)."""
    value = (os.environ.get("NEO_HOME") or os.environ.get("AGENT_NEO_HOME") or "").strip()
    if value:
        return Path(value).expanduser().resolve()
    primary = Path.home() / ".agent-neo"
    alt = Path.home() / ".neo"
    if not primary.exists() and alt.exists():
        return alt
    return primary


agent_neo_home = neo_home


def _pick_settings_file(folder: Path) -> Path:
    primary = folder / SETTINGS_FILE
    alt = folder / ALT_SETTINGS_FILE
    if not primary.is_file() and alt.is_file():
        return alt
    return primary


def home_settings_path(home: Path | None = None) -> Path:
    return _pick_settings_file(home or neo_home())


def workspace_settings_path(workspace: Path) -> Path:
    ws = Path(workspace)
    primary_dir = ws / ".agent-neo"
    alt_dir = ws / ".neo"
    if not primary_dir.exists() and alt_dir.exists():
        return _pick_settings_file(alt_dir)
    return _pick_settings_file(primary_dir)


def _check(key: str, value: Any, origin: str) -> Any:
    want = KEYS[key]
    # bool is a subclass of int: an int key must not accept true/false, a bool key not 0/1.
    ok = isinstance(value, want) and not (want is int and isinstance(value, bool))
    if not ok:
        raise SettingsError(
            f"{origin}: key '{key}' must be {want.__name__}, got {type(value).__name__} ({value!r})")
    if key == "backend" and isinstance(value, str):
        value = BACKEND_ALIASES.get(value.strip().lower(), value)
    if key in _CHOICES and value not in _CHOICES[key]:
        raise SettingsError(f"{origin}: key '{key}' must be one of {', '.join(_CHOICES[key])}; got {value!r}")
    if key in _POSITIVE and value <= 0:
        raise SettingsError(f"{origin}: key '{key}' must be a positive integer; got {value!r}")
    if key == "cdp_port" and value > 65535:
        raise SettingsError(f"{origin}: key 'cdp_port' must be a TCP port (1-65535); got {value!r}")
    return value


def read_settings_file(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Parse one neo.toml. Returns (known values, warnings). Raises SettingsError if malformed."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SettingsError(f"{path}: cannot read settings file ({exc})") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8-sig"))
        values: dict[str, Any] = {}
        warnings: list[str] = []
    except UnicodeDecodeError as exc:
        raise SettingsError(f"{path}: settings file is not UTF-8 ({exc})") from exc
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{path}: malformed TOML ({exc})") from exc
    if isinstance(data.get("custom_backend"), dict):
        table = data.pop("custom_backend")
        for sub, value in table.items():
            key = CUSTOM_TABLE.get(sub)
            if key is None:
                warnings.append(f"{path}: unknown key 'custom_backend.{sub}' ignored")
                continue
            values[key] = _check(key, value, f"{path} [custom_backend]")
    for key, value in data.items():
        if key not in KEYS:
            kind = "table" if isinstance(value, dict) else "key"
            warnings.append(f"{path}: unknown {kind} '{key}' ignored")
            continue
        values[key] = _check(key, value, str(path))
    return values, warnings


def load_settings(
    workspace: Path | None = None,
    overrides: dict[str, Any] | None = None,
    home: Path | None = None,
) -> tuple[Settings, list[str]]:
    """Resolve settings for a workspace. Returns (settings, warnings).

    ``overrides`` are CLI flags (None values are ignored). Raises SettingsError on a malformed
    file, a bad override, or a custom_instructions_file that does not exist.
    """
    merged: dict[str, Any] = {}
    sources: dict[str, str] = {k: "default" for k in KEYS}
    custom_base: Path | None = None
    warnings: list[str] = []
    layers: list[Path] = [home_settings_path(home)]
    if workspace is not None:
        layers.append(workspace_settings_path(Path(workspace)))
    seen: set[Path] = set()
    profile_base: Path | None = None
    ws_path = workspace_settings_path(Path(workspace)) if workspace is not None else None
    for path in layers:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        values, warns = read_settings_file(path)
        warnings.extend(warns)
        for key, value in values.items():
            if key in CUSTOM_KEYS and path == ws_path:
                # plan v0.5 D5: a cloned repo must not send your prompts (and file contents) to a page it chooses
                warnings.append(f"{path}: {key} ignored - only the home neo.toml or the command line may set the custom backend")
                continue
            if key == "browser_profile_dir" and path == ws_path:
                # agent-neo ends the Edge running on this profile at every fresh start: a cloned repo must not choose it
                warnings.append(f"{path}: browser_profile_dir ignored - only the home neo.toml or "
                                f"NEO_BROWSER_PROFILE may set agent-neo's browser profile")
                continue
            merged[key] = value
            sources[key] = str(path)
            if key == "custom_instructions_file":
                custom_base = path.parent
            if key == "browser_profile_dir":
                profile_base = path.parent
    for env_var in ("NEO_BROWSER_PROFILE", "AGENT_NEO_BROWSER_PROFILE"):
        env_profile = os.environ.get(env_var, "").strip()
        if env_profile:
            merged["browser_profile_dir"] = env_profile
            sources["browser_profile_dir"] = f"env {env_var}"
            profile_base = Path.cwd()
            break
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key not in KEYS:
            raise SettingsError(f"command line: unknown setting '{key}'")
        merged[key] = _check(key, value, "command line")
        sources[key] = "cli"
        if key == "custom_instructions_file":
            custom_base = Path.cwd()
        if key == "browser_profile_dir":
            profile_base = Path.cwd()
    settings = replace(Settings(), **merged, sources=sources)
    from neo_edge import check_profile_dir, default_profile_dir
    if settings.browser_profile_dir:
        prof = Path(settings.browser_profile_dir).expanduser()
        if not prof.is_absolute():
            prof = (profile_base or Path.cwd()) / prof
        prof_text = os.path.normpath(os.path.abspath(prof))
    else:
        prof_text = default_profile_dir()
    reason = check_profile_dir(prof_text)
    if reason:
        raise SettingsError(f"{sources['browser_profile_dir']}: browser_profile_dir refused: {reason}")
    settings = replace(settings, browser_profile_path=prof_text)
    if settings.custom_url:
        from urllib.parse import urlsplit
        u = urlsplit(settings.custom_url)
        if u.scheme not in ("https", "http") or not u.hostname:
            raise SettingsError(f"{sources['custom_url']}: custom_url must be an http(s) URL; got {settings.custom_url!r}")
    if settings.backend == "custom" and not settings.custom_url:
        raise SettingsError(f"{sources['backend']}: backend = 'custom' needs custom_url (neo.toml [custom_backend] url, "
                            f"or --custom-url)")
    ws_file = str(workspace_settings_path(Path(workspace))) if workspace is not None else None
    if ws_file and sources.get("auto_approve") == ws_file and settings.auto_approve:
        # Precedence is the plan's (workspace beats home), but a cloned repo turning approvals off must be loud.
        warnings.append(f"{ws_file}: auto_approve = true set by the WORKSPACE file - tool calls will run without asking")
    if settings.custom_instructions_file:
        p = Path(settings.custom_instructions_file).expanduser()
        if not p.is_absolute():
            # A relative path is relative to the folder of the file that set it.
            p = (custom_base or Path.cwd()) / p
        from_ws = bool(ws_file) and sources["custom_instructions_file"] == ws_file and workspace is not None
        if from_ws:
            # Checked as text first: is_file()/resolve() on a UNC path would already contact that host (NTLM).
            root = Path(os.path.abspath(workspace))
            if not Path(os.path.normpath(os.path.abspath(p))).is_relative_to(root):
                raise SettingsError(f"{ws_file}: custom_instructions_file must be inside the workspace ({root}): {p}")
        if not p.is_file():
            raise SettingsError(
                f"{sources['custom_instructions_file']}: custom_instructions_file not found: {p}")
        if from_ws:
            # and again after resolving links (a junction inside the workspace may point elsewhere)
            root = Path(workspace).resolve()
            if not p.resolve().is_relative_to(root):
                raise SettingsError(f"{ws_file}: custom_instructions_file must be inside the workspace ({root}): {p}")
        settings = replace(settings, custom_instructions_path=str(p.resolve()))
    return settings, warnings


def custom_options(settings) -> dict:
    """The custom backend's options from settings (plan v0.5 D5)."""
    return {"url": settings.custom_url, "input_selector": settings.custom_input_selector,
            "send_selector": settings.custom_send_selector,
            "bubble_selector": settings.custom_bubble_selector}


def read_custom_instructions(settings: Settings) -> str:
    """The custom instructions text (verbatim) or '' when unset."""
    if not settings.custom_instructions_path:
        return ""
    return Path(settings.custom_instructions_path).read_text(encoding="utf-8-sig")
