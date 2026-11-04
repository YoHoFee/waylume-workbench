from __future__ import annotations

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import manage_service
import waylume_paths


class ManageServiceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.agents = self.home / "Library" / "LaunchAgents"
        self.project = self.root / "source"
        self.data = self.root / "data"
        self.agents.mkdir(parents=True)
        self.project.mkdir()
        self.data.mkdir()
        (self.project / "server.py").write_text("# isolated fixture\n", encoding="utf-8")
        self.legacy_plist = self.agents / (manage_service.LEGACY_LABEL + ".plist")
        self.original = {
            "Label": manage_service.LEGACY_LABEL,
            "ProgramArguments": ["/usr/bin/python3", "/old/dashboard/server.py"],
            "WorkingDirectory": "/old/dashboard",
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "StartCalendarInterval": [{"Weekday": 1, "Hour": 8, "Minute": 30}],
            "EnvironmentVariables": {"WAYLUME_DATA_DIR": "/stale/data", "CUSTOM": "kept"},
        }
        self.original_bytes = plistlib.dumps(self.original, fmt=plistlib.FMT_XML)
        self.legacy_plist.write_bytes(self.original_bytes)
        self.args = SimpleNamespace(
            home=str(self.home), launch_agents_dir=str(self.agents),
            project=str(self.project), data_dir=None,
            legacy_plist=None, backup_plist=None,
        )
        self.config = self.root / "config"
        self.config.mkdir()
        self.settings = self.config / "settings.json"
        self.settings.write_text('{"data_dir": ' + repr(str(self.data)).replace("'", '"') + "}\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_migrate_and_rollback_preserves_single_label_and_schedule(self):
        launch_calls = []

        def fake_launchctl(*args):
            launch_calls.append(args)
            return SimpleNamespace(returncode=0)

        with patch.object(manage_service.sys, "platform", "darwin"), \
             patch.object(manage_service, "_launchctl", side_effect=fake_launchctl), \
             patch.object(waylume_paths, "config_dir", return_value=self.config), \
             patch.object(waylume_paths, "default_data_dir", return_value=self.data):
            result = manage_service.migrate_legacy_in_place(self.args)
            migrated = plistlib.loads(self.legacy_plist.read_bytes())
            self.assertEqual(migrated["Label"], manage_service.LEGACY_LABEL)
            self.assertEqual(migrated["ProgramArguments"], [sys.executable, str((self.project / "server.py").resolve())])
            self.assertEqual(migrated["StartCalendarInterval"], self.original["StartCalendarInterval"])
            self.assertEqual(migrated["KeepAlive"], self.original["KeepAlive"])
            self.assertEqual(migrated["EnvironmentVariables"], {"CUSTOM": "kept", "PYTHONUNBUFFERED": "1"})
            self.assertEqual(Path(result["backup"]).read_bytes(), self.original_bytes)
            self.assertTrue((self.data / "logs").is_dir())
            self.assertFalse((self.args.home and Path(self.args.launch_agents_dir) / (manage_service.LABEL + ".plist")).exists())

            self.args.backup_plist = result["backup"]
            rollback = manage_service.rollback_legacy_in_place(self.args)
            self.assertEqual(self.legacy_plist.read_bytes(), self.original_bytes)
            self.assertTrue(Path(rollback["backup"]).is_file())
            self.assertTrue(any(call[0] == "bootout" for call in launch_calls))
            self.assertGreaterEqual(sum(call[0] == "bootstrap" for call in launch_calls), 2)

    def test_bootstrap_failure_restores_original_plist_and_keeps_backup(self):
        bootstrap_count = 0

        def fake_launchctl(*args):
            nonlocal bootstrap_count
            if args[0] == "bootstrap":
                bootstrap_count += 1
                return SimpleNamespace(returncode=1 if bootstrap_count == 1 else 0)
            return SimpleNamespace(returncode=0)

        with patch.object(manage_service.sys, "platform", "darwin"), \
             patch.object(manage_service, "_launchctl", side_effect=fake_launchctl), \
             patch.object(waylume_paths, "config_dir", return_value=self.config), \
             patch.object(waylume_paths, "default_data_dir", return_value=self.data):
            with self.assertRaisesRegex(RuntimeError, "rolled_back"):
                manage_service.migrate_legacy_in_place(self.args)
        self.assertEqual(self.legacy_plist.read_bytes(), self.original_bytes)
        self.assertEqual(len(list(self.agents.glob("*.waylume-backup-*"))), 1)

    def test_symlinked_legacy_plist_is_rejected(self):
        real = self.agents / "real.plist"
        real.write_bytes(self.original_bytes)
        self.legacy_plist.unlink()
        self.legacy_plist.symlink_to(real)
        with patch.object(manage_service.sys, "platform", "darwin"), \
             patch.object(waylume_paths, "config_dir", return_value=self.config), \
             patch.object(waylume_paths, "default_data_dir", return_value=self.data):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                manage_service.migrate_legacy_in_place(self.args)

    def test_recover_after_process_stops_between_bootout_and_bootstrap(self):
        loaded = {"value": True}

        def fake_launchctl(*args):
            if args[0] == "print":
                return SimpleNamespace(returncode=0 if loaded["value"] else 1)
            if args[0] == "bootout":
                loaded["value"] = False
            elif args[0] == "bootstrap":
                loaded["value"] = True
            return SimpleNamespace(returncode=0)

        original_replace = manage_service.os.replace

        def crash_on_plist_replace(source, destination):
            if Path(destination).resolve() == self.legacy_plist.resolve():
                raise SystemExit("simulated_process_crash")
            return original_replace(source, destination)

        with patch.object(manage_service.sys, "platform", "darwin"), \
             patch.object(manage_service, "_launchctl", side_effect=fake_launchctl), \
             patch.object(manage_service.os, "replace", side_effect=crash_on_plist_replace), \
             patch.object(waylume_paths, "config_dir", return_value=self.config), \
             patch.object(waylume_paths, "default_data_dir", return_value=self.data):
            with self.assertRaisesRegex(SystemExit, "simulated_process_crash"):
                manage_service.migrate_legacy_in_place(self.args)
            journal = manage_service._legacy_journal_path(self.legacy_plist)
            self.assertTrue(journal.is_file())
            self.assertFalse(loaded["value"])
            recovered = manage_service.recover_legacy_in_place(self.args)

        self.assertEqual(recovered["action"], "restored_or_kept_legacy_service")
        self.assertTrue(loaded["value"])
        self.assertEqual(self.legacy_plist.read_bytes(), self.original_bytes)
        self.assertFalse(manage_service._legacy_journal_path(self.legacy_plist).exists())


if __name__ == "__main__":
    unittest.main()
