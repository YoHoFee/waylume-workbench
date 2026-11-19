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
import workflow


class IterationWorkflowTests(unittest.TestCase):
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

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_reopen_completed_task_in_unreleased_iteration(self):
        it = dashboardctl.create_iteration("v13.4.2", "13.4.2 迭代")["iteration"]
        task = dashboardctl.normalize_record({
            "id": "ZT-T-FLOW001",
            "type": "task",
            "title": "BGM选择弹窗需求",
            "status": "in_progress",
            "iteration_id": it["id"]
        })
        db = dashboardctl.load_store()
        db["records"].append(task)
        dashboardctl.atomic_write(db)

        # Start workflow
        dashboardctl.workflow_start("ZT-T-FLOW001", actor_role="lead", actor="lead-agent")
        # Advance through intake -> requirement -> development -> audit_response -> acceptance -> delivery
        dashboardctl.workflow_submit("ZT-T-FLOW001", {"scope": "全量", "decision_owner": "PM"}, actor_role="lead", actor="lead-agent")
        dashboardctl.workflow_advance("ZT-T-FLOW001", actor_role="lead", actor="lead-agent") # -> requirement
        dashboardctl.workflow_submit("ZT-T-FLOW001", {"requirement_brief": "需求完成", "acceptance_criteria": "口径对齐", "pm_decision": "通过"}, actor_role="pm", actor="pm-agent")
        dashboardctl.workflow_advance("ZT-T-FLOW001", actor_role="pm", actor="pm-agent") # -> development
        dashboardctl.workflow_submit("ZT-T-FLOW001", {"dev_summary": "开发完成", "branch": "local/bgm", "self_check": "自测通过"}, actor_role="dev", actor="dev-agent")
        dashboardctl.workflow_advance("ZT-T-FLOW001", actor_role="dev", actor="dev-agent") # -> audit_response
        dashboardctl.workflow_submit("ZT-T-FLOW001", {"audit_report": "静态审查通过"}, actor_role="auditor", actor="audit-agent")
        dashboardctl.workflow_submit("ZT-T-FLOW001", {"dev_response": "完全采纳"}, actor_role="dev", actor="dev-agent")
        dashboardctl.workflow_advance("ZT-T-FLOW001", actor_role="lead", actor="lead-agent") # -> acceptance
        dashboardctl.workflow_submit("ZT-T-FLOW001", {"pm_acceptance": "验收通过"}, actor_role="pm", actor="pm-agent")
        dashboardctl.workflow_advance("ZT-T-FLOW001", actor_role="pm", actor="pm-agent") # -> delivery
        dashboardctl.workflow_submit("ZT-T-FLOW001", {"delivery_evidence": "交付上线"}, actor_role="lead", actor="lead-agent")
        dashboardctl.workflow_close("ZT-T-FLOW001", actor_role="lead", actor="lead-agent") # completed!

        # Check status is completed
        rec = dashboardctl._find_record(dashboardctl.load_store(), "ZT-T-FLOW001")
        self.assertEqual(rec["status"], "completed")

        # Now, before iteration release, reopen for PM modification!
        reopened = dashboardctl.reopen_record(
            "ZT-T-FLOW001", "PM要求增加弹窗取消按钮文字链接样式",
            audit_mode="lightweight", actor="dev-agent", actor_role="lead"
        )["record"]

        self.assertEqual(reopened["status"], "developing")
        self.assertEqual(reopened["reopen_count"], 1)
        self.assertEqual(len(reopened["reopen_history"]), 1)
        self.assertEqual(reopened["reopen_history"][0]["round"], 1)
        self.assertEqual(reopened["reopen_history"][0]["audit_mode"], "lightweight")
        self.assertEqual(reopened["workflow"]["stage"], "development")
        self.assertEqual(reopened["workflow"]["stage_status"], "active")

        # In lightweight mode: Dev completes dev and then self-audit in audit_response
        dashboardctl.workflow_submit("ZT-T-FLOW001", {"dev_summary": "已加文字取消按钮", "branch": "local/bgm", "self_check": "通过"}, actor_role="dev", actor="dev-agent")
        dashboardctl.workflow_advance("ZT-T-FLOW001", actor_role="dev", actor="dev-agent") # -> audit_response

        # Dev can submit audit_report directly under lightweight mode
        dashboardctl.workflow_submit("ZT-T-FLOW001", {"audit_report": "轻量自审通过：仅微调按钮样式", "dev_response": "无问题"}, actor_role="dev", actor="dev-agent")
        dashboardctl.workflow_advance("ZT-T-FLOW001", actor_role="lead", actor="lead-agent") # -> acceptance

        dashboardctl.workflow_submit("ZT-T-FLOW001", {"pm_acceptance": "二次验收通过"}, actor_role="pm", actor="pm-agent")
        dashboardctl.workflow_advance("ZT-T-FLOW001", actor_role="pm", actor="pm-agent") # -> delivery
        dashboardctl.workflow_submit("ZT-T-FLOW001", {"delivery_evidence": "二次交付完毕"}, actor_role="lead", actor="lead-agent")
        dashboardctl.workflow_close("ZT-T-FLOW001", actor_role="lead", actor="lead-agent")

        rec2 = dashboardctl._find_record(dashboardctl.load_store(), "ZT-T-FLOW001")
        self.assertEqual(rec2["status"], "completed")
        self.assertEqual(rec2["reopen_count"], 1)

        # Release the iteration
        dashboardctl.release_iteration(it["id"])

        # Reopen on released iteration should be rejected
        with self.assertRaises(ValueError) as ctx:
            dashboardctl.reopen_record("ZT-T-FLOW001", "发布后再改")
        self.assertEqual(str(ctx.exception), "cannot_reopen_released_iteration_record")

    def test_reopen_api(self):
        old_port = dashboard_server.PORT
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
        port = server.server_port
        dashboard_server.PORT = port
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        try:
            it = dashboardctl.create_iteration("v13.4.2", "13.4.2 迭代")["iteration"]
            task = dashboardctl.normalize_record({
                "id": "ZT-T-API001",
                "type": "task",
                "title": "API重开测试",
                "status": "completed",
                "iteration_id": it["id"]
            })
            db = dashboardctl.load_store()
            db["records"].append(task)
            dashboardctl.atomic_write(db)

            conn = http.client.HTTPConnection("127.0.0.1", port)
            headers = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
            payload = json.dumps({"reason": "API触发重开", "audit_mode": "lightweight"}).encode("utf-8")
            conn.request("POST", "/api/items/ZT-T-API001/reopen", body=payload, headers=headers)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            res_data = json.loads(resp.read().decode("utf-8"))
            self.assertIn(res_data["record"]["status"], {"developing", "in_progress"})
            self.assertEqual(res_data["record"]["reopen_count"], 1)
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            dashboard_server.PORT = old_port


if __name__ == "__main__":
    unittest.main()
