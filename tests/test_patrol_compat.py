import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import compatibility_bridge
import patrol_compat
import waylume_paths


class PatrolCompatTests(unittest.TestCase):
    def make_legacy(self, root):
        legacy = root / "legacy" / "bug-monitor"
        (legacy / "state").mkdir(parents=True)
        (legacy / "inbox").mkdir()
        (legacy / "state/inspection-slots.lock").touch()
        (legacy / "state.json").write_text('{"patrol":"state"}', encoding="utf-8")
        (legacy / "state" / "inspection-slots.json").write_text('{"active_run":null}', encoding="utf-8")
        (legacy / "inbox" / "event.json").write_text('{"event":"kept"}', encoding="utf-8")
        (legacy / "dispatch_inspection.py").write_text("legacy entry", encoding="utf-8")
        return legacy

    def configured_paths(self, root):
        config = root / "config"
        return mock.patch.multiple(
            waylume_paths,
            config_dir=mock.Mock(return_value=config),
            migration_lock_path=mock.Mock(return_value=config / "data-migration.lock"),
        )

    def test_inspect_reports_data_root_and_schedule_state_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            before = patrol_compat._manifest(legacy)
            result = patrol_compat.inspect(legacy, root / "chosen-data")
            self.assertEqual(result["active_run"], None)
            self.assertEqual(result["new_data_root"], str((root / "chosen-data").resolve()))
            self.assertTrue(result["old_data_retained_as_full_backup"])
            self.assertEqual(patrol_compat._manifest(legacy), before)
            self.assertFalse((root / "chosen-data").exists())

    def test_default_target_uses_selected_waylume_data_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "configured-data"
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(waylume_paths, "_read_settings", return_value={"data_dir": str(selected)}):
                chosen, source = patrol_compat._selected_data_root()
                inventory = patrol_compat.inspect(root / "legacy")
            self.assertEqual(chosen, selected.resolve())
            self.assertEqual(source, "settings")
            self.assertEqual(inventory["new_data_root"], str(selected.resolve()))
            self.assertEqual(inventory["suggested_patrol_root"], str(selected.resolve() / "mcloud-bug-monitor"))

    def test_data_environment_override_precedes_saved_setting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env_root = root / "environment-data"
            saved_root = root / "saved-data"
            with mock.patch.dict(os.environ, {waylume_paths.DATA_ENV: str(env_root)}), \
                    mock.patch.object(waylume_paths, "_read_settings", return_value={"data_dir": str(saved_root)}):
                chosen, source = patrol_compat._selected_data_root()
            self.assertEqual(chosen, env_root.resolve())
            self.assertEqual(source, waylume_paths.DATA_ENV)

    def test_prepare_activate_and_rollback_keep_old_copy_and_single_write_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            target = root / "configured-data" / "mcloud-bug-monitor"
            backup = root / "legacy-original"

            staged = patrol_compat.prepare(legacy, target)
            self.assertTrue(staged["prepared"])
            self.assertEqual(patrol_compat._manifest(legacy), patrol_compat._manifest(target))
            with self.assertRaisesRegex(ValueError, "schedule_quiescence_confirmation_required"):
                patrol_compat.activate(legacy, target, quiesced=False, backup_root=backup)

            with self.configured_paths(root):
                result = patrol_compat.activate(legacy, target, quiesced=True, backup_root=backup)
            self.assertTrue(result["activated"])
            self.assertTrue(legacy.is_symlink())
            self.assertEqual((legacy / "state.json").read_text(encoding="utf-8"), '{"patrol":"state"}')
            self.assertEqual((legacy / "inbox/event.json").read_text(encoding="utf-8"), '{"event":"kept"}')
            self.assertTrue((backup / "state.json").is_file())

            (legacy / "state.json").write_text('{"patrol":"new-state"}', encoding="utf-8")
            self.assertEqual((target / "state.json").read_text(encoding="utf-8"), '{"patrol":"new-state"}')
            with self.configured_paths(root):
                rolled = patrol_compat.rollback(legacy, backup)
            self.assertTrue(rolled["rolled_back"])
            self.assertFalse(legacy.is_symlink())
            self.assertEqual((legacy / "state.json").read_text(encoding="utf-8"), '{"patrol":"state"}')
            self.assertEqual((target / "state.json").read_text(encoding="utf-8"), '{"patrol":"new-state"}')

    def test_activate_refuses_changed_staged_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            target = root / "data" / "mcloud-bug-monitor"
            patrol_compat.prepare(legacy, target)
            (target / "state.json").write_text("drift", encoding="utf-8")
            with self.configured_paths(root):
                with self.assertRaisesRegex(ValueError, "prepared_target_drifted"):
                    patrol_compat.activate(legacy, target, quiesced=True, backup_root=root / "backup")
            self.assertTrue(legacy.is_dir())
            self.assertFalse(legacy.is_symlink())

    def test_backup_inside_code_checkout_is_rejected_before_activation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            target = root / "data" / "mcloud-bug-monitor"
            patrol_compat.prepare(legacy, target)
            unsafe_backup = Path(patrol_compat.__file__).resolve().parent / "test-backup"
            with self.assertRaisesRegex(ValueError, "unsafe_data_path"):
                patrol_compat.activate(legacy, target, quiesced=True, backup_root=unsafe_backup)
            self.assertTrue(legacy.is_dir())
            self.assertFalse(legacy.is_symlink())

    def test_backup_overlapping_legacy_or_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            target = root / "data" / "mcloud-bug-monitor"
            patrol_compat.prepare(legacy, target)
            with self.assertRaisesRegex(ValueError, "source_target_overlap"):
                patrol_compat.activate(legacy, target, quiesced=True, backup_root=legacy / "backup")
            with self.assertRaisesRegex(ValueError, "source_target_overlap"):
                patrol_compat.activate(legacy, target, quiesced=True, backup_root=target / "backup")
            self.assertTrue(legacy.is_dir())
            self.assertFalse(legacy.is_symlink())

    def test_activate_refuses_active_or_locked_slot_and_unsafe_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            target = root / "data" / "mcloud-bug-monitor"
            patrol_compat.prepare(legacy, target)
            (legacy / "state/inspection-slots.json").write_text('{"active_run":{"id":"busy"}}', encoding="utf-8")
            with self.configured_paths(root):
                with self.assertRaisesRegex(ValueError, "inspection_slot_active_or_invalid"):
                    patrol_compat.activate(legacy, target, quiesced=True, backup_root=root / "backup")
                (legacy / "state/inspection-slots.json").write_text('{"active_run":null}', encoding="utf-8")
                with (legacy / "state/inspection-slots.lock").open("r+") as stream:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                    with self.assertRaisesRegex(ValueError, "inspection_slot_busy"):
                        patrol_compat.activate(legacy, target, quiesced=True, backup_root=root / "backup")
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            with self.assertRaisesRegex(ValueError, "unsafe_data_path"):
                patrol_compat.prepare(legacy, Path(patrol_compat.__file__).parent / "mcloud-bug-monitor")

    def test_recover_restores_original_after_crash_between_rename_and_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            target = root / "data" / "mcloud-bug-monitor"
            patrol_compat.prepare(legacy, target)
            backup = root / "legacy-original"
            manifest_hash = patrol_compat._manifest_hash(patrol_compat._manifest(legacy))
            legacy.rename(backup)
            with self.configured_paths(root):
                patrol_compat._write_journal({"version": 1, "phase": "source_renamed", "source_root": str(legacy),
                    "target_root": str(target), "backup_root": str(backup), "manifest_sha256": manifest_hash})
                result = patrol_compat.recover()
            self.assertEqual(result["action"], "restored_original_after_interrupted_activation")
            self.assertTrue(legacy.is_dir())
            self.assertFalse(legacy.is_symlink())
            self.assertFalse(backup.exists())

    def test_recovery_can_run_inside_parent_owned_migration_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.configured_paths(root), patrol_compat._migration_lock():
                result = patrol_compat.recover(migration_lock_held=True)
            self.assertFalse(result["recovered"])

    def test_recover_completes_registry_after_crash_after_alias_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            target = root / "data" / "mcloud-bug-monitor"
            patrol_compat.prepare(legacy, target)
            backup = root / "legacy-original"
            manifest_hash = patrol_compat._manifest_hash(patrol_compat._manifest(legacy))
            legacy.rename(backup)
            legacy.symlink_to(target, target_is_directory=True)
            with self.configured_paths(root):
                patrol_compat._write_journal({"version": 1, "phase": "alias_installed", "source_root": str(legacy),
                    "target_root": str(target), "backup_root": str(backup), "manifest_sha256": manifest_hash})
                result = patrol_compat.recover()
            self.assertEqual(result["action"], "completed_alias_registration")
            self.assertTrue(legacy.is_symlink())
            self.assertTrue(backup.is_dir())

    def test_registered_patrol_link_follows_future_data_root_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            old_data = root / "data-old"
            target = old_data / patrol_compat.PATROL_SUBDIR
            backup = root / "legacy-original"
            patrol_compat.prepare(legacy, target)
            with self.configured_paths(root):
                patrol_compat.activate(legacy, target, quiesced=True, backup_root=backup)
                new_data = root / "data-new"
                new_data.mkdir()
                shutil.copytree(target, new_data / patrol_compat.PATROL_SUBDIR)
                compatibility_bridge.refresh_registered_bridges(new_data)
                self.assertEqual(legacy.resolve(), (new_data / patrol_compat.PATROL_SUBDIR).resolve())
                self.assertEqual((legacy / "state.json").read_text(encoding="utf-8"), '{"patrol":"state"}')
                result = patrol_compat.rollback(legacy, backup)
                self.assertTrue(result["rolled_back"])
                registry = json.loads((root / "config/compatibility-bridges.json").read_text(encoding="utf-8"))
                self.assertEqual(registry["path_links"], [])

    def test_recover_finishes_rollback_after_backup_was_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            target = root / "data" / "mcloud-bug-monitor"
            backup = root / "legacy-original"
            patrol_compat.prepare(legacy, target)
            with self.configured_paths(root):
                patrol_compat.activate(legacy, target, quiesced=True, backup_root=backup)
                (target / "state.json").write_text("changed patrol state", encoding="utf-8")
                manifest_hash = patrol_compat._manifest_hash(patrol_compat._manifest(backup))
                legacy.unlink()
                backup.rename(legacy)
                patrol_compat._write_journal({"version": 1, "phase": "rollback_prepared", "source_root": str(legacy),
                    "target_root": str(target), "backup_root": str(backup), "manifest_sha256": manifest_hash})
                result = patrol_compat.recover()
                registry = json.loads((root / "config/compatibility-bridges.json").read_text(encoding="utf-8"))
            self.assertEqual(result["action"], "completed_interrupted_rollback")
            self.assertTrue(legacy.is_dir())
            self.assertFalse(legacy.is_symlink())
            self.assertEqual(registry["path_links"], [])
            self.assertEqual((target / "state.json").read_text(encoding="utf-8"), "changed patrol state")

    def test_recovery_rejects_unsafe_journal_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            target = root / "data" / "mcloud-bug-monitor"
            backup = root / "backup"
            with self.configured_paths(root):
                patrol_compat._write_journal({"version": 1, "phase": "source_renamed", "source_root": str(legacy),
                    "target_root": str(Path(patrol_compat.__file__).parent / "mcloud-bug-monitor"),
                    "backup_root": str(backup), "manifest_sha256": "0" * 64})
                with self.assertRaisesRegex(ValueError, "unsafe_data_path"):
                    patrol_compat.recover()
            self.assertTrue(legacy.is_dir())
            self.assertFalse(legacy.is_symlink())

    def test_prepare_rejects_symlinked_data_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self.make_legacy(root)
            real = root / "real"
            real.mkdir()
            (root / "alias").symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "patrol_target_path_invalid"):
                patrol_compat.prepare(legacy, root / "alias" / "mcloud-bug-monitor")
            self.assertTrue(legacy.is_dir())
            self.assertFalse((real / "mcloud-bug-monitor").exists())


if __name__ == "__main__":
    unittest.main()
