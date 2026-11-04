"""Portable user configuration and data paths for the Waylume workbench."""
from __future__ import annotations

import json
import os
import fcntl
import hashlib
import base64
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CODE_ROOT = Path(__file__).resolve().parent
DATA_ENV = "WAYLUME_DATA_DIR"

LEGACY_DASHBOARD_DATA_DIRS = {"records", "backups", "reports", "sources", "logs"}
LEGACY_DASHBOARD_DATA_FILES = {
    "meta.json", "store.json", "store.json.migrated.bak", "events.jsonl",
    "events.jsonl.migrated.bak", "scope-catalog.json", "source-seed.json",
}


def legacy_dashboard_data_entries(root: Path) -> set[str]:
    """Names allowed from a mixed legacy dashboard root.

    Core-owned ledger names are fixed; optional integration subtrees are
    admitted only when a live compatibility-link registration proves they
    belong to this data root.
    """
    entries = set(LEGACY_DASHBOARD_DATA_DIRS) | set(LEGACY_DASHBOARD_DATA_FILES)
    entries.update(registered_data_path_subtrees(root))
    return entries


def registered_data_path_subtrees(root: Path) -> tuple[str, ...]:
    """Return optional data subtrees proven by the current bridge registry."""
    try:
        import compatibility_bridge
        return compatibility_bridge.registered_data_path_subtrees(root)
    except ImportError:
        # An absent optional bridge module never makes the core dashboard
        # unavailable; optional data is simply not selected for migration.
        return ()


def validate_data_dir(path: str | Path) -> Path:
    """Reject data paths that overlap the checked-out source tree."""
    candidate = Path(path).expanduser().resolve()
    code = CODE_ROOT.resolve()
    if candidate == code or code in candidate.parents or candidate in code.parents:
        raise ValueError("unsafe_data_path")
    if candidate == Path(candidate.anchor):
        raise ValueError("unsafe_data_path")
    return candidate


def default_data_dir() -> Path:
    return Path.home() / ".waylume"


def config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Waylume"
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / "Waylume"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "waylume"


def sys_platform() -> str:
    import sys
    return sys.platform


def settings_path() -> Path:
    return config_dir() / "settings.json"


def service_lock_path() -> Path:
    """Stable singleton lock that does not move with the selected data root."""
    return config_dir() / "service.lock"


def migration_lock_path() -> Path:
    return config_dir() / "data-migration.lock"


def migration_journal_path() -> Path:
    return config_dir() / "data-migration-journal.json"


def _atomic_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp_name, path)
        try:
            dfd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(dfd)
            finally: os.close(dfd)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp_name): os.unlink(tmp_name)


def write_migration_journal(journal: dict[str, Any]) -> None:
    raw = (json.dumps(journal, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    _atomic_bytes(migration_journal_path(), raw)


def _manifest_digest(root: Path, policy: str = "all") -> str | None:
    if not root.is_dir() or root.is_symlink(): return None
    legacy_entries = legacy_dashboard_data_entries(root) if policy == "legacy_dashboard_data" else set()
    entries = []
    try:
        for current, dirs, names in os.walk(root, followlinks=False):
            base = Path(current)
            dirs[:] = sorted(d for d in dirs if not (base == root and d == "__pycache__")
                             and not (base == root and policy == "legacy_dashboard_data" and d not in legacy_entries))
            if any((base / dirname).is_symlink() for dirname in dirs): return None
            for name in sorted(names):
                if base == root and policy == "legacy_dashboard_data" and name not in legacy_entries:
                    continue
                path = base / name
                if name in {".store.lock", ".server.lock", ".migration.lock", ".transaction.json"}: continue
                if path.is_symlink() or not path.is_file(): return None
                digest = hashlib.sha256(); size = 0
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block); size += len(block)
                entries.append({"path": path.relative_to(root).as_posix(), "size": size, "sha256": digest.hexdigest()})
    except OSError:
        return None
    entries.sort(key=lambda row: row["path"])
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def recover_pending_migration() -> None:
    """Finish a verified copy or restore the prior config after a process crash.

    Recovery is deliberately fail-closed when a target changed after a crash;
    it never removes an installed target copy or moves an unexpected directory.
    """
    # The patrol alias has its own journal but shares this stable migration
    # lock. Recover it before the dashboard data-root journal so both views do
    # not observe different roots after a crash. The fast path is read-only and
    # does not touch user configuration unless a patrol journal exists.
    patrol_journal = config_dir() / "patrol-data-migration.json"
    if patrol_journal.exists() or patrol_journal.is_symlink():
        lock_path = migration_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lock_path.open("a", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            try:
                import patrol_compat
            except ImportError as exc:
                raise ValueError("optional_integration_recovery_unavailable") from exc
            patrol_compat.recover(migration_lock_held=True)
    if os.environ.get(DATA_ENV): return
    journal_path = migration_journal_path()
    if not journal_path.is_file(): return
    if journal_path.is_symlink(): raise ValueError("migration_recovery_journal_unsafe")
    lock_path = migration_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # An active migration owns this stable lock, including when its
            # request runs in this server process. PID equality alone cannot
            # distinguish that from a stale journal left by an earlier request.
            return
        if not journal_path.is_file(): return
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            target = Path(journal["target_path"])
            source = Path(journal["source_path"])
            stage = Path(journal["stage_path"])
            hold = Path(journal["empty_target_hold"])
            expected = journal["expected_manifest"]
            previous = base64.b64decode(journal["previous_settings_b64"], validate=True)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("migration_recovery_journal_invalid") from exc
        if (not isinstance(expected, str) or len(expected) != 64
                or validate_data_dir(target) != target.resolve()
                or validate_data_dir(source) != source.resolve()
                or target.parent != stage.parent or target.parent != hold.parent
                or not stage.name.startswith("." + target.name + ".waylume-stage-")
                or not hold.name.startswith("." + target.name + ".empty-preserved-")):
            raise ValueError("migration_recovery_journal_unsafe")

        current_settings = _read_settings()
        policy = journal.get("manifest_policy", "all")
        if policy not in {"all", "legacy_dashboard_data"}:
            raise ValueError("migration_recovery_journal_unsafe")
        target_digest = _manifest_digest(target)
        target_verified = target_digest == expected
        if target_verified:
            current_settings["data_dir"] = str(target)
            current_settings["last_migration"] = {
                "backup_path": journal["source_path"], "verified_at": journal["verified_at"],
                "manifest_sha256": expected,
            }
            raw = (json.dumps(current_settings, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            _atomic_bytes(settings_path(), raw)
            # Directory bridges are part of the migration commit. Keep the
            # journal until both configuration and links agree on the target.
            import compatibility_bridge
            compatibility_bridge.refresh_registered_bridges(target, migration_lock_held=True)
            if stage.exists(): shutil.rmtree(stage)
            if hold.exists() and hold.is_dir() and not any(hold.iterdir()): hold.rmdir()
            journal_path.unlink()
            return

        if target.exists():
            if journal.get("state") not in {"target_installed", "settings_switched", "committed"}:
                # The crash may have happened between rename and journal update.
                # Without a recorded install phase, ownership is ambiguous.
                raise ValueError("migration_recovery_target_unverified")
            quarantine = target.parent / f".{target.name}.failed-copy-{journal.get('job_id', 'recovery')}"
            if quarantine.exists() or quarantine.is_symlink():
                raise ValueError("migration_recovery_target_quarantine_conflict")
            os.replace(target, quarantine)

        if current_settings.get("data_dir") == str(target):
            if journal.get("settings_existed"):
                _atomic_bytes(settings_path(), previous)
            else:
                settings_path().unlink(missing_ok=True)
        # Restore links before clearing the recovery record. If this fails,
        # the journal remains so the next startup can retry consistently.
        import compatibility_bridge
        compatibility_bridge.refresh_registered_bridges(
            source,
            migration_lock_held=True,
            accepted_data_roots=(target,),
        )
        if stage.exists():
            # This path is uniquely generated and recorded before copy begins.
            if stage.is_symlink() or not stage.is_dir():
                raise ValueError("migration_recovery_stage_unsafe")
            shutil.rmtree(stage)
        if hold.exists():
            if target.exists(): raise ValueError("migration_recovery_target_conflict")
            os.replace(hold, target)
        journal_path.unlink()


def _read_settings() -> dict[str, Any]:
    path = settings_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("settings_unavailable") from exc
    if not isinstance(value, dict):
        raise ValueError("settings_invalid")
    return value


def data_dir() -> Path:
    recover_pending_migration()
    env = os.environ.get(DATA_ENV)
    if env:
        return validate_data_dir(env)
    configured = _read_settings().get("data_dir")
    if configured:
        if not isinstance(configured, str):
            raise ValueError("settings_invalid")
        return validate_data_dir(configured)
    return validate_data_dir(default_data_dir())


def legacy_data_dir() -> Path:
    """Historical runtime location, exposed only for explicit migration review."""
    return Path.home() / ".codex" / "work-agent" / "tools" / "mcloud" / "dashboard"


def write_data_dir(path: Path, *, migration: dict[str, Any] | None = None) -> None:
    """Atomically switch the configured data root; caller owns migration locking."""
    if os.environ.get(DATA_ENV):
        raise ValueError("data_dir_environment_override")
    target = path.expanduser().resolve()
    config = settings_path()
    config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = _read_settings()
    settings["data_dir"] = str(target)
    if migration is not None:
        settings["last_migration"] = dict(migration)
    raw = (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_bytes(config, raw)


@dataclass(frozen=True)
class DataPaths:
    root: Path
    legacy: Path
    settings: Path
    default: Path


def paths() -> DataPaths:
    return DataPaths(data_dir(), legacy_data_dir(), settings_path(), default_data_dir().resolve())
