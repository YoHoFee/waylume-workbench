#!/usr/bin/env python3
"""Local CLI for the dashboard's structured, reviewable data store."""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import fcntl
import io
import json
import os
import re
import sys
import tempfile
import threading
import uuid
import zipfile
import hashlib
import mimetypes
import backend
import waylume_paths
import workflow
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


CODE_ROOT = Path(__file__).resolve().parent
ROOT = waylume_paths.data_dir()
STORE = ROOT / "store.json"
LOCK = ROOT / ".store.lock"
EVENTS = ROOT / "events.jsonl"
_LOCK_LOCAL = threading.local()
UPLOADS = ROOT / "sources" / "uploads"


def refresh_data_paths() -> None:
    """Re-resolve the configured user data root after a settings migration."""
    global ROOT, STORE, LOCK, EVENTS, UPLOADS
    ROOT = waylume_paths.data_dir()
    STORE = ROOT / "store.json"
    LOCK = ROOT / ".store.lock"
    EVENTS = ROOT / "events.jsonl"
    UPLOADS = ROOT / "sources" / "uploads"
VALID_TYPES = {"work", "bug", "task"}
VALID_STATUSES = {
    "in_progress", "developing", "testing", "on_hold", "awaiting_acceptance", "blocked", "completed", "partial",
    "pending", "unknown", "archived", "cancelled", "platform_testing", "submitted_for_testing", "pending_release",
    "owner_changed",
}
ALLOWED_FIELDS = {
    "id", "type", "title", "status", "status_detail", "notes", "evidence",
    "source", "source_updated_at", "observed_at", "evidence_at", "updated_at",
    "owner", "related_ids", "blocked_reason", "history", "severity", "assignee",
    "verification_state", "platform_status", "is_shared", "stale", "archive_reason",
    "source_ref", "next_step", "our_status", "triage", "responsibility_evidence", "source_kind",
    "reopen_cycle_id",
    "source_row_keys", "source_row_fingerprints",
    "completed_at",
    "created_at",
    "transfer_disposition", "transfer_target", "transfer_requested_at", "transfer_confirmed_at", "transfer_confirmation", "completion_kind",
    "latest_brief", "initial_brief", "phase", "progress", "final_result", "current_agent_session_id", "current_agent_name", "last_agent_session_id", "last_agent_name", "last_agent_activity_at", "agent_state", "agent_work_status", "revision", "handoff",
    "workflow", "workflow_template_id", "workflow_stage", "workflow_stage_status", "agent_bindings",
    "iteration_id", "reopen_count", "reopen_history", "manual_status", "manual_completed_at", "manual_completed_by", "attachments",
}
MAX_PROMPT_CHARS = 4000
MAX_RESULT_CHARS = 2000
MAX_SOURCE_FILE_BYTES = 15 * 1024 * 1024
ALLOWED_SOURCE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md", ".markdown", ".png", ".jpg", ".jpeg"}
# Archive fixed slots. Anything else is an "other source"; the slot value is
# persisted so agents can also use it outside the dashboard.
SOURCE_SLOTS = ("requirement_doc", "figma", "codesign", "prototype", "testcase", "supplementary", "api_doc", "architecture", "standard", "other")
SOURCE_SLOT_LABELS = {
    "requirement_doc": "需求列表",
    "figma": "Figma",
    "codesign": "高保真",
    "prototype": "高保真",
    "testcase": "测试用例",
    "supplementary": "补充材料",
    "api_doc": "接口文档",
    "architecture": "公共规范",
    "standard": "公共规范",
    "other": "其他资料",
}
# Fixed slots are scoped: interface contracts are shared across versions while
# the requirement/design/test matrix belongs to exactly one version.
GLOBAL_FIXED_SLOTS = {"api_doc", "architecture", "standard"}
ITERATION_FIXED_SLOTS = {"requirement_doc", "figma", "prototype", "codesign", "testcase"}
ITERATION_STATUSES = {"planning", "active", "pending_release", "released", "archived"}
DEFAULT_ITERATION_STATUS = "active"
MAX_ITERATION_TAG_CHARS = 50
MAX_ITERATION_TITLE_CHARS = 120
MAX_ITERATION_ID_CHARS = 120
# Must stay aligned with the iteration routes in server.py. Real IDs such as
# `ITER-13.4.2-iter2` and `ITER-13.4.0-ANALYTICS` contain dots.
ITERATION_ID_PATTERN = rf"[A-Za-z0-9._-]{{1,{MAX_ITERATION_ID_CHARS}}}"
ITERATION_ID_RE = re.compile(rf"\A{ITERATION_ID_PATTERN}\Z")
SOURCE_INSPECTION_MODES = {"auto", "manual"}
MAX_SOURCE_GROUP_ID = 100
MAX_SOURCE_VERSION_LABEL = 80
MAX_RECORD_ATTACHMENT_BYTES = 10 * 1024 * 1024
RECORD_IMAGE_MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
SOURCE_ATTACHMENT_MIMES = {**RECORD_IMAGE_MIMES, ".heic": "image/heic", ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v", ".zip": "application/zip", ".pdf": "application/pdf", ".txt": "text/plain", ".log": "text/plain", ".json": "application/json", ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".ppt": "application/vnd.ms-powerpoint", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
MAX_SOURCE_ATTACHMENT_BYTES = 100 * 1024 * 1024


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso_datetime(value: Any):
    if not isinstance(value, str) or not value: return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def completion_timestamp(record: dict[str, Any]) -> str | None:
    """Return a recorded completion time only when evidence identifies one."""
    explicit = record.get("completed_at")
    if _parse_iso_datetime(explicit): return explicit
    if record.get("manual_status") == "completed" and _parse_iso_datetime(record.get("manual_completed_at")):
        return record["manual_completed_at"]
    dated = []
    for event in record.get("history", []):
        if not isinstance(event, dict) or not _parse_iso_datetime(event.get("at")):
            continue
        # A plain completed snapshot is only an observation. Accept dates only
        # when the history entry records an explicit completion action or a
        # concrete transition from a nonterminal state.
        note = str(event.get("note") or "")
        explicit_manual = "用户在个人工作台手动标记完成" in note
        from_status = event.get("from_status") or event.get("previous_status")
        to_status = event.get("to_status") or event.get("status")
        explicit_transition = to_status == "completed" and from_status in VALID_STATUSES and from_status not in BATCH_TERMINAL_STATUSES
        explicit_agent = event.get("kind") == "agent_report" and to_status == "completed"
        if explicit_manual or explicit_transition or explicit_agent:
            dated.append(event["at"])
    return max(dated, key=lambda value: _parse_iso_datetime(value)) if dated else None


def has_fresh_agent_owner(record: dict[str, Any], now=None) -> bool:
    from datetime import timedelta
    if not record.get("current_agent_session_id"): return False
    activity = _parse_iso_datetime(record.get("last_agent_activity_at"))
    if not activity: return False
    now = now or datetime.now().astimezone()
    if now.tzinfo is None: now = now.astimezone()
    return activity > now - timedelta(hours=2)


def effective_work_status(record: dict[str, Any], now=None) -> tuple[str, str]:
    if record.get("completion_kind") == "transferred" and record.get("transfer_disposition") == "transferred": return "completed", "transferred"
    if record.get("manual_status") == "completed": return "completed", "manual"
    if record.get("type") == "bug" and bug_has_our_delivery(record): return "completed", "our_status"
    status = str(record.get("status", "unknown"))
    # 待发布/责任人变更是人工设定的终态，不能被"无在线 Agent"覆盖成搁置。
    if status in BATCH_TERMINAL_STATUSES or status in {"pending_release", "owner_changed"}: return status, "source_status"
    workflow_state = record.get("workflow") if isinstance(record.get("workflow"), dict) else {}
    workflow_stage = str(workflow_state.get("stage") or record.get("workflow_stage") or "")
    workflow_stage_status = str(workflow_state.get("stage_status") or record.get("workflow_stage_status") or "")
    lock = workflow.active_lock(record, now)
    if lock is not None:
        try:
            return workflow.status_for_stage(workflow_stage, record.get("type"), status), "workflow_lock"
        except workflow.WorkflowError:
            pass
    try:
        if workflow_stage and workflow_stage_status in {"active", "rework"}:
            return workflow.status_for_stage(workflow_stage, record.get("type"), status), "workflow_stage"
    except workflow.WorkflowError:
        pass
    if has_fresh_agent_owner(record, now):
        agent_status = record.get("agent_work_status")
        return (agent_status if agent_status in {"in_progress", "developing", "testing"} else "in_progress"), "agent_owner"
    return "on_hold", "no_fresh_owner"


def load_store() -> dict[str, Any]:
    return with_lock(lambda: backend.load(ROOT, STORE))


def atomic_write(data: dict[str, Any]) -> None:
    with_lock(lambda: backend.commit(ROOT, STORE, data))


def atomic_write_preserving_context(data: dict[str, Any], kind: str, record_id: str) -> None:
    """Persist structured metadata while retaining the existing human context byte-for-byte."""
    relative = f"records/{kind}/{record_id}"
    directory = ROOT / "records" / kind / record_id
    if directory.is_symlink() or (directory.exists() and directory.resolve().parent != (ROOT / "records" / kind).resolve()):
        raise ValueError("invalid_record_context_path")
    context_path = directory / "context.md"
    overrides = {}
    if context_path.is_symlink():
        raise ValueError("invalid_record_context_path")
    if context_path.is_file():
        overrides[relative] = context_path.read_text(encoding="utf-8")
    with_lock(lambda: backend.commit(ROOT, STORE, data, context_overrides=overrides))


def event(action: str, record: dict[str, Any], revision: int, followup_id: str | None = None) -> None:
    item = {"at": timestamp(), "action": action, "id": record["id"],
            "type": record["type"], "revision": revision,
            "store_revision": revision, "record_revision": int(record.get("revision", 0))}
    if followup_id:
        item["followup_id"] = followup_id
    with EVENTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def normalize_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("input must be a JSON object")
    unknown = set(raw) - ALLOWED_FIELDS
    if unknown:
        raise ValueError("unsupported fields: " + ", ".join(sorted(unknown)))
    record = dict(raw)
    for required in ("id", "type", "title", "status"):
        if not isinstance(record.get(required), str) or not record[required].strip():
            raise ValueError(f"{required} must be a non-empty string")
    if record["type"] not in VALID_TYPES:
        raise ValueError("type must be work, bug, or task")
    if record["status"] not in VALID_STATUSES:
        raise ValueError("unsupported status")
    if "handoff" in record:
        handoff = record["handoff"]
        allowed = {"goal", "verified", "unverified", "next_step", "acceptance", "blocker", "resume_condition", "updated_at"}
        if not isinstance(handoff, dict) or set(handoff) - allowed:
            raise ValueError("handoff must be an object containing supported fields")
        if any(not isinstance(v, str) or len(v) > 4000 for v in handoff.values()):
            raise ValueError("handoff fields must be strings of at most 4000 characters")
        if handoff.get("updated_at") and not _parse_iso_datetime(handoff["updated_at"]):
            raise ValueError("handoff updated_at must be a timezone-aware ISO datetime")
    for key in ("notes", "evidence", "related_ids", "history"):
        if key in record and not isinstance(record[key], list):
            raise ValueError(f"{key} must be a JSON array")
    if "agent_bindings" in record:
        bindings = record["agent_bindings"]
        if not isinstance(bindings, dict) or set(bindings) - {"dev", "auditor", "pm"}:
            raise ValueError("agent_bindings must be an object keyed by dev/auditor/pm")
        for role, binding in bindings.items():
            if not isinstance(binding, dict):
                raise ValueError("agent_binding must be an object")
            if set(binding) - {"actor", "session_id", "channel", "bound_at"}:
                raise ValueError("agent_binding has unsupported fields")
            if not isinstance(binding.get("actor"), str) or not binding["actor"].strip() or len(binding["actor"]) > 100:
                raise ValueError("agent_binding actor is required")
            if binding.get("session_id") is not None and (not isinstance(binding["session_id"], str) or len(binding["session_id"]) > 200):
                raise ValueError("agent_binding session_id is invalid")
            if binding.get("channel") is not None and (not isinstance(binding["channel"], str) or not binding["channel"].strip() or len(binding["channel"]) > 50):
                raise ValueError("agent_binding channel is invalid")
            if binding.get("bound_at") is not None and not _parse_iso_datetime(binding["bound_at"]):
                raise ValueError("agent_binding bound_at must be a timezone-aware ISO datetime")
    if "source_row_keys" in record and (not isinstance(record["source_row_keys"], list) or any(not isinstance(value, str) for value in record["source_row_keys"])):
        raise ValueError("source_row_keys must be an array of strings")
    if "source_row_fingerprints" in record and (not isinstance(record["source_row_fingerprints"], dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in record["source_row_fingerprints"].items())):
        raise ValueError("source_row_fingerprints must be a string map")
    if "iteration_id" in record and record["iteration_id"] is not None and (not isinstance(record["iteration_id"], str) or len(record["iteration_id"]) > 100):
        raise ValueError("iteration_id must be a string of at most 100 characters or null")
    if "reopen_count" in record and record["reopen_count"] is not None and (not isinstance(record["reopen_count"], int) or record["reopen_count"] < 0):
        raise ValueError("reopen_count must be a non-negative integer")
    if "reopen_history" in record and not isinstance(record["reopen_history"], list):
        raise ValueError("reopen_history must be a JSON array")
    if "attachments" in record and not isinstance(record["attachments"], list):
        raise ValueError("attachments must be a JSON array")
    if "manual_status" in record and record["manual_status"] is not None and not isinstance(record["manual_status"], str):
        raise ValueError("manual_status must be a string or null")
    if "manual_completed_at" in record and record["manual_completed_at"] is not None and not _parse_iso_datetime(record["manual_completed_at"]):
        raise ValueError("manual_completed_at must be a timezone-aware ISO datetime or null")
    if "manual_completed_by" in record and record["manual_completed_by"] is not None and not isinstance(record["manual_completed_by"], str):
        raise ValueError("manual_completed_by must be a string or null")
    if "completed_at" in record and record["completed_at"] is not None and not _parse_iso_datetime(record["completed_at"]):
        raise ValueError("completed_at must be a timezone-aware ISO datetime or null")
    if "created_at" in record and record["created_at"] is not None and not _parse_iso_datetime(record["created_at"]):
        raise ValueError("created_at must be a timezone-aware ISO datetime or null")
    for key in ("transfer_requested_at", "transfer_confirmed_at"):
        if key in record and record[key] is not None and not _parse_iso_datetime(record[key]):
            raise ValueError(f"{key} must be a timezone-aware ISO datetime or null")
    if record.get("transfer_disposition") not in (None, "transfer_pending", "transferred"):
        raise ValueError("invalid transfer_disposition")
    if record.get("completion_kind") not in (None, "transferred"):
        raise ValueError("invalid completion_kind")
    if "source_updated_at" not in record:
        record["source_updated_at"] = None
    record.setdefault("observed_at", None)
    record.setdefault("evidence_at", None)
    record.setdefault("status_detail", "")
    record.setdefault("notes", [])
    record.setdefault("evidence", [])
    record.setdefault("related_ids", [])
    record.setdefault("history", [])
    workflow.normalize_record_fields(record)
    record["updated_at"] = timestamp()
    return record


BATCH_TERMINAL_STATUSES = {"completed", "cancelled", "archived"}
BUG_DELIVERED_MARKERS = ("我方完成/已提交测试", "我方已提交测试", "我方已提测")
BUG_CLOSED_PLATFORM_STATUSES = {"关闭", "已关闭"}
BUG_ASSOCIATION_HOLD_PREFIX = "历史关联待核："


def bug_has_our_delivery(record: dict[str, Any]) -> bool:
    return any(
        marker in str(record.get(field) or "")
        for field in ("our_status", "triage", "status_detail")
        for marker in BUG_DELIVERED_MARKERS
    )


def classify_bug_sync_status(item: dict[str, Any]) -> str:
    """Keep source status intact while deriving a stable local dashboard bucket."""
    platform_status = str(item.get("status") or "").strip()
    triage = str(item.get("triage") or "")
    if triage.startswith(BUG_ASSOCIATION_HOLD_PREFIX):
        return "archived"
    if platform_status in BUG_CLOSED_PLATFORM_STATUSES:
        return "completed"
    if platform_status == "测试":
        return "completed" if bug_has_our_delivery(item) else "awaiting_acceptance"
    if platform_status in {"分析", "修复", "处理中"}:
        return "in_progress"
    return "unknown"


def _effective_record_status(record: dict[str, Any]) -> str:
    if record.get("manual_status") == "completed":
        return "completed"
    if record.get("type") == "bug" and bug_has_our_delivery(record):
        return "completed"
    return str(record.get("status", "unknown"))


def _is_batch_terminal(record: dict[str, Any]) -> bool:
    return _effective_record_status(record) in BATCH_TERMINAL_STATUSES


def _batch_by_id(db: dict[str, Any], batch_id: str | None) -> dict[str, Any] | None:
    if not batch_id:
        return None
    batches = db.setdefault("batches", [])
    if not isinstance(batches, list):
        raise ValueError("batch_manifest_invalid")
    return next((batch for batch in batches if batch.get("id") == batch_id), None)


def _batch_has_active_tasks(db: dict[str, Any], batch: dict[str, Any]) -> bool:
    task_ids = set(batch.get("task_ids", []))
    return any(record.get("type") == "task" and record.get("id") in task_ids and not _is_batch_terminal(record)
               for record in db.get("records", []))


def _start_batch(db: dict[str, Any], at: str | None = None) -> dict[str, Any]:
    at = at or timestamp()
    previous = _batch_by_id(db, db.get("current_batch_id"))
    if previous and previous.get("state") == "current":
        previous["state"] = "past"
        previous["past_at"] = at
    batch = {"id": str(uuid.uuid4()), "state": "current", "created_at": at, "task_ids": []}
    db.setdefault("batches", []).append(batch)
    db["current_batch_id"] = batch["id"]
    return batch


def _assign_task_to_current_batch(db: dict[str, Any], record: dict[str, Any], at: str | None = None) -> dict[str, Any]:
    batch = _batch_by_id(db, db.get("current_batch_id"))
    if not batch or batch.get("state") != "current" or not _batch_has_active_tasks(db, batch):
        batch = _start_batch(db, at)
    old_batch_id = record.get("batch_id")
    if old_batch_id and old_batch_id != batch["id"]:
        record.setdefault("batch_history", []).append({"at": at or timestamp(), "batch_id": old_batch_id, "action": "reopened_in_new_batch"})
    record["batch_id"] = batch["id"]
    if record["id"] not in batch["task_ids"]:
        batch["task_ids"].append(record["id"])
    return batch


def adopt_task_batch(task_id: str) -> dict[str, Any]:
    def update() -> dict[str, Any]:
        db = load_store()
        record = _find_record(db, task_id)
        if record.get("type") != "task":
            raise ValueError("record_not_task")
        if not record.get("batch_id"):
            _assign_task_to_current_batch(db, record)
            record.setdefault("initial_brief", record.get("status_detail") or record.get("title", ""))
            record["updated_at"] = timestamp()
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db)
            event("task_batch_adopted", record, db["revision"])
        return {"revision": db.get("revision", 0), "current_batch_id": db.get("current_batch_id"), "record": record}
    return with_lock(update)


def add_progress(record_id: str, message: str, actor: str = "agent", evidence: list[str] | None = None,
                 *, result: bool = False, outcome: str | None = None) -> dict[str, Any]:
    clean_message = message.strip() if isinstance(message, str) else ""
    clean_actor = actor.strip() if isinstance(actor, str) else ""
    if not clean_message or len(clean_message) > 4000 or not clean_actor or len(clean_actor) > 100:
        raise ValueError("invalid_progress")
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list) or len(evidence) > 20 or any(not isinstance(item, str) or len(item) > 1000 for item in evidence):
        raise ValueError("invalid_progress_evidence")
    if outcome is not None and outcome not in {"success", "failure", "partial", "cancelled"}:
        raise ValueError("invalid_result_outcome")

    def update() -> dict[str, Any]:
        db = load_store()
        record = _find_record(db, record_id)
        at = timestamp()
        entry = {"id": str(uuid.uuid4()), "at": at, "actor": clean_actor, "message": clean_message,
                 "evidence": list(evidence), "kind": "result" if result else "progress"}
        if result and outcome:
            entry["outcome"] = outcome
        record.setdefault("progress", []).append(entry)
        record["latest_brief"] = clean_message
        if result:
            record["final_result"] = entry
        record["updated_at"] = at
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("task_result" if result else "task_progress", record, db["revision"])
        return {"revision": db["revision"], "record": record, "progress": entry}
    return with_lock(update)


def task_context(record_id: str) -> dict[str, Any]:
    record = _find_record(load_store(), record_id)
    return {"record": record, "batch_id": record.get("batch_id"),
            "initial_brief": record.get("initial_brief") or record.get("status_detail") or record.get("title", ""),
            "latest_brief": record.get("latest_brief", ""), "progress": list(record.get("progress", [])),
            "final_result": record.get("final_result"), "followups": list(record.get("followups", [])),
            "evidence": list(record.get("evidence", [])), "related_ids": list(record.get("related_ids", []))}


def _real_session(session_id: str | None, *, explicit_caller: bool = False) -> str:
    actual = os.environ.get("CODEX_THREAD_ID")
    candidate = session_id if explicit_caller else actual or session_id
    if (not candidate or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", candidate)
            or (not explicit_caller and actual and session_id is not None and session_id != actual)):
        raise ValueError("invalid_agent_session")
    return candidate


def _check_agent_update(record: dict[str, Any], db: dict[str, Any], session: str, expected_revision: int) -> None:
    if expected_revision != int(record.get("revision", 0)):
        raise ValueError("revision_conflict")
    if record.get("current_agent_session_id") != session:
        raise ValueError("agent_owner_conflict")


def _agent_display_name(value: str | None) -> str | None:
    if value is None: return None
    if not isinstance(value, str): raise ValueError("invalid_agent_name")
    clean = " ".join(value.split())
    if not clean or len(clean) > 80 or any(ord(char) < 32 for char in clean): raise ValueError("invalid_agent_name")
    return clean


def _agent_response(record: dict[str, Any], db: dict[str, Any]) -> dict[str, Any]:
    """Return the per-record CAS token separately from the dashboard-wide revision."""
    return {"revision": int(record.get("revision", 0)), "store_revision": int(db.get("revision", 0)), "record": record}


def agent_claim(record_id: str, expected_revision: int, session_id: str | None = None, agent_name: str | None = None, *, explicit_caller: bool = False) -> dict[str, Any]:
    session = _real_session(session_id, explicit_caller=explicit_caller)
    display_name = _agent_display_name(agent_name)
    def update():
        db = load_store(); record = _find_record(db, record_id)
        if _effective_record_status(record) in BATCH_TERMINAL_STATUSES: raise ValueError("record_completed")
        if expected_revision != int(record.get("revision", 0)): raise ValueError("revision_conflict")
        owner = record.get("current_agent_session_id")
        if owner and owner != session:
            if has_fresh_agent_owner(record): raise ValueError("agent_owner_conflict")
            record["last_agent_session_id"] = owner
            record["last_agent_name"] = record.get("current_agent_name")
            record["current_agent_session_id"] = None
            record["current_agent_name"] = None
            record["agent_state"] = "paused"
        at = timestamp()
        if record.get("agent_work_status") == "on_hold": record["agent_work_status"] = "in_progress"
        record.update({"current_agent_session_id": session,
                       "current_agent_name": display_name or record.get("current_agent_name"),
                       "last_agent_activity_at": at, "agent_state": "active"})
        db["revision"] = int(db.get("revision", 0)) + 1; record["updated_at"] = at
        atomic_write(db); event("agent_claim", record, db["revision"])
        return _agent_response(record, db)
    return with_lock(update)


def agent_heartbeat(record_id: str, expected_revision: int, session_id: str | None = None, activity_kind: str = "agent", now=None, *, explicit_caller: bool = False) -> dict[str, Any]:
    if activity_kind != "agent": raise ValueError("activity_does_not_extend_lease")
    session = _real_session(session_id, explicit_caller=explicit_caller)
    def update():
        db = load_store(); record = _find_record(db, record_id)
        if _effective_record_status(record) in BATCH_TERMINAL_STATUSES: raise ValueError("record_completed")
        if record.get("agent_state") == "paused" and record.get("last_agent_session_id") == session:
            raise ValueError("agent_expired")
        _check_agent_update(record, db, session, expected_revision)
        now_value = now or datetime.now().astimezone()
        if now_value.tzinfo is None: now_value = now_value.astimezone()
        if not has_fresh_agent_owner(record, now_value):
            record["last_agent_session_id"] = session
            record["last_agent_name"] = record.get("current_agent_name")
            record["current_agent_session_id"] = None; record["agent_state"] = "paused"
            record["current_agent_name"] = None
            record["updated_at"] = now_value.isoformat(timespec="seconds")
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db); event("agent_stale_paused", record, db["revision"])
            raise ValueError("agent_expired")
        at = now_value.isoformat(timespec="seconds"); record["last_agent_activity_at"] = at; record["agent_state"] = "active"
        db["revision"] = int(db.get("revision", 0)) + 1; record["updated_at"] = at
        atomic_write(db); event("agent_heartbeat", record, db["revision"])
        return _agent_response(record, db)
    return with_lock(update)


def agent_report(record_id: str, expected_revision: int, message: str, session_id: str | None = None, status: str | None = None, evidence: list[str] | None = None, phase: str | None = None, context_markdown: str | None = None, *, explicit_caller: bool = False) -> dict[str, Any]:
    session = _real_session(session_id, explicit_caller=explicit_caller)
    clean = message.strip() if isinstance(message, str) else ""
    if not clean or len(clean) > 4000: raise ValueError("invalid_progress")
    if context_markdown is not None and (not isinstance(context_markdown, str) or not context_markdown.strip() or len(context_markdown) > 100000):
        raise ValueError("invalid_progress_context")
    if status is not None and status not in VALID_STATUSES: raise ValueError("unsupported status")
    if phase is not None and (not isinstance(phase, str) or not phase.strip() or len(phase) > 200): raise ValueError("invalid_phase")
    if evidence is not None and (not isinstance(evidence, list) or len(evidence) > 20 or any(not isinstance(item, str) or len(item) > 1000 for item in evidence)):
        raise ValueError("invalid_progress_evidence")
    def update():
        db = load_store(); record = _find_record(db, record_id)
        if _effective_record_status(record) in BATCH_TERMINAL_STATUSES: raise ValueError("record_completed")
        _check_agent_update(record, db, session, expected_revision)
        now = datetime.now().astimezone()
        if not has_fresh_agent_owner(record, now):
            record["last_agent_session_id"] = session
            record["last_agent_name"] = record.get("current_agent_name")
            record["current_agent_session_id"] = None; record["current_agent_name"] = None; record["agent_state"] = "paused"
            record["updated_at"] = now.isoformat(timespec="seconds")
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db); event("agent_stale_paused", record, db["revision"])
            raise ValueError("agent_expired")
        at = timestamp(); entry = {"id": str(uuid.uuid4()), "at": at, "actor": session, "message": clean, "evidence": evidence or [], "kind": "agent_report"}
        if context_markdown is not None: entry["context_markdown"] = context_markdown.strip()
        record.setdefault("initial_brief", record.get("status_detail") or record.get("title", ""))
        record.setdefault("progress", []).append(entry); record["latest_brief"] = clean
        if phase is not None: record["phase"] = phase
        if status is not None:
            entry["status"] = status
            if status in {"in_progress", "developing", "testing", "on_hold"}:
                record["agent_work_status"] = status
            else:
                record["status"] = status
        if status == "on_hold":
            record["last_agent_session_id"] = session; record["last_agent_name"] = record.get("current_agent_name")
            record["current_agent_session_id"] = None; record["current_agent_name"] = None; record["agent_state"] = "paused"
        if status in BATCH_TERMINAL_STATUSES:
            if status in {"completed", "cancelled"}: record["completed_at"] = at
            if record.get("current_agent_session_id"):
                record["last_agent_session_id"] = record["current_agent_session_id"]
                record["last_agent_name"] = record.get("current_agent_name")
                record["current_agent_session_id"] = None
                record["current_agent_name"] = None
            record["agent_state"] = "completed" if status == "completed" else "idle"
        record["last_agent_activity_at"] = at; record["updated_at"] = at
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db); event("agent_report", record, db["revision"])
        return {**_agent_response(record, db), "progress": entry}
    return with_lock(update)


def agent_update(record_id: str, expected_revision: int, message: str, session_id: str | None = None,
                 agent_name: str | None = None, status: str | None = None, evidence: list[str] | None = None,
                 phase: str | None = None, context_markdown: str | None = None, *, explicit_caller: bool = False) -> dict[str, Any]:
    """Atomically claim/reclaim the calling agent, report work, renew activity, and release on terminal status."""
    session = _real_session(session_id, explicit_caller=explicit_caller)
    display_name = _agent_display_name(agent_name)
    clean = message.strip() if isinstance(message, str) else ""
    if not clean or len(clean) > 4000: raise ValueError("invalid_progress")
    if context_markdown is not None and (not isinstance(context_markdown, str) or not context_markdown.strip() or len(context_markdown) > 100000):
        raise ValueError("invalid_progress_context")
    if status is not None and status not in VALID_STATUSES: raise ValueError("unsupported status")
    if phase is not None and (not isinstance(phase, str) or not phase.strip() or len(phase) > 200): raise ValueError("invalid_phase")
    if evidence is None: evidence = []
    if not isinstance(evidence, list) or len(evidence) > 20 or any(not isinstance(item, str) or len(item) > 1000 for item in evidence):
        raise ValueError("invalid_progress_evidence")

    def update():
        db = load_store(); record = _find_record(db, record_id)
        if expected_revision != int(record.get("revision", 0)): raise ValueError("revision_conflict")
        if _effective_record_status(record) in BATCH_TERMINAL_STATUSES: raise ValueError("record_completed")
        owner = record.get("current_agent_session_id")
        current_time = datetime.now().astimezone()
        if owner and owner != session:
            if has_fresh_agent_owner(record, current_time): raise ValueError("agent_owner_conflict")
            record["last_agent_session_id"] = owner
            record["last_agent_name"] = record.get("current_agent_name")
            record["current_agent_session_id"] = None; record["current_agent_name"] = None; record["agent_state"] = "paused"
        at = timestamp()
        active_name = display_name or (record.get("current_agent_name") if owner == session else None)
        record["current_agent_session_id"] = session
        record["current_agent_name"] = active_name
        record.setdefault("initial_brief", record.get("status_detail") or record.get("title", ""))
        entry = {"id": str(uuid.uuid4()), "at": at, "actor": session, "message": clean, "evidence": evidence, "kind": "agent_update"}
        if context_markdown is not None: entry["context_markdown"] = context_markdown.strip()
        record.setdefault("progress", []).append(entry)
        record["latest_brief"] = clean
        if phase is not None: record["phase"] = phase
        if status is not None:
            entry["status"] = status
            if status in {"in_progress", "developing", "testing", "on_hold"}:
                record["agent_work_status"] = status
            else:
                record["status"] = status
        elif record.get("agent_work_status") == "on_hold":
            record["agent_work_status"] = "in_progress"
        record["last_agent_activity_at"] = at; record["updated_at"] = at
        if status == "on_hold":
            record["last_agent_session_id"] = session; record["last_agent_name"] = active_name
            record["current_agent_session_id"] = None; record["current_agent_name"] = None; record["agent_state"] = "paused"
        elif status in BATCH_TERMINAL_STATUSES:
            if status in {"completed", "cancelled"}: record["completed_at"] = at
            record["last_agent_session_id"] = session; record["last_agent_name"] = active_name
            record["current_agent_session_id"] = None; record["current_agent_name"] = None
            record["agent_state"] = "completed" if status == "completed" else "idle"
        else:
            record["agent_state"] = "active"
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db); event("agent_update", record, db["revision"])
        return {**_agent_response(record, db), "progress": entry}
    return with_lock(update)


def _brief_from_context(context: str) -> str:
    in_progress = False
    english_state = False
    for line in context.splitlines():
        value = line.strip()
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", value)
        if heading:
            if in_progress: break
            name = heading.group(1).strip()
            english_state = name.casefold() == "current state"
            in_progress = english_state or name in {"最新进展", "当前进展", "最新简报"}
            continue
        if in_progress and value and not value.startswith("<!--"):
            value = re.sub(r"^[-*+]\s+", "", value).strip()
            if english_state:
                conclusion = re.match(r"^conclusion\s*[:：]\s*(.*?)\s*$", value, re.IGNORECASE)
                if conclusion and conclusion.group(1).strip(): return conclusion.group(1).strip()[:4000]
                continue
            if value and not value.endswith((":", "：")): return value[:4000]
    raise ValueError("message_required_no_reliable_progress")


def agent_release(record_id: str, expected_revision: int, session_id: str | None = None, *, explicit_caller: bool = False) -> dict[str, Any]:
    session = _real_session(session_id, explicit_caller=explicit_caller)
    def update():
        db = load_store(); record = _find_record(db, record_id); _check_agent_update(record, db, session, expected_revision)
        record["last_agent_session_id"] = session; record["last_agent_name"] = record.get("current_agent_name")
        record["current_agent_session_id"] = None; record["current_agent_name"] = None; record["agent_state"] = "idle"
        db["revision"] = int(db.get("revision", 0)) + 1; record["updated_at"] = timestamp()
        atomic_write(db); event("agent_release", record, db["revision"])
        return _agent_response(record, db)
    return with_lock(update)


def agent_sweep(now=None) -> dict[str, Any]:
    from datetime import datetime
    now = now or datetime.now().astimezone(); changed = []
    def update():
        db = load_store()
        for record in db["records"]:
            if record.get("current_agent_session_id"):
                if not has_fresh_agent_owner(record, now) and _effective_record_status(record) not in BATCH_TERMINAL_STATUSES:
                    record["last_agent_session_id"] = record["current_agent_session_id"]
                    record["last_agent_name"] = record.get("current_agent_name")
                    record["current_agent_session_id"] = None; record["current_agent_name"] = None; record["agent_state"] = "paused"
                    changed.append(record["id"])
        if changed:
            db["revision"] = int(db.get("revision", 0)) + 1; atomic_write(db)
            for ident in changed: event("agent_stale_paused", _find_record(db, ident), db["revision"])
        return {"revision": db.get("revision", 0), "paused": changed}
    return with_lock(update)


def set_work_hours(start: str, end: str) -> dict[str, Any]:
    pattern = r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    if not isinstance(start, str) or not isinstance(end, str) or not re.fullmatch(pattern, start) or not re.fullmatch(pattern, end) or start >= end:
        raise ValueError("invalid_work_hours")
    def update():
        db = load_store()
        hours = {"start": start, "end": end, "timezone": "Asia/Shanghai"}
        if db.get("preferences", {}).get("work_hours") == hours:
            return {"revision": db.get("revision", 0), "work_hours": hours}
        db.setdefault("preferences", {})["work_hours"] = hours
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        return {"revision": db["revision"], "work_hours": hours}
    return with_lock(update)


def update_preferences(patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch or set(patch) - {"work_hours", "theme"}:
        raise ValueError("invalid_preferences")
    work_hours = patch.get("work_hours")
    if work_hours is not None:
        if not isinstance(work_hours, dict) or set(work_hours) != {"start", "end"}:
            raise ValueError("invalid_work_hours")
        start, end = work_hours.get("start"), work_hours.get("end")
        pattern = r"^(?:[01]\d|2[0-3]):[0-5]\d$"
        if not isinstance(start, str) or not isinstance(end, str) or not re.fullmatch(pattern, start) or not re.fullmatch(pattern, end) or start >= end:
            raise ValueError("invalid_work_hours")
    theme = patch.get("theme")
    if theme is not None and theme not in {"wood", "minimal", "dark"}:
        raise ValueError("invalid_theme")
    def update():
        db = load_store()
        prefs = db.setdefault("preferences", {})
        changed = False
        if work_hours is not None:
            value = {"start": work_hours["start"], "end": work_hours["end"], "timezone": "Asia/Shanghai"}
            if prefs.get("work_hours") != value:
                prefs["work_hours"] = value; changed = True
        if theme is not None and prefs.get("theme") != theme:
            prefs["theme"] = theme; changed = True
        if changed:
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db)
        return {"revision": db.get("revision", 0), "preferences": prefs}
    return with_lock(update)


def override_source(source_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    allowed = {"name", "category", "purpose", "primary", "verification_state"}
    if not isinstance(fields, dict) or set(fields) - allowed: raise ValueError("invalid_source_override")
    def update():
        db = load_store(); sources = _source_store_list(db)
        source = next((item for item in sources if item.get("id") == source_id), None)
        if source is None: raise ValueError("source_not_found")
        overrides = source.setdefault("overrides", {})
        for key, value in fields.items():
            if key == "primary" and not isinstance(value, bool): raise ValueError("invalid_source_override")
            if key != "primary" and not isinstance(value, str): raise ValueError("invalid_source_override")
            overrides[key] = value
        source.update(overrides); source["updated_at"] = timestamp()
        db["revision"] = int(db.get("revision", 0)) + 1; atomic_write(db)
        _source_event("source_overridden", source, db["revision"])
        return {"revision": db["revision"], "source": source}
    return with_lock(update)


def set_source_lifecycle(source_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    allowed = {"lifecycle_status", "lifecycle_reason", "last_verified_at", "lifecycle_override"}
    if not isinstance(fields, dict) or not fields or set(fields) - allowed: raise ValueError("invalid_source_lifecycle")
    states = {"registered", "suspected_deprecated", "deprecated"}
    if "lifecycle_status" in fields and fields["lifecycle_status"] not in states: raise ValueError("invalid_source_lifecycle")
    if "lifecycle_override" in fields and fields["lifecycle_override"] not in {None, "active", "deprecated"}: raise ValueError("invalid_source_lifecycle")
    for key in ("lifecycle_reason", "last_verified_at"):
        if key in fields and fields[key] is not None and not isinstance(fields[key], str): raise ValueError("invalid_source_lifecycle")
    def update():
        db = load_store(); sources = _source_store_list(db)
        source = next((item for item in sources if item.get("id") == source_id), None)
        if source is None: raise ValueError("source_not_found")
        at = timestamp()
        if "lifecycle_override" in fields:
            source["lifecycle_override"] = fields["lifecycle_override"]
            if fields["lifecycle_override"] == "active":
                source["lifecycle_status"] = "registered"; source["suspected_at"] = None; source["deprecated_at"] = None
            elif fields["lifecycle_override"] == "deprecated":
                source["lifecycle_status"] = "deprecated"; source["deprecated_at"] = at; source["suspected_at"] = None
        if "lifecycle_status" in fields:
            # A durable human decision takes precedence over scanner suggestions.
            if source.get("lifecycle_override") == "active": source["lifecycle_status"] = "registered"
            elif source.get("lifecycle_override") == "deprecated": source["lifecycle_status"] = "deprecated"
            else:
                source["lifecycle_status"] = fields["lifecycle_status"]
            source["suspected_deprecated"] = source.get("lifecycle_status") == "suspected_deprecated"
            if source.get("lifecycle_status") == "suspected_deprecated": source["suspected_at"] = at
            if source.get("lifecycle_status") == "deprecated": source["deprecated_at"] = at
        if "lifecycle_reason" in fields: source["lifecycle_reason"] = fields["lifecycle_reason"]
        if "last_verified_at" in fields:
            source["last_verified_at"] = fields["last_verified_at"]
            if fields["last_verified_at"] and source.get("lifecycle_override") != "deprecated":
                source["lifecycle_status"] = "registered"; source["suspected_at"] = None; source["deprecated_at"] = None
        source["lifecycle_updated_at"] = at; source["updated_at"] = at
        db["revision"] = int(db.get("revision", 0)) + 1; atomic_write(db)
        _source_event("source_lifecycle_updated", source, db["revision"])
        return {"revision": db["revision"], "source": source}
    return with_lock(update)


def set_source_read_state(source_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Persist one scanner read attempt without changing source lifecycle."""
    allowed = {"last_read_at", "read_state", "read_scope", "content_updated_at", "read_gap", "snapshot_revision",
               "content_hash", "unchanged_since", "last_successful_read_at", "last_recheck_at"}
    if not isinstance(fields, dict) or not fields or set(fields) - allowed:
        raise ValueError("invalid_source_read_state")
    if fields.get("read_state") not in {"success", "partial", "failed", "skipped_immutable"}:
        raise ValueError("invalid_source_read_state")
    for key in ("last_read_at", "read_scope", "snapshot_revision", "unchanged_since", "last_successful_read_at", "last_recheck_at"):
        if key not in fields:
            continue
        if key == "last_read_at" and fields[key] is None and fields.get("read_state") == "skipped_immutable":
            continue
        if not isinstance(fields[key], str) or len(fields[key]) > 4000:
            raise ValueError("invalid_source_read_state")
    if "content_hash" in fields and fields["content_hash"] is not None and (not isinstance(fields["content_hash"], str) or len(fields["content_hash"]) > 128):
        raise ValueError("invalid_source_read_state")
    if "content_hash" in fields and fields["content_hash"] and not re.fullmatch(r"[0-9a-fA-F]{32,128}", fields["content_hash"]):
        raise ValueError("invalid_source_read_state")
    if "content_updated_at" in fields and fields["content_updated_at"] is not None and (not isinstance(fields["content_updated_at"], str) or len(fields["content_updated_at"]) > 100):
        raise ValueError("invalid_source_read_state")
    gap = fields.get("read_gap", [])
    if not isinstance(gap, (list, str)) or (isinstance(gap, list) and (len(gap) > 100 or any(not isinstance(item, str) or len(item) > 1000 for item in gap))) or (isinstance(gap, str) and len(gap) > 4000):
        raise ValueError("invalid_source_read_state")
    if fields["read_state"] == "success" and gap not in ([], ""):
        raise ValueError("invalid_source_read_state")
    if fields["read_state"] == "skipped_immutable" and gap not in ([], ""):
        raise ValueError("invalid_source_read_state")
    def update():
        db = load_store(); sources = _source_store_list(db)
        source = next((item for item in sources if item.get("id") == source_id), None)
        if source is None: raise ValueError("source_not_found")
        previous_hash = source.get("content_hash")
        previous_unchanged_since = source.get("unchanged_since")
        # Success timestamps and hash-age origin are derived here so a failed
        # or partial scan cannot manufacture continuity evidence.
        source.update({key: value for key, value in fields.items()
                       if key not in {"last_successful_read_at", "unchanged_since", "content_hash"}})
        if fields["read_state"] == "success":
            read_at = fields["last_read_at"]
            source["last_successful_read_at"] = read_at
            new_hash = fields.get("content_hash") or previous_hash
            if new_hash:
                source["content_hash"] = new_hash
                if previous_hash == new_hash and previous_unchanged_since:
                    source["unchanged_since"] = previous_unchanged_since
                else:
                    source["unchanged_since"] = read_at
                    if previous_hash and previous_hash != new_hash and source.get("lifecycle_override") is None:
                        source["lifecycle_status"] = "registered"
                        source["suspected_deprecated"] = False
                        source["suspected_at"] = None
                        source["deprecated_at"] = None
                        source["lifecycle_reason"] = None
                        source["lifecycle_updated_at"] = read_at
        source["updated_at"] = timestamp()
        db["revision"] = int(db.get("revision", 0)) + 1; atomic_write(db)
        _source_event("source_read_state_updated", source, db["revision"])
        return {"revision": db["revision"], "source": source}
    return with_lock(update)


def _image_mime(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    mime = RECORD_IMAGE_MIMES.get(suffix)
    if not mime or not data or len(data) > MAX_RECORD_ATTACHMENT_BYTES:
        raise ValueError("invalid_record_attachment")
    valid = {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }
    if not valid.get(mime, False): raise ValueError("record_attachment_type_mismatch")
    return mime


def add_record_image(record_id: str, filename: str, data: bytes) -> dict[str, Any]:
    if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", filename):
        raise ValueError("invalid_record_attachment_name")
    mime = _image_mime(filename, data)
    digest = hashlib.sha256(data).hexdigest()
    def update():
        db = load_store(); record = _find_record(db, record_id)
        if record.get("type") != "bug": raise ValueError("attachments_require_bug_record")
        attachments = record.setdefault("attachments", [])
        if any(item.get("filename") == filename for item in attachments): raise ValueError("attachment_duplicate")
        raw_base = ROOT / "records" / "bug" / record_id
        if raw_base.is_symlink(): raise ValueError("invalid_record_attachment_path")
        base = raw_base.resolve()
        expected_root = (ROOT / "records" / "bug").resolve()
        if base.parent != expected_root: raise ValueError("invalid_record_attachment_path")
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(base, 0o700)
        folder = base / "attachments"
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if folder.is_symlink(): raise ValueError("invalid_record_attachment_path")
        os.chmod(folder, 0o700)
        path = folder / filename
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ValueError("attachment_duplicate") from exc
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
            entry = {"filename": filename, "mime_type": mime, "size_bytes": len(data), "sha256": digest, "added_at": timestamp()}
            attachments.append(entry); record["updated_at"] = timestamp()
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db); event("record_image_added", record, db["revision"])
            return {"revision": db["revision"], "record": record, "attachment": entry}
        except Exception:
            # A committed reference is retained if post-commit event logging fails.
            current = load_store()
            current_record = next((item for item in current.get("records", []) if item.get("id") == record_id), None)
            if not current_record or not any(item.get("filename") == filename for item in current_record.get("attachments", [])):
                path.unlink(missing_ok=True)
            raise
    return with_lock(update)


def read_record_image(record_id: str, filename: str) -> tuple[bytes, str]:
    body, mime, _ = read_record_attachment(record_id, filename)
    return body, mime


def _source_attachment_mime(filename: str, data: bytes) -> str:
    mime = SOURCE_ATTACHMENT_MIMES.get(Path(filename).suffix.lower())
    if not mime or not data or len(data) > MAX_SOURCE_ATTACHMENT_BYTES:
        raise ValueError("invalid_record_attachment")
    suffix = Path(filename).suffix.lower()
    signatures = {
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"), ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"), ".gif": data.startswith((b"GIF87a", b"GIF89a")),
        ".webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
        ".zip": data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06"),
        ".doc": data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")),
        ".xls": data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")),
        ".ppt": data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")),
        ".docx": data.startswith((b"PK\x03\x04", b"PK\x05\x06")),
        ".xlsx": data.startswith((b"PK\x03\x04", b"PK\x05\x06")),
        ".pptx": data.startswith((b"PK\x03\x04", b"PK\x05\x06")),
        ".heic": len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"),
        ".pdf": data.startswith(b"%PDF-"), ".mp4": len(data) >= 12 and data[4:8] == b"ftyp",
        ".m4v": len(data) >= 12 and data[4:8] == b"ftyp", ".mov": len(data) >= 12 and data[4:8] == b"ftyp",
    }
    if suffix in signatures and not signatures[suffix]: raise ValueError("record_attachment_type_mismatch")
    return mime


def add_source_attachment(record_id: str, filename: str, data: bytes, entry: dict[str, Any]) -> dict[str, Any]:
    if not re.fullmatch(r"BUG\d{14}", record_id) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", filename):
        raise ValueError("invalid_record_attachment_name")
    mime = _source_attachment_mime(filename, data)
    digest = hashlib.sha256(data).hexdigest()
    def update():
        db = load_store(); record = _find_record(db, record_id)
        if record.get("type") != "bug": raise ValueError("attachments_require_bug_record")
        raw_base = ROOT / "records" / "bug" / record_id
        if raw_base.is_symlink(): raise ValueError("invalid_record_attachment_path")
        base = raw_base.resolve(); expected_root = (ROOT / "records" / "bug").resolve()
        if base.parent != expected_root: raise ValueError("invalid_record_attachment_path")
        base.mkdir(mode=0o700, parents=True, exist_ok=True); os.chmod(base, 0o700)
        folder = base / "attachments"; folder.mkdir(mode=0o700, exist_ok=True)
        if folder.is_symlink(): raise ValueError("invalid_record_attachment_path")
        os.chmod(folder, 0o700); path = folder / filename
        existing = record.setdefault("source_attachments", [])
        same = next((item for item in existing if item.get("id") == entry.get("id") and item.get("sha256") == digest), None)
        if same:
            changed = False
            for field in ("source_signature", "display_name", "kind"):
                if entry.get(field) is not None and same.get(field) != entry[field]:
                    same[field] = entry[field]; changed = True
            if changed:
                record["updated_at"] = timestamp(); db["revision"] = int(db.get("revision", 0)) + 1
                atomic_write_preserving_context(db, "bug", record_id)
                event("bug_source_metadata_refreshed", record, db["revision"])
            return {"revision": db["revision"], "attachment": same, "deduplicated": True}
        if any(item.get("filename") == filename for item in existing) or path.exists(): raise ValueError("attachment_duplicate")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle: handle.write(data); handle.flush(); os.fsync(handle.fileno())
            item = {**entry, "filename": filename, "mime_type": mime, "size_bytes": len(data), "sha256": digest,
                    "local_relative_path": f"records/bug/{record_id}/attachments/{filename}",
                    "download_url": f"/api/items/{record_id}/attachments/{filename}", "download_state": "downloaded", "error": None,
                    "added_at": timestamp()}
            existing.append(item); record["updated_at"] = timestamp(); db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write_preserving_context(db, "bug", record_id); event("bug_source_attachment_archived", record, db["revision"])
            return {"revision": db["revision"], "attachment": item, "deduplicated": False}
        except Exception:
            current = load_store(); current_record = next((r for r in current.get("records", []) if r.get("id") == record_id), None)
            if not current_record or not any(i.get("filename") == filename for i in current_record.get("source_attachments", [])): path.unlink(missing_ok=True)
            raise
    return with_lock(update)


def set_bug_evidence(record_id: str, detail: dict[str, Any], attachments: list[dict[str, Any]]) -> dict[str, Any]:
    def update():
        db = load_store(); record = _find_record(db, record_id)
        if record.get("type") != "bug": raise ValueError("attachments_require_bug_record")
        record["bug_detail"] = detail
        current = record.setdefault("source_attachments", [])
        by_key = {(str(item.get("id")), str(item.get("sha256") or "")): item for item in current}
        for item in attachments:
            key = (str(item.get("id")), str(item.get("sha256") or ""))
            prior_failed = next((old for old in current if str(old.get("id")) == key[0] and old.get("download_state") != "downloaded"), None)
            if item.get("download_state") == "downloaded" and prior_failed:
                prior_failed.update(item)
                current[:] = [old for old in current if old is prior_failed or str(old.get("id")) != key[0] or old.get("download_state") == "downloaded"]
                by_key[key] = prior_failed
            elif key not in by_key: current.append(item); by_key[key] = item
            elif item.get("download_state") == "downloaded":
                # Refresh source metadata without replacing the archived file;
                # this signature lets later patrols distinguish unchanged
                # attachments from a new version reusing the same platform ID.
                existing_item = by_key[key]
                for field in ("source_signature", "display_name", "kind"):
                    if item.get(field) is not None: existing_item[field] = item[field]
            elif item.get("download_state") != "downloaded" and by_key[key].get("download_state") != "downloaded": by_key[key].update(item)
        downloaded_ids = {str(item.get("id")) for item in current if item.get("download_state") == "downloaded"}
        seen = set()
        normalized = []
        for item in current:
            if item.get("download_state") != "downloaded" and str(item.get("id")) in downloaded_ids: continue
            key = (str(item.get("id")), str(item.get("sha256") or ""), item.get("download_state"))
            if key in seen: continue
            seen.add(key); normalized.append(item)
        current[:] = normalized
        record["updated_at"] = timestamp(); db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write_preserving_context(db, "bug", record_id); event("bug_evidence_updated", record, db["revision"])
        return {"revision": db["revision"], "record": record}
    return with_lock(update)


def read_record_attachment(record_id: str, filename: str, byte_range: tuple[int, int] | None = None) -> tuple[bytes, str, dict[str, Any]]:
    return with_lock(lambda: _read_record_attachment_locked(record_id, filename, byte_range))


def attachment_size(record_id: str, filename: str) -> int:
    def lookup():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", filename): raise ValueError("record_attachment_not_found")
        record = _find_record(load_store(), record_id)
        kind = record.get("type")
        if kind not in VALID_TYPES: raise ValueError("record_attachment_not_found")
        entry = next((item for key in ("attachments", "source_attachments") for item in record.get(key, []) if item.get("filename") == filename), None)
        if not isinstance(entry, dict): raise ValueError("record_attachment_not_found")
        root = (ROOT / "records" / kind / record_id).resolve(); candidate = root / "attachments" / filename
        if candidate.is_symlink() or root not in candidate.resolve().parents or not candidate.is_file(): raise ValueError("record_attachment_not_found")
        return candidate.stat().st_size
    return with_lock(lookup)


def _read_record_attachment_locked(record_id: str, filename: str, byte_range: tuple[int, int] | None = None) -> tuple[bytes, str, dict[str, Any]]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", filename): raise ValueError("record_attachment_not_found")
    record = _find_record(load_store(), record_id)
    kind = record.get("type")
    if kind not in VALID_TYPES: raise ValueError("record_attachment_not_found")
    entry = next((item for key in ("attachments", "source_attachments") for item in record.get(key, []) if item.get("filename") == filename), None)
    if not isinstance(entry, dict): raise ValueError("record_attachment_not_found")
    root = (ROOT / "records" / kind / record_id).resolve(); candidate = root / "attachments" / filename
    if candidate.is_symlink(): raise ValueError("record_attachment_not_found")
    path = candidate.resolve()
    if root not in path.parents or not path.is_file(): raise ValueError("record_attachment_not_found")
    size = path.stat().st_size
    if size != entry.get("size_bytes") or size > MAX_SOURCE_ATTACHMENT_BYTES: raise ValueError("record_attachment_integrity_error")
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""): hasher.update(chunk)
    if hasher.hexdigest() != entry.get("sha256"): raise ValueError("record_attachment_integrity_error")
    mime = _source_attachment_mime(filename, path.read_bytes() if size <= MAX_RECORD_ATTACHMENT_BYTES else _read_prefix(path, 64))
    if mime != entry.get("mime_type"): raise ValueError("record_attachment_integrity_error")
    start, end = byte_range if byte_range else (0, size - 1)
    if start < 0 or end < start or end >= size: raise ValueError("invalid_range")
    with path.open("rb") as source:
        source.seek(start); body = source.read(end - start + 1)
    return body, mime, {"size": size, "start": start, "end": end, "sha256": hasher.hexdigest()}


def _read_prefix(path: Path, size: int) -> bytes:
    with path.open("rb") as source: return source.read(size)


def _read_record_image_locked(record_id: str, filename: str) -> tuple[bytes, str]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", filename): raise ValueError("record_attachment_not_found")
    record = _find_record(load_store(), record_id)
    kind = record.get("type")
    if kind not in VALID_TYPES: raise ValueError("record_attachment_not_found")
    entry = next((item for item in record.get("attachments", []) if item.get("filename") == filename), None)
    if not isinstance(entry, dict): raise ValueError("record_attachment_not_found")
    root = (ROOT / "records" / kind / record_id).resolve()
    candidate = root / "attachments" / filename
    if candidate.is_symlink(): raise ValueError("record_attachment_not_found")
    path = candidate.resolve()
    if root not in path.parents or not path.is_file(): raise ValueError("record_attachment_not_found")
    body = path.read_bytes()
    mime = _image_mime(filename, body)
    if len(body) != entry.get("size_bytes") or hashlib.sha256(body).hexdigest() != entry.get("sha256") or mime != entry.get("mime_type"):
        raise ValueError("record_attachment_integrity_error")
    return body, mime


def with_lock(fn):
    ROOT.mkdir(parents=True, exist_ok=True)
    depth = getattr(_LOCK_LOCAL, "depth", 0)
    if depth:
        _LOCK_LOCAL.depth = depth + 1
        try: return fn()
        finally: _LOCK_LOCAL.depth -= 1
    with LOCK.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _LOCK_LOCAL.depth = 1
        try: return fn()
        finally:
            _LOCK_LOCAL.depth = 0
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def cmd_upsert(args: argparse.Namespace) -> None:
    raw = json.load(sys.stdin) if args.input == "-" else json.loads(Path(args.input).read_text(encoding="utf-8"))
    supplied_fields = set(raw) if isinstance(raw, dict) else set()
    if isinstance(raw, dict) and not raw.get("id") and raw.get("type") in {"task", "work"}:
        raw["id"] = f"ZT-T-{uuid.uuid4().hex[:12].upper()}" if raw["type"] == "task" else f"ZT-W-{uuid.uuid4().hex[:12].upper()}"
    normalized = normalize_record(raw)

    def update() -> dict[str, Any]:
        db = load_store()
        expected_store_revision = getattr(args, "expected_store_revision", None)
        if expected_store_revision is not None and expected_store_revision != int(db.get("revision", 0)):
            raise ValueError("store_revision_conflict")
        records = db["records"]
        old = next((r for r in records if r.get("id") == normalized["id"]), None)
        if old:
            merged = dict(old)
            was_terminal = _is_batch_terminal(old)
            previous_status = old.get("status")
            for key in supplied_fields:
                merged[key] = normalized[key]
            merged["updated_at"] = normalized["updated_at"]
            if "history" not in supplied_fields:
                merged["history"] = list(old.get("history", []))
                if previous_status != merged.get("status"):
                    merged["history"].append({"at": merged["updated_at"], "from_status": previous_status,
                                               "to_status": merged.get("status"), "status": merged.get("status"),
                                               "note": "通过 dashboardctl 更新"})
            if (merged.get("type") == "task" and old.get("manual_status") == "completed"
                    and "status" in supplied_fields and merged.get("status") not in BATCH_TERMINAL_STATUSES):
                merged["manual_status"] = None
                merged["manual_completed_at"] = None
                merged["manual_completed_by"] = None
                merged.setdefault("history", []).append({
                    "at": merged["updated_at"], "status": merged.get("status"),
                    "note": "任务显式重开，清除手动完成覆盖；此前手动完成历史保留",
                })
            if merged.get("type") == "task" and was_terminal and not _is_batch_terminal(merged):
                _assign_task_to_current_batch(db, merged, merged["updated_at"])
            workflow.normalize_record_fields(merged)
            normalized_local = merged
            records[records.index(old)] = normalized_local
            action = "upsert"
        else:
            if normalized.get("type") == "task":
                _assign_task_to_current_batch(db, normalized, normalized["updated_at"])
                normalized.setdefault("initial_brief", normalized.get("status_detail") or normalized.get("title", ""))
            records.append(normalized)
            normalized_local = normalized
            action = "create"
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event(action, normalized_local, db["revision"])
        return {"revision": db["revision"], "record": normalized_local}

    print(json.dumps(with_lock(update), ensure_ascii=False, indent=2))


def update_handoff(record_id: str, expected_revision: int, patch: Any) -> dict[str, Any]:
    """Update only handoff facts under record CAS without changing status or lease."""
    if not isinstance(patch, dict) or "updated_at" in patch:
        raise ValueError("invalid_handoff_patch")
    def update() -> dict[str, Any]:
        db = load_store(); record = _find_record(db, record_id)
        if expected_revision != int(record.get("revision", 0)): raise ValueError("revision_conflict")
        handoff = dict(record.get("handoff") or {})
        handoff.update(patch); handoff["updated_at"] = timestamp()
        normalize_record({"id": record["id"], "type": record["type"], "title": record["title"],
                          "status": record["status"], "handoff": handoff})
        record["handoff"] = handoff
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write_preserving_context(db, record["type"], record["id"])
        event("handoff_update", record, db["revision"])
        return _agent_response(record, db)
    return with_lock(update)


def cmd_handoff_update(args: argparse.Namespace) -> None:
    patch = json.load(sys.stdin) if args.input == "-" else json.loads(Path(args.input).read_text(encoding="utf-8"))
    print(json.dumps(update_handoff(args.id, args.expected_revision, patch), ensure_ascii=False, indent=2))


def add_record_attachment(record_id: str, filename: str, data: bytes, summary: str | None = None, actor: str = "agent") -> dict[str, Any]:
    from pathlib import PurePath
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", filename):
        raise ValueError("invalid_record_attachment_name")
    extension = PurePath(filename).suffix.lower()
    if extension not in SOURCE_ATTACHMENT_MIMES:
        raise ValueError("unsupported_attachment_file_type")
    if not data or len(data) > MAX_SOURCE_ATTACHMENT_BYTES:
        raise ValueError("attachment_file_size_invalid")

    mime = SOURCE_ATTACHMENT_MIMES[extension]
    digest = hashlib.sha256(data).hexdigest()

    def update() -> dict[str, Any]:
        db = load_store()
        record = _find_record(db, record_id)
        kind = record.get("type")
        if kind not in VALID_TYPES:
            raise ValueError("invalid_record_type")
        raw_base = ROOT / "records" / kind / record_id
        base = raw_base.resolve()
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(base, 0o700)
        folder = base / "attachments"
        folder.mkdir(mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
        path = folder / filename

        current = record.setdefault("attachments", [])
        same = next((item for item in current if item.get("filename") == filename and item.get("sha256") == digest), None)
        if same:
            if summary and same.get("summary") != summary:
                same["summary"] = summary
                record["updated_at"] = timestamp()
                db["revision"] = int(db.get("revision", 0)) + 1
                atomic_write_preserving_context(db, kind, record_id)
                event("record_attachment_updated", record, db["revision"])
            return {"revision": db["revision"], "attachment": same, "deduplicated": True}

        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        item = {
            "id": f"att-{uuid.uuid4().hex[:8]}",
            "filename": filename,
            "mime_type": mime,
            "size_bytes": len(data),
            "sha256": digest,
            "summary": summary or "",
            "download_url": f"/api/items/{record_id}/attachments/{filename}",
            "uploaded_at": timestamp(),
            "uploader": actor,
        }
        current.append(item)
        record["updated_at"] = timestamp()
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write_preserving_context(db, kind, record_id)
        event("record_attachment_added", record, db["revision"])
        return {"revision": db["revision"], "attachment": item, "deduplicated": False}

    return with_lock(update)


def reopen_record(record_id: str, reason: str, *, audit_mode: str = "lightweight",
                  actor: str = "agent", actor_role: str = "lead",
                  evidence: list[str] | None = None, attachments: list[dict[str, Any]] | None = None,
                  expected_store_revision: int | None = None) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reopen_reason_required")
    audit_mode = audit_mode if audit_mode in {"lightweight", "full"} else "lightweight"

    def update() -> dict[str, Any]:
        db = load_store()
        if expected_store_revision is not None and expected_store_revision != int(db.get("revision", 0)):
            raise ValueError("store_revision_conflict")
        record = _find_record(db, record_id)
        if record.get("iteration_id"):
            it = _find_iteration(db, record["iteration_id"])
            if it and it.get("status") == "released":
                raise ValueError("cannot_reopen_released_iteration_record")

        now = timestamp()
        reopen_count = int(record.get("reopen_count") or 0) + 1
        record["reopen_count"] = reopen_count
        history_entry = {
            "round": reopen_count,
            "at": now,
            "actor": actor,
            "actor_role": actor_role,
            "reason": reason.strip(),
            "audit_mode": audit_mode,
            "evidence": list(evidence or []),
            "attachments": list(attachments or []),
        }
        record.setdefault("reopen_history", []).append(history_entry)

        workflow.reopen(record, actor_role=actor_role, actor=actor, reason=reason,
                        audit_mode=audit_mode, evidence=evidence, now=now)

        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write_preserving_context(db, record["type"], record_id)
        event("reopen", record, db["revision"])
        return {"revision": db["revision"], "record": record}

    return with_lock(update)


def _iteration_store_list(db: dict[str, Any]) -> list[dict[str, Any]]:
    iterations = db.setdefault("iterations", [])
    if not isinstance(iterations, list):
        iterations = []
        db["iterations"] = iterations
    return iterations


def _find_iteration(db: dict[str, Any], iteration_id: str) -> dict[str, Any] | None:
    for it in _iteration_store_list(db):
        if it.get("id") == iteration_id or it.get("version_tag") == iteration_id:
            return it
    return None


def _iteration_order_value(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _iteration_sort_key(it: dict[str, Any]) -> tuple[int, int, str, str]:
    """Stable display order: explicit `order` first, then creation time."""
    order = _iteration_order_value(it.get("order"))
    return (0 if order is not None else 1, order if order is not None else 0,
            str(it.get("created_at") or ""), str(it.get("id") or ""))


def ordered_iterations(iterations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ascending persistence order: smaller `order` is older."""
    return sorted((it for it in iterations if isinstance(it, dict)), key=_iteration_sort_key)


def display_iterations(iterations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest first for UI/API while keeping `order` ascending on disk."""
    ordered = ordered_iterations(iterations)
    explicit = [it for it in ordered if _iteration_order_value(it.get("order")) is not None]
    missing = [it for it in ordered if _iteration_order_value(it.get("order")) is None]
    return list(reversed(explicit)) + list(reversed(missing))


def _normalize_iteration_orders(iterations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist a sequential 0..n-1 order so insertion and reordering are stable."""
    ordered = ordered_iterations(iterations)
    for index, it in enumerate(ordered):
        it["order"] = index
    return ordered


def list_iterations() -> dict[str, Any]:
    db = load_store()
    iterations = _iteration_store_list(db)
    current_id = db.get("current_iteration_id")
    return {
        "revision": db.get("revision", 0),
        "current_iteration_id": current_id,
        "iterations": copy.deepcopy(display_iterations(iterations))
    }


def create_iteration(version_tag: str, title: str, *, status: str = "active", target_release_date: str | None = None,
                     description: str = "", notes: list[str] | None = None, iteration_id: str | None = None,
                     order: int | None = None) -> dict[str, Any]:
    if not isinstance(version_tag, str) or not version_tag.strip() or len(version_tag) > MAX_ITERATION_TAG_CHARS:
        raise ValueError("invalid_iteration_version_tag")
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_ITERATION_TITLE_CHARS:
        raise ValueError("invalid_iteration_title")
    if status not in ITERATION_STATUSES:
        raise ValueError("invalid_iteration_status")
    if order is not None and (isinstance(order, bool) or not isinstance(order, int) or order < 0):
        raise ValueError("invalid_iteration_order")

    clean_tag = version_tag.strip()
    clean_title = title.strip()
    if iteration_id is None:
        it_id = f"ITER-{uuid.uuid4().hex[:8]}"
    else:
        if not isinstance(iteration_id, str) or not ITERATION_ID_RE.fullmatch(iteration_id.strip()):
            raise ValueError("invalid_iteration_id")
        it_id = iteration_id.strip()

    def update() -> dict[str, Any]:
        db = load_store()
        iterations = _iteration_store_list(db)
        if any(it.get("id") == it_id or it.get("version_tag") == clean_tag
               or str(it.get("title") or "").strip() == clean_title for it in iterations):
            raise ValueError("iteration_duplicate")
        now = timestamp()
        entry = {
            "id": it_id,
            "version_tag": clean_tag,
            "title": clean_title,
            "status": status,
            "target_release_date": target_release_date,
            "released_at": now if status == "released" else None,
            "created_at": now,
            "updated_at": now,
            "description": str(description or ""),
            "notes": list(notes or []),
        }
        ordered = _normalize_iteration_orders(iterations)
        position = len(ordered) if order is None else min(order, len(ordered))
        ordered.insert(position, entry)
        for index, item in enumerate(ordered):
            item["order"] = index
        iterations.append(entry)
        if not db.get("current_iteration_id") or status == "active":
            db["current_iteration_id"] = it_id
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("iteration_created", {"id": it_id, "type": "iteration"}, db["revision"])
        return {"revision": db["revision"], "iteration": entry}

    return with_lock(update)


def reorder_iterations(iteration_ids: list[str]) -> dict[str, Any]:
    if (not isinstance(iteration_ids, list) or not iteration_ids
            or any(not isinstance(item, str) or not item for item in iteration_ids)
            or len(set(iteration_ids)) != len(iteration_ids)):
        raise ValueError("invalid_iteration_order")

    def update() -> dict[str, Any]:
        db = load_store()
        iterations = _iteration_store_list(db)
        by_id = {it.get("id"): it for it in iterations}
        if set(iteration_ids) != set(by_id):
            raise ValueError("iteration_order_mismatch")
        now = timestamp()
        # The API/UI passes display order (newest first); `order` stays
        # ascending on disk so larger always means newer.
        count = len(iteration_ids)
        for index, it_id in enumerate(iteration_ids):
            by_id[it_id]["order"] = count - 1 - index
            by_id[it_id]["updated_at"] = now
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("iterations_reordered", {"id": "iterations", "type": "iteration"}, db["revision"])
        return {"revision": db["revision"], "iterations": copy.deepcopy(display_iterations(iterations))}

    return with_lock(update)


def update_iteration(iteration_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    def update() -> dict[str, Any]:
        db = load_store()
        it = _find_iteration(db, iteration_id)
        if not it:
            raise ValueError("iteration_not_found")
        now = timestamp()
        if "version_tag" in patch and patch["version_tag"] is not None:
            new_tag = str(patch["version_tag"]).strip()
            if not new_tag or len(new_tag) > MAX_ITERATION_TAG_CHARS:
                raise ValueError("invalid_iteration_version_tag")
            duplicate = next((other for other in _iteration_store_list(db)
                              if other is not it and other.get("version_tag") == new_tag), None)
            if duplicate:
                raise ValueError("iteration_duplicate")
        if "title" in patch and patch["title"] is not None:
            if not isinstance(patch["title"], str) or not patch["title"].strip() or len(patch["title"].strip()) > MAX_ITERATION_TITLE_CHARS:
                raise ValueError("invalid_iteration_title")
            duplicate_title = next((other for other in _iteration_store_list(db)
                                    if other is not it and str(other.get("title") or "").strip() == patch["title"].strip()), None)
            if duplicate_title:
                raise ValueError("iteration_duplicate")
        for key in ("title", "version_tag", "description", "target_release_date", "notes"):
            if key in patch and patch[key] is not None:
                it[key] = patch[key].strip() if key in {"title", "version_tag"} else patch[key]
        if "status" in patch:
            new_status = patch["status"]
            if new_status not in ITERATION_STATUSES:
                raise ValueError("invalid_iteration_status")
            it["status"] = new_status
            if new_status == "released" and not it.get("released_at"):
                it["released_at"] = now
            elif new_status != "released":
                it["released_at"] = None
        it["updated_at"] = now
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("iteration_updated", {"id": it["id"], "type": "iteration"}, db["revision"])
        return {"revision": db["revision"], "iteration": it}

    return with_lock(update)


def release_iteration(iteration_id: str, *, notes: list[str] | None = None) -> dict[str, Any]:
    def update() -> dict[str, Any]:
        db = load_store()
        it = _find_iteration(db, iteration_id)
        if not it:
            raise ValueError("iteration_not_found")
        now = timestamp()
        it["status"] = "released"
        it["released_at"] = now
        it["updated_at"] = now
        if notes:
            it.setdefault("notes", []).extend(notes)
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("iteration_released", {"id": it["id"], "type": "iteration"}, db["revision"])
        return {"revision": db["revision"], "iteration": it}

    return with_lock(update)


def set_current_iteration(iteration_id: str) -> dict[str, Any]:
    def update() -> dict[str, Any]:
        db = load_store()
        it = _find_iteration(db, iteration_id)
        if not it:
            raise ValueError("iteration_not_found")
        db["current_iteration_id"] = it["id"]
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("current_iteration_switched", {"id": it["id"], "type": "iteration"}, db["revision"])
        return {"revision": db["revision"], "current_iteration_id": it["id"]}

    return with_lock(update)


def assign_record_iteration(record_id: str, iteration_id: str | None) -> dict[str, Any]:
    if not isinstance(record_id, str) or not record_id.strip():
        raise ValueError("invalid_record_id")
    if iteration_id is not None and (not isinstance(iteration_id, str) or not iteration_id.strip()):
        raise ValueError("invalid_iteration_id")

    def update() -> dict[str, Any]:
        db = load_store()
        rec = next((r for r in db.get("records", []) if r.get("id") == record_id), None)
        if rec is None:
            raise ValueError("record_not_found")
        if iteration_id is None:
            rec["iteration_id"] = None
        else:
            it = _find_iteration(db, iteration_id.strip())
            if it is None:
                raise ValueError("iteration_not_found")
            rec["iteration_id"] = it["id"]
        rec["updated_at"] = timestamp()
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("record_iteration_assigned", {"id": record_id, "type": rec.get("type", "task"), "iteration_id": rec["iteration_id"]}, db["revision"])
        return {"revision": db["revision"], "record": rec}

    return with_lock(update)


def migrate_iteration_archive(*, default_tag: str = "v13.4.2", default_title: str = "13.4.2 智能相册与AI音乐优化") -> dict[str, Any]:
    def update() -> dict[str, Any]:
        db = load_store()
        iterations = _iteration_store_list(db)
        default_it = next((it for it in iterations if it.get("status") in {"active", "pending_release"}), None)
        now = timestamp()
        created_default = False
        if not default_it:
            default_it = {
                "id": "ITER-v1342",
                "version_tag": default_tag,
                "title": default_title,
                "status": "active",
                "target_release_date": None,
                "released_at": None,
                "created_at": now,
                "updated_at": now,
                "description": "初始迁移默认迭代",
                "notes": []
            }
            iterations.append(default_it)
            db["current_iteration_id"] = default_it["id"]
            created_default = True

        default_id = default_it["id"]
        records_migrated = 0
        for r in db.get("records", []):
            if r.get("iteration_id") is None:
                r["iteration_id"] = default_id
                records_migrated += 1

        sources_migrated = 0
        for s in _source_store_list(db):
            if s.get("scope") is None:
                slot = s.get("slot")
                if slot in {"api_doc", "architecture"}:
                    s["scope"] = "global"
                else:
                    s["scope"] = "iteration"
                    s["iteration_id"] = default_id
                sources_migrated += 1

        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("iteration_archive_migrated", {
            "id": default_id,
            "type": "iteration",
            "default_iteration_id": default_id,
            "created_default": created_default,
            "records_migrated": records_migrated,
            "sources_migrated": sources_migrated
        }, db["revision"])
        return {
            "revision": db["revision"],
            "default_iteration": default_it,
            "records_migrated": records_migrated,
            "sources_migrated": sources_migrated
        }

    return with_lock(update)


def assemble_archive_context(iteration_id: str | None = None, task_id: str | None = None) -> dict[str, Any]:
    db = load_store()
    all_sources = enrich_source_records(_source_store_list(db))

    task_record = None
    if task_id is not None:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("invalid_record_id")
        task_record = next((r for r in db.get("records", []) if r.get("id") == task_id), None)
        if task_record is None:
            raise ValueError("record_not_found")

    target_iteration = None
    if iteration_id is not None:
        if not isinstance(iteration_id, str) or not iteration_id.strip():
            raise ValueError("invalid_iteration_id")
        target_iteration = _find_iteration(db, iteration_id.strip())
        if not target_iteration:
            raise ValueError("iteration_not_found")
    elif task_record is not None:
        # An explicit task fixes the context. If it has no version we stay on
        # public material; never silently fall back to current_iteration_id.
        if task_record.get("iteration_id"):
            target_iteration = _find_iteration(db, task_record["iteration_id"])
            if not target_iteration:
                raise ValueError("iteration_not_found")
    elif db.get("current_iteration_id"):
        target_iteration = _find_iteration(db, db["current_iteration_id"])

    target_iter_id = target_iteration.get("id") if target_iteration else None

    global_materials = [s for s in all_sources if s.get("scope") == "global" and s.get("active")]
    iteration_materials = [s for s in all_sources if s.get("scope") == "iteration" and s.get("iteration_id") == target_iter_id and s.get("active")] if target_iter_id else []

    lines = [f"# 研发工作上下文基线（版本：{target_iteration.get('title') if target_iteration else '全局公共'} · 内部ID: {target_iter_id or 'GLOBAL'}）", ""]
    if target_iteration:
        lines.extend([
            f"## 目标版本：{target_iteration.get('title')}",
            f"- **版本标识（Tag）**：{target_iteration.get('version_tag')}",
            f"- **内部版本ID**：{target_iteration.get('id')}",
            f"- **版本状态**：{target_iteration.get('status')}",
            f"- **目标发布时间**：{target_iteration.get('target_release_date') or '未设定'}",
            f"- **版本描述**：{target_iteration.get('description') or '无'}",
            ""
        ])
    lines.extend(["## 全局公共材料（跨版本共享）", ""])
    if global_materials:
        for item in global_materials:
            slot_name = SOURCE_SLOT_LABELS.get(item.get("slot"), item.get("slot") or "资料")
            lines.append(f"- **[{slot_name}]** {item.get('name')} ({item.get('url') or item.get('filename')}) [版本: {item.get('version_label', 'v1')}]")
            for att in item.get("attachments", []):
                lines.append(f"  - 补充材料: {att.get('summary') or att.get('filename')} ({att.get('download_url')})")
    else:
        lines.append("- （暂无全局公共材料）")
    lines.append("")

    lines.extend([f"## 本版本专属材料（{target_iteration.get('title') if target_iteration else '专属'}）", ""])
    if iteration_materials:
        # Prioritize fixed slots: requirement_doc, figma, prototype, testcase, then supplementary
        fixed_order = {"requirement_doc": 0, "figma": 1, "codesign": 2, "prototype": 2, "testcase": 3, "supplementary": 4}
        sorted_iter = sorted(iteration_materials, key=lambda s: fixed_order.get(s.get("slot"), 10))
        for item in sorted_iter:
            slot_name = SOURCE_SLOT_LABELS.get(item.get("slot"), item.get("slot") or "资料")
            lines.append(f"- **[{slot_name}]** {item.get('name')} ({item.get('url') or item.get('filename')}) [版本: {item.get('version_label', 'v1')}]")
            for att in item.get("attachments", []):
                lines.append(f"  - 补充材料: {att.get('summary') or att.get('filename')} ({att.get('download_url')})")
    else:
        lines.append("- （本版本暂无专属材料）")
    lines.append("")

    if task_record:
        lines.extend([
            f"## 当前任务详情：{task_record.get('title')} ({task_record.get('id')})",
            f"- **当前状态**：{task_record.get('status')}",
            f"- **当前简报**：{task_record.get('latest_brief') or task_record.get('initial_brief') or '无'}",
            f"- **修改轮次**：第 {task_record.get('reopen_count', 0)} 轮",
        ])
        if task_record.get("reopen_history"):
            lines.append("### 变更历史与依据")
            for h in task_record["reopen_history"]:
                lines.append(f"- 第 {h.get('round')} 轮 [{h.get('at')}]: {h.get('reason')} (模式: {h.get('audit_mode')})")
                for att in h.get("attachments", []):
                    lines.append(f"  - 补充材料: {att.get('summary') or att.get('filename')}")
        lines.append("")

    return {
        "iteration": target_iteration,
        "global_materials": global_materials,
        "iteration_materials": iteration_materials,
        "task": task_record,
        "summary_markdown": "\n".join(lines)
    }

# ---------------------------------------------------------------------------
# six-stage workflow pipeline (contract: docs/product/workflow-pipeline.md)


def workflow_templates() -> dict[str, Any]:
    return {"templates": workflow.templates()}


def workflow_show(record_id: str) -> dict[str, Any]:
    """Read-only workflow detail; old records without a pipeline stay valid."""
    db = load_store()
    record = _find_record(db, record_id)
    current = record.get("workflow") if isinstance(record.get("workflow"), dict) else None
    template = None
    if current:
        try:
            template = workflow.get_template(current.get("template_id"))
        except workflow.WorkflowError:
            template = None
    return {"record_revision": int(record.get("revision", 0)), "store_revision": int(db.get("revision", 0)),
            "workflow": current, "template": template}


DISPATCH_INSTRUCTION = (
    "派发前先读本卡，并加载知途工作台自带的 workflow 契约（waylume-assistant skill）。"
    " 按 card.next 的 action/role 派发；具体 Agent 身份、通讯通道和工具权限由宿主平台决定，工作台不硬编码宿主调度方式。"
    " 审计报告只回原开发 Agent，仅供开发参考；是否返工由开发判断。"
    " 交付阶段只能由用户手动交付，Lead 不得 close/push/release。"
    " 宿主通道失败时停止并报告，不得改用绕过工作台流程的人工入口。"
    " 代码任务派发前必须读取 parallel-git-worktree 协议，并在 worker prompt/context 中包含 "
    "record_id、branch、base、worktree、owner、build_owner；缺失时先分配，不得让 worker 进入主 checkout。"
)


def _valid_dispatch_role(role: Any) -> str:
    if not isinstance(role, str) or role not in {"dev", "auditor", "pm"}:
        raise ValueError("invalid_agent_role")
    return role


def bind_agent(record_id: str, role: Any, actor: Any, *, session_id: Any = None,
               channel: Any = None, expected_revision: int | None = None) -> dict[str, Any]:
    """Bind one task role to its fixed agent session. This is task metadata, not a workflow gate."""
    role = _valid_dispatch_role(role)
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 100:
        raise ValueError("invalid_agent_actor")
    if session_id is not None and (not isinstance(session_id, str) or len(session_id) > 200):
        raise ValueError("invalid_agent_session_id")
    if channel is not None and (not isinstance(channel, str) or not channel.strip() or len(channel) > 50):
        raise ValueError("invalid_agent_channel")
    def update() -> dict[str, Any]:
        db = load_store()
        record = _find_record(db, record_id)
        current = int(record.get("revision", 0))
        expected = current if expected_revision is None else expected_revision
        if expected != current:
            raise ValueError("revision_conflict")
        bindings = dict(record.get("agent_bindings") or {})
        bindings[role] = {"actor": actor.strip(), "session_id": session_id,
                          "channel": channel, "bound_at": timestamp()}
        record["agent_bindings"] = bindings
        record["updated_at"] = timestamp()
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("agent_binding_updated", record, db["revision"])
        return {"revision": int(record.get("revision", 0)), "store_revision": db["revision"],
                "record": record, "binding": bindings[role]}
    return with_lock(update)


def dispatch_card(record_id: str) -> dict[str, Any]:
    """Build the pre-dispatch card the Lead must read before delegating any role."""
    db = load_store()
    record = _find_record(db, record_id)
    current = record.get("workflow") if isinstance(record.get("workflow"), dict) else None
    bindings = record.get("agent_bindings") if isinstance(record.get("agent_bindings"), dict) else {}
    card: dict[str, Any] = {
        "record_id": record.get("id"), "record_title": record.get("title"),
        "record_type": record.get("type"), "record_status": record.get("status"),
        "status_detail": record.get("status_detail", ""),
        "workflow_started": bool(current),
        "stage": None, "stage_status": None, "substage": None,
        "stage_label": None, "stage_short": None,
        "required_outputs": [], "missing_outputs": [],
        "next": None, "lock": None, "bindings": bindings,
        "delivery_manual": True,
        "instruction": DISPATCH_INSTRUCTION,
        "workflow_contract": "waylume-assistant/references/workflow.md",
        "host_adapter_required": True,
        "parallel_git": {
            "required": True,
            "protocol": "skills/mcloud-workflow/references/parallel-git-worktree.md",
            "required_context": ["record_id", "branch", "base", "worktree", "owner", "build_owner"],
            "rule": "主 checkout 只做集成；每个需求/BUG 一个 local 分支 + 一个独立 worktree；"
                    "一个 worktree 只给一个代码 writer；共享文件/构建/设备由 Lead 串行。",
        },
    }
    notices: list[str] = []
    if not current:
        card["next"] = {"action": "start", "role": None, "roles": ["lead"], "reason": "事项尚未启动流水线"}
        card["entry_note"] = "旧事项/未启动：入口阶段由 Lead 按当前状态判断，不得凭记忆；建议先记录入口依据。"
        notices.append("未启动流水线，先确认入口阶段，再 start。")
        for role in ("dev", "auditor", "pm"):
            if role not in bindings:
                notices.append(f"未绑定 {role} 固定会话；派发前需 bind-agent 或明确报告。")
        card["notices"] = notices
        return card
    template = workflow.get_template(current.get("template_id"))
    stage, _ = workflow.stage_definition(template, current.get("stage"))
    gate = (current.get("gates") or {}).get(stage.get("id"), {})
    outputs = gate.get("outputs") if isinstance(gate.get("outputs"), dict) else {}
    substage = current.get("substage")
    stage_required = list(stage.get("required_outputs") or [])
    substeps = stage.get("substeps") or []
    pending_substep = None
    for item in substeps:
        required = list(item.get("outputs") or ([item.get("output")] if item.get("output") else []))
        if any(key not in outputs for key in required):
            pending_substep = item
            break
    missing = [key for key in stage_required if key not in outputs]
    card.update({
        "stage": stage.get("id"), "stage_status": current.get("stage_status"),
        "substage": substage, "stage_label": stage.get("label"), "stage_short": stage.get("short"),
        "required_outputs": stage_required, "missing_outputs": missing,
        "template_id": current.get("template_id"), "template_version": current.get("template_version"),
        "workflow": {"started_at": current.get("started_at"), "updated_at": current.get("updated_at"),
                     "rework_count": current.get("rework_count", 0)},
    })
    lock = current.get("lock") if isinstance(current.get("lock"), dict) else None
    card["lock"] = lock
    def role_channel(role: str | None) -> str | None:
        if not role:
            return None
        binding = bindings.get(role) if isinstance(bindings.get(role), dict) else None
        return (binding or {}).get("channel")
    if current.get("stage_status") == "blocked":
        card["next"] = {"action": "unblock", "role": "lead", "roles": ["lead"],
                        "reason": "流水线处于阻塞状态，先处理阻塞/授权后再继续"}
        notices.append("当前 stage_status=blocked，不得跳过。")
    elif lock:
        card["next"] = {"action": "wait_lock", "role": lock.get("role"), "actor": lock.get("actor"),
                        "roles": [lock.get("role")] if lock.get("role") else [],
                        "reason": "当前阶段已有锁，等待/续租或释放后再派发"}
        notices.append("阶段锁存在，禁止另开写者抢写。")
    elif stage.get("id") == "delivery":
        card["next"] = {"action": "manual_delivery", "role": None, "roles": ["lead"],
                        "reason": "已到交付阶段，交付/发布由用户手动执行；Lead 只能收证据"}
        notices.append("禁止 close/push/release；等待用户手动交付。")
    elif pending_substep is not None:
        role = pending_substep.get("role")
        card["next"] = {"action": "dispatch", "role": role,
                        "roles": [role] if role else [],
                        "substage": pending_substep.get("id"),
                        "substage_label": pending_substep.get("label"),
                        "required_outputs": list(pending_substep.get("outputs") or ([pending_substep.get("output")] if pending_substep.get("output") else [])),
                        "missing_outputs": [key for key in (pending_substep.get("outputs") or ([pending_substep.get("output")] if pending_substep.get("output") else [])) if key not in outputs],
                        "channel": role_channel(role),
                        "reason": f"当前子步骤 {pending_substep.get('id')} 未完成"}
    elif missing:
        role = (stage.get("submit_roles") or [None])[0]
        card["next"] = {"action": "dispatch", "role": role,
                        "roles": list(stage.get("submit_roles") or []),
                        "channel": role_channel(role),
                        "missing_outputs": missing,
                        "reason": f"当前阶段缺少 {', '.join(missing)}"}
    else:
        card["next"] = {"action": "advance", "role": (stage.get("advance_roles") or [None])[0],
                        "roles": list(stage.get("advance_roles") or []),
                        "channel": role_channel((stage.get("advance_roles") or [None])[0]),
                        "reason": "当前阶段 required outputs 已完成"}
    for role in ("dev", "auditor", "pm"):
        if role not in bindings:
            notices.append(f"未绑定 {role} 固定会话；派发前需 bind-agent 或明确报告。")
    if bindings:
        notices.append("派发通道由宿主平台解释；绑定里的 channel 只是宿主自定的不透明标签。")
    if stage.get("id") == "audit_response" and pending_substep and pending_substep.get("id") == "audit":
        notices.append("先派独立审计 Agent，不得先让开发走 dev_response。")
    card["notices"] = notices
    return card


def _workflow_write(record_id: str, action: str, expected_revision: int | None, operation) -> dict[str, Any]:
    def update() -> dict[str, Any]:
        db = load_store()
        record = _find_record(db, record_id)
        current = int(record.get("revision", 0))
        expected = current if expected_revision is None else expected_revision
        if not isinstance(expected, int) or isinstance(expected, bool):
            raise ValueError("invalid_expected_revision")
        if expected != current:
            raise workflow.WorkflowError("revision_conflict", f"revision 冲突：期望 {expected}，当前 {current}")
        operation(record)
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("workflow_" + action, record, db["revision"])
        current = record.get("workflow") if isinstance(record.get("workflow"), dict) else None
        template = None
        if current:
            try:
                template = workflow.get_template(current.get("template_id"))
            except workflow.WorkflowError:
                template = None
        return {"revision": int(record.get("revision", 0)), "store_revision": int(db.get("revision", 0)),
                "record": record, "workflow": current, "template": template}
    return with_lock(update)


def workflow_start(record_id: str, *, template_id: str | None = None, stage: str | None = None,
                   actor_role: str, actor: str, note: str = "", evidence: list[str] | None = None,
                   expected_revision: int | None = None) -> dict[str, Any]:
    return _workflow_write(record_id, "start", expected_revision, lambda record: workflow.start(
        record, template_id=template_id, stage=stage, actor_role=actor_role, actor=actor,
        note=note, evidence=evidence))


def workflow_submit(record_id: str, outputs: dict[str, Any], *, actor_role: str, actor: str,
                    note: str = "", evidence: list[str] | None = None,
                    expected_revision: int | None = None) -> dict[str, Any]:
    return _workflow_write(record_id, "submit", expected_revision, lambda record: workflow.submit(
        record, outputs, actor_role=actor_role, actor=actor, note=note, evidence=evidence))


def workflow_advance(record_id: str, *, actor_role: str, actor: str, note: str = "",
                     evidence: list[str] | None = None, to: str | None = None,
                     expected_revision: int | None = None) -> dict[str, Any]:
    return _workflow_write(record_id, "advance", expected_revision, lambda record: workflow.advance(
        record, actor_role=actor_role, actor=actor, note=note, evidence=evidence, to=to))


def workflow_rework(record_id: str, to: str, *, actor_role: str, actor: str, note: str = "",
                    evidence: list[str] | None = None,
                    expected_revision: int | None = None) -> dict[str, Any]:
    return _workflow_write(record_id, "rework", expected_revision, lambda record: workflow.rework(
        record, to, actor_role=actor_role, actor=actor, note=note, evidence=evidence))


def workflow_block(record_id: str, *, actor_role: str, actor: str, note: str = "",
                   evidence: list[str] | None = None,
                   expected_revision: int | None = None) -> dict[str, Any]:
    return _workflow_write(record_id, "block", expected_revision, lambda record: workflow.block(
        record, actor_role=actor_role, actor=actor, note=note))


def workflow_unblock(record_id: str, *, actor_role: str, actor: str, note: str = "",
                     evidence: list[str] | None = None,
                     expected_revision: int | None = None) -> dict[str, Any]:
    return _workflow_write(record_id, "unblock", expected_revision, lambda record: workflow.unblock(
        record, actor_role=actor_role, actor=actor, note=note))


def workflow_close(record_id: str, *, actor_role: str, actor: str, note: str = "",
                   evidence: list[str] | None = None,
                   expected_revision: int | None = None) -> dict[str, Any]:
    return _workflow_write(record_id, "close", expected_revision, lambda record: workflow.close(
        record, actor_role=actor_role, actor=actor, note=note, evidence=evidence))


def workflow_claim(record_id: str, *, actor_role: str, actor: str, session_id: str | None = None,
                   ttl_seconds: int = workflow.LOCK_TTL_SECONDS,
                   expected_revision: int | None = None) -> dict[str, Any]:
    return _workflow_write(record_id, "claim", expected_revision, lambda record: workflow.claim(
        record, actor_role=actor_role, actor=actor, session_id=session_id, ttl_seconds=ttl_seconds))


def workflow_heartbeat(record_id: str, *, actor_role: str, actor: str, session_id: str | None = None,
                       ttl_seconds: int = workflow.LOCK_TTL_SECONDS,
                       expected_revision: int | None = None) -> dict[str, Any]:
    return _workflow_write(record_id, "heartbeat", expected_revision, lambda record: workflow.heartbeat(
        record, actor_role=actor_role, actor=actor, session_id=session_id, ttl_seconds=ttl_seconds))


def workflow_release(record_id: str, *, actor_role: str, actor: str, session_id: str | None = None,
                     expected_revision: int | None = None) -> dict[str, Any]:
    return _workflow_write(record_id, "release", expected_revision, lambda record: workflow.release(
        record, actor_role=actor_role, actor=actor, session_id=session_id))


def cmd_workflow(args: argparse.Namespace) -> None:
    command = args.workflow_command
    if command == "templates":
        print(json.dumps(workflow_templates(), ensure_ascii=False, indent=2))
        return
    if command == "show":
        print(json.dumps(workflow_show(args.id), ensure_ascii=False, indent=2))
        return
    outputs: dict[str, str] = {}
    for item in getattr(args, "output", None) or []:
        if "=" not in item:
            raise ValueError("invalid_outputs: --output 需要 key=value 格式（例如 --output branch=local/xxx）")
        key, _, value = item.partition("=")
        if not key.strip():
            raise ValueError("invalid_outputs: --output 的 key 不能为空")
        outputs[key.strip()] = value
    evidence = getattr(args, "evidence", None) or []
    common = dict(actor_role=args.actor_role, actor=args.actor, note=getattr(args, "note", None) or "",
                  evidence=evidence, expected_revision=args.expected_revision)
    if command == "start":
        result = workflow_start(args.id, template_id=args.template, stage=args.stage, **common)
    elif command == "submit":
        result = workflow_submit(args.id, outputs, **common)
    elif command == "advance":
        result = workflow_advance(args.id, to=args.to, **common)
    elif command == "rework":
        result = workflow_rework(args.id, args.to, **common)
    elif command == "block":
        result = workflow_block(args.id, **common)
    elif command == "unblock":
        result = workflow_unblock(args.id, **common)
    elif command == "close":
        result = workflow_close(args.id, **common)
    elif command == "claim":
        result = workflow_claim(args.id, actor_role=args.actor_role, actor=args.actor,
                                session_id=os.environ.get("CODEX_THREAD_ID"), expected_revision=args.expected_revision)
    elif command == "heartbeat":
        result = workflow_heartbeat(args.id, actor_role=args.actor_role, actor=args.actor,
                                    session_id=os.environ.get("CODEX_THREAD_ID"), expected_revision=args.expected_revision)
    elif command == "release":
        result = workflow_release(args.id, actor_role=args.actor_role, actor=args.actor,
                                  session_id=os.environ.get("CODEX_THREAD_ID"), expected_revision=args.expected_revision)
    else:
        raise ValueError("invalid_workflow_command")
    print(json.dumps(result, ensure_ascii=False, indent=2))


SCOPE_STATUSES = {"confirmed", "candidate", "not_ours"}
SCOPE_EVIDENCE_TYPES = {"user_instruction", "requirement", "bug"}
SCOPE_INPUT_FIELDS = {"id", "name", "status", "in_scope", "out_of_scope", "evidence", "related_ids"}


def _scope_nonempty_text(value: Any, *, maximum: int = 1000) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and not any(ord(char) < 32 and char not in "\t\n\r" for char in value))


def _validate_scope_entry(scope: Any) -> dict[str, Any]:
    if not isinstance(scope, dict) or set(scope) - SCOPE_INPUT_FIELDS - {"version"}:
        raise ValueError("invalid_scope_fields")
    ident = scope.get("id")
    if not isinstance(ident, str) or not re.fullmatch(r"SCOPE-[A-Z0-9]+(?:-[A-Z0-9]+)*", ident):
        raise ValueError("invalid_scope_id")
    if not _scope_nonempty_text(scope.get("name"), maximum=200):
        raise ValueError("invalid_scope_name")
    if not isinstance(scope.get("status"), str) or scope["status"] not in SCOPE_STATUSES:
        raise ValueError("invalid_scope_status")
    for key in ("in_scope", "out_of_scope", "related_ids"):
        values = scope.get(key)
        if not isinstance(values, list) or len(values) > 200 or any(not _scope_nonempty_text(value, maximum=500) for value in values):
            raise ValueError(f"invalid_scope_{key}")
    evidence = scope.get("evidence")
    if not isinstance(evidence, list) or not evidence or len(evidence) > 100:
        raise ValueError("invalid_scope_evidence")
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"type", "date", "reference", "summary"}:
            raise ValueError("invalid_scope_evidence")
        if not isinstance(item.get("type"), str) or item["type"] not in SCOPE_EVIDENCE_TYPES:
            raise ValueError("invalid_scope_evidence_type")
        date = item.get("date")
        if date is not None:
            if not isinstance(date, str): raise ValueError("invalid_scope_evidence_date")
            try: datetime.strptime(date, "%Y-%m-%d")
            except ValueError as exc: raise ValueError("invalid_scope_evidence_date") from exc
        if not _scope_nonempty_text(item.get("reference"), maximum=2000) or not _scope_nonempty_text(item.get("summary"), maximum=2000):
            raise ValueError("invalid_scope_evidence")
    return {key: scope[key] for key in ("id", "name", "status", "in_scope", "out_of_scope", "evidence", "related_ids")}


def upsert_scope(scope_input: Any, expected_catalog_revision: int) -> dict[str, Any]:
    """CAS-upsert one responsibility scope under the existing dashboard store lock."""
    if not isinstance(expected_catalog_revision, int) or expected_catalog_revision < 1:
        raise ValueError("invalid_expected_catalog_revision")
    if not isinstance(scope_input, dict) or set(scope_input) - SCOPE_INPUT_FIELDS:
        raise ValueError("invalid_scope_fields")
    ident = scope_input.get("id")
    if not isinstance(ident, str) or not re.fullmatch(r"SCOPE-[A-Z0-9]+(?:-[A-Z0-9]+)*", ident):
        raise ValueError("invalid_scope_id")

    def update() -> dict[str, Any]:
        path = ROOT / "scope-catalog.json"
        try: catalog = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise ValueError("scope_catalog_unavailable") from exc
        if (not isinstance(catalog, dict) or catalog.get("version") != 1
                or not isinstance(catalog.get("revision"), int)
                or not isinstance(catalog.get("scopes"), list)):
            raise ValueError("scope_catalog_invalid")
        if expected_catalog_revision != catalog["revision"]:
            raise ValueError("scope_catalog_revision_conflict")
        scopes = catalog["scopes"]
        if len({item.get("id") for item in scopes if isinstance(item, dict)}) != len(scopes):
            raise ValueError("scope_catalog_invalid")
        existing = next((item for item in scopes if isinstance(item, dict) and item.get("id") == ident), None)
        merged = dict(existing or {})
        merged.update(scope_input)
        if existing and "status" not in scope_input:
            merged["status"] = existing.get("status")
        merged.setdefault("in_scope", [])
        merged.setdefault("out_of_scope", [])
        merged.setdefault("related_ids", [])
        merged.setdefault("evidence", existing.get("evidence", []) if existing else [])
        normalized = _validate_scope_entry(merged)
        normalized["version"] = int(existing.get("version", 0)) + 1 if existing else 1
        if existing:
            scopes[scopes.index(existing)] = normalized
        else:
            scopes.append(normalized)
        catalog["revision"] += 1
        catalog["updated_at"] = timestamp()[:10]
        backend._atomic_bytes(path, (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return {"revision": catalog["revision"], "scope": normalized}

    return with_lock(update)


def cmd_scope_upsert(args: argparse.Namespace) -> None:
    raw = json.load(sys.stdin) if args.input == "-" else json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = upsert_scope(raw, args.expected_catalog_revision)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    db = load_store()
    records = db["records"]
    if args.type:
        records = [r for r in records if r.get("type") == args.type]
    if not args.include_archived:
        records = [r for r in records if r.get("status") != "archived"]
    print(json.dumps({"revision": db.get("revision", 0), "records": records}, ensure_ascii=False, indent=2))


def cmd_show(args: argparse.Namespace) -> None:
    db = load_store()
    record = next((r for r in db["records"] if r.get("id") == args.id), None)
    if not record:
        raise SystemExit(f"No record with id: {args.id}")
    print(json.dumps(record, ensure_ascii=False, indent=2))


def cmd_batch_init(args: argparse.Namespace) -> None:
    print(json.dumps(adopt_task_batch(args.task_id), ensure_ascii=False, indent=2))


def cmd_task_context(args: argparse.Namespace) -> None:
    print(json.dumps(task_context(args.id), ensure_ascii=False, indent=2))


def cmd_progress(args: argparse.Namespace) -> None:
    evidence = args.evidence or []
    print(json.dumps(add_progress(args.id, args.message, args.actor, evidence), ensure_ascii=False, indent=2))


def cmd_task_result(args: argparse.Namespace) -> None:
    evidence = args.evidence or []
    print(json.dumps(add_progress(args.id, args.message, args.actor, evidence, result=True, outcome=args.outcome),
                     ensure_ascii=False, indent=2))


def cmd_archive(args: argparse.Namespace) -> None:
    def update() -> dict[str, Any]:
        db = load_store()
        record = next((r for r in db["records"] if r.get("id") == args.id), None)
        if not record:
            raise SystemExit(f"No record with id: {args.id}")
        previous_status = record.get("status")
        record["status"] = "archived"
        record["archive_reason"] = args.reason or ""
        record["updated_at"] = timestamp()
        record.setdefault("history", []).append({"at": record["updated_at"], "status": "archived", "note": f"归档；原状态：{previous_status}"})
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        event("archive", record, db["revision"])
        return {"revision": db["revision"], "record": record}

    print(json.dumps(with_lock(update), ensure_ascii=False, indent=2))


def cmd_purge_fixture(args: argparse.Namespace) -> None:
    allowed = {"TEST-FIXTURE-1"}
    if args.id not in allowed:
        raise SystemExit("only the explicitly named disposable fixture may be removed")

    def update() -> dict[str, Any]:
        db = load_store()
        before = len(db["records"])
        record = next((r for r in db["records"] if r.get("id") == args.id), None)
        db["records"] = [r for r in db["records"] if r.get("id") != args.id]
        if record:
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db)
            event("cleanup_test_fixture", record, db["revision"])
        return {"revision": db.get("revision", 0), "removed": before - len(db["records"]), "id": args.id}

    print(json.dumps(with_lock(update), ensure_ascii=False, indent=2))


def markdown_plain(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = value.replace("`", "")
    return re.sub(r"\s+", " ", value).strip()


def parse_requirements(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_table = False
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if line.strip() == "## 需求清单":
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0] == "ID" or set("".join(cells[0])) == {"-"}:
            continue
        record_id, title, responsibility, source_status, note = cells[:5]
        if not record_id.startswith("REQ-"):
            continue
        plain_status = markdown_plain(source_status)
        if plain_status.startswith("🟢"):
            status = "completed"
        elif "阻塞" in plain_status:
            status = "blocked"
        elif "核心已验" in plain_status:
            status = "awaiting_acceptance"
        else:
            status = "partial"
        status_detail = plain_status.removeprefix("🟢").removeprefix("🟡").strip()
        evidence = [markdown_plain(responsibility)]
        shared = "共同" in responsibility
        user_directed = any(marker in responsibility for marker in (
            "历史用户要求", "明确需求摘要", "用户明确反馈与规则",
        ))
        records.append({
            "id": record_id,
            "type": "work",
            "title": markdown_plain(title),
            "status": status,
            "status_detail": status_detail,
            "notes": [markdown_plain(note)],
            "evidence": evidence,
            "source": "REQUIREMENTS.md · 需求清单",
            "source_ref": f"{path}#L{line_number}",
            "source_updated_at": None,
            "observed_at": timestamp(),
            "evidence_at": None,
            "owner": "当前用户（共同负责）" if shared else "当前用户",
            "is_shared": shared,
            "source_kind": "user_directed" if user_directed else "company_assignment",
            "related_ids": [],
            "history": [],
            "responsibility_evidence": markdown_plain(responsibility),
            "verification_state": "历史证据仅覆盖已注明范围；当前 HEAD 与新边界按备注单独核验。" if "限定历史验收范围" in plain_status else None,
        })
    return records


def cmd_bootstrap(args: argparse.Namespace) -> None:
    path = Path(args.requirements).resolve()
    parsed = parse_requirements(path)

    def update() -> dict[str, Any]:
        db = load_store()
        records = db["records"]
        for incoming in parsed:
            old = next((r for r in records if r.get("id") == incoming["id"]), None)
            if old:
                # Re-running bootstrap refreshes only requirement-ledger fields;
                # user-added notes and state history remain intact.
                for key, value in incoming.items():
                    if key not in {"notes", "history"}:
                        old[key] = value
                    elif not old.get(key):
                        old[key] = value
                old["updated_at"] = timestamp()
            else:
                records.append(normalize_record(incoming))
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        for record in parsed:
            event("bootstrap", record, db["revision"])
        return {"revision": db["revision"], "imported": len(parsed), "ids": [r["id"] for r in parsed]}

    print(json.dumps(with_lock(update), ensure_ascii=False, indent=2))


def cmd_sync_bugs(args: argparse.Namespace) -> None:
    path = Path(args.state).resolve()
    state = json.loads(path.read_text(encoding="utf-8"))
    snapshot_at = state.get("last_successful_snapshot_at")
    stale = bool(state.get("stale"))
    bugs = state.get("bugs")
    attempt = state.get("last_attempt") if isinstance(state.get("last_attempt"), dict) else {}
    ids = [str(item.get("id", "")).strip() for item in bugs] if isinstance(bugs, list) and all(isinstance(item, dict) for item in bugs) else []
    total = attempt.get("reported_total")
    verification = attempt.get("verification") if isinstance(attempt.get("verification"), str) else ""
    unique_count = attempt.get("unique_ids")
    captured_at = _parse_iso_datetime(attempt.get("captured_at"))
    snapshot_dt = _parse_iso_datetime(snapshot_at)
    same_round = bool(captured_at and snapshot_dt and abs((captured_at - snapshot_dt).total_seconds()) <= 5)
    successful = bool(snapshot_at) and same_round and isinstance(bugs, list) and not stale and (attempt.get("result") == "succeeded" or attempt.get("bug_list_result") == "succeeded") and attempt.get("stale") is False and attempt.get("filter") == "我负责的" and "matched self owner" in verification and "complete=true" in verification and "all pages returned" in verification and isinstance(total, int) and not isinstance(total, bool) and isinstance(unique_count, int) and not isinstance(unique_count, bool) and total == len(bugs) == len(set(ids)) == unique_count and isinstance(attempt.get("pages_read"), int) and not isinstance(attempt.get("pages_read"), bool) and attempt["pages_read"] > 0 and all(ids)

    def update() -> dict[str, Any]:
        db = load_store()
        db.setdefault("sync", {})["bugs"] = {
            "source": str(path),
            "last_attempt": state.get("last_attempt"),
            "last_successful_snapshot_at": snapshot_at,
            "source_updated_at_available": state.get("source_updated_at_available"),
            "stale": stale or not successful,
            "error": None if successful and not stale else "最新 BUG 快照不可用；保留最近成功记录。",
        }
        if not successful or stale:
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db)
            return {"revision": db["revision"], "synced": 0, "preserved": True, "stale": True}

        records = db["records"]
        incoming_ids = set()
        for item in bugs:
            bug_id = str(item.get("id", "")).strip()
            if not bug_id:
                continue
            incoming_ids.add(bug_id)
            old = next((r for r in records if r.get("id") == bug_id and r.get("type") == "bug"), None)
            incoming_triage = str(item.get("triage") or "")
            old_association_hold = bool(old and str(old.get("archive_reason") or "").startswith(BUG_ASSOCIATION_HOLD_PREFIX))
            explicit_reopen = bool(item.get("reopen_cycle_id"))
            # A user-confirmed historical association remains deduped even if
            # the upstream monitor snapshot still carries its old red triage.
            # Only an explicit reopen cycle may return it to the active lane.
            keep_association_hold = old_association_hold and not explicit_reopen
            association_hold = not explicit_reopen and (
                incoming_triage.startswith(BUG_ASSOCIATION_HOLD_PREFIX) or keep_association_hold
            )
            related_ids = item.get("related_ids")
            if not isinstance(related_ids, list):
                related_ids = old.get("related_ids", []) if old else []
            status = classify_bug_sync_status(item)
            transfer_mode = old.get("transfer_disposition") if old else None
            if transfer_mode == "transferred":
                status = "completed"
                incoming_triage = "已转交"
            elif transfer_mode == "transfer_pending":
                status = "pending"
                incoming_triage = old.get("status_detail") or "请转交相关负责人"
            if explicit_reopen and old_association_hold:
                status = classify_bug_sync_status({**item, "triage": incoming_triage.removeprefix(BUG_ASSOCIATION_HOLD_PREFIX)})
            if association_hold:
                status = "archived"
                if old_association_hold:
                    incoming_triage = old.get("triage") or incoming_triage
                    related_ids = old.get("related_ids", related_ids)
                    hold_reason = old.get("archive_reason") or incoming_triage
                else:
                    hold_reason = incoming_triage
            else:
                hold_reason = None
            record = {
                "id": bug_id,
                "type": "bug",
                "title": str(item.get("title") or bug_id),
                "status": status,
                "platform_status": item.get("status"),
                "status_detail": (hold_reason if association_hold else incoming_triage) or f"平台状态“{item.get('status') or '未提供'}”尚无明确映射，保留原值并待核实。",
                "our_status": incoming_triage or None,
                "triage": incoming_triage or None,
                "assignee": item.get("assignee"),
                "owner": item.get("assignee"),
                "severity": item.get("severity"),
                "source": "BUG monitor 成功快照",
                "source_ref": str(path),
                "source_updated_at": item.get("source_updated_at"),
                "observed_at": item.get("observed_at") or snapshot_at,
                "evidence_at": snapshot_at,
                "notes": list(old.get("notes", [])) if old and old.get("notes") else ["我方已提交测试不代表 QA 通过、平台验收或工单关闭。"],
                "evidence": [f"成功快照时间：{snapshot_at}"],
                "related_ids": related_ids,
                "history": [],
                "verification_state": "我方完成或提交测试不代表 QA 通过、平台验收或工单关闭。",
                "stale": False,
            }
            if association_hold:
                record["archive_reason"] = hold_reason
            elif old and old_association_hold and explicit_reopen:
                record["archive_reason"] = ""
                record["reopen_cycle_id"] = item["reopen_cycle_id"]
                record["history"] = list(old.get("history", [])) + [{
                    "at": timestamp(), "status": "in_progress",
                    "note": f"经明确 reopen 周期 {item['reopen_cycle_id']} 重新进入活动 BUG；历史关联与平台状态保留",
                }]
            if old:
                # GUI-owned fields are user input and survive every source refresh.
                gui_fields = {key: old[key] for key in (
                    "manual_status", "manual_completed_at", "manual_completed_by", "manual_status_conflict", "followups",
                    "current_agent_session_id", "current_agent_name", "last_agent_session_id", "last_agent_name", "last_agent_activity_at", "agent_state", "agent_work_status",
                    "completed_at", "initial_brief", "latest_brief", "progress", "final_result", "phase", "attachments", "revision",
                    "transfer_disposition", "transfer_target", "transfer_requested_at", "transfer_confirmed_at", "transfer_confirmation", "completion_kind",
                ) if key in old}
                prior_source = (old.get("platform_status"), old.get("source_updated_at"), old.get("triage"))
                incoming_source = (record.get("platform_status"), record.get("source_updated_at"), record.get("triage"))
                if old.get("manual_status") == "completed" and prior_source != incoming_source:
                    gui_fields["manual_status_conflict"] = {
                        "detected_at": timestamp(),
                        "reason": "平台 BUG 来源状态或更新时间在用户手动完成后发生变化；需核对返修周期。",
                        "previous_source": list(prior_source),
                        "current_source": list(incoming_source),
                    }
                if not record.get("history"):
                    record["history"] = old.get("history", []) + [{"at": timestamp(), "status": old.get("status"), "note": f"由成功快照 {snapshot_at} 更新"}]
                normalized = normalize_record(record)
                for key, value in normalized.items():
                    if key in {"notes", "evidence"}:
                        prior = old.get(key, [])
                        old[key] = prior if prior else value
                    elif key == "history":
                        old[key] = record["history"]
                    else:
                        old[key] = value
                old.update(gui_fields)
            else:
                records.append(normalize_record(record))
        for old in records:
            transfer_requested = _parse_iso_datetime(old.get("transfer_requested_at")) if old.get("transfer_disposition") == "transfer_pending" else None
            transfer_snapshot = _parse_iso_datetime(snapshot_at)
            if old.get("type") == "bug" and old.get("id") not in incoming_ids and old.get("transfer_disposition") == "transfer_pending" and transfer_requested and transfer_snapshot and transfer_snapshot > transfer_requested:
                old.update({"status": "completed", "status_detail": "已转交", "our_status": "已转交", "triage": "已转交", "transfer_disposition": "transferred", "completion_kind": "transferred", "transfer_confirmed_at": timestamp(), "completed_at": timestamp(), "transfer_confirmation": "complete_successful_owned_list_absence"})
                old.setdefault("history", []).append({"at": timestamp(), "status": "completed", "note": f"完整成功的‘我负责的’列表（{snapshot_at}）已核实不含此 BUG；本地转交完成，不代表平台改派。"})
                old["updated_at"] = timestamp()
            elif old.get("type") == "bug" and old.get("id") not in incoming_ids and old.get("transfer_disposition") == "transfer_pending":
                # A complete but pre-request snapshot cannot confirm this handoff.
                continue
            elif old.get("type") == "bug" and old.get("id") not in incoming_ids and old.get("transfer_disposition") != "transferred" and old.get("status") not in {"archived", "owner_changed"}:
                old.setdefault("history", []).append({"at": timestamp(), "status": old.get("status"), "note": f"不在成功快照 {snapshot_at}，转为已归档历史"})
                old["status"] = "archived"
                old["updated_at"] = timestamp()
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        for bug_id in incoming_ids:
            event("sync_bug", {"id": bug_id, "type": "bug"}, db["revision"])
        return {"revision": db["revision"], "synced": len(incoming_ids), "snapshot_at": snapshot_at, "stale": False}

    print(json.dumps(with_lock(update), ensure_ascii=False, indent=2))


def _find_record(db: dict[str, Any], record_id: str) -> dict[str, Any]:
    record = next((item for item in db["records"] if item.get("id") == record_id), None)
    if record is None:
        raise ValueError("record_not_found")
    return record


def confirm_bug_transfer(record_id: str) -> dict[str, Any]:
    def update() -> dict[str, Any]:
        db = load_store()
        record = _find_record(db, record_id)
        if record.get("type") != "bug" or record.get("transfer_disposition") != "transfer_pending":
            raise ValueError("transfer_not_pending")
        now = timestamp()
        record.update({"status": "completed", "status_detail": "已转交", "our_status": "已转交", "triage": "已转交", "transfer_disposition": "transferred", "completion_kind": "transferred", "transfer_confirmed_at": now, "completed_at": now, "transfer_confirmation": "user_confirmed_local"})
        record.setdefault("history", []).append({"at": now, "status": "completed", "note": f"用户在个人工作台本地确认已转交{record.get('transfer_target') or '相关负责人'}；未修改平台指派或状态。"})
        db["revision"] = int(db.get("revision", 0)) + 1
        record["revision"] = db["revision"]
        record["updated_at"] = now
        atomic_write(db)
        event("transfer_bug_local", record, db["revision"])
        return {"revision": db["revision"], "record": record}
    return with_lock(update)


def _record_event(action: str, record: dict[str, Any], revision: int, followup_id: str | None = None) -> None:
    event(action, record, revision, followup_id)


def cmd_complete(args: argparse.Namespace) -> None:
    print(json.dumps(set_manual_completion(args.id, args.completed), ensure_ascii=False, indent=2))


def set_manual_completion(record_id: str, completed: bool) -> dict[str, Any]:
    def update() -> dict[str, Any]:
        db = load_store()
        record = _find_record(db, record_id)
        was_terminal = _is_batch_terminal(record)
        record["manual_status"] = "completed" if completed else None
        record["manual_completed_at"] = timestamp() if completed else None
        record["completed_at"] = record["manual_completed_at"] if completed else None
        record["manual_completed_by"] = "user" if completed else None
        if completed and record.get("current_agent_session_id"):
            record["last_agent_session_id"] = record["current_agent_session_id"]
            record["last_agent_name"] = record.get("current_agent_name")
            record["current_agent_session_id"] = None
            record["current_agent_name"] = None
            record["agent_state"] = "completed"
        record["updated_at"] = timestamp()
        if record.get("type") == "task" and was_terminal and not _is_batch_terminal(record):
            _assign_task_to_current_batch(db, record, record["updated_at"])
        record.setdefault("history", []).append({"at": record["updated_at"], "status": "completed" if completed else record.get("status"), "note": "用户在个人工作台手动标记完成" if completed else "用户在个人工作台取消手动完成"})
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        _record_event("manual_completion", record, db["revision"])
        return {"revision": db["revision"], "record": record}
    return with_lock(update)


def error_code(exc: ValueError) -> str:
    contract_code = getattr(exc, "code", None)
    if isinstance(contract_code, str) and contract_code:
        return contract_code
    mapping = {
        "agent_expired": "agent_expired",
        "agent_owner_conflict": "agent_owner_conflict",
        "record_attachment_not_found": "record_attachment_not_found",
        "attachment_duplicate": "attachment_duplicate",
        "invalid_record_attachment": "invalid_record_attachment",
        "record_attachment_too_large": "record_attachment_too_large",
        "record_not_found": "record_not_found",
        "followup_not_found": "followup_not_found",
        "followup_not_claimed": "followup_not_claimed",
        "invalid_finish_payload": "invalid_finish_payload",
        "prompt_required_or_too_long": "invalid_prompt",
        "invalid_claim_parameters": "invalid_claim_parameters",
        "invalid_source_url": "invalid_source_url",
        "invalid_source_category": "invalid_source_category",
        "invalid_source_purpose": "invalid_source_purpose",
        "invalid_source_primary": "invalid_source_primary",
        "invalid_source_origin": "invalid_source_origin",
        "invalid_source_name": "invalid_source_name",
        "source_duplicate": "source_duplicate",
        "source_not_found": "source_not_found",
        "invalid_source_filename": "invalid_source_filename",
        "unsupported_source_file_type": "unsupported_source_file_type",
        "source_file_size_invalid": "source_file_size_invalid",
        "source_file_type_mismatch": "source_file_type_mismatch",
        "source_archive_limits_exceeded": "source_archive_limits_exceeded",
        "macros_not_allowed": "macros_not_allowed",
        "source_text_must_be_utf8": "source_text_must_be_utf8",
        "source_manifest_invalid": "source_manifest_invalid",
        "invalid_base64": "invalid_base64",
        "invalid_source_seed": "invalid_source_seed",
        "invalid_source_id": "invalid_source_id",
        "revision_conflict": "revision_conflict",
        "agent_owner_conflict": "agent_owner_conflict",
        "record_completed": "record_completed",
        "invalid_agent_session": "invalid_agent_session",
        "invalid_source_override": "invalid_source_override",
        "invalid_source_lifecycle": "invalid_source_lifecycle",
        "invalid_source_slot": "invalid_source_slot",
        "invalid_source_group": "invalid_source_group",
        "invalid_source_version": "invalid_source_version",
        "invalid_source_inspection": "invalid_source_inspection",
        "source_group_not_found": "source_group_not_found",
        "source_group_slot_conflict": "source_group_slot_conflict",
        "source_group_location_conflict": "source_group_location_conflict",
        "invalid_iteration_id": "invalid_iteration_id",
        "iteration_not_found": "iteration_not_found",
        "invalid_iteration_order": "invalid_iteration_order",
        "iteration_order_mismatch": "iteration_order_mismatch",
        "missing_iteration_id": "missing_iteration_id",
        "invalid_iteration_title": "invalid_iteration_title",
        "invalid_iteration_version_tag": "invalid_iteration_version_tag",
    }
    return mapping.get(str(exc), "invalid_request")


def cmd_add_followup(args: argparse.Namespace) -> None:
    print(json.dumps(add_followup(args.id, args.prompt), ensure_ascii=False, indent=2))


def add_followup(record_id: str, prompt: str) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("prompt_required_or_too_long")
    def update() -> dict[str, Any]:
        db = load_store()
        record = _find_record(db, record_id)
        item = {"id": str(uuid.uuid4()), "created_at": timestamp(), "created_by": "user", "prompt": prompt, "status": "pending"}
        record.setdefault("followups", []).append(item)
        record["updated_at"] = timestamp()
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        _record_event("followup_added", record, db["revision"], item["id"])
        return {"revision": db["revision"], "record": record, "followup": item}
    return with_lock(update)


def pending_followups(db: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for record in db["records"]:
        for item in record.get("followups", []):
            if item.get("status") == "pending":
                result.append({"record_id": record["id"], "record_title": record.get("title", ""), **item})
    return sorted(result, key=lambda item: (item.get("created_at", ""), item.get("id", "")))


def cmd_pending(args: argparse.Namespace) -> None:
    db = load_store()
    print(json.dumps({"revision": db.get("revision", 0), "followups": pending_followups(db)}, ensure_ascii=False, indent=2))


def claim_followups(ids: list[str] | None = None, limit: int = 20, claimed_by: str = "agent") -> dict[str, Any]:
    if not 1 <= limit <= 100 or not claimed_by.strip() or len(claimed_by) > 100:
        raise ValueError("invalid_claim_parameters")
    def update() -> dict[str, Any]:
        db = load_store()
        wanted = set(ids) if ids is not None else None
        claimed = []
        claimed_records = {
            record["id"] for record in db["records"]
            if any(item.get("status") == "claimed" for item in record.get("followups", []))
        }
        for record in db["records"]:
            if record["id"] in claimed_records:
                continue
            for item in record.get("followups", []):
                if item.get("status") == "pending" and (wanted is None or item.get("id") in wanted):
                    item.update({"status": "claimed", "claimed_at": timestamp(), "claimed_by": claimed_by})
                    claimed.append({"record_id": record["id"], "record_title": record.get("title", ""), **item})
                    claimed_records.add(record["id"])
                    break
                if len(claimed) >= limit:
                    break
            if len(claimed) >= limit:
                break
        if wanted is not None:
            found = {item["id"] for record in db["records"] for item in record.get("followups", [])}
            if wanted - found:
                raise ValueError("followup_not_found")
        if claimed:
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db)
            for item in claimed:
                record = _find_record(db, item["record_id"])
                _record_event("followup_claimed", record, db["revision"], item["id"])
        return {"revision": db.get("revision", 0), "claimed": claimed}
    return with_lock(update)


def cmd_claim(args: argparse.Namespace) -> None:
    ids = args.ids.split(",") if args.ids else None
    print(json.dumps(claim_followups(ids, args.limit, args.claimed_by), ensure_ascii=False, indent=2))


def finish_followup(followup_id: str, result: str, outcome: str) -> dict[str, Any]:
    result = result.strip()
    if outcome not in {"success", "failure"} or not result or len(result) > MAX_RESULT_CHARS:
        raise ValueError("invalid_finish_payload")
    def update() -> dict[str, Any]:
        db = load_store()
        for record in db["records"]:
            for item in record.get("followups", []):
                if item.get("id") == followup_id:
                    if item.get("status") != "claimed":
                        raise ValueError("followup_not_claimed")
                    actor = item.get("claimed_by", "agent")
                    item.update({"status": "handled", "handled_at": timestamp(), "handled_by": actor, "outcome": outcome, "result": result})
                    item.pop("claimed_by", None)
                    record["updated_at"] = timestamp()
                    db["revision"] = int(db.get("revision", 0)) + 1
                    atomic_write(db)
                    _record_event("followup_handled", record, db["revision"], followup_id)
                    return {"revision": db["revision"], "record": record, "followup": item}
        raise ValueError("followup_not_found")
    return with_lock(update)


def normalize_source_url(raw_url: str) -> tuple[str, str]:
    if not isinstance(raw_url, str) or len(raw_url) > 4096:
        raise ValueError("invalid_source_url")
    url = raw_url.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("invalid_source_url")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid_source_url") from exc
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None or (parts.scheme.lower(), port) in {("https", 443), ("http", 80)} else f"{host}:{port}"
    normalized = parts._replace(scheme=parts.scheme.lower(), netloc=netloc).geturl()
    if any(char.isspace() or ord(char) < 32 for char in url):
        raise ValueError("invalid_source_url")
    return url, normalized


def _source_store_list(db: dict[str, Any]) -> list[dict[str, Any]]:
    sources = db.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("source_manifest_invalid")
    return sources


def _source_event(action: str, source: dict[str, Any], revision: int) -> None:
    event(action, {"id": source["id"], "type": source["type"]}, revision)


def _source_effective_slot(source: dict[str, Any]) -> str | None:
    slot = source.get("slot")
    return slot if slot in SOURCE_SLOTS else None


def _source_scope(source: dict[str, Any]) -> str:
    stored = source.get("scope")
    if stored in {"global", "iteration"}:
        return stored
    if _source_effective_slot(source) in GLOBAL_FIXED_SLOTS:
        return "global"
    return "iteration" if source.get("iteration_id") else "global"


def _source_group_id(source: dict[str, Any]) -> str:
    value = source.get("group_id")
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        return value
    return str(source.get("id") or "")


def _source_is_active(source: dict[str, Any]) -> bool:
    return bool(source.get("active")) and source.get("status") != "removed"


def _source_stored_version_no(source: dict[str, Any]) -> int | None:
    value = source.get("version_no")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _source_version_label(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > MAX_SOURCE_VERSION_LABEL or any(ord(ch) < 32 for ch in value):
        raise ValueError("invalid_source_version")
    value = value.strip()
    return value or None


def _source_group_key(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("invalid_source_group")
    return value


def _source_slot_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in SOURCE_SLOTS:
        raise ValueError("invalid_source_slot")
    return value


def _source_file_extension(source: dict[str, Any]) -> str | None:
    stored = source.get("file_ext")
    if isinstance(stored, str) and re.fullmatch(r"[A-Za-z0-9]{1,12}", stored):
        return stored.lower()
    filename = source.get("filename") or source.get("name") or ""
    if not isinstance(filename, str):
        filename = ""
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension and re.fullmatch(r"[a-z0-9]{1,12}", extension):
        return extension
    if source.get("type") == "link":
        try:
            extension = Path(urlsplit(str(source.get("url") or "")).path).suffix.lower().lstrip(".")
        except ValueError:
            extension = ""
        if extension and re.fullmatch(r"[a-z0-9]{1,12}", extension):
            return extension
    return None


def _source_mime(source: dict[str, Any]) -> str | None:
    stored = source.get("mime") or source.get("mime_type")
    if isinstance(stored, str) and stored:
        return stored
    filename = source.get("filename") or source.get("name")
    if isinstance(filename, str) and filename:
        guessed = mimetypes.guess_type(filename)[0]
        if guessed:
            return guessed
    return None


def _source_logo_kind(source: dict[str, Any]) -> str:
    slot = _source_effective_slot(source)
    if slot:
        return slot
    return "file" if source.get("type") == "file" else "link"


def _source_version_maps(all_sources: list[dict[str, Any]]) -> tuple[dict[int, int], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Compute effective version numbers and group ordering without mutating stored data."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for source in all_sources:
        groups.setdefault(_source_group_id(source), []).append(source)
    numbers: dict[int, int] = {}
    for members in groups.values():
        ordered = sorted(members, key=lambda item: (str(item.get("added_at") or ""), str(item.get("id") or "")))
        stored = [_source_stored_version_no(item) for item in ordered]
        if all(value is not None for value in stored) and len(set(stored)) == len(stored):
            assigned = list(stored)
        else:
            # Legacy or hand-edited groups may lack version numbers. Their
            # added_at order is authoritative and all numbers are re-derived.
            assigned = list(range(1, len(ordered) + 1))
        for item, number in zip(ordered, assigned):
            numbers[id(item)] = number
    active_by_group: dict[str, list[dict[str, Any]]] = {}
    for group_id, members in groups.items():
        active = [item for item in members if _source_is_active(item)]
        active.sort(key=lambda item: (numbers.get(id(item), 1), str(item.get("added_at") or ""), str(item.get("id") or "")), reverse=True)
        active_by_group[group_id] = active
    return numbers, groups, active_by_group


def enrich_source_records(records: list[dict[str, Any]], all_sources: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Add library/version/inspection fields to copies of source records.

    Stored versions are never removed. `is_latest` and `version_count` are
    derived from the currently active members of each group so a removed newer
    version does not hide an older active one.
    """
    universe = list(all_sources) if all_sources is not None else list(records)
    numbers, groups, active_by_group = _source_version_maps(universe)
    active_latest: dict[str, str | None] = {}
    for group_id, active in active_by_group.items():
        active_latest[group_id] = str(active[0].get("id")) if active else None
    enriched: list[dict[str, Any]] = []
    for record in records:
        copy = dict(record)
        group_id = _source_group_id(record)
        members = groups.get(group_id, [record])
        active = active_by_group.get(group_id, [])
        slot = _source_effective_slot(record)
        version_no = numbers.get(id(record), _source_stored_version_no(record) or 1)
        label = record.get("version_label")
        if not isinstance(label, str) or not label.strip():
            label = f"v{version_no}"
        else:
            label = label.strip()[:MAX_SOURCE_VERSION_LABEL]
        enabled_value = record.get("inspection_enabled")
        inspection_enabled = bool(enabled_value) if isinstance(enabled_value, bool) else slot == "requirement_doc"
        mode = record.get("inspection_mode")
        inspection_mode = mode if mode in SOURCE_INSPECTION_MODES else ("auto" if slot == "requirement_doc" else "manual")
        active_ids = [str(item.get("id")) for item in active]
        rank = active_ids.index(str(record.get("id"))) if str(record.get("id")) in active_ids else None
        inspection_active = bool(_source_is_active(record) and inspection_enabled)
        if slot == "requirement_doc" and rank is not None and rank > 1:
            inspection_active = False
        copy.update({
            "slot": slot,
            "group_id": group_id,
            "version_no": version_no,
            "version_label": label,
            "version_count": len(active) if active else len(members),
            "latest": bool(_source_is_active(record) and active_latest.get(group_id) == str(record.get("id"))),
            "is_latest": bool(_source_is_active(record) and active_latest.get(group_id) == str(record.get("id"))),
            "scope": _source_scope(record),
            "iteration_id": record.get("iteration_id"),
            "attachments": list(record.get("attachments") or []),
            "inspection_enabled": inspection_enabled,
            "inspection_mode": inspection_mode,
            "inspection_active": inspection_active,
            "inspection_rank": rank,
            "last_inspection_attempt_at": record.get("last_inspection_attempt_at") or record.get("latest_read_attempt_at") or record.get("last_read_at"),
            "last_inspection_success_at": record.get("last_inspection_success_at") or record.get("last_successful_read_at"),
            "last_inspection_status": record.get("last_inspection_status") or record.get("read_state") or record.get("read_status"),
            "file_ext": _source_file_extension(record),
            "mime": _source_mime(record),
            "logo_kind": _source_logo_kind(record),
        })
        enriched.append(copy)
    return enriched


def _validate_scope_slot(scope: str, slot: str | None) -> None:
    """Reject fixed slots that belong to the other archive scope."""
    if slot is None:
        return
    if scope == "global" and slot in ITERATION_FIXED_SLOTS:
        raise ValueError("invalid_source_slot")
    if scope == "iteration" and slot in GLOBAL_FIXED_SLOTS:
        raise ValueError("invalid_source_slot")


def _new_source_library_fields(sources: list[dict[str, Any]], source_id: str, *, slot: Any, group_id: Any, version_label: Any,
                               scope: Any = None, iteration_id: Any = None, attachments: Any = None,
                               iterations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Validate and derive library fields for a newly created source/version."""
    slot = _source_slot_value(slot)
    group_id = _source_group_key(group_id)
    label = _source_version_label(version_label)
    numbers, groups, _ = _source_version_maps(sources)
    members: list[dict[str, Any]] = []
    if group_id is not None:
        members = [item for item in sources if _source_group_id(item) == group_id]
        if not members:
            raise ValueError("source_group_not_found")
    else:
        group_id = source_id
    existing_slots = {_source_effective_slot(item) for item in members} - {None}
    if len(existing_slots) > 1:
        raise ValueError("source_group_slot_conflict")
    inherited_slot = next(iter(existing_slots), None)
    if slot is None:
        slot = inherited_slot
    elif inherited_slot is not None and slot != inherited_slot:
        raise ValueError("source_group_slot_conflict")
    version_no = max((numbers.get(id(item), 0) for item in members), default=0) + 1
    if scope is not None:
        if scope not in {"global", "iteration"}:
            raise ValueError("invalid_source_scope")
        effective_scope = scope
    else:
        effective_scope = "global" if slot in GLOBAL_FIXED_SLOTS else "iteration" if iteration_id else "global"
    clean_iter_id = str(iteration_id).strip() if (iteration_id and isinstance(iteration_id, str)) else None
    if members:
        # Versions must live with the group; new members inherit the group's location.
        member_scope = next((item.get("scope") for item in members if item.get("scope") in {"global", "iteration"}), None)
        member_iter = next((item.get("iteration_id") for item in members if item.get("iteration_id")), None)
        if scope is None and member_scope:
            effective_scope = member_scope
        elif scope is None and member_iter:
            effective_scope = "iteration"
        if clean_iter_id is None and member_iter:
            clean_iter_id = member_iter
    if effective_scope == "iteration":
        if not clean_iter_id:
            raise ValueError("missing_iteration_id")
        matched = next((it for it in (iterations or []) if str(it.get("id")) == clean_iter_id or it.get("version_tag") == clean_iter_id), None)
        if not matched:
            raise ValueError("iteration_not_found")
        clean_iter_id = str(matched.get("id"))
        _validate_scope_slot("iteration", slot)
    else:
        clean_iter_id = None
        if scope is not None:
            _validate_scope_slot("global", slot)
    if members:
        # Every version in a group must share one home. Moving is a group
        # operation, never a per-version side effect.
        canonical_member_iter = None
        if member_iter:
            matched_member = next((it for it in (iterations or [])
                                   if str(it.get("id")) == member_iter or it.get("version_tag") == member_iter), None)
            canonical_member_iter = str(matched_member.get("id")) if matched_member else str(member_iter)
        if member_scope and effective_scope != member_scope:
            raise ValueError("source_group_location_conflict")
        if canonical_member_iter and clean_iter_id != canonical_member_iter:
            raise ValueError("source_group_location_conflict")
    return {
        "slot": slot,
        "group_id": group_id,
        "version_no": version_no,
        "version_label": label or f"v{version_no}",
        "scope": effective_scope,
        "iteration_id": clean_iter_id,
        "attachments": list(attachments or []),
        "promote_group_slot": bool(members and slot is not None and not existing_slots),
        "inspection_enabled": slot == "requirement_doc",
        "inspection_mode": "auto" if slot == "requirement_doc" else "manual",
    }


def add_source_link(url: str, name: str | None = None, category: str = "需求资料", origin: str = "dashboard", *, source_id: str | None = None, purpose: str | None = None, primary: bool | None = None, verification_state: str | None = None, slot: Any = None, group_id: Any = None, version_label: Any = None, scope: Any = None, iteration_id: Any = None, attachments: Any = None) -> dict[str, Any]:
    original, normalized_url = normalize_source_url(url)
    if not isinstance(category, str) or not category.strip() or len(category) > 80:
        raise ValueError("invalid_source_category")
    if origin not in {"dashboard", "user_chat_link", "agent_import"}:
        raise ValueError("invalid_source_origin")
    from urllib.parse import urlsplit
    parts = urlsplit(original)
    inferred = parts.path.rstrip("/").split("/")[-1] or parts.hostname
    source = {
        "id": source_id or str(uuid.uuid4()), "type": "link", "name": (name or inferred or original).strip()[:200],
        "url": original, "normalized_url": normalized_url, "category": category.strip(),
        "active": True, "status": "registered", "read_status": "not_read",
        "lifecycle_status": "registered", "suspected_deprecated": False, "lifecycle_override": None,
        "lifecycle_updated_at": timestamp(), "last_verified_at": None, "suspected_at": None, "deprecated_at": None, "lifecycle_reason": None,
        "added_at": timestamp(), "updated_at": timestamp(), "origin": origin,
    }
    if not source["name"]:
        raise ValueError("invalid_source_name")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source["id"]):
        raise ValueError("invalid_source_id")
    if purpose is not None:
        if not isinstance(purpose, str) or len(purpose) > 4000:
            raise ValueError("invalid_source_purpose")
        source["purpose"] = purpose
    if primary is not None:
        if not isinstance(primary, bool):
            raise ValueError("invalid_source_primary")
        source["primary"] = primary
    if verification_state is not None:
        source["verification_state"] = verification_state
    def update() -> dict[str, Any]:
        db = load_store()
        sources = _source_store_list(db)
        if any(item.get("id") == source["id"] for item in sources):
            raise ValueError("source_duplicate")
        library = _new_source_library_fields(sources, source["id"], slot=slot, group_id=group_id, version_label=version_label,
                                             scope=scope, iteration_id=iteration_id, attachments=attachments,
                                             iterations=_iteration_store_list(db))
        duplicate = next((item for item in sources if item.get("normalized_url") == normalized_url), None)
        if duplicate and not (library["group_id"] != source["id"] and _source_group_id(duplicate) == library["group_id"]):
            raise ValueError("source_duplicate")
        source.update({key: value for key, value in library.items() if key != "promote_group_slot"})
        source["file_ext"] = _source_file_extension(source)
        source["mime"] = _source_mime(source)
        source["logo_kind"] = _source_logo_kind(source)
        at = timestamp()
        if library["promote_group_slot"]:
            for member in sources:
                if _source_group_id(member) == library["group_id"]:
                    member["slot"] = library["slot"]
                    member["updated_at"] = at
        sources.append(source)
        db["sources"] = sources
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        _source_event("source_added", source, db["revision"])
        return {"revision": db["revision"], "source": source}
    return with_lock(update)


def validate_source_file(filename: str, data: bytes) -> tuple[str, str]:
    from pathlib import PurePath
    if not isinstance(filename, str) or len(filename) > 255 or not filename.strip() or any(ch in filename for ch in ("/", "\\", "\x00")) or any(ord(ch) < 32 or ord(ch) == 127 for ch in filename):
        raise ValueError("invalid_source_filename")
    extension = PurePath(filename).suffix.lower()
    if extension not in ALLOWED_SOURCE_EXTENSIONS:
        raise ValueError("unsupported_source_file_type")
    if not data or len(data) > MAX_SOURCE_FILE_BYTES:
        raise ValueError("source_file_size_invalid")
    if extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("source_file_type_mismatch")
        mime = "application/pdf"
    elif extension in {".png", ".jpg", ".jpeg"}:
        if extension == ".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("source_file_type_mismatch")
        if extension in {".jpg", ".jpeg"} and not data.startswith(b"\xff\xd8\xff"):
            raise ValueError("source_file_type_mismatch")
        mime = "image/png" if extension == ".png" else "image/jpeg"
    elif extension in {".docx", ".xlsx", ".pptx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > 2000 or sum(info.file_size for info in infos) > 100 * 1024 * 1024:
                    raise ValueError("source_archive_limits_exceeded")
                for info in infos:
                    if info.file_size > 25 * 1024 * 1024 or (info.compress_size == 0 and info.file_size > 0) or (info.compress_size and info.file_size / info.compress_size > 200):
                        raise ValueError("source_archive_limits_exceeded")
                    if "vbaproject.bin" in info.filename.lower():
                        raise ValueError("macros_not_allowed")
                names = {info.filename for info in infos}
                required = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}[extension]
                if "[Content_Types].xml" not in names or required not in names:
                    raise ValueError("source_file_type_mismatch")
        except zipfile.BadZipFile as exc:
            raise ValueError("source_file_type_mismatch") from exc
        mime = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}[extension]
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("source_text_must_be_utf8") from exc
        if "\x00" in text:
            raise ValueError("source_text_must_be_utf8")
        mime = "text/csv" if extension == ".csv" else "text/markdown" if extension in {".md", ".markdown"} else "text/plain"
    return extension, mime


def add_source_file(filename: str, data: bytes, category: str = "需求资料", origin: str = "dashboard", *, name: str | None = None, slot: Any = None, group_id: Any = None, version_label: Any = None, scope: Any = None, iteration_id: Any = None, attachments: Any = None) -> dict[str, Any]:
    extension, mime = validate_source_file(filename, data)
    if not isinstance(category, str) or not category.strip() or len(category) > 80:
        raise ValueError("invalid_source_category")
    if origin not in {"dashboard", "user_chat_attachment", "agent_import"}:
        raise ValueError("invalid_source_origin")
    if name is not None and (not isinstance(name, str) or len(name) > 200 or any(ord(ch) < 32 for ch in name)):
        raise ValueError("invalid_source_name")
    display_name = (name or "").strip() or Path(filename).name
    managed_file_id = f"{uuid.uuid4()}{extension}"
    stored_path = UPLOADS / managed_file_id
    source = {
        "id": str(uuid.uuid4()), "type": "file", "name": display_name,
        "filename": Path(filename).name, "managed_file_id": managed_file_id,
        "category": category.strip(), "mime_type": mime, "size_bytes": len(data),
        "active": True, "status": "registered", "read_status": "not_read",
        "lifecycle_status": "registered", "suspected_deprecated": False, "lifecycle_override": None,
        "lifecycle_updated_at": timestamp(), "last_verified_at": None, "suspected_at": None, "deprecated_at": None, "lifecycle_reason": None,
        "added_at": timestamp(), "updated_at": timestamp(), "origin": origin,
    }
    def update() -> dict[str, Any]:
        db = load_store()
        sources = _source_store_list(db)
        if any(item.get("id") == source["id"] for item in sources):
            raise ValueError("source_duplicate")
        library = _new_source_library_fields(sources, source["id"], slot=slot, group_id=group_id, version_label=version_label,
                                             scope=scope, iteration_id=iteration_id, attachments=attachments,
                                             iterations=_iteration_store_list(db))
        source.update({key: value for key, value in library.items() if key != "promote_group_slot"})
        source.update({"file_ext": extension.lstrip("."), "mime": mime, "logo_kind": _source_logo_kind(source)})
        at = timestamp()
        if library["promote_group_slot"]:
            for member in sources:
                if _source_group_id(member) == library["group_id"]:
                    member["slot"] = library["slot"]
                    member["updated_at"] = at
        UPLOADS.mkdir(parents=True, exist_ok=True)
        # A random UUID filename prevents path traversal and collisions.
        with stored_path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        committed = False
        try:
            sources.append(source)
            db["sources"] = sources
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db)
            committed = True
            _source_event("source_added", source, db["revision"])
            return {"revision": db["revision"], "source": source}
        except Exception:
            if not committed:
                stored_path.unlink(missing_ok=True)
            raise
    return with_lock(update)


def change_source_state(source_id: str, active: bool) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source_id):
        raise ValueError("source_not_found")
    def update() -> dict[str, Any]:
        db = load_store()
        sources = _source_store_list(db)
        source = next((item for item in sources if item.get("id") == source_id), None)
        if source is None:
            raise ValueError("source_not_found")
        source["active"] = active
        source["status"] = "registered" if active else "removed"
        source["updated_at"] = timestamp()
        if active:
            source.pop("removed_at", None)
        else:
            source["removed_at"] = source["updated_at"]
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        _source_event("source_restored" if active else "source_removed", source, db["revision"])
        return {"revision": db["revision"], "source": source}
    return with_lock(update)


def source_manifest(include_removed: bool = False, all_versions: bool = True) -> dict[str, Any]:
    db = load_store()
    sources = _source_store_list(db)
    selected = sources if include_removed else [item for item in sources if item.get("active")]
    enriched = enrich_source_records(selected, sources)
    if not all_versions:
        # The default remains "all versions" so the UI can expand history.
        # Agents that explicitly ask for only the current material can opt in.
        enriched = [item for item in enriched if item.get("is_latest") or not item.get("active")]
    return {"revision": db.get("revision", 0), "sources": enriched, "all_versions": bool(all_versions)}


def set_source_inspection(source_id: str, enabled: bool) -> dict[str, Any]:
    if not isinstance(enabled, bool):
        raise ValueError("invalid_source_inspection")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source_id):
        raise ValueError("source_not_found")
    def update() -> dict[str, Any]:
        db = load_store()
        sources = _source_store_list(db)
        source = next((item for item in sources if item.get("id") == source_id), None)
        if source is None:
            raise ValueError("source_not_found")
        slot = _source_effective_slot(source)
        source["inspection_enabled"] = enabled
        source["inspection_mode"] = "auto" if slot == "requirement_doc" else "manual"
        source["updated_at"] = timestamp()
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        _source_event("source_inspection_updated", source, db["revision"])
        return {"revision": db["revision"], "source": source}
    return with_lock(update)


def _apply_source_slot(member: dict[str, Any], previous_slot: str | None, slot: str | None) -> None:
    """Write a slot and keep the inspection defaults consistent with it."""
    member["slot"] = slot
    if slot == "requirement_doc":
        # Assigning a material to the requirement slot auto-includes it. A
        # version that was already a requirement doc keeps an explicit removal.
        if previous_slot != "requirement_doc" or "inspection_enabled" not in member:
            member["inspection_enabled"] = True
        member["inspection_mode"] = "auto"
    else:
        if member.get("inspection_mode") == "auto" and "inspection_enabled" in member:
            member["inspection_enabled"] = False
        member["inspection_mode"] = "manual"


def set_source_slot(source_id: str, slot: Any) -> dict[str, Any]:
    slot = _source_slot_value(slot)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source_id):
        raise ValueError("source_not_found")
    def update() -> dict[str, Any]:
        db = load_store()
        sources = _source_store_list(db)
        source = next((item for item in sources if item.get("id") == source_id), None)
        if source is None:
            raise ValueError("source_not_found")
        group_id = _source_group_id(source)
        members = [item for item in sources if _source_group_id(item) == group_id]
        at = timestamp()
        for member in members:
            _apply_source_slot(member, _source_effective_slot(member), slot)
            member["group_id"] = group_id
            member["updated_at"] = at
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        _source_event("source_slot_updated", source, db["revision"])
        return {"revision": db["revision"], "source": source}
    return with_lock(update)


def set_source_location(source_id: str, scope: str, iteration_id: str | None = None, slot: str | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source_id):
        raise ValueError("source_not_found")
    if scope not in {"global", "iteration"}:
        raise ValueError("invalid_source_scope")
    if scope == "iteration" and (not iteration_id or not isinstance(iteration_id, str) or not iteration_id.strip()):
        raise ValueError("missing_iteration_id")
    slot_provided = slot is not None
    clean_slot = _source_slot_value(slot) if slot_provided else None
    if slot_provided and clean_slot is None and slot != "":
        raise ValueError("invalid_source_slot")
    if slot_provided:
        _validate_scope_slot(scope, clean_slot)

    def update() -> dict[str, Any]:
        db = load_store()
        sources = _source_store_list(db)
        source = next((item for item in sources if item.get("id") == source_id), None)
        if source is None:
            raise ValueError("source_not_found")
        target_iteration_id = None
        if scope == "iteration":
            it = _find_iteration(db, iteration_id.strip())
            if not it:
                raise ValueError("iteration_not_found")
            target_iteration_id = it["id"]
        # Omitting the slot preserves the current one. A cross-scope move that
        # would keep an invalid fixed slot is rejected instead of silently
        # clearing the group's slot.
        target_slot = clean_slot if slot_provided else _source_effective_slot(source)
        _validate_scope_slot(scope, target_slot)
        # A material group shares one location. Moving one row moves every
        # version so fixed-slot history cannot leak across versions.
        group_id = _source_group_id(source)
        members = [item for item in sources if _source_group_id(item) == group_id]
        at = timestamp()
        for member in members:
            previous_slot = _source_effective_slot(member)
            member["scope"] = scope
            member["iteration_id"] = target_iteration_id
            _apply_source_slot(member, previous_slot, target_slot)
            member["updated_at"] = at
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        _source_event("source_location_updated", source, db["revision"])
        return {"revision": db["revision"], "source": source, "group_size": len(members), "slot": target_slot}

    return with_lock(update)


def set_source_group(source_id: str, group_id: Any, version_label: Any = None) -> dict[str, Any]:
    group_id = _source_group_key(group_id)
    version_label = _source_version_label(version_label)
    if not group_id:
        raise ValueError("invalid_source_group")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source_id):
        raise ValueError("source_not_found")
    def update() -> dict[str, Any]:
        db = load_store()
        sources = _source_store_list(db)
        source = next((item for item in sources if item.get("id") == source_id), None)
        if source is None:
            raise ValueError("source_not_found")
        source["group_id"] = group_id
        source["updated_at"] = timestamp()
        if version_label is not None:
            source["version_label"] = version_label
        members = [item for item in sources if _source_group_id(item) == group_id]
        ordered = sorted(members, key=lambda item: (str(item.get("added_at") or ""), str(item.get("id") or "")))
        for index, member in enumerate(ordered, 1):
            member["version_no"] = index
        db["revision"] = int(db.get("revision", 0)) + 1
        atomic_write(db)
        _source_event("source_group_updated", source, db["revision"])
        return {"revision": db["revision"], "source": source}
    return with_lock(update)


def source_inspection_selection() -> list[dict[str, Any]]:
    """Return the source material that a patrol/inspection run may read.

    Requirement documents contribute only their latest and previous active
    versions. Every other source participates only when explicitly enabled.
    Removed sources never participate.
    """
    all_sources = _source_store_list(load_store())
    _, _, active_by_group = _source_version_maps(all_sources)
    selected_ids: set[int] = set()
    for active in active_by_group.values():
        for rank, source in enumerate(active):
            slot = _source_effective_slot(source)
            stored = source.get("inspection_enabled")
            enabled = bool(stored) if isinstance(stored, bool) else slot == "requirement_doc"
            if not enabled:
                continue
            if slot == "requirement_doc" and rank > 1:
                continue
            selected_ids.add(id(source))
    selected = [item for item in all_sources if id(item) in selected_ids]
    enriched = enrich_source_records(selected, all_sources)
    slot_order = {slot: index for index, slot in enumerate(SOURCE_SLOTS)}
    enriched.sort(key=lambda item: (slot_order.get(item.get("slot"), len(SOURCE_SLOTS)), item.get("group_id", ""), -int(item.get("version_no", 0))))
    return enriched


# Explicit aliases keep the "selection point" easy to find for patrol adapters.
inspection_sources = source_inspection_selection


def import_source_seed(path: Path) -> dict[str, Any]:
    seed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(seed, dict) or seed.get("schema_version") != 1 or not isinstance(seed.get("sources"), list) or len(seed["sources"]) > 100:
        raise ValueError("invalid_source_seed")
    prepared = []
    for raw in seed["sources"]:
        if not isinstance(raw, dict):
            raise ValueError("invalid_source_seed")
        source_id = raw.get("id")
        name = raw.get("name")
        category = raw.get("category", "需求资料")
        url = raw.get("url")
        if not all(isinstance(value, str) for value in (source_id, name, category, url)) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source_id):
            raise ValueError("invalid_source_seed")
        if not isinstance(raw.get("active", True), bool) or ("primary" in raw and not isinstance(raw["primary"], bool)):
            raise ValueError("invalid_source_seed")
        purpose = raw.get("purpose", "")
        verification_state = raw.get("verification_state", "not_checked")
        if not isinstance(purpose, str) or len(purpose) > 4000 or not isinstance(verification_state, str) or len(verification_state) > 100:
            raise ValueError("invalid_source_seed")
        slot = _source_slot_value(raw.get("slot"))
        group_id = _source_group_key(raw.get("group_id")) or source_id
        version_label = _source_version_label(raw.get("version_label"))
        version_no = raw.get("version_no")
        if version_no is not None and (not isinstance(version_no, int) or isinstance(version_no, bool) or version_no < 1):
            raise ValueError("invalid_source_seed")
        inspection_enabled = raw.get("inspection_enabled")
        if inspection_enabled is not None and not isinstance(inspection_enabled, bool):
            raise ValueError("invalid_source_seed")
        inspection_mode = raw.get("inspection_mode")
        if inspection_mode is not None and inspection_mode not in SOURCE_INSPECTION_MODES:
            raise ValueError("invalid_source_seed")
        original, normalized = normalize_source_url(url)
        incoming = {
            "id": source_id, "type": "link", "name": name[:200], "url": original,
            "normalized_url": normalized, "category": category[:80], "purpose": purpose,
            "primary": raw.get("primary", False), "verification_state": verification_state,
            "active": raw.get("active", True), "slot": slot, "group_id": group_id,
            "inspection_enabled": inspection_enabled if inspection_enabled is not None else slot == "requirement_doc",
            "inspection_mode": inspection_mode or ("auto" if slot == "requirement_doc" else "manual"),
        }
        if version_label is not None:
            incoming["version_label"] = version_label
        if version_no is not None:
            incoming["version_no"] = version_no
        prepared.append(incoming)
    def update() -> dict[str, Any]:
        db = load_store()
        sources = _source_store_list(db)
        imported, updated, skipped = [], [], []
        at = timestamp()
        for incoming in prepared:
            old = next((item for item in sources if item.get("id") == incoming["id"]), None)
            if old:
                was_active = bool(old.get("active"))
                added_at = old.get("added_at", at)
                refreshed = dict(incoming)
                # Reimporting a seed refreshes its metadata but never silently restores a removed source.
                refreshed["active"] = was_active
                refreshed["status"] = "registered" if was_active else "removed"
                refreshed["read_status"] = old.get("read_status", "not_read")
                refreshed["origin"] = old.get("origin", "user_seed")
                refreshed["added_at"] = added_at
                for lifecycle_key in ("lifecycle_status", "suspected_deprecated", "lifecycle_override", "lifecycle_updated_at", "last_verified_at", "suspected_at", "deprecated_at", "lifecycle_reason"):
                    if lifecycle_key in old: refreshed[lifecycle_key] = old[lifecycle_key]
                for read_key in ("last_read_at", "read_state", "read_scope", "content_updated_at", "read_gap", "snapshot_revision", "content_hash", "unchanged_since", "last_successful_read_at", "last_recheck_at"):
                    if read_key in old: refreshed[read_key] = old[read_key]
                # Preserve user-assigned library metadata and version identity
                # across seed reimports; the seed only refreshes basic metadata.
                for library_key in ("slot", "group_id", "version_no", "version_label", "inspection_enabled", "inspection_mode",
                                    "last_inspection_attempt_at", "last_inspection_success_at", "last_inspection_status",
                                    "file_ext", "mime", "logo_kind"):
                    if library_key in old: refreshed[library_key] = old[library_key]
                if old.get("overrides"):
                    refreshed["overrides"] = old["overrides"]
                    refreshed.update(refreshed["overrides"])
                if any(old.get(key) != value for key, value in refreshed.items()):
                    refreshed["updated_at"] = at
                    old.update(refreshed)
                    updated.append(old["id"])
                continue
            duplicate = next((item for item in sources if item.get("normalized_url") == incoming["normalized_url"]), None)
            if duplicate:
                skipped.append({"id": incoming["id"], "reason": "duplicate_url", "existing_id": duplicate["id"]})
                continue
            source = {**incoming, "status": "registered" if incoming["active"] else "removed", "read_status": "not_read", "added_at": at, "updated_at": at, "origin": "user_seed"}
            source.update({"lifecycle_status": "registered", "suspected_deprecated": False, "lifecycle_override": None, "lifecycle_updated_at": at, "last_verified_at": None, "suspected_at": None, "deprecated_at": None, "lifecycle_reason": None})
            sources.append(source)
            imported.append(source["id"])
        db["sources"] = sources
        changed = bool(imported or updated)
        if changed:
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db)
            by_id = {item["id"]: item for item in sources}
            for source_id in imported:
                _source_event("source_added", by_id[source_id], db["revision"])
            for source_id in updated:
                _source_event("source_seed_refreshed", by_id[source_id], db["revision"])
        return {"revision": db.get("revision", 0), "imported": imported, "updated": updated, "skipped": skipped}
    return with_lock(update)


def cmd_sources_list(args: argparse.Namespace) -> None:
    manifest = source_manifest(args.include_removed)
    for source in manifest["sources"]:
        if source.get("type") == "file":
            file_id = source.get("managed_file_id", "")
            if re.fullmatch(r"[0-9a-fA-F-]{36}\.[a-z0-9]+", file_id):
                path = UPLOADS / file_id
                source["hosted_path"] = str(path) if path.is_file() else None
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def cmd_source_add_link(args: argparse.Namespace) -> None:
    print(json.dumps(add_source_link(args.url, args.name, args.category, args.origin, purpose=args.purpose, primary=args.primary,
                                     slot=getattr(args, "slot", None), group_id=getattr(args, "group_id", None),
                                     version_label=getattr(args, "version_label", None),
                                     scope=getattr(args, "scope", None),
                                     iteration_id=getattr(args, "iteration_id", None)), ensure_ascii=False, indent=2))


def cmd_source_add_file(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser()
    if not path.is_file() or path.stat().st_size > MAX_SOURCE_FILE_BYTES:
        raise ValueError("source_file_size_invalid")
    data = path.read_bytes()
    print(json.dumps(add_source_file(path.name, data, args.category, args.origin, name=getattr(args, "name", None),
                                     slot=getattr(args, "slot", None), group_id=getattr(args, "group_id", None),
                                     version_label=getattr(args, "version_label", None),
                                     scope=getattr(args, "scope", None),
                                     iteration_id=getattr(args, "iteration_id", None)), ensure_ascii=False, indent=2))


def cmd_source_relocate(args: argparse.Namespace) -> None:
    print(json.dumps(set_source_location(
        args.id,
        scope=args.scope,
        iteration_id=getattr(args, "iteration_id", None),
        slot=getattr(args, "slot", None)
    ), ensure_ascii=False, indent=2))


def cmd_iteration(args: argparse.Namespace) -> None:
    action = args.iteration_action
    if action == "list":
        print(json.dumps(list_iterations(), ensure_ascii=False, indent=2))
    elif action == "create":
        print(json.dumps(create_iteration(
            args.version_tag, args.title, status=args.status,
            target_release_date=args.target_release_date,
            description=args.description or "",
            iteration_id=args.iteration_id,
            order=getattr(args, "order", None)
        ), ensure_ascii=False, indent=2))
    elif action == "show":
        db = load_store()
        it = _find_iteration(db, args.id)
        if not it:
            raise SystemExit(f"No iteration with id: {args.id}")
        print(json.dumps(it, ensure_ascii=False, indent=2))
    elif action == "switch":
        print(json.dumps(set_current_iteration(args.id), ensure_ascii=False, indent=2))
    elif action == "release":
        notes = [args.note] if args.note else []
        print(json.dumps(release_iteration(args.id, notes=notes), ensure_ascii=False, indent=2))
    elif action == "update":
        patch = {}
        if getattr(args, "title", None): patch["title"] = args.title
        if getattr(args, "version_tag", None): patch["version_tag"] = args.version_tag
        if getattr(args, "description", None): patch["description"] = args.description
        if getattr(args, "target_release_date", None): patch["target_release_date"] = args.target_release_date
        if getattr(args, "status", None): patch["status"] = args.status
        print(json.dumps(update_iteration(args.id, patch), ensure_ascii=False, indent=2))
    elif action == "assign-record":
        print(json.dumps(assign_record_iteration(args.record_id, args.iteration_id or None), ensure_ascii=False, indent=2))
    elif action == "reorder":
        print(json.dumps(reorder_iterations(args.ids), ensure_ascii=False, indent=2))
    elif action == "migrate":
        tag = getattr(args, "version_tag", None) or "v13.4.2"
        title = getattr(args, "title", None) or "13.4.2 智能相册与AI音乐优化"
        print(json.dumps(migrate_iteration_archive(default_tag=tag, default_title=title), ensure_ascii=False, indent=2))


def cmd_reopen(args: argparse.Namespace) -> None:
    evidence = args.evidence or []
    print(json.dumps(reopen_record(
        args.id, args.reason, audit_mode=args.audit_mode,
        actor=args.actor, actor_role=args.actor_role,
        evidence=evidence, expected_store_revision=args.expected_store_revision
    ), ensure_ascii=False, indent=2))


def cmd_archive_context(args: argparse.Namespace) -> None:
    res = assemble_archive_context(getattr(args, "iteration_id", None), getattr(args, "task_id", None))
    if getattr(args, "markdown", False):
        print(res["summary_markdown"])
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_record_attachment(args: argparse.Namespace) -> None:
    path = Path(args.file).expanduser()
    if not path.is_file():
        raise SystemExit(f"File not found: {args.file}")
    data = path.read_bytes()
    print(json.dumps(add_record_attachment(
        args.id, path.name, data, summary=args.summary, actor=args.actor
    ), ensure_ascii=False, indent=2))


def cmd_sources_inspection(args: argparse.Namespace) -> None:
    print(json.dumps({"sources": source_inspection_selection()}, ensure_ascii=False, indent=2))


def cmd_source_group(args: argparse.Namespace) -> None:
    print(json.dumps(set_source_group(args.id, args.group_id, args.version_label), ensure_ascii=False, indent=2))


def cmd_source_remove(args: argparse.Namespace) -> None:
    print(json.dumps(change_source_state(args.id, False), ensure_ascii=False, indent=2))


def cmd_source_restore(args: argparse.Namespace) -> None:
    print(json.dumps(change_source_state(args.id, True), ensure_ascii=False, indent=2))


def cmd_source_lifecycle(args: argparse.Namespace) -> None:
    fields = {}
    if args.status is not None: fields["lifecycle_status"] = args.status
    if args.reason is not None: fields["lifecycle_reason"] = args.reason
    if args.last_verified_at is not None: fields["last_verified_at"] = args.last_verified_at
    if args.override is not None: fields["lifecycle_override"] = args.override
    if args.clear_override: fields["lifecycle_override"] = None
    print(json.dumps(set_source_lifecycle(args.id, fields), ensure_ascii=False, indent=2))


def cmd_sources_import_seed(args: argparse.Namespace) -> None:
    print(json.dumps(import_source_seed(Path(args.input).expanduser().resolve()), ensure_ascii=False, indent=2))


def cmd_finish(args: argparse.Namespace) -> None:
    print(json.dumps(finish_followup(args.id, args.result, args.outcome), ensure_ascii=False, indent=2))


def cmd_recover(args: argparse.Namespace) -> None:
    # Explicit recovery only: make abandoned claims visible and claimable again.
    def update() -> dict[str, Any]:
        db = load_store()
        reset = []
        for record in db["records"]:
            for item in record.get("followups", []):
                if item.get("status") == "claimed" and (not args.older_than or item.get("claimed_at", "") < args.older_than):
                    item.update({"status": "pending", "recovered_at": timestamp()})
                    item.pop("claimed_by", None)
                    item.pop("claimed_at", None)
                    reset.append(item["id"])
                    event("followup_recovered", record, int(db.get("revision", 0)) + 1, item["id"])
        if reset:
            db["revision"] = int(db.get("revision", 0)) + 1
            atomic_write(db)
        return {"revision": db.get("revision", 0), "recovered": reset}
    print(json.dumps(with_lock(update), ensure_ascii=False, indent=2))


def cmd_dispatch_card(args: argparse.Namespace) -> None:
    print(json.dumps(dispatch_card(args.id), ensure_ascii=False, indent=2))


def cmd_bind_agent(args: argparse.Namespace) -> None:
    print(json.dumps(bind_agent(args.id, args.role, args.actor, session_id=args.session_id,
                                channel=args.channel, expected_revision=args.expected_revision),
                     ensure_ascii=False, indent=2))


def cmd_agent(args: argparse.Namespace) -> None:
    if args.agent_command == "claim": result = agent_claim(args.id, args.expected_revision, agent_name=args.agent_name)
    elif args.agent_command == "heartbeat": result = agent_heartbeat(args.id, args.expected_revision)
    elif args.agent_command == "update":
        detail = Path(args.context_file).read_text(encoding="utf-8") if args.context_file else None
        message = args.message or _brief_from_context(detail or "")
        result = agent_update(args.id, args.expected_revision, message, agent_name=args.agent_name, status=args.status,
                              phase=args.phase, context_markdown=detail, evidence=args.evidence)
    elif args.agent_command == "report":
        if not args.context_file and not args.message: raise ValueError("invalid_progress")
        detail = Path(args.context_file).read_text(encoding="utf-8") if args.context_file else None
        message = args.message or _brief_from_context(detail or "")
        result = agent_report(args.id, args.expected_revision, message, status=args.status, phase=args.phase, context_markdown=detail)
    else: result = agent_release(args.id, args.expected_revision)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_bug_evidence(args: argparse.Namespace) -> None:
    payload = json.load(sys.stdin if args.input == "-" else open(args.input, encoding="utf-8"))
    record_id = payload.get("record_id")
    if not isinstance(record_id, str) or not re.fullmatch(r"BUG\d{14}", record_id): raise ValueError("invalid_bug_id")
    result = set_bug_evidence(record_id, payload.get("bug_detail", {}), payload.get("source_attachments", []))
    print(json.dumps({"ok": True, "revision": result["revision"]}, ensure_ascii=False))


def cmd_migrate(args: argparse.Namespace) -> None:
    plan = with_lock(lambda: backend.migration_plan(ROOT, STORE))
    if args.dry_run:
        print(json.dumps({"dry_run": True, **plan}, ensure_ascii=False, indent=2)); return
    print(json.dumps(with_lock(lambda: backend.migrate(ROOT, STORE)), ensure_ascii=False, indent=2))


def cmd_init(args: argparse.Namespace) -> None:
    """Create an empty private data store; never import another directory implicitly."""
    refresh_data_paths()
    if ROOT.is_symlink(): raise ValueError("data_root_symlink_not_allowed")
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ROOT, 0o700)
    with_lock(lambda: backend.commit(ROOT, STORE, backend.load(ROOT, STORE)))
    catalog = ROOT / "scope-catalog.json"
    if not catalog.exists():
        raw = json.dumps({"version": 1, "revision": 0, "updated_at": None, "scopes": []}, ensure_ascii=False, indent=2) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=".scope-catalog.", dir=ROOT)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp_name, catalog)
        finally:
            if os.path.exists(temp_name): os.unlink(temp_name)
    print(json.dumps({"ok": True, "data_dir": str(ROOT), "initialized": True, "legacy_imported": False}, ensure_ascii=False))


def cmd_validate_data_dir(args: argparse.Namespace) -> None:
    print(json.dumps({"ok": True, "data_dir": str(waylume_paths.validate_data_dir(args.path))}, ensure_ascii=False))


def cmd_rebuild_context(args: argparse.Namespace) -> None:
    """Regenerate derived Markdown from current structured record data."""
    print(json.dumps(with_lock(lambda: backend.rebuild_context(ROOT, STORE)), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="dashboardctl", description="Manage dashboard records locally")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="initialize an empty user data directory without importing personal data")
    init.set_defaults(func=cmd_init)
    validate = sub.add_parser("validate-data-dir", help="validate a private data path before creating files")
    validate.add_argument("path")
    validate.set_defaults(func=cmd_validate_data_dir)
    migrate = sub.add_parser("migrate", help="inspect or explicitly migrate legacy store.json")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.set_defaults(func=cmd_migrate)
    rebuild = sub.add_parser("rebuild-context", help="safely regenerate all derived record Markdown")
    rebuild.set_defaults(func=cmd_rebuild_context)
    for action in ("claim", "heartbeat", "report", "release", "update"):
        command = sub.add_parser("agent-" + action)
        command.add_argument("id"); command.add_argument("--expected-revision", type=int, required=True)
        if action in {"claim", "update"}: command.add_argument("--agent-name")
        if action in {"report", "update"}:
            command.add_argument("--message"); command.add_argument("--context-file"); command.add_argument("--status", choices=sorted(VALID_STATUSES)); command.add_argument("--phase")
            command.add_argument("--evidence", action="append", help="evidence reference; repeat as needed")
        command.set_defaults(func=cmd_agent, agent_command=action)
    upsert = sub.add_parser("upsert", help="create or update one record from JSON")
    upsert.add_argument("--input", default="-", help="JSON file path, or - for stdin")
    upsert.add_argument("--expected-store-revision", type=int,
                        help="compare-and-swap against the dashboard-wide revision while holding the store lock")
    upsert.set_defaults(func=cmd_upsert)
    handoff_cmd = sub.add_parser("handoff-update", help="CAS-update handoff fields without changing status or lease")
    handoff_cmd.add_argument("id"); handoff_cmd.add_argument("--expected-revision", type=int, required=True)
    handoff_cmd.add_argument("--input", default="-", help="JSON object or - for stdin")
    handoff_cmd.set_defaults(func=cmd_handoff_update)
    workflow_cmd = sub.add_parser("workflow", help="inspect and drive the six-stage workflow pipeline")
    workflow_sub = workflow_cmd.add_subparsers(dest="workflow_command", required=True)
    workflow_templates_cmd = workflow_sub.add_parser("templates", help="list built-in workflow templates")
    workflow_templates_cmd.set_defaults(func=cmd_workflow)
    workflow_show_cmd = workflow_sub.add_parser("show", help="show one record's workflow detail and template")
    workflow_show_cmd.add_argument("id")
    workflow_show_cmd.set_defaults(func=cmd_workflow)

    def add_workflow_write(name: str, help_text: str):
        command = workflow_sub.add_parser(name, help=help_text)
        command.add_argument("id")
        command.add_argument("--actor-role", required=True, choices=sorted(workflow.ROLES))
        command.add_argument("--actor", required=True)
        command.add_argument("--note")
        command.add_argument("--evidence", action="append", help="evidence reference; repeat as needed")
        command.add_argument("--expected-revision", type=int)
        command.set_defaults(func=cmd_workflow)
        return command

    workflow_start_cmd = add_workflow_write("start", "start the pipeline template on one record")
    workflow_start_cmd.add_argument("--template", help="template id; defaults by record type (task/work: dev-standard, bug: bug-standard)")
    workflow_start_cmd.add_argument("--stage", help="explicit starting stage for legacy records; defaults from the current status")
    workflow_submit_cmd = add_workflow_write("submit", "record the current stage outputs, note and evidence")
    workflow_submit_cmd.add_argument("--output", action="append", help="key=value output for the current stage; repeat as needed")
    workflow_advance_cmd = add_workflow_write("advance", "advance to the next stage once required outputs are complete")
    workflow_advance_cmd.add_argument("--to", help="optional target stage; must equal the current stage's next stage")
    workflow_rework_cmd = add_workflow_write("rework", "send the record back to one allowed rework stage")
    workflow_rework_cmd.add_argument("--to", required=True, help="target rework stage")
    add_workflow_write("block", "mark the pipeline blocked while keeping the current stage")
    add_workflow_write("unblock", "clear a pipeline block and restore the mapped status")
    add_workflow_write("close", "close a delivery-stage pipeline and complete the record")
    add_workflow_write("claim", "lock the current stage for this actor to prevent concurrent work")
    add_workflow_write("heartbeat", "renew the current stage lock")
    add_workflow_write("release", "release the current stage lock")
    dispatch_card_cmd = sub.add_parser("dispatch-card", help="read the mandatory pre-dispatch card for one task")
    dispatch_card_cmd.add_argument("id")
    dispatch_card_cmd.set_defaults(func=cmd_dispatch_card)
    bind_agent_cmd = sub.add_parser("bind-agent", help="bind a task role to its fixed agent session")
    bind_agent_cmd.add_argument("id")
    bind_agent_cmd.add_argument("--role", required=True, choices=("dev", "auditor", "pm"))
    bind_agent_cmd.add_argument("--actor", required=True)
    bind_agent_cmd.add_argument("--session-id")
    bind_agent_cmd.add_argument("--channel", help="host-defined opaque transport label")
    bind_agent_cmd.add_argument("--expected-revision", type=int)
    bind_agent_cmd.set_defaults(func=cmd_bind_agent)
    scope_upsert = sub.add_parser("scope-upsert", help="CAS-upsert one scope in the local responsibility catalog")
    scope_upsert.add_argument("--input", default="-", help="one scope JSON object, file path, or - for stdin")
    scope_upsert.add_argument("--expected-catalog-revision", type=int, required=True)
    scope_upsert.set_defaults(func=cmd_scope_upsert)
    listing = sub.add_parser("list", help="list records")
    listing.add_argument("--type", choices=sorted(VALID_TYPES))
    listing.add_argument("--include-archived", action="store_true")
    listing.set_defaults(func=cmd_list)
    show = sub.add_parser("show", help="show one record")
    show.add_argument("id")
    show.set_defaults(func=cmd_show)
    batch_init = sub.add_parser("batch-init", help="adopt one existing task into the current task batch")
    batch_init.add_argument("--task-id", required=True)
    batch_init.set_defaults(func=cmd_batch_init)
    task_context_cmd = sub.add_parser("task-context", help="read one task's initial request, current brief, progress and result")
    task_context_cmd.add_argument("id")
    task_context_cmd.set_defaults(func=cmd_task_context)
    evidence_cmd = sub.add_parser("bug-evidence", help="update one BUG detail and source attachment manifest")
    evidence_cmd.add_argument("--input", default="-", help="JSON input path or stdin")
    evidence_cmd.set_defaults(func=cmd_bug_evidence)
    progress_cmd = sub.add_parser("progress", help="append an independent progress entry to a record")
    progress_cmd.add_argument("id")
    progress_cmd.add_argument("--message", required=True)
    progress_cmd.add_argument("--actor", default="agent")
    progress_cmd.add_argument("--evidence", action="append", help="evidence reference; repeat as needed")
    progress_cmd.set_defaults(func=cmd_progress)
    task_result_cmd = sub.add_parser("task-result", help="append and retain a final task result brief")
    task_result_cmd.add_argument("id")
    task_result_cmd.add_argument("--message", required=True)
    task_result_cmd.add_argument("--actor", default="agent")
    task_result_cmd.add_argument("--outcome", choices=("success", "failure", "partial", "cancelled"))
    task_result_cmd.add_argument("--evidence", action="append", help="evidence reference; repeat as needed")
    task_result_cmd.set_defaults(func=cmd_task_result)
    archive = sub.add_parser("archive", help="archive one record")
    archive.add_argument("id")
    archive.add_argument("--reason")
    archive.set_defaults(func=cmd_archive)
    purge = sub.add_parser("purge-test-fixture", help="remove the one explicitly allowlisted test fixture")
    purge.add_argument("id")
    purge.set_defaults(func=cmd_purge_fixture)
    bootstrap = sub.add_parser("bootstrap", help="one-time idempotent requirements import")
    bootstrap.add_argument("--requirements", required=True, help="explicitly selected requirements export; no personal ledger is imported by default")
    bootstrap.set_defaults(func=cmd_bootstrap)
    sync = sub.add_parser("sync-bugs", help="sync BUGs from the allowlisted local state snapshot")
    sync.add_argument("--state", required=True, help="explicit BUG snapshot path; no personal patrol state is imported by default")
    sync.set_defaults(func=cmd_sync_bugs)
    complete = sub.add_parser("complete", help="set or clear the user's manual completion marker")
    complete.add_argument("id")
    complete.add_argument("--completed", action=argparse.BooleanOptionalAction, default=True)
    complete.set_defaults(func=cmd_complete)
    add_followup = sub.add_parser("add-followup", help="append a user follow-up prompt to a record")
    add_followup.add_argument("id")
    add_followup.add_argument("prompt")
    add_followup.set_defaults(func=cmd_add_followup)
    pending = sub.add_parser("pending-followups", help="list pending follow-up prompts")
    pending.set_defaults(func=cmd_pending)
    claim = sub.add_parser("claim-followups", help="atomically claim pending prompts")
    claim.add_argument("--ids", help="comma-separated follow-up IDs")
    claim.add_argument("--limit", type=int, default=20)
    claim.add_argument("--claimed-by", default="agent")
    claim.set_defaults(func=cmd_claim)
    finish = sub.add_parser("finish-followup", help="mark an attempted claimed follow-up handled, including failures")
    finish.add_argument("id")
    finish.add_argument("--outcome", choices=("success", "failure"), required=True)
    finish.add_argument("--result", required=True)
    finish.set_defaults(func=cmd_finish)
    recover = sub.add_parser("recover-followups", help="explicitly return abandoned claimed prompts to pending")
    recover.add_argument("--older-than", help="only recover claims with claimed_at earlier than this ISO timestamp")
    recover.set_defaults(func=cmd_recover)
    sources = sub.add_parser("sources-list", help="list registered requirement reference sources")
    sources.add_argument("--include-removed", action="store_true")
    sources.set_defaults(func=cmd_sources_list)
    sources_inspection = sub.add_parser("sources-inspection", help="list only sources eligible for automatic inspection")
    sources_inspection.set_defaults(func=cmd_sources_inspection)
    source_link = sub.add_parser("source-add-link", help="register a user-provided reference URL")
    source_link.add_argument("--url", required=True)
    source_link.add_argument("--name")
    source_link.add_argument("--category", default="需求资料")
    source_link.add_argument("--purpose")
    source_link.add_argument("--primary", action=argparse.BooleanOptionalAction, default=None)
    source_link.add_argument("--origin", choices=("dashboard", "user_chat_link", "agent_import"), default="agent_import")
    source_link.add_argument("--slot", choices=SOURCE_SLOTS)
    source_link.add_argument("--group-id")
    source_link.add_argument("--version-label")
    source_link.add_argument("--scope", choices=("global", "iteration"))
    source_link.add_argument("--iteration-id")
    source_link.set_defaults(func=cmd_source_add_link)
    source_file = sub.add_parser("source-add-file", help="copy a permitted file into managed source storage")
    source_file.add_argument("path")
    source_file.add_argument("--name")
    source_file.add_argument("--category", default="需求资料")
    source_file.add_argument("--origin", choices=("dashboard", "user_chat_attachment", "agent_import"), default="agent_import")
    source_file.add_argument("--slot", choices=SOURCE_SLOTS)
    source_file.add_argument("--group-id")
    source_file.add_argument("--version-label")
    source_file.add_argument("--scope", choices=("global", "iteration"))
    source_file.add_argument("--iteration-id")
    source_file.set_defaults(func=cmd_source_add_file)
    source_remove = sub.add_parser("source-remove", help="soft-remove one reference source")
    source_remove.add_argument("id")
    source_remove.set_defaults(func=cmd_source_remove)
    source_restore = sub.add_parser("source-restore", help="restore a soft-removed reference source")
    source_restore.add_argument("id")
    source_restore.set_defaults(func=cmd_source_restore)
    source_group = sub.add_parser("source-group", help="assign a source to a version group")
    source_group.add_argument("id")
    source_group.add_argument("--group-id", required=True)
    source_group.add_argument("--version-label")
    source_group.set_defaults(func=cmd_source_group)
    source_lifecycle = sub.add_parser("source-lifecycle", help="update a source verification lifecycle")
    source_lifecycle.add_argument("id")
    source_lifecycle.add_argument("--status", choices=("registered", "suspected_deprecated", "deprecated"))
    source_lifecycle.add_argument("--reason")
    source_lifecycle.add_argument("--last-verified-at")
    source_lifecycle.add_argument("--override", choices=("active", "deprecated"))
    source_lifecycle.add_argument("--clear-override", action="store_true")
    source_lifecycle.set_defaults(func=cmd_source_lifecycle)
    source_relocate = sub.add_parser("source-relocate", help="relocate source to scope, iteration and slot")
    source_relocate.add_argument("id")
    source_relocate.add_argument("--scope", choices=("global", "iteration"), required=True)
    source_relocate.add_argument("--iteration", dest="iteration_id")
    source_relocate.add_argument("--slot", choices=SOURCE_SLOTS)
    source_relocate.set_defaults(func=cmd_source_relocate)
    seed = sub.add_parser("sources-import-seed", help="idempotently import user-approved reference sources")
    seed.add_argument("--input", required=True)
    seed.set_defaults(func=cmd_sources_import_seed)

    # Iterations management
    iteration_cmd = sub.add_parser("iteration", help="manage versions and iterations")
    iter_sub = iteration_cmd.add_subparsers(dest="iteration_action", required=True)
    iter_list = iter_sub.add_parser("list", help="list all iterations")
    iter_create = iter_sub.add_parser("create", help="create a new iteration")
    iter_create.add_argument("--version-tag", required=True, help="version tag (e.g. v13.4.2)")
    iter_create.add_argument("--title", required=True, help="iteration title")
    iter_create.add_argument("--status", choices=sorted(ITERATION_STATUSES), default="active")
    iter_create.add_argument("--target-release-date", help="target release date (YYYY-MM-DD)")
    iter_create.add_argument("--description", default="")
    iter_create.add_argument("--iteration-id", help="optional custom iteration ID")
    iter_create.add_argument("--order", type=int, help="insert position in ascending order (0=oldest, default=newest)")
    iter_show = iter_sub.add_parser("show", help="show one iteration")
    iter_show.add_argument("id")
    iter_switch = iter_sub.add_parser("switch", help="set current active iteration")
    iter_switch.add_argument("id")
    iter_release = iter_sub.add_parser("release", help="mark an iteration as released")
    iter_release.add_argument("id")
    iter_release.add_argument("--note", help="optional release note")
    iter_update = iter_sub.add_parser("update", help="update iteration metadata")
    iter_update.add_argument("id")
    iter_update.add_argument("--title")
    iter_update.add_argument("--version-tag")
    iter_update.add_argument("--description")
    iter_update.add_argument("--target-release-date")
    iter_update.add_argument("--status", choices=sorted(ITERATION_STATUSES))
    iter_assign = iter_sub.add_parser("assign-record", help="assign record to iteration")
    iter_assign.add_argument("record_id")
    iter_assign.add_argument("iteration_id")
    iter_reorder = iter_sub.add_parser("reorder", help="persist display order; pass IDs newest first")
    iter_reorder.add_argument("ids", nargs="+")
    iter_migrate = iter_sub.add_parser("migrate", help="migrate legacy records and sources into default iteration archive")
    iter_migrate.add_argument("--version-tag", default="v13.4.2", help="default iteration version tag")
    iter_migrate.add_argument("--title", default="13.4.2 智能相册与AI音乐优化", help="default iteration title")
    iteration_cmd.set_defaults(func=cmd_iteration)

    # Reopen record
    reopen_cmd = sub.add_parser("reopen", help="reopen a completed record for modifications before iteration release")
    reopen_cmd.add_argument("id", help="record ID")
    reopen_cmd.add_argument("--reason", required=True, help="reason for reopening/modifying")
    reopen_cmd.add_argument("--audit-mode", choices=("lightweight", "full"), default="lightweight")
    reopen_cmd.add_argument("--actor", default="agent")
    reopen_cmd.add_argument("--actor-role", default="lead")
    reopen_cmd.add_argument("--evidence", action="append", help="evidence reference")
    reopen_cmd.add_argument("--expected-store-revision", type=int)
    reopen_cmd.set_defaults(func=cmd_reopen)

    # Archive context assembly
    archive_ctx_cmd = sub.add_parser("archive-context", help="assemble context from global materials and iteration assets")
    archive_ctx_cmd.add_argument("--iteration", dest="iteration_id", help="iteration ID")
    archive_ctx_cmd.add_argument("--task", dest="task_id", help="optional task ID")
    archive_ctx_cmd.add_argument("--markdown", action="store_true", help="output rendered markdown summary directly")
    archive_ctx_cmd.set_defaults(func=cmd_archive_context)

    # Record attachment
    rec_att_cmd = sub.add_parser("record-attachment", help="add an attachment (screenshot/note) to any record")
    rec_att_cmd.add_argument("id", help="record ID")
    rec_att_cmd.add_argument("--file", required=True, help="path to image/document file")
    rec_att_cmd.add_argument("--summary", help="summary description of attachment (e.g. PM decision)")
    rec_att_cmd.add_argument("--actor", default="agent", help="uploader identity")
    rec_att_cmd.set_defaults(func=cmd_record_attachment)
    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        code = getattr(exc, "code", None)
        message = str(exc)
        if isinstance(code, str) and code and message != code:
            message = f"{code}: {message}"
        raise SystemExit(f"dashboardctl: {message}")


if __name__ == "__main__":
    main()
