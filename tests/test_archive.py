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

    def test_iteration_display_order_insert_and_reorder(self):
        first = dashboardctl.create_iteration("13.4.0", "13.4.0")["iteration"]
        second = dashboardctl.create_iteration("13.4.2 迭代二", "13.4.2 迭代二")["iteration"]
        inserted = dashboardctl.create_iteration("13.4.0 埋点", "13.4.0 埋点", order=1)["iteration"]

        listing = dashboardctl.list_iterations()["iterations"]
        self.assertEqual([it["id"] for it in listing], [first["id"], inserted["id"], second["id"]])
        self.assertEqual([it["order"] for it in listing], [0, 1, 2])

        reordered = dashboardctl.reorder_iterations([second["id"], first["id"], inserted["id"]])["iterations"]
        self.assertEqual([it["id"] for it in reordered], [second["id"], first["id"], inserted["id"]])
        self.assertEqual([it["order"] for it in reordered], [0, 1, 2])

        with self.assertRaisesRegex(ValueError, "iteration_duplicate"):
            dashboardctl.update_iteration(second["id"], {"version_tag": "13.4.0"})
        with self.assertRaisesRegex(ValueError, "iteration_duplicate"):
            dashboardctl.create_iteration("another-tag", "13.4.0 埋点")

    def test_iteration_id_charset_and_update_length_limits(self):
        dotted = dashboardctl.create_iteration(
            "13.4.0 埋点", "13.4.0 埋点", iteration_id="ITER-13.4.0-ANALYTICS"
        )["iteration"]
        self.assertEqual(dotted["id"], "ITER-13.4.0-ANALYTICS")
        updated = dashboardctl.update_iteration(dotted["id"], {"title": "13.4.0 埋点（更名）"})["iteration"]
        self.assertEqual(updated["title"], "13.4.0 埋点（更名）")

        with self.assertRaisesRegex(ValueError, "invalid_iteration_id"):
            dashboardctl.create_iteration("bad id", "bad id", iteration_id="bad id")
        with self.assertRaisesRegex(ValueError, "invalid_iteration_id"):
            dashboardctl.create_iteration("too long", "too long", iteration_id="a" * 121)
        with self.assertRaisesRegex(ValueError, "invalid_iteration_title"):
            dashboardctl.update_iteration(dotted["id"], {"title": "t" * 121})
        with self.assertRaisesRegex(ValueError, "invalid_iteration_version_tag"):
            dashboardctl.update_iteration(dotted["id"], {"version_tag": "t" * 51})

    def test_source_location_moves_group_and_archive_context_stays_isolated(self):
        first = dashboardctl.create_iteration("13.4.0", "13.4.0")["iteration"]
        second = dashboardctl.create_iteration("13.4.2 迭代二", "13.4.2 迭代二")["iteration"]
        group = dashboardctl.add_source_link(
            "https://example.com/prd-v1", "PRD v1", slot="requirement_doc",
            scope="iteration", iteration_id=first["id"]
        )["source"]
        v2 = dashboardctl.add_source_link(
            "https://example.com/prd-v2", "PRD v2", slot="requirement_doc", group_id=group["group_id"],
            scope="iteration", iteration_id=first["id"]
        )["source"]
        other = dashboardctl.add_source_link(
            "https://example.com/other", "另一个版本的材料", slot="supplementary",
            scope="iteration", iteration_id=second["id"]
        )["source"]
        public = dashboardctl.add_source_link(
            "https://example.com/api", "公共接口", slot="api_doc", scope="global"
        )["source"]

        context_first = dashboardctl.assemble_archive_context(iteration_id=first["id"])
        self.assertEqual({item["id"] for item in context_first["iteration_materials"]}, {group["id"], v2["id"]})
        self.assertNotIn(other["name"], context_first["summary_markdown"])
        self.assertIn(public["name"], context_first["summary_markdown"])

        # A version cannot be appended to a group living in another version.
        with self.assertRaisesRegex(ValueError, "source_group_location_conflict"):
            dashboardctl.add_source_link(
                "https://example.com/prd-v3", "PRD v3", slot="requirement_doc", group_id=group["group_id"],
                scope="iteration", iteration_id=second["id"]
            )

        moved = dashboardctl.set_source_location(group["id"], scope="iteration", iteration_id=second["id"], slot="supplementary")
        self.assertEqual(moved["group_size"], 2)
        context_first = dashboardctl.assemble_archive_context(iteration_id=first["id"])
        self.assertEqual(context_first["iteration_materials"], [])
        context_second = dashboardctl.assemble_archive_context(iteration_id=second["id"])
        self.assertEqual({item["id"] for item in context_second["iteration_materials"]}, {group["id"], v2["id"], other["id"]})
        self.assertTrue(all(item["scope"] == "iteration" for item in context_second["iteration_materials"]))
        self.assertTrue(all(item["iteration_id"] == second["id"] for item in context_second["iteration_materials"]))

        with self.assertRaisesRegex(ValueError, "invalid_source_slot"):
            dashboardctl.set_source_location(group["id"], scope="iteration", iteration_id=second["id"], slot="api_doc")

    def test_source_location_preserves_slot_and_syncs_inspection(self):
        first = dashboardctl.create_iteration("13.4.0", "13.4.0")["iteration"]
        second = dashboardctl.create_iteration("13.4.2 迭代二", "13.4.2 迭代二")["iteration"]
        prd = dashboardctl.add_source_link(
            "https://example.com/prd", "需求列表", slot="requirement_doc",
            scope="iteration", iteration_id=first["id"]
        )["source"]
        self.assertTrue(prd["inspection_enabled"])
        self.assertEqual(prd["inspection_mode"], "auto")

        preserved = dashboardctl.set_source_location(prd["id"], scope="iteration", iteration_id=second["id"])
        self.assertEqual(preserved["slot"], "requirement_doc")
        stored = next(item for item in dashboardctl._source_store_list(dashboardctl.load_store()) if item["id"] == prd["id"])
        self.assertEqual(stored["iteration_id"], second["id"])
        self.assertTrue(stored["inspection_enabled"])
        self.assertEqual(stored["inspection_mode"], "auto")

        moved_out = dashboardctl.set_source_location(prd["id"], scope="iteration", iteration_id=first["id"], slot="supplementary")
        stored = next(item for item in dashboardctl._source_store_list(dashboardctl.load_store()) if item["id"] == prd["id"])
        self.assertEqual(moved_out["slot"], "supplementary")
        self.assertFalse(stored["inspection_enabled"])
        self.assertEqual(stored["inspection_mode"], "manual")

        moved_back = dashboardctl.set_source_location(prd["id"], scope="iteration", iteration_id=second["id"], slot="requirement_doc")
        stored = next(item for item in dashboardctl._source_store_list(dashboardctl.load_store()) if item["id"] == prd["id"])
        self.assertEqual(moved_back["slot"], "requirement_doc")
        self.assertTrue(stored["inspection_enabled"])
        self.assertEqual(stored["inspection_mode"], "auto")

        global_api = dashboardctl.add_source_link(
            "https://example.com/api", "公共接口", slot="api_doc", scope="global"
        )["source"]
        with self.assertRaisesRegex(ValueError, "invalid_source_slot"):
            dashboardctl.set_source_location(global_api["id"], scope="iteration", iteration_id=first["id"])
        with self.assertRaisesRegex(ValueError, "invalid_source_slot"):
            dashboardctl.set_source_location(global_api["id"], scope="iteration", iteration_id=first["id"], slot="bogus")

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

    def test_dotted_iteration_ids_work_across_iteration_api(self):
        dotted = "ITER-13.4.0-ANALYTICS"
        status, created = self.request("POST", "/api/iterations", {
            "version_tag": "13.4.0 埋点", "title": "13.4.0 埋点", "iteration_id": dotted
        })
        self.assertEqual(status, 201)
        status, second = self.request("POST", "/api/iterations", {
            "version_tag": "13.4.2 迭代二", "title": "13.4.2 迭代二", "iteration_id": "ITER-13.4.2-iter2"
        })
        self.assertEqual(status, 201)
        second_id = second["iteration"]["id"]

        status, data = self.request("GET", f"/api/iterations/{dotted}")
        self.assertEqual(status, 200)
        self.assertEqual(data["iteration"]["id"], dotted)

        status, data = self.request("POST", f"/api/iterations/{dotted}/update", {
            "title": "13.4.0 埋点（更名）", "version_tag": "13.4.0 埋点（更名）"
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["iteration"]["title"], "13.4.0 埋点（更名）")

        status, data = self.request("POST", "/api/iterations/order", {"ids": [second_id, dotted]})
        self.assertEqual(status, 200)
        self.assertEqual([it["id"] for it in data["iterations"]], [second_id, dotted])

        status, data = self.request("POST", f"/api/iterations/{dotted}/release", {"note": "埋点版本收口"})
        self.assertEqual(status, 200)
        self.assertEqual(data["iteration"]["status"], "released")

        status, data = self.request("POST", f"/api/iterations/{second_id}/switch", {})
        self.assertEqual(status, 200)
        self.assertEqual(data["current_iteration_id"], second_id)

    def test_iteration_order_and_scoped_file_upload_api(self):
        status, first = self.request("POST", "/api/iterations", {"version_tag": "13.4.0", "title": "13.4.0"})
        self.assertEqual(status, 201)
        status, second = self.request("POST", "/api/iterations", {"version_tag": "13.4.2 迭代二", "title": "13.4.2 迭代二"})
        self.assertEqual(status, 201)
        first_id, second_id = first["iteration"]["id"], second["iteration"]["id"]

        status, data = self.request("POST", "/api/iterations/order", {"ids": [second_id, first_id]})
        self.assertEqual(status, 200)
        self.assertEqual([it["id"] for it in data["iterations"]], [second_id, first_id])

        encoded = base64.b64encode("需求补充说明".encode("utf-8")).decode("ascii")
        status, data = self.request("POST", "/api/sources/file", {
            "filename": "note.txt", "content_base64": encoded, "name": "版本补充说明",
            "slot": "supplementary", "scope": "iteration", "iteration_id": second_id
        })
        self.assertEqual(status, 201)
        self.assertEqual(data["source"]["scope"], "iteration")
        self.assertEqual(data["source"]["iteration_id"], second_id)

        status, data = self.request("POST", "/api/sources/file", {
            "filename": "note2.txt", "content_base64": encoded, "name": "公共补充说明",
            "slot": "supplementary", "scope": "global"
        })
        self.assertEqual(status, 201)
        self.assertIsNone(data["source"]["iteration_id"])

        status, data = self.request("POST", "/api/sources/file", {
            "filename": "bad.txt", "content_base64": encoded, "name": "错位资料",
            "slot": "figma", "scope": "global"
        })
        self.assertEqual(status, 400)

        prd = dashboardctl.add_source_link(
            "https://example.com/prd-api", "API 需求列表", slot="requirement_doc",
            scope="iteration", iteration_id=second_id
        )["source"]
        status, data = self.request("POST", f"/api/sources/{prd['id']}/location", {
            "scope": "iteration", "iteration_id": first_id
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["source"]["slot"], "requirement_doc")
        self.assertTrue(data["source"]["inspection_enabled"])
        self.assertEqual(data["source"]["inspection_mode"], "auto")

    def test_record_iteration_can_be_cleared(self):
        iteration = dashboardctl.create_iteration("13.4.2 迭代二", "13.4.2 迭代二")["iteration"]
        record = dashboardctl.normalize_record({
            "id": "ZT-W-CLEAR", "type": "work", "title": "归属可清空", "status": "in_progress",
            "iteration_id": iteration["id"]
        })
        db = dashboardctl.load_store()
        db["records"].append(record)
        dashboardctl.atomic_write(db)

        status, data = self.request("POST", "/api/items/ZT-W-CLEAR/iteration", {"iteration_id": None})
        self.assertEqual(status, 200)
        self.assertIsNone(data["record"]["iteration_id"])


if __name__ == "__main__":
    unittest.main()
