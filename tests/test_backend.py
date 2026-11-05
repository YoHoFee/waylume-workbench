import json
import hashlib
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import backend
import dashboardctl
import server


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patchers = [
            patch.object(dashboardctl, "ROOT", self.root),
            patch.object(dashboardctl, "STORE", self.root / "store.json"),
            patch.object(dashboardctl, "LOCK", self.root / ".store.lock"),
            patch.object(dashboardctl, "EVENTS", self.root / "events.jsonl"),
        ]
        for p in self.patchers: p.start()
        dashboardctl.atomic_write({"schema_version": 1, "revision": 1, "records": [{"id": "T-1", "type": "task", "title": "task", "status": "in_progress", "notes": ["n"], "history": [{"x": 1}], "followups": [{"id": "f"}]}], "sources": [{"id": "s"}], "batches": [{"id": "b"}]})
        (self.root / "scope-catalog.json").write_text(json.dumps({"version": 1, "revision": 4, "updated_at": "2026-10-27", "scopes": [
            {"id": "SCOPE-EXISTING", "name": "Existing", "status": "candidate", "in_scope": ["old"], "out_of_scope": [], "evidence": [{"type": "requirement", "date": None, "reference": "fixture", "summary": "seed"}], "related_ids": ["REQ-1"], "version": 2},
            {"id": "SCOPE-BGM-CONTAINER", "name": "Background music container", "status": "confirmed", "in_scope": ["fixture"], "out_of_scope": ["云盘"], "evidence": [{"type": "requirement", "date": None, "reference": "fixture", "summary": "seed"}], "related_ids": [], "version": 1},
            {"id": "SCOPE-KEEP", "name": "Keep", "status": "not_ours", "in_scope": [], "out_of_scope": ["outside"], "evidence": [{"type": "bug", "date": "2026-10-26", "reference": "BUG-1", "summary": "kept"}], "related_ids": [], "version": 1}
        ]}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patchers): p.stop()
        self.temp.cleanup()

    def test_upsert_generates_ids_for_new_tasks_and_work_but_preserves_source_id(self):
        from argparse import Namespace
        from contextlib import redirect_stdout
        from io import StringIO
        input_path = self.root / "new-record.json"
        results = []
        for payload in ({"type": "task", "title": "new task", "status": "pending"},
                        {"type": "work", "title": "new work", "status": "pending"},
                        {"id": "JIRA-123", "type": "work", "title": "source", "status": "pending"}):
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output): dashboardctl.cmd_upsert(Namespace(input=str(input_path)))
            results.append(json.loads(output.getvalue())["record"]["id"])
        self.assertRegex(results[0], r"^ZT-T-[A-F0-9]{12}$")
        self.assertRegex(results[1], r"^ZT-W-[A-F0-9]{12}$")
        self.assertEqual(results[2], "JIRA-123")
        self.assertEqual(len(set(results)), 3)

    def test_separate_files_recoverable_layout_and_context_path(self):
        self.assertTrue((self.root / "records/task/T-1/record.json").is_file())
        self.assertTrue((self.root / "records/task/T-1/context.md").is_file())
        self.assertTrue((self.root / "meta.json").is_file())
        self.assertFalse((self.root / ".transaction.json").exists())
        record = dashboardctl.load_store()["records"][0]
        self.assertEqual(record["followups"], [{"id": "f"}])
        self.assertIn("# task", backend.read_context(self.root, "task", "T-1"))
        with self.assertRaises(ValueError): backend.read_context(self.root, "task", "../T-1")

    def test_scope_catalog_is_versioned_read_only_data_with_known_statuses(self):
        catalog = json.loads((self.root / "scope-catalog.json").read_text(encoding="utf-8"))
        with patch.object(server, "SCOPE_CATALOG", self.root / "scope-catalog.json"):
            loaded = server.load_scope_catalog()
        self.assertEqual(catalog["version"], 1)
        self.assertEqual(loaded["revision"], catalog["revision"])
        self.assertEqual({scope["status"] for scope in loaded["scopes"]}, {"confirmed", "candidate", "not_ours"})
        self.assertEqual(len({scope["id"] for scope in loaded["scopes"]}), len(loaded["scopes"]))
        container = next(scope for scope in loaded["scopes"] if scope["id"] == "SCOPE-BGM-CONTAINER")
        self.assertTrue(any("云盘" in item for item in container["out_of_scope"]))

    def test_upsert_expected_store_revision_is_compare_and_swap(self):
        bug = next(record for record in dashboardctl.load_store()["records"] if record["id"] == "T-1")
        payload = {key: bug[key] for key in ("id", "type", "title", "status")}
        payload["related_ids"] = ["SCOPE-BGM-CONTAINER"]
        input_path = self.root / "upsert.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        with patch("sys.stdout", new_callable=__import__("io").StringIO):
            dashboardctl.cmd_upsert(__import__("argparse").Namespace(input=str(input_path), expected_store_revision=1))
        updated = dashboardctl.load_store()["records"][0]
        self.assertEqual(updated["related_ids"], ["SCOPE-BGM-CONTAINER"])
        with self.assertRaisesRegex(ValueError, "store_revision_conflict"):
            with patch("sys.stdout", new_callable=__import__("io").StringIO):
                dashboardctl.cmd_upsert(__import__("argparse").Namespace(input=str(input_path), expected_store_revision=1))

    def test_handoff_update_preserves_manual_completion_and_context_under_cas(self):
        db = dashboardctl.load_store(); record = db["records"][0]
        record.update({"manual_status": "completed", "agent_state": "paused"})
        dashboardctl.atomic_write(db)
        record = dashboardctl.load_store()["records"][0]
        context = self.root / "records/task/T-1/context.md"
        context.write_text("# 手写上下文\n\n不可丢失。\n", encoding="utf-8")
        revision = record["revision"]
        dashboardctl.update_handoff("T-1", revision, {"next_step": "核对证据"})
        after = dashboardctl.load_store()["records"][0]
        self.assertEqual(after["manual_status"], "completed")
        self.assertEqual(after["agent_state"], "paused")
        self.assertEqual(after["handoff"]["next_step"], "核对证据")
        self.assertEqual(context.read_text(encoding="utf-8"), "# 手写上下文\n\n不可丢失。\n")
        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            dashboardctl.update_handoff("T-1", revision, {"next_step": "旧写入"})
        self.assertEqual(dashboardctl.load_store()["records"][0]["handoff"]["next_step"], "核对证据")

    def test_scope_upsert_cas_conflict_leaves_catalog_unchanged(self):
        path = self.root / "scope-catalog.json"
        before = path.read_bytes()
        scope = {"id": "SCOPE-NEW", "name": "New", "status": "candidate", "in_scope": ["in"], "out_of_scope": [],
                 "evidence": [{"type": "user_instruction", "date": None, "reference": "fixture", "summary": "scoped"}], "related_ids": []}
        with self.assertRaisesRegex(ValueError, "scope_catalog_revision_conflict"):
            dashboardctl.upsert_scope(scope, expected_catalog_revision=3)
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_scope_upserts_with_same_revision_have_one_winner(self):
        barrier = threading.Barrier(2)
        def submit(scope_id):
            scope = {"id": scope_id, "name": scope_id, "status": "candidate", "in_scope": [], "out_of_scope": [],
                     "evidence": [{"type": "bug", "date": None, "reference": "fixture", "summary": "concurrent"}], "related_ids": []}
            barrier.wait()
            try:
                dashboardctl.upsert_scope(scope, expected_catalog_revision=4)
                return "saved"
            except ValueError as exc:
                if str(exc) == "scope_catalog_revision_conflict": return "conflict"
                raise
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, ["SCOPE-RACE-A", "SCOPE-RACE-B"]))
        catalog = json.loads((self.root / "scope-catalog.json").read_text(encoding="utf-8"))
        self.assertCountEqual(outcomes, ["saved", "conflict"])
        self.assertEqual(catalog["revision"], 5)
        self.assertEqual(len([item for item in catalog["scopes"] if item["id"].startswith("SCOPE-RACE-")]), 1)

    def test_scope_upsert_rejects_invalid_status_without_mutating_catalog(self):
        path = self.root / "scope-catalog.json"
        before = path.read_bytes()
        scope = {"id": "SCOPE-NEW", "name": "New", "status": "confirmed_by_guess", "in_scope": ["in"], "out_of_scope": [],
                 "evidence": [{"type": "user_instruction", "date": None, "reference": "fixture", "summary": "scoped"}], "related_ids": []}
        with self.assertRaisesRegex(ValueError, "invalid_scope_status"):
            dashboardctl.upsert_scope(scope, expected_catalog_revision=4)
        self.assertEqual(path.read_bytes(), before)

    def test_scope_upsert_rejects_missing_traceable_evidence(self):
        scope = {"id": "SCOPE-NEW", "name": "New", "status": "candidate", "in_scope": ["in"], "out_of_scope": [],
                 "evidence": [{"type": "user_instruction", "date": None, "reference": "", "summary": "scoped"}], "related_ids": []}
        with self.assertRaisesRegex(ValueError, "invalid_scope_evidence"):
            dashboardctl.upsert_scope(scope, expected_catalog_revision=4)

    def test_scope_upsert_increments_versions_and_preserves_other_scopes_and_candidate_status(self):
        scope = {"id": "SCOPE-EXISTING", "name": "Existing updated", "in_scope": ["new"], "out_of_scope": [],
                 "evidence": [{"type": "requirement", "date": None, "reference": "fixture-v2", "summary": "updated"}], "related_ids": ["REQ-1"]}
        result = dashboardctl.upsert_scope(scope, expected_catalog_revision=4)
        catalog = json.loads((self.root / "scope-catalog.json").read_text(encoding="utf-8"))
        updated = next(item for item in catalog["scopes"] if item["id"] == "SCOPE-EXISTING")
        preserved = next(item for item in catalog["scopes"] if item["id"] == "SCOPE-KEEP")
        self.assertEqual(result["revision"], 5)
        self.assertEqual(updated["version"], 3)
        self.assertEqual(updated["status"], "candidate")
        self.assertEqual(preserved["version"], 1)
        self.assertEqual(preserved["status"], "not_ours")

    def test_rebuild_context_bumps_cache_revisions_only_when_markdown_changes(self):
        before = dashboardctl.load_store()
        record_before = before["records"][0]["revision"]
        global_before = before["revision"]
        context_path = self.root / "records/task/T-1/context.md"
        context_path.write_text("# stale cached details\n", encoding="utf-8")
        rebuilt = dashboardctl.with_lock(lambda: backend.rebuild_context(self.root, self.root / "store.json"))
        after = dashboardctl.load_store()
        self.assertEqual(rebuilt["changed_records"], 1)
        self.assertEqual(after["records"][0]["revision"], record_before + 1)
        self.assertEqual(after["revision"], global_before + 1)
        self.assertIn("mcloud-context-v2", context_path.read_text(encoding="utf-8"))
        unchanged = dashboardctl.with_lock(lambda: backend.rebuild_context(self.root, self.root / "store.json"))
        final = dashboardctl.load_store()
        self.assertEqual(unchanged["changed_records"], 0)
        self.assertEqual(final["records"][0]["revision"], after["records"][0]["revision"])
        self.assertEqual(final["revision"], after["revision"])

    def test_explicit_migration_backups_and_integrity_manifest(self):
        other = Path(tempfile.mkdtemp())
        legacy = other / "store.json"
        original = {"schema_version": 1, "revision": 7, "records": [{"id": "W-1", "type": "work", "title": "w", "status": "unknown", "history": [{"a": 1}], "followups": [{"id": "f"}]}], "sources": [{"id": "S-1"}], "batches": [{"id": "B-1"}]}
        legacy.write_text(json.dumps(original), encoding="utf-8")
        manifest = backend.migration_plan(other, legacy)
        self.assertEqual(manifest["followup_count"], 1)
        self.assertFalse((other / "meta.json").exists())
        result = backend.migrate(other, legacy)
        self.assertTrue(result["migrated"])
        migrated_record = json.loads((other / "records/work/W-1/record.json").read_text())
        self.assertEqual({k: v for k, v in migrated_record.items() if k != "revision"}, original["records"][0])
        self.assertTrue((other / "store.json.migrated.bak").exists())
        import shutil; shutil.rmtree(other)

    def test_legacy_write_requires_explicit_migration(self):
        other = Path(tempfile.mkdtemp())
        (other / "store.json").write_text('{"records":[]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "explicit_migration_required"):
            backend.commit(other, other / "store.json", {"revision": 1, "records": []})
        import shutil; shutil.rmtree(other)

    def test_journal_replays_and_rejects_tampering(self):
        other = Path(tempfile.mkdtemp()); text = '{"revision":4}\n'
        (other / ".transaction.json").write_text(json.dumps({"writes": {"meta.json": text}, "sha256": {"meta.json": hashlib.sha256(text.encode()).hexdigest()}, "valid_records": []}), encoding="utf-8")
        backend.recover(other)
        self.assertEqual(json.loads((other / "meta.json").read_text())["revision"], 4)
        self.assertFalse((other / ".transaction.json").exists())
        (other / ".transaction.json").write_text(json.dumps({"writes": {"meta.json": text}, "sha256": {"meta.json": "bad"}, "valid_records": []}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "transaction_checksum_mismatch"): backend.recover(other)
        import shutil; shutil.rmtree(other)

    def test_source_lifecycle_human_override_survives_scanner_updates(self):
        source = dashboardctl.add_source_link("https://example.com/a", source_id="SRC-1")["source"]
        first = dashboardctl.set_source_lifecycle("SRC-1", {"lifecycle_status": "suspected_deprecated", "lifecycle_reason": "old evidence"})["source"]
        self.assertEqual(first["lifecycle_status"], "suspected_deprecated")
        dashboardctl.set_source_lifecycle("SRC-1", {"lifecycle_override": "active"})
        later = dashboardctl.set_source_lifecycle("SRC-1", {"lifecycle_status": "deprecated", "lifecycle_reason": "scanner"})["source"]
        self.assertEqual(later["lifecycle_status"], "registered")
        self.assertEqual(later["lifecycle_override"], "active")

    def test_source_read_state_is_persisted_and_lifecycle_independent(self):
        dashboardctl.add_source_link("https://example.com/spec", source_id="SRC-READ")
        fields = {"last_read_at": "2026-10-22T10:30:00+08:00", "read_state": "partial",
                  "read_scope": "tab:Requirements!A1:G120", "content_updated_at": None,
                  "read_gap": ["tab:History not read"], "snapshot_revision": "requirements-r17"}
        result = dashboardctl.set_source_read_state("SRC-READ", fields)
        self.assertEqual({key: result["source"][key] for key in fields}, fields)
        self.assertEqual(result["source"]["lifecycle_status"], "registered")
        with self.assertRaisesRegex(ValueError, "invalid_source_read_state"):
            dashboardctl.set_source_read_state("SRC-READ", {**fields, "read_state": "unknown"})

    def test_source_content_hash_age_requires_success_and_resets_on_change(self):
        dashboardctl.add_source_link("https://example.com/aging", source_id="SRC-AGE")
        first_at = "2026-07-01T10:00:00+08:00"
        hash_a = "a" * 64
        first = dashboardctl.set_source_read_state("SRC-AGE", {"last_read_at": first_at, "read_state": "success", "read_scope": "all tabs", "read_gap": [], "content_hash": hash_a, "content_updated_at": None})["source"]
        self.assertEqual(first["unchanged_since"], first_at)
        self.assertEqual(first["last_successful_read_at"], first_at)
        second_at = "2026-08-01T10:00:00+08:00"
        second = dashboardctl.set_source_read_state("SRC-AGE", {"last_read_at": second_at, "read_state": "success", "read_scope": "all tabs", "read_gap": [], "content_hash": hash_a, "content_updated_at": None})["source"]
        self.assertEqual(second["unchanged_since"], first_at)
        self.assertEqual(second["last_successful_read_at"], second_at)
        dashboardctl.set_source_lifecycle("SRC-AGE", {"lifecycle_status": "suspected_deprecated"})
        third_at = "2026-08-02T10:00:00+08:00"
        hash_b = "b" * 64
        changed = dashboardctl.set_source_read_state("SRC-AGE", {"last_read_at": third_at, "read_state": "success", "read_scope": "all tabs", "read_gap": [], "content_hash": hash_b, "content_updated_at": None, "last_recheck_at": third_at})["source"]
        self.assertEqual(changed["lifecycle_status"], "registered")
        self.assertEqual(changed["unchanged_since"], third_at)
        self.assertEqual(changed["last_recheck_at"], third_at)
        failed_at = "2026-08-03T10:00:00+08:00"
        failed = dashboardctl.set_source_read_state("SRC-AGE", {"last_read_at": failed_at, "read_state": "failed", "read_scope": "", "read_gap": "source unreachable", "content_hash": "c" * 64, "last_successful_read_at": failed_at, "unchanged_since": failed_at})["source"]
        self.assertEqual(failed["content_hash"], hash_b)
        self.assertEqual(failed["unchanged_since"], third_at)
        self.assertEqual(failed["last_successful_read_at"], third_at)

    def test_agent_owner_version_and_completion_guards(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-real"}):
            claimed = dashboardctl.agent_claim("T-1", 1, agent_name="dashboard_progress_fix")
            self.assertEqual(claimed["record"]["current_agent_session_id"], "thread-real")
            self.assertEqual(claimed["record"]["current_agent_name"], "dashboard_progress_fix")
            db = dashboardctl.load_store(); db["records"][0]["current_agent_session_id"] = "older-real-thread"
            (self.root / "records/task/T-1/record.json").write_text(json.dumps(db["records"][0]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "agent_owner_conflict"):
                dashboardctl.agent_claim("T-1", 2)
            db = dashboardctl.load_store(); db["records"][0]["current_agent_session_id"] = "thread-real"
            (self.root / "records/task/T-1/record.json").write_text(json.dumps(db["records"][0]), encoding="utf-8")
            dashboardctl.agent_report("T-1", 2, "done", status="completed")
            current = dashboardctl.load_store()["records"][0]
            self.assertIsNotNone(current["completed_at"])
            with self.assertRaisesRegex(ValueError, "record_completed"):
                dashboardctl.agent_heartbeat("T-1", 3)

    def test_agent_responses_separate_item_and_store_revisions(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-rev-check"}):
            # An unrelated write advances only the store revision for T-1.
            dashboardctl.add_source_link("https://example.com/revision", source_id="SRC-REV")
            before = dashboardctl.load_store()
            item_revision = before["records"][0]["revision"]
            store_revision = before["revision"]
            claimed = dashboardctl.agent_claim("T-1", item_revision, agent_name="revision_worker")
            self.assertEqual(claimed["revision"], claimed["record"]["revision"])
            self.assertGreater(claimed["store_revision"], store_revision)
            self.assertEqual(claimed["record"]["revision"], item_revision + 1)
            heartbeat = dashboardctl.agent_heartbeat("T-1", claimed["revision"])
            self.assertEqual(heartbeat["revision"], heartbeat["record"]["revision"])
            self.assertGreaterEqual(heartbeat["store_revision"], heartbeat["revision"])
            logged = json.loads((self.root / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(logged["record_revision"], heartbeat["record"]["revision"])
            self.assertEqual(logged["store_revision"], heartbeat["store_revision"])

    def test_agent_update_claims_renews_reports_and_releases_atomically(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-agent-update"}):
            updated = dashboardctl.agent_update("T-1", 1, "开始执行", agent_name="dashboard_progress_fix", phase="实现")
            self.assertEqual(updated["revision"], updated["record"]["revision"])
            self.assertEqual(updated["record"]["current_agent_session_id"], "thread-agent-update")
            self.assertEqual(updated["record"]["current_agent_name"], "dashboard_progress_fix")
            self.assertEqual(updated["record"]["progress"][-1]["kind"], "agent_update")
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-other-owner"}):
                with self.assertRaisesRegex(ValueError, "agent_owner_conflict"):
                    dashboardctl.agent_update("T-1", updated["revision"], "不能抢占")
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-agent-update"}):
                finished = dashboardctl.agent_update("T-1", updated["revision"], "已完成", status="completed")
            record = finished["record"]
            self.assertIsNone(record["current_agent_session_id"])
            self.assertEqual(record["last_agent_session_id"], "thread-agent-update")
            self.assertEqual(record["agent_state"], "completed")
            self.assertIsNotNone(record["completed_at"])
            self.assertEqual(finished["revision"], record["revision"])
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-agent-update"}):
                with self.assertRaisesRegex(ValueError, "record_completed"):
                    dashboardctl.agent_update("T-1", finished["revision"], "终态不可续活")

    def test_agent_update_can_take_expired_owner_but_not_fresh_owner(self):
        started = datetime.fromisoformat("2026-10-22T10:00:00+08:00")
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-old-owner"}), patch.object(dashboardctl, "timestamp", return_value=started.isoformat()):
            first = dashboardctl.agent_claim("T-1", 1, agent_name="old_worker")
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-new-owner"}), patch.object(dashboardctl, "has_fresh_agent_owner", return_value=True):
            with self.assertRaisesRegex(ValueError, "agent_owner_conflict"):
                dashboardctl.agent_update("T-1", first["revision"], "fresh owner protected")
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-new-owner"}), patch.object(dashboardctl, "timestamp", return_value=(started + timedelta(minutes=11)).isoformat()), patch.object(dashboardctl, "has_fresh_agent_owner", return_value=False):
            current = dashboardctl.agent_update("T-1", first["revision"], "expired task resumed", agent_name="new_worker")
            self.assertEqual(current["record"]["current_agent_session_id"], "thread-new-owner")
            self.assertEqual(current["record"]["last_agent_session_id"], "thread-old-owner")
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-old-owner"}), patch.object(dashboardctl, "has_fresh_agent_owner", return_value=True):
            with self.assertRaisesRegex(ValueError, "agent_owner_conflict"):
                dashboardctl.agent_update("T-1", current["revision"], "old owner rejected")

    def test_cli_agent_identity_stays_bound_to_its_own_thread(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-root-owner"}):
            with self.assertRaisesRegex(ValueError, "invalid_agent_session"):
                dashboardctl.agent_update("T-1", 1, "伪装子agent", session_id="thread-child-agent", agent_name="child")

    def test_agent_heartbeat_expiry_replacement_and_non_agent_activity(self):
        start = datetime.fromisoformat("2026-10-22T10:00:00+08:00")
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-owner-1"}), patch.object(dashboardctl, "timestamp", return_value=start.isoformat()):
            claimed = dashboardctl.agent_claim("T-1", 1, agent_name="worker_one")
        active = claimed["record"]
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-owner-1"}):
            heartbeat = dashboardctl.agent_heartbeat("T-1", active["revision"], now=start + timedelta(minutes=2))
            self.assertEqual(heartbeat["record"]["last_agent_activity_at"], (start + timedelta(minutes=2)).isoformat(timespec="seconds"))
            with self.assertRaisesRegex(ValueError, "agent_expired"):
                dashboardctl.agent_heartbeat("T-1", heartbeat["record"]["revision"], now=start + timedelta(minutes=12))
            expired = dashboardctl.load_store()["records"][0]
            self.assertIsNone(expired["current_agent_session_id"])
            self.assertIsNone(expired["current_agent_name"])
            self.assertEqual(expired["last_agent_name"], "worker_one")
            self.assertEqual(expired["agent_state"], "paused")
            reclaimed = dashboardctl.agent_claim("T-1", expired["revision"])
            self.assertEqual(reclaimed["record"]["current_agent_session_id"], "thread-owner-1")
            dashboardctl.agent_sweep(datetime.now().astimezone() + timedelta(minutes=10))
            expired_again = dashboardctl.load_store()["records"][0]
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-owner-2"}):
            replacement = dashboardctl.agent_claim("T-1", expired_again["revision"])
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-owner-1"}):
            with self.assertRaisesRegex(ValueError, "agent_owner_conflict"):
                dashboardctl.agent_report("T-1", replacement["record"]["revision"], "old owner write")

    def test_work_hours_preferences_persist_and_reject_invalid_ranges(self):
        saved = dashboardctl.set_work_hours("09:15", "18:00")
        self.assertEqual(saved["work_hours"], {"start": "09:15", "end": "18:00", "timezone": "Asia/Shanghai"})
        self.assertEqual(dashboardctl.load_store()["preferences"]["work_hours"], saved["work_hours"])
        for start, end in (("18:00", "09:00"), ("08:60", "09:00"), ("08:30", "08:30"), ("8:30", "17:30")):
            with self.assertRaisesRegex(ValueError, "invalid_work_hours"):
                dashboardctl.set_work_hours(start, end)

    def test_report_keeps_short_brief_separate_from_context_detail(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-report-owner"}):
            claimed = dashboardctl.agent_claim("T-1", 1)
            result = dashboardctl.agent_report("T-1", claimed["record"]["revision"], "短简报", context_markdown="# 详细报告\n\n很多证据")
        record = result["record"]
        self.assertEqual(record["latest_brief"], "短简报")
        self.assertEqual(record["progress"][-1]["message"], "短简报")
        self.assertEqual(record["progress"][-1]["context_markdown"], "# 详细报告\n\n很多证据")
        self.assertIn("很多证据", backend.read_context(self.root, "task", "T-1"))

    def test_followup_and_expired_sweep_do_not_extend_agent_activity(self):
        start = datetime.fromisoformat("2026-10-22T10:00:00+08:00")
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-owner-3"}), patch.object(dashboardctl, "timestamp", return_value=start.isoformat()):
            claimed = dashboardctl.agent_claim("T-1", 1)
        dashboardctl.add_followup("T-1", "用户批注")
        after = dashboardctl.load_store()["records"][0]
        self.assertEqual(after["last_agent_activity_at"], start.isoformat())
        swept = dashboardctl.agent_sweep(start + timedelta(minutes=10))
        self.assertEqual(swept["paused"], ["T-1"])
        held = dashboardctl.load_store()["records"][0]
        self.assertIsNone(held["current_agent_session_id"])
        self.assertEqual(held["last_agent_activity_at"], start.isoformat())

    def test_cancelled_agent_update_releases_owner_and_expiry_does_not_pause_terminal(self):
        start = datetime.fromisoformat("2026-10-22T10:00:00+08:00")
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-canceller"}), patch.object(dashboardctl, "timestamp", return_value=start.isoformat()):
            cancelled = dashboardctl.agent_update("T-1", 1, "用户取消该委托任务", status="cancelled", agent_name="cancel_worker")
        record = cancelled["record"]
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["completed_at"], start.isoformat())
        self.assertIsNone(record["current_agent_session_id"])
        self.assertEqual(record["last_agent_session_id"], "thread-canceller")
        self.assertEqual(record["agent_state"], "idle")
        swept = dashboardctl.agent_sweep(start + timedelta(minutes=30))
        self.assertEqual(swept["paused"], [])
        after = dashboardctl.load_store()["records"][0]
        self.assertEqual(after["status"], "cancelled")
        self.assertEqual(after["agent_state"], "idle")
        with self.assertRaisesRegex(ValueError, "record_completed"):
            dashboardctl.agent_claim("T-1", after["revision"], session_id="thread-canceller", explicit_caller=True)
        reopen = self.root / "reopen.json"
        reopen.write_text(json.dumps({"id": "T-1", "type": "task", "title": "task", "status": "in_progress"}), encoding="utf-8")
        with patch("sys.stdout", new_callable=__import__("io").StringIO):
            dashboardctl.cmd_upsert(__import__("argparse").Namespace(input=str(reopen), expected_store_revision=None))
        reopened = dashboardctl.load_store()["records"][0]
        self.assertEqual(reopened["status"], "in_progress")
        self.assertEqual(reopened["completed_at"], start.isoformat())

    def test_effective_status_and_completion_dates_use_evidence_only(self):
        now = datetime.fromisoformat("2026-10-22T12:00:00+08:00")
        record = {"id": "B", "type": "bug", "status": "blocked", "source_status": "QA 阻塞", "updated_at": now.isoformat()}
        self.assertEqual(dashboardctl.effective_work_status(record, now)[0], "on_hold")
        record.update(current_agent_session_id="owner", last_agent_activity_at=(now - timedelta(minutes=2)).isoformat())
        self.assertEqual(dashboardctl.effective_work_status(record, now)[0], "in_progress")
        self.assertIsNone(dashboardctl.completion_timestamp(record))
        record["history"] = [{"status": "completed", "at": "2026-10-21T09:30:00+08:00"}]
        self.assertIsNone(dashboardctl.completion_timestamp(record))
        record["history"] = [{"from_status": "in_progress", "to_status": "completed", "status": "completed", "at": "2026-10-21T09:30:00+08:00"}]
        self.assertEqual(dashboardctl.completion_timestamp(record), "2026-10-21T09:30:00+08:00")
        record["completed_at"] = "2026-10-20T10:00:00+08:00"
        self.assertEqual(dashboardctl.completion_timestamp(record), record["completed_at"])

    def test_context_renderer_formats_progress_and_preserves_unmapped_evidence(self):
        record = {"id": "BUG-2", "type": "bug", "title": "可读标题", "status": "in_progress",
                  "progress": [{"at": "2026-10-22T10:00:00+08:00", "actor": "thread-a", "status": "in_progress", "message": "修复已验证", "evidence": ["test.log"]}],
                  "legacy_platform_field": {"proof": "platform attachment id"}}
        markdown = backend.render_context(record)
        self.assertIn("### 2026-10-22T10:00:00+08:00 — thread-a", markdown)
        self.assertIn("修复已验证", markdown)
        self.assertIn('"proof": "platform attachment id"', markdown)
        self.assertNotIn('"actor": "thread-a"', markdown)

    def test_transfer_absence_requires_complete_successful_owned_list(self):
        dashboardctl.atomic_write({"schema_version": 1, "revision": 1, "records": [{"id": "BUG-X", "type": "bug", "title": "x", "status": "pending", "transfer_disposition": "transfer_pending", "transfer_target": "teammate", "transfer_requested_at": "2026-10-22T10:00:30+08:00"}]})
        state_path = self.root / "state.json"
        invalid = {"last_successful_snapshot_at": "2026-10-22T10:00:00+08:00", "stale": False, "bugs": [],
            "last_attempt": {"result": "partial", "stale": False, "pages_read": 1, "reported_total": 0, "unique_ids": 0, "filter": "我负责的", "verification": "matched self owner current-user; complete=false; count=0; uniqueIds=0; pages incomplete"}}
        state_path.write_text(json.dumps(invalid), encoding="utf-8")
        dashboardctl.cmd_sync_bugs(type("Args", (), {"state": str(state_path)})())
        row = dashboardctl.load_store()["records"][0]
        self.assertEqual(row["transfer_disposition"], "transfer_pending")
        self.assertEqual(row["status"], "pending")
        valid = {"last_successful_snapshot_at": "2026-10-22T10:00:00+08:00", "stale": False, "bugs": [],
            "last_attempt": {"result": "succeeded", "stale": False, "captured_at": "2026-10-22T10:00:00+08:00", "pages_read": 1, "reported_total": 0, "unique_ids": 0, "filter": "我负责的", "verification": "matched self owner current-user; complete=true; count=0; uniqueIds=0; all pages returned"}}
        state_path.write_text(json.dumps(valid), encoding="utf-8")
        dashboardctl.cmd_sync_bugs(type("Args", (), {"state": str(state_path)})())
        self.assertEqual(dashboardctl.load_store()["records"][0]["transfer_disposition"], "transfer_pending")
        valid["last_successful_snapshot_at"] = "2026-10-22T10:01:00+08:00"
        valid["last_attempt"]["captured_at"] = "2026-10-22T10:01:00+08:00"
        state_path.write_text(json.dumps(valid), encoding="utf-8")
        dashboardctl.cmd_sync_bugs(type("Args", (), {"state": str(state_path)})())
        row = dashboardctl.load_store()["records"][0]
        self.assertEqual(row["transfer_disposition"], "transferred")
        self.assertEqual(row["completion_kind"], "transferred")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(dashboardctl.effective_work_status(row)[0], "completed")
        self.assertIn("完整成功", row["history"][-1]["note"])


if __name__ == "__main__": unittest.main()
