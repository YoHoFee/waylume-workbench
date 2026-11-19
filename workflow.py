"""Six-stage workflow pipeline for Waylume work items.

This module is the single source of truth for the pipeline contract in
docs/product/workflow-pipeline.md: fixed templates, stage definitions,
role/gate rules, transition validation and the per-record workflow state.

It is intentionally storage agnostic: dashboardctl.py owns locking, record
revisions and persistence, server.py only exposes HTTP, and backend.py renders
the context summary. No module here imports the others to avoid cycles.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta
from typing import Any

TEMPLATE_VERSION = 1
STAGE_IDS = ("intake", "requirement", "development", "audit_response", "acceptance", "delivery")
STAGE_STATUSES = {"active", "blocked", "done", "rework"}
ROLES = ("lead", "pm", "dev", "auditor")
TERMINAL_STATUSES = {"completed", "cancelled", "archived"}
MAX_ACTOR_CHARS = 100
MAX_NOTE_CHARS = 4000
MAX_EVIDENCE_ITEMS = 50
MAX_EVIDENCE_CHARS = 2000
MAX_OUTPUTS = 64
MAX_OUTPUT_KEY_CHARS = 64
MAX_OUTPUT_VALUE_CHARS = 20000
LOCK_TTL_SECONDS = 30 * 60

_OUTPUT_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

OUTPUT_LABELS = {
    "scope": "范围",
    "decision_owner": "决定权归属",
    "requirement_brief": "需求简报",
    "acceptance_criteria": "验收标准",
    "pm_decision": "PM 结论",
    "dev_summary": "开发摘要",
    "branch": "分支",
    "self_check": "自检",
    "audit_report": "审计报告",
    "dev_response": "开发回应",
    "pm_acceptance": "PM 验收结论",
    "delivery_evidence": "交付证据",
}


class WorkflowError(ValueError):
    """A contract error code plus a human readable message and optional detail."""

    def __init__(self, code: str, message: str | None = None, *, detail: dict[str, Any] | None = None):
        self.code = code
        self.detail = dict(detail or {})
        super().__init__(message or code)


def _stage(stage_id: str, label: str, short: str, owner: str, submit_roles: list[str],
           advance_roles: list[str], required_outputs: list[str], *, next_stage: str | None = None,
           rework_to: list[str] | None = None, substeps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": stage_id,
        "label": label,
        "short": short,
        "owner": owner,
        "submit_roles": list(submit_roles),
        "advance_roles": list(advance_roles),
        "required_outputs": list(required_outputs),
        "output_labels": {key: OUTPUT_LABELS.get(key, key) for key in required_outputs},
        "substeps": list(substeps or []),
        "next": next_stage,
        "rework_to": list(rework_to or []),
    }


def _build_stages(*, bug: bool) -> list[dict[str, Any]]:
    if bug:
        labels = {
            "intake": "BUG 接单/立项",
            "requirement": "问题分析与 PM 结论",
            "development": "BUG 修复",
            "audit_response": "静态审计与修复回应",
            "acceptance": "PM 验收",
            "delivery": "交付关闭",
        }
        response_label = "修复回应"
    else:
        labels = {
            "intake": "接单/立项",
            "requirement": "需求分析与 PM 结论",
            "development": "开发",
            "audit_response": "静态审计与开发回应",
            "acceptance": "PM 验收",
            "delivery": "交付关闭",
        }
        response_label = "开发回应"
    return [
        _stage("intake", labels["intake"], "接单", "lead", ["lead"], ["lead"],
               ["scope", "decision_owner"], next_stage="requirement"),
        _stage("requirement", labels["requirement"], "需求", "pm", ["pm"], ["pm", "lead"],
               ["requirement_brief", "acceptance_criteria", "pm_decision"], next_stage="development",
               rework_to=["intake"],
               substeps=[
                   {"id": "understanding", "label": "需求理解/双跑差异", "role": "pm",
                    "output": "requirement_brief", "outputs": ["requirement_brief", "acceptance_criteria"]},
                   {"id": "pm_decision", "label": "PM 结论", "role": "pm",
                    "output": "pm_decision", "outputs": ["pm_decision"]},
               ]),
        _stage("development", labels["development"], "开发", "dev", ["dev"], ["dev", "lead"],
               ["dev_summary", "branch", "self_check"], next_stage="audit_response",
               rework_to=["requirement"]),
        _stage("audit_response", labels["audit_response"], "审计", "auditor", ["auditor", "dev"], ["lead"],
               ["audit_report", "dev_response"], next_stage="acceptance",
               rework_to=["development"],
               substeps=[
                   {"id": "audit", "label": "静态审计", "role": "auditor", "output": "audit_report",
                    "outputs": ["audit_report"]},
                   {"id": "response", "label": response_label, "role": "dev", "output": "dev_response",
                    "outputs": ["dev_response"]},
               ]),
        _stage("acceptance", labels["acceptance"], "验收", "pm", ["pm"], ["pm", "lead"],
               ["pm_acceptance"], next_stage="delivery", rework_to=["development"]),
        _stage("delivery", labels["delivery"], "交付", "lead", ["lead"], ["lead"],
               ["delivery_evidence"], next_stage=None, rework_to=["acceptance"]),
    ]


TEMPLATES: dict[str, dict[str, Any]] = {
    "dev-standard": {
        "id": "dev-standard",
        "version": TEMPLATE_VERSION,
        "name": "开发标准流程",
        "applies_to": ["task", "work"],
        "stages": _build_stages(bug=False),
    },
    "bug-standard": {
        "id": "bug-standard",
        "version": TEMPLATE_VERSION,
        "name": "BUG 标准流程",
        "applies_to": ["bug"],
        "stages": _build_stages(bug=True),
    },
}

DEFAULT_TEMPLATES = {"task": "dev-standard", "work": "dev-standard", "bug": "bug-standard"}


# ---------------------------------------------------------------------------
# templates


def templates() -> list[dict[str, Any]]:
    return [copy.deepcopy(TEMPLATES[key]) for key in TEMPLATES]


def get_template(template_id: str) -> dict[str, Any]:
    template = TEMPLATES.get(template_id) if isinstance(template_id, str) else None
    if template is None:
        raise WorkflowError("template_not_found", f"未知流水线模板：{template_id!r}；可用模板：{'、'.join(TEMPLATES)}",
                            detail={"available": sorted(TEMPLATES)})
    return copy.deepcopy(template)


def default_template_id(record_type: str | None) -> str | None:
    return DEFAULT_TEMPLATES.get(str(record_type or ""))


def resolve_template(template_id: str | None, record_type: str | None) -> dict[str, Any]:
    candidate = template_id if template_id is not None else default_template_id(record_type)
    if not isinstance(candidate, str) or not candidate.strip():
        raise WorkflowError("invalid_template", "未指定流水线模板，且事项类型没有默认模板")
    template = TEMPLATES.get(candidate.strip())
    if template is None:
        raise WorkflowError("invalid_template", f"未知流水线模板：{candidate!r}；可用模板：{'、'.join(TEMPLATES)}",
                            detail={"available": sorted(TEMPLATES)})
    if record_type and record_type not in template["applies_to"]:
        raise WorkflowError("invalid_template",
                            f"模板 {template['id']} 仅适用于 {'、'.join(template['applies_to'])}，不能用于 {record_type}")
    return copy.deepcopy(template)


def stage_definition(template: dict[str, Any], stage_id: Any) -> tuple[dict[str, Any], int]:
    for index, stage in enumerate(template.get("stages") or []):
        if stage.get("id") == stage_id:
            return stage, index
    raise WorkflowError("invalid_stage", f"未知流水线阶段：{stage_id!r}；可用阶段：{'、'.join(STAGE_IDS)}",
                        detail={"available": list(STAGE_IDS)})


def stage_index(template: dict[str, Any], stage_id: str | None) -> int:
    for index, stage in enumerate(template.get("stages") or []):
        if stage.get("id") == stage_id:
            return index + 1
    return 0


# ---------------------------------------------------------------------------
# sanitising helpers


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 and char not in "\t\n\r" for char in value)


def _clean_actor(actor: Any) -> str:
    if not isinstance(actor, str):
        raise WorkflowError("invalid_actor", "actor 必须是字符串")
    clean = actor.strip()
    if not clean or len(clean) > MAX_ACTOR_CHARS or _has_control(clean):
        raise WorkflowError("invalid_actor", f"actor 不能为空、不能超过 {MAX_ACTOR_CHARS} 字符且不能包含控制字符")
    return clean


def _clean_note(note: Any, *, required: bool = False) -> str:
    if note is None:
        note = ""
    if not isinstance(note, str) or len(note) > MAX_NOTE_CHARS or _has_control(note):
        raise WorkflowError("invalid_note", f"note 必须是不超过 {MAX_NOTE_CHARS} 字符的文本")
    clean = note.strip()
    if required and not clean:
        raise WorkflowError("note_required", "该操作需要填写原因（note）")
    return clean


def _clean_evidence(evidence: Any) -> list[str]:
    if evidence is None:
        return []
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE_ITEMS:
        raise WorkflowError("invalid_evidence", f"evidence 必须是最多 {MAX_EVIDENCE_ITEMS} 条的字符串数组")
    cleaned: list[str] = []
    for item in evidence:
        if not isinstance(item, str):
            raise WorkflowError("invalid_evidence", "evidence 的每一项都必须是字符串")
        value = item.strip()
        if not value or len(value) > MAX_EVIDENCE_CHARS or _has_control(value):
            raise WorkflowError("invalid_evidence", f"evidence 的每一项都必须非空且不超过 {MAX_EVIDENCE_CHARS} 字符")
        cleaned.append(value)
    return cleaned


def _clean_outputs(outputs: Any) -> dict[str, str]:
    if outputs is None:
        return {}
    if not isinstance(outputs, dict) or len(outputs) > MAX_OUTPUTS:
        raise WorkflowError("invalid_outputs", f"outputs 必须是最多 {MAX_OUTPUTS} 项的 key/value 对象")
    cleaned: dict[str, str] = {}
    for key, value in outputs.items():
        if not isinstance(key, str) or not _OUTPUT_KEY_RE.fullmatch(key):
            raise WorkflowError("invalid_outputs", f"outputs 的键不合法：{key!r}",
                                detail={"key": str(key)})
        if isinstance(value, bool) or isinstance(value, (int, float)):
            value = str(value)
        if not isinstance(value, str):
            raise WorkflowError("invalid_outputs", f"outputs[{key}] 必须是字符串", detail={"key": key})
        if len(value) > MAX_OUTPUT_VALUE_CHARS:
            raise WorkflowError("invalid_outputs", f"outputs[{key}] 超过 {MAX_OUTPUT_VALUE_CHARS} 字符", detail={"key": key})
        cleaned[key] = value
    return cleaned


def _clean_role(role: Any, allowed: list[str], action: str, stage: dict[str, Any]) -> str:
    clean = role.strip() if isinstance(role, str) else ""
    if clean not in ROLES:
        raise WorkflowError("invalid_role", f"未知角色：{role!r}；允许角色：{'、'.join(ROLES)}",
                            detail={"allowed": list(ROLES)})
    if clean not in allowed:
        raise WorkflowError(
            "invalid_role",
            f"角色 {clean} 无权在「{stage['label']}」阶段执行 {action}；允许角色：{'、'.join(allowed)}",
            detail={"allowed": list(allowed), "stage": stage["id"], "action": action},
        )
    return clean


def _now(now: Any) -> str:
    if now is None:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    if isinstance(now, datetime):
        return now.astimezone().isoformat(timespec="seconds")
    if isinstance(now, str) and now.strip():
        return now
    raise ValueError("invalid_now")



# ---------------------------------------------------------------------------
# stage locks

def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone() if parsed.tzinfo is not None else parsed.astimezone()


def _lock_active(lock: Any, now: datetime) -> bool:
    if not isinstance(lock, dict) or not lock.get("expires_at"):
        return False
    try:
        return _as_datetime(lock["expires_at"]) > now
    except (TypeError, ValueError, OSError):
        return False


def _lock_matches(lock: dict[str, Any], actor: str, role: str, session_id: str | None) -> bool:
    if not isinstance(lock, dict):
        return False
    if str(lock.get("actor") or "") != actor or str(lock.get("role") or "") != role:
        return False
    locked_session = str(lock.get("session_id") or "")
    return not locked_session or not session_id or locked_session == session_id


def _lock_detail(lock: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(lock, dict):
        return None
    return {key: lock.get(key) for key in ("stage", "actor", "role", "session_id", "acquired_at", "heartbeat_at", "expires_at")}


def _set_lock(record: dict[str, Any], workflow_state: dict[str, Any], stage: dict[str, Any],
              *, actor_role: str, actor: str, session_id: str | None, ttl_seconds: int,
              at: str, now: datetime, action: str) -> dict[str, Any]:
    role = _clean_role(actor_role, stage["submit_roles"], action, stage)
    previous = workflow_state.get("lock") if isinstance(workflow_state.get("lock"), dict) else None
    if isinstance(previous, dict) and previous.get("stage") != stage["id"]:
        previous = None
    acquired_at = at
    if isinstance(previous, dict) and previous.get("actor") == actor and previous.get("role") == role:
        acquired_at = str(previous.get("acquired_at") or at)
    lock = {
        "stage": stage["id"], "actor": actor, "role": role, "session_id": session_id,
        "acquired_at": acquired_at, "heartbeat_at": at,
        "expires_at": (now + timedelta(seconds=max(60, int(ttl_seconds)))).isoformat(timespec="seconds"),
    }
    workflow_state["lock"] = lock
    workflow_state["updated_at"] = at
    record["last_agent_activity_at"] = at
    record["current_agent_name"] = actor
    record["agent_state"] = "active"
    if session_id:
        record["current_agent_session_id"] = session_id
    if str(record.get("status") or "") not in TERMINAL_STATUSES:
        _apply_status(record, status_for_stage(stage["id"], record.get("type"), record.get("status")), at,
                      f"锁定「{stage['label']}」：{actor}")
    _append_history(workflow_state, at=at, action=action, stage=stage["id"], actor=actor, role=role,
                    note="", evidence=[], expires_at=lock["expires_at"])
    _sync_summary(record)
    record["updated_at"] = at
    return lock


def _clear_lock(record: dict[str, Any], workflow_state: dict[str, Any], *, at: str,
                action: str = "release", actor: str = "", role: str = "", note: str = "") -> None:
    if not isinstance(workflow_state.get("lock"), dict):
        return
    workflow_state.pop("lock", None)
    workflow_state["updated_at"] = at
    if record.get("current_agent_session_id"):
        record["last_agent_session_id"] = record.get("current_agent_session_id")
        record["last_agent_name"] = record.get("current_agent_name")
    record["current_agent_session_id"] = None
    record["current_agent_name"] = None
    record["agent_state"] = "idle"
    record["last_agent_activity_at"] = at
    _append_history(workflow_state, at=at, action=action, stage=workflow_state.get("stage"), actor=actor,
                    role=role, note=note, evidence=[])
    _sync_summary(record)
    record["updated_at"] = at


def _active_lock(workflow_state: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    lock = workflow_state.get("lock")
    if not isinstance(lock, dict) or lock.get("stage") != workflow_state.get("stage"):
        return None
    return lock if _lock_active(lock, now) else None


def _ensure_lock(record: dict[str, Any], *, actor_role: str, actor: str, session_id: str | None,
                 now: datetime, action: str) -> dict[str, Any]:
    workflow_state = _require_workflow(record)
    _ensure_not_terminal(record)
    _ensure_not_blocked(workflow_state)
    at = _now(now)
    template = _template_for(workflow_state)
    stage, _ = stage_definition(template, workflow_state.get("stage"))
    lock = _active_lock(workflow_state, now)
    if lock is not None:
        if not _lock_matches(lock, actor, actor_role, session_id):
            raise WorkflowError("stage_locked",
                                f"当前阶段已被 {lock.get('actor')}（{lock.get('role')}）锁定，不能并发操作",
                                detail={"lock": _lock_detail(lock)})
        lock["heartbeat_at"] = at
        lock["expires_at"] = (now + timedelta(seconds=LOCK_TTL_SECONDS)).isoformat(timespec="seconds")
        record["last_agent_activity_at"] = at
        record["updated_at"] = at
        workflow_state["updated_at"] = at
        _sync_summary(record)
        return lock
    return _set_lock(record, workflow_state, stage, actor_role=actor_role, actor=actor,
                     session_id=session_id, ttl_seconds=LOCK_TTL_SECONDS, at=at, now=now, action=action)


def claim(record: dict[str, Any], *, actor_role: str, actor: str, session_id: str | None = None,
          ttl_seconds: int = LOCK_TTL_SECONDS, now: Any = None) -> dict[str, Any]:
    workflow_state = _require_workflow(record)
    _ensure_not_terminal(record)
    _ensure_not_blocked(workflow_state)
    at = _now(now)
    now_dt = _as_datetime(at)
    template = _template_for(workflow_state)
    stage, _ = stage_definition(template, workflow_state.get("stage"))
    actor = _clean_actor(actor)
    lock = _active_lock(workflow_state, now_dt)
    if lock is not None and not _lock_matches(lock, actor, actor_role, session_id):
        raise WorkflowError("stage_locked",
                            f"当前阶段已被 {lock.get('actor')}（{lock.get('role')}）锁定，不能并发操作",
                            detail={"lock": _lock_detail(lock)})
    return _set_lock(record, workflow_state, stage, actor_role=actor_role, actor=actor,
                     session_id=session_id, ttl_seconds=ttl_seconds, at=at, now=now_dt, action="claim")


def heartbeat(record: dict[str, Any], *, actor_role: str, actor: str, session_id: str | None = None,
              ttl_seconds: int = LOCK_TTL_SECONDS, now: Any = None) -> dict[str, Any]:
    workflow_state = _require_workflow(record)
    _ensure_not_terminal(record)
    at = _now(now)
    now_dt = _as_datetime(at)
    lock = _active_lock(workflow_state, now_dt)
    if lock is None:
        raise WorkflowError("workflow_not_locked", "当前阶段没有有效锁；请先 workflow claim")
    if not _lock_matches(lock, actor, actor_role, session_id):
        raise WorkflowError("stage_locked",
                            f"当前阶段锁属于 {lock.get('actor')}（{lock.get('role')}），不能续锁",
                            detail={"lock": _lock_detail(lock)})
    lock["heartbeat_at"] = at
    lock["expires_at"] = (now_dt + timedelta(seconds=max(60, int(ttl_seconds)))).isoformat(timespec="seconds")
    workflow_state["updated_at"] = at
    record["last_agent_activity_at"] = at
    record["updated_at"] = at
    _append_history(workflow_state, at=at, action="heartbeat", stage=workflow_state.get("stage"), actor=actor,
                    role=actor_role, note="", evidence=[], expires_at=lock["expires_at"])
    _sync_summary(record)
    return lock


def release(record: dict[str, Any], *, actor_role: str, actor: str, session_id: str | None = None,
            now: Any = None) -> None:
    workflow_state = _require_workflow(record)
    at = _now(now)
    lock = workflow_state.get("lock")
    if not isinstance(lock, dict):
        raise WorkflowError("workflow_not_locked", "当前阶段没有锁，无需释放")
    if actor_role != "lead" and not _lock_matches(lock, actor, actor_role, session_id):
        raise WorkflowError("stage_locked",
                            f"当前阶段锁属于 {lock.get('actor')}（{lock.get('role')}），不能释放",
                            detail={"lock": _lock_detail(lock)})
    _clear_lock(record, workflow_state, at=at, action="release", actor=actor, role=actor_role, note="manual release")


# ---------------------------------------------------------------------------
# status mapping


def map_status(status: str | None) -> tuple[str, str]:
    """Map a coarse record status onto the pipeline stage used when starting."""
    value = str(status or "")
    if value == "completed":
        return "delivery", "done"
    if value == "awaiting_acceptance":
        return "acceptance", "active"
    if value == "developing":
        return "development", "active"
    if value == "blocked":
        return "intake", "blocked"
    return "intake", "active"


def status_for_stage(stage_id: str, record_type: str | None, current_status: str | None = None) -> str:
    if stage_id == "intake":
        return current_status if current_status in {"pending", "in_progress"} else "in_progress"
    if stage_id == "requirement":
        return "in_progress"
    if stage_id == "development":
        return "developing"
    if stage_id == "audit_response":
        return "in_progress"
    if stage_id == "acceptance":
        return "awaiting_acceptance"
    if stage_id == "delivery":
        return "pending_release" if record_type in {"work", "bug"} else "in_progress"
    raise WorkflowError("invalid_stage", f"未知流水线阶段：{stage_id!r}")


# ---------------------------------------------------------------------------
# workflow state helpers


def _require_workflow(record: dict[str, Any]) -> dict[str, Any]:
    workflow = record.get("workflow")
    if not isinstance(workflow, dict) or not workflow:
        raise WorkflowError("workflow_not_started", "该事项尚未启动流水线；请先执行 workflow start")
    if not isinstance(workflow.get("template_id"), str) or not workflow.get("stage"):
        raise WorkflowError("invalid_workflow", "记录中的 workflow 数据不完整，无法继续流转")
    return workflow


def _template_for(workflow: dict[str, Any]) -> dict[str, Any]:
    return get_template(workflow.get("template_id"))


def _ensure_not_terminal(record: dict[str, Any]) -> None:
    status = str(record.get("status") or "")
    if status in TERMINAL_STATUSES:
        raise WorkflowError("workflow_terminal", f"事项状态为 {status}，流水线已终止，禁止继续流转")


def _ensure_not_blocked(workflow: dict[str, Any]) -> None:
    if workflow.get("stage_status") == "blocked":
        raise WorkflowError("workflow_blocked", "流水线处于阻塞状态；请先执行 workflow unblock")


def _sync_summary(record: dict[str, Any]) -> None:
    workflow = record.get("workflow")
    if not isinstance(workflow, dict):
        return
    record["workflow_template_id"] = workflow.get("template_id")
    record["workflow_stage"] = workflow.get("stage")
    record["workflow_stage_status"] = workflow.get("stage_status")


def _apply_status(record: dict[str, Any], desired: str, at: str, note: str) -> None:
    previous = record.get("status")
    if previous == desired:
        return
    record["status"] = desired
    history = record.setdefault("history", [])
    if not isinstance(history, list):
        history = record["history"] = []
    history.append({"at": at, "from_status": previous, "to_status": desired, "status": desired,
                    "note": f"流水线：{note}"})


def _append_history(workflow: dict[str, Any], *, at: str, action: str, stage: str, actor: str, role: str,
                    note: str, evidence: list[str], **extra: Any) -> None:
    entry: dict[str, Any] = {"at": at, "action": action, "stage": stage, "actor": actor, "role": role,
                             "note": note, "evidence": list(evidence)}
    for key, value in extra.items():
        if value is not None:
            entry[key] = value
    history = workflow.setdefault("history", [])
    if not isinstance(history, list):
        history = workflow["history"] = []
    history.append(entry)


def _gate(workflow: dict[str, Any], stage_id: str) -> dict[str, Any]:
    gates = workflow.get("gates")
    if not isinstance(gates, dict):
        gates = workflow["gates"] = {}
    gate = gates.get(stage_id)
    if not isinstance(gate, dict):
        gate = gates[stage_id] = {}
    gate.setdefault("outputs", {})
    gate.setdefault("evidence", [])
    return gate


def _reset_gate(workflow: dict[str, Any], stage_id: str, *, actor: str, role: str, at: str,
                note: str, evidence: list[str]) -> dict[str, Any]:
    """Start a fresh gate, retaining superseded outputs for traceability."""
    previous = workflow.get("gates", {}).get(stage_id) if isinstance(workflow.get("gates"), dict) else None
    superseded: list[dict[str, Any]] = []
    if isinstance(previous, dict):
        superseded = list(previous.get("superseded") or [])
        if previous.get("outputs") or previous.get("evidence"):
            superseded.append({
                "at": previous.get("at"),
                "actor": previous.get("actor"),
                "role": previous.get("role"),
                "note": previous.get("note", ""),
                "outputs": dict(previous.get("outputs") or {}),
                "evidence": list(previous.get("evidence") or []),
            })
    gate = {"status": "active", "actor": actor, "role": role, "at": at, "note": note,
            "evidence": list(evidence), "outputs": {}}
    if superseded:
        gate["superseded"] = superseded
    workflow.setdefault("gates", {})[stage_id] = gate
    return gate


def _missing_outputs(gate: dict[str, Any], stage: dict[str, Any]) -> list[str]:
    outputs = gate.get("outputs") if isinstance(gate.get("outputs"), dict) else {}
    return [key for key in stage["required_outputs"] if not str(outputs.get(key) or "").strip()]


def _substage_for(stage: dict[str, Any], gate: dict[str, Any] | None) -> str | None:
    substeps = stage.get("substeps") or []
    if not substeps:
        return None
    outputs = (gate or {}).get("outputs") if isinstance((gate or {}).get("outputs"), dict) else {}
    for substep in substeps:
        if any(not str(outputs.get(key) or "").strip() for key in substep.get("outputs") or []):
            return substep["id"]
    return None


def _gate_status_for(stage_status: str) -> str:
    return stage_status if stage_status in STAGE_STATUSES else "active"


def _make_gate(stage: dict[str, Any], *, stage_status: str, actor: str, role: str, at: str,
               note: str, evidence: list[str]) -> dict[str, Any]:
    gate = {"status": _gate_status_for(stage_status), "actor": actor, "role": role, "at": at,
            "note": note, "evidence": list(evidence), "outputs": {}}
    return gate


# ---------------------------------------------------------------------------
# transitions


def start(record: dict[str, Any], *, template_id: str | None = None, actor_role: str, actor: str,
          note: Any = "", evidence: Any = None, stage: str | None = None, now: Any = None) -> dict[str, Any]:
    existing = record.get("workflow")
    if isinstance(existing, dict) and existing:
        raise WorkflowError("workflow_already_started",
                            f"该事项已启动流水线（{existing.get('template_id')} / {existing.get('stage')}）")
    at = _now(now)
    actor = _clean_actor(actor)
    note = _clean_note(note)
    evidence = _clean_evidence(evidence)
    record_type = str(record.get("type") or "")
    current_status = str(record.get("status") or "")
    if current_status in {"cancelled", "archived"}:
        raise WorkflowError("workflow_terminal", f"事项状态为 {current_status}，不能启动流水线")
    template = resolve_template(template_id, record_type)
    if stage is None:
        stage_id, stage_status = map_status(current_status)
        target, _ = stage_definition(template, stage_id)
    else:
        if not isinstance(stage, str) or not stage.strip():
            raise WorkflowError("invalid_stage", "stage 必须是非空的阶段 id")
        if current_status == "completed":
            raise WorkflowError("invalid_transition", "已完成事项只允许启动为交付阶段（只读），不能指定其它阶段")
        target, _ = stage_definition(template, stage.strip())
        stage_status = "blocked" if current_status == "blocked" else "active"
    role = _clean_role(actor_role, target["advance_roles"], "start", target)
    gate = _make_gate(target, stage_status=stage_status, actor=actor, role=role, at=at,
                      note=note, evidence=evidence)
    workflow: dict[str, Any] = {
        "template_id": template["id"],
        "template_version": template["version"],
        "stage": target["id"],
        "stage_status": stage_status,
        "substage": _substage_for(target, gate) if stage_status == "active" else None,
        "started_at": at,
        "updated_at": at,
        "rework_count": 0,
        "gates": {target["id"]: gate},
        "history": [],
    }
    if stage_status == "blocked":
        workflow["blocked_from_stage"] = target["id"]
        workflow["blocked_from_stage_status"] = "active"
        workflow["blocked_at"] = at
        workflow["blocked_by"] = actor
        workflow["blocked_from_status"] = current_status
    _append_history(workflow, at=at, action="start", stage=target["id"], actor=actor, role=role,
                    note=note or f"启动流水线：{template['name']}", evidence=evidence,
                    template_id=template["id"], template_version=template["version"],
                    stage_status=stage_status)
    record["workflow"] = workflow
    _sync_summary(record)
    if current_status not in {"completed", "blocked"}:
        desired = status_for_stage(target["id"], record_type, current_status)
        _apply_status(record, desired, at, f"启动流水线进入「{target['label']}」")
    record["updated_at"] = at
    return workflow


def submit(record: dict[str, Any], outputs: Any, *, actor_role: str, actor: str,
           note: Any = "", evidence: Any = None, now: Any = None) -> dict[str, Any]:
    workflow = _require_workflow(record)
    _ensure_not_terminal(record)
    _ensure_not_blocked(workflow)
    at = _now(now)
    template = _template_for(workflow)
    stage, _ = stage_definition(template, workflow.get("stage"))
    actor = _clean_actor(actor)
    role = _clean_role(actor_role, stage["submit_roles"], "submit", stage)
    now_dt = _as_datetime(at)
    _ensure_lock(record, actor_role=actor_role, actor=actor, session_id=None, now=now_dt, action="claim")
    cleaned_outputs = _clean_outputs(outputs)
    for substep in stage.get("substeps") or []:
        for key in substep.get("outputs") or []:
            if key in cleaned_outputs and role != substep.get("role"):
                if workflow.get("audit_mode") == "lightweight" and stage.get("id") == "audit_response" and role == "dev":
                    continue
                raise WorkflowError("invalid_role",
                                    f"输出 {key} 只能由 {substep.get('role')} 提交，当前角色是 {role}")
    note = _clean_note(note)
    evidence = _clean_evidence(evidence)
    if not cleaned_outputs and not note and not evidence:
        raise WorkflowError("invalid_outputs", "submit 至少需要提供 outputs、note 或 evidence 之一")
    gate = _gate(workflow, stage["id"])
    gate.update({"status": "active", "actor": actor, "role": role, "at": at})
    if note:
        gate["note"] = note
    gate["evidence"] = list(gate.get("evidence") or []) + evidence
    merged = dict(gate.get("outputs") or {})
    merged.update(cleaned_outputs)
    gate["outputs"] = merged
    workflow["substage"] = _substage_for(stage, gate)
    workflow["updated_at"] = at
    _append_history(workflow, at=at, action="submit", stage=stage["id"], actor=actor, role=role,
                    note=note, evidence=evidence,
                    output_keys=sorted(cleaned_outputs) if cleaned_outputs else None)
    if stage.get("substeps"):
        role_outputs = [key for sub in stage["substeps"] if sub.get("role") == role for key in sub.get("outputs") or []]
        if role_outputs and all(str(merged.get(key) or "").strip() for key in role_outputs):
            _clear_lock(record, workflow, at=at, action="release", actor=actor, role=role,
                        note=f"子步骤完成：{', '.join(role_outputs)}")
    _sync_summary(record)
    record["updated_at"] = at
    return workflow


def advance(record: dict[str, Any], *, actor_role: str, actor: str, note: Any = "",
            evidence: Any = None, to: str | None = None, now: Any = None) -> dict[str, Any]:
    workflow = _require_workflow(record)
    _ensure_not_terminal(record)
    _ensure_not_blocked(workflow)
    at = _now(now)
    template = _template_for(workflow)
    stage, _ = stage_definition(template, workflow.get("stage"))
    actor = _clean_actor(actor)
    role = _clean_role(actor_role, stage["advance_roles"], "advance", stage)
    now_dt = _as_datetime(at)
    active_lock = _active_lock(workflow, now_dt)
    if active_lock is not None and not _lock_matches(active_lock, actor, role, None):
        raise WorkflowError("stage_locked",
                            f"当前阶段被 {active_lock.get('actor')}（{active_lock.get('role')}）锁定，不能推进",
                            detail={"lock": _lock_detail(active_lock)})
    note = _clean_note(note)
    evidence = _clean_evidence(evidence)
    if workflow.get("stage_status") == "done":
        raise WorkflowError("invalid_transition", f"「{stage['label']}」阶段已结束，不能再次推进")
    next_id = stage.get("next")
    if not next_id:
        raise WorkflowError("invalid_transition", f"「{stage['label']}」是最后一个阶段，只能执行 workflow close")
    if to is not None:
        if not isinstance(to, str) or not to.strip():
            raise WorkflowError("invalid_stage", "to 必须是非空的阶段 id")
        target_definition, _ = stage_definition(template, to.strip())
        if target_definition["id"] != next_id:
            next_definition, _ = stage_definition(template, next_id)
            raise WorkflowError("invalid_transition",
                                f"当前阶段「{stage['label']}」只能推进到「{next_definition['label']}」，不能直接进入「{target_definition['label']}」")
    gate = _gate(workflow, stage["id"])
    missing = _missing_outputs(gate, stage)
    if missing:
        labels = [stage.get("output_labels", {}).get(key, key) for key in missing]
        raise WorkflowError("missing_outputs",
                            f"缺少必需输出：{'、'.join(labels)}（{', '.join(missing)}）；请先执行 workflow submit",
                            detail={"missing": missing, "stage": stage["id"]})
    target, _ = stage_definition(template, next_id)
    if workflow.get("lock"):
        _clear_lock(record, workflow, at=at, action="advance_release", actor=actor, role=role, note="阶段完成")
    gate["status"] = "done"
    gate["completed_at"] = at
    gate["actor"] = actor
    gate["role"] = role
    new_gate = _reset_gate(workflow, target["id"], actor=actor, role=role, at=at, note="", evidence=[])
    workflow["stage"] = target["id"]
    workflow["stage_status"] = "active"
    workflow["substage"] = _substage_for(target, new_gate)
    workflow["updated_at"] = at
    desired = status_for_stage(target["id"], record.get("type"), record.get("status"))
    _apply_status(record, desired, at, f"推进到「{target['label']}」")
    _append_history(workflow, at=at, action="advance", stage=target["id"], actor=actor, role=role,
                    note=note, evidence=evidence, from_stage=stage["id"])
    _sync_summary(record)
    record["updated_at"] = at
    return workflow


def rework(record: dict[str, Any], to: str, *, actor_role: str, actor: str, note: Any = "",
           evidence: Any = None, now: Any = None) -> dict[str, Any]:
    workflow = _require_workflow(record)
    _ensure_not_terminal(record)
    _ensure_not_blocked(workflow)
    at = _now(now)
    template = _template_for(workflow)
    stage, _ = stage_definition(template, workflow.get("stage"))
    actor = _clean_actor(actor)
    role = _clean_role(actor_role, stage["advance_roles"], "rework", stage)
    now_dt = _as_datetime(at)
    active_lock = _active_lock(workflow, now_dt)
    if active_lock is not None and not _lock_matches(active_lock, actor, role, None):
        raise WorkflowError("stage_locked",
                            f"当前阶段被 {active_lock.get('actor')}（{active_lock.get('role')}）锁定，不能退回",
                            detail={"lock": _lock_detail(active_lock)})
    note = _clean_note(note)
    evidence = _clean_evidence(evidence)
    if not isinstance(to, str) or not to.strip():
        raise WorkflowError("invalid_stage", "rework 需要指定退回目标阶段（to）")
    target, _ = stage_definition(template, to.strip())
    if target["id"] not in stage.get("rework_to", []):
        allowed = [stage_definition(template, key)[0]["label"] for key in stage.get("rework_to", [])]
        hint = f"；可退回：{'、'.join(allowed)}" if allowed else "；当前阶段不允许退回"
        raise WorkflowError("invalid_transition",
                            f"「{stage['label']}」不能退回到「{target['label']}」{hint}",
                            detail={"allowed": list(stage.get("rework_to", []))})
    if workflow.get("lock"):
        _clear_lock(record, workflow, at=at, action="rework_release", actor=actor, role=role, note="退回前释放")
    current_gate = _gate(workflow, stage["id"])
    current_gate["status"] = "rework"
    current_gate["actor"] = actor
    current_gate["role"] = role
    current_gate["at"] = at
    new_gate = _reset_gate(workflow, target["id"], actor=actor, role=role, at=at, note=note, evidence=evidence)
    workflow["stage"] = target["id"]
    # "rework" is a UI-visible stage status (contract 8.1): the stage is active
    # again but marked as returned until the next advance.
    workflow["stage_status"] = "rework"
    workflow["substage"] = _substage_for(target, new_gate)
    workflow["rework_count"] = int(workflow.get("rework_count") or 0) + 1
    workflow["updated_at"] = at
    desired = status_for_stage(target["id"], record.get("type"), record.get("status"))
    _apply_status(record, desired, at, f"退回「{target['label']}」")
    _append_history(workflow, at=at, action="rework", stage=target["id"], actor=actor, role=role,
                    note=note, evidence=evidence, from_stage=stage["id"],
                    rework_count=workflow["rework_count"])
    _sync_summary(record)
    record["updated_at"] = at
    return workflow


def reopen(record: dict[str, Any], *, actor_role: str = "lead", actor: str = "agent", reason: str = "",
           audit_mode: str = "lightweight", to_stage: str = "development",
           evidence: Any = None, now: Any = None) -> dict[str, Any]:
    workflow = record.get("workflow")
    at = _now(now)
    actor = _clean_actor(actor)
    reason = _clean_note(reason)
    evidence = _clean_evidence(evidence)
    audit_mode = audit_mode if audit_mode in {"lightweight", "full"} else "lightweight"

    if workflow and isinstance(workflow, dict):
        template = _template_for(workflow)
        target, _ = stage_definition(template, to_stage)
        if workflow.get("lock"):
            _clear_lock(record, workflow, at=at, action="reopen_release", actor=actor, role=actor_role, note="重开释放锁")
        new_gate = _reset_gate(workflow, target["id"], actor=actor, role=actor_role, at=at, note=reason, evidence=evidence)
        workflow["stage"] = target["id"]
        workflow["stage_status"] = "active"
        workflow["substage"] = _substage_for(target, new_gate)
        workflow["rework_count"] = int(workflow.get("rework_count") or 0) + 1
        workflow["audit_mode"] = audit_mode
        workflow["updated_at"] = at

        _append_history(workflow, at=at, action="reopen", stage=target["id"], actor=actor, role=actor_role,
                        note=f"发起修改(第{record.get('reopen_count', 1)}轮): {reason}",
                        evidence=evidence, audit_mode=audit_mode)
        _sync_summary(record)

    desired = status_for_stage(to_stage, record.get("type"), "in_progress") if (workflow and isinstance(workflow, dict)) else "in_progress"
    record["status"] = desired
    record["effective_status"] = desired
    record["manual_status"] = None
    record["manual_completed_at"] = None
    record["manual_completed_by"] = None
    record["completed_at"] = None
    record["updated_at"] = at
    record.setdefault("history", []).append({
        "at": at,
        "from_status": "completed",
        "to_status": desired,
        "status": desired,
        "note": f"发起修改 (第{record.get('reopen_count', 1)}轮): {reason} [审计模式: {audit_mode}]"
    })
    return workflow or {}


def block(record: dict[str, Any], *, actor_role: str, actor: str, note: Any, now: Any = None) -> dict[str, Any]:
    workflow = _require_workflow(record)
    _ensure_not_terminal(record)
    at = _now(now)
    template = _template_for(workflow)
    stage, _ = stage_definition(template, workflow.get("stage"))
    actor = _clean_actor(actor)
    role = _clean_role(actor_role, list(ROLES), "block", stage)
    note = _clean_note(note, required=True)
    if workflow.get("stage_status") == "blocked":
        raise WorkflowError("workflow_blocked", "流水线已处于阻塞状态；请先执行 workflow unblock")
    if workflow.get("stage_status") == "done":
        raise WorkflowError("invalid_transition", f"「{stage['label']}」阶段已结束，不能阻塞")
    active_lock = _active_lock(workflow, _as_datetime(at))
    if active_lock is not None and actor_role != "lead" and not _lock_matches(active_lock, actor, actor_role, None):
        raise WorkflowError("stage_locked",
                            f"当前阶段被 {active_lock.get('actor')}（{active_lock.get('role')}）锁定，不能阻塞",
                            detail={"lock": _lock_detail(active_lock)})
    if workflow.get("lock"):
        _clear_lock(record, workflow, at=at, action="block_release", actor=actor, role=role, note="阻塞前释放")
    gate = _gate(workflow, stage["id"])
    previous_stage_status = workflow.get("stage_status") or "active"
    gate["status"] = "blocked"
    workflow["stage_status"] = "blocked"
    workflow["blocked_from_stage"] = stage["id"]
    workflow["blocked_from_stage_status"] = previous_stage_status
    workflow["blocked_from_status"] = str(record.get("status") or "")
    workflow["blocked_at"] = at
    workflow["blocked_by"] = actor
    workflow["blocked_note"] = note
    workflow["updated_at"] = at
    _apply_status(record, "blocked", at, f"阻塞：{note}")
    _append_history(workflow, at=at, action="block", stage=stage["id"], actor=actor, role=role,
                    note=note, evidence=[])
    _sync_summary(record)
    record["updated_at"] = at
    return workflow


def unblock(record: dict[str, Any], *, actor_role: str, actor: str, note: Any = "", now: Any = None) -> dict[str, Any]:
    workflow = _require_workflow(record)
    _ensure_not_terminal(record)
    at = _now(now)
    template = _template_for(workflow)
    stage, _ = stage_definition(template, workflow.get("stage"))
    actor = _clean_actor(actor)
    role = _clean_role(actor_role, list(ROLES), "unblock", stage)
    note = _clean_note(note)
    if workflow.get("stage_status") != "blocked":
        raise WorkflowError("invalid_transition", "流水线当前未处于阻塞状态，无法解除阻塞")
    previous_status = workflow.get("blocked_from_status") or "blocked"
    previous_stage_status = workflow.get("blocked_from_stage_status") or "active"
    if previous_stage_status not in {"active", "rework"}:
        previous_stage_status = "active"
    workflow["stage_status"] = previous_stage_status
    workflow["blocked_from_stage"] = None
    workflow["blocked_from_stage_status"] = None
    workflow["blocked_at"] = None
    workflow["blocked_by"] = None
    workflow["blocked_note"] = None
    if workflow.get("lock"):
        _clear_lock(record, workflow, at=at, action="unblock_release", actor=actor, role=role, note="解除阻塞前释放")
    gate = _gate(workflow, stage["id"])
    gate["status"] = previous_stage_status
    desired = status_for_stage(stage["id"], record.get("type"), previous_status)
    workflow["updated_at"] = at
    _apply_status(record, desired, at, f"解除阻塞并恢复「{stage['label']}」")
    _append_history(workflow, at=at, action="unblock", stage=stage["id"], actor=actor, role=role,
                    note=note, evidence=[])
    _sync_summary(record)
    record["updated_at"] = at
    return workflow


def close(record: dict[str, Any], *, actor_role: str, actor: str, note: Any = "",
          evidence: Any = None, now: Any = None) -> dict[str, Any]:
    workflow = _require_workflow(record)
    _ensure_not_terminal(record)
    _ensure_not_blocked(workflow)
    at = _now(now)
    template = _template_for(workflow)
    stage, _ = stage_definition(template, workflow.get("stage"))
    if stage["id"] != "delivery":
        raise WorkflowError("invalid_stage", f"只有交付阶段可以 close；当前阶段：{stage['label']}")
    if workflow.get("stage_status") == "done":
        raise WorkflowError("invalid_transition", "交付阶段已关闭，不能重复 close")
    actor = _clean_actor(actor)
    role = _clean_role(actor_role, stage["advance_roles"], "close", stage)
    note = _clean_note(note)
    evidence = _clean_evidence(evidence)
    active_lock = _active_lock(workflow, _as_datetime(at))
    if active_lock is not None and not _lock_matches(active_lock, actor, role, None):
        raise WorkflowError("stage_locked",
                            f"当前阶段被 {active_lock.get('actor')}（{active_lock.get('role')}）锁定，不能关闭",
                            detail={"lock": _lock_detail(active_lock)})
    if workflow.get("lock"):
        _clear_lock(record, workflow, at=at, action="close_release", actor=actor, role=role, note="关闭前释放")
    gate = _gate(workflow, stage["id"])
    missing = _missing_outputs(gate, stage)
    if missing:
        labels = [stage.get("output_labels", {}).get(key, key) for key in missing]
        raise WorkflowError("missing_outputs",
                            f"缺少必需输出：{'、'.join(labels)}（{', '.join(missing)}）；请先执行 workflow submit",
                            detail={"missing": missing, "stage": stage["id"]})
    gate["status"] = "done"
    gate["completed_at"] = at
    gate["actor"] = actor
    gate["role"] = role
    if note:
        gate["note"] = note
    gate["evidence"] = list(gate.get("evidence") or []) + evidence
    workflow["stage_status"] = "done"
    workflow["substage"] = None
    workflow["updated_at"] = at
    _apply_status(record, "completed", at, "交付关闭")
    _append_history(workflow, at=at, action="close", stage=stage["id"], actor=actor, role=role,
                    note=note, evidence=evidence)
    _sync_summary(record)
    record["updated_at"] = at
    return workflow


# ---------------------------------------------------------------------------
# read helpers


def active_lock(record: dict[str, Any], now: Any = None) -> dict[str, Any] | None:
    workflow_state = record.get("workflow")
    if not isinstance(workflow_state, dict):
        return None
    return _active_lock(workflow_state, _as_datetime(_now(now)))


def summary(record: dict[str, Any]) -> dict[str, Any]:
    workflow = record.get("workflow") if isinstance(record.get("workflow"), dict) else None
    template_id = record.get("workflow_template_id") or (workflow or {}).get("template_id")
    stage_id = record.get("workflow_stage") or (workflow or {}).get("stage")
    stage_status = record.get("workflow_stage_status") or (workflow or {}).get("stage_status")
    template = TEMPLATES.get(template_id) if isinstance(template_id, str) else None
    index, label, short = 0, None, None
    if template and stage_id:
        for position, stage in enumerate(template["stages"]):
            if stage["id"] == stage_id:
                index, label, short = position + 1, stage["label"], stage["short"]
                break
    return {
        "workflow_template_id": template_id,
        "workflow_stage": stage_id,
        "workflow_stage_status": stage_status,
        "workflow_stage_index": index,
        "workflow_stage_label": label,
        "workflow_stage_short": short,
        "workflow_rework_count": int((workflow or {}).get("rework_count") or 0),
        "workflow_locked": bool(isinstance((workflow or {}).get("lock"), dict)),
        "workflow_lock_actor": ((workflow or {}).get("lock") or {}).get("actor"),
        "workflow_lock_role": ((workflow or {}).get("lock") or {}).get("role"),
        "workflow_lock_expires_at": ((workflow or {}).get("lock") or {}).get("expires_at"),
    }


def validate_workflow(workflow: Any, record_type: str | None = None) -> None:
    """Validate a persisted workflow blob (used by dashboardctl.normalize_record)."""
    if not isinstance(workflow, dict):
        raise ValueError("workflow must be a JSON object")
    for key in ("template_id", "template_version", "stage", "stage_status", "started_at", "updated_at",
                "rework_count", "gates", "history"):
        if key not in workflow:
            raise ValueError(f"workflow.{key} is required")
    template = get_template(workflow["template_id"])
    if record_type and record_type not in template["applies_to"]:
        raise ValueError(f"workflow template {template['id']} does not apply to {record_type}")
    stage_definition(template, workflow["stage"])
    if workflow["stage_status"] not in STAGE_STATUSES:
        raise ValueError("workflow.stage_status must be active, blocked or done")
    if not isinstance(workflow["template_version"], int) or isinstance(workflow["template_version"], bool):
        raise ValueError("workflow.template_version must be an integer")
    if not isinstance(workflow["rework_count"], int) or isinstance(workflow["rework_count"], bool) or workflow["rework_count"] < 0:
        raise ValueError("workflow.rework_count must be a non-negative integer")
    for key in ("started_at", "updated_at"):
        value = workflow[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"workflow.{key} must be an ISO datetime string")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"workflow.{key} must be an ISO datetime string") from exc
    if not isinstance(workflow["gates"], dict):
        raise ValueError("workflow.gates must be an object")
    for stage_id, gate in workflow["gates"].items():
        stage_definition(template, stage_id)
        if not isinstance(gate, dict):
            raise ValueError("workflow.gates entries must be objects")
    if not isinstance(workflow["history"], list):
        raise ValueError("workflow.history must be an array")
    lock = workflow.get("lock")
    if lock is not None:
        if not isinstance(lock, dict):
            raise ValueError("workflow.lock must be an object or null")
        stage_definition(template, lock.get("stage"))
        if not isinstance(lock.get("actor"), str) or not lock["actor"].strip():
            raise ValueError("workflow.lock.actor is required")
        if lock.get("role") not in ROLES:
            raise ValueError("workflow.lock.role is invalid")
        for key in ("acquired_at", "heartbeat_at", "expires_at"):
            value = lock.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"workflow.lock.{key} is required")
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"workflow.lock.{key} must be an ISO datetime string") from exc


def normalize_record_fields(record: dict[str, Any]) -> None:
    """Keep workflow summary fields consistent with the authoritative workflow blob."""
    workflow = record.get("workflow")
    if workflow is None:
        for key in ("workflow_template_id", "workflow_stage", "workflow_stage_status"):
            if record.get(key) not in (None, ""):
                raise ValueError(f"{key} requires workflow")
        return
    validate_workflow(workflow, record.get("type"))
    record["workflow_template_id"] = workflow["template_id"]
    record["workflow_stage"] = workflow["stage"]
    record["workflow_stage_status"] = workflow["stage_status"]


def context_lines(record: dict[str, Any]) -> list[str]:
    """Render the compact pipeline summary used by backend.render_context."""
    workflow = record.get("workflow")
    if not isinstance(workflow, dict) or not workflow.get("template_id"):
        return []
    try:
        template = get_template(workflow["template_id"])
        stage, _ = stage_definition(template, workflow.get("stage"))
    except WorkflowError:
        return ["## 流水线", "", "workflow 数据无法解析，请检查记录。", ""]
    lines = ["## 流水线", ""]
    lines.append(f"- 模板：{template['name']}（{template['id']} v{template.get('version')}）")
    index = stage_index(template, stage["id"])
    lines.append(f"- 当前阶段：{index}/{len(template['stages'])} {stage['label']}（{stage['id']}）"
                 f"· 状态：{workflow.get('stage_status')} · 负责人：{stage['owner']}")
    lock = workflow.get("lock")
    if isinstance(lock, dict):
        lines.append(f"- 当前锁定：{lock.get('actor')}（{lock.get('role')}）· 到期：{lock.get('expires_at')}")
    else:
        lines.append("- 当前锁定：未锁定")
    if stage.get("substeps") and workflow.get("substage"):
        substep = next((item for item in stage["substeps"] if item["id"] == workflow["substage"]), None)
        if substep:
            lines.append(f"- 当前子步骤：{substep['label']}（{substep['id']} · {substep['role']}）")
    next_id = stage.get("next")
    if next_id:
        next_stage, _ = stage_definition(template, next_id)
        lines.append(f"- 下一步：{next_stage['label']}（{next_stage['id']} · 负责人：{next_stage['owner']}）")
    else:
        lines.append("- 下一步：交付关闭（close）")
    lines.append(f"- 退回次数：{int(workflow.get('rework_count') or 0)}")
    if workflow.get("stage_status") == "blocked":
        lines.append(f"- 阻塞：{workflow.get('blocked_note') or '（未填写原因）'}"
                     f"（原阶段：{workflow.get('blocked_from_stage')}）")
    gate = (workflow.get("gates") or {}).get(stage["id"]) if isinstance(workflow.get("gates"), dict) else None
    outputs = gate.get("outputs") if isinstance(gate, dict) and isinstance(gate.get("outputs"), dict) else {}
    if stage["required_outputs"]:
        checklist = []
        for key in stage["required_outputs"]:
            label = stage.get("output_labels", {}).get(key, key)
            checklist.append(f"{label}{'✅' if str(outputs.get(key) or '').strip() else '⬜'}")
        lines.append(f"- 阶段输出：{'、'.join(checklist)}")
    if isinstance(gate, dict) and gate.get("note"):
        lines.append(f"- 最近 gate 备注：{gate['note']}")
    lines.append("")
    history = workflow.get("history") or []
    if history:
        labels = {item["id"]: item["label"] for item in template["stages"]}
        lines.extend(["### 最近流转", ""])
        for entry in history[-5:]:
            if not isinstance(entry, dict):
                continue
            at = str(entry.get("at") or "时间未知")
            action = str(entry.get("action") or "更新")
            stage_label = labels.get(entry.get("stage"), entry.get("stage") or "")
            parts = [at, action, str(stage_label), str(entry.get("actor") or ""),
                     f"（{entry.get('role')}）" if entry.get("role") else ""]
            line = "- " + " · ".join(part for part in parts if part)
            if entry.get("note"):
                line += f" — {entry['note']}"
            lines.append(line)
        lines.append("")
    return lines


__all__ = [
    "WorkflowError", "TEMPLATES", "STAGE_IDS", "STAGE_STATUSES", "ROLES", "OUTPUT_LABELS",
    "templates", "get_template", "default_template_id", "resolve_template", "stage_definition",
    "stage_index", "map_status", "status_for_stage", "start", "submit", "advance", "rework",
    "block", "unblock", "close", "claim", "heartbeat", "release", "active_lock", "summary",
    "validate_workflow", "normalize_record_fields", "context_lines",
]
