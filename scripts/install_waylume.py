#!/usr/bin/env python3
"""Install or inspect the Waylume workbench service and assistant Skill links."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
SKILL_INSTALLER = ROOT / "scripts" / "install_skill.py"
SERVICE_MANAGER = ROOT / "manage_service.py"
DASHBOARDCTL = ROOT / "dashboardctl.py"


def run(label: str, *args: str, check: bool = True, env: Optional[dict] = None) -> int:
    print(f"\n== {label} ==", flush=True)
    result = subprocess.run([PYTHON, *args], cwd=ROOT, check=False, env=env)
    if result.returncode and check:
        raise RuntimeError(f"{label} failed (exit {result.returncode}); completed earlier steps were kept safely. Re-run after fixing the reported issue.")
    return result.returncode


def preflight() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("The local server's LaunchAgent setup currently supports macOS only.")
    if sys.version_info < (3, 9):
        raise RuntimeError("Python 3.9 or newer is required.")
    missing = [str(path.relative_to(ROOT)) for path in (SKILL_INSTALLER, SERVICE_MANAGER, DASHBOARDCTL) if not path.is_file()]
    if missing:
        raise RuntimeError("Required installer component(s) are missing: " + ", ".join(missing))


def validate_selected_data_root(env: dict) -> None:
    """Validate the resolved user data path before any install step can write."""
    validation_code = (
        "import os, sys; from pathlib import Path; import waylume_paths; "
        "override = os.environ.get(waylume_paths.DATA_ENV); "
        "candidate = override or waylume_paths._read_settings().get('data_dir') or waylume_paths.default_data_dir(); "
        "print(waylume_paths.validate_data_dir(candidate))"
    )
    result = subprocess.run(
        [PYTHON, "-B", "-c", validation_code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError("Data directory validation failed before installation: " + (detail or "unsafe or unavailable path"))
    print("Validated data directory:", result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check", action="store_true", help="read-only check of paths, service, and Skill links")
    actions.add_argument("--install", action="store_true", help="initialize an empty data store, install detected Skills and start the local service")
    actions.add_argument("--uninstall", action="store_true", help="stop/remove the owned service and Skill links; keep all user data")
    parser.add_argument("--hosts", nargs="+", choices=("codex", "antigravity", "dsh", "all"), default=["all"], help="hosts to link; default is detected hosts")
    parser.add_argument("--home", type=Path, help="override home/LaunchAgents roots for isolated testing")
    parser.add_argument("--data-dir", type=Path, help="override workbench data root (sets WAYLUME_DATA_DIR)")
    args = parser.parse_args()

    try:
        preflight()
        hosts = args.hosts
        if "all" in hosts:
            hosts = ["all"]
        extra_args = ["--home", str(args.home.expanduser().resolve())] if args.home else []
        env = dict(os.environ)
        if args.data_dir:
            env["WAYLUME_DATA_DIR"] = str(args.data_dir.expanduser().resolve())
        if args.check:
            run("Skill and host check", str(SKILL_INSTALLER), "--check", *extra_args, env=env)
            run("Workbench service status", str(SERVICE_MANAGER), "status", *extra_args, check=False, env=env)
            return 0
        if args.install:
            if args.home and not args.data_dir:
                raise RuntimeError("--home for isolated installation also requires --data-dir; refusing to initialize the real user data directory")
            # Refuse unsafe overrides before init, Skill linking, or LaunchAgent writes.
            validate_selected_data_root(env)
            # Empty initialization is idempotent. Legacy data is never imported implicitly.
            run("Initialize default/selected data store", str(DASHBOARDCTL), "init", env=env)
            run("Install Skill links", str(SKILL_INSTALLER), "--install", *hosts, *extra_args, env=env)
            service_args = ["--launch-agents-dir", str(args.home.expanduser().resolve() / "Library" / "LaunchAgents")] if args.home else []
            install_args = ["install", *extra_args, *service_args]
            if args.home:
                install_args.append("--no-bootstrap")
            run("Install per-user LaunchAgent", str(SERVICE_MANAGER), *install_args, env=env)
            if args.home:
                print("\nIsolated install staged successfully. The LaunchAgent was not loaded; no launchctl action was performed.")
            else:
                status = subprocess.run([PYTHON, str(SERVICE_MANAGER), "status"], cwd=ROOT, check=False, capture_output=True, text=True, env=env)
                if status.returncode:
                    raise RuntimeError(f"Service status check failed (exit {status.returncode}); installation files were retained.")
                if status.stdout.strip() != "running":
                    run("Start local workbench service", str(SERVICE_MANAGER), "start", env=env)
                else:
                    print("\nService is already running.")
                print("\nWaylume installation steps completed. Verify Skill discovery in each newly opened host session.")
            return 0
        # Removing the app does not remove user records or configuration.
        service_args = ["--launch-agents-dir", str(args.home.expanduser().resolve() / "Library" / "LaunchAgents")] if args.home else []
        run("Remove owned LaunchAgent", str(SERVICE_MANAGER), "uninstall", *extra_args, *service_args, env=env)
        run("Remove owned Skill links", str(SKILL_INSTALLER), "--uninstall", *hosts, *extra_args, env=env)
        print("\nWaylume service and selected Skill links removed. User data and settings were retained.")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
