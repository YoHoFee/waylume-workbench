// Created by Codex on 2026/10/22.
// Copyright © 2026 CMIC. All rights reserved.
const assert = require("node:assert/strict");
const test = require("node:test");
const { groupCompletedByDate, libraryView, librarySlots, partitionCompletedRecords, partitionTaskBatches, shortestMasonryColumn, sortTaskRecords, summarizeFollowups, workPeriodProgress, workflowView, workflowChip } = require("../assets/dashboard-model.js");

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

test("transferred responsibility records fold into completed section even when completed today", () => {
  const rows = [
    { id: "transferred", completed_at: "2026-10-23T15:00:00Z", transfer_disposition: "transferred" },
    { id: "today", completed_at: "2026-10-23T15:00:00Z" }
  ];
  const partition = partitionCompletedRecords(rows, "2026-10-23");
  assert.deepEqual(partition.todayRecords.map(row => row.id), ["today"]);
  assert.deepEqual(partition.groups.map(group => group.records.map(row => row.id)), [["transferred"]]);
  assert.equal(partition.outerFoldHidden, false);
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
    { id: "active-old", effective_status: "developing", created_at: "2026-10-19" },
    { id: "active-new", effective_status: "testing", created_at: "2026-10-21" }
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


// 六阶段流水线：视图模型只根据列表摘要 + 详情 payload 计算，缺数据时保持未知。
const stages = ["intake", "requirement", "development", "audit_response", "acceptance", "delivery"];
function detailFor(stage, status, gates = {}, history = [], rework = 0) {
  return {
    record_revision: 12,
    workflow: { template_id: "dev-standard", template_version: 1, stage, stage_status: status, rework_count: rework, gates, history },
    template: null
  };
}

test("records without a workflow stay disabled and never invent a stage", () => {
  const legacy = workflowView({ id: "legacy", status: "in_progress" }, null);
  assert.equal(legacy.enabled, false);
  assert.equal(legacy.stageId, null);
  assert.equal(legacy.current, null);
  assert.deepEqual(legacy.steps, []);
  assert.equal(legacy.detailState, "pending");

  const confirmedLegacy = workflowView({ id: "legacy" }, { workflow: null, template: { id: "dev-standard", stages: [] } });
  assert.equal(confirmedLegacy.enabled, false);
  assert.equal(confirmedLegacy.detailState, "loaded");
  assert.equal(confirmedLegacy.stageCount, 6);
});

test("active stage highlights the current step and gates required outputs", () => {
  const detail = detailFor("development", "active", {
    development: { status: "active", actor: "gemini", role: "dev", at: "2026-11-12T12:00:00+08:00", note: "实现完成", evidence: ["branch"],
      outputs: { dev_summary: "实现完成", branch: "local/pipeline", self_check: "" } }
  });
  const view = workflowView({ id: "x", workflow_stage: "development", workflow_stage_status: "active" }, detail);
  assert.equal(view.enabled, true);
  assert.equal(view.stageIndex, 3);
  assert.equal(view.stageStatus, "active");
  assert.deepEqual(view.steps.map(step => step.status), ["done", "done", "active", "pending", "pending", "pending"]);
  assert.equal(view.current.id, "development");
  assert.equal(view.current.owner, "Dev");
  assert.equal(view.current.nextLabel, "审计");
  assert.deepEqual(view.current.checklist.map(item => [item.key, item.done]), [["dev_summary", true], ["branch", true], ["self_check", false]]);
  assert.equal(view.current.note, "实现完成");
  assert.deepEqual(view.current.evidence, ["branch"]);
  assert.equal(view.summaryOnly, false);
});

test("blocked and rework stages expose their own status without losing the stage index", () => {
  const blocked = workflowView({ id: "b", workflow_stage: "requirement", workflow_stage_status: "blocked" },
    detailFor("requirement", "blocked", { requirement: { status: "blocked", note: "等待 PM 结论" } }));
  assert.equal(blocked.blocked, true);
  assert.equal(blocked.statusLabel, "已阻塞");
  assert.equal(blocked.steps[1].status, "blocked");
  assert.equal(blocked.steps[1].statusLabel, "已阻塞");

  const rework = workflowView({ id: "r", workflow_stage: "development", workflow_stage_status: "rework" },
    detailFor("development", "rework", {}, [], 2));
  assert.equal(rework.reworkCount, 2);
  assert.equal(rework.stageStatus, "rework");
  assert.equal(rework.steps[2].status, "rework");
  assert.equal(rework.steps[2].statusLabel, "已退回");
});

test("stage 4 exposes 4a static audit and 4b dev response substeps", () => {
  const detail = detailFor("audit_response", "active", {
    audit_response: { status: "active", note: "审计完成，待开发回应", outputs: { audit_report: "ADVISORY 报告" } }
  });
  const view = workflowView({ id: "a", workflow_stage: "audit_response" }, detail);
  const substeps = view.steps[3].substeps;
  assert.deepEqual(substeps.map(step => [step.indexLabel, step.label, step.status]), [
    ["4a", "静态审计", "done"],
    ["4b", "开发回应", "active"]
  ]);
  assert.deepEqual(substeps.map(step => step.roleLabel), ["Auditor", "Dev"]);
  assert.equal(view.current.nextLabel, "验收");
});

test("stage 2 exposes requirement understanding and the PM decision as 2a/2b", () => {
  const substeps = workflowView({ workflow_stage: "requirement" }, detailFor("requirement", "active")).steps[1].substeps;
  assert.deepEqual(substeps.map(step => [step.indexLabel, step.label]), [["2a", "需求理解"], ["2b", "PM结论"]]);
  assert.deepEqual(substeps.map(step => step.status), ["active", "pending"]);
});

test("an unavailable workflow endpoint degrades to the list summary without fabricated outputs", () => {
  const view = workflowView({ id: "d", workflow_stage: "acceptance", workflow_stage_status: "active", workflow_rework_count: 1 }, { unavailable: true });
  assert.equal(view.enabled, true);
  assert.equal(view.detailState, "unavailable");
  assert.equal(view.stageIndex, 5);
  assert.equal(view.current.checklist.every(item => item.done === null), true);
  assert.equal(view.historyCount, 0);
  assert.equal(view.summaryOnly, true);
});

test("card stage chip renders only for records that carry a workflow stage", () => {
  assert.equal(workflowChip({ id: "legacy", status: "in_progress" }), null);
  assert.deepEqual(workflowChip({ workflow_stage: "audit_response", workflow_stage_short: "审计", workflow_stage_status: "blocked" }), {
    stageId: "audit_response", label: "审计", status: "blocked", statusLabel: "已阻塞", title: "流水线：静态审计与开发回应 · 已阻塞"
  });
  assert.equal(workflowChip({ workflow_stage: "intake" }).label, "接单");
});

test("active and expired stage locks are exposed without inventing ownership", () => {
  const activeDetail = detailFor("development", "active");
  activeDetail.workflow.lock = { stage: "development", actor: "gemini", role: "dev", expires_at: "2099-01-01T00:00:00+08:00" };
  const active = workflowView({ workflow_stage: "development" }, activeDetail);
  assert.equal(active.lock.active, true);
  assert.equal(active.lock.actor, "gemini");
  assert.equal(active.lock.roleLabel, "Dev");
  assert.equal(active.current.lock.actor, "gemini");

  const expiredDetail = detailFor("development", "active");
  expiredDetail.workflow.lock = { stage: "development", actor: "gemini", role: "dev", expires_at: "2000-01-01T00:00:00+08:00" };
  const expired = workflowView({ workflow_stage: "development" }, expiredDetail);
  assert.equal(expired.lock.active, false);
  assert.equal(expired.lock.expired, true);

  const summaryOnly = workflowView({
    workflow_stage: "development",
    workflow: { lock: { stage: "development", actor: "gemini", role: "dev", expires_at: "2099-01-01T00:00:00+08:00" } }
  }, null);
  assert.equal(summaryOnly.lock.actor, "gemini");
  assert.equal(summaryOnly.summaryOnly, true);
});

test("workflow history keeps time, action, actor, role, note and evidence", () => {
  const history = workflowView({ workflow_stage: "development" }, detailFor("development", "active", {}, [{
    at: "2026-11-12T11:00:00+08:00", action: "advance", stage: "requirement", actor: "xiao-zhi", role: "pm", note: "需求已确认", evidence: ["brief.md"]
  }])).history;
  assert.deepEqual(history, [{
    at: "2026-11-12T11:00:00+08:00", action: "advance", actionLabel: "推进阶段", stage: "requirement", stageLabel: "需求分析与PM结论",
    actor: "xiao-zhi", role: "pm", roleLabel: "PM", note: "需求已确认", evidence: ["brief.md"]
  }]);
});


test("rework is also detected from gate status or the latest history entry", () => {
  const fromHistory = workflowView({ workflow_stage: "development" }, detailFor("development", "active", {}, [
    { at: "2026-11-12T11:00:00+08:00", action: "rework", stage: "development", actor: "xiao-zhi", role: "pm", note: "补自检", evidence: ["review.md"] }
  ], 2));
  assert.equal(fromHistory.stageStatus, "rework");
  assert.equal(fromHistory.steps[2].status, "rework");
  assert.equal(fromHistory.reworkCount, 2);

  const fromGate = workflowView({ workflow_stage: "development" }, detailFor("development", "active", { development: { status: "rework" } }));
  assert.equal(fromGate.current.status, "rework");

  const afterSubmit = workflowView({ workflow_stage: "development" }, detailFor("development", "active", {}, [
    { at: "2026-11-12T11:00:00+08:00", action: "rework", stage: "development", actor: "xiao-zhi", role: "pm" },
    { at: "2026-11-12T12:00:00+08:00", action: "submit", stage: "development", actor: "gemini", role: "dev" }
  ], 1));
  assert.equal(afterSubmit.stageStatus, "active");
});


test("backend template output labels and substep short labels are preferred", () => {
  const template = { id: "dev-standard", stages: [
    { id: "intake", label: "接单/立项", short: "接单", submit_roles: ["lead"], required_outputs: ["scope"], next: "requirement" },
    { id: "requirement", label: "需求分析与PM结论", short: "需求", submit_roles: ["pm"], required_outputs: ["requirement_brief"], next: "development",
      output_labels: { requirement_brief: "需求理解" },
      substeps: [{ id: "understanding", label: "需求理解/双跑差异", role: "pm", output: "requirement_brief" }] }
  ] };
  const view = workflowView({ workflow_stage: "requirement" }, {
    workflow: { stage: "requirement", stage_status: "active", gates: {} }, template
  });
  assert.equal(view.stepCount === undefined, true);
  assert.equal(view.steps.length, 2);
  assert.equal(view.steps[1].checklist[0].label, "需求理解");
  assert.equal(view.steps[1].substeps[0].label, "需求理解/双跑差异");
  assert.equal(view.steps[1].substeps[0].short, "需求理解");
});

// 资料库：固定四位始终存在，每个位置取版本组的最新版；其他来源不丢数据。
test("library keeps four fixed slots and picks each group's latest version", () => {
  assert.deepEqual(librarySlots.map(slot => [slot.id, slot.label]), [
    ["requirement_doc", "需求文档"], ["figma", "Figma"], ["codesign", "CoDesign"], ["api_doc", "接口文档"]
  ]);
  const sources = [
    { id: "req-1", active: true, slot: "requirement_doc", group_id: "req", version_no: 1, added_at: "2026-11-01T09:00:00+08:00", inspection_enabled: true },
    { id: "req-2", active: true, slot: "requirement_doc", group_id: "req", version_no: 2, added_at: "2026-11-02T09:00:00+08:00", inspection_enabled: true },
    { id: "req-3", active: true, slot: "requirement_doc", group_id: "req", version_no: 3, added_at: "2026-11-03T09:00:00+08:00", inspection_enabled: true },
    { id: "fig-1", active: true, slot: "figma", group_id: "fig", version_no: 1, added_at: "2026-11-01T09:00:00+08:00" },
    { id: "api-1", active: true, slot: "api_doc", group_id: "api", version_no: 1, added_at: "2026-11-01T09:00:00+08:00" }
  ];
  const view = libraryView(sources);
  assert.deepEqual(view.slots.map(slot => slot.slot), ["requirement_doc", "figma", "codesign", "api_doc"]);
  assert.equal(view.slots[0].card.id, "req-3");
  assert.equal(view.slots[0].version_count, 3);
  assert.equal(view.slots[0].history_count, 2);
  assert.deepEqual(view.slots[0].versions.map(source => source.id), ["req-3", "req-2", "req-1"]);
  assert.deepEqual(view.slots[0].versions.map(source => source.is_latest), [true, false, false]);
  assert.deepEqual(view.slots[0].versions.map(source => source.version_label), ["v3", "v2", "v1"]);
  assert.equal(view.slots[1].card.id, "fig-1");
  assert.equal(view.slots[2].empty, true);
  assert.equal(view.slots[3].card.id, "api-1");
  assert.equal(view.slots[2].versions.length, 0);
  assert.equal(view.activeCount, 5);
});

test("library classifies non-fixed groups as other sources without duplicating history", () => {
  const sources = [
    { id: "fig-old", active: true, slot: "figma", group_id: "fig-a", version_no: 1, added_at: "2026-11-01T09:00:00+08:00" },
    { id: "fig-new", active: true, slot: "figma", group_id: "fig-a", version_no: 2, added_at: "2026-11-02T09:00:00+08:00" },
    { id: "fig-other", active: true, slot: "figma", group_id: "fig-b", version_no: 1, added_at: "2026-11-03T09:00:00+08:00" },
    { id: "misc", active: true, group_id: "misc", version_no: 1, added_at: "2026-11-04T09:00:00+08:00" }
  ];
  const view = libraryView(sources);
  assert.equal(view.slots[1].card.id, "fig-other");
  assert.deepEqual(view.others.map(group => group.group_id).sort(), ["fig-a", "misc"]);
  const figA = view.others.find(group => group.group_id === "fig-a");
  assert.equal(figA.card.id, "fig-new");
  assert.deepEqual(figA.versions.map(source => source.id), ["fig-new", "fig-old"]);
  assert.equal(figA.history_count, 1);
  assert.equal(view.others.find(group => group.group_id === "misc").slot, null);
});

test("library inspection includes requirement latest and previous, and only enabled other sources", () => {
  const sources = [
    { id: "req-1", active: true, slot: "requirement_doc", group_id: "req", version_no: 1, added_at: "2026-11-01T09:00:00+08:00", inspection_enabled: true, last_inspection_success_at: "2026-11-10T10:00:00+08:00" },
    { id: "req-2", active: true, slot: "requirement_doc", group_id: "req", version_no: 2, added_at: "2026-11-02T09:00:00+08:00", inspection_enabled: true },
    { id: "req-3", active: true, slot: "requirement_doc", group_id: "req", version_no: 3, added_at: "2026-11-03T09:00:00+08:00" },
    { id: "fig-on", active: true, slot: "figma", group_id: "fig-on", version_no: 1, added_at: "2026-11-04T09:00:00+08:00", inspection_enabled: true, last_successful_read_at: "2026-11-11T10:00:00+08:00" },
    { id: "fig-off", active: true, slot: "figma", group_id: "fig-off", version_no: 1, added_at: "2026-11-03T09:00:00+08:00", inspection_enabled: false }
  ];
  const view = libraryView(sources);
  assert.deepEqual(view.slots[0].versions.map(source => [source.id, source.inspection_active]), [
    ["req-3", true], ["req-2", true], ["req-1", false]
  ]);
  assert.deepEqual(view.slots[0].versions.map(source => source.inspection_eligible), [true, true, false]);
  assert.equal(view.slots[0].versions[0].inspection_enabled, true);
  assert.equal(view.slots[0].versions[2].last_inspection_success_at, "2026-11-10T10:00:00+08:00");
  assert.equal(view.slots[0].group.last_inspection_success_at, "2026-11-10T10:00:00+08:00");
  assert.equal(view.slots[1].card.id, "fig-on");
  assert.equal(view.slots[1].card.inspection_active, true);
  assert.equal(view.slots[1].card.last_inspection_success_at, "2026-11-11T10:00:00+08:00");
  const figOff = view.others.find(group => group.group_id === "fig-off");
  assert.equal(figOff.card.inspection_active, false);
  assert.equal(figOff.card.inspection_eligible, true);
});

test("explicitly removing the latest requirement version leaves the previous version inspectable", () => {
  const sources = [
    { id: "req-1", active: true, slot: "requirement_doc", group_id: "req", version_no: 1, added_at: "2026-11-01T09:00:00+08:00", inspection_enabled: true },
    { id: "req-2", active: true, slot: "requirement_doc", group_id: "req", version_no: 2, added_at: "2026-11-02T09:00:00+08:00", inspection_enabled: false }
  ];
  const view = libraryView(sources);
  assert.equal(view.slots[0].card.id, "req-2");
  assert.equal(view.slots[0].card.inspection_active, false);
  assert.equal(view.slots[0].versions[1].inspection_active, true);
  assert.equal(view.slots[0].versions[1].inspection_rank, 1);
});

test("library derives version numbers for legacy groups and excludes removed versions", () => {
  const sources = [
    { id: "legacy-b", active: true, group_id: "legacy", added_at: "2026-11-02T09:00:00+08:00" },
    { id: "legacy-a", active: true, group_id: "legacy", added_at: "2026-11-01T09:00:00+08:00" },
    { id: "removed", active: false, status: "removed", group_id: "legacy", version_no: 3, added_at: "2026-11-03T09:00:00+08:00" }
  ];
  const view = libraryView(sources);
  const other = view.others.find(group => group.group_id === "legacy");
  assert.deepEqual(other.versions.map(source => [source.id, source.version_no]), [["legacy-b", 2], ["legacy-a", 1]]);
  assert.equal(other.card.version_label, "v2");
  assert.deepEqual(view.removed.map(source => source.id), ["removed"]);
  assert.equal(view.activeCount, 2);
  assert.equal(view.slots.every(slot => slot.empty), true);
});
