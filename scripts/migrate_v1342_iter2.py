#!/usr/bin/env python3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboardctl

def run_migration():
    def _migrate():
        db = dashboardctl.load_store()
        now = dashboardctl.timestamp()

        # 1. Setup iterations
        iter_current = {
            "id": "ITER-13.4.2-iter2",
            "version_tag": "13.4.2 迭代二",
            "title": "13.4.2 迭代二",
            "status": "active",
            "target_release_date": None,
            "released_at": None,
            "created_at": "2026-11-05T00:00:00+08:00",
            "updated_at": now,
            "description": "13.4.2 迭代二当前版本（包含AI音乐与语音输入等）",
            "notes": []
        }

        iter_1340 = {
            "id": "ITER-13.4.0",
            "version_tag": "13.4.0",
            "title": "13.4.0",
            "status": "released",
            "target_release_date": "2026-11-06",
            "released_at": "2026-11-06T00:00:00+08:00",
            "created_at": "2026-10-15T00:00:00+08:00",
            "updated_at": now,
            "description": "13.4.0 历史发布版本（已上线）",
            "notes": []
        }

        db["iterations"] = [iter_current, iter_1340]
        db["current_iteration_id"] = "ITER-13.4.2-iter2"

        # 2. Assign tasks
        target_v1342_ids = {
            "LOCAL-20261105-filmcollection-bgm-opt",
            "LOCAL-20261105-ai-voice-input-prompt",
            "AGENT-WAYLUME-ITERATION-ARCHIVE-20261118"
        }

        v1342_count = 0
        v1340_count = 0

        for r in db.get("records", []):
            rid = r.get("id")
            if rid in target_v1342_ids:
                r["iteration_id"] = "ITER-13.4.2-iter2"
                v1342_count += 1
            else:
                r["iteration_id"] = "ITER-13.4.0"
                v1340_count += 1

        print(f"Assigned records: {v1342_count} to ITER-13.4.2-iter2, {v1340_count} to ITER-13.4.0")

        # 3. Relocate sources
        sources = db.get("sources", [])
        relocated_counts = {"global": 0, "iter2": 0, "iter1340": 0}

        for s in sources:
            sid = s.get("id")
            if sid == "SRC-MCLOUD-KDOCS-API":
                s["scope"] = "global"
                s["iteration_id"] = None
                s["slot"] = "api_doc"
                relocated_counts["global"] += 1
            elif sid == "SRC-MCLOUD-PROGRESS-API":
                s["scope"] = "global"
                s["iteration_id"] = None
                s["slot"] = "supplementary"
                relocated_counts["global"] += 1
            elif sid == "d3e2ed4c-bf66-4f00-a6e8-0dcc3ff4a572":
                s["scope"] = "iteration"
                s["iteration_id"] = "ITER-13.4.2-iter2"
                s["slot"] = "requirement_doc"
                relocated_counts["iter2"] += 1
            elif sid == "7e91a9f6-1787-430f-9d4a-0729cce0904c":
                s["scope"] = "iteration"
                s["iteration_id"] = "ITER-13.4.2-iter2"
                s["slot"] = "supplementary"
                relocated_counts["iter2"] += 1
            elif sid == "SRC-MCLOUD-TENCENT-ASSIGNMENT":
                s["scope"] = "iteration"
                s["iteration_id"] = "ITER-13.4.0"
                s["slot"] = "requirement_doc"
                relocated_counts["iter1340"] += 1
            elif sid == "SRC-MCLOUD-FIGMA-DESIGN":
                s["scope"] = "iteration"
                s["iteration_id"] = "ITER-13.4.0"
                s["slot"] = "figma"
                relocated_counts["iter1340"] += 1
            elif sid == "4dcf836c-e455-4d48-8d89-33f319e684fc":
                s["scope"] = "iteration"
                s["iteration_id"] = "ITER-13.4.0"
                s["slot"] = "prototype"
                relocated_counts["iter1340"] += 1
            elif sid == "SRC-MCLOUD-QA-SMOKE":
                s["scope"] = "iteration"
                s["iteration_id"] = "ITER-13.4.0"
                s["slot"] = "testcase"
                relocated_counts["iter1340"] += 1
            else:
                s["scope"] = "iteration"
                s["iteration_id"] = "ITER-13.4.0"
                s["slot"] = "supplementary"
                relocated_counts["iter1340"] += 1

            s["updated_at"] = now

        print(f"Sources relocated: {relocated_counts}")

        db["revision"] = int(db.get("revision", 0)) + 1
        dashboardctl.atomic_write(db)
        print(f"Migration completed! New revision: {db['revision']}")
        return db

    return dashboardctl.with_lock(_migrate)

if __name__ == "__main__":
    run_migration()
