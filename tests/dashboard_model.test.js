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

// Version ordering: explicit order wins; missing order falls back to creation time.
const iterations = [
  { id: "ITER-v1340", version_tag: "13.4.0", title: "13.4.0", status: "released", order: 0, created_at: "2026-10-15T00:00:00+08:00" },
  { id: "ITER-v1342", version_tag: "13.4.2 迭代二", title: "13.4.2 迭代二", status: "active", order: 1, created_at: "2026-11-05T00:00:00+08:00" },
  { id: "ITER-v1341", version_tag: "13.4.1", title: "13.4.1", status: "released", order: 2, created_at: "2026-10-20T00:00:00+08:00" }
];
assert.deepEqual(model.orderedIterations(iterations).map(it => it.id), ["ITER-v1341", "ITER-v1342", "ITER-v1340"], "newest order first");
const legacyIterations = [
  { id: "ITER-b", title: "B", created_at: "2026-11-02T00:00:00+08:00" },
  { id: "ITER-a", title: "A", created_at: "2026-11-01T00:00:00+08:00" }
];
assert.deepEqual(model.orderedIterations(legacyIterations).map(it => it.id), ["ITER-b", "ITER-a"], "legacy fallback is creation-time descending");

// One readable version name; version_tag is a compatibility field only.
assert.equal(model.iterationName({ id: "X", title: "13.4.2 迭代二", version_tag: "13.4.2 迭代二" }), "13.4.2 迭代二");
assert.equal(model.iterationSecondaryTag({ id: "X", title: "13.4.2 迭代二", version_tag: "13.4.2 迭代二" }), "");
assert.equal(model.iterationName({ id: "X", title: "版本标题", version_tag: "v1" }), "版本标题");
assert.equal(model.iterationSecondaryTag({ id: "X", title: "版本标题", version_tag: "v1" }), "v1");

// Work grouping: every version stays flat; tasks never join a version group.
const iterRecords = [
  { id: "work-1", type: "work", title: "需求1", status: "in_progress", iteration_id: "ITER-v1342" },
  { id: "work-2", type: "work", title: "需求2(已完成但未发布)", status: "completed", iteration_id: "ITER-v1342" },
  { id: "work-3", type: "work", title: "老需求", status: "completed", iteration_id: "ITER-v1340" },
  { id: "work-backlog", type: "work", title: "待规划需求", status: "in_progress", iteration_id: null },
  { id: "task-should-not-group", type: "task", title: "委托任务", status: "completed", iteration_id: "ITER-v1342" }
];

const iterPartition = model.partitionIterationRecords(iterRecords, iterations);
assert.deepEqual(iterPartition.groups.map(group => group.iteration.id), ["ITER-v1341", "ITER-v1342", "ITER-v1340"]);
assert.deepEqual(iterPartition.activeGroups.map(group => group.iteration.id), ["ITER-v1342"]);
assert.deepEqual(iterPartition.releasedGroups.map(group => group.iteration.id), ["ITER-v1341", "ITER-v1340"]);

const activeGroup = iterPartition.groups.find(group => group.iteration.id === "ITER-v1342");
assert.equal(activeGroup.visibleRecords.length, 2, "Both in-progress and completed records remain visible before release");
assert.deepEqual(activeGroup.visibleRecords.map(r => r.id), ["work-1", "work-2"]);
assert.equal(activeGroup.foldedRecords.length, 0, "Nothing is folded in an unreleased version");

const releasedGroup = iterPartition.groups.find(group => group.iteration.id === "ITER-v1340");
assert.equal(releasedGroup.visibleRecords.length, 0);
assert.equal(releasedGroup.foldedRecords.length, 1);
assert.equal(releasedGroup.foldedRecords[0].id, "work-3");

assert.equal(iterPartition.backlog.unfinished.length, 1);
assert.equal(iterPartition.backlog.unfinished[0].id, "work-backlog");
assert.ok(!JSON.stringify(iterPartition).includes("task-should-not-group"), "Delegated tasks must not participate in version grouping");

// Archive matrix: strict isolation between versions and public material.
const sources = [
  { id: "s-global-api", scope: "global", slot: "api_doc", name: "全局接口文档", active: true },
  { id: "s-global-supp", scope: "global", slot: "supplementary", name: "公共补充", active: true },
  { id: "s-1342-prd", scope: "iteration", iteration_id: "ITER-v1342", slot: "requirement_doc", name: "13.4.2 PRD", active: true },
  { id: "s-1342-codesign", scope: "iteration", iteration_id: "ITER-v1342", slot: "codesign", name: "CoDesign原型", active: true },
  { id: "s-1342-qa", scope: "iteration", iteration_id: "ITER-v1342", slot: "testcase", name: "13.4.2 测试用例", active: true },
  { id: "s-1342-supp", scope: "iteration", iteration_id: "ITER-v1342", slot: "supplementary", name: "PM截图", active: true, attachments: [{ filename: "chat.png", download_url: "/x" }] },
  { id: "s-1340-figma", scope: "iteration", iteration_id: "ITER-v1340", slot: "figma", name: "13.4.0 Figma", active: true }
];

const archive = model.archiveView(sources, iterations);
assert.deepEqual(archive.iterations.map(section => section.iteration.id), ["ITER-v1341", "ITER-v1342", "ITER-v1340"]);
assert.equal(archive.global.slots.find(slot => slot.slot === "api_doc").card.name, "全局接口文档");
const section1342 = archive.iterations.find(section => section.iteration.id === "ITER-v1342");
const section1340 = archive.iterations.find(section => section.iteration.id === "ITER-v1340");
assert.equal(section1342.slots.find(slot => slot.slot === "requirement_doc").card.name, "13.4.2 PRD");
const protoSlot = section1342.slots.find(slot => slot.slot === "prototype");
assert.ok(protoSlot && protoSlot.card);
assert.equal(protoSlot.card.name, "CoDesign原型");
const testcaseSlot = section1342.slots.find(slot => slot.slot === "testcase");
assert.ok(testcaseSlot && testcaseSlot.card, "testcase must occupy its own fixed slot");
assert.equal(testcaseSlot.card.name, "13.4.2 测试用例");

// Isolation: a version's materials never appear in another version or in public material.
const names1340 = [...section1340.groups.map(g => g.card.name), ...section1340.supplementaryGroups.map(g => g.card.name)];
assert.ok(!names1340.includes("13.4.2 PRD"));
assert.ok(!names1340.includes("PM截图"));
const publicNames = archive.global.groups.map(group => group.card.name);
assert.ok(!publicNames.includes("13.4.2 PRD"));
assert.ok(!publicNames.includes("13.4.0 Figma"));

// The group that owns a fixed slot is not repeated as supplementary material.
assert.deepEqual(section1342.supplementaryGroups.map(group => group.card.name), ["PM截图"]);
assert.equal(section1342.supplementaryCount, 2); // PM截图 + its attachment
assert.equal(archive.global.supplementaryGroups.map(group => group.card.name).join(","), "公共补充");

// A group whose versions sit in different versions is flagged for cleanup; each
// version still only sees its own members.
const splitArchive = model.archiveView([
  { id: "split-a", group_id: "split-group", scope: "iteration", iteration_id: "ITER-v1342", slot: "supplementary", name: "分工表 v1", active: true },
  { id: "split-b", group_id: "split-group", scope: "iteration", iteration_id: "ITER-v1340", slot: "supplementary", name: "分工表 v2", active: true }
], iterations);
assert.ok(splitArchive.mixedGroups.has("split-group"));
const split1342 = splitArchive.iterations.find(section => section.iteration.id === "ITER-v1342");
const split1340 = splitArchive.iterations.find(section => section.iteration.id === "ITER-v1340");
assert.deepEqual(split1342.supplementaryGroups.flatMap(group => group.versions.map(v => v.id)), ["split-a"]);
assert.deepEqual(split1340.supplementaryGroups.flatMap(group => group.versions.map(v => v.id)), ["split-b"]);

// Simulated whole-group move interaction: the view must relocate the card
// immediately, and the old version must no longer render it anywhere.
const movedSources = sources.map(source => source.id === "s-1340-figma"
  ? { ...source, iteration_id: "ITER-v1342" }
  : source);
const movedArchive = model.archiveView(movedSources, iterations);
const moved1342 = movedArchive.iterations.find(section => section.iteration.id === "ITER-v1342");
const moved1340 = movedArchive.iterations.find(section => section.iteration.id === "ITER-v1340");
assert.equal(moved1342.slots.find(slot => slot.slot === "figma").card.name, "13.4.0 Figma");
assert.equal(moved1340.slots.find(slot => slot.slot === "figma").empty, true);
assert.ok(!moved1340.groups.some(group => group.card.name === "13.4.0 Figma"));
assert.ok(!moved1340.supplementaryGroups.some(group => group.card.name === "13.4.0 Figma"));

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
