#!/usr/bin/env python3
"""Increment the workbench patch (iteration) number in VERSION.

Major and minor are managed by the user; this script only adds one to the last
component, so each update can be released as major.minor.iteration. Pass
--dry-run to preview without writing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VERSION = Path(__file__).resolve().parents[1] / "VERSION"
PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Increment the workbench iteration number.")
    parser.add_argument("--dry-run", action="store_true", help="print the next version without writing it")
    parser.add_argument("--set", metavar="MAJOR.MINOR.ITERATION", help="explicitly set a version (user-managed major/minor)")
    args = parser.parse_args()
    raw = VERSION.read_text(encoding="utf-8").strip()
    match = PATTERN.fullmatch(raw)
    if args.set:
        if not PATTERN.fullmatch(args.set.strip()):
            print(f"invalid version: {args.set!r}", file=sys.stderr)
            return 1
        nxt = args.set.strip()
    elif match:
        major, minor, patch = (int(part) for part in match.groups())
        nxt = f"{major}.{minor}.{patch + 1}"
    else:
        print(f"invalid version: {raw!r}", file=sys.stderr)
        return 1
    if not args.dry_run:
        VERSION.write_text(nxt + "\n", encoding="utf-8")
    print(nxt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
