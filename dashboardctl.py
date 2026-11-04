#!/usr/bin/env python3
"""Local CLI for the dashboard's structured, reviewable data store."""

from __future__ import annotations

import argparse
import base64
import binascii
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
    "in_progress", "awaiting_acceptance", "blocked", "completed", "partial",
    "pending", "unknown", "archived", "cancelled", "platform_testing", "submitted_for_testing",
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
    "latest_brief", "initial_brief", "phase", "progress", "final_result", "current_agent_session_id", "current_agent_name", "last_agent_session_id", "last_agent_name", "last_agent_activity_at", "agent_state", "revision", "handoff",
}
MAX_PROMPT_CHARS = 4000
MAX_RESULT_CHARS = 2000
MAX_SOURCE_FILE_BYTES = 15 * 1024 * 1024
ALLOWED_SOURCE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md", ".markdown", ".png", ".jpg", ".jpeg"}
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
    return activity > now - timedelta(minutes=10)


def effective_work_status(record: dict[str, Any], now=None) -> tuple[str, str]:
    if record.get("completion_kind") == "transferred" and record.get("transfer_disposition") == "transferred": return "completed", "transferred"
    if record.get("manual_status") == "completed": return "completed", "manual"
    if record.get("type") == "bug" and bug_has_our_delivery(record): return "completed", "our_status"
    status = str(record.get("status", "unknown"))
    if status in BATCH_TERMINAL_STATUSES: return status, "source_status"
    if has_fresh_agent_owner(record, now): return "in_progress", "agent_owner"
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
    if "source_row_keys" in record and (not isinstance(record["source_row_keys"], list) or any(not isinstance(value, str) for value in record["source_row_keys"])):
        raise ValueError("source_row_keys must be an array of strings")
    if "source_row_fingerprints" in record and (not isinstance(record["source_row_fingerprints"], dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in record["source_row_fingerprints"].items())):
        raise ValueError("source_row_fingerprints must be a string map")
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
        if status is not None: record["status"] = status
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
        if status is not None: record["status"] = status
        record["last_agent_activity_at"] = at; record["updated_at"] = at
        if status in BATCH_TERMINAL_STATUSES:
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
        entry = next((item for key in ("attachments", "source_attachments") for item in record.get(key, []) if item.get("filename") == filename), None)
        if record.get("type") != "bug" or not isinstance(entry, dict): raise ValueError("record_attachment_not_found")
        root = (ROOT / "records" / "bug" / record_id).resolve(); candidate = root / "attachments" / filename
        if candidate.is_symlink() or root not in candidate.resolve().parents or not candidate.is_file(): raise ValueError("record_attachment_not_found")
        return candidate.stat().st_size
    return with_lock(lookup)


def _read_record_attachment_locked(record_id: str, filename: str, byte_range: tuple[int, int] | None = None) -> tuple[bytes, str, dict[str, Any]]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", filename): raise ValueError("record_attachment_not_found")
    record = _find_record(load_store(), record_id)
    if record.get("type") != "bug": raise ValueError("record_attachment_not_found")
    entry = next((item for key in ("attachments", "source_attachments") for item in record.get(key, []) if item.get("filename") == filename), None)
    if not isinstance(entry, dict): raise ValueError("record_attachment_not_found")
    root = (ROOT / "records" / "bug" / record_id).resolve(); candidate = root / "attachments" / filename
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
    if record.get("type") != "bug": raise ValueError("record_attachment_not_found")
    entry = next((item for item in record.get("attachments", []) if item.get("filename") == filename), None)
    if not isinstance(entry, dict): raise ValueError("record_attachment_not_found")
    root = (ROOT / "records" / "bug" / record_id).resolve()
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
    successful = bool(snapshot_at) and same_round and isinstance(bugs, list) and not stale and attempt.get("result") == "succeeded" and attempt.get("stale") is False and attempt.get("filter") == "我负责的" and "matched self owner" in verification and "complete=true" in verification and "all pages returned" in verification and isinstance(total, int) and not isinstance(total, bool) and isinstance(unique_count, int) and not isinstance(unique_count, bool) and total == len(bugs) == len(set(ids)) == unique_count and isinstance(attempt.get("pages_read"), int) and not isinstance(attempt.get("pages_read"), bool) and attempt["pages_read"] > 0 and all(ids)

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
                    "current_agent_session_id", "last_agent_session_id", "last_agent_activity_at", "agent_state",
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
            elif old.get("type") == "bug" and old.get("id") not in incoming_ids and old.get("transfer_disposition") != "transferred" and old.get("status") != "archived":
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


def add_source_link(url: str, name: str | None = None, category: str = "需求资料", origin: str = "dashboard", *, source_id: str | None = None, purpose: str | None = None, primary: bool | None = None, verification_state: str | None = None) -> dict[str, Any]:
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
        existing = next((item for item in sources if item.get("id") == source["id"] or item.get("normalized_url") == normalized_url), None)
        if existing:
            if existing.get("id") != source["id"]:
                raise ValueError("source_duplicate")
            raise ValueError("source_duplicate")
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


def add_source_file(filename: str, data: bytes, category: str = "需求资料", origin: str = "dashboard") -> dict[str, Any]:
    extension, mime = validate_source_file(filename, data)
    if not isinstance(category, str) or not category.strip() or len(category) > 80:
        raise ValueError("invalid_source_category")
    if origin not in {"dashboard", "user_chat_attachment", "agent_import"}:
        raise ValueError("invalid_source_origin")
    managed_file_id = f"{uuid.uuid4()}{extension}"
    stored_path = UPLOADS / managed_file_id
    source = {
        "id": str(uuid.uuid4()), "type": "file", "name": Path(filename).name,
        "filename": Path(filename).name, "managed_file_id": managed_file_id,
        "category": category.strip(), "mime_type": mime, "size_bytes": len(data),
        "active": True, "status": "registered", "read_status": "not_read",
        "lifecycle_status": "registered", "suspected_deprecated": False, "lifecycle_override": None,
        "lifecycle_updated_at": timestamp(), "last_verified_at": None, "suspected_at": None, "deprecated_at": None, "lifecycle_reason": None,
        "added_at": timestamp(), "updated_at": timestamp(), "origin": origin,
    }
    def update() -> dict[str, Any]:
        UPLOADS.mkdir(parents=True, exist_ok=True)
        # A random UUID filename prevents path traversal and collisions.
        with stored_path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        committed = False
        try:
            db = load_store()
            sources = _source_store_list(db)
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


def source_manifest(include_removed: bool = False) -> dict[str, Any]:
    db = load_store()
    sources = _source_store_list(db)
    if not include_removed:
        sources = [item for item in sources if item.get("active")]
    return {"revision": db.get("revision", 0), "sources": sources}


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
        original, normalized = normalize_source_url(url)
        prepared.append({
            "id": source_id, "type": "link", "name": name[:200], "url": original,
            "normalized_url": normalized, "category": category[:80], "purpose": purpose,
            "primary": raw.get("primary", False), "verification_state": verification_state,
            "active": raw.get("active", True),
        })
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
    print(json.dumps(add_source_link(args.url, args.name, args.category, args.origin, purpose=args.purpose, primary=args.primary), ensure_ascii=False, indent=2))


def cmd_source_add_file(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser()
    if not path.is_file() or path.stat().st_size > MAX_SOURCE_FILE_BYTES:
        raise ValueError("source_file_size_invalid")
    data = path.read_bytes()
    print(json.dumps(add_source_file(path.name, data, args.category, args.origin), ensure_ascii=False, indent=2))


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
    source_link = sub.add_parser("source-add-link", help="register a user-provided reference URL")
    source_link.add_argument("--url", required=True)
    source_link.add_argument("--name")
    source_link.add_argument("--category", default="需求资料")
    source_link.add_argument("--purpose")
    source_link.add_argument("--primary", action=argparse.BooleanOptionalAction, default=None)
    source_link.add_argument("--origin", choices=("dashboard", "user_chat_link", "agent_import"), default="agent_import")
    source_link.set_defaults(func=cmd_source_add_link)
    source_file = sub.add_parser("source-add-file", help="copy a permitted file into managed source storage")
    source_file.add_argument("path")
    source_file.add_argument("--category", default="需求资料")
    source_file.add_argument("--origin", choices=("dashboard", "user_chat_attachment", "agent_import"), default="agent_import")
    source_file.set_defaults(func=cmd_source_add_file)
    source_remove = sub.add_parser("source-remove", help="soft-remove one reference source")
    source_remove.add_argument("id")
    source_remove.set_defaults(func=cmd_source_remove)
    source_restore = sub.add_parser("source-restore", help="restore a soft-removed reference source")
    source_restore.add_argument("id")
    source_restore.set_defaults(func=cmd_source_restore)
    source_lifecycle = sub.add_parser("source-lifecycle", help="update a source verification lifecycle")
    source_lifecycle.add_argument("id")
    source_lifecycle.add_argument("--status", choices=("registered", "suspected_deprecated", "deprecated"))
    source_lifecycle.add_argument("--reason")
    source_lifecycle.add_argument("--last-verified-at")
    source_lifecycle.add_argument("--override", choices=("active", "deprecated"))
    source_lifecycle.add_argument("--clear-override", action="store_true")
    source_lifecycle.set_defaults(func=cmd_source_lifecycle)
    seed = sub.add_parser("sources-import-seed", help="idempotently import user-approved reference sources")
    seed.add_argument("--input", required=True)
    seed.set_defaults(func=cmd_sources_import_seed)
    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"dashboardctl: {exc}")


if __name__ == "__main__":
    main()
