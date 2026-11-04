#!/usr/bin/env python3
"""Read-only localhost dashboard for the mCloud personal work ledger."""

from __future__ import annotations

import fcntl
import base64
import binascii
import json
import mimetypes
import re
import subprocess
import os
import shutil
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, quote, urlsplit

import dashboardctl
import data_location
import integrations
import waylume_paths
from handoff_view import handoff_view


CODE_ROOT = Path(__file__).resolve().parent
ROOT = waylume_paths.data_dir()
WEB = CODE_ROOT / "web"
STORE = ROOT / "store.json"
SCOPE_CATALOG = ROOT / "scope-catalog.json"
HOST = "127.0.0.1"
PORT = int(os.environ.get("MCLOUD_DASHBOARD_PORT", "8765"))
MAX_SOURCE_REQUEST_BYTES = 21 * 1024 * 1024
MAX_AGENT_REPORT_REQUEST_BYTES = 128 * 1024
MAX_RECORD_IMAGE_REQUEST_BYTES = 14 * 1024 * 1024
MAX_CONTEXT_RESPONSE_BYTES = 256 * 1024
MAX_RECORD_IMAGE_BYTES = 10 * 1024 * 1024
CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
try:
    import mcloud_patrol_adapter
    _PATROL_PATHS = mcloud_patrol_adapter.resolve_paths()
except (ImportError, OSError, ValueError):
    # Keep the general workbench usable if the optional patrol module or its
    # registration is absent/invalid. Patrol API calls then report unavailable.
    _PATROL_PATHS = None
INSPECTION_CONFIG = _PATROL_PATHS.schedule_config if _PATROL_PATHS else CODEX_HOME / "automations/bug-2/automation.toml"
INSPECTION_SLOTS = _PATROL_PATHS.slots if _PATROL_PATHS else None
INSPECTION_DISPATCHER = _PATROL_PATHS.dispatcher if _PATROL_PATHS else None
INSPECTION_ZONE = "Asia/Shanghai"
_instance_lock = None


def refresh_data_paths() -> None:
    global ROOT, STORE, SCOPE_CATALOG
    dashboardctl.refresh_data_paths()
    ROOT = dashboardctl.ROOT
    STORE = ROOT / "store.json"
    SCOPE_CATALOG = ROOT / "scope-catalog.json"


class ApiError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def now_local() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def inspection_schedule() -> dict:
    """Expose a tiny, sanitized view of the one allowlisted bug-2 schedule."""
    try:
        raw = INSPECTION_CONFIG.read_text(encoding="utf-8")
        try:
            import tomllib  # Python 3.11+
            config = tomllib.loads(raw)
            status, rule, prompt = config.get("status"), config.get("rrule"), config.get("prompt", "")
        except ImportError:  # macOS system Python may predate tomllib
            status_match = re.search(r'^status\s*=\s*"([A-Z]+)"\s*$', raw, re.M)
            rule_match = re.search(r'^rrule\s*=\s*"([^"]+)"\s*$', raw, re.M)
            prompt_match = re.search(r'^prompt\s*=\s*"(.*)"\s*$', raw, re.M)
            status = status_match.group(1) if status_match else None
            rule = rule_match.group(1) if rule_match else None
            prompt = prompt_match.group(1) if prompt_match else ""
        if not isinstance(status, str) or not isinstance(rule, str):
            return {"available": False, "state": "unknown"}
        match = re.fullmatch(
            r"(?:RRULE:)?FREQ=(DAILY|WEEKLY);"
            r"(?:BYDAY=([A-Z,]+);)?BYHOUR=([0-9,]+);BYMINUTE=([0-9]{1,2})",
            rule,
        )
        if not match:
            return {"available": False, "state": "unknown"}
        frequency, raw_days, raw_hours, raw_minute = match.groups()
        if frequency == "WEEKLY" and not raw_days:
            return {"available": False, "state": "unknown"}
        days = raw_days.split(",") if raw_days else ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
        valid_days = {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}
        if not days or any(day not in valid_days for day in days) or len(set(days)) != len(days):
            return {"available": False, "state": "unknown"}
        hours = [int(hour) for hour in raw_hours.split(",")]
        minute = int(raw_minute)
        # Prompts often begin with absolute paths such as `/Users/...`;
        # select a slash-delimited token only if it is a real IANA timezone.
        timezone = None
        for zone_match in re.finditer(r"\b([A-Za-z_]+/[A-Za-z_]+)\b", prompt):
            candidate = zone_match.group(1)
            try:
                ZoneInfo(candidate)
            except (KeyError, ValueError):
                continue
            timezone = candidate
            break
        if timezone is None:
            # The Codex automation format currently stores status and RRULE,
            # but no timezone field. Keep the established workbench fallback
            # until the host supplies an explicit zone.
            timezone = INSPECTION_ZONE
        if not hours or any(hour < 0 or hour > 23 for hour in hours) or minute > 59:
            return {"available": False, "state": "unknown"}
        if not timezone or timezone != INSPECTION_ZONE:
            return {"available": False, "state": "unknown"}
        return {
            "available": True,
            "enabled": status == "ACTIVE",
            "state": "active" if status == "ACTIVE" else "paused" if status == "PAUSED" else "unknown",
            "timezone": timezone,
            "times": [f"{hour:02d}:{minute:02d}" for hour in hours],
            "weekdays": days,
            "server_time": datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds"),
        }
    except (OSError, ValueError, TypeError):
        return {"available": False, "state": "unknown"}


def bug_inspection_status() -> dict:
    schedule = inspection_schedule()
    result = {
        "available": False,
        "dispatch_available": False,
        "enabled": bool(schedule.get("enabled", False)),
        "window_open": False,
        "timezone": INSPECTION_ZONE,
        "next_run_at": None,
        "slot_id": None,
        "event_id": None,
        "status": "unavailable",
        "slot_state": None,
        "active_event": None,
        "last_event": None,
    }
    try:
        now = datetime.now(ZoneInfo(INSPECTION_ZONE))
        result["window_open"] = (now.hour > 8 or (now.hour == 8 and now.minute >= 30)) and (now.hour < 20 or (now.hour == 20 and now.minute <= 30))
        if not isinstance(INSPECTION_SLOTS, Path) or not INSPECTION_SLOTS.is_file():
            return result
        completed = subprocess.run(
            [sys.executable, str(INSPECTION_SLOTS), "status"],
            check=False, capture_output=True, text=True, timeout=3,
        )
        if completed.returncode != 0 or len(completed.stdout) > 65536:
            return result
        slot_data = json.loads(completed.stdout)
        if not isinstance(slot_data, dict) or slot_data.get("timezone") != INSPECTION_ZONE:
            return result
        active = slot_data.get("active_run")
        last_run = slot_data.get("last_run")
        if isinstance(last_run, dict):
            result["last_event"] = {key: last_run[key] for key in (
                "event_id", "slot_id", "source", "status", "result", "reserved_at", "started_at", "finished_at", "detail"
            ) if key in last_run}
        if isinstance(active, dict):
            # The helper's ownership token is internal coordination data.
            active = {key: value for key, value in active.items() if key != "token"}
            result["active_event"] = active
            result["event_id"] = active.get("event_id")
            active_status = active.get("status", "queued")
            result["status"] = "queued" if active_status == "reserved" else active_status
            result["slot_state"] = active.get("status")
        else:
            slot = slot_data.get("slot")
            if isinstance(slot, dict):
                result["slot_state"] = slot.get("status") or slot.get("result")
            if isinstance(last_run, dict):
                result["status"] = last_run.get("status") or last_run.get("result") or "idle"
                result["event_id"] = last_run.get("event_id")
            elif isinstance(slot, dict):
                result["status"] = slot.get("status") or slot.get("result") or "idle"
            else:
                result["status"] = "idle"
        result.update({
            "available": True,
            "dispatch_available": isinstance(INSPECTION_DISPATCHER, Path) and INSPECTION_DISPATCHER.is_file(),
            "next_run_at": slot_data.get("next_at"),
            "slot_id": slot_data.get("slot_id"),
        })
        return result
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError):
        return result


def effective_status(record: dict) -> tuple[str, str]:
    return dashboardctl.effective_work_status(record)


def public_source(source: dict) -> dict:
    result = dict(source)
    result.setdefault("last_read_at", None)
    result.setdefault("read_state", None)
    result.setdefault("read_scope", None)
    result.setdefault("content_updated_at", None)
    result.setdefault("read_gap", [])
    result.setdefault("snapshot_revision", None)
    result.setdefault("content_hash", None)
    result.setdefault("unchanged_since", None)
    result.setdefault("last_successful_read_at", None)
    result.setdefault("last_recheck_at", None)
    result.setdefault("lifecycle_status", "registered")
    result.setdefault("suspected_deprecated", result["lifecycle_status"] == "suspected_deprecated")
    if source.get("type") == "link":
        from urllib.parse import parse_qsl, urlencode, urlunsplit
        parts = urlsplit(source.get("url", ""))
        safe_query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if re.search(r"token|secret|password|credential|auth|share.?code|access.?key|api.?key|signature|(^|_)sig($|_)|session", key, re.I):
                value = "[redacted]"
            safe_query.append((key, value))
        result["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_query), parts.fragment))
        result["open_path"] = f"/api/sources/{source['id']}/open"
        result.pop("normalized_url", None)
    elif source.get("type") == "file":
        result["download_url"] = f"/api/sources/{source['id']}/file"
    return result


def load_scope_catalog() -> dict:
    """Read the explicitly maintained, versioned responsibility catalog."""
    try:
        catalog = json.loads(SCOPE_CATALOG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "revision": 0, "updated_at": None, "scopes": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("scope_catalog_unavailable") from exc
    if (not isinstance(catalog, dict) or catalog.get("version") != 1
            or not isinstance(catalog.get("revision"), int)
            or not isinstance(catalog.get("scopes"), list)):
        raise ValueError("scope_catalog_invalid")
    valid_statuses = {"confirmed", "candidate", "not_ours"}
    seen = set()
    for scope in catalog["scopes"]:
        if (not isinstance(scope, dict) or not isinstance(scope.get("id"), str)
                or scope["id"] in seen or scope.get("status") not in valid_statuses
                or not isinstance(scope.get("version"), int)):
            raise ValueError("scope_catalog_invalid")
        seen.add(scope["id"])
    return catalog


def build_payload() -> dict:
    """Expose only records explicitly entered into the local structured store."""
    if STORE.is_file() or (dashboardctl.ROOT / "meta.json").is_file():
        db = dashboardctl.load_store()
        records = db.get("records", [])
        sync = db.get("sync", {})
        revision = db.get("revision", 0)
    else:
        db = {"sources": []}
        records, sync, revision = [], {}, 0
    grouped = {
        "requirements": [item for item in records if item.get("type") == "work"],
        "bugs": [item for item in records if item.get("type") == "bug"],
        "delegated": [item for item in records if item.get("type") == "task"],
    }
    def creation_times() -> dict[str, str]:
        created = {}
        try:
            with dashboardctl.EVENTS.open(encoding="utf-8") as stream:
                for line in stream:
                    try: event = json.loads(line)
                    except json.JSONDecodeError: continue
                    if event.get("type") == "task" and event.get("action") == "create":
                        created.setdefault(str(event.get("id") or ""), str(event.get("at") or ""))
        except OSError:
            pass
        return created
    task_created_at = dashboardctl.with_lock(creation_times)
    for items in grouped.values():
        for item in items:
            item["source_status"] = item.get("status")
            item["effective_status"], item["effective_status_basis"] = effective_status(item)
            item.setdefault("current_agent_session_id", None)
            item.setdefault("current_agent_name", None)
            item.setdefault("last_agent_session_id", None)
            item.setdefault("last_agent_name", None)
            item.setdefault("last_agent_activity_at", None)
            item["completed_at"] = dashboardctl.completion_timestamp(item)
            item.setdefault("revision", 0)
            if item.get("type") == "task":
                item.setdefault("created_at", task_created_at.get(str(item.get("id") or "")) or
                                next((entry.get("at") for entry in item.get("progress", []) if isinstance(entry, dict) and entry.get("at")),
                                     item.get("updated_at")))
                item.setdefault("initial_brief", item.get("status_detail") or item.get("title", ""))
                item.setdefault("latest_brief", "")
                item.setdefault("progress", [])
                item.setdefault("final_result", None)
            followups = item.get("followups", [])
            pending = [entry for entry in followups if entry.get("status") == "pending"]
            claimed = [entry for entry in followups if entry.get("status") == "claimed"]
            item["followup_preview"] = pending[-1] if pending else claimed[-1] if claimed else followups[-1] if followups else None
        items.sort(key=lambda item: (item.get("status") != "blocked", item.get("updated_at", "")), reverse=False)
    batches = db.get("batches", [])
    if not isinstance(batches, list):
        batches = []
    legacy_task_ids = [item.get("id") for item in grouped["delegated"] if not item.get("batch_id")]
    return {
        "generated_at": now_local(),
        "data_state": "ready" if records else "empty",
        # `revision` is retained as the dashboard-wide store token for existing readers.
        "revision": revision,
        "store_revision": revision,
        "current_batch_id": db.get("current_batch_id"),
        "batches": batches,
        "legacy_task_ids": legacy_task_ids,
        "preferences": db.get("preferences", {}),
        **grouped,
        "sync": sync,
        "sources": [public_source(item) for item in dashboardctl._source_store_list(db) if item.get("active")],
        "scope_catalog": load_scope_catalog(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "MCloudDashboard/1.0"

    def do_HEAD(self) -> None:  # noqa: N802
        route = urlsplit(self.path).path
        attachment = re.fullmatch(r"/api/items/([A-Za-z0-9_.:-]{1,120})/attachments/([A-Za-z0-9][A-Za-z0-9._-]{0,119})", route)
        if not attachment:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        try:
            total = dashboardctl.attachment_size(attachment.group(1), attachment.group(2))
            _, mime, _ = dashboardctl.read_record_attachment(attachment.group(1), attachment.group(2), (0, 0))
        except (ValueError, OSError):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        disposition = "inline" if mime.startswith(("image/", "video/")) else "attachment"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition", f"{disposition}; filename=\"{attachment.group(2)}\"")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(total))
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        route = urlsplit(self.path).path
        if route == "/api/data-location":
            self._send_json(data_location.location()); return
        if route == "/api/integrations/status":
            self._send_json(integrations.status()); return
        if route == "/api/preferences":
            db = dashboardctl.load_store()
            self._send_json({"revision": db.get("revision", 0), "preferences": db.get("preferences", {})}); return
        migration_job = re.fullmatch(r"/api/data-location/migrations/([A-Za-z0-9_-]{12,80})", route)
        if migration_job:
            result = data_location.job(migration_job.group(1))
            if result is None: self._send_json({"error": "migration_job_not_found"}, status=404)
            else: self._send_json(result)
            return
        image = re.fullmatch(r"/api/items/([A-Za-z0-9_.:-]{1,120})/attachments/([A-Za-z0-9][A-Za-z0-9._-]{0,119})", route)
        if image:
            try:
                total = dashboardctl.attachment_size(image.group(1), image.group(2))
                requested_range = self.headers.get("Range")
                byte_range = None
                if requested_range:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested_range.strip())
                    if not match or (not match.group(1) and not match.group(2)):
                        self.send_response(416); self.send_header("Content-Range", f"bytes */{total}"); self.send_header("Content-Length", "0"); self.end_headers(); return
                    if match.group(1):
                        start = int(match.group(1)); end = min(int(match.group(2)) if match.group(2) else total - 1, total - 1)
                    else:
                        length = int(match.group(2)); start = max(0, total - length); end = total - 1
                    if start >= total or end < start:
                        self.send_response(416); self.send_header("Content-Range", f"bytes */{total}"); self.send_header("Content-Length", "0"); self.end_headers(); return
                    byte_range = (start, end)
                body, mime, info = dashboardctl.read_record_attachment(image.group(1), image.group(2), byte_range)
            except ValueError:
                self._send_json({"error": "record_attachment_not_found"}, status=404); return
            partial = byte_range is not None
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", mime)
            disposition = "inline" if mime.startswith(("image/", "video/")) else "attachment"
            self.send_header("Content-Disposition", f"{disposition}; filename=\"{image.group(2)}\"")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Accept-Ranges", "bytes")
            if partial: self.send_header("Content-Range", f"bytes {info['start']}-{info['end']}/{info['size']}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        source_open = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]{1,100})/open", route)
        if source_open:
            source = next((item for item in dashboardctl._source_store_list(dashboardctl.load_store()) if item.get("id") == source_open.group(1) and item.get("active")), None)
            if source and source.get("type") == "link":
                self.send_response(302)
                self.send_header("Location", source["url"])
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send_json({"error": "source_not_found"}, status=404)
            return
        if route == "/api/sources":
            include_removed = parse_qs(urlsplit(self.path).query).get("include_removed") == ["1"]
            manifest = dashboardctl.source_manifest(include_removed)
            self._send_json({"revision": manifest["revision"], "sources": [public_source(item) for item in manifest["sources"]]})
            return
        source_file = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]{1,100})/file", route)
        if source_file:
            include_removed = parse_qs(urlsplit(self.path).query).get("include_removed") == ["1"]
            source = next((item for item in dashboardctl._source_store_list(dashboardctl.load_store()) if item.get("id") == source_file.group(1) and (item.get("active") or include_removed)), None)
            managed_id = source.get("managed_file_id", "") if source else ""
            if not source or source.get("type") != "file" or not re.fullmatch(r"[0-9a-fA-F-]{36}\.(pdf|docx|xlsx|pptx|csv|txt|md|markdown|png|jpg|jpeg)", managed_id):
                self._send_json({"error": "source_not_found"}, status=404)
                return
            path = dashboardctl.UPLOADS / managed_id
            if not path.is_file() or path.resolve().parent != dashboardctl.UPLOADS.resolve():
                self._send_json({"error": "source_file_unavailable"}, status=404)
                return
            body = path.read_bytes()
            filename = source.get("filename", "source")
            safe_ascii = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120] or "source"
            self.send_response(200)
            self.send_header("Content-Type", source.get("mime_type", "application/octet-stream"))
            self.send_header("Content-Disposition", f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{quote(filename, safe='')}")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if route == "/api/dashboard":
            self._send_json(build_payload())
            return
        if route == "/api/scopes":
            try:
                self._send_json(load_scope_catalog())
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=503)
            return
        context = re.fullmatch(r"/api/items/([A-Za-z0-9_.:-]{1,120})/context", route)
        if context:
            def read_consistent_snapshot():
                db = dashboardctl.load_store()
                record = next((item for item in db.get("records", []) if item.get("id") == context.group(1)), None)
                if record is None: return db, None, None, None
                if len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > MAX_CONTEXT_RESPONSE_BYTES:
                    return db, record, None, "context_too_large"
                try:
                    markdown = dashboardctl.backend.read_context(dashboardctl.ROOT, record["type"], record["id"], MAX_CONTEXT_RESPONSE_BYTES)
                    source = "stored"
                except ValueError as exc:
                    if str(exc) == "context_too_large": return db, record, None, "context_too_large"
                    if str(exc) != "record_not_found": raise
                    markdown = dashboardctl.backend.render_context(record)
                    source = "legacy_render"
                return db, record, markdown, source
            db, record, markdown, context_source = dashboardctl.with_lock(read_consistent_snapshot)
            if record is None:
                self._send_json({"error": "record_not_found"}, status=404); return
            record["effective_status"], record["effective_status_basis"] = effective_status(record)
            if context_source == "context_too_large" or len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > MAX_CONTEXT_RESPONSE_BYTES:
                self._send_json({"error": "context_too_large"}, status=413); return
            response = {"revision": int(record.get("revision", 0)),
                        "store_revision": int(db.get("revision", 0)),
                        "record": record, "context_markdown": markdown,
                        "context_source": context_source, "handoff": handoff_view(record)}
            if len(json.dumps(response, ensure_ascii=False).encode("utf-8")) > MAX_CONTEXT_RESPONSE_BYTES:
                self._send_json({"error": "context_too_large"}, status=413); return
            self._send_json(response)
            return
        if route == "/api/inspection-schedule":
            self._send_json(inspection_schedule())
            return
        if route == "/api/bug-inspection/status":
            self._send_json(bug_inspection_status())
            return
        if route == "/api/followups/pending":
            db = dashboardctl.load_store()
            self._send_json({"revision": db.get("revision", 0), "followups": dashboardctl.pending_followups(db)})
            return
        if route == "/health":
            self._send_json({"ok": True, "generated_at": now_local()})
            return
        if route in ("/", "/index.html"):
            self._send_file(WEB / "index.html")
            return
        if route.startswith("/assets/"):
            relative = route.removeprefix("/assets/")
            candidate = (WEB / "assets" / relative).resolve()
            if WEB.resolve() not in candidate.parents:
                self.send_error(404)
                return
            self._send_file(candidate)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._validate_write_request()
            route = urlsplit(self.path).path
            if route == "/api/data-location/preview":
                payload = self._read_json(8192); self._only_keys(payload, {"target_path", "expected_revision"})
                try: result = data_location.preview(payload.get("target_path"), payload.get("expected_revision"))
                except ValueError as exc: raise ApiError(409 if str(exc) == "revision_conflict" else 400, str(exc))
                self._send_json(result); return
            if route == "/api/data-location/migrate":
                payload = self._read_json(8192); self._only_keys(payload, {"target_path", "preview_id", "confirmation_token", "expected_revision"})
                try:
                    result = data_location.migrate(payload.get("target_path"), payload.get("preview_id"), payload.get("confirmation_token"), payload.get("expected_revision"))
                except ValueError as exc: raise ApiError(409 if str(exc) in {"revision_conflict", "preview_expired", "target_not_empty", "source_changed"} else 400, str(exc))
                if result.get("status") == "verified": refresh_data_paths()
                self._send_json(result); return
            integration_check = re.fullmatch(r"/api/integrations/([a-z_]+)/check", route)
            if integration_check:
                payload = self._read_json(8192); self._only_keys(payload, set())
                try: result = integrations.check(integration_check.group(1))
                except ValueError as exc: raise ApiError(400, str(exc))
                self._send_json(result); return
            integration_report = re.fullmatch(r"/api/integrations/([a-z_]+)/report", route)
            if integration_report:
                payload = self._read_json(8192)
                try: result = integrations.report_agent_check(integration_report.group(1), payload)
                except ValueError as exc: raise ApiError(400, str(exc))
                self._send_json(result); return
            if route == "/api/preferences":
                payload = self._read_json(8192)
                try: self._send_json(dashboardctl.update_preferences(payload))
                except ValueError as exc: raise ApiError(400, str(exc))
                return
            max_bytes = MAX_SOURCE_REQUEST_BYTES if route == "/api/sources/file" else MAX_RECORD_IMAGE_REQUEST_BYTES if re.fullmatch(r"/api/items/[A-Za-z0-9_.:-]{1,120}/attachments", route) else MAX_AGENT_REPORT_REQUEST_BYTES if route in {"/api/agents/report", "/api/agents/update"} or re.fullmatch(r"/api/sources/[A-Za-z0-9_-]{1,100}/read-state", route) else 8192
            payload = self._read_json(max_bytes)
            attachment = re.fullmatch(r"/api/items/([A-Za-z0-9_.:-]{1,120})/attachments", route)
            if attachment:
                self._only_keys(payload, {"filename", "content_base64"})
                filename, encoded = payload.get("filename"), payload.get("content_base64")
                if not isinstance(filename, str) or not isinstance(encoded, str): raise ApiError(400, "invalid_record_attachment")
                try: raw = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError): raise ApiError(400, "invalid_base64")
                if len(raw) > MAX_RECORD_IMAGE_BYTES: raise ApiError(413, "record_attachment_too_large")
                result = dashboardctl.add_record_image(attachment.group(1), filename, raw)
                result["attachment"]["download_url"] = f"/api/items/{attachment.group(1)}/attachments/{filename}"
                self._send_json(result, status=201); return
            agent_route = re.fullmatch(r"/api/agents/(claim|heartbeat|report|release|update)", route)
            if agent_route:
                action = agent_route.group(1)
                common = {"record_id", "session_id", "expected_revision"}
                extra = {
                    "heartbeat": {"activity_kind"},
                    "report": {"message", "context_markdown", "status", "evidence", "phase"},
                    "claim": {"agent_name"}, "release": set(),
                    "update": {"agent_name", "message", "context_markdown", "status", "evidence", "phase"},
                }[action]
                self._only_keys(payload, common | extra)
                rid, sid, rev = payload.get("record_id"), payload.get("session_id"), payload.get("expected_revision")
                if not isinstance(rid, str) or (sid is not None and not isinstance(sid, str)) or not isinstance(rev, int) or isinstance(rev, bool):
                    raise ApiError(400, "invalid_agent_payload")
                if action == "claim": result = dashboardctl.agent_claim(rid, rev, sid, payload.get("agent_name"), explicit_caller=True)
                elif action == "heartbeat": result = dashboardctl.agent_heartbeat(rid, rev, sid, payload.get("activity_kind", "agent"), explicit_caller=True)
                elif action == "release": result = dashboardctl.agent_release(rid, rev, sid, explicit_caller=True)
                elif action == "update":
                    brief = payload.get("message")
                    detail = payload.get("context_markdown")
                    status = payload.get("status"); evidence = payload.get("evidence", []); phase = payload.get("phase")
                    if (brief is not None and not isinstance(brief, str)) or (detail is not None and not isinstance(detail, str)) or (brief is None and detail is None) or (status is not None and not isinstance(status, str)) or not isinstance(evidence, list) or (phase is not None and not isinstance(phase, str)):
                        raise ApiError(400, "invalid_agent_payload")
                    if brief is None: brief = dashboardctl._brief_from_context(detail or "")
                    result = dashboardctl.agent_update(rid, rev, brief, sid, payload.get("agent_name"), status, evidence, phase, detail, explicit_caller=True)
                else:
                    brief = payload.get("message")
                    detail = payload.get("context_markdown")
                    status = payload.get("status"); evidence = payload.get("evidence", []); phase = payload.get("phase")
                    if (brief is not None and not isinstance(brief, str)) or (detail is not None and not isinstance(detail, str)) or (brief is None and detail is None) or (status is not None and not isinstance(status, str)) or not isinstance(evidence, list) or (phase is not None and not isinstance(phase, str)): raise ApiError(400, "invalid_agent_payload")
                    if brief is None: brief = dashboardctl._brief_from_context(detail or "")
                    result = dashboardctl.agent_report(rid, rev, brief, sid, status, evidence, phase, detail, explicit_caller=True)
                self._send_json(result); return
            if route == "/api/preferences/work-hours":
                self._only_keys(payload, {"start", "end"})
                self._send_json(dashboardctl.set_work_hours(payload.get("start"), payload.get("end")))
                return
            if route == "/api/bug-inspection/run":
                self._only_keys(payload, set())
                if not isinstance(INSPECTION_DISPATCHER, Path) or not INSPECTION_DISPATCHER.is_file():
                    self._send_json({"error": "inspection_dispatch_unavailable"}, status=503)
                    return
                try:
                    completed = subprocess.run(
                        [sys.executable, str(INSPECTION_DISPATCHER), "request-manual"],
                        check=False, capture_output=True, text=True, timeout=8,
                        cwd=str(INSPECTION_DISPATCHER.parent),
                    )
                    if len(completed.stdout) > 65536:
                        self._send_json({"error": "inspection_dispatch_failed"}, status=503)
                        return
                    dispatched = json.loads(completed.stdout)
                    state = dispatched.get("state")
                    if state == "queued":
                        self._send_json({
                            "event_id": dispatched.get("event_id"),
                            "slot_id": dispatched.get("slot_id"),
                            "status": "queued",
                            "poll_url": "/api/bug-inspection/status",
                        }, status=202)
                    elif state in {"join", "pending_dispatch"}:
                        active = dispatched.get("active_run", {})
                        self._send_json({
                            "event_id": active.get("event_id"),
                            "slot_id": dispatched.get("slot_id"),
                            "status": "queued" if active.get("status") == "reserved" else active.get("status", "running"),
                            "poll_url": "/api/bug-inspection/status",
                            "joined": True,
                        }, status=202)
                    elif state == "skipped_success":
                        self._send_json({"event_id": dispatched.get("event_id"), "slot_id": dispatched.get("slot_id"), "status": "succeeded", "already_succeeded": True}, status=200)
                    elif state == "failed":
                        self._send_json({"error": dispatched.get("reason", "inspection_dispatch_failed"), "event_id": dispatched.get("event_id"), "slot_id": dispatched.get("slot_id"), "status": "failed"}, status=503)
                    else:
                        self._send_json({"error": "inspection_dispatch_invalid_state"}, status=503)
                except (OSError, subprocess.SubprocessError, ValueError, TypeError):
                    self._send_json({"error": "inspection_dispatch_failed"}, status=503)
                return
            if route == "/api/sources/link":
                self._only_keys(payload, {"url", "name", "category", "purpose", "primary"})
                url, name, category = payload.get("url"), payload.get("name"), payload.get("category", "需求资料")
                purpose, primary = payload.get("purpose"), payload.get("primary")
                if not isinstance(url, str) or (name is not None and not isinstance(name, str)) or not isinstance(category, str) or (purpose is not None and not isinstance(purpose, str)) or (primary is not None and not isinstance(primary, bool)):
                    raise ApiError(400, "invalid_source_payload")
                result = dashboardctl.add_source_link(url, name, category, "dashboard", purpose=purpose, primary=primary)
                self._send_json({"revision": result["revision"], "source": public_source(result["source"])}, status=201)
                return
            override = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]{1,100})/override", route)
            if override:
                self._only_keys(payload, {"fields"})
                fields = payload.get("fields")
                if not isinstance(fields, dict): raise ApiError(400, "invalid_source_override")
                result = dashboardctl.override_source(override.group(1), fields)
                self._send_json({"revision": result["revision"], "source": public_source(result["source"])})
                return
            lifecycle = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]{1,100})/lifecycle", route)
            if lifecycle:
                self._only_keys(payload, {"lifecycle_status", "lifecycle_reason", "last_verified_at", "lifecycle_override"})
                result = dashboardctl.set_source_lifecycle(lifecycle.group(1), payload)
                self._send_json({"revision": result["revision"], "source": public_source(result["source"])})
                return
            read_state = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]{1,100})/read-state", route)
            if read_state:
                result = dashboardctl.set_source_read_state(read_state.group(1), payload)
                self._send_json({"revision": result["revision"], "source": public_source(result["source"])})
                return
            if route == "/api/sources/file":
                self._only_keys(payload, {"filename", "content_base64", "category"})
                filename = payload.get("filename")
                encoded = payload.get("content_base64")
                category = payload.get("category", "需求资料")
                if not isinstance(filename, str) or not isinstance(encoded, str) or not isinstance(category, str):
                    raise ApiError(400, "invalid_source_payload")
                try:
                    file_bytes = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError):
                    raise ApiError(400, "invalid_base64")
                result = dashboardctl.add_source_file(filename, file_bytes, category, "dashboard")
                self._send_json({"revision": result["revision"], "source": public_source(result["source"])}, status=201)
                return
            restore = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]{1,100})/restore", route)
            if restore:
                self._only_keys(payload, set())
                result = dashboardctl.change_source_state(restore.group(1), True)
                self._send_json({"revision": result["revision"], "source": public_source(result["source"])})
                return
            if route == "/api/followups/claim":
                self._only_keys(payload, {"ids", "limit", "claimed_by"})
                ids = payload.get("ids")
                if ids is not None and (not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(value, str) or len(value) > 100 for value in ids)):
                    raise ApiError(400, "invalid_ids")
                limit = payload.get("limit", 20)
                claimed_by = payload.get("claimed_by", "agent")
                if not isinstance(limit, int) or isinstance(limit, bool) or not isinstance(claimed_by, str):
                    raise ApiError(400, "invalid_claim_parameters")
                self._send_json(dashboardctl.claim_followups(ids, limit, claimed_by))
                return
            match = re.fullmatch(r"/api/items/([A-Za-z0-9_.:-]{1,120})/(complete|followups|transfer)", route)
            if match:
                record_id, action = match.groups()
                if action == "complete":
                    self._only_keys(payload, {"completed"})
                    if not isinstance(payload.get("completed"), bool):
                        raise ApiError(400, "invalid_completed")
                    self._send_json(dashboardctl.set_manual_completion(record_id, payload["completed"]))
                    return
                if action == "transfer":
                    self._only_keys(payload, set())
                    self._send_json(dashboardctl.confirm_bug_transfer(record_id))
                    return
                self._only_keys(payload, {"prompt"})
                if not isinstance(payload.get("prompt"), str):
                    raise ApiError(400, "invalid_prompt")
                self._send_json(dashboardctl.add_followup(record_id, payload["prompt"]), status=201)
                return
            match = re.fullmatch(r"/api/followups/([0-9a-fA-F-]{36})/finish", route)
            if match:
                self._only_keys(payload, {"result", "outcome"})
                result = payload.get("result")
                outcome = payload.get("outcome")
                if not isinstance(result, str) or not isinstance(outcome, str):
                    raise ApiError(400, "invalid_finish_payload")
                self._send_json(dashboardctl.finish_followup(match.group(1), result, outcome))
                return
            raise ApiError(404, "not_found")
        except ApiError as exc:
            self._send_json({"error": exc.code}, status=exc.status)
        except ValueError as exc:
            code = dashboardctl.error_code(exc)
            status = 404 if code in {"record_not_found", "record_attachment_not_found", "followup_not_found", "source_not_found"} else 413 if code == "record_attachment_too_large" else 409 if code in {"followup_not_claimed", "source_duplicate", "attachment_duplicate", "revision_conflict", "agent_owner_conflict", "agent_expired", "record_completed"} else 400
            self._send_json({"error": code}, status=status)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"error": "invalid_json"}, status=400)
        except OSError:
            self._send_json({"error": "storage_error"}, status=500)

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            self._validate_write_request()
            payload = self._read_json(8192)
            self._only_keys(payload, set())
            match = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]{1,100})", urlsplit(self.path).path)
            if not match:
                raise ApiError(404, "not_found")
            result = dashboardctl.change_source_state(match.group(1), False)
            self._send_json({"revision": result["revision"], "source": public_source(result["source"])})
        except ApiError as exc:
            self._send_json({"error": exc.code}, status=exc.status)
        except ValueError as exc:
            code = dashboardctl.error_code(exc)
            self._send_json({"error": code}, status=404 if code == "source_not_found" else 400)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"error": "invalid_json"}, status=400)
        except OSError:
            self._send_json({"error": "storage_error"}, status=500)

    def _validate_write_request(self) -> None:
        if self.headers.get("Host") != f"{HOST}:{PORT}":
            raise ApiError(403, "invalid_host")
        if self.headers.get("Origin") != f"http://{HOST}:{PORT}":
            raise ApiError(403, "invalid_origin")
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "content_type_required")

    def _read_json(self, max_bytes: int) -> dict:
        raw_length = self.headers.get("Content-Length")
        if not raw_length or not raw_length.isdigit():
            raise ApiError(411, "content_length_required")
        length = int(raw_length)
        if length > max_bytes:
            raise ApiError(413, "payload_too_large")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ApiError(400, "json_object_required")
        return payload

    @staticmethod
    def _only_keys(payload: dict, allowed: set[str]) -> None:
        if set(payload) - allowed:
            raise ApiError(400, "unsupported_fields")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Keep the persistent service quiet; errors still go through stderr.
        if args and str(args[1:2]).startswith("('5"):
            super().log_message(fmt, *args)


def _recover_startup_data() -> dict:
    # mCloud patrol is an optional integration. A clean install without its
    # migration journal must start the generic workbench without importing it.
    journal = waylume_paths.config_dir() / "patrol-data-migration.json"
    if not journal.exists() and not journal.is_symlink():
        return {"recovered": False, "reason": "optional_integration_not_configured"}
    try:
        import patrol_compat
    except ImportError as exc:
        # A leftover transaction cannot be ignored safely, even though the
        # integration itself is optional in steady state.
        raise ValueError("optional_integration_recovery_unavailable") from exc
    return patrol_compat.recover()


def main() -> None:
    global _instance_lock
    # Keep singleton ownership stable when the user changes the data directory.
    lock_path = waylume_paths.service_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _instance_lock = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(_instance_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Dashboard is already running; leaving the existing instance in place.", flush=True)
        return
    # Resolve an interrupted patrol alias transaction before accepting HTTP
    # requests. A corrupt journal, active inspection slot or ambiguous path is
    # fail-closed: leave the existing files untouched and do not serve a split
    # data view.
    try:
        patrol_recovery = _recover_startup_data()
    except Exception:
        _instance_lock.close()
        _instance_lock = None
        raise
    if patrol_recovery.get("recovered"):
        print("Patrol data recovery:", patrol_recovery.get("action", "completed"), flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    def sweep_loop():
        while True:
            time.sleep(30)
            try: dashboardctl.agent_sweep()
            except (OSError, ValueError): pass
    threading.Thread(target=sweep_loop, name="dashboard-agent-sweep", daemon=True).start()
    print(f"Dashboard listening at http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
