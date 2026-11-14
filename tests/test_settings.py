"""Settings: neo.toml precedence, malformed-file refusal, example file (plan v0.4 section 2)."""
from __future__ import annotations

import os
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from tests.support import REPO, Scratch

from neo_settings import (
    CUSTOM_TABLE,
    KEYS,
    Settings,
    SettingsError,
    agent_neo_home,
    load_settings,
    neo_home,
    read_custom_instructions,
)


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("settings")
        self.home = self.s.path / "home"
        self.ws = self.s.path / "ws"
        (self.ws / ".agent-neo").mkdir(parents=True)
        self.home.mkdir()

    def tearDown(self) -> None:
        self.s.cleanup()

    def write_home(self, text: str) -> Path:
        p = self.home / "neo.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def write_ws(self, text: str) -> Path:
        p = self.ws / ".agent-neo" / "neo.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_defaults_without_files(self) -> None:
        settings, warnings = load_settings(self.ws, {}, home=self.home)
        self.assertEqual(warnings, [])
        self.assertEqual(settings.public(), Settings().public())
        self.assertEqual(settings.backend, "copilot")
        self.assertEqual(settings.browser_mode, "offscreen")
        self.assertFalse(settings.enable_subagents)
        self.assertFalse(settings.show_thinking)
        self.assertTrue(settings.environment_awareness)
        self.assertEqual((settings.cdp_port, settings.step_chunk, settings.hard_ceiling), (9365, 10, 255))
        s_gem, _ = load_settings(self.ws, {"backend": "gemini"}, home=self.home)
        self.assertEqual(s_gem.backend, "gemini")
        s_wa, _ = load_settings(self.ws, {"backend": "whatsapp"}, home=self.home)
        self.assertEqual(s_wa.backend, "whatsapp")
        s_web, _ = load_settings(self.ws, {"backend": "web"}, home=self.home)
        self.assertEqual(s_web.backend, "copilot")

    def test_precedence_defaults_home_workspace_cli(self) -> None:
        home_file = self.write_home("step_chunk = 5\nshow_thinking = true\nbrowser_mode = \"visible\"\n")
        ws_file = self.write_ws("step_chunk = 7\nbrowser_mode = \"headless\"\n")
        s, _ = load_settings(self.ws, {}, home=self.home)
        self.assertEqual(s.step_chunk, 7)              # workspace beats home
        self.assertTrue(s.show_thinking)               # home beats default
        self.assertEqual(s.browser_mode, "headless")
        self.assertEqual(s.hard_ceiling, 255)          # default
        self.assertEqual(s.sources["step_chunk"], str(ws_file))
        self.assertEqual(s.sources["show_thinking"], str(home_file))
        self.assertEqual(s.sources["hard_ceiling"], "default")
        s, _ = load_settings(self.ws, {"step_chunk": 9, "browser_mode": None}, home=self.home)
        self.assertEqual(s.step_chunk, 9)              # CLI beats workspace
        self.assertEqual(s.browser_mode, "headless")   # a None flag does not override
        self.assertEqual(s.sources["step_chunk"], "cli")

    def test_home_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"NEO_HOME": str(self.home), "AGENT_NEO_HOME": ""}):
            self.assertEqual(neo_home(), self.home.resolve())
            self.assertEqual(agent_neo_home(), self.home.resolve())
            self.write_home("hard_ceiling = 40\n")
            s, _ = load_settings(self.ws, {})
            self.assertEqual(s.hard_ceiling, 40)
        with mock.patch.dict(os.environ, {"NEO_HOME": "", "AGENT_NEO_HOME": str(self.home)}):
            self.assertEqual(neo_home(), self.home.resolve())
        with mock.patch.dict(os.environ, {"NEO_HOME": "", "AGENT_NEO_HOME": ""}):
            self.assertEqual(neo_home(), Path.home() / ".agent-neo")

    def test_malformed_toml_refused_with_file_name(self) -> None:
        p = self.write_ws("step_chunk = = 3\n")
        with self.assertRaises(SettingsError) as cm:
            load_settings(self.ws, {}, home=self.home)
        self.assertIn(str(p), str(cm.exception))
        self.assertIn("malformed TOML", str(cm.exception))

    def test_wrong_types_and_values_refused(self) -> None:
        cases = {
            'step_chunk = "ten"': "step_chunk",
            "enable_subagents = 1": "enable_subagents",       # an int is not a bool
            "cdp_port = true": "cdp_port",                     # a bool is not an int
            'browser_mode = "minimised"': "browser_mode",
            'backend = "chatgpt"': "backend",
            "hard_ceiling = 0": "hard_ceiling",
            "shell_timeout = 0": "shell_timeout",
            "cdp_port = 70000": "cdp_port",
        }
        for text, key in cases.items():
            with self.subTest(text=text):
                p = self.write_home(text + "\n")
                with self.assertRaises(SettingsError) as cm:
                    load_settings(self.ws, {}, home=self.home)
                self.assertIn(key, str(cm.exception))
                self.assertIn(str(p), str(cm.exception))

    def test_bad_cli_override_refused(self) -> None:
        with self.assertRaises(SettingsError):
            load_settings(self.ws, {"step_chunk": -1}, home=self.home)
        with self.assertRaises(SettingsError):
            load_settings(self.ws, {"no_such_key": 1}, home=self.home)

    def test_unknown_key_warns_but_loads(self) -> None:
        self.write_ws("colour = \"blue\"\n[extras]\nx = 1\nstep_chunk = 3\n")
        # step_chunk under [extras] belongs to that table: ignored with the table.
        s, warnings = load_settings(self.ws, {}, home=self.home)
        self.assertEqual(s.step_chunk, 10)
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("colour" in w for w in warnings))
        self.assertTrue(any("extras" in w for w in warnings))

    def test_custom_instructions_relative_to_setting_file(self) -> None:
        (self.ws / ".agent-neo" / "extra.md").write_text("Always answer in haiku.\n", encoding="utf-8")
        self.write_ws('custom_instructions_file = "extra.md"\n')
        s, _ = load_settings(self.ws, {}, home=self.home)
        self.assertEqual(Path(s.custom_instructions_path), (self.ws / ".agent-neo" / "extra.md").resolve())
        self.assertEqual(read_custom_instructions(s), "Always answer in haiku.\n")

    def test_custom_instructions_missing_file_refused(self) -> None:
        self.write_home('custom_instructions_file = "nope.md"\n')
        with self.assertRaises(SettingsError) as cm:
            load_settings(self.ws, {}, home=self.home)
        self.assertIn("nope.md", str(cm.exception))

    def test_workspace_custom_instructions_must_stay_inside_the_workspace(self) -> None:
        # code review r1: a cloned repo's toml could paste any local file into the prompt sent to the model
        secret = self.s.path / "secret.txt"
        secret.write_text("TOP SECRET\n", encoding="utf-8")
        for value in ("../../secret.txt", str(secret).replace("\\", "/")):
            self.write_ws(f'custom_instructions_file = "{value}"\n')
            with self.assertRaises(SettingsError) as cm:
                load_settings(self.ws, {}, home=self.home)
            self.assertIn("must be inside the workspace", str(cm.exception))
        # the same absolute path is fine from the user's own home file
        (self.ws / ".agent-neo" / "neo.toml").unlink()
        self.write_home(f'custom_instructions_file = "{str(secret).replace(chr(92), "/")}"\n')
        s, _ = load_settings(self.ws, {}, home=self.home)
        self.assertEqual(read_custom_instructions(s), "TOP SECRET\n")

    def test_workspace_custom_instructions_unc_refused_before_touching_it(self) -> None:
        # code review r2: is_file()/resolve() on \\host\share would already contact the host (NTLM)
        self.write_ws('custom_instructions_file = "//203.0.113.9/share/x.md"\n')
        touched = []
        real_is_file = Path.is_file

        def spy(p, *a, **k):
            touched.append(str(p))
            return False if str(p).startswith("\\\\") else real_is_file(p, *a, **k)
        with mock.patch.object(Path, "is_file", spy):
            with self.assertRaises(SettingsError) as cm:
                load_settings(self.ws, {}, home=self.home)
        self.assertIn("must be inside the workspace", str(cm.exception))
        self.assertEqual([t for t in touched if t.startswith("\\\\")], [])

    def test_workspace_custom_instructions_through_a_junction_refused(self) -> None:
        outside = self.s.path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("TOP SECRET\n", encoding="utf-8")
        import subprocess
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.ws / "link"), str(outside)], capture_output=True)
        if r.returncode != 0:
            self.skipTest("cannot create a junction here")
        try:
            self.write_ws('custom_instructions_file = "../link/secret.txt"\n')
            with self.assertRaises(SettingsError) as cm:
                load_settings(self.ws, {}, home=self.home)
            self.assertIn("must be inside the workspace", str(cm.exception))
        finally:
            os.rmdir(self.ws / "link")   # the junction only

    def test_workspace_auto_approve_warns(self) -> None:
        self.write_ws("auto_approve = true\n")
        s, warnings = load_settings(self.ws, {}, home=self.home)
        self.assertTrue(s.auto_approve)       # plan precedence kept; made loud instead
        self.assertTrue(any("auto_approve = true set by the WORKSPACE file" in w for w in warnings), warnings)
        self.write_home("auto_approve = true\n")
        (self.ws / ".agent-neo" / "neo.toml").unlink()
        s, warnings = load_settings(self.ws, {}, home=self.home)
        self.assertTrue(s.auto_approve)
        self.assertEqual(warnings, [])

    def test_utf8_bom_accepted(self) -> None:
        (self.home / "neo.toml").write_bytes("\ufeffstep_chunk = 4\n".encode("utf-8"))
        s, _ = load_settings(self.ws, {}, home=self.home)
        self.assertEqual(s.step_chunk, 4)

    def test_example_file_lists_every_key_with_its_default(self) -> None:
        data = tomllib.loads((REPO / "neo.example.toml").read_text(encoding="utf-8"))
        for sub, key in CUSTOM_TABLE.items():   # v0.5: the [custom_backend] table holds the custom_* keys
            data[key] = data["custom_backend"].pop(sub)
        self.assertEqual(data.pop("custom_backend"), {})
        self.assertEqual(set(data), set(KEYS))
        self.assertEqual(data, Settings().public())


if __name__ == "__main__":
    unittest.main()
