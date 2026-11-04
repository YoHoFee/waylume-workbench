"""Preview and safely migrate the user's private Waylume data directory."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import base64
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import dashboardctl
import compatibility_bridge
import waylume_paths


_guard = threading.RLock()
_previews: dict[str, dict[str, Any]] = {}
_jobs: dict[str, dict[str, Any]] = {}
_EXCLUDED = {".store.lock", ".server.lock", ".migration.lock", ".transaction.json"}
def _has_data(root: Path) -> bool:
    entries = waylume_paths.legacy_dashboard_data_entries(root)
    return root.is_dir() and any((root / name).exists() for name in entries)


def _revision() -> int:
    config = waylume_paths.settings_path()
    try:
        content = config.read_bytes()
    except OSError:
        content = b""
    raw = str(waylume_paths.data_dir()).encode() + b"\0" + content
    return int(hashlib.sha256(raw).hexdigest()[:12], 16)


def location() -> dict[str, Any]:
    selected = waylume_paths.data_dir()
    default = waylume_paths.default_data_dir().resolve()
    legacy = waylume_paths.legacy_data_dir().resolve()
    settings = waylume_paths._read_settings()
    env_override = bool(os.environ.get(waylume_paths.DATA_ENV))
    migration = settings.get("last_migration") if isinstance(settings.get("last_migration"), dict) else {}
    backup_path = _verified_backup_path(migration, legacy)
    return {
        "current_path": str(selected),
        "default_path": str(default),
        "is_default": selected == default,
        "legacy_path": str(legacy),
        "legacy_exists": _has_data(legacy) and legacy != selected,
        "backup_path": str(backup_path) if backup_path else None,
        "revision": _revision(),
        "environment_override": env_override,
        "migration": {"legacy_path": str(legacy), "backup_path": str(backup_path) if backup_path else None},
    }


def _verified_backup_path(migration: dict[str, Any], legacy: Path) -> Path | None:
    """Return only a retained backup whose data manifest matches migration metadata."""
    expected = migration.get("manifest_sha256")
    candidate_text = migration.get("backup_path")
    if not isinstance(expected, str) or len(expected) != 64:
        return None
    candidates: list[Path] = []
    if isinstance(candidate_text, str) and candidate_text:
        candidates.append(Path(candidate_text).expanduser())
    # During the authorized in-place service migration, the original mixed
    # dashboard root was archived and replaced with a compatibility bridge.
    # Locate that retained archive by its stable sibling naming and verify it;
    # never return the bridge or an unverified similarly named directory.
    if isinstance(candidate_text, str) and Path(candidate_text).expanduser().resolve() == legacy:
        candidates.extend(legacy.parent.glob(legacy.name + ".full-backup-*.original"))
    verified: list[Path] = []
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            roots = migration.get("legacy_data_entries")
            mixed = bool(roots) or (candidate != Path(candidate_text).expanduser() if isinstance(candidate_text, str) else False)
            _, _, digest = _file_manifest(
                candidate, legacy_mixed_root=mixed,
                legacy_entries=set(roots) if mixed and isinstance(roots, list) and all(isinstance(item, str) for item in roots) else None,
            )
        except (OSError, ValueError):
            continue
        if digest == expected:
            verified.append(candidate.resolve())
    unique = sorted(set(verified), key=str)
    return unique[0] if len(unique) == 1 else None


def _source_for(target: Path, current: Path, legacy: Path) -> Path:
    if _has_data(current):
        return current
    if current == waylume_paths.default_data_dir().resolve() and _has_data(legacy) and legacy != target:
        return legacy
    return current


def _file_manifest(root: Path, *, legacy_mixed_root: bool = False,
                    legacy_entries: set[str] | None = None) -> tuple[list[dict[str, Any]], int, str]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("source_unavailable")
    if legacy_mixed_root and legacy_entries is None:
        legacy_entries = waylume_paths.legacy_dashboard_data_entries(root)
    legacy_entries = legacy_entries or set()
    entries: list[dict[str, Any]] = []
    total = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[:] = sorted(d for d in dirs if not (base == root and d == "__pycache__")
                         and not (base == root and legacy_mixed_root and d not in legacy_entries))
        for dirname in dirs:
            if (base / dirname).is_symlink():
                raise ValueError("source_contains_symlink")
        for name in sorted(names):
            if base == root and legacy_mixed_root and name not in legacy_entries:
                continue
            path = base / name
            rel = path.relative_to(root).as_posix()
            if Path(rel).name in _EXCLUDED:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("source_contains_unsupported_entry")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block); size += len(block)
            entries.append({"path": rel, "size": size, "sha256": digest.hexdigest()})
            total += size
    entries.sort(key=lambda row: row["path"])
    manifest_digest = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return entries, total, manifest_digest


def preview(target_text: str, expected_revision: Any = None) -> dict[str, Any]:
    if not isinstance(target_text, str) or not target_text.strip() or len(target_text) > 4096:
        raise ValueError("invalid_target_path")
    current_info = location()
    if expected_revision is not None and expected_revision != current_info["revision"]:
        raise ValueError("revision_conflict")
    raw_target = Path(target_text).expanduser()
    if raw_target.is_symlink():
        raise ValueError("target_symlink_not_allowed")
    target = waylume_paths.validate_data_dir(raw_target)
    current = waylume_paths.validate_data_dir(waylume_paths.data_dir())
    legacy = waylume_paths.legacy_data_dir().resolve()
    source = _source_for(target, current, legacy)
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("source_target_overlap")
    if os.environ.get(waylume_paths.DATA_ENV):
        raise ValueError("data_dir_environment_override")
    if not source.is_dir() or not os.access(source, os.R_OK | os.X_OK):
        raise ValueError("source_unavailable")
    parent = target.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    parent_writable = parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)
    target_exists = target.exists()
    target_empty = target_exists and target.is_dir() and not any(target.iterdir())
    if target_exists and not target.is_dir():
        target_empty = False
    legacy_mixed_root = source.resolve() == legacy
    legacy_entries = waylume_paths.legacy_dashboard_data_entries(source) if legacy_mixed_root else set()
    registered_subtrees = waylume_paths.registered_data_path_subtrees(source)
    files, byte_count, digest = _file_manifest(
        source, legacy_mixed_root=legacy_mixed_root,
        legacy_entries=legacy_entries if legacy_mixed_root else None,
    )
    excluded_roots = sorted(entry.name for entry in source.iterdir()
                            if legacy_mixed_root and entry.name not in legacy_entries and entry.name not in _EXCLUDED)
    preview_id = secrets.token_urlsafe(18)
    token = secrets.token_urlsafe(32)
    expires = time.time() + 300
    risks = []
    if source == legacy:
        risks.append("来源是代码与数据混放的旧目录；只迁移已识别的数据、附件和备份，原目录完整保留作回滚。")
    if target_exists and not target_empty:
        risks.append("目标目录非空或不是目录，不能覆盖。")
    if os.environ.get(waylume_paths.DATA_ENV):
        risks.append("当前数据路径由 WAYLUME_DATA_DIR 环境变量固定，需由服务管理员调整后再迁移。")
    can_migrate = bool(parent_writable and (not target_exists or target_empty) and not os.environ.get(waylume_paths.DATA_ENV))
    preview_info = {
        "preview_id": preview_id, "confirmation_token": token, "revision": current_info["revision"],
        "source_path": str(source), "target_path": str(target), "source_manifest": digest,
        "source_readable": True, "target_parent_writable": parent_writable,
        "target_exists": target_exists, "target_empty": target_empty,
        "estimated_files": len(files), "estimated_bytes": byte_count,
        "source_kind": "legacy_mixed_dashboard" if legacy_mixed_root else "data_directory",
        "excluded_legacy_roots": excluded_roots,
        "_legacy_data_entries": sorted(legacy_entries) if legacy_mixed_root else [],
        "_registered_data_subtrees": list(registered_subtrees),
        "confirmation_required": True, "can_migrate": can_migrate,
        "risks": risks, "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(timespec="seconds"),
        "_expires": expires,
    }
    with _guard:
        _previews[preview_id] = preview_info
        for key in list(_previews):
            if _previews[key]["_expires"] < time.time():
                _previews.pop(key, None)
    return {key: value for key, value in preview_info.items() if not key.startswith("_") and key != "source_manifest"}


@contextmanager
def _file_locks(paths: list[Path]) -> Iterator[None]:
    handles = []
    try:
        for path in sorted(set(p.resolve() for p in paths), key=str):
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            handle = path.open("a", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


@contextmanager
def _patrol_snapshot_lock(source: Path, registered_subtrees: tuple[str, ...] = ()) -> Iterator[None]:
    """Freeze the optional patrol only when its path is registered."""
    if "mcloud-bug-monitor" not in registered_subtrees:
        yield
        return
    monitor = source / "mcloud-bug-monitor"
    if not monitor.exists():
        raise ValueError("registered_integration_data_unavailable")
    try:
        import patrol_compat
    except ImportError as exc:
        raise ValueError("registered_integration_lock_unavailable") from exc
    with patrol_compat._inactive_slot(monitor):
        yield


def _copy_data(source: Path, stage: Path, *, legacy_mixed_root: bool = False,
               legacy_entries: set[str] | None = None) -> None:
    if legacy_mixed_root and legacy_entries is None:
        legacy_entries = waylume_paths.legacy_dashboard_data_entries(source)
    legacy_entries = legacy_entries or set()
    stage.mkdir(mode=0o700)
    for current, dirs, files in os.walk(source, followlinks=False):
        rel = Path(current).relative_to(source)
        target_dir = stage / rel
        target_dir.mkdir(mode=0o700, exist_ok=True)
        dirs[:] = sorted(d for d in dirs if not (Path(current) == source and d == "__pycache__")
                         and not (Path(current) == source and legacy_mixed_root and d not in legacy_entries))
        for dirname in dirs:
            src_dir = Path(current) / dirname
            if src_dir.is_symlink():
                raise ValueError("source_contains_symlink")
            (target_dir / dirname).mkdir(mode=0o700, exist_ok=True)
        for filename in files:
            if Path(current) == source and legacy_mixed_root and filename not in legacy_entries:
                continue
            src = Path(current) / filename
            if Path(filename).name in _EXCLUDED:
                continue
            if src.is_symlink() or not src.is_file():
                raise ValueError("source_contains_unsupported_entry")
            dst = target_dir / filename
            with src.open("rb") as reader, dst.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush(); os.fsync(writer.fileno())
            os.chmod(dst, 0o600)
        os.chmod(target_dir, 0o700)
    os.chmod(stage, 0o700)


def migrate(target_text: str, preview_id: str, confirmation_token: str, expected_revision: Any) -> dict[str, Any]:
    with _guard:
        item = _previews.get(preview_id)
        if not item or item["_expires"] < time.time() or not secrets.compare_digest(str(item.get("confirmation_token", "")), str(confirmation_token)):
            raise ValueError("migration_confirmation_invalid")
        if expected_revision != item["revision"] or expected_revision != _revision():
            raise ValueError("revision_conflict")
        if str(Path(target_text).expanduser().resolve()) != item["target_path"]:
            raise ValueError("preview_target_mismatch")
        if not item["can_migrate"]:
            raise ValueError("migration_preflight_failed")
        _previews.pop(preview_id, None)

    source = waylume_paths.validate_data_dir(item["source_path"])
    target = waylume_paths.validate_data_dir(item["target_path"])
    legacy_mixed_root = item.get("source_kind") == "legacy_mixed_dashboard"
    job_id = secrets.token_urlsafe(12)
    backup_path = str(source)  # Source is kept intact as a verified rollback copy.
    legacy_entries = set(item.get("_legacy_data_entries", [])) if legacy_mixed_root else set()
    registered_subtrees = tuple(item.get("_registered_data_subtrees", []))
    job = {"job_id": job_id, "status": "checking", "progress": 0, "current_path": str(waylume_paths.data_dir()),
           "backup_path": backup_path, "verification": {"verified": False},
           "rollback": {"attempted": False, "succeeded": False}}
    with _guard:
        _jobs[job_id] = dict(job)
    config_lock = waylume_paths.migration_lock_path()
    source_lock = source / ".store.lock"
    target_lock = target.parent / f".{target.name}.migration.lock"
    stage = target.parent / f".{target.name}.waylume-stage-{job_id}"
    empty_target_hold = target.parent / f".{target.name}.empty-preserved-{job_id}"
    moved_target = False
    moved_empty = False
    journal_path = waylume_paths.migration_journal_path()
    journal_written = False
    settings_path = waylume_paths.settings_path()
    settings_existed = settings_path.is_file()
    previous_settings = settings_path.read_bytes() if settings_existed else b""
    verified_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        with _file_locks([config_lock, source_lock, target_lock]), ExitStack() as patrol_locks:
            patrol_locks.enter_context(_patrol_snapshot_lock(source, registered_subtrees))
            # Resolve any in-flight record transaction before taking the copy snapshot.
            dashboardctl.backend.recover(source)
            source_manifest, source_bytes, source_digest = _file_manifest(source, legacy_mixed_root=legacy_mixed_root)
            if source_digest != item["source_manifest"]:
                raise ValueError("source_changed_since_preview")
            if target.exists() and (not target.is_dir() or any(target.iterdir())):
                raise ValueError("target_not_empty")
            if target.parent.resolve() != target.parent:
                raise ValueError("target_parent_symlink_not_allowed")
            journal = {
                "version": 1, "job_id": job_id, "owner_pid": os.getpid(), "state": "copying",
                "source_path": str(source), "target_path": str(target), "stage_path": str(stage),
                "empty_target_hold": str(empty_target_hold), "target_was_empty": target.exists(),
                "expected_manifest": source_digest,
                "manifest_policy": "legacy_dashboard_data" if legacy_mixed_root else "all",
                "legacy_data_entries": sorted(legacy_entries),
                "verified_at": verified_at,
                "settings_existed": settings_existed,
                "previous_settings_b64": base64.b64encode(previous_settings).decode("ascii"),
            }
            waylume_paths.write_migration_journal(journal)
            journal_written = True
            _copy_data(source, stage, legacy_mixed_root=legacy_mixed_root,
                       legacy_entries=legacy_entries if legacy_mixed_root else None)
            target_manifest, target_bytes, target_digest = _file_manifest(stage)
            if source_digest != target_digest or source_bytes != target_bytes or source_manifest != target_manifest:
                raise ValueError("migration_verification_failed")
            journal["state"] = "stage_verified"
            waylume_paths.write_migration_journal(journal)
            if target.exists():
                os.replace(target, empty_target_hold)
                moved_empty = True
                journal["empty_target_moved"] = True
                waylume_paths.write_migration_journal(journal)
            os.replace(stage, target)
            moved_target = True
            # The alias currently points at the source, so the copied lock file
            # has a distinct inode. Acquire the target-side lock before any
            # compatibility link can be retargeted, and keep both locks until
            # journal/config/registry commit (or rollback) is complete.
            patrol_locks.enter_context(_patrol_snapshot_lock(target, registered_subtrees))
            journal["state"] = "target_installed"
            waylume_paths.write_migration_journal(journal)
            waylume_paths.write_data_dir(target, migration={"backup_path": backup_path, "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"), "manifest_sha256": target_digest, "legacy_data_entries": sorted(legacy_entries)})
            journal["state"] = "settings_switched"
            waylume_paths.write_migration_journal(journal)
            dashboardctl.refresh_data_paths()
            compatibility_bridge.refresh_registered_bridges(target, migration_lock_held=True)
            journal["state"] = "committed"
            waylume_paths.write_migration_journal(journal)
            journal_path.unlink(missing_ok=True)
            if moved_empty and empty_target_hold.exists() and not any(empty_target_hold.iterdir()):
                empty_target_hold.rmdir()
            result = {
                "job_id": job_id, "status": "verified", "progress": 100,
                "current_path": str(target), "backup_path": backup_path,
                "verification": {"verified": True, "source_files": len(source_manifest), "target_files": len(target_manifest),
                                 "source_bytes": source_bytes, "target_bytes": target_bytes, "manifest_sha256": target_digest},
                "rollback": {"attempted": False, "succeeded": False},
                "message": "迁移副本校验通过；原目录保留，可用于回退。",
            }
            with _guard:
                _jobs[job_id] = dict(result)
            return result
    except Exception as exc:
        rollback = {"attempted": bool(moved_target or moved_empty), "succeeded": True, "reason": "migration_failed"}
        try:
            if journal_written:
                settings_now = waylume_paths._read_settings()
                if settings_now.get("data_dir") == str(target):
                    if settings_existed:
                        waylume_paths._atomic_bytes(settings_path, previous_settings)
                    else:
                        settings_path.unlink(missing_ok=True)
                if moved_target and target.exists():
                    preserve = target.parent / f".{target.name}.failed-copy-{job_id}"
                    os.replace(target, preserve)
                    rollback["preserved_copy_path"] = str(preserve)
                if moved_empty and empty_target_hold.exists() and not target.exists():
                    os.replace(empty_target_hold, target)
                if stage.exists(): shutil.rmtree(stage)
                dashboardctl.refresh_data_paths()
                compatibility_bridge.refresh_registered_bridges(
                    source,
                    migration_lock_held=True,
                    accepted_data_roots=(target,),
                )
                # Clear the recovery record only after config and every
                # registered legacy data link agree on the rollback source.
                journal_path.unlink(missing_ok=True)
        except OSError:
            rollback["succeeded"] = False
        except Exception:
            rollback["succeeded"] = False
        if stage.exists() and not journal_written:
            try:
                shutil.rmtree(stage)
            except OSError:
                rollback["succeeded"] = False
        result = {
            "job_id": job_id, "status": "failed", "progress": 0,
            "current_path": str(waylume_paths.data_dir()), "backup_path": backup_path,
            "verification": {"verified": False}, "rollback": rollback,
            "message": str(exc) if str(exc) in {"source_changed_since_preview", "target_not_empty", "migration_verification_failed", "data_dir_environment_override"} else "迁移未完成，原数据目录未切换；请保留现场并重新检查。",
        }
        with _guard:
            _jobs[job_id] = dict(result)
        return result


def job(job_id: str) -> dict[str, Any] | None:
    with _guard:
        value = _jobs.get(job_id)
        return dict(value) if value else None
