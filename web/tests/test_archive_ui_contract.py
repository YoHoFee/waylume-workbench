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
                         "iteration-assign-list", "iteration-create-open", "iteration-dialog",
                         "iteration-edit-dialog", "reopen-dialog", "reopen-reason-input", "reopen-audit-mode"):
            self.assertIn(f'id="{selector}"', HTML)
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

    def test_relocate_dialog_shows_current_and_group_context(self):
        for selector in ("relocate-current-location", "relocate-group-hint", "relocate-scope-select",
                         "relocate-iteration-select", "relocate-slot-select"):
            self.assertIn(f'id="{selector}"', HTML)
        self.assertIn("当前位置：", SCRIPT)
        self.assertIn("移动会把整组一起移到目标位置", SCRIPT)
        self.assertIn("撤销", SCRIPT)


if __name__ == "__main__":
    unittest.main()
