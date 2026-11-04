// Created by Codex on 2026/10/22.
// Copyright © 2026 CMIC. All rights reserved.
const assert = require("node:assert/strict");
const test = require("node:test");
const { groupCompletedByDate, partitionCompletedRecords, partitionTaskBatches, shortestMasonryColumn, sortTaskRecords, summarizeFollowups, workPeriodProgress } = require("../assets/dashboard-model.js");

test("masonry always adds the next card to the shorter column", () => {
  assert.equal(shortestMasonryColumn([120, 80]), 1);
  assert.equal(shortestMasonryColumn([80, 120]), 0);
  assert.equal(shortestMasonryColumn([80, 80]), 0);
  assert.equal(shortestMasonryColumn([80]), 0);
});

test("completion grouping uses Beijing day boundaries and keeps unknown dates separate", () => {
  const rows = [
    { id: "today", completed_at: "2026-10-22T15:59:00Z" }, // 23:59 Beijing on 2026-10-22
    { id: "yesterday", completed_at: "2026-10-21T15:59:00Z" }, // 23:59 Beijing on 2026-10-21
    { id: "unknown", completed_at: null }
  ];
  const groups = groupCompletedByDate(rows, "2026-10-22");
  assert.deepEqual(groups.map(group => [group.date, group.records.map(row => row.id), group.open]), [
    ["2026-10-22", ["today"], true],
    ["2026-10-21", ["yesterday"], false],
    ["unknown", ["unknown"], false]
  ]);
});

test("completed task in current batch stays current while previous batch is historical", () => {
  const tasks = [
    { id: "done-current", batch_id: "batch-current", effective_status: "completed" },
    { id: "old", batch_id: "batch-old", effective_status: "completed" }
  ];
  const batches = [
    { id: "batch-current", state: "current", task_ids: ["done-current"] },
    { id: "batch-old", state: "past", task_ids: ["old"] }
  ];
  const result = partitionTaskBatches(tasks, batches, "batch-current");
  assert.deepEqual(result.currentTasks.map(task => task.id), ["done-current"]);
  assert.deepEqual(result.currentBatchTasks.map(task => task.id), ["done-current"]);
  assert.equal(result.currentBatchTasks.every(task => task.effective_status === "completed"), true);
  assert.deepEqual(result.pastBatches.map(group => [group.batch.id, group.records.map(task => task.id)]), [["batch-old", ["old"]]]);
});


test("today's work and bugs stay active while older and unknown completions fold", () => {
  const rows = [
    { id: "today", completed_at: "2026-10-23T15:00:00Z" },
    { id: "yesterday", completed_at: "2026-10-22T15:59:00Z" },
    { id: "unknown", completed_at: null }
  ];
  const partition = partitionCompletedRecords(rows, "2026-10-23");
  assert.deepEqual(partition.todayRecords.map(row => row.id), ["today"]);
  assert.deepEqual(partition.groups.map(group => [group.date, group.records.map(row => row.id), group.open]), [["2026-10-22", ["yesterday"], true], ["unknown", ["unknown"], false]]);
  assert.equal(partition.outerFoldHidden, false);
  const todayOnly = partitionCompletedRecords(rows.slice(0, 1), "2026-10-23");
  assert.deepEqual(todayOnly.todayRecords.map(row => row.id), ["today"]);
  assert.equal(todayOnly.outerFoldHidden, true);
});

test("worktime progress respects Shanghai boundaries and custom hours", () => {
  const pct = (time, start = "08:30", end = "17:30") => workPeriodProgress(Date.parse(time), start, end);
  assert.equal(pct("2026-10-23T00:29:59Z"), 0);
  assert.equal(pct("2026-10-23T00:30:00Z"), 0);
  assert.equal(pct("2026-10-23T05:00:00Z"), 50);
  assert.equal(pct("2026-10-23T09:30:00Z"), 100);
  assert.equal(pct("2026-10-23T10:00:00Z"), 100);
  assert.equal(pct("2026-10-23T05:37:30Z", "09:15", "18:00"), 50);
  assert.throws(() => workPeriodProgress(Date.parse("2026-10-23T04:00:00Z"), "18:00", "08:30"), /invalid_work_hours/);
});

test("task ordering prioritizes active work, then stable creation time, with completed last", () => {
  const rows = [
    { id: "done", effective_status: "completed", created_at: "2026-10-22", updated_at: "2026-10-30" },
    { id: "hold-old", effective_status: "on_hold", created_at: "2026-10-20", updated_at: "2026-10-29" },
    { id: "active-old", effective_status: "in_progress", created_at: "2026-10-19" },
    { id: "active-new", effective_status: "in_progress", created_at: "2026-10-21" }
  ];
  assert.deepEqual(sortTaskRecords(rows).map(row => row.id), ["active-new", "active-old", "hold-old", "done"]);
});

test("revision refresh keeps previous Markdown as placeholder and restores the current scroll position after async fill", () => {
  const { contextPlaceholder, replaceContextPreservingScroll } = require("../assets/dashboard-model.js");
  const prior = { revision: 3, markdown: "# Previous context\n\nLong body" };
  assert.deepEqual(contextPlaceholder(prior, 4), { markdown: prior.markdown, isCurrent: false });
  assert.equal(contextPlaceholder(null, 4), null);
  const detail = { scrollTop: 420 };
  const target = { children: ["old"], replaceChildren() { this.children = []; } };
  replaceContextPreservingScroll(detail, target, "# Updated context", (node, markdown) => {
    node.children.push(markdown);
    detail.scrollTop = 0; // Simulate browser clamping while the content is replaced.
  });
  assert.equal(target.children[0], "# Updated context");
  assert.equal(detail.scrollTop, 420);
  detail.scrollTop = 615; // A later fill uses the user's latest scroll, not the earlier position.
  replaceContextPreservingScroll(detail, target, "# Latest context", (node, markdown) => node.children.push(markdown));
  assert.equal(detail.scrollTop, 615);
});

test("active followup summary counts pending and processing and selects the newest active prompt", () => {
  const result = summarizeFollowups([
    { status: "pending", created_at: "2026-10-26T09:00:00+08:00", prompt: "Older correction" },
    { status: "processing", created_at: "2026-10-26T10:00:00+08:00", prompt: "Current correction" },
    { status: "handled", created_at: "2026-10-26T11:00:00+08:00", prompt: "Already handled" }
  ]);
  assert.deepEqual({ activeCount: result.activeCount, pendingCount: result.pendingCount, processingCount: result.processingCount }, { activeCount: 2, pendingCount: 1, processingCount: 1 });
  assert.equal(result.latestActive.prompt, "Current correction");
  assert.equal(summarizeFollowups([{ status: "handled" }]).latestActive, null);
});
