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
        self.assertIn('record.transfer_disposition === "transferred" ? "责任转移"', SCRIPT)
        self.assertRegex(STYLE, r"\.record-card\.record-completed \.record-title[^}]*color:#397859")
        self.assertIn('.record-card.transfer-pending .record-title', STYLE)
        self.assertIn('submitTransfer(transfer)', SCRIPT)
        self.assertIn('/transfer`, {}', SCRIPT)

    def test_agentless_work_is_shown_as_neutral_on_hold(self):
        self.assertIn('on_hold: "搁置"', SCRIPT)
        self.assertIn('activity: "Agent活动状态"', SCRIPT)
        self.assertIn('["in_progress", "developing", "testing", "on_hold", "pending_release"].includes(statusCode)', SCRIPT)
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

    def test_pending_release_status_is_blue_and_kept_out_of_completed_fold(self):
        self.assertIn('pending_release: "待发布"', SCRIPT)
        self.assertIn('"pending_release"].includes(statusCode)', SCRIPT)
        self.assertIn('effectiveStatus(record) === "pending_release"', SCRIPT)
        self.assertIn('.status-pill.pending_release{background:#e8f0fb;color:#2f6bb0}', STYLE)
        self.assertIn('.record-card.record-pending-release .record-title', STYLE)

    def test_workflow_section_sits_between_title_and_progress(self):
        self.assertIn('detail.append(head, statusLine, makeWorkflowSection(record), overview);', SCRIPT)
        self.assertIn('title.textContent = "流水线"', SCRIPT)
        self.assertIn('overviewTitle.textContent = "目前进展"', SCRIPT)
        self.assertIn('aria-labelledby", `workflow-title-${cssSafe(record.id)}`', SCRIPT)
        self.assertIn('.detail-workflow{margin:9px 0 12px', STYLE)

    def test_workflow_detail_fetches_its_own_endpoint_and_degrades_quietly(self):
        self.assertIn('fetch(`/api/items/${encodeURIComponent(id)}/workflow`, { cache: "no-store" })', SCRIPT)
        self.assertIn('payload = { unavailable: true };', SCRIPT)
        self.assertIn('loadItemWorkflow(record);', SCRIPT)
        self.assertIn('renderWorkflowInto(section, current);', SCRIPT)
        self.assertIn('detailState === "unavailable"', SCRIPT)
        self.assertIn('流程详情暂时读取失败，仅显示列表摘要。', SCRIPT)
        self.assertIn('workflowView(record, state.workflows.get(record.id) || null)', SCRIPT)

    def test_workflow_stepper_is_keyboard_accessible_and_not_color_only(self):
        self.assertIn('const list = document.createElement("ol"); list.className = "workflow-steps"', SCRIPT)
        self.assertIn('list.setAttribute("aria-label", "六阶段流水线")', SCRIPT)
        self.assertIn('if (step.current) item.setAttribute("aria-current", "step")', SCRIPT)
        self.assertIn('button.setAttribute("aria-expanded"', SCRIPT)
        self.assertIn('button.setAttribute("aria-controls"', SCRIPT)
        self.assertIn('selectWorkflowStage(workflowStage)', SCRIPT)
        self.assertIn('workflowGlyph(step.status)', SCRIPT)
        self.assertIn('stateText.textContent = step.statusLabel', SCRIPT)
        self.assertIn('subIndex.textContent = sub.indexLabel', SCRIPT)
        self.assertIn('indexLabel: `${index + 1}${"abcdefgh"[subIndex]', MODEL)
        self.assertIn('data-workflow-stage', SCRIPT)
        self.assertIn('.workflow-step[data-status="blocked"]{--wf-tone:var(--wf-blocked)', STYLE)

    def test_workflow_stage_chip_only_for_records_with_a_stage(self):
        self.assertIn('const chip = model.workflowChip(record);', SCRIPT)
        self.assertIn('if (!stageId) return null;', MODEL)
        self.assertIn('workflow_stage_short', MODEL)
        self.assertIn('stageChip.dataset.stage = chip.stageId', SCRIPT)
        self.assertIn('.record-card .record-head .stage-chip', STYLE)
        self.assertIn('.stage-chip[data-stage="audit_response"]', STYLE)
        self.assertIn('body[data-theme="dark"] .stage-chip[data-stage="development"]', STYLE)

    def test_workflow_stepper_is_horizontal_on_desktop_and_compact_on_narrow_screens(self):
        self.assertIn('.workflow-steps{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px', STYLE)
        self.assertIn('@media(max-width:620px){', STYLE)
        self.assertIn('.workflow-steps{display:grid;grid-template-columns:minmax(0,1fr);gap:6px}', STYLE)
        self.assertIn('.workflow-substeps{margin:3px 0 0 31px}', STYLE)

    def test_workflow_motion_is_css_only_and_respects_reduced_motion(self):
        self.assertIn('animation:workflow-step-in 160ms ease-out both', STYLE)
        self.assertIn('animation-delay:calc(var(--workflow-delay,0) * 30ms)', STYLE)
        self.assertIn('animation:workflow-pulse 1.8s ease-in-out infinite', STYLE)
        self.assertIn('@media(prefers-reduced-motion:reduce){', STYLE)
        self.assertIn('.workflow-enter .workflow-step{opacity:1;transform:none}', STYLE)
        self.assertIn('animation:none!important;transition:none!important}', STYLE)

    def test_workflow_legacy_records_offer_start_without_fake_outputs(self):
        self.assertIn('"未启用标准流程"', SCRIPT)
        self.assertIn('"启用标准流程"', SCRIPT)
        self.assertIn('`/api/items/${encodeURIComponent(id)}/workflow/start`', SCRIPT)
        self.assertIn('actor: "user"', SCRIPT)
        self.assertIn('"当前服务版本还不支持流水线接口"', SCRIPT)
        self.assertIn('阶段历史（${view.history.length}）', SCRIPT)
        self.assertIn('"未取得"', SCRIPT)

    def test_workflow_stage_lock_is_visible_and_has_expired_state(self):
        self.assertIn('workflow-lock', SCRIPT)
        self.assertIn('已锁定', SCRIPT)
        self.assertIn('锁已过期', SCRIPT)
        self.assertIn('workflow.lock', MODEL)
        self.assertIn('.workflow-lock[data-state="locked"]', STYLE)
        self.assertIn('.workflow-lock[data-state="expired"]', STYLE)
        self.assertIn('body[data-theme="dark"] .workflow-lock[data-state="locked"]', STYLE)

    def test_workflow_fixture_covers_legacy_active_blocked_rework_stage_four_and_failure(self):
        for marker in ("fixture-workflow-legacy", "fixture-workflow-active", "fixture-workflow-blocked",
                       "fixture-workflow-rework", "fixture-workflow-audit", "fixture-workflow-unavailable"):
            self.assertIn(marker, FIXTURE)
        self.assertIn("WORKFLOW_TEMPLATE", FIXTURE)
        self.assertIn('"substeps"', FIXTURE)
        self.assertIn('path.endswith("/workflow")', FIXTURE)

    def test_library_dialog_exposes_fixed_slots_versions_and_inspection(self):
        self.assertIn('<h2 id="sources-title">资料馆</h2>', HTML)
        self.assertIn('需求来源与参考资料', HTML)
        self.assertIn('aria-label="关闭资料馆"', HTML)
        self.assertIn('id="library-slot-grid"', HTML)
        self.assertIn('id="library-other-list"', HTML)
        self.assertIn('id="library-add-slot"', HTML)
        self.assertIn('id="library-add-version"', HTML)
        for slot in ("requirement_doc", "figma", "codesign", "api_doc"):
            self.assertIn(f'value="{slot}"', HTML)
        for logo in ("requirement_doc", "figma", "codesign", "api_doc", "file", "link"):
            self.assertIn(f'id="library-logo-{logo}"', HTML)
            self.assertTrue((WEB / "assets" / f"library-{logo.replace('_', '-')}.svg").is_file())
        self.assertIn('libraryView(state.sources)', SCRIPT)
        self.assertIn('还有 ${group.history_count} 个版本', SCRIPT)
        self.assertIn('article.append(name, link, subtitle, inspection, actions, makeVersionHistory(slot.group));', SCRIPT)
        self.assertIn('data-library-versions', SCRIPT)
        self.assertIn('巡检包含', SCRIPT)
        self.assertIn('上次巡检成功', SCRIPT)
        self.assertIn('/inspection', SCRIPT)
        self.assertIn('/slot', SCRIPT)
        self.assertIn('all_versions=1', SCRIPT)
        self.assertIn('source.inspection_eligible === false ? "历史版本不巡检"', SCRIPT)
        self.assertIn('select.title = "设为固定资料"', SCRIPT)
        self.assertIn('添加资料或新版本', HTML)
        self.assertIn('.library-slot-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))', STYLE)
        self.assertIn('.library-inspection-dot{', STYLE)
        self.assertIn('animation:library-breathe 1.9s ease-in-out infinite', STYLE)
        self.assertIn('body[data-theme="dark"] .sources-dialog.library-dialog', STYLE)
        self.assertIn('@media(max-width:640px)', STYLE)
        self.assertIn('@media(prefers-reduced-motion:reduce){', STYLE)

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
