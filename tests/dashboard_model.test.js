// Created by Codex on 2026/10/28.
// Copyright © 2026 CMIC. All rights reserved.
const assert = require("node:assert/strict");
const model = require("../web/assets/dashboard-model.js");

const records = [
  { id: "cancel-today", status: "cancelled", completed_at: "2026-10-28T14:23:49+08:00" },
  { id: "done-today-utc", status: "completed", completed_at: "2026-10-28T15:30:00Z" },
  { id: "cancel-yesterday", status: "cancelled", completed_at: "2026-10-27T15:30:00+08:00" }
];
const partition = model.partitionTerminalRecords(records, "2026-10-28");
assert.deepEqual(partition.todayRecords.map(item => item.id), ["cancel-today", "done-today-utc"]);
assert.equal(partition.groups.length, 1);
assert.equal(partition.groups[0].date, "2026-10-27");

const taskPartition = model.partitionTaskBatches(
  [{ id: "task-cancelled", effective_status: "cancelled", batch_id: null }], [], null, ["task-cancelled"]
);
assert.equal(taskPartition.legacyDone[0].id, "task-cancelled");
assert.deepEqual(taskPartition.legacyOpen, []);
console.log("dashboard model cancellation date checks passed");

// Test partitionIterationRecords zero-collapse rule
const iterations = [
  { id: "ITER-v1342", version_tag: "v13.4.2", title: "13.4.2迭代", status: "active" },
  { id: "ITER-v1341", version_tag: "v13.4.1", title: "13.4.1迭代", status: "released" }
];
const iterRecords = [
  { id: "task-1", title: "需求1", status: "in_progress", iteration_id: "ITER-v1342" },
  { id: "task-2", title: "需求2(已完成但未发布)", status: "completed", iteration_id: "ITER-v1342" },
  { id: "task-3", title: "老需求", status: "completed", iteration_id: "ITER-v1341" },
  { id: "task-backlog", title: "待规划需求", status: "in_progress", iteration_id: null }
];

const iterPartition = model.partitionIterationRecords(iterRecords, iterations, "ITER-v1342");
assert.equal(iterPartition.activeIterations.length, 1);
assert.equal(iterPartition.releasedIterations.length, 1);

// Active iteration: ZERO-COLLAPSE RULE
const activeGroup = iterPartition.activeIterations[0];
assert.equal(activeGroup.iteration.id, "ITER-v1342");
assert.equal(activeGroup.visibleRecords.length, 2, "Both in-progress and completed tasks MUST remain visible before release");
assert.deepEqual(activeGroup.visibleRecords.map(r => r.id), ["task-1", "task-2"]);
assert.equal(activeGroup.foldedRecords.length, 0, "No tasks folded in unreleased iteration");

// Released iteration: folded
const releasedGroup = iterPartition.releasedIterations[0];
assert.equal(releasedGroup.iteration.id, "ITER-v1341");
assert.equal(releasedGroup.visibleRecords.length, 0);
assert.equal(releasedGroup.foldedRecords.length, 1);
assert.equal(releasedGroup.foldedRecords[0].id, "task-3");

// Backlog
assert.equal(iterPartition.backlog.unfinished.length, 1);
assert.equal(iterPartition.backlog.unfinished[0].id, "task-backlog");

// Test archiveView
const sources = [
  { id: "s-global-api", scope: "global", slot: "api_doc", name: "全局接口文档", active: true },
  { id: "s-iter-prd", scope: "iteration", iteration_id: "ITER-v1342", slot: "requirement_doc", name: "13.4.2 PRD", active: true },
  { id: "s-iter-codesign", scope: "iteration", iteration_id: "ITER-v1342", slot: "codesign", name: "CoDesign原型", active: true },
  { id: "s-iter-supp", scope: "iteration", iteration_id: "ITER-v1342", slot: "supplementary", name: "PM截图", attachments: [{ filename: "chat.png" }], active: true }
];

const archive = model.archiveView(sources, iterations, "ITER-v1342");
assert.equal(archive.currentIteration.id, "ITER-v1342");
assert.equal(archive.globalSlots.find(s => s.slot === "api_doc").card.name, "全局接口文档");
assert.equal(archive.iterationSlots.find(s => s.slot === "requirement_doc").card.name, "13.4.2 PRD");
// Check prototype slot correctly aliases codesign
const protoSlot = archive.iterationSlots.find(s => s.slot === "prototype");
assert.ok(protoSlot && protoSlot.card);
assert.equal(protoSlot.card.name, "CoDesign原型");
// Check supplementary items
assert.equal(archive.supplementaryItems.length, 2); // s-iter-supp + chat.png

// Test workflowView reopen
const reopenedRecord = {
  id: "ZT-T-001",
  type: "task",
  reopen_count: 2,
  reopen_history: [
    { round: 1, reason: "微调", audit_mode: "lightweight" },
    { round: 2, reason: "再次微调", audit_mode: "lightweight" }
  ],
  workflow: {
    stage: "development",
    stage_status: "active",
    reopen_count: 2,
    audit_mode: "lightweight",
    reopen_history: [
      { round: 1, reason: "微调", audit_mode: "lightweight" },
      { round: 2, reason: "再次微调", audit_mode: "lightweight" }
    ],
    history: [
      { at: "2026-11-19T10:00:00+08:00", action: "reopen", stage: "development", actor: "dev", note: "发起二次修改" }
    ]
  }
};

const wfView = model.workflowView(reopenedRecord, { workflow: reopenedRecord.workflow });
assert.equal(wfView.reopenCount, 2);
assert.equal(wfView.auditMode, "lightweight");
assert.equal(wfView.history[0].actionLabel, "发起二次修改");

console.log("all archive and zero-collapse iteration tests passed!");
