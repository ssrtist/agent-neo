"""agent-neo's own Edge profile (0.4.0): agent-neo only ever ends, drives or launches the Edge on ITS profile,
never another tool's Edge profile or the user's own Edge."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from tests.support import Scratch

import neo_edge as edge
from neo_settings import SettingsError, load_settings

LA = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
OURS = os.path.join(LA, "agent-neo-edge")
OTHER = os.path.join(LA, "other-edge-profile")
REAL = os.path.join(LA, "Microsoft", "Edge", "User Data")
CHROME_REAL = os.path.join(LA, "Google", "Chrome", "User Data")
EXE = r'"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"'

PROCESSES = [
    # another tool's Edge on a separate profile: main process and a child
    edge.Proc(101, "msedge.exe", f"{EXE} --remote-debugging-port=9366 --user-data-dir={OTHER} --no-first-run about:blank"),
    edge.Proc(102, "msedge.exe", f'{EXE} --type=renderer --user-data-dir="{OTHER}" --lang=en-US'),
    # agent-neo's own Edge: main process (Windows quoting of the whole argument) and a child
    edge.Proc(201, "msedge.exe", f'{EXE} --remote-debugging-port=9365 "--user-data-dir={OURS}" --no-startup-window'),
    edge.Proc(202, "msedge.exe", f'{EXE} --type=gpu-process --user-data-dir="{OURS.upper()}\\" --x'),
    # look-alikes that must NOT match: a folder whose name starts with ours, one that ends with it, one inside it
    edge.Proc(301, "msedge.exe", f"{EXE} --user-data-dir={OURS}-old --remote-debugging-port=9399"),
    edge.Proc(302, "msedge.exe", f"{EXE} --user-data-dir={os.path.join(LA, 'x-agent-neo-edge')}"),
    edge.Proc(303, "msedge.exe", f"{EXE} --user-data-dir={os.path.join(OURS, 'nested')}"),
    # the user's real Edge (no --user-data-dir on the browser process; children carry the real profile)
    edge.Proc(401, "msedge.exe", f"{EXE} --profile-directory=Default"),
    edge.Proc(402, "msedge.exe", f'{EXE} --type=utility "--user-data-dir={REAL}" /prefetch:4'),
    # not Edge, even on our profile
    edge.Proc(501, "chrome.exe", f"chrome.exe --user-data-dir={OURS}"),
    # a process that names two profiles: not wholly ours
    edge.Proc(601, "msedge.exe", f"{EXE} --user-data-dir={OURS} --user-data-dir={OTHER}"),
]


class EdgeProfileTests(unittest.TestCase):
    def test_only_processes_on_our_exact_profile_match(self) -> None:
        self.assertEqual(edge.profile_pids(PROCESSES, OURS), [201, 202])
        self.assertEqual(edge.profile_pids(PROCESSES, OURS + "\\"), [201, 202])     # trailing separator, same folder

    def test_kill_ends_only_our_edge_never_other_profiles(self) -> None:
        stopped: list[list[int]] = []
        with mock.patch.object(edge, "list_edge_processes", side_effect=[PROCESSES, []]), \
                mock.patch.object(edge, "_stop_pids", side_effect=lambda pids: stopped.append(list(pids))), \
                mock.patch.object(edge.browser, "cdp_port_ready", return_value=False), \
                mock.patch.object(edge.browser, "kill_cdp_browser") as shared_kill:
            ended = edge.kill_profile_edge(OURS, 9365, wait_seconds=1)
        self.assertEqual(ended, [201, 202])
        self.assertEqual(stopped, [[201, 202]])
        self.assertTrue(set(stopped[0]).isdisjoint({101, 102, 301, 302, 303, 401, 402, 501, 601}))
        shared_kill.assert_not_called()

    def test_kill_refuses_real_profiles(self) -> None:
        for bad in (REAL, os.path.join(REAL, "Default"), CHROME_REAL, LA):
            with mock.patch.object(edge, "_stop_pids") as stop:
                with self.assertRaises(ValueError, msg=bad):
                    edge.kill_profile_edge(bad, 9366, wait_seconds=0)
                stop.assert_not_called()

    def test_ensure_never_lets_browser_automation_kill_and_passes_our_profile(self) -> None:
        with mock.patch.object(edge, "kill_profile_edge") as own_kill, \
                mock.patch.object(edge.browser, "cdp_version", return_value=None), \
                mock.patch.object(edge.browser, "ensure_cdp_browser", return_value={"Browser": "Edg"}) as ensure, \
                mock.patch.object(edge.browser, "kill_cdp_browser") as shared_kill:
            edge.ensure_profile_edge(OURS, 9365, force_fresh=True, start_url="about:blank")
        own_kill.assert_called_once()
        self.assertTrue(edge.same_path(own_kill.call_args.args[0], OURS))
        kw = ensure.call_args.kwargs
        self.assertIs(kw["force_fresh"], False)            # browser_automation's own (folder-name) kill never runs
        self.assertTrue(edge.same_path(kw["user_data_dir"], OURS))
        self.assertEqual(kw["port"], 9365)
        shared_kill.assert_not_called()

    def test_a_port_answered_by_another_browser_is_refused(self) -> None:
        foreign = [p for p in PROCESSES if p.pid in (101, 102, 301)]
        with mock.patch.object(edge, "kill_profile_edge"), \
                mock.patch.object(edge, "list_edge_processes", return_value=foreign), \
                mock.patch.object(edge.browser, "cdp_version", return_value={"Browser": "Edg"}), \
                mock.patch.object(edge.browser, "ensure_cdp_browser") as ensure:
            for fresh in (True, False):
                with self.assertRaises(edge.ForeignBrowserError):
                    edge.ensure_profile_edge(OURS, 9366, force_fresh=fresh)
        ensure.assert_not_called()
        # our own Edge on the port is fine (reuse)
        self.assertTrue(edge.port_owned_by_profile(9365, OURS, PROCESSES))
        self.assertFalse(edge.port_owned_by_profile(9399, OURS, PROCESSES))
        self.assertFalse(edge.port_owned_by_profile(936, OURS, PROCESSES))

    def test_driver_defaults_to_our_profile_and_port_and_refuses_real_profile(self) -> None:
        import copilot_web as web
        d = web.WebCopilotDriver()
        self.assertTrue(edge.same_path(d.profile_dir, OURS))
        self.assertEqual(d.port, 9365)
        self.assertEqual(edge.DEFAULT_CDP_PORT, 9365)
        with self.assertRaises(ValueError):
            web.WebCopilotDriver(profile_dir=REAL)


class ProfileSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scratch("edgeprof")
        self.home = self.s.path / "home"
        self.ws = self.s.path / "ws"
        (self.ws / ".agent-neo").mkdir(parents=True)
        self.home.mkdir()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("NEO_BROWSER_PROFILE", None)
        os.environ.pop("AGENT_NEO_BROWSER_PROFILE", None)

    def tearDown(self) -> None:
        self.env.stop()
        self.s.cleanup()

    def _home(self, text: str) -> None:
        (self.home / "neo.toml").write_text(text, encoding="utf-8")

    def _ws(self, text: str) -> Path:
        p = self.ws / ".agent-neo" / "neo.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_default_profile_and_port(self) -> None:
        s, _ = load_settings(self.ws, {}, home=self.home)
        self.assertTrue(edge.same_path(s.browser_profile_path, OURS))
        self.assertEqual(s.cdp_port, 9365)

    def test_home_file_relative_and_env_override(self) -> None:
        self._home('browser_profile_dir = "profiles/neo"\n')
        s, _ = load_settings(self.ws, {}, home=self.home)
        self.assertTrue(edge.same_path(s.browser_profile_path, str(self.home / "profiles" / "neo")))
        env_dir = str(self.s.path / "env-profile")
        os.environ["NEO_BROWSER_PROFILE"] = env_dir
        s, _ = load_settings(self.ws, {}, home=self.home)
        self.assertTrue(edge.same_path(s.browser_profile_path, env_dir))
        self.assertEqual(s.sources["browser_profile_dir"], "env NEO_BROWSER_PROFILE")

    def test_workspace_file_cannot_choose_the_profile(self) -> None:
        ws_file = self._ws(f'browser_profile_dir = "{(self.s.path / "elsewhere").as_posix()}"\n')
        s, warnings = load_settings(self.ws, {}, home=self.home)
        self.assertTrue(edge.same_path(s.browser_profile_path, OURS))
        self.assertTrue(any(str(ws_file) in w and "browser_profile_dir ignored" in w for w in warnings), warnings)

    def test_real_profiles_refused(self) -> None:
        for bad in (REAL, CHROME_REAL):
            self._home(f'browser_profile_dir = "{Path(bad).as_posix()}"\n')
            with self.assertRaises(SettingsError, msg=bad):
                load_settings(self.ws, {}, home=self.home)
        (self.home / "neo.toml").unlink()
        os.environ["NEO_BROWSER_PROFILE"] = REAL
        with self.assertRaises(SettingsError):
            load_settings(self.ws, {}, home=self.home)


if __name__ == "__main__":
    unittest.main()
