"""Create and refresh a narrow legacy-path bridge for the Waylume dashboard.

Company-specific monitor code/state migration is intentionally not implemented:
the existing patrol remains at its current path until its complete execution
chain can be safely separated and verified.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import tempfile
from contextlib import contextmanager
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterator

import waylume_paths


DATA_LINKS = ("records", "backups", "reports", "sources", "logs")
RUNTIME_CODE = (
    "backend.py", "dashboardctl.py", "data_location.py", "handoff_view.py",
    "integrations.py", "server.py", "waylume_paths.py", "web", "skills", "scripts",
)
REGISTRY_NAME = "compatibility-bridges.json"


def _secure_path(path: Path) -> Path:
    """Canonicalize a path and reject user-controlled symlink components."""
    raw = Path(os.path.abspath(os.fspath(path.expanduser())))
    cursor = Path(raw.anchor)
    for part in raw.parts[1:]:
        candidate = cursor / part
        if candidate.is_symlink():
            # macOS temporary directories are exposed through these OS aliases.
            if str(candidate) in {"/var", "/tmp"}:
                cursor = candidate.resolve(strict=True)
                continue
            raise ValueError("bridge_path_symlink_component")
        cursor = candidate
    return raw.resolve(strict=False)


def _secure_link_path(path: Path) -> Path:
    return _secure_path(path.parent) / path.name


def _link_target(path: Path) -> Path:
    """Return a symlink's canonical target, validating both link parent and target."""
    link = _secure_link_path(path)
    if not link.is_symlink():
        raise ValueError("bridge_link_missing")
    parent = link.parent
    raw_target = Path(os.readlink(link))
    if not raw_target.is_absolute():
        raw_target = parent / raw_target
    return _secure_path(raw_target)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path = _secure_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _migration_lock() -> Iterator[None]:
    lock_path = waylume_paths.migration_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _registry_path() -> Path:
    return _secure_path(waylume_paths.config_dir() / REGISTRY_NAME)


def _read_registry() -> dict[str, Any]:
    path = _registry_path()
    if path.is_symlink():
        raise ValueError("bridge_registry_symlink_not_allowed")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "bridges": [], "path_links": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("bridge_registry_unavailable") from exc
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("bridges"), list):
        raise ValueError("bridge_registry_invalid")
    value.setdefault("path_links", [])
    if not isinstance(value["path_links"], list):
        raise ValueError("bridge_registry_invalid")
    return value


def _write_registry(value: dict[str, Any]) -> None:
    _atomic_json(_registry_path(), value)


def registered_data_path_links(data_root: Path) -> tuple[dict[str, str], ...]:
    """Return verified legacy path links for a data root."""
    root = waylume_paths.validate_data_dir(data_root)
    registry = _read_registry()
    links: list[dict[str, str]] = []
    for row in registry["path_links"]:
        if not isinstance(row, dict) or row.get("data_root") != str(root):
            continue
        relative = _safe_relative_target(row.get("target_relative_path"))
        link_path = _secure_link_path(Path(row.get("link_path", "")))
        target = _secure_path(root / relative)
        if root not in target.parents or not target.is_dir():
            raise ValueError("bridge_relative_target_unavailable")
        if _link_target(link_path) != target:
            raise ValueError("bridge_path_link_target_changed")
        links.append({"link_path": str(link_path), "target_relative_path": relative.as_posix(), "data_root": str(root)})
    return tuple(sorted(links, key=lambda row: row["link_path"]))


def registered_data_path_subtrees(data_root: Path) -> tuple[str, ...]:
    """Return verified top-level subtrees referenced by registered legacy links.

    Core migration uses this list to carry optional integration data without
    embedding integration-specific directory names in the generic data layer.
    """
    return tuple(sorted({Path(row["target_relative_path"]).parts[0]
                         for row in registered_data_path_links(data_root)}))


def _link(link_path: Path, target: Path) -> None:
    parent = _secure_path(link_path.parent)
    target = _secure_path(target)
    link_path = parent / link_path.name
    if link_path.exists() or link_path.is_symlink():
        if link_path.is_symlink() and _link_target(link_path) == target:
            return
        raise ValueError("bridge_destination_not_owned")
    link_path.symlink_to(target, target_is_directory=target.is_dir())


def _safe_relative_target(value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("bridge_relative_target_invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("bridge_relative_target_invalid")
    return Path(*relative.parts)


def _register_data_path_link_unlocked(link_path: Path, target_relative_path: str, data_root: Path) -> dict[str, Any]:
    link_path = _secure_link_path(Path(link_path))
    data_root = waylume_paths.validate_data_dir(data_root)
    relative = _safe_relative_target(target_relative_path)
    target = _secure_path(data_root / relative)
    if data_root not in target.parents or not target.is_dir():
        raise ValueError("bridge_relative_target_unavailable")
    if _link_target(link_path) != target:
        raise ValueError("bridge_path_link_target_mismatch")
    registry = _read_registry()
    row = {
        "link_path": str(link_path),
        "target_relative_path": relative.as_posix(),
        "data_root": str(data_root),
    }
    matches = [item for item in registry["path_links"] if item.get("link_path") == str(link_path)]
    if matches and matches != [row]:
        raise ValueError("bridge_path_link_already_registered")
    if not matches:
        registry["path_links"].append(row)
        _write_registry(registry)
    return {"registered": True, **row}


def register_data_path_link(
    link_path: Path,
    target_relative_path: str,
    data_root: Path,
    *,
    migration_lock_held: bool = False,
) -> dict[str, Any]:
    """Register an already-created legacy symlink to a relative data-root path."""
    if migration_lock_held:
        return _register_data_path_link_unlocked(link_path, target_relative_path, data_root)
    with _migration_lock():
        return _register_data_path_link_unlocked(link_path, target_relative_path, data_root)


def _unregister_data_path_link_unlocked(link_path: Path, target_relative_path: str, data_root: Path) -> dict[str, Any]:
    link_path = _secure_link_path(Path(link_path))
    relative = _safe_relative_target(target_relative_path)
    data_root = waylume_paths.validate_data_dir(data_root)
    expected = {
        "link_path": str(link_path),
        "target_relative_path": relative.as_posix(),
        "data_root": str(data_root),
    }
    registry = _read_registry()
    matches = [item for item in registry["path_links"] if item.get("link_path") == str(link_path)]
    if matches and matches != [expected]:
        raise ValueError("bridge_path_link_registration_mismatch")
    registry["path_links"] = [item for item in registry["path_links"] if item.get("link_path") != str(link_path)]
    _write_registry(registry)
    return {"registered": False, **expected}


def unregister_data_path_link(
    link_path: Path,
    target_relative_path: str,
    data_root: Path,
    *,
    migration_lock_held: bool = False,
) -> dict[str, Any]:
    """Remove only a matching registry row; never remove the actual link or data."""
    if migration_lock_held:
        return _unregister_data_path_link_unlocked(link_path, target_relative_path, data_root)
    with _migration_lock():
        return _unregister_data_path_link_unlocked(link_path, target_relative_path, data_root)


def _retarget(link_path: Path, expected_old: Path, new_target: Path) -> None:
    link_path = _secure_link_path(link_path)
    if _link_target(link_path) != _secure_path(expected_old):
        raise ValueError("bridge_link_target_changed")
    new_target = _secure_path(new_target)
    temporary = link_path.parent / ("." + link_path.name + "." + secrets.token_hex(6))
    temporary.symlink_to(new_target, target_is_directory=True)
    try:
        os.replace(temporary, link_path)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def create_dashboard_bridge(bridge_root: Path, project_root: Path, data_root: Path) -> dict[str, Any]:
    """Create a code-link/data-directory-link bridge and register it for path changes."""
    bridge_root = _secure_path(bridge_root)
    project_root = _secure_path(project_root)
    data_root = waylume_paths.validate_data_dir(data_root)
    if bridge_root.exists() or bridge_root.is_symlink():
        raise ValueError("bridge_root_already_exists")
    if bridge_root == project_root or bridge_root in project_root.parents or project_root in bridge_root.parents:
        raise ValueError("bridge_project_overlap")
    with _migration_lock():
        registry = _read_registry()
        bridge_root.mkdir(parents=True, mode=0o700)
        created: list[Path] = []
        try:
            for entry in RUNTIME_CODE:
                candidate = project_root / entry
                if candidate.exists():
                    if candidate.is_symlink() or _secure_path(candidate) != candidate:
                        raise ValueError("bridge_project_entry_symlink")
                    _link(bridge_root / entry, candidate)
                    created.append(bridge_root / entry)
            for entry in DATA_LINKS:
                _link(bridge_root / entry, data_root / entry)
                created.append(bridge_root / entry)
            item = {"bridge_root": str(bridge_root), "project_root": str(project_root), "data_root": str(data_root)}
            registry["bridges"] = [row for row in registry["bridges"] if row.get("bridge_root") != str(bridge_root)] + [item]
            _write_registry(registry)
        except Exception:
            for item_path in reversed(created):
                if item_path.is_symlink():
                    item_path.unlink()
            try:
                bridge_root.rmdir()
            except OSError:
                pass
            raise
    return {"ok": True, **item, "records_target": str((bridge_root / "records").resolve())}


def _refresh_registered_bridges_unlocked(
    data_root: Path,
    accepted_data_roots: tuple[Path, ...] = (),
) -> dict[str, Any]:
    data_root = waylume_paths.validate_data_dir(data_root)
    accepted_roots = tuple(waylume_paths.validate_data_dir(root) for root in accepted_data_roots)
    if not data_root.is_dir() or data_root.is_symlink():
        raise ValueError("bridge_data_root_unavailable")
    for entry in DATA_LINKS:
        destination = data_root / entry
        if destination.is_symlink():
            raise ValueError("bridge_data_subdirectory_symlink")
        destination.mkdir(mode=0o700, exist_ok=True)
        if not destination.is_dir():
            raise ValueError("bridge_data_subdirectory_invalid")
    registry = _read_registry()
    changed: list[tuple[Path, Path, Path]] = []
    updated = []
    updated_path_links = []
    try:
        for row in registry["bridges"]:
            bridge = _secure_path(Path(row["bridge_root"]))
            project = _secure_path(Path(row["project_root"]))
            old_root = waylume_paths.validate_data_dir(row["data_root"])
            if not bridge.is_dir() or bridge.is_symlink():
                raise ValueError("bridge_root_unavailable")
            for entry in RUNTIME_CODE:
                candidate = project / entry
                if candidate.exists() and _link_target(bridge / entry) != candidate:
                    raise ValueError("bridge_code_link_changed")
            for entry in DATA_LINKS:
                link_path = bridge / entry
                old_target = old_root / entry
                new_target = data_root / entry
                actual_target = _link_target(link_path)
                expected_old = _secure_path(old_target)
                expected_new = _secure_path(new_target)
                # A prior process may have stopped after replacing some of
                # the directory links but before committing the registry.
                # Accept either owned endpoint and converge the remaining
                # links; reject anything else as an external modification.
                accepted_targets = {expected_old, expected_new}
                accepted_targets.update(_secure_path(root / entry) for root in accepted_roots)
                if actual_target not in accepted_targets:
                    raise ValueError("bridge_data_link_changed")
                if actual_target != expected_new:
                    _retarget(link_path, actual_target, new_target)
                    changed.append((link_path, new_target, actual_target))
            updated.append({"bridge_root": str(bridge), "project_root": str(project), "data_root": str(data_root)})
        for row in registry["path_links"]:
            link_path = _secure_link_path(Path(row["link_path"]))
            relative = _safe_relative_target(row["target_relative_path"])
            old_root = waylume_paths.validate_data_dir(row["data_root"])
            old_target = _secure_path(old_root / relative)
            new_target = _secure_path(data_root / relative)
            if data_root not in new_target.parents or not new_target.is_dir():
                raise ValueError("bridge_relative_target_unavailable")
            actual_target = _link_target(link_path)
            expected_targets = {old_target, new_target}
            expected_targets.update(_secure_path(root / relative) for root in accepted_roots)
            if actual_target not in expected_targets:
                raise ValueError("bridge_path_link_target_changed")
            if actual_target != new_target:
                _retarget(link_path, actual_target, new_target)
                changed.append((link_path, new_target, actual_target))
            updated_path_links.append({
                "link_path": str(link_path),
                "target_relative_path": relative.as_posix(),
                "data_root": str(data_root),
            })
        registry["bridges"] = updated
        registry["path_links"] = updated_path_links
        _write_registry(registry)
    except Exception as original_error:
        rollback_errors: list[Exception] = []
        for link_path, current, old in reversed(changed):
            try:
                _retarget(link_path, current, old)
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise RuntimeError("bridge_rollback_failed") from rollback_errors[0]
        raise original_error
    return {"updated": len(updated), "data_root": str(data_root)}


def refresh_registered_bridges(
    data_root: Path,
    *,
    migration_lock_held: bool = False,
    accepted_data_roots: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Retarget registered compatibility directory links after a data migration."""
    if migration_lock_held:
        return _refresh_registered_bridges_unlocked(data_root, accepted_data_roots)
    with _migration_lock():
        return _refresh_registered_bridges_unlocked(data_root, accepted_data_roots)


def plan(legacy_dashboard: Path, monitor_root: Path, project_root: Path, data_root: Path) -> dict[str, Any]:
    """Read-only inventory; monitor migration is intentionally left unsupported."""
    data_root = waylume_paths.validate_data_dir(data_root)
    old = _secure_link_path(legacy_dashboard)
    monitor = _secure_link_path(monitor_root)
    return {
        "legacy_dashboard_exists": old.exists(),
        "legacy_dashboard_is_symlink": old.is_symlink(),
        "legacy_dashboard_entries": sorted(p.name for p in old.iterdir()) if old.is_dir() else [],
        "monitor_exists": monitor.exists(),
        "monitor_is_symlink": monitor.is_symlink(),
        "monitor_migration_supported": False,
        "monitor_migration_reason": "完整巡检链路及私人代码/状态分离尚未验证；保留当前服务与路径。",
        "project_root": str(_secure_path(project_root)),
        "data_root": str(data_root),
        "will_preserve_old_dashboard": True,
        "requires_service_stopped": True,
        "does_not_change_automation_schedule": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or inspect a Waylume legacy dashboard bridge")
    parser.add_argument("action", choices=("plan", "stage-dashboard", "refresh-dashboard"))
    parser.add_argument("--legacy-dashboard", type=Path, required=True)
    parser.add_argument("--monitor-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--data-root", type=Path, default=waylume_paths.default_data_dir())
    parser.add_argument("--bridge-root", type=Path)
    args = parser.parse_args()
    if args.action == "plan":
        result = plan(args.legacy_dashboard, args.monitor_root, args.project_root, args.data_root)
    elif args.action == "stage-dashboard":
        if args.bridge_root is None:
            parser.error("--bridge-root required")
        result = create_dashboard_bridge(args.bridge_root, args.project_root, args.data_root)
    else:
        result = refresh_registered_bridges(args.data_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
