import base64
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import dashboardctl
import server as dashboard_server


class LibraryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patchers = [
            patch.object(dashboardctl, "ROOT", self.root),
            patch.object(dashboardctl, "STORE", self.root / "store.json"),
            patch.object(dashboardctl, "LOCK", self.root / ".store.lock"),
            patch.object(dashboardctl, "EVENTS", self.root / "events.jsonl"),
            patch.object(dashboardctl, "UPLOADS", self.root / "sources" / "uploads"),
        ]
        for patcher in self.patchers:
            patcher.start()
        dashboardctl.atomic_write({"schema_version": 1, "revision": 0, "records": [], "sources": []})

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def add_link(self, url, name="资料", **kwargs):
        return dashboardctl.add_source_link(url, name, **kwargs)["source"]

    def test_fixed_slot_versions_keep_all_history_and_expose_only_latest(self):
        first = self.add_link("https://example.com/req-1", "需求 v1", slot="requirement_doc", version_label="v1")
        second = self.add_link("https://example.com/req-2", "需求 v2", slot="requirement_doc",
                               group_id=first["group_id"], version_label="v2")
        third = self.add_link("https://example.com/req-3", "需求 v3", slot="requirement_doc",
                              group_id=first["group_id"], version_label="v3")
        manifest = dashboardctl.source_manifest()
        by_id = {item["id"]: item for item in manifest["sources"]}
        self.assertEqual(len(by_id), 3)
        self.assertEqual({item["group_id"] for item in by_id.values()}, {first["group_id"]})
        self.assertEqual([by_id[item["id"]]["version_no"] for item in (first, second, third)], [1, 2, 3])
        self.assertEqual([by_id[item["id"]]["is_latest"] for item in (first, second, third)], [False, False, True])
        self.assertTrue(all(item["version_count"] == 3 for item in by_id.values()))
        self.assertEqual(by_id[third["id"]]["version_label"], "v3")
        self.assertEqual(by_id[third["id"]]["logo_kind"], "requirement_doc")
        self.assertTrue(by_id[third["id"]]["latest"])

        latest_only = dashboardctl.source_manifest(all_versions=False)["sources"]
        self.assertEqual([item["id"] for item in latest_only], [third["id"]])
        self.assertFalse(dashboardctl.source_manifest(all_versions=False)["all_versions"])

    def test_requirement_inspection_reads_only_latest_and_previous_active_version(self):
        first = self.add_link("https://example.com/req-a", "需求 A", slot="requirement_doc")
        second = self.add_link("https://example.com/req-b", "需求 B", slot="requirement_doc", group_id=first["group_id"])
        third = self.add_link("https://example.com/req-c", "需求 C", slot="requirement_doc", group_id=first["group_id"])
        selected = dashboardctl.source_inspection_selection()
        self.assertEqual([item["id"] for item in selected], [third["id"], second["id"]])
        self.assertNotIn(first["id"], [item["id"] for item in selected])
        stored = {item["id"]: item for item in dashboardctl.source_manifest()["sources"]}
        self.assertTrue(stored[first["id"]]["inspection_enabled"])
        self.assertFalse(stored[first["id"]]["inspection_active"])
        self.assertTrue(stored[second["id"]]["inspection_active"])
        self.assertTrue(stored[third["id"]]["inspection_active"])

        dashboardctl.set_source_inspection(second["id"], False)
        self.assertEqual([item["id"] for item in dashboardctl.source_inspection_selection()], [third["id"]])
        dashboardctl.set_source_inspection(third["id"], False)
        self.assertEqual(dashboardctl.source_inspection_selection(), [])

    def test_other_sources_join_inspection_only_after_explicit_enable(self):
        figma = self.add_link("https://example.com/figma", "视觉稿", slot="figma")
        self.assertFalse(figma["inspection_enabled"])
        self.assertEqual(dashboardctl.source_inspection_selection(), [])
        dashboardctl.set_source_inspection(figma["id"], True)
        selected = dashboardctl.source_inspection_selection()
        self.assertEqual([item["id"] for item in selected], [figma["id"]])
        self.assertTrue(selected[0]["inspection_active"])

        second = self.add_link("https://example.com/figma-2", "视觉稿 2", slot="figma", group_id=figma["group_id"])
        self.assertFalse(second["inspection_enabled"])
        self.assertEqual([item["id"] for item in dashboardctl.source_inspection_selection()], [figma["id"]])
        dashboardctl.set_source_inspection(second["id"], True)
        self.assertEqual({item["id"] for item in dashboardctl.source_inspection_selection()}, {figma["id"], second["id"]})

    def test_slot_assignment_moves_the_group_and_sets_auto_inspection(self):
        other = self.add_link("https://example.com/misc", "普通资料")
        self.assertIsNone(other["slot"])
        self.assertEqual(other["logo_kind"], "link")
        moved = dashboardctl.set_source_slot(other["id"], "requirement_doc")["source"]
        self.assertEqual(moved["slot"], "requirement_doc")
        self.assertTrue(moved["inspection_enabled"])
        self.assertEqual(moved["inspection_mode"], "auto")
        self.assertEqual([item["id"] for item in dashboardctl.source_inspection_selection()], [other["id"]])

        version = self.add_link("https://example.com/misc-2", "普通资料 2", group_id=other["group_id"])
        self.assertEqual(version["slot"], "requirement_doc")
        self.assertTrue(version["inspection_enabled"])

        moved_out = dashboardctl.set_source_slot(other["id"], "figma")["source"]
        self.assertEqual(moved_out["slot"], "figma")
        manifest = {item["id"]: item for item in dashboardctl.source_manifest()["sources"]}
        self.assertFalse(manifest[other["id"]]["inspection_enabled"])
        self.assertFalse(manifest[version["id"]]["inspection_enabled"])
        self.assertEqual(dashboardctl.source_inspection_selection(), [])

        cleared = dashboardctl.set_source_slot(other["id"], None)["source"]
        self.assertIsNone(cleared["slot"])
        self.assertEqual(cleared["inspection_mode"], "manual")

    def test_group_validation_and_argument_limits(self):
        first = self.add_link("https://example.com/group", "组", slot="figma")
        with self.assertRaisesRegex(ValueError, "source_group_not_found"):
            dashboardctl.add_source_link("https://example.com/x", "x", slot="figma", group_id="missing-group")
        with self.assertRaisesRegex(ValueError, "source_group_slot_conflict"):
            dashboardctl.add_source_link("https://example.com/y", "y", slot="codesign", group_id=first["group_id"])
        with self.assertRaisesRegex(ValueError, "invalid_source_slot"):
            dashboardctl.add_source_link("https://example.com/z", "z", slot="unknown")
        with self.assertRaisesRegex(ValueError, "invalid_source_group"):
            dashboardctl.add_source_link("https://example.com/g", "g", group_id="bad group")
        with self.assertRaisesRegex(ValueError, "invalid_source_version"):
            dashboardctl.add_source_link("https://example.com/v", "v", version_label="bad\nlabel")
        with self.assertRaisesRegex(ValueError, "source_duplicate"):
            dashboardctl.add_source_link("https://example.com/group", "重复")

    def test_removed_and_restored_versions_still_derive_an_active_latest(self):
        first = self.add_link("https://example.com/keep-1", "需求 1", slot="requirement_doc")
        second = self.add_link("https://example.com/keep-2", "需求 2", slot="requirement_doc", group_id=first["group_id"])
        dashboardctl.change_source_state(second["id"], False)
        manifest = dashboardctl.source_manifest(include_removed=True)
        stored = {item["id"]: item for item in manifest["sources"]}
        self.assertTrue(stored[first["id"]]["is_latest"])
        self.assertEqual(stored[first["id"]]["version_count"], 1)
        self.assertFalse(stored[second["id"]]["is_latest"])
        self.assertEqual([item["id"] for item in dashboardctl.source_inspection_selection()], [first["id"]])

        dashboardctl.change_source_state(second["id"], True)
        stored = {item["id"]: item for item in dashboardctl.source_manifest()["sources"]}
        self.assertTrue(stored[second["id"]]["is_latest"])
        self.assertFalse(stored[first["id"]]["is_latest"])
        self.assertEqual([item["id"] for item in dashboardctl.source_inspection_selection()], [second["id"], first["id"]])

    def test_source_add_cli_accepts_library_options(self):
        from argparse import Namespace
        from contextlib import redirect_stdout
        from io import StringIO
        output = StringIO()
        args = Namespace(url="https://example.com/cli", name="CLI 资料", category="需求资料", origin="agent_import",
                         purpose=None, primary=None, slot="figma", group_id=None, version_label="v1")
        with redirect_stdout(output):
            dashboardctl.cmd_source_add_link(args)
        created = json.loads(output.getvalue())["source"]
        self.assertEqual((created["slot"], created["version_label"]), ("figma", "v1"))

        file_path = self.root / "接口.md"
        file_path.write_text("# 接口\n", encoding="utf-8")
        output = StringIO()
        args = Namespace(path=str(file_path), category="接口文档", origin="agent_import", name="接口说明",
                         slot="api_doc", group_id=None, version_label="v1")
        with redirect_stdout(output):
            dashboardctl.cmd_source_add_file(args)
        uploaded = json.loads(output.getvalue())["source"]
        self.assertEqual((uploaded["slot"], uploaded["name"], uploaded["version_label"]), ("api_doc", "接口说明", "v1"))

    def test_legacy_sources_keep_working_without_library_fields(self):
        dashboardctl.atomic_write({
            "schema_version": 1, "revision": 2, "records": [],
            "sources": [{
                "id": "LEGACY-1", "type": "link", "name": "旧资料", "url": "https://example.com/legacy",
                "normalized_url": "https://example.com/legacy", "category": "需求资料", "active": True,
                "status": "registered", "added_at": "2026-01-01T09:00:00+08:00", "updated_at": "2026-01-01T09:00:00+08:00"
            }]
        })
        item = dashboardctl.source_manifest()["sources"][0]
        self.assertIsNone(item["slot"])
        self.assertEqual(item["group_id"], "LEGACY-1")
        self.assertEqual(item["version_no"], 1)
        self.assertEqual(item["version_count"], 1)
        self.assertTrue(item["is_latest"])
        self.assertFalse(item["inspection_enabled"])
        self.assertEqual(item["logo_kind"], "link")
        self.assertTrue(dashboardctl.set_source_inspection("LEGACY-1", True)["source"]["inspection_enabled"])


class LibraryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patchers = [
            patch.object(dashboardctl, "ROOT", self.root),
            patch.object(dashboardctl, "STORE", self.root / "store.json"),
            patch.object(dashboardctl, "LOCK", self.root / ".store.lock"),
            patch.object(dashboardctl, "EVENTS", self.root / "events.jsonl"),
            patch.object(dashboardctl, "UPLOADS", self.root / "sources" / "uploads"),
            patch.object(dashboard_server, "STORE", self.root / "store.json"),
            patch.object(dashboard_server, "SCOPE_CATALOG", self.root / "scope-catalog.json"),
        ]
        for patcher in self.patchers:
            patcher.start()
        dashboardctl.atomic_write({"schema_version": 1, "revision": 0, "records": [], "sources": []})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
        self.old_port = dashboard_server.PORT
        dashboard_server.PORT = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join()
        self.httpd.server_close()
        dashboard_server.PORT = self.old_port
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def send(self, method, path, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            body = json.dumps(payload or {}).encode()
            connection.request(method, path, body=body, headers={
                "Host": f"127.0.0.1:{self.httpd.server_port}",
                "Origin": self.origin,
                "Content-Type": "application/json",
            })
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
        finally:
            connection.close()

    def test_source_edit_api_updates_name_and_link_without_changing_identity(self):
        _, first = self.send("POST", "/api/sources/link", {"name": "资料 A", "url": "https://example.com/a"})
        _, second = self.send("POST", "/api/sources/link", {"name": "资料 B", "url": "https://example.com/b"})
        source = first["source"]
        status, edited = self.send("POST", f"/api/sources/{source['id']}/edit",
                                   {"name": "资料 A · 编辑", "url": "https://example.com/a-new"})
        self.assertEqual(status, 200)
        self.assertEqual(edited["source"]["id"], source["id"])
        self.assertEqual(edited["source"]["name"], "资料 A · 编辑")
        self.assertEqual(edited["source"]["url"], "https://example.com/a-new")
        self.assertEqual(edited["source"]["group_id"], source["group_id"])
        status, duplicate = self.send("POST", f"/api/sources/{source['id']}/edit",
                                      {"url": second["source"]["url"]})
        self.assertEqual((status, duplicate["error"]), (409, "source_duplicate"))
        status, invalid = self.send("POST", f"/api/sources/{source['id']}/edit", {"url": "ftp://example.com/x"})
        self.assertEqual((status, invalid["error"]), (400, "invalid_source_url"))
        status, missing = self.send("POST", "/api/sources/missing/edit", {"name": "x"})
        self.assertEqual((status, missing["error"]), (404, "source_not_found"))

    def test_source_api_accepts_versions_inspection_and_slot_changes(self):
        status, first = self.send("POST", "/api/sources/link", {
            "name": "需求文档", "url": "https://example.com/doc-1", "slot": "requirement_doc", "version_label": "初稿"
        })
        self.assertEqual(status, 201)
        source = first["source"]
        self.assertEqual((source["slot"], source["version_no"], source["version_label"]), ("requirement_doc", 1, "初稿"))
        self.assertTrue(source["is_latest"])
        status, second = self.send("POST", "/api/sources/link", {
            "name": "需求文档", "url": "https://example.com/doc-2", "slot": "requirement_doc",
            "group_id": source["group_id"], "version_label": "v2"
        })
        self.assertEqual(status, 201)
        self.assertEqual(second["source"]["version_no"], 2)
        self.assertTrue(second["source"]["is_latest"])

        status, manifest = self.send("GET", "/api/sources?all_versions=1")
        self.assertEqual(status, 200)
        self.assertTrue(manifest["all_versions"])
        self.assertEqual(len(manifest["sources"]), 2)
        self.assertEqual({item["group_id"] for item in manifest["sources"]}, {source["group_id"]})
        status, default_manifest = self.send("GET", "/api/sources")
        self.assertEqual((status, len(default_manifest["sources"])), (200, 2))
        self.assertTrue(default_manifest["all_versions"])
        status, latest = self.send("GET", "/api/sources?all_versions=0")
        self.assertEqual([item["id"] for item in latest["sources"]], [second["source"]["id"]])
        self.assertFalse(latest["all_versions"])

        status, toggled = self.send("POST", f"/api/sources/{source['id']}/inspection", {"enabled": True})
        self.assertEqual(status, 200)
        self.assertTrue(toggled["source"]["inspection_enabled"])
        status, _ = self.send("POST", f"/api/sources/{source['id']}/inspection", {"enabled": "yes"})
        self.assertEqual(status, 400)

        status, moved = self.send("POST", f"/api/sources/{source['id']}/slot", {"slot": "figma"})
        self.assertEqual(status, 200)
        self.assertEqual(moved["source"]["slot"], "figma")
        status, cleared = self.send("POST", f"/api/sources/{source['id']}/slot", {"slot": None})
        self.assertEqual(status, 200)
        self.assertIsNone(cleared["source"]["slot"])
        status, error = self.send("POST", f"/api/sources/{source['id']}/slot", {"slot": "unknown"})
        self.assertEqual((status, error["error"]), (400, "invalid_source_slot"))
        status, error = self.send("POST", "/api/sources/missing/slot", {"slot": "figma"})
        self.assertEqual((status, error["error"]), (404, "source_not_found"))
        status, error = self.send("POST", f"/api/sources/{source['id']}/inspection", {"enabled": True, "extra": 1})
        self.assertEqual((status, error["error"]), (400, "unsupported_fields"))

    def test_file_upload_accepts_library_metadata_and_keeps_download_working(self):
        file_bytes = b"%PDF-1.4\nlibrary fixture"
        status, created = self.send("POST", "/api/sources/file", {
            "filename": "接口说明.pdf", "content_base64": base64.b64encode(file_bytes).decode(),
            "category": "接口文档", "name": "接口说明", "slot": "api_doc", "version_label": "v1"
        })
        self.assertEqual(status, 201)
        source = created["source"]
        self.assertEqual((source["slot"], source["version_label"], source["logo_kind"]), ("api_doc", "v1", "api_doc"))
        self.assertTrue(source["is_latest"])
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.request("GET", source["download_url"])
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), file_bytes)
        finally:
            connection.close()

    def test_removed_and_restore_contract_still_works_with_version_fields(self):
        status, created = self.send("POST", "/api/sources/link", {
            "name": "资料", "url": "https://example.com/remove", "slot": "figma"
        })
        self.assertEqual(status, 201)
        source_id = created["source"]["id"]
        status, removed = self.send("DELETE", f"/api/sources/{source_id}")
        self.assertEqual(status, 200)
        self.assertFalse(removed["source"]["is_latest"])
        status, manifest = self.send("GET", "/api/sources?include_removed=1&all_versions=1")
        removed_item = next(item for item in manifest["sources"] if item["id"] == source_id)
        self.assertEqual(removed_item["status"], "removed")
        status, restored = self.send("POST", f"/api/sources/{source_id}/restore", {})
        self.assertEqual(status, 200)
        self.assertTrue(restored["source"]["is_latest"])
        status, lifecycle = self.send("POST", f"/api/sources/{source_id}/lifecycle", {"lifecycle_override": "active"})
        self.assertEqual(status, 200)
        self.assertEqual(lifecycle["source"]["lifecycle_override"], "active")
        status, read_state = self.send("POST", f"/api/sources/{source_id}/read-state", {
            "last_read_at": "2026-11-13T10:00:00+08:00", "read_state": "success",
            "read_scope": "all", "read_gap": [], "content_hash": "a" * 64, "content_updated_at": None
        })
        self.assertEqual(status, 200)
        self.assertEqual(read_state["source"]["last_inspection_success_at"], "2026-11-13T10:00:00+08:00")
        self.assertEqual(read_state["source"]["last_inspection_status"], "success")


class SourceEditAndPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patchers = [
            patch.object(dashboardctl, "ROOT", self.root),
            patch.object(dashboardctl, "STORE", self.root / "store.json"),
            patch.object(dashboardctl, "LOCK", self.root / ".store.lock"),
            patch.object(dashboardctl, "EVENTS", self.root / "events.jsonl"),
            patch.object(dashboardctl, "UPLOADS", self.root / "sources" / "uploads"),
        ]
        for patcher in self.patchers:
            patcher.start()
        dashboardctl.atomic_write({"schema_version": 1, "revision": 0, "records": [], "sources": []})

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_update_source_renames_and_relinks_without_changing_id(self):
        source = dashboardctl.add_source_link("https://example.com/old", "旧名字")["source"]
        updated = dashboardctl.update_source(source["id"], name=" 新名字  ", url="https://example.com/new")["source"]
        self.assertEqual(updated["id"], source["id"])
        self.assertEqual(updated["name"], "新名字")
        self.assertEqual(updated["url"], "https://example.com/new")
        self.assertEqual(updated["normalized_url"], "https://example.com/new")

    def test_update_source_name_keeps_visible_override_in_sync(self):
        source = dashboardctl.add_source_link("https://example.com/override", "原始名字")["source"]
        dashboardctl.override_source(source["id"], {"name": "整理后的名字"})
        updated = dashboardctl.update_source(source["id"], name="再次编辑")["source"]
        self.assertEqual(updated["name"], "再次编辑")
        self.assertEqual(updated["overrides"]["name"], "再次编辑")

    def test_update_source_rejects_duplicate_link(self):
        first = dashboardctl.add_source_link("https://example.com/a", "A")["source"]
        dashboardctl.add_source_link("https://example.com/b", "B")
        with self.assertRaisesRegex(ValueError, "source_duplicate"):
            dashboardctl.update_source(first["id"], url="https://example.com/b")

    def test_update_source_rejects_invalid_arguments(self):
        source = dashboardctl.add_source_link("https://example.com/a", "A")["source"]
        with self.assertRaisesRegex(ValueError, "invalid_source_update"):
            dashboardctl.update_source(source["id"])
        with self.assertRaisesRegex(ValueError, "invalid_source_name"):
            dashboardctl.update_source(source["id"], name="   ")
        with self.assertRaisesRegex(ValueError, "invalid_source_url"):
            dashboardctl.update_source(source["id"], url="ftp://example.com/x")

    def test_inspection_runs_summarize_today_with_titles(self):
        db = dashboardctl.load_store()
        db["records"].append(dashboardctl.normalize_record({
            "id": "BUG-INSPECT-1", "type": "bug", "title": "巡检新增 BUG", "status": "pending"}))
        db["records"].append(dashboardctl.normalize_record({
            "id": "1009", "type": "work", "title": "巡检新增需求", "status": "pending"}))
        dashboardctl.atomic_write(db)
        entry = dashboardctl.record_inspection({"status": "succeeded", "new_bugs": ["BUG-INSPECT-1"],
                                                 "new_requirements": ["1009"], "notes": "一轮"})
        self.assertTrue(entry["at"])
        summary = dashboardctl.inspection_runs(entry["at"][:10])
        self.assertEqual(summary["new_bug_count"], 1)
        self.assertEqual(summary["new_requirement_count"], 1)
        run = summary["runs"][0]
        self.assertEqual(run["new_bugs"][0]["title"], "巡检新增 BUG")
        self.assertEqual(run["new_requirements"][0]["type"], "work")
        with self.assertRaisesRegex(ValueError, "invalid_inspection_status"):
            dashboardctl.record_inspection({"status": "nope"})

    def test_source_note_supports_plain_and_masked_text(self):
        source = dashboardctl.add_source_link("https://codesign.qq.com/s/2", "原型")["source"]
        updated = dashboardctl.update_source(source["id"], note="访问密码：IUUR", note_masked=True)["source"]
        self.assertEqual(updated["note"], "访问密码：IUUR")
        self.assertTrue(updated["note_masked"])
        manifest = dashboardctl.source_manifest()
        row = next(item for item in manifest["sources"] if item["id"] == source["id"])
        self.assertEqual(row["note"], "访问密码：IUUR")
        self.assertTrue(row["note_masked"])
        with self.assertRaisesRegex(ValueError, "invalid_source_note_masked"):
            dashboardctl.update_source(source["id"], note="x", note_masked="yes")

    def test_workflow_reports_are_mirrored_into_record_folder(self):
        record = {"id": "1001", "type": "task", "workflow": {"gates": {"audit_response": {"outputs": {
            "audit_report": "# 审计报告\n- 结论：可合入", "dev_response": "# 开发回应\n- 决定：采纳"}}}}}
        dashboardctl._persist_workflow_reports(record)
        folder = self.root / "records" / "task" / "1001"
        self.assertIn("可合入", (folder / "audit-report.md").read_text(encoding="utf-8"))
        self.assertIn("采纳", (folder / "dev-response.md").read_text(encoding="utf-8"))

    def test_workflow_report_paths_are_not_copied_as_bodies(self):
        record = {"id": "1002", "type": "task", "workflow": {"gates": {"audit_response": {"outputs": {
            "audit_report": "/tmp/report.md"}}}}}
        dashboardctl._persist_workflow_reports(record)
        self.assertFalse((self.root / "records" / "task" / "1002" / "audit-report.md").exists())

    def test_preview_helpers_reject_private_hosts_and_parse_title(self):
        self.assertFalse(dashboardctl._preview_host_allowed("localhost"))
        self.assertFalse(dashboardctl._preview_host_allowed("127.0.0.1"))
        self.assertFalse(dashboardctl._preview_host_allowed("10.1.2.3"))
        self.assertEqual(dashboardctl._extract_html_title("<html><title> A &amp; B </title></html>"), "A & B")
        with self.assertRaisesRegex(ValueError, "invalid_source_url"):
            dashboardctl.preview_source_link("http://127.0.0.1/secret")
        with self.assertRaisesRegex(ValueError, "invalid_source_url"):
            dashboardctl.preview_source_link("ftp://example.com/x")


if __name__ == "__main__":
    unittest.main()