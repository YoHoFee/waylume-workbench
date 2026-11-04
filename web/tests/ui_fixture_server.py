#!/usr/bin/env python3
"""Isolated visual fixtures for the dashboard. Binds only localhost:8766."""
# Created by Codex on 2026/10/22.
# Copyright © 2026 CMIC. All rights reserved.
from __future__ import annotations
import json
import tempfile
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

WEB = Path(__file__).resolve().parents[1]
TODAY = datetime.now().astimezone().date().isoformat()
YESTERDAY = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()

def item(record_id, kind, title, completed_at=None, status="completed", **extra):
    return {"id": record_id, "type": kind, "title": title, "status": status, "source_status": status,
            "effective_status": status, "completed_at": completed_at, "revision": 1, "updated_at": TODAY,
            "latest_brief": "Fixture short brief: completion and grouping visual check.", **extra}

sources = [
    {"id":"fixture-source-ok", "name":"Fixture · 成功读取", "type":"link", "url":"https://example.com/guide", "status":"registered", "active":True, "read_state":"success", "read_status":"not_read", "verification_state":"verified", "latest_read_attempt_at":datetime.now().astimezone().isoformat(), "last_successful_read_at":datetime.now().astimezone().isoformat(), "read_gap":[], "lifecycle_status":"registered", "open_path":"/api/sources/fixture-source-ok/open"},
    {"id":"fixture-source-failed", "name":"Fixture · 失败且有缺口", "type":"file", "filename":"broken-reference.md", "status":"registered", "active":True, "read_state":"failed", "read_status":"not_read", "verification_state":"not_checked", "latest_read_attempt_at":datetime.now().astimezone().isoformat(), "last_successful_read_at":"2026-10-20T14:30:00+08:00", "read_gap":["第 2 页未能读取", "表格内容缺失"], "lifecycle_status":"suspected_deprecated", "suspected_deprecated":True},
    {"id":"fixture-source-unchecked", "name":"Fixture · 尚未读取", "type":"link", "url":"https://example.org/notes", "status":"registered", "active":True, "read_state":"not_read", "read_status":"not_checked", "verification_state":"not_checked", "latest_read_attempt_at":None, "last_successful_read_at":None, "read_gap":[], "lifecycle_status":"registered"}
]

records = [
    item("fixture-work-today", "work", "Fixture work completed today", datetime.now().astimezone().isoformat()),
    item("fixture-work-yesterday", "work", "Fixture work completed yesterday", f"{YESTERDAY}T12:01:00+08:00"),
    item("fixture-work-unknown", "work", "Fixture work completion date unknown", None),
    item("fixture-bug-today", "bug", "Fixture bug completed today", datetime.now().astimezone().isoformat()),
    item("fixture-bug-yesterday", "bug", "Fixture bug completed yesterday", f"{YESTERDAY}T12:01:00+08:00"),
    item("fixture-bug-unknown", "bug", "Fixture bug completion date unknown", None),
    item("fixture-bug-attachments-none", "bug", "Fixture BUG · empty attachment list confirmed", None, status="pending", effective_status="on_hold", bug_detail={"read_state":"success", "description":"附件枚举成功且没有附件。", "attachments_read_state":"success", "attachments_gaps":[]}, source_attachments=[]),
    item("fixture-bug-attachments-failed", "bug", "Fixture BUG · attachment enumeration failed", None, status="pending", effective_status="on_hold", bug_detail={"read_state":"success", "description":"正文读取成功，但附件枚举失败。", "attachments_read_state":"failed", "attachments_gaps":["附件服务暂不可用"]}, source_attachments=[]),
    item("fixture-bug-attachments-partial", "bug", "Fixture BUG · attachment list partially read", None, status="pending", effective_status="on_hold", bug_detail={"read_state":"success", "description":"附件列表部分读取。", "attachments_read_state":"partial", "attachments_gaps":["附件 2 下载失败"]}, source_attachments=[]),
    item("fixture-held", "work", "Fixture ownerless held item", None, status="pending", effective_status="on_hold", effective_status_basis="activity", current_agent_session_id=None),
    item("fixture-current-done", "task", "Fixture completed task in current batch", datetime.now().astimezone().isoformat(), batch_id="batch-current", latest_brief="Completed task remains in the current batch."),
    item("fixture-old-task", "task", "Fixture task in old batch", datetime.now().astimezone().isoformat(), batch_id="batch-old", latest_brief="Historical task remains folded under its batch.")
]

def make_context(title):
    body = [f"# {title}", "", "This context is deliberately long for scrolling and lazy-fill checks.", "", "<img src=x onerror=alert(1)>", "[unsafe javascript](javascript:alert(1))", "", "## Details", "", "- first detail", "- second detail", "", "```html", "<script>alert('fixture')</script>", "```", ""]
    body.extend(f"Paragraph {i}: fixture content preserves scrolling when fetched asynchronously." for i in range(1, 81))
    return "\n".join(body)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(WEB), **kwargs)
    def log_message(self, *_): pass
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            payload = {"requirements": [r for r in records if r["type"] == "work"], "bugs": [r for r in records if r["type"] == "bug"],
                       "delegated": [r for r in records if r["type"] == "task"], "sources": sources, "revision": 1, "generated_at": datetime.now().astimezone().isoformat(),
                       "current_batch_id": "batch-current", "batches": [{"id":"batch-current","state":"current","task_ids":["fixture-current-done"]}, {"id":"batch-old","state":"past","task_ids":["fixture-old-task"]}], "inspection": {}}
            return self.respond(payload)
        if path.startswith("/api/items/") and path.endswith("/context"):
            rid = path.split("/")[3]
            rec = next((r for r in records if r["id"] == rid), None)
            if rec: return self.respond({"revision": rec["revision"], "record": rec, "context_markdown": make_context(rec["title"])})
            return self.respond({"error":"not_found"}, 404)
        if path == "/api/sources" or path == "/api/sources?include_removed=1": return self.respond({"sources": sources})
        if path == "/api/inspection-schedule": return self.respond({"available":True,"enabled":True,"state":"active","timezone":"Asia/Shanghai","times":["23:59"],"server_time":datetime.now().astimezone().isoformat()})
        if path == "/api/bug-inspection/status": return self.respond({"available":True,"enabled":True,"status":"idle","next_run_at":None})
        return super().do_GET()
    def respond(self, payload, code=200):
        body=json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="mcloud-dashboard-fixture-") as temp_root:
        Path(temp_root, "README.txt").write_text("Temporary isolated fixture root; no production store is used.\n", encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
        print(f"Fixture dashboard: http://127.0.0.1:8766/ (isolated temp root {temp_root}; Ctrl-C to stop)", flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()
