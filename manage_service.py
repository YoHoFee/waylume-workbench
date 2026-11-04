#!/usr/bin/env python3
"""Install and control the per-user Waylume macOS LaunchAgent safely."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path


LABEL = "com.waylume.workbench"
LEGACY_LABEL = "com.mcloud.personal-dashboard"


def paths(args):
    home = Path(args.home or Path.home()).expanduser().resolve()
    launch_agents = Path(args.launch_agents_dir or os.environ.get("WAYLUME_LAUNCH_AGENTS_DIR") or home / "Library" / "LaunchAgents").expanduser().resolve()
    project = Path(args.project or Path(__file__).resolve().parent).expanduser().resolve()
    data = Path(args.data_dir or os.environ.get("WAYLUME_DATA_DIR") or home / ".waylume").expanduser().resolve()
    return home, launch_agents, project, data


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def _legacy_dashboard_is_loaded() -> bool:
    return _launchctl("print", "gui/" + str(os.getuid()) + "/" + LEGACY_LABEL).returncode == 0


def install(args):
    home, agents, project, data = paths(args)
    if sys.platform != "darwin": raise RuntimeError("launchagent_requires_macos")
    if args.home and not args.launch_agents_dir:
        raise RuntimeError("isolated_home_requires_launch_agents_dir")
    if args.data_dir and not args.home:
        raise RuntimeError("data_dir_override_allowed_only_for_isolated_install")
    # The old dashboard may own port 8765 and the existing BUG patrol. Never
    # start a second service beside it; a reviewed migration must update that
    # single owner in place and preserve its schedule/state.
    if not args.home and _legacy_dashboard_is_loaded():
        raise RuntimeError("legacy_dashboard_service_loaded; migrate_single_service_in_place")
    if not (project / "server.py").is_file(): raise RuntimeError("project_server_missing")
    if agents.is_symlink(): raise RuntimeError("launchagents_symlink_not_allowed")
    agents.mkdir(parents=True, exist_ok=True, mode=0o700)
    plist = agents / (LABEL + ".plist")
    if plist.exists() or plist.is_symlink(): raise RuntimeError("launchagent_already_exists")
    log_dir = data / "logs"
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(data, 0o700)
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = {"PYTHONUNBUFFERED": "1"}
    # A pinned data directory is only allowed for a non-bootstrapped isolated
    # install. Normal LaunchAgents resolve ~/.waylume and settings.json at runtime
    # so the UI can switch locations without reinstalling the service.
    if args.data_dir:
        environment["WAYLUME_DATA_DIR"] = str(data)
    spec = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(project / "server.py")],
        "WorkingDirectory": str(project),
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": environment,
        "StandardOutPath": str(log_dir / "service.log"),
        "StandardErrorPath": str(log_dir / "service-error.log"),
    }
    fd, temp_name = tempfile.mkstemp(prefix="." + LABEL, dir=agents)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(spec, stream, fmt=plistlib.FMT_XML, sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp_name, plist)
        os.chmod(plist, 0o600)
    finally:
        if os.path.exists(temp_name): os.unlink(temp_name)
    if not args.no_bootstrap and not args.home:
        domain = "gui/" + str(os.getuid())
        result = _launchctl("bootstrap", domain, str(plist))
        if result.returncode != 0:
            plist.unlink(missing_ok=True)
            raise RuntimeError("launchagent_bootstrap_failed_rolled_back")
    print("installed", plist)


def control(action, args):
    home, agents, project, data = paths(args)
    domain = "gui/" + str(os.getuid())
    plist, service_label = _owned_service(agents, project)
    if action == "start":
        if plist is None: raise RuntimeError("launchagent_not_installed")
        result = _launchctl("kickstart", "-k", domain + "/" + service_label)
    elif action == "stop":
        if plist is None: raise RuntimeError("launchagent_not_installed")
        result = _launchctl("kill", "SIGTERM", domain + "/" + service_label)
    elif action == "status":
        result = _launchctl("print", domain + "/" + service_label) if plist else None
        print("running" if result and result.returncode == 0 else "not_running")
        return
    elif action == "uninstall":
        if plist:
            if not args.home: _launchctl("bootout", domain, str(plist))
            plist.unlink()
        print("uninstalled; user data retained", data)
        return
    if result.returncode != 0: raise RuntimeError("launchagent_" + action + "_failed")
    print(action + "_requested")


def _owned_service(agents: Path, project: Path) -> tuple[Path | None, str | None]:
    """Find this project's installed service, including an in-place legacy Label.

    A legacy plist is controllable only after its Label and executable path both
    point at this checkout. This prevents start/stop/status from operating on a
    same-named service owned by another project.
    """
    expected_server = str((project / "server.py").resolve())
    matches = []
    for label in (LABEL, LEGACY_LABEL):
        path = agents / (label + ".plist")
        if not path.exists() and not path.is_symlink():
            continue
        stored = _load_plist(path, label)
        arguments = stored.get("ProgramArguments")
        if (isinstance(arguments, list) and arguments
                and str(Path(arguments[-1]).expanduser().resolve()) == expected_server):
            matches.append((path, label))
    if len(matches) > 1:
        raise RuntimeError("multiple_owned_launchagents")
    return matches[0] if matches else (None, None)


def _atomic_replace_bytes(path: Path, content: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _load_plist(path: Path, expected_label: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("launchagent_plist_unavailable")
    try:
        value = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise RuntimeError("launchagent_plist_invalid") from exc
    if not isinstance(value, dict) or value.get("Label") != expected_label:
        raise RuntimeError("launchagent_label_mismatch")
    return value


def _legacy_journal_path(plist: Path) -> Path:
    return plist.with_name(plist.name + ".waylume-migration.json")


def _write_service_journal(path: Path, value: dict) -> None:
    _atomic_replace_bytes(path, (json.dumps(value, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)


def _read_service_journal(path: Path, agents: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.parent.resolve() != agents:
        raise RuntimeError("legacy_migration_journal_unavailable")
    if path.stat().st_mode & 0o077:
        raise RuntimeError("legacy_migration_journal_permissions_invalid")
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("legacy_migration_journal_invalid") from exc
    if not isinstance(journal, dict) or journal.get("version") != 1:
        raise RuntimeError("legacy_migration_journal_invalid")
    backup = Path(journal.get("backup", ""))
    staged = Path(journal.get("staged", ""))
    if (backup.parent.resolve() != agents or not backup.name.startswith(path.name.replace(".waylume-migration.json", ".waylume-backup-"))
            or staged.parent.resolve() != agents or not staged.name.startswith("." + LEGACY_LABEL + ".plist.waylume-stage-")):
        raise RuntimeError("legacy_migration_journal_unsafe")
    for key in ("original_sha256", "updated_sha256"):
        value = journal.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise RuntimeError("legacy_migration_journal_invalid")
    return journal


def recover_legacy_in_place(args):
    if sys.platform != "darwin": raise RuntimeError("launchagent_requires_macos")
    home, agents, project, data = paths(args)
    legacy_plist = Path(args.legacy_plist or agents / (LEGACY_LABEL + ".plist")).expanduser()
    if legacy_plist.parent.resolve() != agents or legacy_plist.name != LEGACY_LABEL + ".plist":
        raise RuntimeError("legacy_plist_path_mismatch")
    journal_path = _legacy_journal_path(legacy_plist)
    journal = _read_service_journal(journal_path, agents)
    backup = Path(journal["backup"])
    original = backup.read_bytes()
    if hashlib.sha256(original).hexdigest() != journal["original_sha256"]:
        raise RuntimeError("legacy_migration_backup_hash_mismatch")
    current = legacy_plist.read_bytes()
    current_hash = hashlib.sha256(current).hexdigest()
    domain = "gui/" + str(os.getuid())
    service = domain + "/" + LEGACY_LABEL
    loaded = _launchctl("print", service).returncode == 0
    if current_hash == journal["original_sha256"]:
        if not loaded:
            if _launchctl("bootstrap", domain, str(legacy_plist)).returncode != 0:
                raise RuntimeError("legacy_migration_recovery_bootstrap_failed")
        if _launchctl("print", service).returncode != 0:
            raise RuntimeError("legacy_migration_recovery_health_check_failed")
        _legacy_journal_path(legacy_plist).unlink()
        Path(journal["staged"]).unlink(missing_ok=True)
        return {"recovered": True, "action": "restored_or_kept_legacy_service", "backup_retained": str(backup)}
    if current_hash != journal["updated_sha256"]:
        raise RuntimeError("legacy_migration_plist_drifted")
    if not loaded:
        if _launchctl("bootstrap", domain, str(legacy_plist)).returncode != 0:
            _atomic_replace_bytes(legacy_plist, original)
            if _launchctl("bootstrap", domain, str(legacy_plist)).returncode != 0:
                raise RuntimeError("legacy_migration_recovery_rollback_failed; backup_retained=" + str(backup))
            if _launchctl("print", service).returncode != 0:
                raise RuntimeError("legacy_migration_recovery_rollback_health_check_failed; backup_retained=" + str(backup))
            journal_path.unlink()
            return {"recovered": True, "action": "rolled_back_to_legacy", "backup_retained": str(backup)}
        loaded = _launchctl("print", service).returncode == 0
    if not loaded:
        raise RuntimeError("legacy_migration_recovery_health_check_failed")
    journal_path.unlink()
    Path(journal["staged"]).unlink(missing_ok=True)
    return {"recovered": True, "action": "completed_new_service", "backup_retained": str(backup)}


def migrate_legacy_in_place(args):
    """Retarget the existing legacy service without creating a second Label."""
    if sys.platform != "darwin": raise RuntimeError("launchagent_requires_macos")
    home, agents, project, data = paths(args)
    if not (project / "server.py").is_file(): raise RuntimeError("project_server_missing")
    legacy_plist = Path(args.legacy_plist or agents / (LEGACY_LABEL + ".plist")).expanduser()
    if legacy_plist.parent.resolve() != agents or legacy_plist.name != LEGACY_LABEL + ".plist":
        raise RuntimeError("legacy_plist_path_mismatch")
    current = _load_plist(legacy_plist, LEGACY_LABEL)
    if not isinstance(current.get("ProgramArguments"), list) or not current["ProgramArguments"]:
        raise RuntimeError("legacy_launchagent_arguments_invalid")
    if (agents / (LABEL + ".plist")).exists() or (agents / (LABEL + ".plist")).is_symlink():
        raise RuntimeError("second_launchagent_present")
    journal_path = _legacy_journal_path(legacy_plist)
    if journal_path.exists() or journal_path.is_symlink():
        raise RuntimeError("legacy_migration_recovery_required")
    import waylume_paths
    if os.environ.get(waylume_paths.DATA_ENV):
        raise RuntimeError("data_dir_environment_override")
    data = waylume_paths.data_dir()
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(logs, 0o700)

    original_bytes = legacy_plist.read_bytes()
    backup = legacy_plist.with_name(legacy_plist.name + ".waylume-backup-" + str(int(time.time())) + "-" + secrets.token_hex(4))
    with backup.open("xb") as stream:
        os.chmod(backup, 0o600)
        stream.write(original_bytes)
        stream.flush(); os.fsync(stream.fileno())

    updated = dict(current)
    updated["ProgramArguments"] = [sys.executable, str(project / "server.py")]
    updated["WorkingDirectory"] = str(project)
    environment = dict(updated.get("EnvironmentVariables") or {})
    environment.pop("WAYLUME_DATA_DIR", None)
    environment.setdefault("PYTHONUNBUFFERED", "1")
    updated["EnvironmentVariables"] = environment
    updated["StandardOutPath"] = str(logs / "service.log")
    updated["StandardErrorPath"] = str(logs / "service-error.log")
    staged = legacy_plist.parent / ("." + legacy_plist.name + ".waylume-stage-" + secrets.token_hex(6))
    staged_bytes = plistlib.dumps(updated, fmt=plistlib.FMT_XML, sort_keys=True)
    with staged.open("xb") as stream:
        os.chmod(staged, 0o600)
        stream.write(staged_bytes)
        stream.flush(); os.fsync(stream.fileno())
    journal = {"version": 1, "phase": "prepared", "backup": str(backup), "staged": str(staged),
               "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
               "updated_sha256": hashlib.sha256(staged_bytes).hexdigest()}
    _write_service_journal(journal_path, journal)

    domain = "gui/" + str(os.getuid())
    service = domain + "/" + LEGACY_LABEL
    loaded = _launchctl("print", service).returncode == 0
    journal["phase"] = "stopping"
    _write_service_journal(journal_path, journal)
    if loaded and _launchctl("bootout", domain, str(legacy_plist)).returncode != 0:
        staged.unlink(missing_ok=True)
        journal_path.unlink(missing_ok=True)
        raise RuntimeError("legacy_launchagent_stop_failed; plist_backup=" + str(backup))
    try:
        os.replace(staged, legacy_plist)
        journal["phase"] = "plist_replaced"
        _write_service_journal(journal_path, journal)
        if _launchctl("bootstrap", domain, str(legacy_plist)).returncode != 0:
            raise RuntimeError("legacy_launchagent_bootstrap_failed")
        if _launchctl("print", service).returncode != 0:
            raise RuntimeError("legacy_launchagent_health_check_failed")
        journal["phase"] = "bootstrapped"
        _write_service_journal(journal_path, journal)
    except Exception as exc:
        if _launchctl("print", service).returncode == 0:
            _launchctl("bootout", domain, str(legacy_plist))
        _atomic_replace_bytes(legacy_plist, original_bytes)
        if _launchctl("bootstrap", domain, str(legacy_plist)).returncode != 0:
            raise RuntimeError("legacy_launchagent_restore_failed; plist_backup=" + str(backup)) from exc
        journal_path.unlink(missing_ok=True)
        raise RuntimeError("legacy_launchagent_migration_rolled_back; plist_backup=" + str(backup)) from exc
    finally:
        if staged.exists(): staged.unlink()
    journal_path.unlink(missing_ok=True)
    print("migrated_in_place", legacy_plist, "backup", backup)
    return {"plist": str(legacy_plist), "backup": str(backup), "label": LEGACY_LABEL,
            "project": str(project), "data_root_resolved_at_runtime": True}


def rollback_legacy_in_place(args):
    if sys.platform != "darwin": raise RuntimeError("launchagent_requires_macos")
    home, agents, project, data = paths(args)
    legacy_plist = Path(args.legacy_plist or agents / (LEGACY_LABEL + ".plist")).expanduser()
    backup = Path(args.backup_plist).expanduser()
    if legacy_plist.parent.resolve() != agents or legacy_plist.name != LEGACY_LABEL + ".plist":
        raise RuntimeError("legacy_plist_path_mismatch")
    if backup.parent.resolve() != agents or not backup.name.startswith(LEGACY_LABEL + ".plist.waylume-backup-"):
        raise RuntimeError("legacy_plist_backup_path_mismatch")
    _load_plist(legacy_plist, LEGACY_LABEL)
    _load_plist(backup, LEGACY_LABEL)
    original_bytes = backup.read_bytes()
    domain = "gui/" + str(os.getuid())
    service = domain + "/" + LEGACY_LABEL
    if _launchctl("print", service).returncode == 0 and _launchctl("bootout", domain, str(legacy_plist)).returncode != 0:
        raise RuntimeError("legacy_launchagent_stop_failed")
    _atomic_replace_bytes(legacy_plist, original_bytes)
    if _launchctl("bootstrap", domain, str(legacy_plist)).returncode != 0:
        raise RuntimeError("legacy_launchagent_rollback_bootstrap_failed; backup_retained=" + str(backup))
    if _launchctl("print", service).returncode != 0:
        raise RuntimeError("legacy_launchagent_rollback_health_check_failed; backup_retained=" + str(backup))
    print("rolled_back_in_place", legacy_plist, "backup_retained", backup)
    return {"plist": str(legacy_plist), "backup": str(backup), "label": LEGACY_LABEL, "backup_retained": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "start", "stop", "status", "uninstall", "migrate-in-place", "rollback-in-place", "recover-in-place"))
    parser.add_argument("--home")
    parser.add_argument("--launch-agents-dir")
    parser.add_argument("--project")
    parser.add_argument("--data-dir")
    parser.add_argument("--legacy-plist")
    parser.add_argument("--backup-plist")
    parser.add_argument("--no-bootstrap", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "install": install(args)
        elif args.action == "migrate-in-place": migrate_legacy_in_place(args)
        elif args.action == "recover-in-place": recover_legacy_in_place(args)
        elif args.action == "rollback-in-place":
            if not args.backup_plist: parser.error("rollback-in-place requires --backup-plist")
            rollback_legacy_in_place(args)
        else: control(args.action, args)
    except (OSError, RuntimeError) as exc:
        print("error:", str(exc), file=sys.stderr)
        if args.action == "migrate-in-place":
            legacy = Path(args.legacy_plist or (Path(args.launch_agents_dir).expanduser() if args.launch_agents_dir else Path.home() / "Library" / "LaunchAgents") / (LEGACY_LABEL + ".plist"))
            if _legacy_journal_path(legacy).is_file():
                command = [sys.executable, str(Path(__file__).resolve()), "recover-in-place"]
                for option, value in (("--home", args.home), ("--launch-agents-dir", args.launch_agents_dir), ("--project", args.project), ("--legacy-plist", args.legacy_plist)):
                    if value:
                        command.extend((option, value))
                print("manual recovery:", shlex.join(command), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
