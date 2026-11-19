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


class ArchiveStoreTests(unittest.TestCase):
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
        dashboardctl.atomic_write({
            "schema_version": 2, "revision": 0, "records": [], "sources": [],
            "iterations": [], "current_iteration_id": None
        })

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_iteration_crud_and_lifecycle(self):
        # Create iteration
        created = dashboardctl.create_iteration(
            "v13.4.2", "13.4.2 智能相册与AI音乐优化",
            target_release_date="2026-11-30",
            description="本迭代包含Wan3.0与BGM优化"
        )["iteration"]
        self.assertEqual(created["version_tag"], "v13.4.2")
        self.assertEqual(created["status"], "active")
        self.assertIsNone(created["released_at"])

        # List iterations
        listing = dashboardctl.list_iterations()
        self.assertEqual(len(listing["iterations"]), 1)
        self.assertEqual(listing["current_iteration_id"], created["id"])

        # Update iteration
        updated = dashboardctl.update_iteration(created["id"], {"description": "更新描述"})["iteration"]
        self.assertEqual(updated["description"], "更新描述")

        # Create second iteration and switch
        created2 = dashboardctl.create_iteration(
            "v13.5.0", "13.5.0 创新功能探索", status="planning"
        )["iteration"]
        self.assertEqual(dashboardctl.list_iterations()["current_iteration_id"], created["id"])
        dashboardctl.set_current_iteration(created2["id"])
        self.assertEqual(dashboardctl.list_iterations()["current_iteration_id"], created2["id"])

        # Release first iteration
        released = dashboardctl.release_iteration(created["id"], notes=["顺利发布上线"])["iteration"]
        self.assertEqual(released["status"], "released")
        self.assertIsNotNone(released["released_at"])
        self.assertIn("顺利发布上线", released["notes"])

    def test_sources_scoping_global_and_iteration(self):
        it = dashboardctl.create_iteration("v13.4.2", "13.4.2 迭代")["iteration"]

        # Global API doc
        global_api = dashboardctl.add_source_link(
            "https://api.example.com/doc", "通用接口文档", slot="api_doc", scope="global"
        )["source"]
        self.assertEqual(global_api["scope"], "global")
        self.assertIsNone(global_api["iteration_id"])

        # Iteration PRD
        iter_prd = dashboardctl.add_source_link(
            "https://wiki.example.com/prd-v1342", "13.4.2 需求列表", slot="requirement_doc",
            scope="iteration", iteration_id=it["id"], version_label="v1.0"
        )["source"]
        self.assertEqual(iter_prd["scope"], "iteration")
        self.assertEqual(iter_prd["iteration_id"], it["id"])

        # Iteration Figma
        iter_figma = dashboardctl.add_source_link(
            "https://figma.com/file/1342", "13.4.2 Figma设计稿", slot="figma",
            scope="iteration", iteration_id=it["id"]
        )["source"]
        self.assertEqual(iter_figma["slot"], "figma")
        self.assertEqual(iter_figma["iteration_id"], it["id"])

        # Test context assembly
        context = dashboardctl.assemble_archive_context(iteration_id=it["id"])
        self.assertEqual(context["iteration"]["id"], it["id"])
        self.assertEqual(len(context["global_materials"]), 1)
        self.assertEqual(context["global_materials"][0]["id"], global_api["id"])
        self.assertEqual(len(context["iteration_materials"]), 2)
        self.assertIn("研发工作上下文基线", context["summary_markdown"])
        self.assertIn("13.4.2 需求列表", context["summary_markdown"])
        self.assertIn("通用接口文档", context["summary_markdown"])

    def test_record_attachment_for_tasks_and_work(self):
        task = dashboardctl.normalize_record({
            "id": "ZT-T-ATTACH001",
            "type": "task",
            "title": "测试任务带附件",
            "status": "in_progress",
        })
        db = dashboardctl.load_store()
        db["records"].append(task)
        dashboardctl.atomic_write(db)

        png_data = b"\x89PNG\r\n\x1a\n" + b"fake-task-screenshot"
        res = dashboardctl.add_record_attachment(
            "ZT-T-ATTACH001", "chat_pm.png", png_data, summary="PM确认不需要横屏"
        )
        self.assertEqual(res["attachment"]["filename"], "chat_pm.png")
        self.assertEqual(res["attachment"]["summary"], "PM确认不需要横屏")
        self.assertEqual(res["attachment"]["download_url"], "/api/items/ZT-T-ATTACH001/attachments/chat_pm.png")

        # Read back
        size = dashboardctl.attachment_size("ZT-T-ATTACH001", "chat_pm.png")
        self.assertEqual(size, len(png_data))
        body, mime, info = dashboardctl.read_record_attachment("ZT-T-ATTACH001", "chat_pm.png")
        self.assertEqual(body, png_data)
        self.assertEqual(mime, "image/png")

    def test_migrate_iteration_archive(self):
        # Setup legacy store with records and sources without iteration/scope
        db = dashboardctl.load_store()
        db["records"] = [
            dashboardctl.normalize_record({"id": "ZT-W-LEGACY1", "type": "work", "title": "旧需求", "status": "in_progress"}),
            dashboardctl.normalize_record({"id": "ZT-T-LEGACY2", "type": "task", "title": "旧任务", "status": "completed"})
        ]
        db["sources"] = [
            {"id": "src-legacy-prd", "name": "老PRD", "slot": "requirement_doc", "type": "link", "url": "https://example.com/prd", "active": True},
            {"id": "src-legacy-api", "name": "老API", "slot": "api_doc", "type": "link", "url": "https://example.com/api", "active": True}
        ]
        dashboardctl.atomic_write(db)

        # Run migration
        res = dashboardctl.migrate_iteration_archive()
        self.assertEqual(res["records_migrated"], 2)
        self.assertEqual(res["sources_migrated"], 2)

        # Verify default iteration
        def_it = res["default_iteration"]
        self.assertEqual(def_it["version_tag"], "v13.4.2")
        self.assertEqual(def_it["status"], "active")

        # Verify db state
        db2 = dashboardctl.load_store()
        self.assertEqual(db2["current_iteration_id"], def_it["id"])
        self.assertEqual(db2["records"][0]["iteration_id"], def_it["id"])
        self.assertEqual(db2["records"][1]["iteration_id"], def_it["id"])

        sources_by_id = {s["id"]: s for s in db2["sources"]}
        self.assertEqual(sources_by_id["src-legacy-prd"]["scope"], "iteration")
        self.assertEqual(sources_by_id["src-legacy-prd"]["iteration_id"], def_it["id"])
        self.assertEqual(sources_by_id["src-legacy-api"]["scope"], "global")


class ArchiveApiTests(unittest.TestCase):
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
        ]
        for patcher in self.patchers:
            patcher.start()
        dashboardctl.atomic_write({
            "schema_version": 2, "revision": 0, "records": [], "sources": [],
            "iterations": [], "current_iteration_id": None
        })
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
        self.port = self.server.server_port
        self.old_port = dashboard_server.PORT
        dashboard_server.PORT = self.port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        dashboard_server.PORT = self.old_port
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {
            "Host": f"127.0.0.1:{self.port}",
            "Origin": f"http://127.0.0.1:{self.port}",
            "Content-Type": "application/json"
        }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, json.loads(raw.decode("utf-8")) if raw else None

    def test_iterations_api_lifecycle(self):
        # Create iteration via POST /api/iterations
        status, data = self.request("POST", "/api/iterations", {
            "version_tag": "v13.4.2",
            "title": "13.4.2 智能相册与AI音乐优化",
            "target_release_date": "2026-11-30",
            "description": "API测试迭代"
        })
        self.assertEqual(status, 201)
        it_id = data["iteration"]["id"]

        # GET /api/iterations
        status, data = self.request("GET", "/api/iterations")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["iterations"]), 1)
        self.assertEqual(data["current_iteration_id"], it_id)

        # GET /api/iterations/<id>
        status, data = self.request("GET", f"/api/iterations/{it_id}")
        self.assertEqual(status, 200)
        self.assertEqual(data["iteration"]["version_tag"], "v13.4.2")

        # POST /api/iterations/<id>/release
        status, data = self.request("POST", f"/api/iterations/{it_id}/release", {"note": "完成上线"})
        self.assertEqual(status, 200)
        self.assertEqual(data["iteration"]["status"], "released")


if __name__ == "__main__":
    unittest.main()
