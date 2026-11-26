# Created by Codex on 2026/11/19.
# Copyright © 2026 CMIC. All rights reserved.

import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
SCRIPT = (WEB / "assets/dashboard.js").read_text(encoding="utf-8")
MODEL = (WEB / "assets/dashboard-model.js").read_text(encoding="utf-8")
STYLE = (WEB / "assets/dashboard.css").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")


class ArchiveUIContractTests(unittest.TestCase):
    def test_version_manager_replaces_current_version_bar(self):
        for selector in ("iteration-bar", "iteration-select", "iteration-status-pill",
                         "iteration-release-button", "iteration-id-badge", "iteration-edit-open"):
            self.assertNotIn(f'id="{selector}"', HTML)
        for selector in ("iteration-manager-open", "iteration-manager-dialog", "iteration-manager-list",
                         "iteration-manager-count", "iteration-manager-loading",
                         "iteration-create-open", "iteration-dialog",
                         "iteration-edit-dialog", "iteration-type-input", "iteration-edit-type"):
            self.assertIn(f'id="{selector}"', HTML)
        # 发起修改入口已从界面移除；历史数据仍由后端与详情只读保留。
        for selector in ("reopen-dialog", "reopen-reason-input", "reopen-audit-mode"):
            self.assertNotIn(f'id="{selector}"', HTML)
        self.assertNotIn("data-reopen-id", SCRIPT)
        self.assertNotIn("openReopenDialog", SCRIPT)
        self.assertIn('aria-haspopup="dialog">编辑版本</button>', HTML)
        self.assertIn('<h2 id="iteration-manager-title">编辑版本</h2>', HTML)
        self.assertIn('aria-label="关闭编辑版本"', HTML)
        self.assertIn("越靠上越新", HTML)
        # Assignment editing moved back to the record detail only.
        self.assertNotIn('id="iteration-assign-list"', HTML)
        self.assertNotIn('id="iteration-assign-empty"', HTML)
        self.assertNotIn("需求归属", HTML)
        self.assertNotIn("renderIterationAssignments", SCRIPT)
        self.assertNotIn("assignRecordIteration", SCRIPT)
        self.assertIn("在需求详情里即可调整所属版本", HTML)
        self.assertIn("ariaLabel: `上移 ${versionLabel}`", SCRIPT)
        self.assertIn(".iteration-manager-dialog .manager-hero{", STYLE)
        self.assertNotIn("\n.manager-hero{", STYLE)
        self.assertIn('iterSelect.className = "detail-iteration-select"', SCRIPT)
        self.assertIn('makeManagerAction("上移"', SCRIPT)
        self.assertIn('makeManagerAction("标记发布"', SCRIPT)
        # One editable version name; no second visible version_tag field.
        self.assertNotIn('id="iteration-tag-input"', HTML)
        self.assertNotIn('id="iteration-edit-tag-input"', HTML)
        self.assertIn('id="iteration-order-select"', HTML)

    def test_archive_has_exactly_two_tabs_and_a_version_matrix(self):
        self.assertIn('id="archive-scope-tabs"', HTML)
        self.assertIn('data-scope="iteration"', HTML)
        self.assertIn('data-scope="global"', HTML)
        self.assertNotIn('data-scope="history"', HTML)
        self.assertIn('id="archive-body"', HTML)
        for removed in ("supplementary-pool-section", "supplementary-grid", "library-other-list",
                        "archive-history-section", "archive-history-list"):
            self.assertNotIn(f'id="{removed}"', HTML)
        self.assertIn("版本资料", HTML)
        self.assertIn("公共材料", HTML)
        self.assertNotIn("当前版本资料", HTML)
        self.assertNotIn("历史版本归档", HTML)
        self.assertNotIn("补充材料池", HTML)

    def test_work_is_grouped_by_version_but_tasks_are_not(self):
        self.assertIn('id="work-iteration-container"', HTML)
        self.assertNotIn('id="task-iteration-container"', HTML)
        self.assertNotIn('id="task-released-fold"', HTML)
        self.assertNotIn('id="work-released-fold"', HTML)
        self.assertIn("model.partitionIterationRecords(records, iterations)", SCRIPT)
        self.assertIn("makeWorkIterationBlock", SCRIPT)
        self.assertIn("iteration-work-meta", SCRIPT)
        self.assertNotIn('$("#task-iteration-container")', SCRIPT)
        self.assertNotIn("releasedIterations", SCRIPT)
        # The task renderer must not call the version partitioner.
        task_start = SCRIPT.index("function renderTaskBatches()")
        task_end = SCRIPT.index("function makeTaskCard(")
        self.assertNotIn("partitionIterationRecords", SCRIPT[task_start:task_end])

    def test_version_ordering_and_single_readable_name(self):
        self.assertIn("function orderedIterations", MODEL)
        self.assertIn("function iterationName", MODEL)
        self.assertIn("function iterationSecondaryTag", MODEL)
        self.assertIn('title || tag || id || "未命名版本"', MODEL)
        self.assertIn("iteration-order-select", SCRIPT)
        self.assertIn("/api/iterations/order", SCRIPT)
        self.assertIn("reorderIteration", SCRIPT)
        self.assertIn("iterationStatusLabel", SCRIPT)

    def test_archive_model_isolates_scopes_and_groups_move_whole_versions(self):
        self.assertIn("function sourceScope", MODEL)
        self.assertIn("function buildArchiveSection", MODEL)
        self.assertIn("supplementaryGroups", MODEL)
        self.assertIn("section.removed", MODEL)
        self.assertIn('id="relocate-group-hint"', HTML)
        self.assertIn("groupHint", SCRIPT)
        self.assertIn("groupSize", SCRIPT)
        self.assertIn("/location", SCRIPT)
        self.assertIn("source_group_location_conflict", SCRIPT)
        self.assertIn("set_source_location", (WEB.parent / "dashboardctl.py").read_text(encoding="utf-8"))

    def test_versions_are_newest_first_and_released_items_are_progressive(self):
        self.assertIn("return bOrder - aOrder;", MODEL)
        self.assertIn("activeGroups: groups.filter", MODEL)
        self.assertIn("releasedGroups: groups.filter", MODEL)
        for selector in ("work-released-section", "work-released-toggle", "work-released-list"):
            self.assertIn(f'id="{selector}"', HTML)
        # Active versions stay above the released entry; the released list is
        # nested after the active container so expanded cards render below it.
        self.assertLess(HTML.index('id="work-iteration-container"'), HTML.index('id="work-released-section"'))
        self.assertLess(HTML.index('id="work-iteration-container"'), HTML.index('id="work-released-list"'))
        for marker in ("查看已发布版本", "state.workReleasedOpen", "state.workReleasedExpanded",
                       "state.archiveReleasedExpanded", "makeArchiveSection(section, { released: true })",
                       "archive-block-released"):
            self.assertIn(marker, SCRIPT + HTML + STYLE)
        self.assertIn("已发布版本", SCRIPT)

    def test_small_screen_masonry_and_search_expand_released_versions(self):
        self.assertIn("const visibleColumns = columns.map(column => getComputedStyle(column).display !== \"none\");", SCRIPT)
        self.assertIn("model.masonryColumnIndex([], visibleColumns, index)", SCRIPT)
        self.assertIn("card.style.order = String(index);", SCRIPT)
        self.assertIn("model.releasedEntryOpen(state.workReleasedOpen, searchActive, groups.length)", SCRIPT)
        self.assertIn("model.releasedVersionOpen(state.workReleasedExpanded.has(item.iteration.id), searchActive, true)", SCRIPT)
        self.assertIn("搜索命中，临时展开", HTML)
        self.assertIn("target._releasedGroup", SCRIPT)
        self.assertIn(".released-version-list[hidden]{display:none;margin-top:0}", STYLE.replace("\n", ""))

    def test_topbar_scroll_chrome_and_overlay_scrollbars_are_passive(self):
        self.assertIn('window.addEventListener("scroll", scheduleScrollFrame, { passive: true })', SCRIPT)
        self.assertIn("requestAnimationFrame(() => { scrollFrame = 0; updatePageChrome(); })", SCRIPT)
        self.assertIn('topbar.style.setProperty("--topbar-p", topbarP.toFixed(3))', SCRIPT)
        self.assertIn('id="page-scroll-rail"', HTML)
        self.assertIn('id="detail-scroll-rail"', HTML)
        self.assertIn('id="topbar-waves"', HTML)
        # 顶栏道路：canvas、rAF、隐藏页面暂停与 reduced-motion 静态回退。
        self.assertIn("setupTopbarWaves(topbar)", SCRIPT)
        self.assertIn("function surgeLevel(now)", SCRIPT)
        self.assertIn("const PULSE_MS = 2100", SCRIPT)
        self.assertIn("0.5 - 0.5 * Math.cos(progress * Math.PI * 2)", SCRIPT)
        self.assertNotIn("Math.exp(-Math.max", SCRIPT)
        self.assertIn('document.addEventListener("visibilitychange"', SCRIPT)
        self.assertIn("if (document.hidden) { stop(); return; }", SCRIPT)
        self.assertIn("if (reduceMotion.matches) { stop(); draw(0, 0); return; }", SCRIPT)
        self.assertIn("waves.pulse(1)", SCRIPT)
        self.assertNotIn("is-at-bottom", SCRIPT)
        self.assertNotIn("topbar-bottom-line", STYLE)
        self.assertIn(".topbar .topbar-waves", STYLE)
        # 顶栏道路（重做）：四条大曲度车道贯穿整幅顶栏，近宽远窄，
        # 实线与短虚线组合；滚动推相位、压缩沿同一组曲线连续变形。
        self.assertIn("function measureRoad()", SCRIPT)
        self.assertIn("const spreadAt = t =>", SCRIPT)
        self.assertIn("const near = w * (0.205 - compressed * 0.010)", SCRIPT)
        self.assertIn("const far = w * (0.052 - compressed * 0.003)", SCRIPT)
        self.assertIn("Math.pow(Math.max(0, 1 - t), 0.92)", SCRIPT)
        self.assertIn("const centerAt = t => w * (0.405 + 0.17 * t + 0.06 * t * t)", SCRIPT)
        self.assertIn("lane.offset * spreadAt(t)", SCRIPT)
        self.assertIn("const startT = -0.05, endT = 1.05", SCRIPT)
        self.assertIn("ctx.setLineDash(lane.dash || [])", SCRIPT)
        self.assertIn("ctx.lineDashOffset", SCRIPT)
        self.assertIn("ctx.createLinearGradient(0, h, 0, 0)", SCRIPT)
        self.assertIn("const phase = time * 0.3 + scrollPhase", SCRIPT)
        self.assertIn("waves.nudge(dy)", SCRIPT)
        self.assertIn("return { pulse, nudge, resize: scheduleResize };", SCRIPT)
        # 滚动条：一束光，长光晕 + 短亮核；光束随滚动移动，核位与能量连续变化。
        self.assertIn(".scroll-rail-thumb", STYLE)
        self.assertIn("--rail-core-shift", STYLE)
        self.assertIn("--rail-energy", STYLE)
        self.assertIn("var(--rail-core-color) 43%,var(--rail-core-color) 57%", STYLE)
        self.assertIn("background-position:0 calc(var(--rail-flow,.5) * 100%)", STYLE)
        self.assertIn("const beamSize = track =>", SCRIPT)
        self.assertIn("paintFlow(rail, flow)", SCRIPT)
        self.assertIn("nudgeFlow(pageFlow, dy, pageRail)", SCRIPT)
        self.assertIn("nudgeFlow(detailFlow, top - lastDetailTop, detailRail)", SCRIPT)
        self.assertIn("scrollbar-width:none", STYLE)
        self.assertIn("@media(prefers-reduced-motion:reduce)", STYLE)
        self.assertIn("return index % 2;", MODEL)

    def test_record_dialog_close_uses_one_guarded_exit_animation(self):
        self.assertIn("function closeDetailDialog()", SCRIPT)
        self.assertIn('dialog.classList.contains("is-closing")', SCRIPT)
        self.assertIn('dialog.classList.add("is-closing")', SCRIPT)
        self.assertIn("detailCloseTimer = window.setTimeout", SCRIPT)
        self.assertIn("}, 200);", SCRIPT)
        self.assertIn("if (detailReduceMotion.matches) { dialog.close(); return; }", SCRIPT)
        # 关闭按钮、遮罩、ESC（cancel + keydown）统一走同一入口。
        self.assertIn('$("#dialog-close").addEventListener("click", closeDetailDialog)', SCRIPT)
        self.assertIn('if (event.target === $("#record-dialog")) closeDetailDialog();', SCRIPT)
        self.assertIn('$("#record-dialog").addEventListener("cancel"', SCRIPT)
        self.assertIn("event.preventDefault();", SCRIPT)
        self.assertIn("if ($(\"#record-dialog\").open) closeDetailDialog();", SCRIPT)
        self.assertIn("animation:dialog-out 200ms", STYLE)
        self.assertIn("body[data-theme] .record-dialog.is-closing::backdrop", STYLE)
        self.assertIn("body[data-theme] .record-dialog.is-closing::backdrop{animation:none!important}", STYLE)

    def test_record_dialog_backdrop_drops_blur_and_every_scroll_surface_has_a_rail(self):
        # 详情弹窗遮罩不再做实时模糊，避免滚动时重采样背后的 canvas。
        self.assertIn("body[data-theme] .record-dialog::backdrop{background:#0000006b;backdrop-filter:none", STYLE)
        self.assertIn("body[data-theme] .record-dialog{", STYLE)
        record_block = STYLE[STYLE.index("body[data-theme] .record-dialog{"):STYLE.index("body[data-theme] .record-dialog::backdrop")]
        self.assertIn("contain:paint", record_block)
        # 所有主滚动面共用同一条自绘轨道，并隐藏原生滚动条。
        for pair in ('["#sources-dialog", ".library-shell"]',
                     '["#inspection-dialog", ".inspection-dialog-shell"]',
                     '["#settings-dialog", ".sources-shell"]',
                     '["#scopes-dialog", ".sources-shell"]',
                     '["#text-viewer-dialog", ".text-viewer-content"]',
                     '["#iteration-manager-dialog", ".iteration-manager-list"]',
                     '["#iteration-dialog", ".dialog-shell"]',
                     '["#source-edit-dialog", ".source-edit-form"]',
                     '["#source-relocate-dialog", ".dialog-shell"]',
                     '["#work-hours-dialog", ".work-hours-form"]'):
            self.assertIn(pair, SCRIPT)
        self.assertIn("function setupDialogRail(dialogSelector, containerSelector)", SCRIPT)
        self.assertIn("scrollbar-gutter:auto", STYLE)
        self.assertIn("::-webkit-scrollbar{width:0;height:0;display:none}", STYLE)
        self.assertIn("observer.observe(container)", SCRIPT)
        self.assertIn("container.addEventListener(\"scroll\", update, { passive: true })", SCRIPT)

    def test_text_viewer_backdrop_drops_blur_and_scrolls_in_its_own_paint_layer(self):
        # 全文查看器遮罩同样不再实时模糊，正文滚动限定在独立绘制层内。
        self.assertIn("body[data-theme] #text-viewer-dialog::backdrop{background:#0000006b;backdrop-filter:none", STYLE)
        self.assertIn('body[data-theme="dark"] #text-viewer-dialog::backdrop{background:#000000b8;backdrop-filter:none', STYLE)
        viewer_block = STYLE[STYLE.index("#text-viewer-dialog .text-viewer-content{"):STYLE.index("#text-viewer-dialog .text-viewer-actions{")]
        self.assertIn("contain:paint", viewer_block)
        self.assertIn("overscroll-behavior:contain", viewer_block)

    def test_library_dialog_drops_scroll_blur_and_skips_redundant_rerenders(self):
        self.assertIn(".sources-dialog.library-dialog::backdrop{backdrop-filter:none", STYLE)
        self.assertIn("body .library-dialog .library-top{backdrop-filter:none", STYLE)
        self.assertNotIn("library-dialog::backdrop{background:#00000038;backdrop-filter", STYLE)
        self.assertIn("state.sourcesRevision !== revision", SCRIPT)
        self.assertIn('if (changed || !$("#archive-body")?.childElementCount) renderSources();', SCRIPT)
        self.assertIn("history.hidden = !expanded", SCRIPT)
        self.assertIn("window.scrollTo(scrollX, scrollY)", SCRIPT)

    def test_new_sections_reuse_the_existing_visual_language(self):
        compact = STYLE.replace(" ", "").replace("\n", "")
        for selector in (".archive-block{", ".archive-block-head{", ".archive-supplementary{",
                         ".iteration-work-block{", ".iteration-manager-row{", ".archive-nav{",
                         ".toolbar-action{"):
            self.assertIn(selector, compact)
        self.assertIn('body[data-theme="dark"] .iteration-manager-row', STYLE)
        self.assertIn("@media(max-width:520px)", STYLE)
        self.assertIn("@media(prefers-reduced-motion:reduce)", STYLE)
        self.assertIn(".archive-body .is-highlighted", STYLE)
        self.assertNotIn("📦", SCRIPT)
        self.assertNotIn("style.cssText", SCRIPT)

    def test_render_detail_keeps_history_but_has_no_reopen_or_followup_action(self):
        start = SCRIPT.index("function renderDetail(record)")
        end = SCRIPT.index("function openDetail(", start)
        body = SCRIPT[start:end]
        self.assertNotIn("data-reopen-id", body)
        self.assertNotIn("发起二次修改", body)
        self.assertNotIn("data-followup-id", body)
        # 二次修改轮次与跟进历史仍以只读记录展示，数据不删除。
        self.assertIn("二次修改与再审计记录", body)
        self.assertIn("跟进历史（${historyFollowups.length}）", body)

    def test_archive_tabs_are_inset_segmented_controls_with_aria_and_keyboard(self):
        for marker in (".sources-dialog.library-dialog .archive-nav",
                       "max-width:calc(100% - 48px)", "margin:2px 24px 14px",
                       "padding:3px", "border-radius:11px",
                       "body[data-theme=\"dark\"] .library-dialog .archive-tab-btn.active",
                       "archive-tab-btn:focus-visible"):
            self.assertIn(marker, STYLE)
        self.assertIn("display:inline-flex;\n  align-self:flex-start;", STYLE)
        self.assertIn('aria-controls="archive-body"', HTML)
        self.assertIn('id="tab-archive-iteration" aria-selected="true" aria-controls="archive-body" tabindex="0"', HTML)
        self.assertIn('aria-labelledby", activeScope === "global" ? "tab-archive-global" : "tab-archive-iteration"', SCRIPT)
        self.assertIn('btn.tabIndex = isActive ? 0 : -1;', SCRIPT)
        self.assertIn('event.key === "ArrowRight" || event.key === "ArrowDown"', SCRIPT)
        self.assertIn('event.key === "Home"', SCRIPT)

    def test_version_type_is_modeled_across_manager_archive_and_home(self):
        self.assertIn("version_type", MODEL)
        self.assertIn("function iterationVersionType", MODEL)
        self.assertIn("function versionSlotDefinitions", MODEL)
        self.assertIn("analytics_requirement: \"埋点需求表\"", MODEL)
        self.assertIn("analytics_acceptance: \"埋点验收表\"", MODEL)
        self.assertIn("versionSlotDefinitions(iteration)", MODEL)
        self.assertIn("versionTypePill", SCRIPT)
        self.assertIn("/type`, { version_type: targetType }", SCRIPT)
        self.assertIn('makeManagerAction(iterationVersionType(iteration) === "analytics" ? "转为迭代" : "转为埋点"', SCRIPT)
        self.assertIn('id="iteration-type-input"', HTML)
        self.assertIn('<option value="analytics">埋点版本</option>', HTML)
        self.assertIn("version_tag: title, title, target_release_date, description, order, version_type", SCRIPT)
        self.assertIn(".version-type-pill.analytics", STYLE)
        self.assertIn("body[data-theme=\"dark\"] .version-type-pill.analytics", STYLE)
        # 版本名重复时由后端自动叠加迭代序号，ID 不变。
        self.assertIn("unique_iteration_title", (WEB.parent / "dashboardctl.py").read_text(encoding="utf-8"))

    def test_version_manager_shows_status_capsule_and_manual_switch(self):
        self.assertIn('id="iteration-manager-status-note"', HTML)
        self.assertIn("规划中 / 进行中会随版本内需求数量自动变化", HTML)
        self.assertIn("待发布 / 已发布 / 已归档为手动状态", HTML)
        self.assertIn('const ITERATION_STATUS_CHOICES = ["planning", "active", "pending_release", "released", "archived"];', SCRIPT)
        self.assertIn("function iterationStatusSelect(iteration, busy)", SCRIPT)
        self.assertIn("titleLine.append(name, versionTypePill(iteration), iterationStatusSelect(iteration, busy));", SCRIPT)
        self.assertIn("select.dataset.iterationStatus = iteration.id;", SCRIPT)
        self.assertIn('select.setAttribute("aria-describedby", "iteration-manager-status-note");', SCRIPT)
        self.assertIn("changeIterationStatus(select.dataset.iterationStatus, select.value);", SCRIPT)
        self.assertIn("`/api/iterations/${encodeURIComponent(iterationId)}/update`, { status }", SCRIPT)
        self.assertIn("该状态会随需求数量自动变化", SCRIPT)
        self.assertIn('invalid_iteration_status: "版本状态无效，请刷新后重试。"', SCRIPT)
        self.assertIn("无法连接本地服务，版本状态未修改", SCRIPT)
        self.assertIn(".iteration-manager-status-select{", STYLE)
        for status in ("planning", "active", "pending_release", "released", "archived"):
            self.assertIn(f".iteration-manager-status-select.is-{status}", STYLE)
        self.assertIn('body[data-theme="dark"] .iteration-manager-status-select.is-released', STYLE)
        self.assertIn(".iteration-manager-dialog .manager-status-note{", STYLE)

    def test_create_version_dialog_uses_apple_minimal_card_layout(self):
        for selector in ("iteration-form", "iteration-title-input", "iteration-type-input",
                         "iteration-order-select", "iteration-date-input", "iteration-desc-input",
                         "iteration-feedback", "iteration-cancel", "iteration-submit"):
            self.assertIn(f'id="{selector}"', HTML)
        self.assertIn('id="iteration-form" class="iteration-create-form"', HTML)
        self.assertNotIn('id="iteration-form" class="library-add-form"', HTML)
        self.assertIn("iteration-create-card", HTML)
        self.assertIn("iteration-create-grid", HTML)
        self.assertIn("iteration-create-status-note", HTML)
        self.assertIn("新建为空版本，自动为规划中；加入需求后自动进行中。", HTML)
        self.assertIn(".iteration-create-dialog{", STYLE)
        self.assertIn(".iteration-create-card{display:grid;", STYLE)
        self.assertIn(".iteration-create-grid{display:grid;", STYLE)
        self.assertIn("#iteration-dialog .source-feedback{", STYLE)
        self.assertIn('body[data-theme="dark"] .iteration-create-card{', STYLE)
        self.assertIn("@media(max-width:520px)", STYLE)
        self.assertIn(".iteration-create-card{grid-template-columns:minmax(0,1fr);", STYLE)
        self.assertIn(".iteration-create-grid{grid-template-columns:minmax(0,1fr);", STYLE)

    def test_numeric_ids_use_one_display_identifier(self):
        self.assertIn("function recordDisplayId", SCRIPT)
        self.assertIn("return /^[0-9]+$/.test(String(raw)) ? `#${raw}` : String(raw);", SCRIPT)
        self.assertIn("recordDisplayId(record)", SCRIPT)
        self.assertIn("recordDisplayId(record), record.title", SCRIPT)
        self.assertIn("recordDisplayId(record)", SCRIPT)
        self.assertIn("navigator.clipboard.writeText(record.id)", SCRIPT)
        self.assertIn("NUMERIC_RECORD_ID_RE", (WEB.parent / "dashboardctl.py").read_text(encoding="utf-8"))
        self.assertIn("record_id_seq", (WEB.parent / "dashboardctl.py").read_text(encoding="utf-8"))

    def test_relocate_dialog_shows_current_and_group_context(self):
        for selector in ("relocate-current-location", "relocate-group-hint", "relocate-scope-select",
                         "relocate-iteration-select", "relocate-slot-select"):
            self.assertIn(f'id="{selector}"', HTML)
        self.assertIn("当前位置：", SCRIPT)
        self.assertIn("移动会把整组一起移到目标位置", SCRIPT)
        self.assertIn("撤销", SCRIPT)


if __name__ == "__main__":
    unittest.main()
