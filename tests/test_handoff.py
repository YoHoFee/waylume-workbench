import copy
import unittest

import dashboardctl
from handoff_view import handoff_view


class HandoffTests(unittest.TestCase):
    def test_projection_preserves_input_and_does_not_infer_user_pause(self):
        record = {"id": "T-1", "type": "task", "title": "任务", "status": "in_progress",
                  "agent_state": "paused", "initial_brief": "初始目标", "latest_brief": "已分析",
                  "next_step": "核对证据", "platform_status": "分析", "observed_at": "2026-11-04T10:00:00+08:00"}
        before = copy.deepcopy(record)
        view = handoff_view(record)
        self.assertEqual(record, before)
        self.assertEqual(view["fields"]["goal"], "初始目标")
        self.assertEqual(view["fields"]["next_step"], "核对证据")
        self.assertIsNone(view["fields"]["resume_condition"])
        self.assertEqual(view["platform_snapshot"]["status"], "分析")
        self.assertEqual(view["evidence_availability"], "not_checked")

    def test_explicit_handoff_and_validation(self):
        record = {"id": "T-1", "type": "task", "title": "任务", "status": "partial",
                  "initial_brief": "旧目标", "handoff": {"goal": "确认目标", "verified": "仅静态检查", "unverified": "未真机",
                  "updated_at": "2026-11-04T10:00:00+08:00"}}
        normalized = dashboardctl.normalize_record(record)
        self.assertEqual(handoff_view(normalized)["fields"]["goal"], "确认目标")
        self.assertEqual(handoff_view(normalized)["fields"]["unverified"], "未真机")
        with self.assertRaises(ValueError):
            dashboardctl.normalize_record({**record, "handoff": {"unknown": "x"}})
        with self.assertRaises(ValueError):
            dashboardctl.normalize_record({**record, "handoff": {"updated_at": "2026-11-04"}})

    def test_summary_time_comes_only_from_matching_progress(self):
        record = {"latest_brief": "已完成检查", "updated_at": "new", "progress": [
            {"message": "已完成检查", "at": "original"}, {"message": "其他内容", "at": "later"}]}
        self.assertEqual(handoff_view(record)["summary_updated_at"], "original")
        record["progress"] = []
        self.assertIsNone(handoff_view(record)["summary_updated_at"])

    def test_brief_requires_explicit_progress_section(self):
        self.assertEqual(dashboardctl._brief_from_context("# 标题\n\n## 最新进展\n- 已完成检查\n## 下一步\n继续"), "已完成检查")
        self.assertEqual(dashboardctl._brief_from_context("# Task\n## Current state\n- Conclusion: Tested successfully\n- Next action: Review"), "Tested successfully")
        for context in ("", "# 标题", "# 标题\n## 下一步\n继续", "## 最新进展\n## 下一步\n继续", "## Current state\n- Conclusion:\n- Next action: Review"):
            with self.assertRaisesRegex(ValueError, "message_required"):
                dashboardctl._brief_from_context(context)


if __name__ == "__main__":
    unittest.main()
