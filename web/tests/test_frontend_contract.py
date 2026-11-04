# Created by Codex on 2026/10/22.
# Copyright © 2026 CMIC. All rights reserved.

import unittest
from pathlib import Path


WEB = Path(__file__).resolve().parents[1]
SCRIPT = (WEB / "assets/dashboard.js").read_text(encoding="utf-8")
MODEL = (WEB / "assets/dashboard-model.js").read_text(encoding="utf-8")
STYLE = (WEB / "assets/dashboard.css").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
FIXTURE = (WEB / "tests/ui_fixture_server.py").read_text(encoding="utf-8")


class DashboardFrontendContractTests(unittest.TestCase):
    def test_brief_is_rendered_as_text_and_completion_has_green_state(self):
        self.assertIn('summary.textContent = record.latest_brief || cardBrief(record)', SCRIPT)
        self.assertIn('record.transfer_disposition === "transfer_pending" ? "待转交"', SCRIPT)
        self.assertIn('record.transfer_disposition === "transferred" ? "已转交"', SCRIPT)
        self.assertRegex(STYLE, r"\.record-card\.record-completed \.record-title[^}]*color:#397859")
        self.assertIn('.record-card.transfer-pending .record-title', STYLE)
        self.assertIn('submitTransfer(transfer)', SCRIPT)
        self.assertIn('/transfer`, {}', SCRIPT)

    def test_agentless_work_is_shown_as_neutral_on_hold(self):
        self.assertIn('on_hold: "搁置"', SCRIPT)
        self.assertIn('activity: "Agent活动状态"', SCRIPT)
        self.assertIn('statusCode === "in_progress" || statusCode === "on_hold"', SCRIPT)
        self.assertIn('.status-pill.on_hold{border-color:#c7ccd3;background:#eef0f2;color:#626b76}', STYLE)

    def test_completed_and_cancelled_work_and_bugs_are_grouped_by_beijing_date(self):
        self.assertIn('timeZone: "Asia/Shanghai"', MODEL)
        self.assertIn('record.completed_at', MODEL)
        self.assertIn('todayRecords', MODEL)
        self.assertIn('...completion.todayRecords', SCRIPT)
        self.assertIn('allCancelled ? "取消日期" : allCompleted ? "完成日期" : "终结日期"', SCRIPT)
        self.assertIn('`${dateKind}未知 · ${rows.length}`', SCRIPT)

    def test_cancelled_status_is_localized_green_and_terminal_on_card_and_detail(self):
        self.assertIn('cancelled: "已取消"', SCRIPT)
        self.assertIn('function isCancelled(record)', SCRIPT)
        self.assertIn('cancelled ? "已取消"', SCRIPT)
        self.assertIn('record-cancelled-title', SCRIPT)
        self.assertIn('.record-card.record-cancelled .record-title', STYLE)
        self.assertIn('.record-card.record-cancelled .status-pill', STYLE)
        self.assertIn('20261028-cancelled-terminal-1', HTML)

    def test_active_owner_name_and_worktime_control_are_rendered(self):
        self.assertIn('current_agent_name', SCRIPT)
        self.assertIn('正在处理中', SCRIPT)
        self.assertIn('08:30', HTML)
        self.assertIn('17:30', SCRIPT)
        self.assertIn('/api/preferences/work-hours', SCRIPT)
        self.assertIn('Asia/Shanghai', SCRIPT)
        self.assertIn('worktime-track', STYLE)

    def test_context_is_lazy_and_inserted_as_inert_text(self):
        self.assertIn('`/api/items/${encodeURIComponent(id)}/context`', SCRIPT)
        self.assertIn('$("#record-dialog").showModal();', SCRIPT)
        self.assertIn('contextSection.addEventListener("toggle", () => { if (contextSection.open) loadItemContext(record); })', SCRIPT)
        self.assertIn('$(".item-context-section", detail)?.open', SCRIPT)
        self.assertIn('window.renderSafeMarkdown(contextBody', SCRIPT)
        self.assertIn('replaceContextPreservingScroll', SCRIPT)
        self.assertIn('model.contextPlaceholder(state.contexts.get(record.id), record.revision)', SCRIPT)
        self.assertIn('跟进历史（${historyFollowups.length}）', SCRIPT)
        self.assertIn('className = "detail-history-fold"', SCRIPT)

    def test_item_detail_leads_with_structured_brief_and_folds_full_context(self):
        self.assertIn('overviewTitle.textContent = "目前进展"', SCRIPT)
        self.assertIn('conclusion.textContent = txt(record.latest_brief || record.status_detail, "暂未记录进展")', SCRIPT)
        self.assertIn('["当前阶段", record.phase]', SCRIPT)
        self.assertIn('handoff.next_step || record.next_action || record.next_step', SCRIPT)
        self.assertIn('handoff.blocker || record.blocked_reason', SCRIPT)
        self.assertIn('["当前执行代理", record.current_agent_name || "未提供"]', SCRIPT)
        self.assertIn('["最近执行代理", record.last_agent_name || "未提供"]', SCRIPT)
        self.assertIn('["当前 agent 会话", record.current_agent_session_id || "未关联"]', SCRIPT)
        self.assertIn('["最近 agent 会话", record.last_agent_session_id || "未关联"]', SCRIPT)
        self.assertIn('context-${record.id}', SCRIPT)
        self.assertIn('bug-source-${record.id}', SCRIPT)
        self.assertIn('@media(max-width:400px){.detail-overview{padding:8px}.detail-overview-grid{grid-template-columns:minmax(0,1fr)}', STYLE)

    def test_active_followups_are_visible_and_fold_state_survives_refresh(self):
        self.assertIn('model.summarizeFollowups(followups(record))', SCRIPT)
        self.assertIn('`待跟进/处理中 ${followupSummary.activeCount} 条`', SCRIPT)
        self.assertIn('followupSummary.latestActive?.prompt', SCRIPT)
        self.assertIn('followupHistory.open = followupSummary.activeCount > 0', SCRIPT)
        self.assertIn('followupHistory.dataset.preserveKey = `followups-${record.id}`', SCRIPT)
        self.assertIn('const preservedFoldStates = new Map($$("details[data-preserve-key]")', SCRIPT)
        self.assertIn('node.open = preservedFoldStates.get(key)', SCRIPT)
        self.assertIn('if (detailFocus) $(`[data-dialog-focus="${CSS.escape(detailFocus)}"]`, detail)?.focus({ preventScroll: true })', SCRIPT)
        self.assertIn('state.drafts[id] = input.value', SCRIPT)
        self.assertIn('.active-followup-summary', STYLE)

    def test_bug_detail_sections_use_platform_contract_and_safe_archive_routes(self):
        self.assertIn('record.bug_detail', SCRIPT)
        self.assertIn('record.source_attachments', SCRIPT)
        self.assertIn('detailSection("BUG信息"', SCRIPT)
        self.assertIn('detailSection("BUG详情"', SCRIPT)
        self.assertIn('detailSection("BUG附件列表"', SCRIPT)
        self.assertIn('contextHeading.textContent = "事项上下文"', SCRIPT)
        self.assertIn('function missingBugFieldText(detail, key)', SCRIPT)
        self.assertIn('state === "not_provided"', SCRIPT)
        self.assertIn('state === "parse_failed"', SCRIPT)
        self.assertIn('尚未取得此项内容', SCRIPT)
        self.assertIn('attachment.error_summary', SCRIPT)
        self.assertNotIn('txt(attachment.error)', SCRIPT)
        self.assertIn('video.preload = "none"', SCRIPT)
        self.assertIn('function safeAttachmentHref(record, attachment)', SCRIPT)
        self.assertIn('filename.includes("/")', SCRIPT)
        self.assertNotIn('innerHTML', SCRIPT)
        self.assertIn('.bug-attachment-preview img', STYLE)

    def test_bug_attachment_empty_state_requires_successful_enumeration(self):
        self.assertIn('const readState = detail.attachments_read_state', SCRIPT)
        self.assertIn('detail.attachments_gaps', SCRIPT)
        self.assertIn('readState === "success" ? "平台未提供附件。"', SCRIPT)
        self.assertIn('readState === "failed" ? "附件列表读取失败，附件情况未知。"', SCRIPT)
        self.assertIn('"attachments_read_state":"success"', FIXTURE)
        self.assertIn('"attachments_read_state":"failed"', FIXTURE)
        self.assertIn('"attachments_read_state":"partial"', FIXTURE)

    def test_global_inspection_and_compact_motion_rules_are_present(self):
        self.assertIn('id="inspection-tools"', HTML)
        self.assertIn('马上开始巡检', SCRIPT)
        self.assertIn('巡检正在运行', SCRIPT)
        self.assertIn('上次巡检失败', SCRIPT)
        self.assertIn('@media(prefers-reduced-motion:reduce)', STYLE)
        self.assertRegex(STYLE, r"\.search\{height:40px[^}]*border-radius:999px")

    def test_source_directory_exposes_persistent_override_and_deprecation(self):
        self.assertIn('"非废弃·已锁定" : "非废弃"', SCRIPT)
        self.assertIn('sourceAction === "nondeprecated"', SCRIPT)
        self.assertIn('/lifecycle', SCRIPT)
        self.assertIn('lifecycle_override: lifecycleOverride', SCRIPT)
        self.assertIn('疑似废弃', SCRIPT)

    def test_scope_catalog_is_visible_and_bug_scope_ids_render_as_tags(self):
        self.assertIn('id="scopes-open"', HTML)
        self.assertIn('id="scopes-dialog"', HTML)
        self.assertIn('fetch("/api/scopes"', SCRIPT)
        self.assertIn("scopeStatusLabel(scope.status)", SCRIPT)
        self.assertIn('tag.className = `scope-tag scope-${scope.status}`', SCRIPT)
        self.assertIn('evidence.reference', SCRIPT)
        self.assertIn('link.rel = "noopener noreferrer"', SCRIPT)


if __name__ == "__main__":
    unittest.main()
