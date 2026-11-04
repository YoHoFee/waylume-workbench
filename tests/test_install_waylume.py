# Created by Codex on 2026/11/04.
# Copyright © 2026 CMIC. All rights reserved.

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]


class InstallerPathSafetyTests(unittest.TestCase):
    def test_install_rejects_data_directory_inside_checkout_before_writing(self):
        with tempfile.TemporaryDirectory(prefix="waylume-installer-test-") as temporary:
            workspace = Path(temporary)
            checkout = workspace / "checkout"
            shutil.copytree(
                REPOSITORY,
                checkout,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
            isolated_home = workspace / "home"
            command = [
                sys.executable,
                str(checkout / "scripts" / "install_waylume.py"),
                "--install",
                "--home",
                str(isolated_home),
                "--data-dir",
                str(checkout),
            ]
            result = subprocess.run(
                command,
                cwd=checkout,
                capture_output=True,
                text=True,
                check=False,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("unsafe_data_path", result.stdout + result.stderr)
            self.assertFalse((checkout / "store.json").exists())
            self.assertFalse((checkout / "scope-catalog.json").exists())
            self.assertFalse((checkout / "records").exists())
            self.assertFalse((isolated_home / "Library" / "LaunchAgents").exists())
            self.assertFalse((isolated_home / ".codex" / "skills" / "waylume-assistant").exists())


if __name__ == "__main__":
    unittest.main()
