# Created by Codex on 2026/10/21.
# Copyright © 2026 CMIC. All rights reserved.

import contextlib
import base64
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import dashboardctl
import server as dashboard_server

def mark_complete_owned_snapshot(snapshot):
    bugs = snapshot["bugs"]
    snapshot["last_attempt"] = {"result": "succeeded", "stale": False, "captured_at": snapshot["last_successful_snapshot_at"], "pages_read": 1,
        "reported_total": len(bugs), "unique_ids": len({item["id"] for item in bugs}), "filter": "我负责的",
        "verification": f"matched self owner current-user; complete=true; count={len(bugs)}; uniqueIds={len(bugs)}; all pages returned"}
    return snapshot


class FollowupStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [
            patch.object(dashboardctl, "ROOT", root),
            patch.object(dashboardctl, "STORE", root / "store.json"),
            patch.object(dashboardctl, "LOCK", root / ".store.lock"),
            patch.object(dashboardctl, "EVENTS", root / "events.jsonl"),
            patch.object(dashboardctl, "UPLOADS", root / "sources" / "uploads"),
            patch.object(dashboard_server, "STORE", root / "store.json"),
            patch.object(dashboard_server, "SCOPE_CATALOG", root / "scope-catalog.json"),
        ]
        for item in self.patches:
            item.start()
        (root / "scope-catalog.json").write_text(json.dumps({"version": 1, "revision": 0, "updated_at": None, "scopes": [
            {"id": "SCOPE-BGM-CONTAINER", "name": "Background music container", "status": "confirmed", "in_scope": [], "out_of_scope": [], "evidence": [], "related_ids": [], "version": 1}
        ]}), encoding="utf-8")
        dashboardctl.atomic_write({"schema_version": 1, "revision": 0, "records": [{
            "id": "BUG-1", "type": "bug", "title": "test", "status": "awaiting_acceptance",
            "platform_status": "测试", "source_updated_at": "2026-10-20", "history": [],
        }], "sync": {}})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def read_db(self):
        return dashboardctl.load_store()

    def test_requirement_parser_keeps_source_status_and_uses_generic_owner(self):
        requirements = Path(self.temp.name) / "requirements.md"
        requirements.write_text(
            "## 需求清单\n"
            "| ID | 标题 | 责任范围 | 状态 | 备注 |\n"
            "|---|---|---|---|---|\n"
            "| REQ-FIXTURE | Sample work | 当前用户负责 | 🟡 核心已验收 | synthetic note |\n"
            "| REQ-SHARED | Shared work | 当前用户与团队共同负责 | 🟢 已完成 | synthetic note |\n",
            encoding="utf-8",
        )
        records = dashboardctl.parse_requirements(requirements)
        self.assertEqual([item["status"] for item in records], ["awaiting_acceptance", "completed"])
        self.assertEqual([item["owner"] for item in records], ["当前用户", "当前用户（共同负责）"])
        self.assertEqual(records[0]["id"], "REQ-FIXTURE")
        self.assertEqual(records[1]["id"], "REQ-SHARED")

    def test_manual_completion_and_partial_followup_updates_survive(self):
        dashboardctl.set_manual_completion("BUG-1", True)
        first = dashboardctl.add_followup("BUG-1", "  请核查修复后的回归  ")
        second = dashboardctl.add_followup("BUG-1", "再核对一次")
        self.assertNotEqual(first["followup"]["id"], second["followup"]["id"])
        row = self.read_db()["records"][0]
        self.assertEqual(row["manual_status"], "completed")
        self.assertEqual(row["status"], "awaiting_acceptance")
        self.assertEqual([item["prompt"] for item in row["followups"]], ["请核查修复后的回归", "再核对一次"])
        self.assertEqual(len(dashboardctl.pending_followups(self.read_db())), 2)

    def test_payload_separates_manual_effective_and_source_status(self):
        db = self.read_db()
        db["records"][0]["our_status"] = "我方完成/已提交测试；不代表QA通过"
        dashboardctl.atomic_write(db)
        payload = dashboard_server.build_payload()
        item = payload["bugs"][0]
        self.assertEqual(item["source_status"], "awaiting_acceptance")
        self.assertEqual(item["effective_status"], "completed")
        self.assertEqual(item["effective_status_basis"], "our_status")

    def test_claim_is_atomic_and_failure_finish_is_handled(self):
        followup = dashboardctl.add_followup("BUG-1", "执行回归")["followup"]
        outcomes = []
        barrier = threading.Barrier(3)
        def worker(name):
            barrier.wait()
            outcomes.append(dashboardctl.claim_followups([followup["id"]], claimed_by=name)["claimed"])
        threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(len(items) for items in outcomes), 1)
        done = dashboardctl.finish_followup(followup["id"], "设备不可用，回归未完成", "failure")
        self.assertEqual(done["followup"]["status"], "handled")
        self.assertEqual(done["followup"]["outcome"], "failure")
        self.assertIn("设备不可用", done["followup"]["result"])

    def test_same_record_followups_are_claimed_in_sequence(self):
        first = dashboardctl.add_followup("BUG-1", "第一步")["followup"]
        second = dashboardctl.add_followup("BUG-1", "第二步")["followup"]
        claimed = dashboardctl.claim_followups()["claimed"]
        self.assertEqual([item["id"] for item in claimed], [first["id"]])
        self.assertEqual(dashboardctl.claim_followups()["claimed"], [])
        dashboardctl.finish_followup(first["id"], "第一步已尝试", "success")
        next_claim = dashboardctl.claim_followups()["claimed"]
        self.assertEqual([item["id"] for item in next_claim], [second["id"]])

    def test_source_sync_preserves_manual_completion_and_reports_change(self):
        db = self.read_db()
        db["sources"] = [{"id": "SRC-PRESERVE", "type": "link", "url": "https://docs.example/?tab=x", "active": True, "purpose": "seeded reference"}]
        dashboardctl.atomic_write(db)
        dashboardctl.set_manual_completion("BUG-1", True)
        followup = dashboardctl.add_followup("BUG-1", "复核来源变化")["followup"]
        db = self.read_db()
        db["records"][0].update({"current_agent_session_id": "thread-sync-owner", "last_agent_session_id": "thread-previous-owner", "last_agent_activity_at": "2026-10-21T09:00:00+08:00", "agent_state": "active"})
        dashboardctl.atomic_write(db)
        state_path = Path(self.temp.name) / "bug-state.json"
        snapshot = {
            "last_successful_snapshot_at": "2026-10-21T10:00:00+08:00", "stale": False,
            "bugs": [{"id": "BUG-1", "title": "test", "status": "处理中", "source_updated_at": "2026-10-21", "observed_at": "2026-10-21T10:00:00+08:00"}],
        }
        state_path.write_text(json.dumps(mark_complete_owned_snapshot(snapshot)), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            dashboardctl.cmd_sync_bugs(type("Args", (), {"state": str(state_path)})())
        row = self.read_db()["records"][0]
        self.assertEqual(row["manual_status"], "completed")
        self.assertEqual(row["followups"][0]["id"], followup["id"])
        self.assertEqual(row["platform_status"], "处理中")
        self.assertEqual(row["current_agent_session_id"], "thread-sync-owner")
        self.assertEqual(row["last_agent_activity_at"], "2026-10-21T09:00:00+08:00")
        self.assertIn("返修周期", row["manual_status_conflict"]["reason"])
        self.assertEqual(self.read_db()["sources"][0]["id"], "SRC-PRESERVE")

    def test_two_day_completed_bug_sync_preserves_known_date_and_keeps_unknown_null(self):
        db = self.read_db()
        db["records"].extend([
            {"id": "BUG-OLD-DATE", "type": "bug", "title": "known prior completion", "status": "completed",
             "completed_at": "2026-07-15T09:00:00+08:00", "history": [{"status": "completed", "at": "2026-07-15T09:00:00+08:00"}]},
            {"id": "BUG-UNKNOWN-DATE", "type": "bug", "title": "no completion evidence", "status": "completed", "history": []},
        ])
        dashboardctl.atomic_write(db)
        state_path = Path(self.temp.name) / "bug-state.json"
        for snapshot in ("2026-10-21T10:00:00+08:00", "2026-10-22T10:00:00+08:00"):
            state_path.write_text(json.dumps(mark_complete_owned_snapshot({"last_successful_snapshot_at": snapshot, "stale": False,
                "bugs": [{"id": "BUG-OLD-DATE", "title": "known prior completion", "status": "关闭"},
                         {"id": "BUG-UNKNOWN-DATE", "title": "no completion evidence", "status": "关闭"}]})), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                dashboardctl.cmd_sync_bugs(type("Args", (), {"state": str(state_path)})())
        rows = {item["id"]: item for item in dashboard_server.build_payload()["bugs"]}
        self.assertEqual(rows["BUG-OLD-DATE"]["completed_at"], "2026-07-15T09:00:00+08:00")
        self.assertIsNone(rows["BUG-UNKNOWN-DATE"]["completed_at"])
        unknown = next(row for row in self.read_db()["records"] if row["id"] == "BUG-UNKNOWN-DATE")
        self.assertTrue(any("成功快照" in event.get("note", "") for event in unknown["history"]))

    def test_bug_sync_keeps_platform_status_and_dedupes_associated_history(self):
        initial = {
            "last_successful_snapshot_at": "2026-10-21T14:30:00+08:00",
            "stale": False,
            "bugs": [
                {"id": "BUG-TESTED-OLD", "title": "old tested", "status": "测试",
                 "triage": "我方完成/已提交测试；不代表QA通过、平台验收或关闭"},
                {"id": "BUG-CLOSED-OLD", "title": "old closed", "status": "关闭",
                 "triage": "既有处理记录为我方已提交测试；平台当前显示关闭，关闭原因未读取"},
                {"id": "BUG-HIST-ASSOCIATED", "title": "historical association", "status": "分析",
                 "triage": "历史关联待核：与既有播放地址问题相关；原始失败响应缺失，未判已修；保留历史关联，不重复排查。",
                 "related_ids": ["BUG-PLAYBACK-PRIOR"]},
                {"id": "BUG-NEW-UI", "title": "new UI bug", "status": "分析",
                 "triage": "🔴等待分析（真机排队）"},
            ],
        }
        mark_complete_owned_snapshot(initial)
        state_path = Path(self.temp.name) / "bug-state.json"
        state_path.write_text(json.dumps(initial, ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            dashboardctl.cmd_sync_bugs(type("Args", (), {"state": str(state_path)})())

        rows = {row["id"]: row for row in self.read_db()["records"]}
        self.assertEqual(rows["BUG-TESTED-OLD"]["status"], "completed")
        self.assertEqual(rows["BUG-TESTED-OLD"]["platform_status"], "测试")
        self.assertEqual(rows["BUG-CLOSED-OLD"]["status"], "completed")
        self.assertEqual(rows["BUG-CLOSED-OLD"]["platform_status"], "关闭")
        self.assertEqual(rows["BUG-HIST-ASSOCIATED"]["status"], "archived")
        self.assertEqual(rows["BUG-HIST-ASSOCIATED"]["platform_status"], "分析")
        self.assertEqual(rows["BUG-HIST-ASSOCIATED"]["related_ids"], ["BUG-PLAYBACK-PRIOR"])
        self.assertIn("未判已修", rows["BUG-HIST-ASSOCIATED"]["status_detail"])
        self.assertEqual(rows["BUG-NEW-UI"]["status"], "in_progress")
        self.assertEqual(rows["BUG-NEW-UI"]["platform_status"], "分析")
        self.assertEqual(rows["BUG-NEW-UI"]["triage"], "🔴等待分析（真机排队）")

        # A later source refresh with the upstream red marker still present must
        # not re-create this user-confirmed historical association as a new BUG.
        second = dict(initial)
        second["last_successful_snapshot_at"] = "2026-10-21T15:30:00+08:00"
        second["bugs"] = [dict(item) for item in initial["bugs"]]
        second["bugs"][2]["triage"] = "🔴等待分析（真机排队）"
        second["bugs"][2].pop("related_ids", None)
        mark_complete_owned_snapshot(second)
        state_path.write_text(json.dumps(second, ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            dashboardctl.cmd_sync_bugs(type("Args", (), {"state": str(state_path)})())
        rows = {row["id"]: row for row in self.read_db()["records"]}
        self.assertEqual(rows["BUG-HIST-ASSOCIATED"]["status"], "archived")
        self.assertEqual(rows["BUG-HIST-ASSOCIATED"]["related_ids"], ["BUG-PLAYBACK-PRIOR"])
        self.assertEqual(rows["BUG-NEW-UI"]["status"], "in_progress")

    def test_same_origin_api_writes_and_cross_origin_is_rejected(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
        old_port = dashboard_server.PORT
        dashboard_server.PORT = httpd.server_port
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{httpd.server_port}"
            request = Request(origin + "/api/items/BUG-1/followups", data=json.dumps({"prompt": "实际验证"}).encode(), method="POST", headers={"Content-Type": "application/json", "Origin": origin})
            with urlopen(request) as response:
                payload = json.load(response)
            self.assertEqual(payload["followup"]["status"], "pending")
            with urlopen(origin + "/api/items/BUG-1/context") as response:
                context = json.load(response)
                self.assertEqual(context["revision"], context["record"]["revision"])
                self.assertEqual(context["store_revision"], dashboardctl.load_store()["revision"])
            self.assertIn("context_markdown", context)
            session = "thread-test-123"
            # The service may inherit the parent agent's thread ID; HTTP caller identity
            # comes from its same-origin, format-validated payload and remains distinct.
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-root-service"}):
                rev = context["record"].get("revision", 0)
                body = {"record_id": "BUG-1", "session_id": session, "expected_revision": rev}
                claim_body = {**body, "agent_name": "dashboard_test_worker"}
                request = Request(origin + "/api/agents/claim", data=json.dumps(claim_body).encode(), method="POST", headers={"Content-Type": "application/json", "Origin": origin})
                with urlopen(request) as response: claimed = json.load(response)
                self.assertEqual(claimed["record"]["current_agent_session_id"], session)
                self.assertEqual(claimed["record"]["current_agent_name"], "dashboard_test_worker")
                next_body = {**body, "expected_revision": claimed["record"]["revision"], "message": "验证报告已提交", "context_markdown": "## 执行报告\n追加历史", "phase": "验证"}
                request = Request(origin + "/api/agents/report", data=json.dumps(next_body).encode(), method="POST", headers={"Content-Type": "application/json", "Origin": origin})
                with urlopen(request) as response: reported = json.load(response)
                self.assertEqual(reported["progress"]["message"], next_body["message"])
                self.assertEqual(reported["progress"]["context_markdown"], next_body["context_markdown"])
                detail_text = "长进展记录" * 900
                update_body = {**body, "expected_revision": reported["revision"], "agent_name": "dashboard_test_worker", "message": "原子完成", "context_markdown": detail_text, "status": "completed"}
                request = Request(origin + "/api/agents/update", data=json.dumps(update_body).encode(), method="POST", headers={"Content-Type": "application/json", "Origin": origin})
                with urlopen(request) as response: completed = json.load(response)
                self.assertEqual(completed["revision"], completed["record"]["revision"])
                self.assertEqual(completed["store_revision"], dashboardctl.load_store()["revision"])
                self.assertEqual(completed["record"]["effective_status"] if "effective_status" in completed["record"] else completed["record"]["status"], "completed")
                self.assertIsNone(completed["record"]["current_agent_session_id"])
                self.assertEqual(completed["record"]["progress"][-1]["context_markdown"], detail_text)
                invalid_identity = {**body, "expected_revision": completed["revision"], "session_id": "invalid", "message": "must reject"}
                request = Request(origin + "/api/agents/update", data=json.dumps(invalid_identity).encode(), method="POST", headers={"Content-Type": "application/json", "Origin": origin})
                with self.assertRaises(HTTPError) as invalid_response: urlopen(request)
                self.assertEqual(invalid_response.exception.code, 400)
                preference_request = Request(origin + "/api/preferences/work-hours", data=json.dumps({"start": "09:00", "end": "18:00"}).encode(), method="POST", headers={"Content-Type": "application/json", "Origin": origin})
                with urlopen(preference_request) as response: preference = json.load(response)
                self.assertEqual(preference["work_hours"], {"start": "09:00", "end": "18:00", "timezone": "Asia/Shanghai"})
                with urlopen(origin + "/api/dashboard") as response: dashboard_payload = json.load(response)
                self.assertEqual(dashboard_payload["preferences"]["work_hours"]["start"], "09:00")
                self.assertEqual(dashboard_payload["revision"], dashboard_payload["store_revision"])
                self.assertEqual(dashboard_payload["store_revision"], dashboardctl.load_store()["revision"])
                self.assertIsInstance(dashboard_payload["scope_catalog"]["revision"], int)
                with urlopen(origin + "/api/scopes") as response: scope_payload = json.load(response)
                self.assertEqual(scope_payload["revision"], dashboard_payload["scope_catalog"]["revision"])
                self.assertIn("SCOPE-BGM-CONTAINER", {scope["id"] for scope in scope_payload["scopes"]})
                stale = Request(origin + "/api/agents/heartbeat", data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json", "Origin": origin})
                with self.assertRaises(HTTPError) as stale_response: urlopen(stale)
                self.assertEqual(stale_response.exception.code, 409)
            request = Request(origin + "/api/items/BUG-1/complete", data=b'{"completed":true}', method="POST", headers={"Content-Type": "application/json", "Origin": "https://attacker.example"})
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request)
            self.assertEqual(rejected.exception.code, 403)
            self.assertEqual(self.read_db()["records"][0].get("manual_status"), None)
        finally:
            httpd.shutdown()
            thread.join()
            httpd.server_close()

    def test_local_transfer_confirmation_api_only_changes_dashboard_record(self):
        db = self.read_db()
        db["records"][0].update({"transfer_disposition": "transfer_pending", "transfer_target": "teammate", "status_detail": "请转交 teammate"})
        dashboardctl.atomic_write(db)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
        old_port = dashboard_server.PORT
        dashboard_server.PORT = httpd.server_port
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        try:
            origin = f"http://127.0.0.1:{httpd.server_port}"
            request = Request(origin + "/api/items/BUG-1/transfer", data=b"{}", method="POST", headers={"Content-Type": "application/json", "Origin": origin})
            with urlopen(request) as response: payload = json.load(response)
            record = payload["record"]
            self.assertEqual(record["transfer_disposition"], "transferred")
            self.assertEqual(record["completion_kind"], "transferred")
            self.assertEqual(record["status"], "completed")
            self.assertEqual(record["platform_status"], "测试")
            self.assertIsNone(record.get("assignee"))
            self.assertEqual(dashboardctl.effective_work_status(record)[0], "completed")
        finally:
            httpd.shutdown(); thread.join(); httpd.server_close(); dashboard_server.PORT = old_port
            dashboard_server.PORT = old_port

    def test_bug_image_attachment_is_record_scoped_and_context_report_limits_apply(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
        old_port = dashboard_server.PORT
        dashboard_server.PORT = httpd.server_port
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
        origin = f"http://127.0.0.1:{httpd.server_port}"
        def send(method, path, payload=None):
            body = json.dumps(payload or {}).encode()
            connection.request(method, path, body=body, headers={"Host": f"127.0.0.1:{httpd.server_port}", "Origin": origin, "Content-Type": "application/json"})
            response = connection.getresponse(); raw = response.read()
            return response.status, dict(response.getheaders()), raw
        try:
            png = b"\x89PNG\r\n\x1a\n" + b"test-image-bytes"
            status, _, raw = send("POST", "/api/items/BUG-1/attachments", {"filename": "capture.png", "content_base64": base64.b64encode(png).decode()})
            self.assertEqual(status, 201)
            entry = json.loads(raw)["attachment"]
            self.assertEqual(set(entry) & {"url", "signed_url", "signature", "token"}, set())
            self.assertEqual(entry["download_url"], "/api/items/BUG-1/attachments/capture.png")
            status, headers, body = send("GET", entry["download_url"])
            self.assertEqual((status, body), (200, png))
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            record_dir = Path(self.temp.name) / "records" / "bug" / "BUG-1"
            attachment_dir = record_dir / "attachments"
            image_path = attachment_dir / "capture.png"
            self.assertEqual(record_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(attachment_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(image_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(send("GET", "/api/items/BUG-OTHER/attachments/capture.png")[0], 404)
            self.assertEqual(send("POST", "/api/items/BUG-1/attachments", {"filename": "capture.png", "content_base64": base64.b64encode(png).decode()})[0], 409)
            bad = send("POST", "/api/items/BUG-1/attachments", {"filename": "fake.png", "content_base64": base64.b64encode(b"not-image").decode()})
            self.assertEqual(bad[0], 400)
            self.assertEqual(send("GET", "/api/items/BUG-1/attachments/..%2Fsecret.png")[0], 404)

            with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-http-owner"}):
                context = json.loads(send("GET", "/api/items/BUG-1/context")[2])
                claim_status, _, claim_raw = send("POST", "/api/agents/claim", {"record_id": "BUG-1", "session_id": "thread-http-owner", "expected_revision": context["record"]["revision"], "agent_name": "attachment_test_worker"})
                self.assertEqual(claim_status, 200)
                revision = json.loads(claim_raw)["record"]["revision"]
                too_large = {"record_id": "BUG-1", "session_id": "thread-http-owner", "expected_revision": revision, "context_markdown": "x" * 140_000}
                self.assertEqual(send("POST", "/api/agents/report", too_large)[0], 413)
            db = self.read_db(); db["records"][0]["latest_brief"] = "x" * (dashboard_server.MAX_CONTEXT_RESPONSE_BYTES + 100)
            dashboardctl.atomic_write(db)
            self.assertEqual(send("GET", "/api/items/BUG-1/context")[0], 413)
        finally:
            connection.close(); httpd.shutdown(); thread.join(); httpd.server_close()
            dashboard_server.PORT = old_port

    def test_cross_process_reports_and_context_reads_share_one_locked_snapshot(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
        old_port = dashboard_server.PORT
        dashboard_server.PORT = httpd.server_port
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        script = '''
import os, sys, time
from pathlib import Path
import dashboardctl
root = Path(sys.argv[1])
dashboardctl.ROOT = root
dashboardctl.STORE = root / "store.json"
dashboardctl.LOCK = root / ".store.lock"
dashboardctl.EVENTS = root / "events.jsonl"
os.environ["CODEX_THREAD_ID"] = "thread-process-writer"
record = dashboardctl.load_store()["records"][0]
dashboardctl.agent_claim("BUG-1", record["revision"])
for index in range(24):
    record = dashboardctl.load_store()["records"][0]
    dashboardctl.agent_report("BUG-1", record["revision"], f"brief-{index}", context_markdown=f"detail-{index}")
    time.sleep(0.008)
'''
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        child = subprocess.Popen([sys.executable, "-c", script, str(self.temp.name)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        failures = []
        read_count = 0
        try:
            while child.poll() is None:
                connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
                try:
                    connection.request("GET", "/api/items/BUG-1/context")
                    response = connection.getresponse()
                    raw = response.read()
                    if response.status != 200:
                        failures.append(f"HTTP {response.status}")
                        continue
                    payload = json.loads(raw)
                    read_count += 1
                    progress = payload["record"].get("progress", [])
                    latest = next((item for item in reversed(progress) if item.get("kind") == "agent_report"), None)
                    if latest and (latest["message"] not in payload["context_markdown"] or latest.get("context_markdown") not in payload["context_markdown"]):
                        failures.append("record/Markdown snapshot mismatch")
                finally:
                    connection.close()
            stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, stderr or stdout)
            self.assertGreater(read_count, 0)
            self.assertEqual(failures, [])
        finally:
            if child.poll() is None:
                child.kill(); child.wait()
            httpd.shutdown(); thread.join(); httpd.server_close()
            dashboard_server.PORT = old_port

    def test_source_seed_import_is_idempotent_and_preserves_sheet_query(self):
        seed_path = Path(self.temp.name) / "seed.json"
        seed_path.write_text(json.dumps({"schema_version": 1, "sources": [
            {"id": "SRC-PROGRESS-A", "name": "表A", "category": "分工", "url": "https://docs.example/sheet?tab=A", "purpose": "整文档扫描", "active": True, "primary": True, "verification_state": "not_checked"},
            {"id": "SRC-PROGRESS-B", "name": "表B", "category": "联调", "url": "https://docs.example/sheet?tab=B", "purpose": "另一个工作表", "active": True, "primary": False, "verification_state": "not_checked"},
        ]}), encoding="utf-8")
        first = dashboardctl.import_source_seed(seed_path)
        first_revision = self.read_db()["revision"]
        second = dashboardctl.import_source_seed(seed_path)
        self.assertEqual(len(first["imported"]), 2)
        self.assertEqual(self.read_db()["revision"], first_revision)
        self.assertEqual(second["updated"], [])
        sources = dashboardctl.source_manifest()["sources"]
        self.assertEqual(len(sources), 2)
        self.assertEqual({source["url"].split("tab=")[-1] for source in sources}, {"A", "B"})
        self.assertEqual({source["purpose"] for source in sources}, {"整文档扫描", "另一个工作表"})
        dashboardctl.change_source_state("SRC-PROGRESS-A", False)
        dashboardctl.import_source_seed(seed_path)
        removed = next(item for item in dashboardctl.source_manifest(True)["sources"] if item["id"] == "SRC-PROGRESS-A")
        self.assertFalse(removed["active"])

    def test_source_api_upload_remove_restore_redaction_and_download(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
        old_port = dashboard_server.PORT
        dashboard_server.PORT = httpd.server_port
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        origin = f"http://127.0.0.1:{httpd.server_port}"
        def send(method, path, payload=None):
            body = json.dumps(payload or {}).encode()
            connection.request(method, path, body=body, headers={"Host": f"127.0.0.1:{httpd.server_port}", "Origin": origin, "Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
        try:
            status, _, raw = send("POST", "/api/sources/link", {"url": "https://docs.example/sheet?tab=sheet-a&access_token=secret", "name": "用户提供链接", "purpose": "核对来源"})
            self.assertEqual(status, 201)
            link = json.loads(raw)["source"]
            self.assertIn("%5Bredacted%5D", link["url"])
            self.assertIn("tab=sheet-a", link["url"])
            status, _, raw = send("POST", f"/api/sources/{link['id']}/read-state", {
                "last_read_at": "2026-10-22T10:00:00+08:00", "read_state": "partial",
                "read_scope": "tab:Requirements!A1:G100", "content_updated_at": None,
                "read_gap": ["tab:History missing"], "snapshot_revision": "snapshot-1",
            })
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)["source"]["read_state"], "partial")
            status, _, raw = send("GET", "/api/sources")
            api_source = next(item for item in json.loads(raw)["sources"] if item["id"] == link["id"])
            self.assertEqual(api_source["read_scope"], "tab:Requirements!A1:G100")
            file_bytes = b"%PDF-1.4\nsource"
            status, _, raw = send("POST", "/api/sources/file", {"filename": "需求说明.pdf", "content_base64": base64.b64encode(file_bytes).decode(), "category": "需求资料"})
            self.assertEqual(status, 201)
            file_source = json.loads(raw)["source"]
            self.assertEqual(file_source["status"], "registered")
            status, headers, body = send("GET", file_source["download_url"])
            self.assertEqual(status, 200)
            self.assertEqual(body, file_bytes)
            self.assertIn("attachment", headers["Content-Disposition"].lower())
            status, _, raw = send("GET", "/api/sources")
            self.assertEqual(status, 200)
            self.assertEqual(len(json.loads(raw)["sources"]), 2)
            status, _, _ = send("DELETE", f"/api/sources/{file_source['id']}")
            self.assertEqual(status, 200)
            status, _, raw = send("GET", "/api/sources?include_removed=1")
            self.assertEqual(status, 200)
            self.assertEqual(len(json.loads(raw)["sources"]), 2)
            status, _, _ = send("POST", f"/api/sources/{file_source['id']}/restore", {})
            self.assertEqual(status, 200)
            status, _, body = send("GET", file_source["download_url"])
            self.assertEqual((status, body), (200, file_bytes))
        finally:
            connection.close()
            httpd.shutdown()
            thread.join()
            httpd.server_close()
            dashboard_server.PORT = old_port

    def _upsert(self, record):
        args = type("Args", (), {"input": "-"})()
        with patch("sys.stdin", io.StringIO(json.dumps(record))), contextlib.redirect_stdout(io.StringIO()):
            dashboardctl.cmd_upsert(args)

    def test_batches_join_until_terminal_then_roll_forward_on_next_create(self):
        self._upsert({"id": "TASK-A", "type": "task", "title": "A", "status": "in_progress", "status_detail": "A initial brief"})
        first_batch = dashboardctl.load_store()["current_batch_id"]
        self._upsert({"id": "TASK-A", "type": "task", "title": "A renamed", "status": "in_progress"})
        self._upsert({"id": "TASK-B", "type": "task", "title": "B", "status": "in_progress"})
        db = dashboardctl.load_store()
        self.assertEqual(db["current_batch_id"], first_batch)
        self.assertEqual([r["batch_id"] for r in db["records"] if r["type"] == "task"], [first_batch, first_batch])
        self.assertEqual(next(r for r in db["records"] if r["id"] == "TASK-A")["initial_brief"], "A initial brief")
        self._upsert({"id": "TASK-A", "type": "task", "title": "A", "status": "completed"})
        self._upsert({"id": "TASK-B", "type": "task", "title": "B", "status": "cancelled"})
        before_new_task = dashboardctl.load_store()
        self.assertEqual(before_new_task["batches"][0]["state"], "current")
        self._upsert({"id": "TASK-C", "type": "task", "title": "C", "status": "in_progress"})
        after = dashboardctl.load_store()
        self.assertEqual(after["batches"][0]["state"], "past")
        self.assertEqual(after["batches"][1]["state"], "current")
        self.assertNotEqual(after["records"][-1]["batch_id"], first_batch)

    def test_manual_completion_and_reopen_move_task_with_history(self):
        self._upsert({"id": "TASK-A", "type": "task", "title": "A", "status": "in_progress"})
        first_batch = dashboardctl.load_store()["current_batch_id"]
        dashboardctl.set_manual_completion("TASK-A", True)
        self._upsert({"id": "TASK-B", "type": "task", "title": "B", "status": "in_progress"})
        db = dashboardctl.load_store()
        second_batch = db["current_batch_id"]
        self.assertNotEqual(first_batch, second_batch)
        self.assertEqual(db["batches"][0]["state"], "past")
        dashboardctl.set_manual_completion("TASK-A", False)
        row = next(r for r in dashboardctl.load_store()["records"] if r["id"] == "TASK-A")
        self.assertEqual(row["batch_id"], second_batch)
        self.assertEqual(row["batch_history"][0]["batch_id"], first_batch)

    def test_upsert_reopen_clears_manual_override_and_rejoins_current_batch(self):
        self._upsert({"id": "TASK-A", "type": "task", "title": "A", "status": "in_progress"})
        dashboardctl.set_manual_completion("TASK-A", True)
        self._upsert({"id": "TASK-B", "type": "task", "title": "B", "status": "in_progress"})
        current_batch = dashboardctl.load_store()["current_batch_id"]
        self._upsert({"id": "TASK-A", "type": "task", "title": "A", "status": "in_progress"})
        row = next(r for r in dashboardctl.load_store()["records"] if r["id"] == "TASK-A")
        self.assertIsNone(row["manual_status"])
        self.assertEqual(row["batch_id"], current_batch)
        self.assertTrue(any("手动完成历史保留" in item.get("note", "") for item in row["history"]))

    def test_inspection_status_redacts_helper_token_and_run_returns_async_event(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
        old_port = dashboard_server.PORT
        dashboard_server.PORT = httpd.server_port
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        old_dispatcher = dashboard_server.INSPECTION_DISPATCHER
        dispatcher_dir = Path(self.temp.name) / "bug-monitor"
        dispatcher_dir.mkdir()
        dashboard_server.INSPECTION_DISPATCHER = dispatcher_dir / "dispatch_inspection.py"
        dashboard_server.INSPECTION_DISPATCHER.write_text("# fixture only\n", encoding="utf-8")
        helper = {"slot_id": "2026-10-21T10:30+08:00", "next_at": "2026-10-21T10:30+08:00", "timezone": "Asia/Shanghai",
                  "active_run": {"token": "private-token", "slot_id": "2026-10-21T10:30+08:00", "source": "manual", "status": "running"},
                  "slot": {"status": "running"}}
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(helper)})()
        with patch.object(dashboard_server.subprocess, "run", return_value=completed):
            try:
                connection.request("GET", "/api/bug-inspection/status", headers={"Host": f"127.0.0.1:{httpd.server_port}"})
                response = connection.getresponse()
                status = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(status["slot_id"], helper["slot_id"])
                self.assertEqual(status["active_event"]["status"], "running")
                self.assertNotIn("token", status["active_event"])
                self.assertTrue(status["dispatch_available"])

                origin = f"http://127.0.0.1:{httpd.server_port}"
                dispatch = {"state": "queued", "event_id": "event-123", "slot_id": helper["slot_id"], "pid": 1234}
                queued = type("Completed", (), {"returncode": 0, "stdout": json.dumps(dispatch)})()
                with patch.object(dashboard_server.subprocess, "run", return_value=queued):
                    connection.request("POST", "/api/bug-inspection/run", body=b"{}", headers={
                        "Host": f"127.0.0.1:{httpd.server_port}", "Origin": origin, "Content-Type": "application/json"})
                    accepted = connection.getresponse()
                    body = json.loads(accepted.read())
                self.assertEqual(accepted.status, 202)
                self.assertEqual(body["status"], "queued")
                self.assertEqual(body["event_id"], "event-123")
                self.assertEqual(body["poll_url"], "/api/bug-inspection/status")

                with patch.object(dashboard_server, "INSPECTION_DISPATCHER", Path("/no/such/dispatcher.py")):
                    connection.request("POST", "/api/bug-inspection/run", body=b"{}", headers={
                        "Host": f"127.0.0.1:{httpd.server_port}", "Origin": origin, "Content-Type": "application/json"})
                    rejected = connection.getresponse()
                    self.assertEqual(rejected.status, 503)
                    self.assertEqual(json.loads(rejected.read())["error"], "inspection_dispatch_unavailable")
            finally:
                connection.close()
                httpd.shutdown()
                thread.join()
                httpd.server_close()
                dashboard_server.PORT = old_port
                dashboard_server.INSPECTION_DISPATCHER = old_dispatcher

    def test_batch_init_adopts_only_explicit_legacy_task_and_context_is_append_only(self):
        db = self.read_db()
        db["records"].append({"id": "LEGACY-TASK", "type": "task", "title": "legacy", "status": "in_progress", "status_detail": "initial request"})
        dashboardctl.atomic_write(db)
        self.assertEqual(dashboard_server.build_payload()["legacy_task_ids"], ["LEGACY-TASK"])
        adopted = dashboardctl.adopt_task_batch("LEGACY-TASK")
        self.assertEqual(adopted["record"]["initial_brief"], "initial request")
        payload = dashboard_server.build_payload()
        self.assertEqual(payload["current_batch_id"], adopted["current_batch_id"])
        self.assertEqual(payload["batches"][0]["task_ids"], ["LEGACY-TASK"])
        task_view = next(row for row in payload["delegated"] if row["id"] == "LEGACY-TASK")
        self.assertEqual(task_view["batch_id"], adopted["current_batch_id"])
        self.assertEqual(task_view["latest_brief"], "")
        self.assertEqual(payload["legacy_task_ids"], [])
        with self.assertRaisesRegex(ValueError, "record_not_task"):
            dashboardctl.adopt_task_batch("BUG-1")
        dashboardctl.add_progress("LEGACY-TASK", "progress one", actor="agent", evidence=["local check"])
        dashboardctl.add_progress("LEGACY-TASK", "user correction", actor="user")
        dashboardctl.add_progress("LEGACY-TASK", "final result", actor="agent", result=True, outcome="partial")
        context = dashboardctl.task_context("LEGACY-TASK")
        self.assertEqual(context["initial_brief"], "initial request")
        self.assertEqual(context["latest_brief"], "final result")
        self.assertEqual([entry["message"] for entry in context["progress"]], ["progress one", "user correction", "final result"])
        self.assertEqual(context["final_result"]["outcome"], "partial")


if __name__ == "__main__":
    unittest.main()
