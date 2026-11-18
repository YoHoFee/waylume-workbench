"""Dispatch-card contract: Lead must read it before delegating any role."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dashboardctl


class DispatchCardTests(unittest.TestCase):
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
        dashboardctl.atomic_write({"schema_version": 1, "revision": 1, "records": [{
            "id": "T-1", "type": "task", "title": "派发卡任务", "status": "pending_release", "history": []
        }]})

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def record(self):
        return dashboardctl.load_store()["records"][0]

    def bind_all(self):
        for role, actor, channel in (("dev", "host-dev", "host-dev-transport"),
                                     ("auditor", "host-auditor", "host-audit-transport"),
                                     ("pm", "host-pm", "host-pm-transport")):
            dashboardctl.bind_agent("T-1", role, actor, channel=channel)

    def test_unstarted_task_requires_lead_to_decide_entry_and_bindings(self):
        card = dashboardctl.dispatch_card("T-1")
        self.assertFalse(card["workflow_started"])
        self.assertEqual(card["next"]["action"], "start")
        self.assertIn("不得凭记忆", card["entry_note"])
        self.assertTrue(any("auditor" in notice for notice in card["notices"]))

    def test_audit_stage_comes_before_development_response(self):
        dashboardctl.workflow_start("T-1", template_id="dev-standard", stage="audit_response",
                                    actor_role="lead", actor="lead")
        self.bind_all()
        card = dashboardctl.dispatch_card("T-1")
        self.assertEqual(card["stage"], "audit_response")
        self.assertEqual(card["next"]["action"], "dispatch")
        self.assertEqual(card["next"]["role"], "auditor")
        self.assertEqual(card["next"]["channel"], "host-audit-transport")
        self.assertTrue(any("先派独立审计" in notice for notice in card["notices"]))

        dashboardctl.workflow_submit("T-1", {"audit_report": "ADVISORY: report"},
                                     actor_role="auditor", actor="dispatch-auditor")
        card = dashboardctl.dispatch_card("T-1")
        self.assertEqual(card["substage"], "response")
        self.assertEqual(card["next"]["role"], "dev")
        self.assertEqual(card["next"]["channel"], "host-dev-transport")

        dashboardctl.workflow_submit("T-1", {"dev_response": "accepted"},
                                     actor_role="dev", actor="gemini-work-assistant")
        card = dashboardctl.dispatch_card("T-1")
        self.assertEqual(card["next"]["action"], "advance")
        self.assertEqual(card["next"]["roles"], ["lead"])

    def test_delivery_is_manual_and_forbids_lead_close(self):
        dashboardctl.workflow_start("T-1", template_id="dev-standard", stage="delivery",
                                    actor_role="lead", actor="lead")
        card = dashboardctl.dispatch_card("T-1")
        self.assertEqual(card["next"]["action"], "manual_delivery")
        self.assertTrue(card["delivery_manual"])
        self.assertTrue(any("禁止 close" in notice for notice in card["notices"]))

    def test_bind_agent_records_channel_and_session(self):
        dashboardctl.bind_agent("T-1", "dev", "gemini-work-assistant",
                                session_id="0163a4ef-338c-420b-a962-2d27854b1b24",
                                channel="gemini-internal")
        binding = self.record()["agent_bindings"]["dev"]
        self.assertEqual(binding["channel"], "gemini-internal")
        self.assertEqual(binding["session_id"], "0163a4ef-338c-420b-a962-2d27854b1b24")


if __name__ == "__main__":
    unittest.main()
