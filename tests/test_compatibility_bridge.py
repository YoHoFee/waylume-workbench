import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compatibility_bridge
import waylume_paths


class CompatibilityBridgeTests(unittest.TestCase):
    def test_dashboard_bridge_uses_directory_links_and_registration(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            data = root / "private-data"
            bridge = root / "legacy" / "dashboard"
            config = root / "home" / "config"
            project.mkdir()
            (project / "dashboardctl.py").write_text("# fixture", encoding="utf-8")
            (data / "records").mkdir(parents=True)
            (data / "records" / "old.txt").write_text("before", encoding="utf-8")

            with patch.object(waylume_paths, "config_dir", return_value=config):
                result = compatibility_bridge.create_dashboard_bridge(bridge, project, data)
            self.assertTrue((bridge / "dashboardctl.py").is_symlink())
            self.assertTrue((bridge / "records").is_symlink())
            self.assertFalse((bridge / "store.json").is_symlink())
            self.assertEqual(result["records_target"], str((data / "records").resolve()))

            temporary = bridge / "records" / ".new"
            temporary.write_text("after", encoding="utf-8")
            os.replace(temporary, bridge / "records" / "old.txt")
            self.assertEqual((data / "records" / "old.txt").read_text(encoding="utf-8"), "after")

    def test_data_migration_refreshes_registered_bridge_links_atomically(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            source = root / "data-old"
            target = root / "data-new"
            bridge = root / "legacy" / "dashboard"
            config = root / "home" / "config"
            project.mkdir()
            (project / "dashboardctl.py").write_text("# fixture", encoding="utf-8")
            (source / "records").mkdir(parents=True)
            (source / "meta.json").write_text("{}", encoding="utf-8")
            target.mkdir()
            with patch.object(waylume_paths, "config_dir", return_value=config):
                compatibility_bridge.create_dashboard_bridge(bridge, project, source)
                refreshed = compatibility_bridge.refresh_registered_bridges(target)
            self.assertEqual(refreshed["updated"], 1)
            self.assertEqual(compatibility_bridge._link_target(bridge / "records"), (target / "records").resolve())
            self.assertTrue((target / "records").is_dir())
            self.assertEqual(compatibility_bridge._link_target(bridge / "records"), (target / "records").resolve())

    def test_interrupted_link_refresh_retries_and_converges(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            source = root / "data-old"
            target = root / "data-new"
            bridge = root / "legacy" / "dashboard"
            config = root / "home" / "config"
            project.mkdir()
            (project / "dashboardctl.py").write_text("# fixture", encoding="utf-8")
            (source / "records").mkdir(parents=True)
            (source / "records" / "item.md").write_text("context", encoding="utf-8")
            target.mkdir()
            with patch.object(waylume_paths, "config_dir", return_value=config):
                compatibility_bridge.create_dashboard_bridge(bridge, project, source)
                original_retarget = compatibility_bridge._retarget
                calls = {"count": 0}

                def retarget_then_crash(link_path, old_target, new_target):
                    original_retarget(link_path, old_target, new_target)
                    calls["count"] += 1
                    if calls["count"] == 1:
                        raise SystemExit("injected_crash_mid_link_refresh")

                with patch.object(compatibility_bridge, "_retarget", side_effect=retarget_then_crash):
                    with self.assertRaisesRegex(SystemExit, "injected_crash_mid_link_refresh"):
                        compatibility_bridge.refresh_registered_bridges(target)

                # One entry moved, registry still records the old root.
                self.assertEqual(compatibility_bridge._link_target(bridge / "records"), (target / "records").resolve())
                self.assertEqual(compatibility_bridge._link_target(bridge / "backups"), (source / "backups").resolve())
                result = compatibility_bridge.refresh_registered_bridges(target)
                self.assertEqual(result["updated"], 1)
                for entry in compatibility_bridge.DATA_LINKS:
                    self.assertEqual(compatibility_bridge._link_target(bridge / entry), (target / entry).resolve())
                registry = compatibility_bridge._read_registry()
                self.assertEqual(registry["bridges"][0]["data_root"], str(target.resolve()))

    def test_bridge_rollback_failure_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            source = root / "data-old"
            target = root / "data-new"
            bridge = root / "legacy" / "dashboard"
            config = root / "home" / "config"
            project.mkdir()
            (project / "dashboardctl.py").write_text("# fixture", encoding="utf-8")
            (source / "records").mkdir(parents=True)
            target.mkdir()
            with patch.object(waylume_paths, "config_dir", return_value=config):
                compatibility_bridge.create_dashboard_bridge(bridge, project, source)
                original_retarget = compatibility_bridge._retarget
                calls = {"count": 0}

                def fail_during_update_and_rollback(link_path, old_target, new_target):
                    calls["count"] += 1
                    if calls["count"] in {2, 3}:
                        raise OSError("injected_link_failure")
                    original_retarget(link_path, old_target, new_target)

                with patch.object(compatibility_bridge, "_retarget", side_effect=fail_during_update_and_rollback):
                    with self.assertRaisesRegex(RuntimeError, "bridge_rollback_failed"):
                        compatibility_bridge.refresh_registered_bridges(target)

    def test_data_path_link_registration_is_exact_and_unregistration_keeps_link_and_data(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            data = root / "data"
            target = data / "mcloud-bug-monitor"
            target.mkdir(parents=True)
            (target / "state.json").write_text("keep", encoding="utf-8")
            alias = root / "legacy" / "bug-monitor"
            alias.parent.mkdir()
            alias.symlink_to(target, target_is_directory=True)
            config = root / "home" / "config"
            with patch.object(waylume_paths, "config_dir", return_value=config):
                row = compatibility_bridge.register_data_path_link(alias, "mcloud-bug-monitor", data)
                self.assertTrue(row["registered"])
                self.assertEqual(compatibility_bridge._read_registry()["path_links"], [{
                    "link_path": str(alias.parent.resolve() / alias.name),
                    "target_relative_path": "mcloud-bug-monitor",
                    "data_root": str(data.resolve()),
                }])
                result = compatibility_bridge.unregister_data_path_link(alias, "mcloud-bug-monitor", data)
                self.assertFalse(result["registered"])
                self.assertTrue(alias.is_symlink())
                self.assertEqual((alias / "state.json").read_text(encoding="utf-8"), "keep")
                self.assertEqual(compatibility_bridge._read_registry()["path_links"], [])

    def test_relative_symlink_target_is_resolved_and_parent_is_validated(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to("target", target_is_directory=True)
            self.assertEqual(compatibility_bridge._link_target(link), target.resolve())
            (root / "parent-link").symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "bridge_path_symlink_component"):
                compatibility_bridge._link_target(root / "parent-link" / "link")

    def test_data_path_with_symlinked_ancestor_is_rejected(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            actual = root / "actual"
            actual.mkdir()
            alias = root / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "bridge_path_symlink_component"):
                compatibility_bridge.create_dashboard_bridge(alias / "dashboard", root, root / "data")

    def test_monitor_migration_is_explicitly_unsupported_and_does_not_touch_path(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            monitor = root / "legacy" / "bug-monitor"
            monitor.mkdir(parents=True)
            (monitor / "private-state.json").write_text("keep", encoding="utf-8")
            plan = compatibility_bridge.plan(root / "dashboard", monitor, root / "project", root / "data")
            self.assertFalse(plan["monitor_migration_supported"])
            self.assertEqual((monitor / "private-state.json").read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
