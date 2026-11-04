"""Inspect and stage a retained-copy migration of the legacy mCloud patrol.

The patrol's old absolute entry paths can continue working by pointing the old
root at a full copy under the configurable Waylume data root. `prepare` is
read/copy/verify only. `activate` requires the operator to quiesce the old
schedule first; it atomically renames the old root to a retained sibling backup
and replaces the original path with a symlink to the verified new root.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import waylume_paths


DEFAULT_LEGACY_ROOT = Path.home() / ".codex/work-agent/tools/mcloud/bug-monitor"
DATA_ENV = "WAYLUME_DATA_DIR"
PATROL_SUBDIR = "mcloud-bug-monitor"
JOURNAL_NAME = "patrol-data-migration.json"


def _canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _entry_path(path: Path) -> Path:
    """Resolve parent aliases while preserving whether the final entry is a symlink."""
    expanded = path.expanduser()
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    return absolute.parent.resolve(strict=False) / absolute.name


def _manifest(root: Path) -> dict[str, dict[str, Any]]:
    """Hash a tree without following symlinks or accepting special files."""
    root = _canonical(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("source_directory_unavailable")
    result: dict[str, dict[str, Any]] = {}
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[:] = sorted(dirs)
        for name in dirs:
            path = base / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError("source_contains_unsupported_entry")
            result[path.relative_to(root).as_posix() + "/"] = {"mode": stat.S_IMODE(path.stat().st_mode), "kind": "dir"}
        for name in sorted(names):
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("source_contains_unsupported_entry")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
            result[path.relative_to(root).as_posix()] = {
                "mode": stat.S_IMODE(path.stat().st_mode), "kind": "file",
                "size": size, "sha256": digest.hexdigest(),
            }
    return dict(sorted(result.items()))


def _manifest_hash(manifest: dict[str, dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _selected_data_root(explicit: Path | None = None) -> tuple[Path, str]:
    if explicit is not None:
        return waylume_paths.validate_data_dir(explicit), "argument"
    env = os.environ.get(DATA_ENV)
    if env:
        return waylume_paths.validate_data_dir(env), DATA_ENV
    settings = waylume_paths._read_settings()
    configured = settings.get("data_dir")
    if configured is not None:
        if not isinstance(configured, str):
            raise ValueError("settings_invalid")
        return waylume_paths.validate_data_dir(configured), "settings"
    return waylume_paths.validate_data_dir(waylume_paths.default_data_dir()), "default"


def _validated_backup_root(path: Path) -> Path:
    if path.expanduser().is_symlink():
        raise ValueError("backup_path_symlink_not_allowed")
    candidate = _entry_path(path)
    validated = waylume_paths.validate_data_dir(candidate)
    if candidate != validated:
        raise ValueError("backup_path_invalid")
    return validated


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _journal_path() -> Path:
    return waylume_paths.config_dir() / JOURNAL_NAME


@contextmanager
def _migration_lock() -> Iterator[None]:
    path = waylume_paths.migration_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _write_journal(value: dict[str, Any]) -> None:
    path = _journal_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.parent / ("." + path.name + "." + secrets.token_hex(8))
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        temporary.unlink(missing_ok=True)


def _read_journal() -> dict[str, Any] | None:
    path = _journal_path()
    if path.is_symlink():
        raise ValueError("patrol_migration_journal_unsafe")
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("patrol_migration_journal_permissions_invalid")
        journal = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("patrol_migration_journal_invalid") from exc
    if not isinstance(journal, dict) or journal.get("version") != 1:
        raise ValueError("patrol_migration_journal_invalid")
    return journal


def _clear_journal() -> None:
    path = _journal_path()
    path.unlink(missing_ok=True)
    if path.parent.exists():
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def _inactive_slot(root: Path) -> Iterator[None]:
    lock_path = root / "state/inspection-slots.lock"
    state_path = root / "state/inspection-slots.json"
    if lock_path.is_symlink() or not lock_path.is_file() or state_path.is_symlink() or not state_path.is_file():
        raise ValueError("inspection_slot_state_unavailable")
    with lock_path.open("r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("inspection_slot_busy") from exc
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("active_run") is not None:
                raise ValueError("inspection_slot_active_or_invalid")
            yield
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("inspection_slot_state_invalid") from exc
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _validate_patrol_target(target_root: Path) -> Path:
    expanded = target_root.expanduser()
    raw = expanded if expanded.is_absolute() else Path.cwd() / expanded
    cursor = Path(raw.anchor)
    for part in raw.parts[1:]:
        candidate = cursor / part
        if candidate.is_symlink():
            if str(candidate) in {"/var", "/tmp"}:
                cursor = candidate.resolve(strict=True)
                continue
            raise ValueError("patrol_target_path_invalid")
        cursor = candidate
    validated = waylume_paths.validate_data_dir(raw)
    if raw.resolve(strict=False) != validated or validated.name != PATROL_SUBDIR:
        raise ValueError("patrol_target_path_invalid")
    return validated


def _copy_tree(source: Path, target: Path, expected: dict[str, dict[str, Any]]) -> None:
    if target.exists() or target.is_symlink():
        raise ValueError("target_already_exists")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = target.parent / ("." + target.name + ".stage-" + secrets.token_hex(8))
    try:
        shutil.copytree(source, stage, symlinks=False, copy_function=shutil.copy2)
        if _manifest(stage) != expected:
            raise ValueError("copy_verification_failed")
        os.replace(stage, target)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise


def inspect(legacy_root: Path = DEFAULT_LEGACY_ROOT, data_root: Path | None = None) -> dict[str, Any]:
    """Read-only path and state inventory."""
    selected_data, data_root_source = _selected_data_root(data_root)
    legacy = _entry_path(legacy_root)
    state = legacy / "state.json"
    slots = legacy / "state/inspection-slots.json"
    try:
        slot_data = json.loads(slots.read_text(encoding="utf-8"))
        active_run = slot_data.get("active_run") if isinstance(slot_data, dict) else "invalid"
    except FileNotFoundError:
        active_run = None
    except (OSError, json.JSONDecodeError):
        active_run = "unreadable"
    components = {
        name: (legacy / name).is_file()
        for name in ("dispatch_inspection.py", "inspection_slots.py", "unified_inspection.exec.js")
    }
    return {
        "legacy_root": str(legacy), "legacy_root_is_symlink": legacy.is_symlink(),
        "legacy_state_exists": state.is_file(), "legacy_inbox_entries": len(list((legacy / "inbox").iterdir())) if (legacy / "inbox").is_dir() else 0,
        "legacy_schedule_components": components, "active_run": active_run,
        "new_data_root": str(selected_data),
        "new_data_root_source": data_root_source,
        "suggested_patrol_root": str(selected_data / PATROL_SUBDIR),
        "old_data_retained_as_full_backup": True,
        "legacy_entry_path_will_resolve_to_new_root": True,
        "schedule_must_be_quiesced_before_activation": True,
        "dual_write_enabled": False,
    }


def prepare(legacy_root: Path, target_root: Path) -> dict[str, Any]:
    """Copy and verify the complete legacy root at target; source is untouched."""
    source, target = _entry_path(legacy_root), _validate_patrol_target(target_root)
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("source_target_overlap")
    before = _manifest(source)
    _copy_tree(source, target, before)
    return {"prepared": True, "source_root": str(source), "target_root": str(target),
            "file_count": sum(1 for row in before.values() if row["kind"] == "file"),
            "entry_count": len(before), "manifest_sha256": hashlib.sha256(
                json.dumps(before, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "source_untouched": True, "activation_performed": False}


def _registered_path_link(link: Path, target: Path, data_root: Path, *, register: bool) -> None:
    import compatibility_bridge
    if register:
        compatibility_bridge.register_data_path_link(
            link, PATROL_SUBDIR, data_root, migration_lock_held=True)
    else:
        compatibility_bridge.unregister_data_path_link(
            link, PATROL_SUBDIR, data_root, migration_lock_held=True)


def _journal_paths(journal: dict[str, Any]) -> tuple[Path, Path, Path]:
    try:
        source = _entry_path(Path(journal["source_root"]))
        target = _canonical(Path(journal["target_root"]))
        backup = _validated_backup_root(Path(journal["backup_root"]))
    except (KeyError, TypeError) as exc:
        raise ValueError("patrol_migration_journal_invalid") from exc
    if (_overlaps(source, target) or _overlaps(backup, source) or _overlaps(backup, target)
            or target != _validate_patrol_target(target)):
        raise ValueError("patrol_migration_journal_unsafe")
    return source, target, backup


def _recover_journal(journal: dict[str, Any], source: Path, target: Path, backup: Path) -> dict[str, Any]:
    phase = journal.get("phase")
    expected = journal.get("manifest_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("patrol_migration_journal_invalid")
    backup_ok = backup.is_dir() and not backup.is_symlink() and _manifest_hash(_manifest(backup)) == expected
    target_ok = target.is_dir() and not target.is_symlink() and _manifest_hash(_manifest(target)) == expected

    if phase in {"activation_prepared", "source_renamed"}:
        # Before a committed alias, restore the old entry. Never decide to
        # complete activation automatically after a process crash.
        if source.is_symlink():
            if source.resolve(strict=True) != target or not backup_ok or not target_ok:
                raise ValueError("patrol_migration_recovery_conflict")
            source.unlink()
        elif source.exists():
            if backup.exists() or _manifest_hash(_manifest(source)) != expected:
                raise ValueError("patrol_migration_recovery_conflict")
            _clear_journal()
            return {"recovered": True, "action": "discarded_unstarted_activation"}
        if not backup_ok:
            raise ValueError("patrol_migration_recovery_backup_invalid")
        os.replace(backup, source)
        _clear_journal()
        return {"recovered": True, "action": "restored_original_after_interrupted_activation"}

    if phase == "alias_installed":
        if not (source.is_symlink() and source.resolve(strict=True) == target and backup_ok and target_ok):
            raise ValueError("patrol_migration_recovery_conflict")
        _registered_path_link(source, target, target.parent, register=True)
        _clear_journal()
        return {"recovered": True, "action": "completed_alias_registration"}

    if phase == "activation_cleanup":
        if source.is_symlink():
            if source.resolve(strict=True) != target or not backup_ok:
                raise ValueError("patrol_migration_recovery_conflict")
            source.unlink()
        if not source.exists() and backup_ok:
            os.replace(backup, source)
        elif source.is_dir() and not source.is_symlink() and not backup.exists():
            if _manifest_hash(_manifest(source)) != expected:
                raise ValueError("patrol_migration_recovery_conflict")
        else:
            raise ValueError("patrol_migration_recovery_conflict")
        _registered_path_link(source, target, target.parent, register=False)
        _clear_journal()
        return {"recovered": True, "action": "cleaned_interrupted_activation"}

    if phase == "rollback_prepared":
        if source.is_symlink():
            if source.resolve(strict=True) != target:
                raise ValueError("patrol_migration_recovery_conflict")
            source.unlink()
        elif source.exists():
            if backup.exists() or _manifest_hash(_manifest(source)) != expected:
                raise ValueError("patrol_migration_recovery_conflict")
            _registered_path_link(source, target, target.parent, register=False)
            _clear_journal()
            return {"recovered": True, "action": "completed_interrupted_rollback"}
        if not backup_ok:
            raise ValueError("patrol_migration_recovery_backup_invalid")
        os.replace(backup, source)
        _registered_path_link(source, target, target.parent, register=False)
        _clear_journal()
        return {"recovered": True, "action": "restored_original_after_interrupted_rollback"}

    raise ValueError("patrol_migration_journal_phase_invalid")


def _recover_unlocked() -> dict[str, Any]:
    journal = _read_journal()
    if journal is None:
        return {"recovered": False, "reason": "no_pending_migration"}
    source, target, backup = _journal_paths(journal)
    slot_root = source if source.is_dir() else (target if source.is_symlink() else backup)
    with _inactive_slot(slot_root):
        return _recover_journal(journal, source, target, backup)


def recover(*, migration_lock_held: bool = False) -> dict[str, Any]:
    """Resolve an interrupted activation/rollback under the shared migration lock."""
    if migration_lock_held:
        return _recover_unlocked()
    with _migration_lock():
        return _recover_unlocked()


def activate(legacy_root: Path, target_root: Path, *, quiesced: bool, backup_root: Path | None = None) -> dict[str, Any]:
    """Switch the legacy path to a verified target, preserving original tree."""
    if not quiesced:
        raise ValueError("schedule_quiescence_confirmation_required")
    source, target = _entry_path(legacy_root), _validate_patrol_target(target_root)
    backup = _validated_backup_root(backup_root) if backup_root is not None else _validated_backup_root(
        source.with_name(source.name + f".legacy-backup-{int(time.time())}"))
    if source.is_symlink() or not source.is_dir():
        raise ValueError("legacy_root_must_be_real_directory")
    if not target.is_dir() or target.is_symlink():
        raise ValueError("prepared_target_unavailable")
    if _overlaps(source, target) or _overlaps(backup, source) or _overlaps(backup, target):
        raise ValueError("source_target_overlap")
    if backup.exists() or backup.is_symlink():
        raise ValueError("backup_path_already_exists")
    with _migration_lock():
        if _read_journal() is not None:
            raise ValueError("patrol_migration_recovery_required")
        with _inactive_slot(source):
            source_manifest = _manifest(source)
            if source_manifest != _manifest(target):
                raise ValueError("prepared_target_drifted")
            journal = {"version": 1, "phase": "activation_prepared", "source_root": str(source),
                       "target_root": str(target), "backup_root": str(backup),
                       "manifest_sha256": _manifest_hash(source_manifest)}
            _write_journal(journal)
            os.replace(source, backup)
            journal["phase"] = "source_renamed"
            _write_journal(journal)
            try:
                source.symlink_to(target, target_is_directory=True)
                journal["phase"] = "alias_installed"
                _write_journal(journal)
                _registered_path_link(source, target, target.parent, register=True)
                _clear_journal()
            except Exception:
                journal["phase"] = "activation_cleanup"
                _write_journal(journal)
                if source.is_symlink() and source.resolve(strict=True) == target:
                    source.unlink()
                if not source.exists() and backup.exists():
                    os.replace(backup, source)
                if source.is_dir() and not source.is_symlink() and not backup.exists():
                    _registered_path_link(source, target, target.parent, register=False)
                    _clear_journal()
                raise
    return {"activated": True, "legacy_alias": str(source), "new_root": str(target),
            "retained_original_backup": str(backup), "schedule_changed": False,
            "single_writer_path": str(target), "slot_checked_inactive": True,
            "registered_for_future_data_root_changes": True}


def rollback(legacy_root: Path, backup_root: Path) -> dict[str, Any]:
    """Restore the retained original directory; leave migrated data in place."""
    source, backup = _entry_path(legacy_root), _validated_backup_root(backup_root)
    with _migration_lock():
        if _read_journal() is not None:
            raise ValueError("patrol_migration_recovery_required")
        if not source.is_symlink() or not backup.is_dir() or backup.is_symlink():
            raise ValueError("rollback_paths_invalid")
        target = source.resolve(strict=True)
        if _overlaps(source, backup) or _overlaps(target, backup):
            raise ValueError("rollback_paths_invalid")
        if target != _validate_patrol_target(target) or not target.is_dir() or target.is_symlink():
            raise ValueError("rollback_target_invalid")
        with _inactive_slot(source):
            expected = _manifest_hash(_manifest(backup))
            journal = {"version": 1, "phase": "rollback_prepared", "source_root": str(source),
                       "target_root": str(target), "backup_root": str(backup), "manifest_sha256": expected}
            _write_journal(journal)
            source.unlink()
            os.replace(backup, source)
            _registered_path_link(source, target, target.parent, register=False)
            _clear_journal()
    return {"rolled_back": True, "restored_legacy_root": str(source),
            "migrated_data_retained_at": str(target)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Retain and relocate legacy mCloud patrol data")
    parser.add_argument("action", choices=("inspect", "prepare", "activate", "rollback", "recover"))
    parser.add_argument("--legacy-root", type=Path, default=DEFAULT_LEGACY_ROOT)
    parser.add_argument("--data-root", type=Path, help="Defaults to WAYLUME_DATA_DIR or ~/.waylume")
    parser.add_argument("--target-root", type=Path)
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--confirm-schedule-quiesced", action="store_true")
    args = parser.parse_args()
    if args.action == "inspect":
        result = inspect(args.legacy_root, args.data_root)
    elif args.action == "prepare":
        data_root, _ = _selected_data_root(args.data_root)
        target = args.target_root or data_root / PATROL_SUBDIR
        result = prepare(args.legacy_root, target)
    elif args.action == "activate":
        data_root, _ = _selected_data_root(args.data_root)
        target = args.target_root or data_root / PATROL_SUBDIR
        result = activate(args.legacy_root, target, quiesced=args.confirm_schedule_quiesced, backup_root=args.backup_root)
    elif args.action == "rollback":
        if args.backup_root is None:
            parser.error("rollback requires --backup-root")
        result = rollback(args.legacy_root, args.backup_root)
    else:
        result = recover()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
