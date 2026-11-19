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

NOW = datetime.now().astimezone().isoformat(timespec="seconds")
sources = [
    {"id":"fixture-req-1", "name":"需求文档 · v1", "type":"link", "url":"https://example.com/requirements-v1", "status":"registered", "active":True, "slot":"requirement_doc", "group_id":"fixture-req", "version_no":1, "version_label":"v1", "inspection_enabled":True, "inspection_mode":"auto", "last_inspection_success_at":"2026-11-10T22:00:00+08:00", "read_state":"success", "verification_state":"verified", "lifecycle_status":"registered", "added_at":"2026-11-01T09:00:00+08:00", "open_path":"/api/sources/fixture-req-1/open"},
    {"id":"fixture-req-2", "name":"需求文档 · v2", "type":"link", "url":"https://example.com/requirements-v2", "status":"registered", "active":True, "slot":"requirement_doc", "group_id":"fixture-req", "version_no":2, "version_label":"v2", "inspection_enabled":True, "inspection_mode":"auto", "last_inspection_success_at":"2026-11-12T22:00:00+08:00", "read_state":"success", "verification_state":"verified", "lifecycle_status":"registered", "added_at":"2026-11-08T09:00:00+08:00", "open_path":"/api/sources/fixture-req-2/open"},
    {"id":"fixture-req-3", "name":"需求文档 · 终版", "type":"link", "url":"https://example.com/requirements-final", "status":"registered", "active":True, "slot":"requirement_doc", "group_id":"fixture-req", "version_no":3, "version_label":"终版", "inspection_enabled":True, "inspection_mode":"auto", "last_inspection_success_at":NOW, "read_state":"success", "verification_state":"verified", "lifecycle_status":"registered", "added_at":NOW, "open_path":"/api/sources/fixture-req-3/open"},
    {"id":"fixture-figma-1", "name":"Figma · 首页视觉", "type":"link", "url":"https://www.figma.com/file/fixture/home", "status":"registered", "active":True, "slot":"figma", "group_id":"fixture-figma", "version_no":1, "version_label":"初稿", "inspection_enabled":True, "inspection_mode":"manual", "last_inspection_success_at":"2026-11-11T22:00:00+08:00", "read_state":"partial", "read_gap":["部分画板未读取"], "verification_state":"not_checked", "lifecycle_status":"registered", "added_at":"2026-11-02T09:00:00+08:00", "open_path":"/api/sources/fixture-figma-1/open"},
    {"id":"fixture-figma-2", "name":"Figma · 首页视觉", "type":"link", "url":"https://www.figma.com/file/fixture/home-v2", "status":"registered", "active":True, "slot":"figma", "group_id":"fixture-figma", "version_no":2, "version_label":"v2", "inspection_enabled":False, "inspection_mode":"manual", "read_state":"not_read", "verification_state":"not_checked", "lifecycle_status":"registered", "added_at":"2026-11-09T09:00:00+08:00", "open_path":"/api/sources/fixture-figma-2/open"},
    {"id":"fixture-codesign-1", "name":"CoDesign · 组件标注", "type":"link", "url":"https://codesign.qq.com/fixture/components", "status":"registered", "active":True, "slot":"codesign", "group_id":"fixture-codesign", "version_no":1, "version_label":"v1", "inspection_enabled":False, "inspection_mode":"manual", "read_state":"not_read", "verification_state":"not_checked", "lifecycle_status":"registered", "added_at":"2026-11-05T09:00:00+08:00", "open_path":"/api/sources/fixture-codesign-1/open"},
    {"id":"fixture-source-ok", "name":"Fixture · 其他来源成功读取", "type":"link", "url":"https://example.com/guide", "status":"registered", "active":True, "group_id":"fixture-source-ok", "version_no":1, "inspection_enabled":True, "inspection_mode":"manual", "last_inspection_success_at":NOW, "read_state":"success", "read_status":"not_read", "verification_state":"verified", "latest_read_attempt_at":NOW, "last_successful_read_at":NOW, "read_gap":[], "lifecycle_status":"registered", "added_at":"2026-10-30T09:00:00+08:00", "open_path":"/api/sources/fixture-source-ok/open"},
    {"id":"fixture-source-failed", "name":"Fixture · 失败且有缺口", "type":"file", "filename":"broken-reference.md", "status":"registered", "active":True, "group_id":"fixture-source-failed", "version_no":1, "inspection_enabled":False, "inspection_mode":"manual", "read_state":"failed", "read_status":"not_read", "verification_state":"not_checked", "latest_read_attempt_at":NOW, "last_successful_read_at":"2026-10-20T14:30:00+08:00", "read_gap":["第 2 页未能读取", "表格内容缺失"], "lifecycle_status":"suspected_deprecated", "suspected_deprecated":True, "added_at":"2026-10-29T09:00:00+08:00"},
    {"id":"fixture-source-unchecked", "name":"Fixture · 尚未读取", "type":"link", "url":"https://example.org/notes", "status":"registered", "active":True, "group_id":"fixture-source-unchecked", "version_no":1, "inspection_enabled":False, "inspection_mode":"manual", "read_state":"not_read", "read_status":"not_checked", "verification_state":"not_checked", "latest_read_attempt_at":None, "last_successful_read_at":None, "read_gap":[], "lifecycle_status":"registered", "added_at":"2026-10-28T09:00:00+08:00", "open_path":"/api/sources/fixture-source-unchecked/open"},
    {"id":"fixture-removed", "name":"Fixture · 已移出的旧资料", "type":"link", "url":"https://example.org/old", "status":"removed", "active":False, "group_id":"fixture-removed", "version_no":1, "inspection_enabled":False, "inspection_mode":"manual", "read_state":"not_read", "verification_state":"not_checked", "lifecycle_status":"registered", "added_at":"2026-10-01T09:00:00+08:00", "removed_at":"2026-10-20T09:00:00+08:00", "open_path":"/api/sources/fixture-removed/open"}
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

# 六阶段流水线 fixture：覆盖旧记录、active、blocked、rework、第 4 阶段子步骤与接口失败。
WORKFLOW_TEMPLATE = {
    "id": "dev-standard", "version": 1, "name": "开发标准流程",
    "stages": [
        {"id": "intake", "label": "接单/立项", "short": "接单", "submit_roles": ["lead"], "advance_roles": ["lead"], "required_outputs": ["scope", "decision_owner"], "next": "requirement"},
        {"id": "requirement", "label": "需求分析与PM结论", "short": "需求", "submit_roles": ["pm"], "advance_roles": ["pm", "lead"], "required_outputs": ["requirement_brief", "acceptance_criteria", "pm_decision"], "next": "development"},
        {"id": "development", "label": "开发", "short": "开发", "submit_roles": ["dev"], "advance_roles": ["dev", "lead"], "required_outputs": ["dev_summary", "branch", "self_check"], "next": "audit_response"},
        {"id": "audit_response", "label": "静态审计与开发回应", "short": "审计", "submit_roles": ["auditor", "dev"], "advance_roles": ["lead"], "required_outputs": ["audit_report", "dev_response"], "next": "acceptance",
         "substeps": [{"id": "audit", "label": "静态审计", "role": "auditor", "output": "audit_report", "outputs": ["audit_report"]},
                      {"id": "response", "label": "开发回应", "role": "dev", "output": "dev_response", "outputs": ["dev_response"]}]},
        {"id": "acceptance", "label": "PM验收", "short": "验收", "submit_roles": ["pm"], "advance_roles": ["pm", "lead"], "required_outputs": ["pm_acceptance"], "next": "delivery"},
        {"id": "delivery", "label": "交付关闭", "short": "交付", "submit_roles": ["lead"], "advance_roles": ["lead"], "required_outputs": ["delivery_evidence"], "next": None}
    ]
}


def workflow(stage, status, gates=None, history=None, rework=0, lock=None):
    return {"template_id": "dev-standard", "template_version": 1, "stage": stage, "stage_status": status, "substage": None,
            "started_at": f"{TODAY}T09:00:00+08:00", "updated_at": f"{TODAY}T12:00:00+08:00", "rework_count": rework,
            "gates": gates or {}, "history": history or [], "lock": lock}


WORKFLOWS = {
    "fixture-workflow-active": workflow("development", "active", {
        "development": {"status": "active", "actor": "gemini", "role": "dev", "at": f"{TODAY}T11:30:00+08:00",
                        "note": "实现完成，等待自检", "evidence": ["local/fixture-workflow"], "outputs": {"dev_summary": "实现完成", "branch": "local/fixture-workflow"}}},
        [{"at": f"{TODAY}T09:00:00+08:00", "action": "start", "stage": "intake", "actor": "lead", "role": "lead", "note": "启动标准流程", "evidence": []},
         {"at": f"{TODAY}T10:00:00+08:00", "action": "advance", "stage": "development", "actor": "lead", "role": "lead", "note": "需求确认，进入开发", "evidence": ["brief.md"]}],
        lock={"stage": "development", "actor": "gemini", "role": "dev", "session_id": "fixture-session",
              "acquired_at": f"{TODAY}T11:30:00+08:00", "heartbeat_at": f"{TODAY}T12:00:00+08:00",
              "expires_at": (datetime.now().astimezone() + timedelta(minutes=25)).isoformat(timespec="seconds")}),
    "fixture-workflow-blocked": workflow("requirement", "blocked", {
        "requirement": {"status": "blocked", "actor": "lead", "role": "lead", "note": "等待 PM 给出验收口径", "evidence": []}}),
    "fixture-workflow-rework": workflow("development", "rework", {
        "development": {"status": "rework", "actor": "pm", "role": "pm", "note": "退回：缺少自检记录", "evidence": ["review.md"], "outputs": {"dev_summary": "初版"}}},
        [{"at": f"{TODAY}T11:00:00+08:00", "action": "rework", "stage": "development", "actor": "xiao-zhi", "role": "pm", "note": "退回开发补充自检", "evidence": ["review.md"]}], 2),
    "fixture-workflow-audit": workflow("audit_response", "active", {
        "audit_response": {"status": "active", "actor": "auditor", "role": "auditor", "at": f"{TODAY}T11:40:00+08:00",
                           "note": "静态审计完成，待开发回应", "evidence": ["audit.md"], "outputs": {"audit_report": "ADVISORY 报告"}}}),
    "fixture-workflow-unavailable": workflow("acceptance", "active")
}

records += [
    item("fixture-workflow-active", "work", "Fixture workflow · 开发中", None, status="in_progress",
         workflow_template_id="dev-standard", workflow_stage="development", workflow_stage_status="active", workflow_stage_index=3,
         workflow_stage_label="开发", workflow_stage_short="开发", workflow_rework_count=0),
    item("fixture-workflow-blocked", "work", "Fixture workflow · 阻塞", None, status="blocked",
         workflow_stage="requirement", workflow_stage_status="blocked", workflow_stage_index=2, workflow_stage_label="需求分析与PM结论", workflow_stage_short="需求"),
    item("fixture-workflow-rework", "work", "Fixture workflow · 退回", None, status="in_progress",
         workflow_stage="development", workflow_stage_status="rework", workflow_stage_index=3, workflow_stage_label="开发", workflow_stage_short="开发", workflow_rework_count=2),
    item("fixture-workflow-audit", "work", "Fixture workflow · 审计与回应", None, status="in_progress",
         workflow_stage="audit_response", workflow_stage_status="active", workflow_stage_index=4, workflow_stage_label="静态审计与开发回应", workflow_stage_short="审计", iteration_id="iter-fixture-1"),
    item("fixture-workflow-legacy", "work", "Fixture workflow · 旧记录未启用", None, status="in_progress"),
    item("fixture-workflow-unavailable", "work", "Fixture workflow · 详情接口失败", None, status="in_progress",
         workflow_stage="acceptance", workflow_stage_status="active", workflow_stage_index=5, workflow_stage_label="PM验收", workflow_stage_short="验收"),
    item("fixture-work-completed-in-active-iter", "work", "已完成但未发布迭代的需求 (零折叠常驻展示)", datetime.now().astimezone().isoformat(), status="completed",
         iteration_id="iter-fixture-1", reopen_count=1,
         reopen_history=[{"round": 1, "reopened_at": f"{TODAY}T10:00:00+08:00", "reason": "PM微调需求样式", "audit_mode": "lightweight"}])
]

FIXTURE_ITERATIONS = [
    {"id": "iter-fixture-1", "version_tag": "v13.4.2", "title": "13.4.2 智能相册与AI音乐优化", "status": "active",
     "target_release_date": "2026-11-30", "description": "包含 Wan3.0 适配、本地音乐列表滚动优化及埋点修复"},
    {"id": "iter-fixture-old", "version_tag": "v13.4.1", "title": "13.4.1 历史已发布迭代", "status": "released",
     "target_release_date": "2026-10-31", "description": "历史迭代"}
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
                       "current_batch_id": "batch-current", "batches": [{"id":"batch-current","state":"current","task_ids":["fixture-current-done"]}, {"id":"batch-old","state":"past","task_ids":["fixture-old-task"]}],
                       "iterations": FIXTURE_ITERATIONS, "current_iteration_id": "iter-fixture-1", "inspection": {}}
            return self.respond(payload)
        if path.startswith("/api/items/") and path.endswith("/context"):
            rid = path.split("/")[3]
            rec = next((r for r in records if r["id"] == rid), None)
            if rec: return self.respond({"revision": rec["revision"], "record": rec, "context_markdown": make_context(rec["title"])})
            return self.respond({"error":"not_found"}, 404)
        if path.startswith("/api/items/") and path.endswith("/workflow"):
            rid = path.split("/")[3]
            rec = next((r for r in records if r["id"] == rid), None)
            if not rec: return self.respond({"error": "record_not_found"}, 404)
            if rid == "fixture-workflow-unavailable": return self.respond({"error": "workflow_unavailable"}, 503)
            return self.respond({"record_revision": rec["revision"], "store_revision": 1, "workflow": WORKFLOWS.get(rid), "template": WORKFLOW_TEMPLATE})
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
