"""Negative controls for the offline suite (builder rule: every check must be shown able to fail).

Copies the repo to a scratch folder, applies one mutation at a time, runs the named tests and
expects them to FAIL. Never edits the real repo. Run:  python tests/negative_controls.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (description, file, old, new, tests that must fail)
MUTATIONS = [
    ("close() leaves an Edge launched before Playwright connected (review r2)", "copilot_web.py",
     "        elif self._browser is None and self._launched and self._edge_up and",
     "        elif False and",
     ["tests.test_web_driver.WebDriverTests.test_close_after_stop_during_launch_ends_our_edge"]),
    ("interrupted sign-in not re-run (review r2)", "copilot_web.py",
     "            if not self._signed_in:\n", "            if False:\n",
     ["tests.test_web_driver.WebDriverTests.test_interrupted_sign_in_runs_again_on_the_next_turn"]),
    ("sign-in moves a reused Edge (review r2)", "copilot_web.py",
     '        if self.browser_mode == "visible" or not self._launched:\n', '        if self.browser_mode == "visible":\n',
     ["tests.test_web_driver.WebDriverTests.test_sign_in_never_moves_a_reused_edge"]),
    ("web sign-in wait ignores Stop (review r1)", "copilot_web.py",
     "            self._check_cancel(\"while waiting for sign-in\")\n            if self._ready_url(page.url):\n                return\n            if _is_login_url",
     "            if self._ready_url(page.url):\n                return\n            if _is_login_url",
     ["tests.test_web_driver.WebDriverTests.test_sign_in_wait_honours_stop_and_never_shows_the_window"]),
    ("web Stop not checked before typing (review r1)", "copilot_web.py",
     "        input_box = self.find_input()\n        self._check_cancel(\"before the prompt was sent\")\n",
     "        input_box = self.find_input()\n",
     ["tests.test_web_driver.WebDriverTests.test_stop_before_submit_never_types_the_prompt"]),
    ("offscreen parks a reused Edge (review r1)", "copilot_web.py",
     "        if offscreen and self._launched:\n", "        if offscreen:\n",
     ["tests.test_web_driver.WebDriverTests.test_offscreen_parks_only_an_edge_it_launched"]),
    ("sub-agent snapshots yolo again (review r1)", "neo_core.py",
     "        child.parent = self\n",
     "        child.auto_approve = self.auto_approve\n",
     ["tests.test_core.SubagentTests.test_yolo_off_mid_run_reaches_a_running_child"]),
    ("workspace custom_instructions_file may leave the workspace (review r1)", "neo_settings.py",
     "            if not p.resolve().is_relative_to(root):",
     "            if False:",
     ["tests.test_settings.SettingsTests.test_workspace_custom_instructions_through_a_junction_refused"]),
    ("workspace custom_instructions_file touched before the lexical check (review r2)", "neo_settings.py",
     "            if not Path(os.path.normpath(os.path.abspath(p))).is_relative_to(root):",
     "            if False:",
     ["tests.test_settings.SettingsTests.test_workspace_custom_instructions_unc_refused_before_touching_it"]),
    ("workspace auto_approve silent (review r1)", "neo_settings.py",
     "    if ws_file and sources.get(\"auto_approve\") == ws_file and settings.auto_approve:",
     "    if False:",
     ["tests.test_settings.SettingsTests.test_workspace_auto_approve_warns"]),
    ("CLI default backend back to auto", "neo.py",
     'help="Chat automation backend (default: copilot,', 'help="Chat automation backend (default: auto,',
     ["tests.test_cli.CliTests.test_help_shows_default_copilot_and_browser_mode"]),
    ("--browser-mode flag removed", "neo.py",
     'parser.add_argument("--browser-mode"', 'parser.add_argument("--browser-window"',
     ["tests.test_cli.CliTests.test_help_shows_default_copilot_and_browser_mode"]),
    ("workspace file no longer beats home file", "neo_settings.py",
     "        layers.append(workspace_settings_path(Path(workspace)))",
     "        layers.insert(0, workspace_settings_path(Path(workspace)))",
     ["tests.test_settings.SettingsTests.test_precedence_defaults_home_workspace_cli"]),
    ("malformed TOML silently ignored", "neo_settings.py",
     '        raise SettingsError(f"{path}: malformed TOML ({exc})") from exc',
     "        return {}, []",
     ["tests.test_settings.SettingsTests.test_malformed_toml_refused_with_file_name",
      "tests.test_cli.CliTests.test_malformed_settings_refuse_to_start"]),
    ("run_subagent always advertised", "neo_core.py",
     'SUBAGENT_TOOL_LINE if subagents else ""', "SUBAGENT_TOOL_LINE",
     ["tests.test_protocol.SystemPromptTests.test_subagent_tool_hidden_when_disabled",
      "tests.test_core.SubagentTests.test_disabled_hidden_and_refused"]),
    ("disabled sub-agent not refused", "neo_core.py",
     "        if not self.settings.enable_subagents:\n", "        if False:\n",
     ["tests.test_core.SubagentTests.test_disabled_hidden_and_refused"]),
    ("thought not emitted", "neo_core.py",
     'self.emit("thought", text=thought.strip(), step=step)', "pass",
     ["tests.test_core.CoreLoopTests.test_thought_event_only_when_present"]),
    ("approval skipped", "neo_core.py",
     "        return self._ask(\"tool\", summary, tool=name, args=truncate_args(args), step=step)",
     "        return True",
     ["tests.test_core.CoreLoopTests.test_tool_approval_final",
      "tests.test_core.CoreLoopTests.test_denied_tool_is_reported_not_run"]),
    ("cancel not checked after an approval", "neo_core.py",
     "        answer = self.approver(public(request))\n        self._check_cancel()\n",
     "        answer = self.approver(public(request))\n",
     ["tests.test_core.CoreLoopTests.test_cancel_after_approval_still_stops"]),
    ("driver cancel_check not passed", "neo_core.py",
     'if "cancel_check" in params:', "if False:",
     ["tests.test_core.CoreLoopTests.test_cancel_while_waiting_for_reply"]),
    ("step threshold never pauses", "neo_core.py",
     "if pause and limit <= step < ceiling:", "if False:",
     ["tests.test_core.CoreLoopTests.test_step_threshold_stop",
      "tests.test_core.CoreLoopTests.test_step_threshold_continue"]),
    ("no retry on a driver error", "neo_core.py",
     "for attempt in (1, 2):", "for attempt in (2,):",
     ["tests.test_core.CoreLoopTests.test_driver_error_retried_once"]),
    ("status heartbeats not de-duplicated", "neo_events.py",
     "            if key == last[\"key\"]:\n                return\n", "",
     ["tests.test_events.SessionLogTests.test_dedupe_status_and_fanout",
      "tests.test_cli.CliTests.test_repl_turn_with_fake_backend"]),
    ("private fields logged", "neo_events.py",
     'out = {k: v for k, v in event.items() if not k.startswith("_")}', "out = dict(event)",
     ["tests.test_events.EventSchemaTests.test_public_strips_private_fields_recursively",
      "tests.test_events.SessionLogTests.test_log_round_trip_lazy_and_public"]),
    ("0.3.0 prompt rule dropped", "neo_core.py",
     "- Prefer read before edit.\n", "",
     ["tests.test_protocol.SystemPromptTests.test_backward_compatible_with_0_3_0"]),
    ("lost parent context not re-sent", "neo_core.py",
     "                if self._context_lost:", "                if False:",
     ["tests.test_core.SubagentTests.test_backend_without_child_support_restarts_parent_context"]),
    # ---- agent-neo's own Edge profile (0.4.0)
    ("Edge profile matched by folder name, not the whole path", "neo_edge.py",
     "    return bool(dirs) and all(same_path(d, profile_dir) for d in dirs)",
     "    return bool(dirs) and any(os.path.basename(norm(profile_dir)) in norm(d) for d in dirs)",
     ["tests.test_edge_profile.EdgeProfileTests.test_only_processes_on_our_exact_profile_match",
      "tests.test_edge_profile.EdgeProfileTests.test_kill_ends_only_our_edge_never_other_profiles"]),
    ("fresh start lets browser_automation kill by folder name", "neo_edge.py",
     "force_fresh=False, extra_args=extra_args", "force_fresh=force_fresh, extra_args=extra_args",
     ["tests.test_edge_profile.EdgeProfileTests.test_ensure_never_lets_browser_automation_kill_and_passes_our_profile"]),
    ("driver ends Edge with the shared-profile kill", "copilot_web.py",
     "            edge.kill_profile_edge(self.profile_dir, self.port, wait_seconds=wait_seconds)",
     "            browser.kill_cdp_browser(\"edge\", port=self.port, wait_seconds=wait_seconds)",
     ["tests.test_web_driver.WebDriverTests.test_close_after_stop_during_launch_ends_our_edge"]),
    ("real profiles not refused", "neo_edge.py",
     "    for bad in forbidden_profile_dirs():", "    for bad in []:",
     ["tests.test_edge_profile.EdgeProfileTests.test_kill_refuses_real_profiles",
      "tests.test_edge_profile.ProfileSettingsTests.test_real_profiles_refused",
      "tests.test_edge_profile.EdgeProfileTests.test_driver_defaults_to_our_profile_and_port_and_refuses_real_profile"]),
    ("workspace file chooses the Edge profile", "neo_settings.py",
     '            if key == "browser_profile_dir" and path == ws_path:', "            if False:",
     ["tests.test_edge_profile.ProfileSettingsTests.test_workspace_file_cannot_choose_the_profile"]),
    ("default CDP port changed from 9365", "neo_settings.py",
     "    cdp_port: int = 9365", "    cdp_port: int = 9366",
     ["tests.test_edge_profile.ProfileSettingsTests.test_default_profile_and_port"]),
    ("a CDP port answered by another browser is driven", "neo_edge.py",
     "    if browser.cdp_version(port) is not None and not port_owned_by_profile(port, profile_dir):",
     "    if False:",
     ["tests.test_edge_profile.EdgeProfileTests.test_a_port_answered_by_another_browser_is_refused"]),
    # ---- v0.5.0 (plan docs/PLAN-v0.5.md)
    ("D1 raw paths not protected before marked", "gui/lib/paths.js",
     "    : protectRawPaths(part.replace(", "    : ((x) => x)(part.replace(",
     ["tests.test_gui_js.GuiJsTests.test_raw_windows_paths_survive_markdown"]),
    ("D2 preamble not kept as thought", "neo_core.py",
     "            return _preamble_thought(obj, src[:start])", "            return obj",
     ["tests.test_protocol.ParseActionTests.test_preamble_becomes_thought"]),
    ("D2 first object wins over the action object", "neo_core.py",
     '        if obj.get("type") in _ACTION_TYPES:\n            return', "        if True:\n            return",
     ["tests.test_protocol.ParseActionTests.test_action_preferred_over_earlier_object"]),
    ("D3 composer not verified after send", "copilot_ui.py",
     "    sleep(settle)\n    if input_cleared(edit):\n        return True\n    for attempt", "    return True\n    for attempt",
     ["tests.test_desktop_ui.SubmitTests.test_retry_refocuses_then_clears",
      "tests.test_desktop_ui.SubmitTests.test_stuck_warns_after_three_retries"]),
    ("D3 retry without re-focus", "copilot_ui.py",
     "        focus_copilot(app_window)\n        try:\n            edit.set_focus()", "        try:\n            pass",
     ["tests.test_desktop_ui.SubmitTests.test_retry_refocuses_then_clears"]),
    ("D5 workspace file may set the custom backend URL", "neo_settings.py",
     "            if key in CUSTOM_KEYS and path == ws_path:", "            if False:",
     ["tests.test_custom_backend.SettingsCustomTests.test_workspace_file_cannot_set_custom_backend"]),
    ("D5 non-http custom_url accepted", "neo_settings.py",
     '        if u.scheme not in ("https", "http") or not u.hostname:', "        if False:",
     ["tests.test_custom_backend.SettingsCustomTests.test_non_http_url_refused"]),
    ("D5 login page not detected", "custom_driver.py",
     '        report("Signed in (not on a login page)", not is_login_url(url, self.url), url)',
     '        report("Signed in (not on a login page)", True, url)',
     ["tests.test_custom_backend.PreflightTests.test_stuck_on_login_fails_at_auth"]),
    ("D5 canary skipped", "custom_driver.py", "        if canary:\n", "        if False:\n",
     ["tests.test_custom_backend.PreflightTests.test_checklist_all_ok_and_reported_in_order",
      "tests.test_custom_backend.PreflightTests.test_no_reply_fails_canary"]),
    ("yolo auto-approves protected .agent-neo/.neo/.git paths", "neo_core.py",
     "        if self.auto_approve and not protected:",
     "        if self.auto_approve:",
     ["tests.test_core.CoreLoopTests.test_yolo_still_asks_for_protected_paths"]),
    ("Gemini URL detection broken", "gemini_web.py",
     '    return "gemini.google.com" in u and not _is_google_login_url(u)',
     '    return False',
     ["tests.test_gemini_driver.GeminiDriverTests.test_url_and_login_detection"]),
    ("DeepSeek URL detection broken", "deepseek_web.py",
     '    return "chat.deepseek.com" in u and not _is_deepseek_login_url(u)',
     '    return False',
     ["tests.test_deepseek_driver.DeepSeekDriverTests.test_url_and_login_detection"]),
    ("Consumer Copilot URL rejected by WebCopilotDriver", "copilot_web.py",
     '    return ("cloud.microsoft" in u and "chat" in u) or "copilot.microsoft.com" in u',
     '    return "cloud.microsoft" in u and "chat" in u',
     ["tests.test_web_driver.WebDriverTests.test_recognises_both_enterprise_m365_and_consumer_copilot_urls"]),
    ("D5 bubble_selector override ignored", "custom_driver.py",
     "        return [self.bubble_selector] if self.bubble_selector else BUBBLE_HEURISTICS", "        return BUBBLE_HEURISTICS",
     ["tests.test_custom_backend.PreflightTests.test_enter_used_without_send_button_and_selector_override"]),
]


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory(prefix="neo-negctl-") as tmp:
        for desc, rel, old, new, tests in MUTATIONS:
            work = Path(tmp) / "repo"
            if work.exists():
                shutil.rmtree(work)
            shutil.copytree(REPO, work, ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__", "node_modules"))
            target = work / rel
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"BROKEN CONTROL  {desc}: pattern found {text.count(old)} times in {rel}")
                failures += 1
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "unittest", *tests], cwd=work, capture_output=True, text=True)
            failed = r.returncode != 0 and ("FAILED" in r.stderr)
            print(f"{'ok (fails)' if failed else 'NOT CAUGHT'}  {desc}")
            if not failed:
                failures += 1
                print(r.stderr[-1500:])
    print(f"\n{len(MUTATIONS) - failures}/{len(MUTATIONS)} mutations caught")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
