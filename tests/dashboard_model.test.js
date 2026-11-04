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
