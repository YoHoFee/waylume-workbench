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
      // 责任转移是终结状态，不进入当天“进行中/今日完成”区域；统一收进已完成折叠区。
      if (record.transfer_disposition === "transferred") historical.push(record);
      else if (record.completed_at && beijingDate(record.completed_at) === todayKey) todayRecords.push(record);
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
  const iterationStatusLabels = Object.freeze({
    planning: "规划中", active: "进行中", pending_release: "待发布", released: "已发布", archived: "已归档"
  });
  function iterationStatusLabel(status) { return iterationStatusLabels[status] || String(status || "未设置"); }
  function iterationIsReleased(iteration) {
    return !!iteration && (iteration.status === "released" || iteration.status === "archived");
  }
  // A version has exactly one readable name. `title` wins; `version_tag` is kept
  // only for backward compatibility and never becomes a second visible name.
  function iterationName(iteration) {
    if (!iteration || typeof iteration !== "object") return "未命名版本";
    const title = String(iteration.title || "").trim();
    const tag = String(iteration.version_tag || "").trim();
    const id = String(iteration.id || "").trim();
    return title || tag || id || "未命名版本";
  }
  function iterationSecondaryTag(iteration) {
    if (!iteration || typeof iteration !== "object") return "";
    const title = String(iteration.title || "").trim();
    const tag = String(iteration.version_tag || "").trim();
    return title && tag && title !== tag ? tag : "";
  }
  function iterationOrderValue(value) {
    return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
  }
  function orderedIterations(iterations) {
    const list = (Array.isArray(iterations) ? iterations : []).filter(item => item && typeof item === "object");
    return [...list].sort((a, b) => {
      const aOrder = iterationOrderValue(a.order);
      const bOrder = iterationOrderValue(b.order);
      if (aOrder !== bOrder) {
        if (aOrder === null) return 1;
        if (bOrder === null) return -1;
        return aOrder - bOrder;
      }
      return String(a.created_at || "").localeCompare(String(b.created_at || ""))
        || String(a.id || "").localeCompare(String(b.id || ""));
    });
  }
  // Delegated tasks (and BUGs) intentionally live outside the version matrix.
  function iterationApplies(record) {
    if (!record || typeof record !== "object") return false;
    return record.type !== "task" && record.type !== "bug";
  }
  function partitionIterationRecords(records, iterations, today) {
    const list = (Array.isArray(records) ? records : []).filter(iterationApplies);
    const iterList = orderedIterations(iterations);
    const iterMap = new Map(iterList.map(it => [it.id, it]));
    const groupedByIter = new Map();
    const backlog = [];

    for (const record of list) {
      const iterId = record.iteration_id;
      if (iterId && iterMap.has(iterId)) {
        if (!groupedByIter.has(iterId)) groupedByIter.set(iterId, []);
        groupedByIter.get(iterId).push(record);
      } else {
        backlog.push(record);
      }
    }

    const groups = iterList.map(it => {
      const iterRecords = groupedByIter.get(it.id) || [];
      const isReleased = iterationIsReleased(it);
      const unfinished = iterRecords.filter(r => !["completed", "cancelled", "archived"].includes(r.effective_status || r.status));
      const completed = iterRecords.filter(r => ["completed", "cancelled", "archived"].includes(r.effective_status || r.status));
      return {
        iteration: it,
        records: iterRecords,
        unfinished,
        completed,
        isReleased,
        // Unreleased versions keep every record flat. Released versions stay
        // visible and only fold their completed records inside the group.
        visibleRecords: isReleased ? unfinished : iterRecords,
        foldedRecords: isReleased ? completed : []
      };
    });

    const backlogUnfinished = backlog.filter(r => !["completed", "cancelled", "archived"].includes(r.effective_status || r.status));
    const backlogCompleted = backlog.filter(r => ["completed", "cancelled", "archived"].includes(r.effective_status || r.status));
    const backlogCompletion = partitionDailyCompletions(backlogCompleted, today);

    return {
      iterations: iterList,
      groups,
      backlog: {
        records: backlog,
        unfinished: backlogUnfinished,
        completed: backlogCompleted,
        completion: backlogCompletion
      }
    };
  }
  function sortTaskRecords(items) {
    const rank = item => item.effective_status === "completed" ? 2 : ["in_progress", "developing", "testing"].includes(item.effective_status) ? 0 : 1;
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
  const workflowStagesFallback = [
    { id: "intake", label: "接单/立项", short: "接单", submit_roles: ["lead"], required_outputs: ["scope", "decision_owner"], next: "requirement" },
    { id: "requirement", label: "需求分析与PM结论", short: "需求", submit_roles: ["pm"], required_outputs: ["requirement_brief", "acceptance_criteria", "pm_decision"], next: "development" },
    { id: "development", label: "开发", short: "开发", submit_roles: ["dev"], required_outputs: ["dev_summary", "branch", "self_check"], next: "audit_response" },
    { id: "audit_response", label: "静态审计与开发回应", short: "审计", submit_roles: ["auditor", "dev"], required_outputs: ["audit_report", "dev_response"], next: "acceptance" },
    { id: "acceptance", label: "PM验收", short: "验收", submit_roles: ["pm"], required_outputs: ["pm_acceptance"], next: "delivery" },
    { id: "delivery", label: "交付关闭", short: "交付", submit_roles: ["lead"], required_outputs: ["delivery_evidence"], next: null }
  ];
  const workflowSubstepsFallback = {
    requirement: [
      { id: "understanding", label: "需求理解", role: "pm", output: "requirement_brief" },
      { id: "pm_decision", label: "PM结论", role: "pm", output: "pm_decision" }
    ],
    audit_response: [
      { id: "audit", label: "静态审计", role: "auditor", output: "audit_report" },
      { id: "response", label: "开发回应", role: "dev", output: "dev_response" }
    ]
  };
  const workflowRoleLabels = { lead: "Lead", pm: "PM", dev: "Dev", auditor: "Auditor" };
  const workflowSubstepShortLabels = { understanding: "需求理解", pm_decision: "PM结论", audit: "静态审计", response: "开发回应" };
  const workflowOutputLabels = {
    scope: "范围", decision_owner: "决定权", requirement_brief: "需求简报", acceptance_criteria: "验收口径", pm_decision: "PM 结论",
    dev_summary: "开发总结", branch: "分支", self_check: "自检", audit_report: "审计报告", dev_response: "开发回应",
    pm_acceptance: "PM 验收", delivery_evidence: "交付证据"
  };
  const workflowStatusLabels = { done: "已完成", active: "进行中", pending: "待开始", blocked: "已阻塞", rework: "已退回" };
  const workflowActionLabels = { start: "启动流程", submit: "提交输出", advance: "推进阶段", rework: "退回修改", block: "标记阻塞", unblock: "解除阻塞", close: "关闭流程", reopen: "发起二次修改", release: "迭代发布" };

  function workflowRoleLabel(role) {
    const value = String(role || "").trim();
    return workflowRoleLabels[value] || value || "未指定";
  }
  function workflowStatusLabel(value) { return workflowStatusLabels[value] || "未开始"; }
  function workflowOutputLabel(key) { return workflowOutputLabels[key] || String(key || "输出").replace(/_/g, " "); }
  function workflowNormalizeStatus(value) { return ["done", "active", "pending", "blocked", "rework"].includes(value) ? value : "active"; }
  function workflowOutputFilled(outputs, key) {
    if (!outputs || typeof outputs !== "object") return null;
    const value = outputs[key];
    if (value === undefined || value === null) return false;
    if (typeof value === "string") return value.trim().length > 0;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "object") return Object.keys(value).length > 0;
    return true;
  }
  function workflowStageDefinitions(template) {
    const source = Array.isArray(template?.stages) && template.stages.length ? template.stages : workflowStagesFallback;
    return source.map((stage, index) => {
      const fallback = workflowStagesFallback.find(item => item.id === stage.id) || {};
      const roles = Array.isArray(stage.submit_roles) && stage.submit_roles.length ? stage.submit_roles : (fallback.submit_roles || []);
      const substeps = Array.isArray(stage.substeps) && stage.substeps.length ? stage.substeps : (workflowSubstepsFallback[stage.id] || []);
      const label = String(stage.label || fallback.label || stage.id || `阶段 ${index + 1}`);
      const outputLabels = stage.output_labels && typeof stage.output_labels === "object" ? stage.output_labels : (fallback.output_labels || {});
      return {
        id: String(stage.id || fallback.id || `stage-${index + 1}`),
        index: index + 1,
        label,
        short: String(stage.short || fallback.short || label),
        roles,
        owner: roles.length ? roles.map(role => workflowRoleLabel(role).replace("Auditor", "Audit")).join("/") : "未指定",
        next: stage.next === undefined ? (fallback.next ?? null) : stage.next,
        requiredOutputs: Array.isArray(stage.required_outputs) ? stage.required_outputs.map(String) : (fallback.required_outputs || []),
        outputLabels,
        substeps: substeps.map((sub, subIndex) => ({
          id: String(sub.id || `sub-${subIndex + 1}`),
          label: String(sub.label || sub.id || `子步骤 ${subIndex + 1}`),
          short: workflowSubstepShortLabels[sub.id] || String(sub.label || sub.id || ""),
          role: sub.role || "",
          roleLabel: workflowRoleLabel(sub.role || roles[0]),
          output: sub.output || (Array.isArray(sub.outputs) ? sub.outputs[0] : null) || workflowSubstepsFallback[stage.id]?.[sub.id]?.output || null,
          indexLabel: `${index + 1}${"abcdefgh"[subIndex] || subIndex + 1}`
        }))
      };
    });
  }
  function workflowHistoryEntries(workflow, stages) {
    const source = workflow && Array.isArray(workflow.history) ? workflow.history : [];
    return source.map(entry => {
      const item = entry && typeof entry === "object" ? entry : { note: entry };
      const stage = stages.find(candidate => candidate.id === item.stage);
      return {
        at: String(item.at || ""),
        action: String(item.action || ""),
        actionLabel: workflowActionLabels[item.action] || String(item.action || "记录"),
        stage: String(item.stage || ""),
        stageLabel: stage ? stage.label : String(item.stage_label || item.stage || ""),
        actor: String(item.actor || ""),
        role: String(item.role || ""),
        roleLabel: item.role ? workflowRoleLabel(item.role) : "",
        note: String(item.note || ""),
        evidence: (Array.isArray(item.evidence) ? item.evidence : item.evidence ? [item.evidence] : []).map(value => String(value))
      };
    });
  }
  // Builds the pipeline view model from list summary fields plus the optional
  // GET /api/items/<ID>/workflow payload. Never invents gate outputs: when the
  // detail payload is unavailable every checklist item is reported as unknown.
  function workflowView(record, detail) {
    const summary = record && typeof record === "object" ? record : {};
    const payload = detail && typeof detail === "object" ? detail : null;
    const unavailable = !!payload?.unavailable;
    const loaded = !!payload && !unavailable;
    const workflow = loaded && payload.workflow && typeof payload.workflow === "object" ? payload.workflow : null;
    const template = loaded && payload.template && typeof payload.template === "object" ? payload.template : null;
    const detailState = unavailable ? "unavailable" : loaded ? "loaded" : "pending";
    const stages = workflowStageDefinitions(template);
    const stageId = (loaded ? (workflow && workflow.stage) : summary.workflow_stage) || null;
    const stageIndex = stageId ? stages.findIndex(stage => stage.id === stageId) : -1;
    const templateId = (workflow && workflow.template_id) || (template && template.id) || summary.workflow_template_id || null;
    const base = {
      enabled: false, detailState, unavailable, templateId,
      templateVersion: (workflow && workflow.template_version) ?? (template && template.version) ?? null,
      stageId: null, stageIndex: null, stageCount: stages.length,
      stageStatus: null, statusLabel: "未启用", blocked: false, reworkCount: 0, lock: null,
      reopenCount: 0, reopenHistory: [], auditMode: null,
      steps: [], history: [], historyCount: 0, current: null, summaryOnly: detailState !== "loaded"
    };
    if (!stageId) return base;
    const rawStatus = (workflow && workflow.stage_status) || (loaded ? "active" : summary.workflow_stage_status) || "active";
    const gates = workflow && workflow.gates && typeof workflow.gates === "object" ? workflow.gates : null;
    const stageGate = gates && gates[stageId] && typeof gates[stageId] === "object" ? gates[stageId] : null;
    // 后端可能只在 history/gates 里标记退回而把 stage_status 保持 active，这里统一折成 rework。
    const historyTail = workflow && Array.isArray(workflow.history) ? workflow.history[workflow.history.length - 1] : null;
    const reworked = rawStatus !== "rework" && rawStatus !== "blocked" && rawStatus !== "done" &&
      ((stageGate && stageGate.status === "rework") || (historyTail && historyTail.action === "rework" && historyTail.stage === stageId));
    const status = workflowNormalizeStatus(reworked ? "rework" : rawStatus);
    const reworkCount = Number((workflow && workflow.rework_count) ?? summary.workflow_rework_count ?? 0) || 0;
    const rawLock = (workflow && workflow.lock) || (summary.workflow && summary.workflow.lock) || null;
    const lockExpires = rawLock && rawLock.expires_at ? Date.parse(rawLock.expires_at) : NaN;
    const lockExpired = !Number.isFinite(lockExpires) || lockExpires <= Date.now();
    const lock = rawLock ? {
      actor: String(rawLock.actor || ""),
      role: String(rawLock.role || ""),
      roleLabel: workflowRoleLabel(rawLock.role),
      stage: String(rawLock.stage || ""),
      expiresAt: String(rawLock.expires_at || ""),
      active: !lockExpired,
      expired: lockExpired,
      remainingMinutes: Number.isFinite(lockExpires) ? Math.max(0, Math.ceil((lockExpires - Date.now()) / 60000)) : 0
    } : null;
    const currentIndex = stageIndex >= 0 ? stageIndex : -1;
    const nextById = new Map(stages.map(stage => [stage.id, stage]));
    const steps = stages.map((stage, index) => {
      let stepStatus = "pending";
      if (currentIndex >= 0) stepStatus = index < currentIndex ? "done" : index > currentIndex ? "pending" : status;
      const current = index === currentIndex;
      const gate = index === currentIndex ? stageGate : gates && gates[stage.id] && typeof gates[stage.id] === "object" ? gates[stage.id] : null;
      const outputs = gate && gate.outputs && typeof gate.outputs === "object" ? gate.outputs : null;
      const nextStage = stage.next ? nextById.get(stage.next) : null;
      let activeSubstepAssigned = false;
      const substeps = stage.substeps.map(sub => {
        let substepStatus = "pending";
        if (stepStatus === "done") substepStatus = "done";
        else if (["active", "blocked", "rework"].includes(stepStatus)) {
          const filled = sub.output ? workflowOutputFilled(outputs, sub.output) : null;
          if (filled === true) substepStatus = "done";
          else if (!activeSubstepAssigned) { substepStatus = "active"; activeSubstepAssigned = true; }
        }
        return { id: sub.id, label: sub.label, short: sub.short, indexLabel: sub.indexLabel, role: sub.role, roleLabel: sub.roleLabel, status: substepStatus, statusLabel: workflowStatusLabels[substepStatus] };
      });
      return {
        id: stage.id, index: stage.index, label: stage.label, short: stage.short, owner: stage.owner, roles: stage.roles,
        status: stepStatus, statusLabel: workflowStatusLabels[stepStatus], current,
        nextLabel: nextStage ? (nextStage.short || nextStage.label) : "流程结束",
        checklist: stage.requiredOutputs.map(key => ({ key, label: stage.outputLabels[key] || workflowOutputLabel(key), done: outputs ? workflowOutputFilled(outputs, key) : null })),
        note: gate && gate.note ? String(gate.note) : "",
        evidence: gate && Array.isArray(gate.evidence) ? gate.evidence.map(String) : gate && gate.evidence ? [String(gate.evidence)] : [],
        actor: gate && gate.actor ? String(gate.actor) : "",
        role: gate && gate.role ? String(gate.role) : "",
        roleLabel: gate && gate.role ? workflowRoleLabel(gate.role) : "",
        at: gate && gate.at ? String(gate.at) : "",
        lock: current ? lock : null,
        substeps
      };
    });
    const currentStep = currentIndex >= 0 ? steps[currentIndex] : null;
    const history = workflowHistoryEntries(workflow, stages);
    const reopenCount = Number((workflow && workflow.reopen_count) ?? summary.reopen_count ?? 0) || 0;
    const reopenHistory = Array.isArray(workflow && workflow.reopen_history) ? workflow.reopen_history : Array.isArray(summary.reopen_history) ? summary.reopen_history : [];
    const auditMode = (workflow && workflow.audit_mode) || (summary.workflow && summary.workflow.audit_mode) || (reopenHistory.length ? reopenHistory[reopenHistory.length - 1].audit_mode : null) || "full";
    return Object.assign(base, {
      enabled: true,
      stageId,
      stageIndex: currentIndex >= 0 ? currentIndex + 1 : null,
      stageStatus: status,
      statusLabel: workflowStatusLabels[status],
      blocked: status === "blocked",
      reworkCount,
      lock,
      reopenCount,
      reopenHistory,
      auditMode,
      steps,
      history,
      historyCount: history.length,
      current: currentStep,
      summaryOnly: detailState !== "loaded"
    });
  }
  // Card-level summary chip. Only records that actually carry a workflow stage
  // get a chip; everything stays hidden for legacy records.
  function workflowChip(record) {
    if (!record || typeof record !== "object") return null;
    const inline = record.workflow && typeof record.workflow === "object" ? record.workflow : null;
    const stageId = record.workflow_stage || (inline && inline.stage) || null;
    if (!stageId) return null;
    const stage = workflowStagesFallback.find(item => item.id === stageId) || null;
    const status = workflowNormalizeStatus(record.workflow_stage_status || (inline && inline.stage_status) || "active");
    const label = record.workflow_stage_short || record.workflow_stage_label || (stage && stage.short) || (stage && stage.label) || String(stageId);
    return {
      stageId: String(stageId),
      label: String(label),
      status,
      statusLabel: workflowStatusLabels[status],
      title: `流水线：${(stage && stage.label) || record.workflow_stage_label || stageId} · ${workflowStatusLabels[status]}`
    };
  }
  // 资料库：固定四个资料位，版本组保留全部历史；巡检只读取规则允许的版本。
  const librarySlots = Object.freeze([
    Object.freeze({ id: "requirement_doc", label: "需求文档", hint: "交付依据" }),
    Object.freeze({ id: "figma", label: "Figma", hint: "视觉稿" }),
    Object.freeze({ id: "codesign", label: "CoDesign", hint: "设计协作" }),
    Object.freeze({ id: "api_doc", label: "接口文档", hint: "联调契约" })
  ]);
  const librarySlotLabels = Object.freeze(Object.fromEntries(librarySlots.map(slot => [slot.id, slot.label])));
  function libraryActive(source) {
    return !!source && typeof source === "object" && source.active !== false && source.status !== "removed";
  }
  function libraryGroupId(source) {
    return String(source.group_id || source.id || "");
  }
  function libraryStoredVersionNo(source) {
    const value = source.version_no;
    return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
  }
  function libraryOrderedVersions(records) {
    const ordered = [...records].sort((a, b) =>
      String(a.added_at || "").localeCompare(String(b.added_at || "")) || String(a.id || "").localeCompare(String(b.id || "")));
    const stored = ordered.map(libraryStoredVersionNo);
    const numbers = stored.every(Number.isInteger) && new Set(stored).size === stored.length
      ? stored
      : ordered.map((_, index) => index + 1);
    return ordered.map((source, index) => ({ source, versionNo: numbers[index] }))
      .sort((a, b) => b.versionNo - a.versionNo
        || String(b.source.added_at || "").localeCompare(String(a.source.added_at || ""))
        || String(b.source.id || "").localeCompare(String(a.source.id || "")));
  }
  function libraryGroupSlot(versions) {
    const known = slot => !!slot && (librarySlotLabels[slot] || archiveSlotLabels[slot]);
    const latestSlot = versions[0]?.source?.slot;
    if (known(latestSlot)) return latestSlot;
    const inherited = versions.find(item => known(item.source.slot));
    return inherited ? inherited.source.slot : null;
  }
  function libraryInspectionState(source, index, groupSlot) {
    const enabled = typeof source.inspection_enabled === "boolean"
      ? source.inspection_enabled
      : groupSlot === "requirement_doc";
    // 需求文档只巡检最新与上一版；更旧版本即使显式开启也不参与。
    const eligible = groupSlot !== "requirement_doc" || index < 2;
    return { enabled, eligible, active: enabled && eligible, rank: index };
  }
  function libraryView(sources) {
    const list = Array.isArray(sources) ? sources.filter(item => item && typeof item === "object") : [];
    const active = list.filter(libraryActive);
    const removed = list.filter(source => !libraryActive(source)).map(source => ({ ...source }));
    const grouped = new Map();
    for (const source of active) {
      const groupId = libraryGroupId(source);
      if (!grouped.has(groupId)) grouped.set(groupId, []);
      grouped.get(groupId).push(source);
    }
    const groups = [];
    for (const [groupId, members] of grouped) {
      const ordered = libraryOrderedVersions(members);
      const slot = libraryGroupSlot(ordered);
      const versionCount = ordered.length;
      const versions = ordered.map(({ source, versionNo }, index) => {
        const inspection = libraryInspectionState(source, index, slot);
        return {
          ...source, group_id: groupId, version_no: versionNo,
          version_label: typeof source.version_label === "string" && source.version_label.trim()
            ? source.version_label.trim() : `v${versionNo}`,
          version_count: versionCount,
          is_latest: index === 0, latest: index === 0,
          inspection_enabled: inspection.enabled,
          inspection_eligible: inspection.eligible,
          inspection_active: inspection.active,
          inspection_rank: index,
          last_inspection_success_at: source.last_inspection_success_at || source.last_successful_read_at || null
        };
      });
      const card = versions[0];
      const successTimes = versions.map(source => source.last_inspection_success_at).filter(Boolean).sort();
      groups.push({
        group_id: groupId, slot, slot_label: librarySlotLabels[slot] || archiveSlotLabels[slot] || "补充材料",
        card, latest: card, versions, version_count: versionCount,
        history_count: Math.max(0, versionCount - 1),
        last_inspection_success_at: successTimes.length ? successTimes[successTimes.length - 1] : null
      });
    }
    groups.sort((a, b) => String(b.card.added_at || "").localeCompare(String(a.card.added_at || ""))
      || String(a.group_id).localeCompare(String(b.group_id)));
    const selected = new Map();
    for (const definition of librarySlots) {
      const candidates = groups.filter(group => group.slot === definition.id)
        .sort((a, b) => String(b.card.added_at || "").localeCompare(String(a.card.added_at || ""))
          || b.card.version_no - a.card.version_no
          || String(a.group_id).localeCompare(String(b.group_id)));
      selected.set(definition.id, candidates[0] || null);
    }
    const selectedGroups = new Set([...selected.values()].filter(Boolean).map(group => group.group_id));
    const slots = librarySlots.map(definition => {
      const group = selected.get(definition.id);
      return {
        slot: definition.id, label: definition.label, hint: definition.hint,
        group, card: group?.card || null, versions: group?.versions || [],
        version_count: group?.version_count || 0, history_count: group?.history_count || 0,
        empty: !group
      };
    });
    const others = groups.filter(group => !selectedGroups.has(group.group_id));
    return { slots, others, removed, activeCount: active.length, groups };
  }
  const globalArchiveSlots = Object.freeze([
    Object.freeze({ id: "api_doc", label: "接口文档", hint: "跨版本共享接口契约与定义" })
  ]);
  const iterationArchiveSlots = Object.freeze([
    Object.freeze({ id: "requirement_doc", label: "需求列表", hint: "交付依据与功能清单" }),
    Object.freeze({ id: "figma", label: "Figma", hint: "界面与交互视觉设计主稿" }),
    Object.freeze({ id: "prototype", label: "高保真", hint: "CoDesign / 交互原型" }),
    Object.freeze({ id: "testcase", label: "测试用例", hint: "QA冒烟与验收用例参考" })
  ]);
  const archiveSlotLabels = Object.freeze({
    requirement_doc: "需求列表",
    figma: "Figma",
    prototype: "高保真",
    codesign: "高保真",
    testcase: "测试用例",
    supplementary: "补充材料",
    api_doc: "接口文档",
    architecture: "公共规范",
    other: "其他资料"
  });
  function sourceScope(source) {
    if (!source || typeof source !== "object") return "global";
    if (source.scope === "global" || source.scope === "iteration") return source.scope;
    if (["api_doc", "architecture", "standard"].includes(source.slot)) return "global";
    return source.iteration_id ? "iteration" : "global";
  }
  function archiveAttachments(sources) {
    const items = [];
    for (const source of sources) {
      for (const attachment of Array.isArray(source.attachments) ? source.attachments : []) {
        items.push({ ...attachment, attachment: true, source_id: source.id, source_name: sourceDisplayNameOf(source) });
      }
    }
    return items;
  }
  function sourceDisplayNameOf(source) {
    return String(source?.name || source?.filename || source?.id || "材料");
  }
  function buildArchiveSection(partSources, definitions, meta) {
    const view = libraryView(partSources);
    const selected = new Map();
    for (const definition of definitions) {
      const candidates = view.groups.filter(group => {
        if (definition.id === "prototype") return group.slot === "prototype" || group.slot === "codesign";
        return group.slot === definition.id;
      });
      selected.set(definition.id, candidates[0] || null);
    }
    const slots = definitions.map(definition => {
      const group = selected.get(definition.id);
      return {
        slot: definition.id, label: definition.label, hint: definition.hint,
        group, card: group?.card || null, versions: group?.versions || [],
        version_count: group?.version_count || 0, history_count: group?.history_count || 0,
        empty: !group
      };
    });
    const fixedGroupIds = new Set([...selected.values()].filter(Boolean).map(group => group.group_id));
    // Only groups that did not win a fixed slot become supplementary material.
    // This is the isolation rule: one group has exactly one home in this scope.
    const supplementaryGroups = view.groups.filter(group => !fixedGroupIds.has(group.group_id));
    const attachments = archiveAttachments(partSources);
    return {
      ...meta,
      slots,
      supplementaryGroups,
      attachments,
      groups: view.groups,
      fixedCount: slots.filter(slot => !slot.empty).length,
      supplementaryCount: supplementaryGroups.length + attachments.length
    };
  }
  // Archive matrix. Every source is assigned to exactly one section by scope
  // and iteration, so a material can never leak into another version or into
  // the shared public list.
  function archiveView(sources, iterations) {
    const list = Array.isArray(sources) ? sources.filter(item => item && typeof item === "object") : [];
    const iterList = orderedIterations(iterations);
    const iterMap = new Map(iterList.map(it => [it.id, it]));
    const active = list.filter(libraryActive);
    const removed = list.filter(source => !libraryActive(source)).map(source => ({ ...source }));

    const globalSources = [];
    const byIteration = new Map(iterList.map(it => [it.id, []]));
    const groupLocations = new Map();
    for (const source of active) {
      const scope = sourceScope(source);
      const groupId = source.group_id || source.id;
      const location = scope === "iteration" && source.iteration_id && iterMap.has(source.iteration_id)
        ? `iteration:${source.iteration_id}` : "global";
      if (!groupLocations.has(groupId)) groupLocations.set(groupId, new Set());
      groupLocations.get(groupId).add(location);
      if (scope === "iteration" && source.iteration_id && iterMap.has(source.iteration_id)) {
        byIteration.get(source.iteration_id).push(source);
      } else {
        // Version material with a missing target is treated as unassigned public
        // material instead of leaking into an arbitrary version.
        globalSources.push(source);
      }
    }
    const mixedGroups = new Set([...groupLocations.entries()]
      .filter(([, locations]) => locations.size > 1)
      .map(([groupId]) => groupId));

    const globalSection = buildArchiveSection(globalSources, globalArchiveSlots, { scope: "global", iteration: null });
    globalSection.mixedGroups = mixedGroups;
    globalSection.removed = removed.filter(source => {
      const scope = sourceScope(source);
      return scope === "global" || !source.iteration_id || !iterMap.has(source.iteration_id);
    });

    const iterationSections = iterList.map(iteration => {
      const section = buildArchiveSection(byIteration.get(iteration.id), iterationArchiveSlots, { scope: "iteration", iteration });
      section.removed = removed.filter(source => sourceScope(source) === "iteration" && source.iteration_id === iteration.id);
      section.mixedGroups = mixedGroups;
      return section;
    });

    return {
      iterations: iterationSections,
      global: globalSection,
      globalSection,
      globalSlots: globalSection.slots,
      mixedGroups,
      removed,
      orderedIterations: iterList,
      activeCount: active.length
    };
  }
  const model = { beijingDate, groupCompletedByDate, partitionCompletedRecords, partitionTerminalRecords, partitionDailyCompletions, partitionIterationRecords, partitionTaskBatches, contextPlaceholder, replaceContextPreservingScroll, summarizeFollowups, shortestMasonryColumn, sortTaskRecords, workPeriodProgress,
    iterationStatusLabels, iterationStatusLabel, iterationIsReleased, iterationName, iterationSecondaryTag, orderedIterations, iterationApplies,
    librarySlots, librarySlotLabels, libraryActive, libraryView, sourceScope, sourceDisplayNameOf,
    globalArchiveSlots, iterationArchiveSlots, archiveSlotLabels, archiveView,
    workflowView, workflowChip, workflowStageDefinitions, workflowStatusLabel, workflowRoleLabel, workflowOutputLabel, workflowStagesFallback };
  if (typeof module !== "undefined" && module.exports) module.exports = model;
  if (typeof window !== "undefined") window.dashboardModel = model;
})();
