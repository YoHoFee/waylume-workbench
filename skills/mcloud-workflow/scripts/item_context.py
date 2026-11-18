#!/usr/bin/env python3
"""Read one bounded, structured context package from the local mCloud dashboard."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from handoff_view import handoff_view


DEFAULT_DASHBOARD_ROOT = Path("/Users/mr.yao/.codex/work-agent/tools/mcloud/dashboard")
KINDS = ("work", "bug", "task")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z")
MAX_RECORD_BYTES = 5 * 1024 * 1024
MAX_CONTEXT_BYTES = 200 * 1024
MAX_CONTEXT_CHARS = 20_000
DEFAULT_TEXT_CHARS = 800
MAX_TEXT_CHARS = 4_000
MAX_LIST_ITEMS = 20
MAX_ATTACHMENTS = 50
MAX_PENDING_FOLLOWUPS = 10
MAX_OUTPUT_BYTES = 96 * 1024
MAX_SCOPE_CATALOG_BYTES = 1024 * 1024
MAX_SCOPE_MATCHES = 20
MAX_SCOPE_EVIDENCE = 10
SCOPE_STATUSES = {"confirmed", "candidate", "not_ours"}
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(access[_-]?token|refresh[_-]?token|api[_-]?key|token|password|passwd|secret|authorization)"
    r"(\s*[=:]\s*|\s+)(?:bearer\s+)?([^\s&;,\"']+)"
)
SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:access[_-]?token|refresh[_-]?token|api[_-]?key|token|password|secret|sig|signature)=)[^&#\s]+"
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")


class ReadError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def bounded_text(value: Any, limit: int) -> Any:
    if not isinstance(value, str):
        return value
    value = SECRET_VALUE_RE.sub(lambda match: match.group(1) + match.group(2) + "[REDACTED]", value)
    value = SECRET_QUERY_RE.sub(lambda match: match.group(1) + "[REDACTED]", value)
    value = BEARER_RE.sub("Bearer [REDACTED]", value)
    if len(value) <= limit:
        return value
    return value[:limit] + "…[truncated]"


def bounded_list(value: Any, limit: int, text_limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        if isinstance(item, str):
            result.append(bounded_text(item, text_limit))
        elif isinstance(item, dict):
            result.append({str(k): bounded_text(v, text_limit) for k, v in item.items()
                           if isinstance(k, str) and k not in {"actor", "session_id"}
                           and (v is None or isinstance(v, (str, int, float, bool)))} )
        else:
            result.append(item)
    return result


def find_record(root: Path, record_id: str) -> tuple[str, Path]:
    if not ID_RE.fullmatch(record_id) or record_id in {".", ".."}:
        raise ReadError("invalid_id", "ID must be a single dashboard record identifier")
    matches: list[tuple[str, Path]] = []
    for kind in KINDS:
        candidate = root / "records" / kind / record_id / "record.json"
        if candidate.is_file():
            matches.append((kind, candidate))
    if not matches:
        raise ReadError("record_not_found", "No dashboard record exists for this ID", id=record_id)
    if len(matches) > 1:
        raise ReadError("ambiguous_record", "The ID exists in more than one dashboard record type", id=record_id)
    kind, path = matches[0]
    resolved_root = (root / "records" / kind).resolve()
    if resolved_root not in path.resolve().parents:
        raise ReadError("path_outside_dashboard", "Resolved record path escapes its type directory")
    return kind, path


def read_record(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
        if size > MAX_RECORD_BYTES:
            raise ReadError("record_too_large", "Record exceeds the 5 MiB read limit", size_bytes=size)
        value = json.loads(path.read_text(encoding="utf-8"))
    except ReadError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadError("record_unreadable", "Dashboard record could not be read as UTF-8 JSON",
                        detail=type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ReadError("record_invalid", "Dashboard record root must be a JSON object")
    return value


def scope_context(root: Path, record: dict[str, Any], text_limit: int) -> dict[str, Any]:
    """Return only catalog entries linked from this record; never infer ownership."""
    result: dict[str, Any] = {
        "catalog_revision": None,
        "matched_scopes": [],
        "unclassified": True,
        "scope_gap": None,
    }
    path = root / "scope-catalog.json"
    try:
        if not path.is_file():
            result["scope_gap"] = {"code": "scope_catalog_missing"}
            return result
        if path.resolve().parent != root.resolve():
            result["scope_gap"] = {"code": "scope_catalog_outside_dashboard"}
            return result
        size = path.stat().st_size
        if size > MAX_SCOPE_CATALOG_BYTES:
            result["scope_gap"] = {"code": "scope_catalog_too_large", "size_bytes": size}
            return result
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result["scope_gap"] = {
            "code": "scope_catalog_unreadable",
            "detail": type(exc).__name__,
        }
        return result

    if not isinstance(catalog, dict):
        result["scope_gap"] = {"code": "scope_catalog_invalid"}
        return result
    revision = catalog.get("revision")
    catalog_version = catalog.get("version")
    scopes = catalog.get("scopes")
    if (
        type(catalog_version) is not int or catalog_version < 1
        or type(revision) is not int or revision < 1
        or not isinstance(scopes, list)
    ):
        result["scope_gap"] = {"code": "scope_catalog_invalid"}
        return result

    valid_scopes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in scopes:
        if not isinstance(entry, dict):
            result["scope_gap"] = {"code": "scope_catalog_invalid"}
            return result
        scope_id = entry.get("id")
        related_ids = entry.get("related_ids")
        evidence = entry.get("evidence")
        if (
            not isinstance(scope_id, str) or not ID_RE.fullmatch(scope_id)
            or scope_id in seen_ids
            or not isinstance(entry.get("name"), str)
            or entry.get("status") not in SCOPE_STATUSES
            or type(entry.get("version")) is not int or entry["version"] < 1
            or not isinstance(entry.get("in_scope"), list)
            or not all(isinstance(value, str) for value in entry["in_scope"])
            or not isinstance(entry.get("out_of_scope"), list)
            or not all(isinstance(value, str) for value in entry["out_of_scope"])
            or not isinstance(related_ids, list)
            or not all(isinstance(value, str) for value in related_ids)
            or not isinstance(evidence, list)
            or not all(
                isinstance(value, dict)
                and all(isinstance(value.get(key), str) for key in ("type", "reference", "summary"))
                and (value.get("date") is None or isinstance(value.get("date"), str))
                for value in evidence
            )
        ):
            result["scope_gap"] = {"code": "scope_catalog_invalid"}
            return result
        seen_ids.add(scope_id)
        valid_scopes.append(entry)

    result["catalog_revision"] = revision
    record_ids = record.get("related_ids")
    if not isinstance(record_ids, list):
        record_ids = []
    record_id_set = {value for value in record_ids if isinstance(value, str)}
    matched = [entry for entry in valid_scopes if entry["id"] in record_id_set]
    selected = matched[:MAX_SCOPE_MATCHES]
    summaries: list[dict[str, Any]] = []
    for entry in selected:
        evidence_items = []
        for evidence in entry["evidence"][:MAX_SCOPE_EVIDENCE]:
            evidence_items.append({
                key: bounded_text(evidence[key], text_limit)
                for key in ("type", "date", "reference", "summary")
                if isinstance(evidence.get(key), str)
            })
        summaries.append({
            "id": bounded_text(entry["id"], text_limit),
            "name": bounded_text(entry["name"], text_limit),
            "status": entry["status"],
            "version": entry["version"],
            "in_scope": bounded_list(entry["in_scope"], MAX_LIST_ITEMS, text_limit),
            "out_of_scope": bounded_list(entry["out_of_scope"], MAX_LIST_ITEMS, text_limit),
            "evidence": evidence_items,
            "omitted_evidence_count": max(0, len(entry["evidence"]) - MAX_SCOPE_EVIDENCE),
        })
    result["matched_scopes"] = summaries
    result["unclassified"] = not bool(matched)
    if len(matched) > MAX_SCOPE_MATCHES:
        result["scope_gap"] = {
            "code": "scope_matches_truncated",
            "omitted_match_count": len(matched) - MAX_SCOPE_MATCHES,
        }
    return result


def attachment_index(record: dict[str, Any], text_limit: int) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    omitted = 0
    for field in ("attachments", "source_attachments"):
        items = record.get(field)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if len(entries) >= MAX_ATTACHMENTS:
                omitted += 1
                continue
            entry = {key: item.get(key) for key in (
                "id", "display_name", "filename", "kind", "mime_type", "size_bytes",
                "sha256", "download_state", "error", "source_signature", "added_at",
                "download_url", "local_relative_path",
            ) if key in item}
            entry["manifest"] = field
            entries.append({key: bounded_text(value, text_limit) for key, value in entry.items()})
    return {"items": entries, "omitted_count": omitted,
            "note": "索引来自记录 manifest；未读取或审阅附件内容。"}


def pending_user_updates(record: dict[str, Any], text_limit: int) -> dict[str, Any]:
    pending = record.get("followups")
    if not isinstance(pending, list):
        pending = []
    pending = [entry for entry in pending if isinstance(entry, dict)
               and entry.get("status") == "pending"]
    processing = [entry for entry in record.get("followups", []) if isinstance(entry, dict)
                  and entry.get("status") in {"processing", "claimed"}] if isinstance(record.get("followups"), list) else []
    def preview(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{key: bounded_text(entry.get(key), text_limit) for key in
                 ("id", "created_at", "created_by", "status", "prompt") if key in entry}
                for entry in entries[-MAX_PENDING_FOLLOWUPS:]]
    comments: list[dict[str, Any]] = []
    for field in ("user_comments", "comments"):
        values = record.get(field)
        if isinstance(values, list):
            for comment in values:
                if isinstance(comment, dict):
                    comments.append({key: bounded_text(comment.get(key), text_limit) for key in
                                     ("id", "created_at", "author", "text", "message", "status") if key in comment})
    comments = comments[-MAX_PENDING_FOLLOWUPS:]
    return {
        "pending_followup_count": len(pending),
        "pending_followups": preview(pending),
        "processing_followup_count": len(processing),
        "processing_followups": preview(processing),
        "omitted_followup_count": max(0, len(pending) - MAX_PENDING_FOLLOWUPS) + max(0, len(processing) - MAX_PENDING_FOLLOWUPS),
        "user_comment_count": len(record.get("user_comments", [])) + len(record.get("comments", []))
        if isinstance(record.get("user_comments", []), list) and isinstance(record.get("comments", []), list) else len(comments),
        "user_comments": comments,
        "omitted_comment_count": max(0, sum(len(record.get(field, [])) for field in ("user_comments", "comments")
                                               if isinstance(record.get(field), list)) - len(comments)),
    }


def make_package(root: Path, record_id: str, include_context: bool, text_limit: int) -> dict[str, Any]:
    kind, record_path = find_record(root, record_id)
    record = read_record(record_path)
    if record.get("id") not in (None, record_id) or record.get("type") not in (None, kind):
        raise ReadError("record_integrity_mismatch", "Record ID or type does not match its dashboard path")

    keys = (
        "id", "type", "title", "status", "status_detail", "platform_status", "our_status",
        "triage", "phase", "owner", "assignee", "current_agent_name", "last_agent_name",
        "current_agent_session_id", "last_agent_session_id", "last_agent_activity_at",
        "agent_state", "initial_brief", "latest_brief", "final_result", "next_action",
        "next_step", "blocked_reason", "blocker", "revision", "updated_at", "source",
        "source_ref", "source_updated_at", "observed_at", "evidence_at", "verification_state",
        "stale", "transfer_disposition", "completion_kind", "related_ids",
    )
    item = {key: record[key] for key in keys if key in record}
    for key, value in list(item.items()):
        if isinstance(value, str):
            item[key] = bounded_text(value, text_limit)
        elif isinstance(value, list):
            item[key] = bounded_list(value, MAX_LIST_ITEMS, text_limit)
    item["evidence_references"] = bounded_list(record.get("evidence"), MAX_LIST_ITEMS, text_limit)
    item["notes_preview"] = bounded_list(record.get("notes"), 5, text_limit)
    item["user_updates"] = pending_user_updates(record, text_limit)
    item["scope_context"] = scope_context(root, record, text_limit)
    bug_detail = record.get("bug_detail")
    if isinstance(bug_detail, dict):
        item["bug_intake"] = {
            key: bounded_text(bug_detail[key], text_limit) for key in
            ("description", "steps", "expected", "actual", "read_state", "error_summary",
             "gaps", "attachments_read_state", "attachments_gaps", "video_review_state")
            if key in bug_detail and isinstance(bug_detail[key], str)
        }
        for key in ("gaps", "attachments_gaps"):
            if isinstance(bug_detail.get(key), list):
                item["bug_intake"][key] = bounded_list(bug_detail[key], MAX_LIST_ITEMS, text_limit)
    item["attachment_index"] = attachment_index(record, text_limit)
    projection = handoff_view(record)
    projection["fields"] = {key: bounded_text(value, text_limit) for key, value in projection["fields"].items()}
    projection["summary"] = bounded_text(projection["summary"], text_limit)
    for key in ("handoff_updated_at", "summary_updated_at"):
        if isinstance(projection.get(key), str):
            projection[key] = bounded_text(projection[key], text_limit)
    for key, value in projection.get("platform_snapshot", {}).items():
        if isinstance(value, str):
            projection["platform_snapshot"][key] = bounded_text(value, text_limit)
    item["handoff"] = projection
    result: dict[str, Any] = {
        "ok": True,
        "package_kind": "item_context",
        "record_path": str(record_path),
        "item": item,
        "context_included": False,
        "credential_redaction": "Common labeled credentials, bearer values, and sensitive query values are redacted from rendered text fields.",
        "limits": {"text_chars_per_value": text_limit, "record_bytes": MAX_RECORD_BYTES,
                   "attachment_index_entries": MAX_ATTACHMENTS, "output_bytes": MAX_OUTPUT_BYTES},
    }
    if include_context:
        context_path = record_path.parent / "context.md"
        try:
            size = context_path.stat().st_size
            if size > MAX_CONTEXT_BYTES:
                raise ReadError("context_too_large", "Context exceeds the 200 KiB read limit", size_bytes=size)
            context = context_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ReadError("context_not_found", "Full context was requested but is not present")
        except ReadError:
            raise
        except (OSError, UnicodeError) as exc:
            raise ReadError("context_unreadable", "Context could not be read as UTF-8",
                            detail=type(exc).__name__) from exc
        result["context_markdown"] = bounded_text(context, MAX_CONTEXT_CHARS)
        result["context_included"] = True
        result["context_truncated"] = len(context) > MAX_CONTEXT_CHARS
    # Keep even an unusually dense record inside a hard output bound while preserving JSON.
    # Reduce repeated preview arrays first; never drop the counts or truncation notice.
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        for collection in ("evidence_references", "notes_preview"):
            values = item.get(collection)
            if isinstance(values, list) and len(values) > 3:
                item[collection] = values[:3]
                item[f"{collection}_omitted_count"] = len(values) - 3
        attachments = item["attachment_index"]
        if len(attachments["items"]) > 10:
            attachments["omitted_count"] += len(attachments["items"]) - 10
            attachments["items"] = attachments["items"][:10]
        updates = item["user_updates"]
        if len(updates["pending_followups"]) > 3:
            updates["omitted_followup_count"] += len(updates["pending_followups"]) - 3
            updates["pending_followups"] = updates["pending_followups"][:3]
        if len(updates["processing_followups"]) > 3:
            updates["omitted_followup_count"] += len(updates["processing_followups"]) - 3
            updates["processing_followups"] = updates["processing_followups"][:3]
        if len(updates["user_comments"]) > 3:
            updates["omitted_comment_count"] += len(updates["user_comments"]) - 3
            updates["user_comments"] = updates["user_comments"][:3]
        result["truncated_for_output_limit"] = True
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        # Long explicitly requested context is the only remaining potentially large field.
        if "context_markdown" in result:
            result["context_markdown"] = bounded_text(result["context_markdown"], 4_000)
            result["context_truncated"] = True
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        def shrink(value: Any) -> Any:
            if isinstance(value, str):
                return bounded_text(value, 160)
            if isinstance(value, list):
                return [shrink(entry) for entry in value[:10]]
            if isinstance(value, dict):
                return {key: shrink(entry) for key, entry in value.items()}
            return value
        result = shrink(result)
        result["truncated_for_output_limit"] = True
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ReadError("output_too_large", "Package exceeded the 96 KiB output limit after truncation")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a bounded mCloud dashboard item context package")
    parser.add_argument("record_id", help="Exact stable dashboard record ID")
    parser.add_argument("--dashboard-root", type=Path, default=DEFAULT_DASHBOARD_ROOT,
                        help="Dashboard data root (for isolated fixtures, use a temporary directory)")
    parser.add_argument("--include-context", action="store_true",
                        help="Also include context.md, capped at 20,000 characters and 200 KiB source size")
    parser.add_argument("--text-limit", type=int, default=DEFAULT_TEXT_CHARS,
                        help="Maximum characters per structured text value (1-4000; default 800)")
    args = parser.parse_args()
    if not 1 <= args.text_limit <= MAX_TEXT_CHARS:
        error = ReadError("invalid_text_limit", "--text-limit must be between 1 and 4000")
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}, ensure_ascii=False))
        return 2
    try:
        output = make_package(args.dashboard_root.expanduser(), args.record_id,
                              args.include_context, args.text_limit)
    except ReadError as exc:
        output = {"ok": False, "error": {"code": exc.code, "message": str(exc), **exc.details}}
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
        return 1
    serialized = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_OUTPUT_BYTES:
        error = {"ok": False, "error": {"code": "output_too_large", "message": "Output exceeded 96 KiB"}}
        print(json.dumps(error, ensure_ascii=False, separators=(",", ":")))
        return 1
    print(serialized)
    return 0


if __name__ == "__main__":
    sys.exit(main())
