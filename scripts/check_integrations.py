#!/usr/bin/env python3
"""Run the checks this process can actually perform; never invent host MCP results."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import integrations


def main() -> None:
    parser = argparse.ArgumentParser(description="Run read-only Waylume integration checks")
    parser.add_argument("providers", nargs="*", choices=integrations.PROVIDERS)
    args = parser.parse_args()
    selected = args.providers or list(integrations.PROVIDERS)
    results = {}
    for provider in selected:
        outcome = integrations.check(provider)
        record = outcome["provider"]
        results[provider] = {"status": record["status"], "checked_at": record.get("checked_at"),
                             "verification_method": record.get("verification_method"),
                             "check_outcome": outcome.get("check_outcome", "checked")}
    print(json.dumps({"providers": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
