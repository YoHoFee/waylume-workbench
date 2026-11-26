#!/usr/bin/env python3
"""Isolated visual fixtures for the dashboard. Binds only localhost:8766."""
# Created by Codex on 2026/10/22.
# Copyright © 2026 CMIC. All rights reserved.
from __future__ import annotations
import json
import os
import tempfile
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB = Path(__file__).resolve().parents[1]
try:
    VERSION = (WEB.parent / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    VERSION = "0.0.0"
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
    {"id":"fixture-req-3", "name":"需求文档 · 终版", "type":"link", "url":"https://example.com/requirements-final", "status":"registered", "active":True, "slot":"requirement_doc", "group_id":"fixture-req", "version_no":3, "version_label":"终版", "inspection_enabled":True, "inspection_mode":"auto", "last_inspection_success_at":NOW, "read_state":"success", "verification_state":"verified", "lifecycle_status":"registered", "added_at":NOW, "note":"最终版以此为准，评审记录见补充材料。", "note_masked":False, "open_path":"/api/sources/fixture-req-3/open"},
    {"id":"fixture-figma-1", "name":"Figma · 首页视觉", "type":"link", "url":"https://www.figma.com/file/fixture/home", "status":"registered", "active":True, "slot":"figma", "group_id":"fixture-figma", "version_no":1, "version_label":"初稿", "inspection_enabled":True, "inspection_mode":"manual", "last_inspection_success_at":"2026-11-11T22:00:00+08:00", "read_state":"partial", "read_gap":["部分画板未读取"], "verification_state":"not_checked", "lifecycle_status":"registered", "added_at":"2026-11-02T09:00:00+08:00", "open_path":"/api/sources/fixture-figma-1/open"},
    {"id":"fixture-figma-2", "name":"Figma · 首页视觉", "type":"link", "url":"https://www.figma.com/file/fixture/home-v2", "status":"registered", "active":True, "slot":"figma", "group_id":"fixture-figma", "version_no":2, "version_label":"v2", "inspection_enabled":False, "inspection_mode":"manual", "read_state":"not_read", "verification_state":"not_checked", "lifecycle_status":"registered", "added_at":"2026-11-09T09:00:00+08:00", "open_path":"/api/sources/fixture-figma-2/open"},
    {"id":"fixture-codesign-1", "name":"CoDesign · 组件标注", "type":"link", "url":"https://codesign.qq.com/fixture/components", "status":"registered", "active":True, "slot":"codesign", "group_id":"fixture-codesign", "version_no":1, "version_label":"v1", "inspection_enabled":False, "inspection_mode":"manual", "read_state":"not_read", "verification_state":"not_checked", "lifecycle_status":"registered", "added_at":"2026-11-05T09:00:00+08:00", "note":"访问密码：FIXTURE", "note_masked":True, "open_path":"/api/sources/fixture-codesign-1/open"},
    {"id":"fixture-source-ok", "name":"Fixture · 其他来源成功读取", "type":"link", "url":"https://example.com/guide", "status":"registered", "active":True, "group_id":"fixture-source-ok", "version_no":1, "inspection_enabled":True, "inspection_mode":"manual", "last_inspection_success_at":NOW, "read_state":"success", "read_status":"not_read", "verification_state":"verified", "latest_read_attempt_at":NOW, "last_successful_read_at":NOW, "read_gap":[], "lifecycle_status":"registered", "added_at":"2026-10-30T09:00:00+08:00", "open_path":"/api/sources/fixture-source-ok/open"},
    {"id":"fixture-source-failed", "name":"Fixture · 失败且有缺口", "type":"file", "filename":"broken-reference.md", "status":"registered", "active":True, "group_id":"fixture-source-failed", "version_no":1, "inspection_enabled":False, "inspection_mode":"manual", "read_state":"failed", "read_status":"not_read", "verification_state":"not_checked", "latest_read_attempt_at":NOW, "last_successful_read_at":"2026-10-20T14:30:00+08:00", "read_gap":["第 2 页未能读取", "表格内容缺失"], "lifecycle_status":"suspected_deprecated", "suspected_deprecated":True, "added_at":"2026-10-29T09:00:00+08:00"},
    {"id":"fixture-source-unchecked", "name":"Fixture · 尚未读取", "type":"link", "url":"https://example.org/notes", "status":"registered", "active":True, "group_id":"fixture-source-unchecked", "version_no":1, "inspection_enabled":False, "inspection_mode":"manual", "read_state":"not_read", "read_status":"not_checked", "verification_state":"not_checked", "latest_read_attempt_at":None, "last_successful_read_at":None, "read_gap":[], "lifecycle_status":"registered", "added_at":"2026-10-28T09:00:00+08:00", "open_path":"/api/sources/fixture-source-unchecked/open"},
    {"id":"fixture-removed", "name":"Fixture · 已移出的旧资料", "type":"link", "url":"https://example.org/old", "status":"removed", "active":False, "group_id":"fixture-removed", "version_no":1, "inspection_enabled":False, "inspection_mode":"manual", "read_state":"not_read", "verification_state":"not_checked", "lifecycle_status":"registered", "added_at":"2026-10-01T09:00:00+08:00", "removed_at":"2026-10-20T09:00:00+08:00", "open_path":"/api/sources/fixture-removed/open"}
]

source_revision = 1

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

# Version matrix fixture: keep version material isolated from public material.
sources.extend([
    {"id": "fixture-global-api", "name": "公共接口文档", "type": "link", "url": "https://example.com/api", "status": "registered", "active": True, "scope": "global", "slot": "api_doc", "group_id": "fixture-global-api", "version_no": 1, "inspection_enabled": False, "inspection_mode": "manual", "read_state": "not_read", "lifecycle_status": "registered", "added_at": "2026-10-20T09:00:00+08:00"},
    {"id": "fixture-global-supp", "name": "公共联调登记", "type": "link", "url": "https://example.com/api-log", "status": "registered", "active": True, "scope": "global", "slot": "supplementary", "group_id": "fixture-global-supp", "version_no": 1, "inspection_enabled": False, "inspection_mode": "manual", "read_state": "not_read", "lifecycle_status": "registered", "added_at": "2026-10-21T09:00:00+08:00"},
    {"id": "fixture-1342-supp", "name": "PM 沟通截图", "type": "file", "filename": "pm-chat.png", "status": "registered", "active": True, "scope": "iteration", "iteration_id": "iter-fixture-1", "slot": "supplementary", "group_id": "fixture-1342-supp", "version_no": 1, "inspection_enabled": False, "inspection_mode": "manual", "read_state": "not_read", "lifecycle_status": "registered", "added_at": "2026-11-12T09:00:00+08:00", "attachments": [{"filename": "note.txt", "summary": "PM 补充确认", "download_url": "/fixture-note.txt"}]},
    {"id": "fixture-analytics-req", "name": "13.4.2 埋点需求表", "type": "link", "url": "https://example.com/analytics-requirements", "status": "registered", "active": True, "scope": "iteration", "iteration_id": "iter-fixture-analytics", "slot": "analytics_requirement", "group_id": "fixture-analytics-req", "version_no": 2, "version_label": "v2", "inspection_enabled": True, "inspection_mode": "auto", "read_state": "success", "lifecycle_status": "registered", "added_at": "2026-11-14T09:00:00+08:00", "open_path": "/api/sources/fixture-analytics-req/open"},
    {"id": "fixture-analytics-acc", "name": "13.4.2 埋点验收表", "type": "link", "url": "https://example.com/analytics-acceptance", "status": "registered", "active": True, "scope": "iteration", "iteration_id": "iter-fixture-analytics", "slot": "analytics_acceptance", "group_id": "fixture-analytics-acc", "version_no": 1, "inspection_enabled": False, "inspection_mode": "manual", "read_state": "not_read", "lifecycle_status": "registered", "added_at": "2026-11-15T09:00:00+08:00", "open_path": "/api/sources/fixture-analytics-acc/open"},
    {"id": "fixture-analytics-note", "name": "埋点口径补充说明", "type": "link", "url": "https://example.com/analytics-note", "status": "registered", "active": True, "scope": "iteration", "iteration_id": "iter-fixture-analytics", "slot": "supplementary", "group_id": "fixture-analytics-note", "version_no": 1, "inspection_enabled": False, "inspection_mode": "manual", "read_state": "not_read", "lifecycle_status": "registered", "added_at": "2026-11-16T09:00:00+08:00", "open_path": "/api/sources/fixture-analytics-note/open"},
    {"id": "fixture-old-figma", "name": "13.4.1 历史 Figma", "type": "link", "url": "https://figma.com/file/old", "status": "registered", "active": True, "scope": "iteration", "iteration_id": "iter-fixture-old", "slot": "figma", "group_id": "fixture-old-figma", "version_no": 1, "inspection_enabled": False, "inspection_mode": "manual", "read_state": "not_read", "lifecycle_status": "registered", "added_at": "2026-10-10T09:00:00+08:00"},
    {"id": "fixture-old-supp", "name": "13.4.1 验收记录", "type": "link", "url": "https://example.com/old-qa", "status": "registered", "active": True, "scope": "iteration", "iteration_id": "iter-fixture-old", "slot": "supplementary", "group_id": "fixture-old-supp", "version_no": 1, "inspection_enabled": False, "inspection_mode": "manual", "read_state": "not_read", "lifecycle_status": "registered", "added_at": "2026-10-11T09:00:00+08:00"},
])

for _source in sources:
    if _source.get("scope"):
        continue
    if _source.get("slot") == "api_doc":
        _source["scope"] = "global"
        _source["iteration_id"] = None
    else:
        _source["scope"] = "iteration"
        _source["iteration_id"] = "iter-fixture-old" if _source["id"] == "fixture-removed" else "iter-fixture-1"

for _record in records:
    if _record["type"] == "task":
        _record.pop("iteration_id", None)
    elif _record["id"] == "fixture-work-yesterday":
        _record["iteration_id"] = "iter-fixture-old"
    elif _record["id"] in {"fixture-work-today", "fixture-work-unknown", "fixture-held"}:
        _record["iteration_id"] = "iter-fixture-1"

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
                           "note": "静态审计完成，待开发回应", "evidence": ["audit.md", "https://example.com/audit", "review.md"],
                           "outputs": {
                               "audit_report": "ADVISORY 报告 · 13.4.2 埋点验收\n\n| 检查项 | 结论 | 风险 | 证据与位置 | 处理建议 | 负责人 |\n|:--|:--:|:--:|:--|:--|--:|\n| 播放器返回键在折叠态下与列表滚动竞争 | 已复现 | 中 | `PlayerView.swift`<br>列表滚动锁缺失<br>返回栈可被连续触发 | 折叠态加滚动锁，返回只消费一次 | 开发 |\n| 空列表弱网加载态 | 待补 | 低 | 首屏 skeleton 缺失<br>弱网下白屏约 2.4s | 补空态与骨架屏 | 开发 |\n| 数字 ID 展示 | 已统一 | 无 | 卡片、详情、全文均使用 #编号<br>长编号不换行溢出 | 保持现状 | PM |\n| 埋点曝光口径 | 待确认 | 中 | 视频曝光以 50% 可视面积计<br>重复曝光按 session 去重 | PM 与数据确认后冻结 | PM |\n\n以上均为建议，不阻塞交付。",
                               "dev_response": "开发回应：\n\n| 审计条目 | 状态 | 说明 | 验证方式 |\n|:--|:--:|:--|:--|\n| 返回键与滚动竞争 | 已修复 | 折叠态加滚动锁<br>返回只消费一次 | 真机回归 3 轮 |\n| 空列表与弱网加载态 | 已补齐 | 空态文案 + 骨架屏 | 弱网模拟 |\n| 数字 ID | 知悉 | 保持 #编号 | 视觉走查 |\n| 埋点曝光口径 | 待 PM 确认 | 未改口径 | 等待结论 |"}}}),
    # 阶段输出以本地路径记录的用例：审计报告路径存在，开发回应路径已不存在。
    "fixture-workflow-report-path": workflow("audit_response", "active", {
        "audit_response": {"status": "active", "actor": "auditor", "role": "auditor", "at": f"{TODAY}T11:50:00+08:00",
                           "note": "审计报告已写入本地文件", "evidence": [],
                           "outputs": {
                               "audit_report": "reports/fixture-audit.md",
                               "dev_response": "reports/fixture-missing-response.md"}}}),
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
    item("fixture-workflow-report-path", "work", "Fixture workflow · 报告输出为路径", None, status="in_progress",
         workflow_stage="audit_response", workflow_stage_status="active", workflow_stage_index=4, workflow_stage_label="静态审计与开发回应", workflow_stage_short="审计", iteration_id="iter-fixture-1"),
    item("fixture-workflow-legacy", "work", "Fixture workflow · 旧记录未启用", None, status="in_progress"),
    item("fixture-workflow-unavailable", "work", "Fixture workflow · 详情接口失败", None, status="in_progress",
         workflow_stage="acceptance", workflow_stage_status="active", workflow_stage_index=5, workflow_stage_label="PM验收", workflow_stage_short="验收"),
    item("fixture-inspection-new", "work", "Fixture 巡检新建需求 · 等待指定版本", None, status="pending"),
    item("fixture-work-completed-in-active-iter", "work", "已完成但未发布迭代的需求 (零折叠常驻展示)", datetime.now().astimezone().isoformat(), status="completed",
         iteration_id="iter-fixture-1", reopen_count=1,
         reopen_history=[{"round": 1, "reopened_at": f"{TODAY}T10:00:00+08:00", "reason": "PM微调需求样式", "audit_mode": "lightweight"}]),
    item("1001", "work", "数字编号需求 · 埋点视频曝光验收", None, status="in_progress",
         iteration_id="iter-fixture-analytics", status_line="埋点验收表已补齐，等待 PM 确认曝光口径",
         latest_brief="埋点验收表已补齐，等待 PM 确认曝光口径"),
    item("1002", "task", "数字编号任务 · 埋点回归", None, status="in_progress",
         batch_id="batch-current", status_line="回归中，等待真机日志",
         latest_brief="回归中，等待真机日志")
]

FIXTURE_ITERATIONS = [
    {"id": "iter-fixture-old", "version_tag": "13.4.1", "title": "13.4.1 历史已发布迭代", "status": "released",
     "version_type": "iteration", "order": 0, "created_at": "2026-10-01T09:00:00+08:00",
     "target_release_date": "2026-10-31", "description": "历史迭代"},
    {"id": "iter-fixture-1", "version_tag": "13.4.2 迭代二", "title": "13.4.2 迭代二", "status": "active",
     "version_type": "iteration", "order": 1, "created_at": "2026-11-01T09:00:00+08:00",
     "target_release_date": "2026-11-30", "description": "包含 Wan3.0 适配、本地音乐列表滚动优化及埋点修复"},
    {"id": "iter-fixture-analytics", "version_tag": "13.4.2 埋点", "title": "13.4.2 埋点", "status": "planning",
     "version_type": "analytics", "order": 2, "created_at": "2026-11-14T09:00:00+08:00",
     "target_release_date": None, "description": "发布后的埋点补充与验收阶段"}
]


def _inspection_ref(record_id):
    record = next((row for row in records if row["id"] == record_id), None)
    return {"id": record_id, "title": record["title"] if record else record_id,
            "type": record["type"] if record else "work"}

def _inspection_runs():
    return [
        {"at": f"{TODAY}T18:30:00+08:00", "status": "succeeded",
         "new_bugs": [_inspection_ref("fixture-bug-attachments-none")],
         "new_requirements": [_inspection_ref("fixture-inspection-new")],
         "notes": "本轮读取需求文档终版与 Figma v2；新建需求未指定版本，等待归入迭代。",
         "run_id": "fixture-run-2"},
        {"at": f"{TODAY}T12:30:00+08:00", "status": "partial",
         "new_bugs": [], "new_requirements": [],
         "notes": "Figma 部分画板未读取，其余资料正常。",
         "run_id": "fixture-run-1"}
    ]

def make_context(title):
    body = [f"# {title}", "", "This context is deliberately long for scrolling and lazy-fill checks.", "", "<img src=x onerror=alert(1)>", "[unsafe javascript](javascript:alert(1))", "", "## Details", "", "- first detail", "- second detail", "", "```html", "<script>alert('fixture')</script>", "```", ""]
    body.extend(f"Paragraph {i}: fixture content preserves scrolling when fetched asynchronously." for i in range(1, 81))
    return "\n".join(body)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(WEB), **kwargs)
    def log_message(self, *_): pass
    def do_POST(self):
        global source_revision
        import re
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        match = re.fullmatch(r"/api/iterations/([A-Za-z0-9._-]{1,120})/update", path)
        if match:
            iteration = next((item for item in FIXTURE_ITERATIONS if item["id"] == match.group(1)), None)
            if not iteration:
                return self.respond({"error": "iteration_not_found"}, 404)
            if "status" in body:
                status = body.get("status")
                if status not in {"planning", "active", "pending_release", "released", "archived"}:
                    return self.respond({"error": "invalid_iteration_status"}, 400)
                iteration["status"] = status
                iteration["released_at"] = NOW if status == "released" else None
            for key in ("title", "version_tag", "target_release_date", "description"):
                if key in body:
                    iteration[key] = body[key]
            iteration["updated_at"] = NOW
            return self.respond({"revision": 2, "iteration": iteration})
        match = re.fullmatch(r"/api/iterations/([A-Za-z0-9._-]{1,120})/type", path)
        if match:
            iteration = next((item for item in FIXTURE_ITERATIONS if item["id"] == match.group(1)), None)
            target = body.get("version_type")
            if not iteration or target not in {"iteration", "analytics"}:
                return self.respond({"error": "iteration_not_found"}, 404)
            changed = iteration.get("version_type") != target
            iteration["version_type"] = target
            if changed:
                mapping = {"requirement_doc": "analytics_requirement", "testcase": "analytics_acceptance",
                           "analytics_requirement": "requirement_doc", "analytics_acceptance": "testcase"}
                allowed = {"analytics": {"analytics_requirement", "analytics_acceptance", "supplementary"},
                           "iteration": {"requirement_doc", "figma", "prototype", "codesign", "testcase", "supplementary"}}
                for source in sources:
                    if source.get("scope") == "iteration" and source.get("iteration_id") == iteration["id"]:
                        source["slot"] = mapping.get(source.get("slot"), source.get("slot"))
                        if source["slot"] not in allowed[target]:
                            source["slot"] = "supplementary"
            return self.respond({"changed": changed, "iteration": iteration, "revision": 2,
                                 "backup_path": "/tmp/fixture-backup" if changed else None,
                                 "verification": {"ok": True, "source_count_unchanged": True,
                                                  "group_ids_unchanged": True, "slots_valid": True}})
        match = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]{1,100})/edit", path)
        if match:
            source = next((item for item in sources if item["id"] == match.group(1)), None)
            if not source:
                return self.respond({"error": "source_not_found"}, 404)
            name = body.get("name")
            url = body.get("url")
            if name is not None:
                if not isinstance(name, str) or not name.strip():
                    return self.respond({"error": "invalid_source_name"}, 400)
                source["name"] = " ".join(name.split())[:200]
            if url is not None:
                if source.get("type") != "link":
                    return self.respond({"error": "invalid_source_type"}, 400)
                if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
                    return self.respond({"error": "invalid_source_url"}, 400)
                if any(item["id"] != source["id"] and item.get("active") and item.get("url") == url for item in sources):
                    return self.respond({"error": "source_duplicate"}, 409)
                source["url"] = url
                source["open_path"] = f"/api/sources/{source['id']}/open"
            note = body.get("note")
            note_masked = body.get("note_masked")
            if note is not None:
                if not isinstance(note, str) or len(note) > 2000:
                    return self.respond({"error": "invalid_source_note"}, 400)
                source["note"] = note.strip()
            if note_masked is not None:
                if not isinstance(note_masked, bool):
                    return self.respond({"error": "invalid_source_note_masked"}, 400)
                source["note_masked"] = note_masked
            source_revision += 1
            return self.respond({"revision": source_revision, "source": source})
        match = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]{1,100})/location", path)
        if match:
            source = next((item for item in sources if item["id"] == match.group(1)), None)
            if not source:
                return self.respond({"error": "source_not_found"}, 404)
            group_id = source.get("group_id") or source["id"]
            members = [item for item in sources if (item.get("group_id") or item["id"]) == group_id]
            for member in members:
                member["scope"] = body.get("scope")
                member["iteration_id"] = body.get("iteration_id") if body.get("scope") == "iteration" else None
                member["slot"] = body.get("slot")
            return self.respond({"revision": 2, "group_size": len(members), "source": source})
        return self.respond({"error": "unsupported"}, 405)
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            payload = {"requirements": [r for r in records if r["type"] == "work"], "bugs": [r for r in records if r["type"] == "bug"],
                       "delegated": [r for r in records if r["type"] == "task"], "sources": sources, "revision": 1, "version": VERSION,
                       "generated_at": datetime.now().astimezone().isoformat(),
                       "current_batch_id": "batch-current", "batches": [{"id":"batch-current","state":"current","task_ids":["fixture-current-done"]}, {"id":"batch-old","state":"past","task_ids":["fixture-old-task"]}],
                       "iterations": FIXTURE_ITERATIONS, "current_iteration_id": "iter-fixture-1", "inspection": {}}
            return self.respond(payload)
        if path == "/api/version":
            return self.respond({"version": VERSION})
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
        if path == "/api/scopes":
            return self.respond({"version": 1, "revision": 3, "scopes": [
                {"id": "scope-001", "name": "播放器列表滚动与返回键", "status": "confirmed", "version": 3,
                 "in_scope": ["列表滚动性能", "返回键与折叠态交互", "弱网加载态"], "out_of_scope": ["推荐算法排序", "服务端接口协议"],
                 "related_ids": ["1001", "fixture-workflow-audit"],
                 "evidence": [{"date": TODAY, "summary": "PM 在需求评审中确认由本组负责", "reference": "https://example.com/scope-review | review.md"}]},
                {"id": "scope-002", "name": "埋点口径与验收", "status": "candidate", "version": 1,
                 "in_scope": ["曝光与点击埋点验收"], "out_of_scope": ["埋点 SDK 实现"], "related_ids": ["1001"],
                 "evidence": [{"date": TODAY, "summary": "待 PM 最终确认", "reference": ""}]}
            ]})
        if path == "/api/sources/preview":
            url = (parse_qs(urlparse(self.path).query).get("url") or [""])[0]
            if not url.lower().startswith(("http://", "https://")):
                return self.respond({"error": "invalid_source_url"}, 400)
            if "preview-fail" in url:
                return self.respond({"error": "preview_failed"}, 502)
            slug = urlparse(url).path.strip("/").split("/")[-1] or urlparse(url).hostname or "资料"
            return self.respond({"url": url, "final_url": url, "title": f"自动取名 · {slug}"})
        if path.startswith("/api/items/") and path.endswith("/evidence-file"):
            rid = path.split("/")[3]
            evidence = (parse_qs(urlparse(self.path).query).get("path") or [""])[0]
            texts = {
                "audit.md": "# 审计报告\n\n- 播放器返回键与列表滚动存在竞争。\n- 建议补充空列表与弱网加载态。\n- 结论：ADVISORY，不阻塞交付。\n",
                "review.md": "# 复核记录\n\n退回原因已修复，自检通过，等待重新审计。\n",
                "reports/fixture-audit.md": "# 路径型审计报告\n\n该报告由 workflow 以本地路径记录，而不是直接写入输出正文。\n\n| 检查项 | 结论 | 风险 | 证据 | 建议 | 责任人 |\n|:--|:--:|:--:|:--|:--|--:|\n| 返回键与列表滚动 | 通过 | 低 | 折叠态滚动锁<br>真机 3 轮 | 保持 | 开发 |\n| 空列表与弱网加载态 | 通过 | 低 | 骨架屏 + 空态 | 保持 | 开发 |\n| 宽表横向滚动 | 验证中 | 无 | 本行用于验证全文查看器的横向光束轨道 | 观察 | 设计 |\n\n- 结论：ADVISORY，不阻塞交付。\n\n" + "\n".join(f"- 补充检查 {i}：fixture 长报告用于验证全文查看器滚动与自绘轨道。" for i in range(1, 31)) + "\n"
            }
            if rid not in {r["id"] for r in records}:
                return self.respond({"error": "record_not_found"}, 404)
            if evidence not in texts:
                return self.respond({"error": "evidence_not_found"}, 404)
            return self.respond_text(texts[evidence])
        if path == "/api/sources" or path == "/api/sources?include_removed=1": return self.respond({"revision": source_revision, "sources": sources})
        if path == "/api/inspection-schedule": return self.respond({"available":True,"enabled":True,"state":"active","timezone":"Asia/Shanghai","times":["23:59"],"server_time":datetime.now().astimezone().isoformat()})
        if path == "/api/bug-inspection/status": return self.respond({"available":True,"enabled":True,"status":"succeeded","slot_id":"fixture-slot","next_run_at":f"{TODAY}T23:59:00+08:00"})
        if path.startswith("/api/inspections"):
            runs = _inspection_runs()
            return self.respond({"date": TODAY, "runs": runs,
                                 "new_bug_count": sum(len(run["new_bugs"]) for run in runs),
                                 "new_requirement_count": sum(len(run["new_requirements"]) for run in runs)})
        return super().do_GET()
    def respond(self, payload, code=200):
        body=json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def respond_text(self, text, code=200):
        body=text.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type","text/plain; charset=utf-8")
        self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Content-Length",str(len(body)))
        self.end_headers(); self.wfile.write(body)

if __name__ == "__main__":
    port = int(os.environ.get("WAYLUME_FIXTURE_PORT", "8766"))
    with tempfile.TemporaryDirectory(prefix="mcloud-dashboard-fixture-") as temp_root:
        Path(temp_root, "README.txt").write_text("Temporary isolated fixture root; no production store is used.\n", encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        print(f"Fixture dashboard: http://127.0.0.1:{port}/ (isolated temp root {temp_root}; Ctrl-C to stop)", flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()
