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
    def test_iteration_bar_and_dialogs_exist_in_html(self):
        self.assertIn('id="iteration-bar"', HTML)
        self.assertIn('id="iteration-select"', HTML)
        self.assertIn('id="iteration-status-pill"', HTML)
        self.assertIn('id="iteration-release-button"', HTML)
        self.assertIn('id="iteration-create-open"', HTML)
        self.assertIn('id="iteration-dialog"', HTML)
        self.assertIn('id="reopen-dialog"', HTML)
        self.assertIn('id="reopen-reason-input"', HTML)
        self.assertIn('id="reopen-audit-mode"', HTML)

    def test_archive_scope_tabs_and_supplementary_pool(self):
        self.assertIn('id="archive-scope-tabs"', HTML)
        self.assertIn('data-scope="iteration"', HTML)
        self.assertIn('data-scope="global"', HTML)
        self.assertIn('id="supplementary-pool-section"', HTML)
        self.assertIn('id="supplementary-grid"', HTML)
        self.assertIn('id="library-logo-prototype"', HTML)
        self.assertIn('id="library-logo-architecture"', HTML)
        self.assertIn('id="library-logo-supplementary"', HTML)

    def test_zero_collapse_iteration_partitioning(self):
        self.assertIn('partitionIterationRecords', MODEL)
        self.assertIn('model.partitionIterationRecords', SCRIPT)
        self.assertIn('iteration-active-block', SCRIPT)
        self.assertIn('item.visibleRecords.map(makeCard)', SCRIPT)
        self.assertIn('reopen-badge', SCRIPT)
        self.assertIn('rework-btn', SCRIPT)
        self.assertIn('detail-rework-bar', SCRIPT)

    def test_apple_minimalist_css_for_iteration_and_archive(self):
        self.assertIn('.iteration-bar{', STYLE.replace(" ", ""))
        self.assertIn('.iteration-status-pill{', STYLE.replace(" ", ""))
        self.assertIn('.archive-nav{', STYLE.replace(" ", ""))
        self.assertIn('body[data-theme="dark"].iteration-bar', STYLE.replace(" ", ""))
        self.assertIn('body[data-theme="dark"].archive-nav', STYLE.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
