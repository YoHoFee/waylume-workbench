"""Per-record durable storage for the mCloud dashboard.

The old store.json is read once as an import source and retained as a backup.
All subsequent commits use a recoverable journal over record JSON/Markdown and
the shared metadata manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(name): os.unlink(name)


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


CONTEXT_FORMAT_VERSION = 2


def _context(record: dict[str, Any]) -> str:
    lines = [f"<!-- mcloud-context-v{CONTEXT_FORMAT_VERSION} -->", f"# {record.get('title', record.get('id', '事项'))}", ""]

    def add_value(label: str, value: Any) -> None:
        if value not in (None, "", [], {}): lines.extend([f"## {label}", "", str(value), ""])

    add_value("目标", record.get("initial_brief") or record.get("status_detail"))
    if record.get("decisions"):
        lines.extend(["## 已确认决定", ""])
        for item in record["decisions"]:
            if isinstance(item, dict):
                when = item.get("at") or item.get("created_at")
                body = item.get("message") or item.get("text") or item.get("decision") or item.get("note") or ""
                lines.append(f"- {when + ' — ' if when else ''}{body}")
                for key, value in item.items():
                    if key not in {"at", "created_at", "message", "text", "decision", "note"} and value not in (None, "", [], {}):
                        lines.append(f"  - {key}: {value}")
            else: lines.append(f"- {item}")
        lines.append("")
    add_value("执行阶段", record.get("phase"))
    add_value("当前简报", record.get("latest_brief"))
    add_value("阻塞", record.get("blocked_reason"))
    add_value("下一步", record.get("next_step"))
    for label, key in (("备注", "notes"), ("已完成证据", "evidence")):
        values = record.get(key) or []
        if values:
            lines.extend([f"## {label}", ""])
            for item in values:
                if isinstance(item, dict):
                    for name, value in item.items():
                        if value not in (None, "", [], {}): lines.append(f"- **{name}:** {value}")
                else: lines.append(f"- {item}")
            lines.append("")
    histories = record.get("history") or []
    if histories:
        lines.extend(["## 历史状态", ""])
        for item in histories:
            if isinstance(item, dict):
                parts = [str(item.get("at") or "时间未知"), str(item.get("status") or item.get("action") or "更新")]
                if item.get("note"): parts.append(str(item["note"]))
                lines.append("- " + " — ".join(parts))
                for key, value in item.items():
                    if key not in {"at", "status", "action", "note"} and value not in (None, "", [], {}): lines.append(f"  - {key}: {value}")
            else: lines.append(f"- {item}")
        lines.append("")
    for label, key in (("执行进展", "progress"), ("用户跟进", "followups")):
        values = record.get(key) or []
        if not values: continue
        lines.extend([f"## {label}", ""])
        for item in values:
            if not isinstance(item, dict):
                lines.extend([f"- {item}", ""]); continue
            when = item.get("at") or item.get("created_at") or item.get("claimed_at") or "时间未知"
            actor = item.get("actor") or item.get("created_by") or item.get("handled_by") or item.get("claimed_by")
            heading = f"### {when}" + (f" — {actor}" if actor else "")
            status = item.get("status")
            if status: heading += f" · {status}"
            lines.extend([heading, "", str(item.get("message") or item.get("prompt") or item.get("result") or "（无正文）")])
            detail = item.get("context_markdown") or item.get("detail")
            if detail:
                lines.extend(["", "详细记录：", "", str(detail)])
            if item.get("outcome"): lines.append(f"结果：{item['outcome']}")
            evidence = item.get("evidence") or []
            if evidence:
                lines.append("证据：")
                lines.extend(f"- {entry}" for entry in evidence)
            if item.get("result") and item.get("prompt"):
                lines.extend(["", "处理结果：", str(item["result"])])
            lines.append("")
    result = record.get("final_result")
    if result:
        lines.extend(["## 最终结果", ""])
        if isinstance(result, dict):
            when = result.get("at") or result.get("completed_at")
            if when: lines.append(f"完成时间：{when}")
            if result.get("outcome"): lines.append(f"结果：{result['outcome']}")
            if result.get("message"): lines.extend(["", str(result["message"])])
            for key, value in result.items():
                if key not in {"at", "completed_at", "outcome", "message", "evidence", "actor", "id", "kind"} and value not in (None, "", [], {}): lines.append(f"- {key}: {value}")
            for item in result.get("evidence", []): lines.append(f"- 证据：{item}")
        else: lines.append(str(result))
        lines.append("")
    attachments = record.get("attachments") or []
    if attachments:
        lines.extend(["## 图片附件", ""])
        for item in attachments:
            if isinstance(item, dict):
                name = item.get("filename", "image")
                url = item.get("download_url") or f"/api/items/{record.get('id')}/attachments/{name}"
                lines.append(f"- [{name}]({url}) ({item.get('mime_type', 'image')}, {item.get('size_bytes', '未知')} bytes)")
        lines.append("")
    rendered_fields = {
        "id", "type", "title", "status", "status_detail", "initial_brief", "latest_brief",
        "decisions", "phase", "blocked_reason", "next_step", "notes", "evidence", "history",
        "progress", "followups", "final_result", "attachments", "revision", "updated_at",
        "created_at", "source_updated_at", "observed_at", "evidence_at", "effective_status",
        "effective_status_basis", "current_agent_session_id", "current_agent_name", "last_agent_session_id", "last_agent_name",
        "last_agent_activity_at", "agent_state", "completed_at", "manual_status",
        "manual_completed_at", "manual_completed_by",
    }
    # Keep legacy/source-specific evidence available without repeating the
    # identity and status fields already shown in the detail header.
    extras = {key: value for key, value in record.items()
              if key not in rendered_fields and value not in (None, "", [], {})}
    if extras:
        lines.extend(["## 其他记录字段", "", "```json",
                      json.dumps(extras, ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines).rstrip() + "\n"


render_context = _context


def commit(root: Path, store_path: Path, db: dict[str, Any], *, force_context: bool = False,
           context_overrides: dict[str, str] | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    records_dir = root / "records"
    if store_path.exists() and not (root / "store.json.migrated.bak").exists() and not (root / "meta.json").exists():
        raise ValueError("explicit_migration_required")
    # First transition preserves the original monolith as a recoverable backup.
    if store_path.exists() and not (root / "store.json.migrated.bak").exists():
        _atomic_bytes(root / "store.json.migrated.bak", store_path.read_bytes())
    writes: dict[str, bytes] = {}
    ids: set[tuple[str, str]] = set()
    global_ids: set[str] = set()
    for record in db.get("records", []):
        kind, ident = record.get("type"), record.get("id")
        if kind not in {"work", "bug", "task"} or not isinstance(ident, str) or not ident or "/" in ident or ident in {".", ".."}:
            raise ValueError("invalid_record_identity")
        key = (kind, ident)
        if key in ids: raise ValueError("duplicate_record_id")
        ids.add(key)
        if ident in global_ids: raise ValueError("duplicate_record_id")
        global_ids.add(ident)
        record.setdefault("revision", int(db.get("revision", 0)))
        base = f"records/{kind}/{ident}"
        record_path = root / base / "record.json"
        context_path = root / base / "context.md"
        previous = None
        if record_path.is_file():
            try: previous = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError): previous = None
        if previous is None:
            record.setdefault("revision", int(db.get("revision", 0)))
        elif {k: v for k, v in previous.items() if k != "revision"} != {k: v for k, v in record.items() if k != "revision"}:
            record["revision"] = int(previous.get("revision", 0)) + 1
        else:
            # Callers may explicitly bump the item revision for a derived
            # context change even when structured record fields are identical.
            record["revision"] = max(int(previous.get("revision", 0)), int(record.get("revision", previous.get("revision", 0))))
        writes[base + "/record.json"] = _json(record)
        current_context = context_path.read_bytes() if context_path.is_file() else b""
        current_marker = f"<!-- mcloud-context-v{CONTEXT_FORMAT_VERSION} -->".encode()
        if context_overrides and base in context_overrides:
            writes[base + "/context.md"] = context_overrides[base].encode("utf-8")
        elif not force_context and previous == record and current_context.startswith(current_marker):
            writes[base + "/context.md"] = current_context
        else:
            writes[base + "/context.md"] = _context(record).encode("utf-8")
    meta = {k: v for k, v in db.items() if k != "records"}
    meta["schema_version"] = max(2, int(meta.get("schema_version", 1)))
    writes["meta.json"] = _json(meta)
    # Remove stale record directories only after journal application.
    manifest = {"writes": {name: data.decode("utf-8") for name, data in writes.items()},
                "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in writes.items()},
                "valid_records": sorted(f"{kind}/{ident}" for kind, ident in ids)}
    journal = root / ".transaction.json"
    _atomic_bytes(journal, _json(manifest))
    recover(root)


def rebuild_context(root: Path, store_path: Path) -> dict[str, Any]:
    db = load(root, store_path)
    changed = []
    for record in db.get("records", []):
        context_path = root / "records" / record["type"] / record["id"] / "context.md"
        rendered = _context(record).encode("utf-8")
        current = context_path.read_bytes() if context_path.is_file() else b""
        if current != rendered:
            record["revision"] = int(record.get("revision", 0)) + 1
            changed.append(record)
    if changed:
        db["revision"] = int(db.get("revision", 0)) + 1
    commit(root, store_path, db, force_context=True)
    return {"records": len(db.get("records", [])), "changed_records": len(changed),
            "context_format_version": CONTEXT_FORMAT_VERSION, "revision": db.get("revision", 0)}


def recover(root: Path) -> None:
    journal = root / ".transaction.json"
    if not journal.exists(): return
    tx = json.loads(journal.read_text(encoding="utf-8"))
    for rel, text in tx["writes"].items():
        path = (root / rel).resolve()
        if root.resolve() not in path.parents: raise ValueError("transaction_path_invalid")
        raw = text.encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != tx["sha256"].get(rel): raise ValueError("transaction_checksum_mismatch")
        _atomic_bytes(path, raw)
    keep = set(tx["valid_records"])
    records = root / "records"
    if records.exists():
        for kind_dir in records.iterdir():
            if not kind_dir.is_dir(): continue
            for entry in kind_dir.iterdir():
                if entry.is_dir() and f"{kind_dir.name}/{entry.name}" not in keep:
                    import shutil
                    shutil.rmtree(entry)
    journal.unlink()


def load(root: Path, legacy: Path) -> dict[str, Any]:
    recover(root)
    meta_path = root / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        records = []
        base = root / "records"
        if base.exists():
            for kind in ("work", "bug", "task"):
                for path in sorted((base / kind).glob("*/record.json")) if (base / kind).exists() else []:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    expected = path.parent.name
                    if record.get("id") != expected or record.get("type") != kind: raise ValueError("record_integrity_mismatch")
                    records.append(record)
        meta["records"] = records
        return meta
    if legacy.exists():
        db = json.loads(legacy.read_text(encoding="utf-8"))
        if not isinstance(db, dict) or not isinstance(db.get("records"), list): raise ValueError("legacy_store_invalid")
        # Migration occurs only when the first ordinary write commits.
        return db
    return {"schema_version": 2, "revision": 0, "records": [], "sync": {}, "sources": [], "batches": []}


def read_context(root: Path, kind: str, ident: str, max_bytes: int | None = None) -> str:
    if kind not in {"work", "bug", "task"} or not ident or "/" in ident or ident in {".", ".."}: raise ValueError("record_not_found")
    path = (root / "records" / kind / ident / "context.md").resolve()
    base = (root / "records" / kind).resolve()
    if base not in path.parents or not path.is_file(): raise ValueError("record_not_found")
    if max_bytes is not None and path.stat().st_size > max_bytes: raise ValueError("context_too_large")
    return path.read_text(encoding="utf-8")


def migration_plan(root: Path, legacy: Path) -> dict[str, Any]:
    db = load(root, legacy)
    records = db.get("records", [])
    digest_records = sorted(({k: v for k, v in record.items() if k != "revision"} for record in records), key=lambda x: (x.get("type", ""), x.get("id", "")))
    return {"source": str(legacy), "target": str(root / "records"), "record_count": len(records),
            "ids": sorted(f"{x.get('type')}/{x.get('id')}" for x in records),
            "followup_count": sum(len(x.get("followups", [])) for x in records),
            "history_count": sum(len(x.get("history", [])) for x in records),
            "source_count": len(db.get("sources", [])), "source_ids": sorted(x.get("id", "") for x in db.get("sources", [])),
            "batch_count": len(db.get("batches", [])), "batch_ids": sorted(x.get("id", "") for x in db.get("batches", [])),
            "records_sha256": hashlib.sha256(_json(digest_records)).hexdigest(),
            "already_migrated": (root / "meta.json").exists()}


def migrate(root: Path, legacy: Path) -> dict[str, Any]:
    plan = migration_plan(root, legacy)
    if plan["already_migrated"]: return plan
    db = json.loads(legacy.read_text(encoding="utf-8"))
    if legacy.exists() and not (root / "store.json.migrated.bak").exists():
        _atomic_bytes(root / "store.json.migrated.bak", legacy.read_bytes())
    events = root / "events.jsonl"
    if events.exists() and not (root / "events.jsonl.migrated.bak").exists():
        _atomic_bytes(root / "events.jsonl.migrated.bak", events.read_bytes())
    commit(root, legacy, db)
    # Verify the complete preserved inventory after journal replay.
    loaded = load(root, legacy)
    actual = migration_plan(root, legacy)
    for key in ("record_count", "ids", "followup_count", "history_count", "source_count", "source_ids", "batch_count", "batch_ids", "records_sha256"):
        if actual[key] != plan[key]: raise ValueError("migration_integrity_mismatch")
    actual["migrated"] = True
    return actual
