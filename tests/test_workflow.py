"""Six-stage workflow pipeline tests (contract: docs/product/workflow-pipeline.md)."""

import contextlib
import io
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import backend
import dashboardctl
import server
import workflow


class WorkflowStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(dashboardctl, "ROOT", self.root),
            patch.object(dashboardctl, "STORE", self.root / "store.json"),
            patch.object(dashboardctl, "LOCK", self.root / ".store.lock"),
            patch.object(dashboardctl, "EVENTS", self.root / "events.jsonl"),
            patch.object(dashboardctl, "UPLOADS", self.root / "sources" / "uploads"),
            patch.object(server, "STORE", self.root / "store.json"),
            patch.object(server, "SCOPE_CATALOG", self.root / "scope-catalog.json"),
        ]
        for item in self.patches:
            item.start()
        self.set_records([self.make_record()])

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    @staticmethod
    def make_record(rid="T-1", **overrides):
        record = {"id": rid, "type": "task", "title": f"流水线任务 {rid}", "status": "pending", "history": []}
        record.update(overrides)
        return record

    def set_records(self, records):
        dashboardctl.atomic_write({"schema_version": 1, "revision": 1, "records": records, "sync": {}})

    def record(self, rid="T-1"):
        return next(item for item in dashboardctl.load_store()["records"] if item["id"] == rid)

    def revision(self, rid="T-1"):
        return self.record(rid)["revision"]

    def start(self, rid="T-1", **overrides):
        params = {"actor_role": "lead", "actor": "lead", "expected_revision": self.revision(rid)}
        params.update(overrides)
        return dashboardctl.workflow_start(rid, **params)

    def submit(self, outputs=None, rid="T-1", **overrides):
        params = {"actor_role": "lead", "actor": "lead", "expected_revision": self.revision(rid)}
        params.update(overrides)
        return dashboardctl.workflow_submit(rid, outputs if outputs is not None else {}, **params)

    def advance(self, rid="T-1", **overrides):
        params = {"actor_role": "lead", "actor": "lead", "expected_revision": self.revision(rid)}
        params.update(overrides)
        return dashboardctl.workflow_advance(rid, **params)

    def walk_to_development(self, rid="T-1"):
        self.start(rid)
        self.submit({"scope": "工作台", "decision_owner": "PM"}, rid=rid)
        self.advance(rid)
        self.submit({"requirement_brief": "简报", "acceptance_criteria": "标准", "pm_decision": "开始"},
                    actor_role="pm", actor="pm", rid=rid)
        self.advance(rid, actor_role="pm", actor="pm")
        return self.record(rid)

    def walk_to_delivery(self, rid="T-1"):
        self.walk_to_development(rid)
        self.submit({"dev_summary": "实现完成", "branch": "local/pipeline", "self_check": "自检通过"},
                    actor_role="dev", actor="gemini", rid=rid)
        self.advance(rid, actor_role="dev", actor="gemini")
        self.submit({"audit_report": "ADVISORY 报告"}, actor_role="auditor", actor="auditor", rid=rid)
        self.submit({"dev_response": "逐条回应"}, actor_role="dev", actor="gemini", rid=rid)
        self.advance(rid)
        self.submit({"pm_acceptance": "验收通过"}, actor_role="pm", actor="pm", rid=rid)
        self.advance(rid, actor_role="pm", actor="pm")
        return self.record(rid)


class WorkflowTemplateTests(WorkflowStoreTestCase):
    def test_templates_define_six_stages_roles_and_gates(self):
        templates = {item["id"]: item for item in workflow.templates()}
        self.assertEqual(set(templates), {"dev-standard", "bug-standard"})
        dev = templates["dev-standard"]
        self.assertEqual([stage["id"] for stage in dev["stages"]],
                         ["intake", "requirement", "development", "audit_response", "acceptance", "delivery"])
        self.assertEqual(dev["applies_to"], ["task", "work"])
        self.assertEqual(templates["bug-standard"]["applies_to"], ["bug"])
        self.assertEqual(dev["version"], 1)
        stages = {stage["id"]: stage for stage in dev["stages"]}
        self.assertEqual(stages["intake"]["required_outputs"], ["scope", "decision_owner"])
        self.assertEqual(stages["development"]["required_outputs"], ["dev_summary", "branch", "self_check"])
        self.assertEqual(stages["audit_response"]["required_outputs"], ["audit_report", "dev_response"])
        self.assertEqual([item["id"] for item in stages["audit_response"]["substeps"]], ["audit", "response"])
        self.assertEqual([item["role"] for item in stages["audit_response"]["substeps"]], ["auditor", "dev"])
        self.assertEqual([item["id"] for item in stages["requirement"]["substeps"]], ["understanding", "pm_decision"])
        self.assertEqual(stages["audit_response"]["substeps"][0]["output"], "audit_report")
        self.assertEqual(stages["requirement"]["substeps"][1]["output"], "pm_decision")
        self.assertEqual(stages["audit_response"]["advance_roles"], ["lead"])
        self.assertEqual(stages["acceptance"]["rework_to"], ["development"])
        self.assertEqual(templates["bug-standard"]["stages"][2]["label"], "BUG 修复")

    def test_unknown_templates_are_reported_with_distinct_read_and_write_codes(self):
        with self.assertRaises(workflow.WorkflowError) as read_error:
            workflow.get_template("missing")
        self.assertEqual(read_error.exception.code, "template_not_found")
        with self.assertRaises(workflow.WorkflowError) as write_error:
            workflow.resolve_template("bug-standard", "task")
        self.assertEqual(write_error.exception.code, "invalid_template")
        self.assertEqual(workflow.default_template_id("bug"), "bug-standard")
        self.assertEqual(workflow.default_template_id("task"), "dev-standard")


class WorkflowStartTests(WorkflowStoreTestCase):
    def test_start_writes_summary_fields_and_history(self):
        revision = self.revision()
        result = self.start(note="启动六阶段")
        record = result["record"]
        self.assertEqual(result["revision"], revision + 1)
        self.assertEqual(result["workflow"]["template_id"], "dev-standard")
        self.assertEqual(result["template"]["id"], "dev-standard")
        self.assertEqual(result["workflow"]["template_version"], 1)
        self.assertEqual(result["workflow"]["stage"], "intake")
        self.assertEqual(result["workflow"]["stage_status"], "active")
        self.assertEqual(record["workflow_stage"], "intake")
        self.assertEqual(record["workflow_stage_status"], "active")
        self.assertEqual(record["workflow_template_id"], "dev-standard")
        self.assertEqual(record["status"], "pending")
        self.assertEqual(result["workflow"]["history"][0]["action"], "start")
        self.assertEqual(result["workflow"]["history"][0]["role"], "lead")
        self.assertEqual(result["workflow"]["gates"]["intake"]["status"], "active")
        saved = self.record()
        self.assertEqual(saved["workflow"]["stage"], "intake")
        events = (self.root / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn("workflow_start", events)

    def test_start_requires_a_lead_and_rejects_a_second_start(self):
        with self.assertRaises(workflow.WorkflowError) as error:
            self.start(actor_role="dev", actor="gemini")
        self.assertEqual(error.exception.code, "invalid_role")
        self.assertNotIn("workflow", self.record())
        self.start()
        with self.assertRaises(workflow.WorkflowError) as second:
            self.start()
        self.assertEqual(second.exception.code, "workflow_already_started")

    def test_start_maps_legacy_statuses_to_stages(self):
        cases = [
            ("T-ACC", "awaiting_acceptance", "acceptance", "active", "awaiting_acceptance"),
            ("T-DEV", "developing", "development", "active", "developing"),
            ("T-BLK", "blocked", "intake", "blocked", "blocked"),
            ("T-DONE", "completed", "delivery", "done", "completed"),
        ]
        self.set_records([self.make_record(rid, status=status) for rid, status, *_ in cases])
        for rid, status, stage, stage_status, expected_status in cases:
            with self.subTest(status=status):
                result = self.start(rid)
                self.assertEqual(result["workflow"]["stage"], stage)
                self.assertEqual(result["workflow"]["stage_status"], stage_status)
                self.assertEqual(result["record"]["status"], expected_status)

    def test_start_rejects_terminal_and_stage_override_on_completed(self):
        self.set_records([self.make_record("T-CAN", status="cancelled"), self.make_record("T-DONE2", status="completed")])
        with self.assertRaises(workflow.WorkflowError) as cancelled:
            self.start("T-CAN")
        self.assertEqual(cancelled.exception.code, "workflow_terminal")
        with self.assertRaises(workflow.WorkflowError) as override:
            self.start("T-DONE2", stage="development")
        self.assertEqual(override.exception.code, "invalid_transition")

    def test_start_respects_an_explicit_stage_for_legacy_records(self):
        self.set_records([self.make_record("T-OLD", status="in_progress")])
        result = self.start("T-OLD", stage="development")
        self.assertEqual(result["workflow"]["stage"], "development")
        self.assertEqual(result["record"]["status"], "developing")


class WorkflowTransitionTests(WorkflowStoreTestCase):
    def test_submit_and_advance_enforce_roles_and_required_outputs(self):
        self.start()
        with self.assertRaises(workflow.WorkflowError) as missing:
            self.advance()
        self.assertEqual(missing.exception.code, "missing_outputs")
        self.assertEqual(set(missing.exception.detail["missing"]), {"scope", "decision_owner"})
        with self.assertRaises(workflow.WorkflowError) as role:
            self.submit({"scope": "越权"}, actor_role="dev", actor="gemini")
        self.assertEqual(role.exception.code, "invalid_role")
        self.assertEqual(self.record()["workflow"]["gates"]["intake"].get("outputs"), {})
        self.submit({"scope": "工作台"}, note="范围确认", evidence=["REQ-1"])
        with self.assertRaises(workflow.WorkflowError) as partial:
            self.advance()
        self.assertEqual(partial.exception.code, "missing_outputs")
        self.assertEqual(partial.exception.detail["missing"], ["decision_owner"])
        self.submit({"decision_owner": "PM"})
        result = self.advance(note="进入需求", evidence=["scope"])
        workflow_state = result["workflow"]
        self.assertEqual(workflow_state["stage"], "requirement")
        self.assertEqual(workflow_state["stage_status"], "active")
        self.assertEqual(workflow_state["gates"]["intake"]["status"], "done")
        self.assertEqual(workflow_state["gates"]["intake"]["outputs"]["scope"], "工作台")
        self.assertEqual(workflow_state["substage"], "understanding")
        self.assertEqual(result["record"]["status"], "in_progress")
        self.assertEqual([entry["action"] for entry in workflow_state["history"]],
                         ["start", "claim", "submit", "submit", "advance_release", "advance"])

    def test_audit_response_substeps_track_audit_then_development_response(self):
        self.walk_to_development()
        self.submit({"dev_summary": "实现", "branch": "local/x", "self_check": "ok"},
                    actor_role="dev", actor="gemini")
        result = self.advance(actor_role="dev", actor="gemini")
        self.assertEqual(result["workflow"]["stage"], "audit_response")
        self.assertEqual(result["workflow"]["substage"], "audit")
        self.assertEqual(result["record"]["status"], "in_progress")
        with self.assertRaises(workflow.WorkflowError) as wrong_substep_role:
            self.submit({"dev_response": "越权回应"}, actor_role="auditor", actor="auditor")
        self.assertEqual(wrong_substep_role.exception.code, "invalid_role")
        result = self.submit({"audit_report": "静态审计报告"}, actor_role="auditor", actor="auditor")
        self.assertEqual(result["workflow"]["substage"], "response")
        with self.assertRaises(workflow.WorkflowError) as missing:
            self.advance()
        self.assertEqual(missing.exception.code, "missing_outputs")
        self.assertEqual(missing.exception.detail["missing"], ["dev_response"])
        result = self.submit({"dev_response": "逐条回应"}, actor_role="dev", actor="gemini")
        self.assertIsNone(result["workflow"]["substage"])

    def test_full_pipeline_closes_from_delivery(self):
        self.start()
        self.submit({"scope": "s", "decision_owner": "d"})
        self.advance()
        self.submit({"requirement_brief": "b", "acceptance_criteria": "a", "pm_decision": "go"},
                    actor_role="pm", actor="pm")
        self.advance(actor_role="pm", actor="pm")
        self.submit({"dev_summary": "s", "branch": "b", "self_check": "c"}, actor_role="dev", actor="gemini")
        self.advance(actor_role="dev", actor="gemini")
        self.submit({"audit_report": "r"}, actor_role="auditor", actor="auditor")
        self.submit({"dev_response": "d"}, actor_role="dev", actor="gemini")
        self.advance()
        self.submit({"pm_acceptance": "pass"}, actor_role="pm", actor="pm")
        result = self.advance(actor_role="pm", actor="pm")
        self.assertEqual(result["workflow"]["stage"], "delivery")
        self.assertEqual(result["record"]["status"], "in_progress")
        with self.assertRaises(workflow.WorkflowError) as too_early:
            self.advance()
        self.assertEqual(too_early.exception.code, "invalid_transition")
        with self.assertRaises(workflow.WorkflowError) as wrong_role:
            dashboardctl.workflow_close("T-1", actor_role="dev", actor="gemini",
                                        expected_revision=self.revision())
        self.assertEqual(wrong_role.exception.code, "invalid_role")
        with self.assertRaises(workflow.WorkflowError) as missing_delivery:
            dashboardctl.workflow_close("T-1", actor_role="lead", actor="lead",
                                        expected_revision=self.revision())
        self.assertEqual(missing_delivery.exception.code, "missing_outputs")
        self.assertEqual(missing_delivery.exception.detail["missing"], ["delivery_evidence"])
        self.submit({"delivery_evidence": "release-note"}, actor_role="lead", actor="lead")
        result = dashboardctl.workflow_close("T-1", actor_role="lead", actor="lead", note="交付完成",
                                             evidence=["release-note"], expected_revision=self.revision())
        self.assertEqual(result["record"]["status"], "completed")
        self.assertEqual(result["workflow"]["stage_status"], "done")
        self.assertEqual(result["workflow"]["gates"]["delivery"]["status"], "done")
        self.assertEqual(result["workflow"]["history"][-1]["action"], "close")
        self.assertEqual(result["record"]["history"][-1]["to_status"], "completed")
        with self.assertRaises(workflow.WorkflowError) as terminal:
            self.submit({"scope": "again"})
        self.assertEqual(terminal.exception.code, "workflow_terminal")

    def test_close_requires_the_delivery_stage(self):
        self.walk_to_development()
        with self.assertRaises(workflow.WorkflowError) as error:
            dashboardctl.workflow_close("T-1", actor_role="lead", actor="lead", expected_revision=self.revision())
        self.assertEqual(error.exception.code, "invalid_stage")

    def test_rework_resets_the_target_gate_and_counts_the_rework(self):
        self.walk_to_delivery()
        self.assertEqual(self.record()["workflow"]["stage"], "delivery")
        result = dashboardctl.workflow_rework("T-1", "acceptance", actor_role="lead", actor="lead",
                                              note="验收结论不成立", expected_revision=self.revision())
        self.assertEqual(result["workflow"]["stage"], "acceptance")
        self.assertEqual(result["workflow"]["stage_status"], "rework")
        self.assertEqual(result["workflow"]["rework_count"], 1)
        self.assertEqual(result["record"]["status"], "awaiting_acceptance")
        self.assertEqual(result["record"]["workflow_stage_status"], "rework")
        self.assertEqual(result["workflow"]["gates"]["acceptance"]["outputs"], {})
        self.assertEqual(result["workflow"]["gates"]["acceptance"]["superseded"][0]["outputs"],
                         {"pm_acceptance": "验收通过"})
        self.assertEqual(result["workflow"]["history"][-1]["action"], "rework")
        with self.assertRaises(workflow.WorkflowError) as target:
            dashboardctl.workflow_rework("T-1", "intake", actor_role="pm", actor="pm",
                                         expected_revision=self.revision())
        self.assertEqual(target.exception.code, "invalid_transition")
        with self.assertRaises(workflow.WorkflowError) as unknown:
            dashboardctl.workflow_rework("T-1", "nope", actor_role="pm", actor="pm",
                                         expected_revision=self.revision())
        self.assertEqual(unknown.exception.code, "invalid_stage")
        with self.assertRaises(workflow.WorkflowError) as role:
            dashboardctl.workflow_rework("T-1", "development", actor_role="dev", actor="gemini",
                                         expected_revision=self.revision())
        self.assertEqual(role.exception.code, "invalid_role")
        result = dashboardctl.workflow_rework("T-1", "development", actor_role="pm", actor="pm",
                                              note="回归不通过", expected_revision=self.revision())
        self.assertEqual(result["workflow"]["stage"], "development")
        self.assertEqual(result["workflow"]["stage_status"], "rework")
        self.assertEqual(server.build_payload()["delegated"][0]["workflow_stage_status"], "rework")
        self.assertEqual(result["workflow"]["rework_count"], 2)
        self.assertEqual(result["record"]["status"], "developing")
        self.assertEqual(result["workflow"]["gates"]["development"]["superseded"][0]["outputs"]["dev_summary"], "实现完成")
        self.assertEqual(result["workflow"]["gates"]["development"]["outputs"], {})
        dashboardctl.workflow_block("T-1", actor_role="pm", actor="pm", note="暂停",
                                    expected_revision=self.revision())
        result = dashboardctl.workflow_unblock("T-1", actor_role="pm", actor="pm", note="继续",
                                               expected_revision=self.revision())
        self.assertEqual(result["workflow"]["stage_status"], "rework")

    def test_block_is_a_side_path_and_unblock_restores_the_mapped_status(self):
        self.walk_to_development()
        result = dashboardctl.workflow_block("T-1", actor_role="lead", actor="lead", note="等待真机",
                                             expected_revision=self.revision())
        self.assertEqual(result["record"]["status"], "blocked")
        self.assertEqual(result["workflow"]["stage"], "development")
        self.assertEqual(result["workflow"]["stage_status"], "blocked")
        self.assertEqual(result["workflow"]["blocked_from_stage"], "development")
        self.assertEqual(result["workflow"]["gates"]["development"]["status"], "blocked")
        with self.assertRaises(workflow.WorkflowError) as blocked:
            self.submit({"dev_summary": "x"}, actor_role="dev", actor="gemini")
        self.assertEqual(blocked.exception.code, "workflow_blocked")
        with self.assertRaises(workflow.WorkflowError) as blocked_advance:
            self.advance()
        self.assertEqual(blocked_advance.exception.code, "workflow_blocked")
        result = dashboardctl.workflow_unblock("T-1", actor_role="lead", actor="lead", note="真机可用",
                                               expected_revision=self.revision())
        self.assertEqual(result["record"]["status"], "developing")
        self.assertEqual(result["workflow"]["stage_status"], "active")
        self.assertIsNone(result["workflow"]["blocked_from_stage"])
        self.assertEqual(result["workflow"]["gates"]["development"]["status"], "active")
        self.assertEqual([entry["action"] for entry in result["workflow"]["history"][-2:]], ["block", "unblock"])
        with self.assertRaises(workflow.WorkflowError) as not_blocked:
            dashboardctl.workflow_unblock("T-1", actor_role="lead", actor="lead",
                                          expected_revision=self.revision())
        self.assertEqual(not_blocked.exception.code, "invalid_transition")

    def test_block_requires_a_reason_and_a_known_role(self):
        self.walk_to_development()
        with self.assertRaises(workflow.WorkflowError) as missing_reason:
            dashboardctl.workflow_block("T-1", actor_role="lead", actor="lead", note="  ",
                                        expected_revision=self.revision())
        self.assertEqual(missing_reason.exception.code, "note_required")
        with self.assertRaises(workflow.WorkflowError) as unknown_role:
            dashboardctl.workflow_block("T-1", actor_role="hacker", actor="x", note="原因",
                                        expected_revision=self.revision())
        self.assertEqual(unknown_role.exception.code, "invalid_role")
        self.assertEqual(self.record()["status"], "developing")

    def test_every_write_is_compare_and_swap_on_the_record_revision(self):
        self.start()
        stale = self.revision() - 1
        with self.assertRaises(workflow.WorkflowError) as conflict:
            self.submit({"scope": "x"}, expected_revision=stale)
        self.assertEqual(conflict.exception.code, "revision_conflict")
        self.assertEqual(self.record()["workflow"]["gates"]["intake"].get("outputs"), {})
        result = self.submit({"scope": "x"}, expected_revision=self.revision())
        self.assertEqual(result["revision"], self.revision())
        # Omitting the expectation means "current revision" and still succeeds.
        result = self.submit({"decision_owner": "PM"}, expected_revision=None)
        self.assertEqual(result["workflow"]["gates"]["intake"]["outputs"]["decision_owner"], "PM")

    def test_submit_coerces_scalar_outputs_and_rejects_structured_values(self):
        self.start()
        result = self.submit({"scope": 42, "decision_owner": True})
        outputs = result["workflow"]["gates"]["intake"]["outputs"]
        self.assertEqual(outputs, {"scope": "42", "decision_owner": "True"})
        with self.assertRaises(workflow.WorkflowError) as invalid:
            self.submit({"scope": {"nested": "x"}})
        self.assertEqual(invalid.exception.code, "invalid_outputs")


class WorkflowLockTests(WorkflowStoreTestCase):
    def test_claim_blocks_other_actors_and_exposes_the_lock(self):
        self.walk_to_development()
        claimed = dashboardctl.workflow_claim("T-1", actor_role="dev", actor="gemini",
                                               expected_revision=self.revision())
        lock = claimed["workflow"]["lock"]
        self.assertEqual(lock["actor"], "gemini")
        self.assertEqual(lock["role"], "dev")
        self.assertEqual(lock["stage"], "development")
        self.assertTrue(lock["expires_at"])
        self.assertEqual(dashboardctl.effective_work_status(claimed["record"])[0], "developing")
        self.assertEqual(dashboardctl.effective_work_status(claimed["record"])[1], "workflow_lock")

        with self.assertRaises(workflow.WorkflowError) as conflict:
            dashboardctl.workflow_claim("T-1", actor_role="dev", actor="claude",
                                        expected_revision=self.revision())
        self.assertEqual(conflict.exception.code, "stage_locked")
        self.assertEqual(conflict.exception.detail["lock"]["actor"], "gemini")

        with self.assertRaises(workflow.WorkflowError) as advance_conflict:
            dashboardctl.workflow_advance("T-1", actor_role="lead", actor="lead",
                                          expected_revision=self.revision())
        self.assertEqual(advance_conflict.exception.code, "stage_locked")

        heartbeat = dashboardctl.workflow_heartbeat("T-1", actor_role="dev", actor="gemini",
                                                    expected_revision=self.revision())
        self.assertGreaterEqual(heartbeat["workflow"]["lock"]["expires_at"], claimed["workflow"]["lock"]["expires_at"])

        released = dashboardctl.workflow_release("T-1", actor_role="lead", actor="lead",
                                                 expected_revision=self.revision())
        self.assertNotIn("lock", released["workflow"])
        self.assertEqual(dashboardctl.effective_work_status(released["record"])[0], "developing")

    def test_submit_auto_claims_and_releases_completed_substeps(self):
        self.walk_to_development()
        submitted = self.submit({"dev_summary": "实现", "branch": "local/x", "self_check": "ok"},
                                actor_role="dev", actor="gemini")
        self.assertEqual(submitted["workflow"]["lock"]["actor"], "gemini")
        self.assertEqual(dashboardctl.effective_work_status(submitted["record"])[0], "developing")

        self.advance(actor_role="dev", actor="gemini")
        audit = self.submit({"audit_report": "ADVISORY 报告"}, actor_role="auditor", actor="auditor")
        self.assertNotIn("lock", audit["workflow"])
        response = self.submit({"dev_response": "逐条回应"}, actor_role="dev", actor="gemini")
        self.assertNotIn("lock", response["workflow"])

class WorkflowCompatibilityTests(WorkflowStoreTestCase):
    def test_old_records_have_no_workflow_and_no_summary(self):
        detail = dashboardctl.workflow_show("T-1")
        self.assertIsNone(detail["workflow"])
        self.assertIsNone(detail["template"])
        self.assertEqual(detail["record_revision"], self.revision())
        payload_item = server.build_payload()["delegated"][0]
        self.assertIsNone(payload_item["workflow_template_id"])
        self.assertIsNone(payload_item["workflow_stage"])
        self.assertEqual(payload_item["workflow_stage_index"], 0)
        self.assertIsNone(payload_item.get("workflow"))
        saved = self.record()
        self.assertNotIn("workflow", saved)
        self.assertNotIn("workflow_stage", saved)

    def test_dashboard_payload_has_summary_without_history_and_detail_has_full_history(self):
        self.walk_to_development()
        payload_item = server.build_payload()["delegated"][0]
        self.assertEqual(payload_item["workflow_template_id"], "dev-standard")
        self.assertEqual(payload_item["workflow_stage"], "development")
        self.assertEqual(payload_item["workflow_stage_status"], "active")
        self.assertEqual(payload_item["workflow_stage_index"], 3)
        self.assertEqual(payload_item["workflow_stage_label"], "开发")
        self.assertEqual(payload_item["workflow_stage_short"], "开发")
        self.assertEqual(payload_item["workflow_rework_count"], 0)
        self.assertNotIn("history", payload_item["workflow"])
        self.assertGreater(payload_item["workflow"]["history_count"], 0)
        detail = dashboardctl.workflow_show("T-1")
        self.assertEqual(len(detail["workflow"]["history"]), payload_item["workflow"]["history_count"])
        self.assertEqual(detail["template"]["id"], "dev-standard")

    def test_backend_context_contains_the_pipeline_summary(self):
        self.walk_to_development()
        self.submit({"dev_summary": "部分完成"}, actor_role="dev", actor="gemini")
        context = backend.read_context(self.root, "task", "T-1")
        self.assertIn("## 流水线", context)
        self.assertIn("当前阶段", context)
        self.assertIn("开发", context)
        self.assertIn("下一步", context)
        self.assertIn("静态审计与开发回应", context)
        self.assertIn("阶段输出", context)
        self.assertIn("自检⬜", context)
        self.assertNotIn('"workflow"', context)

    def test_transitions_require_a_started_workflow(self):
        with self.assertRaises(workflow.WorkflowError) as error:
            dashboardctl.workflow_submit("T-1", {"scope": "x"}, actor_role="lead", actor="lead",
                                         expected_revision=self.revision())
        self.assertEqual(error.exception.code, "workflow_not_started")
        self.assertNotIn("workflow", self.record())

    def test_bug_defaults_to_the_bug_standard_template(self):
        self.set_records([self.make_record("BUG-1", type="bug", status="in_progress")])
        result = self.start("BUG-1")
        self.assertEqual(result["workflow"]["template_id"], "bug-standard")
        self.assertEqual(result["workflow"]["stage"], "intake")
        with self.assertRaises(workflow.WorkflowError) as mismatch:
            self.start("BUG-1", template_id="dev-standard")
        self.assertEqual(mismatch.exception.code, "workflow_already_started")


class WorkflowHttpTests(WorkflowStoreTestCase):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.old_port = server.PORT
        server.PORT = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join()
        self.httpd.server_close()
        server.PORT = self.old_port
        super().tearDown()

    def request(self, method, path, payload=None):
        body = json.dumps(payload or {}).encode()
        request = Request(self.origin + path, data=body, method=method,
                          headers={"Content-Type": "application/json", "Origin": self.origin})
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def test_templates_and_detail_endpoints(self):
        status, payload = self.request("GET", "/api/workflow/templates")
        self.assertEqual(status, 200)
        self.assertEqual({item["id"] for item in payload["templates"]}, {"dev-standard", "bug-standard"})
        status, template = self.request("GET", "/api/workflow/templates/dev-standard")
        self.assertEqual((status, template["id"]), (200, "dev-standard"))
        status, missing = self.request("GET", "/api/workflow/templates/nope")
        self.assertEqual((status, missing["error"]), (404, "template_not_found"))
        status, detail = self.request("GET", "/api/items/T-1/workflow")
        self.assertEqual(status, 200)
        self.assertIsNone(detail["workflow"])
        self.assertIsNone(detail["template"])
        status, missing_record = self.request("GET", "/api/items/NOPE/workflow")
        self.assertEqual((status, missing_record["error"]), (404, "record_not_found"))

    def test_lock_endpoints_claim_heartbeat_and_release(self):
        revision = self.revision()
        _, started = self.request("POST", "/api/items/T-1/workflow/start",
                                  {"expected_revision": revision, "actor_role": "lead", "actor": "lead"})
        status, claimed = self.request("POST", "/api/items/T-1/workflow/claim",
                                       {"expected_revision": started["revision"], "actor_role": "lead", "actor": "lead"})
        self.assertEqual(status, 200)
        self.assertEqual(claimed["workflow"]["lock"]["actor"], "lead")
        status, heartbeat = self.request("POST", "/api/items/T-1/workflow/heartbeat",
                                         {"expected_revision": claimed["revision"], "actor_role": "lead", "actor": "lead"})
        self.assertEqual(status, 200)
        self.assertEqual(heartbeat["workflow"]["lock"]["stage"], "intake")
        status, released = self.request("POST", "/api/items/T-1/workflow/release",
                                        {"expected_revision": heartbeat["revision"], "actor_role": "lead", "actor": "lead"})
        self.assertEqual(status, 200)
        self.assertNotIn("lock", released["workflow"])

    def test_write_endpoints_cover_start_submit_advance_and_conflicts(self):
        revision = self.revision()
        status, started = self.request("POST", "/api/items/T-1/workflow/start",
                                       {"expected_revision": revision, "actor_role": "lead",
                                        "actor": "lead", "note": "启动"})
        self.assertEqual((status, started["workflow"]["stage"]), (200, "intake"))
        status, restarted = self.request("POST", "/api/items/T-1/workflow/start",
                                         {"expected_revision": started["revision"], "actor_role": "lead", "actor": "lead"})
        self.assertEqual((status, restarted["error"]), (400, "workflow_already_started"))
        status, stale = self.request("POST", "/api/items/T-1/workflow/advance",
                                     {"expected_revision": revision, "actor_role": "lead", "actor": "lead"})
        self.assertEqual((status, stale["error"]), (409, "revision_conflict"))
        status, missing = self.request("POST", "/api/items/T-1/workflow/advance",
                                       {"expected_revision": started["revision"], "actor_role": "lead", "actor": "lead"})
        self.assertEqual((status, missing["error"]), (400, "missing_outputs"))
        self.assertEqual(set(missing["detail"]["missing"]), {"scope", "decision_owner"})
        status, wrong_role = self.request("POST", "/api/items/T-1/workflow/submit",
                                          {"expected_revision": started["revision"], "actor_role": "dev",
                                           "actor": "gemini", "outputs": {"scope": "x"}})
        self.assertEqual((status, wrong_role["error"]), (400, "invalid_role"))
        status, submitted = self.request("POST", "/api/items/T-1/workflow/submit",
                                         {"expected_revision": started["revision"], "actor_role": "lead",
                                          "actor": "lead", "outputs": {"scope": "工作台", "decision_owner": "PM"}})
        self.assertEqual(status, 200)
        status, advanced = self.request("POST", "/api/items/T-1/workflow/advance",
                                        {"expected_revision": submitted["revision"], "actor_role": "lead",
                                         "actor": "lead", "note": "进入需求"})
        self.assertEqual((status, advanced["workflow"]["stage"]), (200, "requirement"))
        status, unsupported = self.request("POST", "/api/items/T-1/workflow/advance",
                                           {"expected_revision": advanced["revision"], "actor_role": "lead",
                                            "actor": "lead", "unknown_field": True})
        self.assertEqual((status, unsupported["error"]), (400, "unsupported_fields"))
        status, missing_record = self.request("POST", "/api/items/NOPE/workflow/start",
                                              {"expected_revision": 0, "actor_role": "lead", "actor": "lead"})
        self.assertEqual((status, missing_record["error"]), (404, "record_not_found"))
        status, dashboard = self.request("GET", "/api/dashboard")
        item = dashboard["delegated"][0]
        self.assertEqual(item["workflow_stage"], "requirement")
        self.assertNotIn("history", item["workflow"])
        status, detail = self.request("GET", "/api/items/T-1/workflow")
        self.assertEqual(status, 200)
        self.assertEqual(detail["workflow"]["stage"], "requirement")
        self.assertEqual(detail["template"]["id"], "dev-standard")


class WorkflowCliTests(WorkflowStoreTestCase):
    def run_cli(self, *argv):
        output = io.StringIO()
        with patch("sys.argv", ["dashboardctl.py", "workflow", *argv]):
            with contextlib.redirect_stdout(output):
                dashboardctl.main()
        return json.loads(output.getvalue())

    def test_cli_templates_show_start_submit_and_advance(self):
        templates = self.run_cli("templates")
        self.assertEqual({item["id"] for item in templates["templates"]}, {"dev-standard", "bug-standard"})
        started = self.run_cli("start", "T-1", "--template", "dev-standard", "--actor-role", "lead",
                               "--actor", "lead", "--note", "启动", "--expected-revision", str(self.revision()))
        self.assertEqual(started["workflow"]["stage"], "intake")
        detail = self.run_cli("show", "T-1")
        self.assertEqual(detail["template"]["id"], "dev-standard")
        self.assertEqual(detail["workflow"]["history"][0]["action"], "start")
        submitted = self.run_cli("submit", "T-1", "--output", "scope=工作台",
                                 "--output", "decision_owner=PM", "--actor-role", "lead", "--actor", "lead",
                                 "--expected-revision", str(self.revision()))
        self.assertEqual(submitted["workflow"]["gates"]["intake"]["outputs"]["scope"], "工作台")
        advanced = self.run_cli("advance", "T-1", "--actor-role", "lead", "--actor", "lead",
                                "--note", "进入需求", "--expected-revision", str(self.revision()))
        self.assertEqual(advanced["workflow"]["stage"], "requirement")

    def test_cli_rejects_malformed_outputs_and_denied_roles(self):
        self.run_cli("start", "T-1", "--actor-role", "lead", "--actor", "lead")
        with self.assertRaises(SystemExit) as malformed:
            self.run_cli("submit", "T-1", "--output", "no-equals", "--actor-role", "lead", "--actor", "lead")
        self.assertIn("invalid_outputs", str(malformed.exception))
        with self.assertRaises(SystemExit) as denied:
            self.run_cli("advance", "T-1", "--actor-role", "dev", "--actor", "gemini")
        self.assertIn("invalid_role", str(denied.exception))


if __name__ == "__main__":
    unittest.main()
