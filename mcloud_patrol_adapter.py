# Created by Codex on 2026/11/05.
# Copyright © 2026 CMIC. All rights reserved.
"""Optional adapter for the existing mCloud bug-patrol runtime.

The workbench can run without this integration. When present, it resolves the
existing Codex automation and legacy patrol entry point, preferring the path
already registered in the compatibility bridge registry.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import waylume_paths


@dataclass(frozen=True)
class PatrolPaths:
    schedule_config: Path
    slots: Path
    dispatcher: Path
    patrol_root: Path


def resolve_paths() -> PatrolPaths:
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    schedule = codex_home / "automations" / "bug-2" / "automation.toml"
    selected_root = waylume_paths.data_dir()
    patrol_root = None
    try:
        import compatibility_bridge
        for row in compatibility_bridge.registered_data_path_links(selected_root):
            if row["target_relative_path"] == "mcloud-bug-monitor":
                patrol_root = Path(row["link_path"])
                break
    except ImportError:
        # Compatibility support is optional for generic workbench installs.
        pass
    if patrol_root is None:
        # Keep the historical entry usable for users who have not registered
        # a bridge yet. This path is only reported as available when its old
        # helper scripts are actually present.
        patrol_root = waylume_paths.legacy_data_dir().parent / "bug-monitor"
    return PatrolPaths(
        schedule_config=schedule,
        slots=patrol_root / "inspection_slots.py",
        dispatcher=patrol_root / "dispatch_inspection.py",
        patrol_root=patrol_root,
    )
