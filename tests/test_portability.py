import json
import os
import base64
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import data_location
import dashboardctl
import compatibility_bridge
import integrations
import manage_service
import mcloud_patrol_adapter
import patrol_compat
import server
import waylume_paths


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.source = base / "current"
        self.target = base / "custom" / ".waylume"
        self.config = base / "config"
        self.legacy = base / "legacy"
        self.source.mkdir()
        (self.source / "meta.json").write_text(json.dumps({"schema_version": 2, "revision": 1, "records": []}), encoding="utf-8")
        (self.source / "events.jsonl").write_text('{"event":"fixture"}\n', encoding="utf-8")
        (self.source / "records").mkdir()
        (self.source / "records" / "attachment.txt").write_text("preserve", encoding="utf-8")
        self.patchers = [
            patch.dict(os.environ, {waylume_paths.DATA_ENV: ""}),
            patch.object(waylume_paths, "default_data_dir", return_value=self.source),
            patch.object(waylume_paths, "config_dir", return_value=self.config),
            patch.object(waylume_paths, "legacy_data_dir", return_value=self.legacy),
        ]
        for patcher in self.patchers:
            patcher.start()
        dashboardctl.refresh_data_paths()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_new_integrations_require_readonly_evidence_and_can_be_reported(self):
        for provider in ("jira", "linear"):
            self.assertNotEqual(integrations.status()["providers"][provider]["status"], "passed")
            payload = {"status": "passed", "tool": "fixture", "agent": "fixture", "read_only": True, "evidence_summary": "read-only check passed"}
            result = integrations.report_agent_check(provider, payload)["provider"]
            self.assertEqual(result["status"], "passed")
            self.assertTrue(result["checked_at"])
            self.assertEqual(integrations.status()["providers"][provider]["status"], "passed")

    def test_iphone_use_probe_reports_bundle_not_phone_readiness(self):
        home = Path(self.temp.name) / "codex"
        root = home / "plugins/cache/iphone-use-local/iphone-use/0.3.10"
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            self.assertEqual(integrations._probe_iphone_use()[0], "unknown")
            for name in (".codex-plugin/plugin.json", ".mcp.json", "server/iphone_use.py", "skills/iphone-use-setup/SKILL.md"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"name": "iphone-use"}) if name.endswith("plugin.json") else "{}")
            status, reason, missing, detail = integrations._probe_iphone_use()
            self.assertEqual((status, reason), ("passed", "plugin_files_present"))
            self.assertIn("宿主实际加载未验证", missing[0])
            self.assertIn("宿主", detail)

    def test_git_probe_checks_only_executable_without_accessing_repository(self):
        from types import SimpleNamespace
        ok = SimpleNamespace(returncode=0, stdout="git version 2.50.0\n")
        with patch.object(integrations, "_find_cli", return_value="/usr/bin/git"):
            with patch.object(integrations.subprocess, "run", return_value=ok) as run:
                status, reason, gaps, detail = integrations._probe_git()
                self.assertEqual((status, reason), ("passed", "readonly_probe_passed"))
                self.assertIn("仓库访问及远端权限未验证", detail)
                run.assert_called_once()
                self.assertEqual(run.call_args.args[0], ["/usr/bin/git", "--version"])
        with patch.object(integrations, "_find_cli", return_value=None):
            self.assertEqual(integrations._probe_git()[0], "unknown")

    def test_cli_probes_resolve_user_bin_and_pass_runtime_path(self):
        from types import SimpleNamespace
        fake_home = Path(self.temp.name) / "home"
        bin_dir = fake_home / ".local/bin"
        bin_dir.mkdir(parents=True)
        for command in ("lark-cli", "kdocs-cli"):
            (bin_dir / command).touch(mode=0o755)
        result = SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "checks": [{"name": "network", "status": "pass"}]}))
        with patch.object(integrations.Path, "home", return_value=fake_home), patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            self.assertEqual(integrations._find_cli("lark-cli"), str(bin_dir / "lark-cli"))
            self.assertEqual(integrations._find_cli("kdocs-cli"), str(bin_dir / "kdocs-cli"))
            with patch.object(integrations.subprocess, "run", return_value=result) as run:
                self.assertEqual(integrations._probe_feishu()[0], "passed")
                path = run.call_args.kwargs["env"]["PATH"]
                self.assertIn(str(bin_dir), path.split(os.pathsep))
                self.assertIn("/usr/local/bin", path.split(os.pathsep))
                self.assertEqual(integrations._probe_kdocs()[0], "passed")

    def test_legacy_unprobeable_host_result_is_unknown(self):
        path = integrations._status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"tencent_docs": {"status": "not_configured", "checked_at": "2026-11-05T00:00:00+08:00", "verification_method": "local_readonly_probe", "reason_code": "host_connector_not_probeable"}}))
        result = integrations.status()["providers"]["tencent_docs"]
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["checked_at"])

    def test_tencent_probe_requires_account_check_and_redacts_raw_output(self):
        from types import SimpleNamespace
        plugin = self.root_for_tencent_probe()
        with patch.dict(os.environ, {"CODEX_HOME": str(plugin)}), patch.object(integrations.shutil, "which", return_value="node"):
            for output, expected in (("Connection OK. Tools discovered: 227; account checked.\n", "passed"),
                                     ("Connection OK. Tools discovered: 227; discovery only.\n", "unknown"),
                                     ("unexpected token=secret", "unknown")):
                with patch.object(integrations.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=output)):
                    result = integrations._probe_tencent_docs()
                    self.assertEqual(result[0], expected)
                    self.assertNotIn("secret", str(result))

    def test_tencent_probe_uses_pinned_node_when_service_path_is_empty(self):
        from types import SimpleNamespace
        plugin = self.root_for_tencent_probe()
        runtime = Path(self.temp.name) / "node"
        runtime.touch(mode=0o755)
        config = plugin / "plugins/cache/akashmahedy-plugins/tencent-docs/1.2.2/.mcp.json"
        config.write_text(json.dumps({"mcpServers": {"tencent_docs": {"command": str(runtime)}}}))
        with patch.dict(os.environ, {"CODEX_HOME": str(plugin)}), patch.object(integrations.shutil, "which", return_value=None):
            with patch.object(integrations.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="Connection OK. Tools discovered: 227; account checked.")) as run:
                self.assertEqual(integrations._probe_tencent_docs()[0], "passed")
                self.assertEqual(run.call_args.args[0][0], str(runtime))

    def root_for_tencent_probe(self):
        root = Path(self.temp.name) / "codex"
        script = root / "plugins/cache/akashmahedy-plugins/tencent-docs/1.2.2/scripts/setup.mjs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.touch()
        return root

    def test_local_check_preserves_host_verified_connection(self):
        for provider in ("figma",):
            reported = integrations.report_agent_check(provider, {"status": "passed", "tool": "fixture", "agent": "fixture", "read_only": True, "evidence_summary": "read-only check passed"})["provider"]
            checked = integrations.check(provider)
            self.assertEqual(checked["check_outcome"], "host_agent_required")
            self.assertEqual(checked["provider"], reported)
            self.assertEqual(integrations.status()["providers"][provider], reported)

    def test_migration_requires_preview_token_and_preserves_source(self):
        revision = data_location.location()["revision"]
        preflight = data_location.preview(str(self.target), revision)
        self.assertTrue(preflight["confirmation_required"])
        result = data_location.migrate(str(self.target), preflight["preview_id"], preflight["confirmation_token"], revision)
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["verification"]["verified"])
        self.assertEqual(data_location.location()["current_path"], str(self.target.resolve()))
        self.assertEqual((self.source / "events.jsonl").read_text(encoding="utf-8"), '{"event":"fixture"}\n')
        self.assertEqual((self.target / "events.jsonl").read_text(encoding="utf-8"), '{"event":"fixture"}\n')

    def test_nonempty_target_is_not_overwritten(self):
        self.target.mkdir(parents=True)
        marker = self.target / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
        result = data_location.preview(str(self.target))
        self.assertFalse(result["can_migrate"])
        self.assertTrue(result["target_exists"])
        self.assertFalse(result["target_empty"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def _preview_and_migrate(self, target=None):
        revision = data_location.location()["revision"]
        info = data_location.preview(str(target or self.target), revision)
        return data_location.migrate(str(target or self.target), info["preview_id"], info["confirmation_token"], revision)

    def _register_dashboard_bridge(self):
        project = Path(self.temp.name) / "project"
        project.mkdir()
        (project / "dashboardctl.py").write_text("# fixture", encoding="utf-8")
        bridge = Path(self.temp.name) / "legacy" / "dashboard"
        compatibility_bridge.create_dashboard_bridge(bridge, project, self.source)
        return bridge

    def test_refresh_failure_rolls_back_config_and_preserves_verified_copy(self):
        original_refresh = dashboardctl.refresh_data_paths
        calls = {"count": 0}
        def fail_once():
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("injected_refresh_failure")
            return original_refresh()
        with patch.object(dashboardctl, "refresh_data_paths", side_effect=fail_once):
            result = self._preview_and_migrate()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(waylume_paths.data_dir(), self.source.resolve())
        self.assertTrue((self.target.parent / f".{self.target.name}.failed-copy-{result['job_id']}").is_dir())
        self.assertEqual((self.source / "events.jsonl").read_text(encoding="utf-8"), '{"event":"fixture"}\n')
        self.assertFalse(waylume_paths.migration_journal_path().exists())

    def test_crash_after_copy_stage_recovers_without_switching_settings(self):
        def crash_after_partial_copy(source, stage, **kwargs):
            stage.mkdir(mode=0o700)
            (stage / "partial.bin").write_bytes(b"partial")
            raise SystemExit("injected_copy_crash")
        with patch.object(data_location, "_copy_data", side_effect=crash_after_partial_copy):
            with self.assertRaises(SystemExit):
                self._preview_and_migrate()
        journal = json.loads(waylume_paths.migration_journal_path().read_text(encoding="utf-8"))
        # A stale journal may have the same PID after an earlier request in a
        # long-running service. Recovery must key off the live flock, not PID.
        self.assertEqual(waylume_paths.data_dir(), self.source.resolve())
        self.assertFalse(Path(journal["stage_path"]).exists())
        self.assertFalse(waylume_paths.migration_journal_path().exists())

    def test_dashboard_cli_and_server_resolve_migrated_data_root(self):
        result = self._preview_and_migrate()
        self.assertEqual(result["status"], "verified")
        dashboardctl.refresh_data_paths()
        server.refresh_data_paths()
        self.assertEqual(dashboardctl.ROOT, self.target.resolve())
        self.assertEqual(server.ROOT, self.target.resolve())
        self.assertEqual(dashboardctl.load_store()["revision"], 1)

    def test_legacy_mixed_root_migrates_data_only_and_reports_excluded_code(self):
        self.legacy.mkdir()
        (self.legacy / "meta.json").write_text(json.dumps({"schema_version": 2, "revision": 7, "records": []}), encoding="utf-8")
        (self.legacy / "records").mkdir()
        (self.legacy / "records" / "keep.txt").write_text("attachment", encoding="utf-8")
        (self.legacy / "server.py").write_text("source", encoding="utf-8")
        (self.legacy / "web").mkdir()
        (self.legacy / "web" / "index.html").write_text("ui", encoding="utf-8")
        (self.legacy / "mcloud-bug-monitor" / "state").mkdir(parents=True)
        (self.legacy / "mcloud-bug-monitor" / "state" / "inspection-slots.lock").touch()
        (self.legacy / "mcloud-bug-monitor" / "state.json").write_text('{"seen": ["BUG-1"]}', encoding="utf-8")
        (self.legacy / "mcloud-bug-monitor" / "state" / "inspection-slots.json").write_text('{"active_run": null}', encoding="utf-8")
        patrol_alias = self.legacy.parent / "legacy-patrol-entry"
        patrol_alias.symlink_to(self.legacy / "mcloud-bug-monitor", target_is_directory=True)
        compatibility_bridge.register_data_path_link(patrol_alias, "mcloud-bug-monitor", self.legacy)
        empty_default = self.source.parent / "empty-default"
        empty_default.mkdir()
        with patch.object(waylume_paths, "default_data_dir", return_value=empty_default), \
             patch.object(waylume_paths, "legacy_data_dir", return_value=self.legacy):
            dashboardctl.refresh_data_paths()
            data_location._previews.clear()
            info = data_location.preview(str(self.target))
            self.assertEqual(info["source_kind"], "legacy_mixed_dashboard")
            self.assertEqual(info["excluded_legacy_roots"], ["server.py", "web"])
            result = data_location.migrate(str(self.target), info["preview_id"], info["confirmation_token"], info["revision"])
        self.assertEqual(result["status"], "verified")
        self.assertTrue((self.target / "records" / "keep.txt").is_file())
        self.assertFalse((self.target / "server.py").exists())
        self.assertFalse((self.target / "web").exists())
        self.assertEqual((self.target / "mcloud-bug-monitor" / "state.json").read_text(encoding="utf-8"), '{"seen": ["BUG-1"]}')
        self.assertTrue((self.target / "mcloud-bug-monitor" / "state" / "inspection-slots.json").is_file())
        self.assertTrue((self.legacy / "server.py").is_file())

    def test_patrol_only_current_root_is_detected_and_migrated(self):
        for child in list(self.source.iterdir()):
            if child.is_dir():
                import shutil
                shutil.rmtree(child)
            else:
                child.unlink()
        monitor = self.source / "mcloud-bug-monitor"
        (monitor / "state").mkdir(parents=True)
        (monitor / "state" / "inspection-slots.lock").touch()
        (monitor / "state" / "inspection-slots.json").write_text('{"active_run":null}', encoding="utf-8")
        (monitor / "state.json").write_text('{"seen": ["BUG-2"]}', encoding="utf-8")
        (monitor / "inbox" ).mkdir()
        (monitor / "inbox" / "pending.json").write_text('{"id":"BUG-3"}', encoding="utf-8")
        patrol_alias = self.legacy.parent / "patrol-entry"
        patrol_alias.symlink_to(monitor, target_is_directory=True)
        compatibility_bridge.register_data_path_link(patrol_alias, "mcloud-bug-monitor", self.source)
        self.assertTrue(data_location._has_data(self.source))
        result = self._preview_and_migrate()
        self.assertEqual(result["status"], "verified")
        self.assertEqual((self.target / "mcloud-bug-monitor" / "state.json").read_text(encoding="utf-8"), '{"seen": ["BUG-2"]}')
        self.assertEqual((self.target / "mcloud-bug-monitor" / "inbox" / "pending.json").read_text(encoding="utf-8"), '{"id":"BUG-3"}')

    def test_active_patrol_run_blocks_data_snapshot_and_keeps_source(self):
        monitor = self.source / "mcloud-bug-monitor"
        (monitor / "state").mkdir(parents=True)
        (monitor / "state" / "inspection-slots.lock").touch()
        (monitor / "state" / "inspection-slots.json").write_text('{"active_run":{"id":"running"}}', encoding="utf-8")
        (monitor / "state.json").write_text('{"keep":true}', encoding="utf-8")
        patrol_alias = self.legacy.parent / "active-patrol-entry"
        patrol_alias.symlink_to(monitor, target_is_directory=True)
        compatibility_bridge.register_data_path_link(patrol_alias, "mcloud-bug-monitor", self.source)
        result = self._preview_and_migrate()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(self.source.is_dir())
        self.assertEqual((self.source / "mcloud-bug-monitor" / "state.json").read_text(encoding="utf-8"), '{"keep":true}')
        self.assertFalse(self.target.exists())

    def test_only_registered_optional_subtrees_are_migrated_from_mixed_root(self):
        self.legacy.mkdir()
        (self.legacy / "meta.json").write_text('{"schema_version":2,"revision":1,"records":[]}', encoding="utf-8")
        (self.legacy / "records").mkdir()
        registered = self.legacy / "sample-addon-state"
        registered.mkdir()
        (registered / "state.json").write_text('{"preserve":true}', encoding="utf-8")
        unregistered = self.legacy / "unregistered-addon-state"
        unregistered.mkdir()
        (unregistered / "private.txt").write_text("exclude", encoding="utf-8")
        alias = self.legacy.parent / "sample-addon-entry"
        alias.symlink_to(registered, target_is_directory=True)
        compatibility_bridge.register_data_path_link(alias, "sample-addon-state", self.legacy)
        empty_default = self.source.parent / "empty-default"
        empty_default.mkdir()
        with patch.object(waylume_paths, "default_data_dir", return_value=empty_default), \
             patch.object(waylume_paths, "legacy_data_dir", return_value=self.legacy):
            dashboardctl.refresh_data_paths()
            data_location._previews.clear()
            info = data_location.preview(str(self.target))
            self.assertNotIn("sample-addon-state", info["excluded_legacy_roots"])
            self.assertIn("unregistered-addon-state", info["excluded_legacy_roots"])
            result = data_location.migrate(str(self.target), info["preview_id"], info["confirmation_token"], info["revision"])
        self.assertEqual(result["status"], "verified")
        self.assertEqual((self.target / "sample-addon-state" / "state.json").read_text(encoding="utf-8"), '{"preserve":true}')
        self.assertFalse((self.target / "unregistered-addon-state").exists())

    def test_target_patrol_lock_is_held_before_legacy_alias_retarget(self):
        monitor = self.source / "mcloud-bug-monitor"
        (monitor / "state").mkdir(parents=True)
        (monitor / "state" / "inspection-slots.lock").touch()
        (monitor / "state" / "inspection-slots.json").write_text('{"active_run":null}', encoding="utf-8")
        (monitor / "state.json").write_text('{"keep":true}', encoding="utf-8")
        alias = self.legacy / "bug-monitor"
        alias.parent.mkdir(parents=True)
        alias.symlink_to(monitor, target_is_directory=True)
        compatibility_bridge.register_data_path_link(alias, "mcloud-bug-monitor", self.source)
        original_refresh = compatibility_bridge.refresh_registered_bridges
        observed = []

        def inspect_target_lock(data_root, **kwargs):
            if Path(data_root).resolve() == self.target.resolve():
                try:
                    with patrol_compat._inactive_slot(self.target / "mcloud-bug-monitor"):
                        observed.append("unexpectedly_unlocked")
                except ValueError as exc:
                    observed.append(str(exc))
            return original_refresh(data_root, **kwargs)

        with patch.object(compatibility_bridge, "refresh_registered_bridges", side_effect=inspect_target_lock):
            result = self._preview_and_migrate()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(observed, ["inspection_slot_busy"])
        self.assertEqual(alias.resolve(), (self.target / "mcloud-bug-monitor").resolve())

    def test_patrol_journal_is_recovered_before_environment_data_override_returns(self):
        journal = self.config / patrol_compat.JOURNAL_NAME
        self.config.mkdir(parents=True, exist_ok=True)
        journal.write_text('{"pending": true}', encoding="utf-8")
        calls = []
        with patch.dict(os.environ, {waylume_paths.DATA_ENV: str(self.target)}), \
             patch.object(patrol_compat, "recover", side_effect=lambda **kwargs: calls.append(kwargs) or {"recovered": False}):
            self.assertEqual(waylume_paths.data_dir(), self.target.resolve())
        self.assertEqual(calls, [{"migration_lock_held": True}])

    def test_server_starts_without_optional_patrol_module_when_no_transaction_exists(self):
        with patch.object(waylume_paths, "config_dir", return_value=self.config), \
             patch.dict("sys.modules", {"patrol_compat": None}):
            self.assertEqual(server._recover_startup_data(), {
                "recovered": False, "reason": "optional_integration_not_configured"})

    def test_missing_patrol_module_does_not_hide_pending_transaction(self):
        self.config.mkdir(parents=True, exist_ok=True)
        (self.config / patrol_compat.JOURNAL_NAME).write_text("{}", encoding="utf-8")
        with patch.object(waylume_paths, "config_dir", return_value=self.config), \
             patch.dict("sys.modules", {"patrol_compat": None}):
            with self.assertRaisesRegex(ValueError, "optional_integration_recovery_unavailable"):
                server._recover_startup_data()

    def test_patrol_adapter_prefers_registered_legacy_entry_and_keeps_fallback(self):
        monitor = self.source / "mcloud-bug-monitor"
        monitor.mkdir()
        alias = self.legacy.parent / "registered-patrol-entry"
        alias.symlink_to(monitor, target_is_directory=True)
        compatibility_bridge.register_data_path_link(alias, "mcloud-bug-monitor", self.source)
        with patch.object(waylume_paths, "data_dir", return_value=self.source), \
             patch.object(waylume_paths, "legacy_data_dir", return_value=self.legacy):
            self.assertEqual(mcloud_patrol_adapter.resolve_paths().patrol_root, compatibility_bridge._secure_link_path(alias))
        compatibility_bridge.unregister_data_path_link(alias, "mcloud-bug-monitor", self.source)
        with patch.object(waylume_paths, "data_dir", return_value=self.source), \
             patch.object(waylume_paths, "legacy_data_dir", return_value=self.legacy):
            self.assertEqual(mcloud_patrol_adapter.resolve_paths().patrol_root, self.legacy.parent / "bug-monitor")

    def test_service_runs_patrol_recovery_before_http_acceptance(self):
        events = []

        class FakeHTTPServer:
            daemon_threads = False
            def __init__(self, *args, **kwargs):
                events.append("http")
            def serve_forever(self, **kwargs):
                raise KeyboardInterrupt()
            def server_close(self):
                events.append("close")

        class FakeThread:
            def __init__(self, *args, **kwargs): pass
            def start(self): pass

        with patch.object(waylume_paths, "service_lock_path", return_value=self.config / "service.lock"), \
             patch.object(server, "_recover_startup_data", side_effect=lambda: events.append("recover") or {"recovered": False}), \
             patch.object(server, "ThreadingHTTPServer", FakeHTTPServer), \
             patch.object(server.threading, "Thread", FakeThread):
            server.main()
        self.assertEqual(events[:2], ["recover", "http"])

    def test_crash_after_target_install_finishes_verified_config_switch(self):
        with patch.object(waylume_paths, "write_data_dir", side_effect=SystemExit("injected_config_crash")):
            with self.assertRaises(SystemExit):
                self._preview_and_migrate()
        journal = json.loads(waylume_paths.migration_journal_path().read_text(encoding="utf-8"))
        journal["owner_pid"] = os.getpid() + 1
        waylume_paths.write_migration_journal(journal)
        self.assertEqual(waylume_paths.data_dir(), self.target.resolve())
        self.assertEqual(json.loads(waylume_paths.settings_path().read_text(encoding="utf-8"))["data_dir"], str(self.target.resolve()))
        self.assertFalse(waylume_paths.migration_journal_path().exists())

    def test_crash_after_bridge_switch_recovers_config_and_bridge_to_verified_target(self):
        bridge = self._register_dashboard_bridge()
        original_refresh = compatibility_bridge.refresh_registered_bridges

        def refresh_then_crash(data_root, **kwargs):
            result = original_refresh(data_root, **kwargs)
            if Path(data_root).resolve() == self.target.resolve():
                raise SystemExit("injected_crash_after_bridge_switch")
            return result

        with patch.object(compatibility_bridge, "refresh_registered_bridges", side_effect=refresh_then_crash):
            with self.assertRaisesRegex(SystemExit, "injected_crash_after_bridge_switch"):
                self._preview_and_migrate()

        self.assertTrue(waylume_paths.migration_journal_path().is_file())
        self.assertEqual(compatibility_bridge._link_target(bridge / "records"), (self.target / "records").resolve())
        self.assertEqual(waylume_paths.data_dir(), self.target.resolve())
        self.assertEqual(compatibility_bridge._link_target(bridge / "records"), (self.target / "records").resolve())
        self.assertFalse(waylume_paths.migration_journal_path().exists())
        self.assertTrue(self.source.is_dir(), "the original data remains available as rollback copy")
        self.assertEqual((bridge / "records" / "attachment.txt").read_text(encoding="utf-8"), "preserve")

    def test_recovery_keeps_journal_until_bridge_refresh_succeeds(self):
        bridge = self._register_dashboard_bridge()
        with patch.object(waylume_paths, "write_data_dir", side_effect=SystemExit("injected_crash_before_config_switch")):
            with self.assertRaisesRegex(SystemExit, "injected_crash_before_config_switch"):
                self._preview_and_migrate()
        self.assertTrue(waylume_paths.migration_journal_path().is_file())
        self.assertEqual(compatibility_bridge._link_target(bridge / "records"), (self.source / "records").resolve())

        with patch.object(compatibility_bridge, "refresh_registered_bridges", side_effect=RuntimeError("injected_bridge_refresh_failure")):
            with self.assertRaisesRegex(RuntimeError, "injected_bridge_refresh_failure"):
                waylume_paths.data_dir()
        self.assertTrue(waylume_paths.migration_journal_path().is_file(), "recovery must remain retryable")

        self.assertEqual(waylume_paths.data_dir(), self.target.resolve())
        self.assertEqual(compatibility_bridge._link_target(bridge / "records"), (self.target / "records").resolve())
        self.assertFalse(waylume_paths.migration_journal_path().exists())

    def test_crash_recovery_quarantines_bad_copy_and_restores_bridge_to_source(self):
        bridge = self._register_dashboard_bridge()
        original_refresh = compatibility_bridge.refresh_registered_bridges

        def refresh_then_crash(data_root, **kwargs):
            result = original_refresh(data_root, **kwargs)
            if Path(data_root).resolve() == self.target.resolve():
                raise SystemExit("injected_crash_after_bridge_switch")
            return result

        with patch.object(compatibility_bridge, "refresh_registered_bridges", side_effect=refresh_then_crash):
            with self.assertRaisesRegex(SystemExit, "injected_crash_after_bridge_switch"):
                self._preview_and_migrate()

        (self.target / "events.jsonl").write_text("corrupted after simulated crash", encoding="utf-8")
        self.assertEqual(waylume_paths.data_dir(), self.source.resolve())
        self.assertEqual(compatibility_bridge._link_target(bridge / "records"), (self.source / "records").resolve())
        self.assertEqual((bridge / "records" / "attachment.txt").read_text(encoding="utf-8"), "preserve")
        self.assertTrue(self.source.is_dir(), "rollback must preserve the original data")
        quarantine = self.target.parent / f".{self.target.name}.failed-copy-"
        self.assertTrue(any(self.target.parent.glob(quarantine.name + "*")))
        self.assertFalse(waylume_paths.migration_journal_path().exists())

    def test_rollback_accepts_journal_target_when_registry_still_names_source(self):
        bridge = self._register_dashboard_bridge()
        original_retarget = compatibility_bridge._retarget
        calls = {"count": 0}

        def retarget_then_crash(link_path, old_target, new_target):
            original_retarget(link_path, old_target, new_target)
            calls["count"] += 1
            if calls["count"] == 1:
                raise SystemExit("injected_crash_mid_migration_bridge_switch")

        with patch.object(compatibility_bridge, "_retarget", side_effect=retarget_then_crash):
            with self.assertRaisesRegex(SystemExit, "injected_crash_mid_migration_bridge_switch"):
                self._preview_and_migrate()

        journal = json.loads(waylume_paths.migration_journal_path().read_text(encoding="utf-8"))
        self.assertEqual(journal["state"], "settings_switched")
        self.assertEqual(compatibility_bridge._read_registry()["bridges"][0]["data_root"], str(self.source.resolve()))
        self.assertEqual(compatibility_bridge._link_target(bridge / "records"), (self.target / "records").resolve())
        (self.target / "events.jsonl").write_text("corrupted", encoding="utf-8")

        self.assertEqual(waylume_paths.data_dir(), self.source.resolve())
        for entry in compatibility_bridge.DATA_LINKS:
            self.assertEqual(compatibility_bridge._link_target(bridge / entry), (self.source / entry).resolve())
        self.assertEqual(compatibility_bridge._read_registry()["bridges"][0]["data_root"], str(self.source.resolve()))
        self.assertTrue(self.source.is_dir())
        self.assertFalse(waylume_paths.migration_journal_path().exists())

    def test_patrol_alias_partial_target_link_rolls_back_from_migration_journal(self):
        source_patrol = self.source / "mcloud-bug-monitor"
        (source_patrol / "state").mkdir(parents=True)
        (source_patrol / "state" / "inspection-slots.lock").touch()
        (source_patrol / "state" / "inspection-slots.json").write_text('{"active_run":null}', encoding="utf-8")
        (source_patrol / "state.json").write_text('{"state":"old"}', encoding="utf-8")
        alias = Path(self.temp.name) / "legacy" / "bug-monitor"
        alias.parent.mkdir()
        alias.symlink_to(source_patrol, target_is_directory=True)
        compatibility_bridge.register_data_path_link(alias, "mcloud-bug-monitor", self.source)
        original_retarget = compatibility_bridge._retarget
        calls = {"count": 0}

        def retarget_then_crash(link_path, old_target, new_target):
            original_retarget(link_path, old_target, new_target)
            calls["count"] += 1
            if calls["count"] == 1:
                raise SystemExit("injected_crash_after_patrol_alias_switch")

        with patch.object(compatibility_bridge, "_retarget", side_effect=retarget_then_crash):
            with self.assertRaisesRegex(SystemExit, "injected_crash_after_patrol_alias_switch"):
                self._preview_and_migrate()

        journal = json.loads(waylume_paths.migration_journal_path().read_text(encoding="utf-8"))
        self.assertEqual(journal["state"], "settings_switched")
        self.assertEqual(alias.resolve(), (self.target / "mcloud-bug-monitor").resolve())
        (self.target / "events.jsonl").write_text("corrupted", encoding="utf-8")

        self.assertEqual(waylume_paths.data_dir(), self.source.resolve())
        self.assertEqual(alias.resolve(), source_patrol.resolve())
        self.assertEqual((alias / "state.json").read_text(encoding="utf-8"), '{"state":"old"}')
        self.assertTrue(source_patrol.is_dir())
        self.assertFalse(waylume_paths.migration_journal_path().exists())

    def test_corrupt_installed_target_is_quarantined_and_source_remains_authoritative(self):
        with patch.object(waylume_paths, "write_data_dir", side_effect=SystemExit("injected_config_crash")):
            with self.assertRaises(SystemExit):
                self._preview_and_migrate()
        journal = json.loads(waylume_paths.migration_journal_path().read_text(encoding="utf-8"))
        self.assertEqual(journal["state"], "target_installed")
        (self.target / "events.jsonl").write_text("corrupt", encoding="utf-8")
        self.assertEqual(waylume_paths.data_dir(), self.source.resolve())
        quarantine = self.target.parent / f".{self.target.name}.failed-copy-{journal['job_id']}"
        self.assertEqual((quarantine / "events.jsonl").read_text(encoding="utf-8"), "corrupt")
        self.assertFalse(self.target.exists())
        self.assertFalse(waylume_paths.migration_journal_path().exists())

    def test_data_paths_reject_source_tree_and_ancestors(self):
        with self.assertRaisesRegex(ValueError, "unsafe_data_path"):
            waylume_paths.validate_data_dir(waylume_paths.CODE_ROOT)
        with self.assertRaisesRegex(ValueError, "unsafe_data_path"):
            waylume_paths.validate_data_dir(waylume_paths.CODE_ROOT.parent)
        with patch.dict(os.environ, {waylume_paths.DATA_ENV: str(waylume_paths.CODE_ROOT)}):
            with self.assertRaisesRegex(ValueError, "unsafe_data_path"):
                waylume_paths.data_dir()

    def test_host_agent_evidence_is_timestamped_and_secrets_rejected(self):
        reported = integrations.report_agent_check("figma", {
            "status": "passed", "tool": "figma-mcp", "agent": "host-agent",
            "read_only": True, "evidence_summary": "只读获取目标文件元信息成功",
            "missing_capabilities": [], "reason_code": "readonly_probe_passed",
        })["provider"]
        self.assertEqual(reported["status"], "passed")
        self.assertIsNotNone(reported["checked_at"])
        self.assertTrue(reported["evidence"]["read_only"])
        with self.assertRaisesRegex(ValueError, "passed_requires_readonly_evidence"):
            integrations.report_agent_check("figma", {"status": "passed", "tool": "mcp", "agent": "agent", "read_only": False, "evidence_summary": "ok"})
        with self.assertRaisesRegex(ValueError, "unsafe_integration_evidence"):
            integrations.report_agent_check("figma", {"status": "failed", "tool": "mcp", "agent": "agent", "read_only": True, "evidence_summary": "token=private-value"})

    def test_figma_prompt_routes_to_host_agent_readonly_probe(self):
        status = integrations._blank("figma")
        self.assertEqual(status["reason_code"], "host_agent_check_required")
        self.assertIn("宿主 Agent", status["prompt_context"])
        self.assertIn("只读 MCP", status["prompt_context"])
        self.assertIn("不能直接探测或调用宿主 MCP", status["prompt_context"])
        self.assertIn("指定文件/节点的读取权限", status["missing_capabilities"])
        self.assertIn("宿主 Agent 检查当前会话", status["safe_next_step"])

    def test_weekday_inspection_schedule_is_read_only_and_reported(self):
        config = self.config / "automation.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            'status = "ACTIVE"\n'
            'rrule = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8,9,20;BYMINUTE=30"\n'
            'prompt = "Read /home/example/.codex/automations/bug-2 first; timezone Asia/Shanghai"\n', encoding="utf-8")
        with patch.object(server, "INSPECTION_CONFIG", config):
            schedule = server.inspection_schedule()
        self.assertTrue(schedule["available"])
        self.assertEqual(schedule["timezone"], "Asia/Shanghai")
        self.assertEqual(schedule["weekdays"], ["MO", "TU", "WE", "TH", "FR"])
        self.assertEqual(schedule["times"], ["08:30", "09:30", "20:30"])

    def test_inspection_schedule_uses_established_timezone_fallback_when_prompt_has_paths(self):
        config = self.config / "automation-no-timezone.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            'status = "ACTIVE"\n'
            'rrule = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8,9,20;BYMINUTE=30"\n'
            'prompt = "Read /home/example/.codex/automations/bug-2 first"\n', encoding="utf-8")
        with patch.object(server, "INSPECTION_CONFIG", config):
            schedule = server.inspection_schedule()
        self.assertTrue(schedule["available"])
        self.assertEqual(schedule["timezone"], server.INSPECTION_ZONE)

    def test_manager_refuses_second_service_when_legacy_is_loaded(self):
        result = type("Result", (), {"returncode": 0})()
        with patch.object(manage_service, "_launchctl", return_value=result):
            self.assertTrue(manage_service._legacy_dashboard_is_loaded())

    def test_service_manager_recognizes_owned_legacy_label(self):
        agents = Path(self.temp.name) / "LaunchAgents"
        project = Path(self.temp.name) / "project"
        agents.mkdir(); project.mkdir()
        server_path = project / "server.py"
        server_path.write_text("# fixture", encoding="utf-8")
        plist = agents / (manage_service.LEGACY_LABEL + ".plist")
        plist.write_bytes(plistlib.dumps({
            "Label": manage_service.LEGACY_LABEL,
            "ProgramArguments": ["/usr/bin/python3", str(server_path)],
        }))
        owned, label = manage_service._owned_service(agents, project)
        self.assertEqual(owned, plist)
        self.assertEqual(label, manage_service.LEGACY_LABEL)

        result = type("Result", (), {"returncode": 0})()
        with patch.object(manage_service, "paths", return_value=(Path(self.temp.name), agents, project, self.source)), \
             patch.object(manage_service, "_launchctl", return_value=result) as launchctl:
            manage_service.control("stop", type("Args", (), {"home": True})())
        self.assertEqual(launchctl.call_args.args, ("kill", "SIGTERM", f"gui/{os.getuid()}/{manage_service.LEGACY_LABEL}"))

    def test_data_location_reports_only_manifest_verified_backup(self):
        self.legacy.mkdir()
        (self.legacy / "server.py").write_text("compatibility runtime", encoding="utf-8")
        (self.legacy / "records").symlink_to(self.source / "records", target_is_directory=True)
        expected = data_location._file_manifest(self.source)[2]
        archived = self.legacy.parent / (self.legacy.name + ".full-backup-20261105-010203.original")
        archived.mkdir()
        (archived / "meta.json").write_text((self.source / "meta.json").read_text(encoding="utf-8"), encoding="utf-8")
        (archived / "records").mkdir()
        (archived / "records" / "attachment.txt").write_text("preserve", encoding="utf-8")
        (archived / "events.jsonl").write_text((self.source / "events.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        (archived / "server.py").write_text("old code is retained but excluded from data hash", encoding="utf-8")
        waylume_paths._atomic_bytes(waylume_paths.settings_path(), json.dumps({
            "data_dir": str(self.source),
            "last_migration": {"backup_path": str(self.legacy), "manifest_sha256": expected},
        }).encode("utf-8"))
        with patch.object(waylume_paths, "legacy_data_dir", return_value=self.legacy):
            self.assertEqual(data_location.location()["backup_path"], str(archived.resolve()))

        # A mismatching or ambiguous archive must never be advertised as safe.
        (archived / "records" / "attachment.txt").write_text("different", encoding="utf-8")
        with patch.object(waylume_paths, "legacy_data_dir", return_value=self.legacy):
            self.assertIsNone(data_location.location()["backup_path"])


if __name__ == "__main__":
    unittest.main()
