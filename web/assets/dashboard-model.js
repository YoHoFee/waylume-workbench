// Created by Codex on 2026/10/22.
// Copyright © 2026 CMIC. All rights reserved.
(() => {
  function beijingDate(value) {
    if (!value) return null;
    const epoch = Date.parse(value);
    if (Number.isNaN(epoch)) return null;
    return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(epoch));
  }
  function workPeriodProgress(nowEpoch, start, end) {
    const timePattern = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
    if (!timePattern.test(start) || !timePattern.test(end) || start >= end) throw new RangeError("invalid_work_hours");
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(new Date(nowEpoch));
    const local = Object.fromEntries(parts.filter(part => part.type !== "literal").map(part => [part.type, Number(part.value)]));
    const offsetAt = epoch => {
      const values = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(new Date(epoch));
      const p = Object.fromEntries(values.filter(part => part.type !== "literal").map(part => [part.type, Number(part.value)]));
      return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - Math.floor(epoch / 1000) * 1000;
    };
    const localEpoch = value => {
      const [hour, minute] = value.split(":").map(Number);
      const asUtc = Date.UTC(local.year, local.month - 1, local.day, hour, minute, 0);
      let epoch = asUtc - offsetAt(asUtc);
      epoch = asUtc - offsetAt(epoch);
      return epoch;
    };
    const startEpoch = localEpoch(start), endEpoch = localEpoch(end);
    return Math.max(0, Math.min(100, Math.floor((nowEpoch - startEpoch) * 100 / (endEpoch - startEpoch))));
  }
  function groupCompletedByDate(records, today) {
    const groups = new Map();
    for (const record of records) {
      const date = beijingDate(record.completed_at) || "unknown";
      if (!groups.has(date)) groups.set(date, []);
      groups.get(date).push(record);
    }
    return [...groups.entries()].sort((a, b) => a[0] === "unknown" ? 1 : b[0] === "unknown" ? -1 : b[0].localeCompare(a[0])).map(([date, records], index) => ({ date, records, open: index === 0 }));
  }
  function partitionDailyCompletions(records, today) {
    const todayKey = today || beijingDate(new Date().toISOString());
    const todayRecords = [], historical = [];
    for (const record of records) {
      if (record.completed_at && beijingDate(record.completed_at) === todayKey) todayRecords.push(record);
      else historical.push(record);
    }
    const groups = groupCompletedByDate(historical, todayKey);
    return { todayRecords, groups, outerFoldHidden: groups.length === 0 };
  }
  function contextPlaceholder(cached, expectedRevision) {
    if (!cached || typeof cached.markdown !== "string") return null;
    return { markdown: cached.markdown, isCurrent: String(cached.revision) === String(expectedRevision) };
  }
  function replaceContextPreservingScroll(detail, target, markdown, renderMarkdown) {
    const currentScrollTop = detail.scrollTop;
    target.replaceChildren();
    renderMarkdown(target, markdown);
    detail.scrollTop = currentScrollTop;
  }
  function summarizeFollowups(items) {
    const active = (Array.isArray(items) ? items : []).filter(item => item && (item.status === "pending" || item.status === "processing"));
    active.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    return {
      activeCount: active.length,
      pendingCount: active.filter(item => item.status === "pending").length,
      processingCount: active.filter(item => item.status === "processing").length,
      latestActive: active[0] || null
    };
  }
  function shortestMasonryColumn(heights) {
    if (!Array.isArray(heights) || heights.length < 2) return 0;
    return heights[0] <= heights[1] ? 0 : 1;
  }
  function partitionTerminalRecords(records, today) { return partitionDailyCompletions(records, today); }
  function partitionCompletedRecords(records, today) { return partitionTerminalRecords(records, today); }
  function sortTaskRecords(items) {
    const rank = item => item.effective_status === "completed" ? 2 : item.effective_status === "in_progress" ? 0 : 1;
    return [...items].sort((a, b) => rank(a) - rank(b) || String(b.created_at || "").localeCompare(String(a.created_at || "")) || String(a.id || "").localeCompare(String(b.id || "")));
  }
  function partitionTaskBatches(tasks, batches, currentBatchId, legacyIds = []) {
    const byId = new Map(tasks.map(item => [item.id, item]));
    const known = new Map(batches.map(batch => [batch.id, batch]));
    const current = known.get(currentBatchId) || null;
    const currentTasks = currentBatchId ? (Array.isArray(current?.task_ids) ? current.task_ids.map(id => byId.get(id)).filter(Boolean) : tasks.filter(item => item.batch_id === currentBatchId)) : [];
    const legacySet = new Set(legacyIds.length ? legacyIds : tasks.filter(item => !item.batch_id).map(item => item.id));
    const legacy = tasks.filter(item => legacySet.has(item.id));
    const pastBatches = batches.filter(batch => batch.state === "past").map(batch => ({ batch, records: Array.isArray(batch.task_ids) ? batch.task_ids.map(id => byId.get(id)).filter(Boolean) : tasks.filter(item => item.batch_id === batch.id) })).filter(group => group.records.length);
    const unlisted = new Map();
    for (const item of tasks) if (item.batch_id && item.batch_id !== currentBatchId && !known.has(item.batch_id)) {
      if (!unlisted.has(item.batch_id)) unlisted.set(item.batch_id, []);
      unlisted.get(item.batch_id).push(item);
    }
    return { current, currentTasks, currentBatchTasks: currentBatchId ? tasks.filter(item => item.batch_id === currentBatchId) : [],
      legacyOpen: legacy.filter(item => !["completed", "cancelled", "archived"].includes(item.effective_status)), legacyDone: legacy.filter(item => ["completed", "cancelled", "archived"].includes(item.effective_status)),
      pastBatches, unlisted, unbatchedUnknown: tasks.filter(item => !item.batch_id && !legacySet.has(item.id)) };
  }
  const model = { beijingDate, groupCompletedByDate, partitionCompletedRecords, partitionTerminalRecords, partitionDailyCompletions, partitionTaskBatches, contextPlaceholder, replaceContextPreservingScroll, summarizeFollowups, shortestMasonryColumn, sortTaskRecords, workPeriodProgress };
  if (typeof module !== "undefined" && module.exports) module.exports = model;
  if (typeof window !== "undefined") window.dashboardModel = model;
})();
