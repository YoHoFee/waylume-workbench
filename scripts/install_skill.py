#!/usr/bin/env python3
"""Safely link the Waylume assistant Skill into supported host skill directories."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import sys
from pathlib import Path
from typing import Optional


SKILL_NAME = "waylume-assistant"
REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "skills" / SKILL_NAME
HOSTS = ("codex", "antigravity", "dsh")


def exists_app(path: Path) -> bool:
    return path.exists()


def dsh_version() -> Optional[str]:
    plist = Path("/Applications/DeepSeek Harness.app/Contents/Info.plist")
    try:
        with plist.open("rb") as stream:
            return str(plistlib.load(stream).get("CFBundleShortVersionString", "unknown"))
    except (OSError, plistlib.InvalidFileException):
        return None


def host_info(home: Path) -> dict[str, dict[str, object]]:
    codex_cli = shutil.which("codex")
    agy_cli = shutil.which("agy")
    agy_app = Path("/Applications/Antigravity.app")
    dsh_app = Path("/Applications/DeepSeek Harness.app")
    return {
        "codex": {
            "detected": bool(codex_cli),
            "evidence": codex_cli or "codex CLI not found",
            "target": home / ".codex" / "skills" / SKILL_NAME,
            "runtime": "requires a fresh Codex session check",
        },
        "antigravity": {
            "detected": bool(agy_cli or exists_app(agy_app)),
            "evidence": agy_cli or (str(agy_app) if exists_app(agy_app) else "agy CLI/Antigravity app not found"),
            "target": home / ".gemini" / "config" / "skills" / SKILL_NAME,
            "runtime": "requires a fresh Antigravity session check",
        },
        "dsh": {
            "detected": exists_app(dsh_app),
            "evidence": (f"DeepSeek Harness {dsh_version() or 'version unknown'}" if exists_app(dsh_app) else "DeepSeek Harness app not found"),
            "target": home / ".agents" / "skills" / SKILL_NAME,
            "runtime": "unverified until a fresh DSH session discovers and loads the Skill",
        },
    }


def target_state(target: Path) -> str:
    if not (target.exists() or target.is_symlink()):
        return "absent"
    if target.is_symlink():
        try:
            return "linked-here" if target.resolve(strict=True) == SOURCE.resolve() else f"conflicting-link -> {os.readlink(target)}"
        except (OSError, RuntimeError):
            return f"broken-link -> {os.readlink(target)}"
    return "conflicting-existing-file-or-directory"


def install(target: Path) -> tuple[bool, str]:
    state = target_state(target)
    if state == "linked-here":
        return True, "already linked; no change"
    if state != "absent":
        return False, f"refused to overwrite: {state}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(SOURCE, target_is_directory=True)
        return True, "linked to repository Skill; runtime still needs host-session validation"
    except OSError as exc:
        return False, f"install failed: {exc}"


def uninstall(target: Path) -> tuple[bool, str]:
    state = target_state(target)
    if state == "absent":
        return True, "not installed"
    if state != "linked-here":
        return False, f"refused to remove: {state}"
    try:
        target.unlink()
        return True, "owned Skill link removed"
    except OSError as exc:
        return False, f"uninstall failed: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="read-only host and installation check (default)")
    action.add_argument("--install", nargs="+", metavar="HOST", help="link selected hosts: codex antigravity dsh all")
    action.add_argument("--uninstall", nargs="+", metavar="HOST", help="remove only links that still point to this checkout")
    parser.add_argument("--home", type=Path, help="override home directory (for isolated testing; host detection still checks this Mac)")
    args = parser.parse_args()
    selected = args.install if args.install is not None else args.uninstall
    if selected:
        if "all" in selected:
            selected = list(HOSTS)
        unknown = set(selected) - set(HOSTS)
        if unknown:
            parser.error("unknown host(s): " + ", ".join(sorted(unknown)))

    if not SOURCE.is_dir() or not (SOURCE / "SKILL.md").is_file():
        print(f"ERROR: Skill source missing or incomplete: {SOURCE}", file=sys.stderr)
        return 2

    home = args.home.expanduser().resolve() if args.home else Path.home()
    info = host_info(home)
    print(f"Skill source: {SOURCE}")
    if args.home:
        print(f"Target home override: {home}")
    failures = 0
    for name in (selected or HOSTS):
        host = info[name]
        target = Path(host["target"])
        state = target_state(target)
        detected = bool(host["detected"])
        print(f"\n{name}: {'host detected' if detected else 'host not detected'} ({host['evidence']})")
        if args.install is not None:
            if not detected:
                print(f"  SKIP: {host['evidence']}; no files written")
                continue
            ok, message = install(target)
            print(f"  {'OK' if ok else 'ERROR'}: {target}: {message}")
            failures += not ok
        elif args.uninstall is not None:
            ok, message = uninstall(target)
            print(f"  {'OK' if ok else 'ERROR'}: {target}: {message}")
            failures += not ok
        else:
            print(f"  files: {state} ({target})")
            print(f"  runtime: {host['runtime']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
