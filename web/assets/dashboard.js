(() => {
  const state = { data: null, query: "", showArchived: false, selectedId: null, toastTimer: null, busy: new Set(), drafts: {}, sources: [], sourcesBusy: false, contexts: new Map(), contextRequests: new Map(), scopeCatalog: null };
  const inspectionClock = { schedule: null, serverEpoch: null, syncedAt: null, nextRunAt: null };
  const inspectionRun = { busy: false, state: "unknown", slotId: null, eventId: null, enabled: null, windowOpen: null, nextRunAt: null, serverEpoch: null, syncedAt: null, message: "正在读取巡检状态…" };
  const themeStorage = (() => { try { return window.localStorage; } catch (_) { return { getItem: () => null, setItem: () => {} }; } })();
  let savedTheme = window.__GEMINI_INITIAL_THEME__;
  if (!["minimal", "wood", "dark"].includes(savedTheme)) {
    savedTheme = null;
    try { savedTheme = themeStorage.getItem("mcloud-dashboard-theme"); } catch (_) {}
  }
  try { delete window.__GEMINI_INITIAL_THEME__; } catch (_) {}
  document.body.dataset.theme = savedTheme === "dark" ? "dark" : "minimal";
  const groups = [
    { key: "work", field: "requirements", list: "#work-records", empty: "#work-empty", done: "#work-completed", doneCount: "#work-completed-count", doneList: "#work-completed-list", count: "#work-count" },
    { key: "bug", field: "bugs", list: "#bug-records", empty: "#bug-empty", done: "#bug-completed", doneCount: "#bug-completed-count", doneList: "#bug-completed-list", count: "#bug-count" },
    { key: "task", field: "delegated", list: "#task-records", empty: "#task-empty", done: "#task-completed", doneCount: "#task-completed-count", doneList: "#task-completed-list", count: "#task-count" }
  ];
  const labels = {
    in_progress: "进行中", on_hold: "搁置", awaiting_acceptance: "待验收", blocked: "阻塞", completed: "已完成", cancelled: "已取消", partial: "部分完成",
    pending: "待处理", unknown: "未核实", archived: "已归档", platform_testing: "平台测试中", submitted_for_testing: "我方已提交测试"
  };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const model = window.dashboardModel;
  let workHours = { start: "08:30", end: "17:30", timezone: "Asia/Shanghai" };
  const integrationProviders = [
    ["feishu", "飞书", "文档", "飞"], ["tencent_docs", "腾讯文档", "文档", "腾"], ["kdocs", "金山文档", "文档", "金"],
    ["jira", "Jira", "需求平台", "J"], ["figma", "Figma", "UI 平台", "F"],
    ["linear", "Linear", "缺陷管理平台", "L"], ["git", "Git", "代码管理平台", "G"], ["iphone_use", "iPhone Use", "开发工具", "i"]
  ];
  let dataLocationPreview = null;
  let dataLocationRevision = null;

  function txt(value, fallback = "未提供") {
    if (value === null || value === undefined || value === "") return fallback;
    if (Array.isArray(value)) return value.map(v => typeof v === "string" ? v : JSON.stringify(v)).join("；");
    if (typeof value === "object") return value.label || value.title || value.path || JSON.stringify(value);
    return String(value);
  }
  function values(value) { return Array.isArray(value) ? value : value ? [value] : []; }
  function sourceStatus(record) {
    if (record.type === "bug") return record.source_status || record.platform_status || record.status;
    return record.source_status || record.status;
  }
  function statusLabel(value) { return labels[value] || txt(value, "未核实"); }
  function statusBasisLabel(value) {
    return ({ manual: "手动标记", our_status: "我方处理阶段", source_status: "原始状态", triage: "我方分流记录", activity: "Agent活动状态" })[value] || txt(value);
  }
  function ourBugDone(record) {
    const phase = txt(record.our_status || record.triage || "");
    return /(我方完成|已完成|已提测|已提交测试)/.test(phase);
  }
  function effectiveStatus(record) {
    if (record.transfer_disposition === "transferred" && record.completion_kind === "transferred") return "completed";
    if (record.effective_status) return record.effective_status;
    if (record.type === "bug" && ourBugDone(record)) return "completed";
    return record.status;
  }
  function isCompleted(record) { return effectiveStatus(record) === "completed"; }
  function isCancelled(record) { return effectiveStatus(record) === "cancelled"; }
  function isTerminal(record) { return isCompleted(record) || isCancelled(record); }
  const beijingDate = value => model.beijingDate(value);
  function hasManualCompletion(record) { return record.manual_completed === true || record.effective_status_basis === "manual"; }
  function groupRecords(group) {
    const source = state.data?.[group.field] || [];
    return source.filter(record => state.showArchived || effectiveStatus(record) !== "archived").filter(matchesSearch);
  }
  function matchesSearch(record) {
    const query = state.query.trim().toLocaleLowerCase("zh-CN");
    if (!query) return true;
    return [record.id, record.display_id, record.title, record.status, record.source_status, record.status_detail, record.owner, record.assignee,
      record.notes, record.evidence, record.source, record.our_status, record.triage, record.latest_brief,
      record.progress?.map(item => typeof item === "string" ? item : JSON.stringify(item)), record.followups?.map(item => item.prompt)]
      .map(txt).join(" ").toLocaleLowerCase("zh-CN").includes(query);
  }
  function sortRecords(items) {
    return [...items].sort((a, b) => Number(effectiveStatus(a) !== "blocked") - Number(effectiveStatus(b) !== "blocked") ||
      String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  }
  function sortTaskRecords(items) {
    return [...items].sort((a, b) => {
      const aDone = isCompleted(a), bDone = isCompleted(b);
      const aActive = effectiveStatus(a) === "in_progress", bActive = effectiveStatus(b) === "in_progress";
      return Number(bActive) - Number(aActive) || Number(aDone) - Number(bDone) ||
        String(b.created_at || "").localeCompare(String(a.created_at || "")) ||
        String(a.id).localeCompare(String(b.id));
    });
  }
  function sourceLabel(record) {
    if (record.type === "bug") return "平台 BUG";
    if (record.type === "task" || record.source_kind === "user_directed") return "用户交办";
    return record.is_shared ? "公司需求 · 共同负责" : "公司需求";
  }
  function dateLabel(value, fallback = "未取得执行记录") {
    if (!value) return fallback;
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return String(value);
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  }
  function followups(record) { return values(record.followups); }
  function previewFollowup(record) {
    if (record.followup_preview) return record.followup_preview;
    const all = followups(record);
    const pending = all.filter(item => item.status === "pending").sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    return pending[0] || [...all].sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))[0] || null;
  }
  function setText(selector, text) { $(selector).textContent = text; }
  function escClass(value) { return String(value || "unknown").replace(/[^a-z0-9_-]/gi, "_"); }

  function zonedParts(epoch, timezone) {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(new Date(epoch));
    return Object.fromEntries(parts.filter(part => part.type !== "literal").map(part => [part.type, Number(part.value)]));
  }
  function offsetAt(epoch, timezone) {
    const p = zonedParts(epoch, timezone);
    return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - Math.floor(epoch / 1000) * 1000;
  }
  function zonedLocalEpoch(year, month, day, hour, minute, timezone) {
    const localAsUtc = Date.UTC(year, month - 1, day, hour, minute, 0);
    let epoch = localAsUtc - offsetAt(localAsUtc, timezone);
    epoch = localAsUtc - offsetAt(epoch, timezone);
    return epoch;
  }
  function nextInspection(nowEpoch, times, timezone, weekdays = []) {
    const now = zonedParts(nowEpoch, timezone);
    const day = new Date(Date.UTC(now.year, now.month - 1, now.day));
    const dayCodes = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"];
    for (let add = 0; add <= 7; add += 1) {
      const date = new Date(day.getTime() + add * 86400000);
      const weekday = dayCodes[date.getUTCDay()];
      if (weekdays.length && !weekdays.includes(weekday)) continue;
      for (const value of [...times].sort()) {
        const [hour, minute] = value.split(":").map(Number);
        const target = zonedLocalEpoch(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate(), hour, minute, timezone);
        if (target > nowEpoch) return { epoch: target, dayOffset: add, time: value };
      }
    }
    return null;
  }
  function renderInspectionPlan() {
    const plan = $("#inspection-plan");
    const schedule = inspectionClock.schedule;
    const state = schedule?.state === "paused" || schedule?.enabled === false || inspectionRun.state === "paused" || inspectionRun.enabled === false
      ? "paused" : inspectionRun.state;
    const stateText = ({ running: "巡检正在运行", queued: "巡检已排队", awaiting_confirmation: "等待巡检结果", failed: "上次巡检失败", partial: "上次巡检部分完成", succeeded: "本时段巡检已完成", paused: "巡检已暂停" })[state] || "";
    if (state === "paused") { plan.textContent = stateText; plan.dataset.state = state; renderInspectionRun(); return; }
    const syncedEpoch = inspectionClock.serverEpoch || inspectionRun.serverEpoch;
    if (!schedule || !schedule.available || schedule.state === "unknown" || !syncedEpoch) {
      plan.textContent = stateText ? `${stateText} · 巡检时间未同步` : "巡检时间未同步";
      plan.dataset.state = state === "unknown" ? "unknown" : state; renderInspectionRun(); return;
    }
    const syncedAt = inspectionClock.syncedAt || inspectionRun.syncedAt;
    const now = syncedEpoch + (performance.now() - syncedAt);
    const authoritativeAt = inspectionRun.nextRunAt || inspectionClock.nextRunAt || schedule.next_run_at || schedule.nextRunAt;
    const nextEpoch = authoritativeAt ? Date.parse(authoritativeAt) : NaN;
    const next = Number.isNaN(nextEpoch) && Array.isArray(schedule.times) ? nextInspection(now, schedule.times, schedule.timezone, Array.isArray(schedule.weekdays) ? schedule.weekdays : []) : null;
    const targetEpoch = Number.isNaN(nextEpoch) ? next?.epoch : nextEpoch;
    if (!targetEpoch) { plan.textContent = stateText ? `${stateText} · 下次时间未同步` : "巡检时间未同步"; plan.dataset.state = state || "unknown"; renderInspectionRun(); return; }
    const seconds = Math.max(0, Math.ceil((targetEpoch - now) / 1000));
    const countdown = seconds < 60 ? "马上开始巡检" : `${Math.ceil(seconds / 60)}分钟后`;
    const label = new Intl.DateTimeFormat("zh-CN", { timeZone: schedule.timezone, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(targetEpoch));
    plan.textContent = `${stateText ? `${stateText} · ` : ""}下次 ${label} · ${countdown}`;
    plan.dataset.state = state === "unknown" ? "active" : state;
    renderInspectionRun();
  }
  function renderWorktimeProgress() {
    const percent = model.workPeriodProgress(Date.now(), workHours.start, workHours.end);
    $("#worktime-fill").style.width = `${percent}%`;
    $("#worktime-percent").textContent = `${percent}%`;
    const label = `${workHours.start}–${workHours.end}`;
    const button = $("#worktime-open");
    button.title = `每日工作时间：${label}（北京时间），点击自定义`;
    button.setAttribute("aria-label", `每日工作时间 ${label}，当前进度 ${percent}%，点击自定义`);
  }
  function safeText(value, limit = 240) {
    return String(value || "").replace(/(?:Bearer\s+)?[A-Za-z0-9_-]{32,}/g, "[已隐藏]")
      .replace(/([?&](?:token|key|secret|code|auth|signature)=)[^&\s]+/gi, "$1[已隐藏]").slice(0, limit);
  }
  function integrationStatusLabel(status) {
    return ({ passed: "检查通过", failed: "检查失败", unknown: "未核实", checking: "检查中", not_configured: "待 Agent 检测" })[status] || "未核实";
  }
  function renderIntegrationStatuses(payload) {
    const list = $("#integration-list"); list.replaceChildren();
    const statuses = payload?.providers || payload?.integrations || {};
    let currentGroup = "", groupList;
    for (const [key, name, group, logo] of integrationProviders) {
      if (group !== currentGroup) {
        currentGroup = group;
        const section = document.createElement("li"); section.className = "integration-group";
        const heading = document.createElement("h4"); heading.textContent = group;
        groupList = document.createElement("ul"); section.append(heading, groupList); list.append(section);
      }
      const item = statuses[key] || {};
      const passed = item.status === "passed" && !!item.checked_at;
      const li = document.createElement("li"); li.className = "integration-row"; li.dataset.state = passed ? "passed" : "unknown";
      const identity = document.createElement("div"); identity.className = "integration-identity";
      const mark = document.createElement("span"); mark.className = `integration-logo integration-logo-${key}`; mark.textContent = logo; mark.setAttribute("aria-hidden", "true");
      const title = document.createElement("strong"); title.textContent = name; identity.append(mark, title);
      const action = document.createElement("button"); action.type = "button"; action.className = "integration-state";
      action.textContent = passed ? (key === "iphone_use" && item.reason_code === "plugin_files_present" ? "已安装" : "已集成") : item.status === "not_configured" ? "去集成" : "待检查";
      if (passed) { action.disabled = true; action.title = `最近检查：${dateLabel(item.checked_at)}${item.safe_next_step ? ` · ${safeText(item.safe_next_step, 240)}` : ""}`; }
      else { action.dataset.integrationPrompt = key; action.title = item.safe_next_step || "点击复制检查与接入提示"; }
      li.append(identity, action); groupList.append(li);
    }
  }
  async function loadIntegrationStatuses() {
    const list = $("#integration-list");
    try {
      const response = await fetch("/api/integrations/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json(); renderIntegrationStatuses(payload);
    } catch (_) {
      renderIntegrationStatuses({ providers: {} });
      const note = document.createElement("li"); note.className = "integration-empty error"; note.textContent = "无法读取真实检测状态；当前均标为未核实。请确认服务端文档工具状态接口已启用。"; list.prepend(note);
    }
  }
  async function checkIntegration(provider, button) {
    button.disabled = true; button.textContent = "检查中…";
    try {
      await postAction(`/api/integrations/${encodeURIComponent(provider)}/check`, {});
      showToast("检查已启动，正在等待真实结果");
      for (let attempt = 0; attempt < 12; attempt += 1) {
        await loadIntegrationStatuses();
        const response = await fetch("/api/integrations/status", { cache: "no-store" });
        if (!response.ok) break;
        const payload = await response.json(), item = (payload.providers || payload.integrations || {})[provider] || {};
        if (item.status !== "checking" && item.status !== "queued") break;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    } catch (error) { showToast(error.message || "检查未能启动", "error"); }
    finally { button.disabled = false; button.textContent = "重新检查"; }
  }
  async function copyIntegrationPrompt(provider) {
    let item = {};
    try {
      const response = await fetch("/api/integrations/status", { cache: "no-store" });
      if (response.ok) { const data = await response.json(); item = (data.providers || data.integrations || {})[provider] || {}; }
    } catch (_) {}
    const name = integrationProviders.find(([key]) => key === provider)?.[1] || provider;
    const gaps = (item.missing_capabilities || []).map(value => safeText(value, 100)).filter(Boolean);
    const details = [item.reason_code && `检查结果：${safeText(item.reason_code, 120)}`, gaps.length && `缺少能力：${gaps.join("、")}`, item.safe_next_step && `建议：${safeText(item.safe_next_step, 240)}`].filter(Boolean).join("\n");
    const purposes = { feishu: "飞书文档的搜索与读取", tencent_docs: "腾讯文档和表格的只读访问", kdocs: "金山文档的搜索与读取", jira: "Jira 需求和项目事项读取", figma: "Figma 文件与设计节点读取", linear: "Linear 缺陷和事项读取", git: "Git 仓库及版本状态读取", iphone_use: "通过 USB 在授权的真实 iPhone 上进行开发和自动化测试" };
    const prompt = provider === "iphone_use" ? (item.prompt_context || "请帮助安装和配置 iPhone Use。先检查当前宿主的 iphone-use-setup Skill 和 PUA 工具，调用 pua_setup(status) 并复用现有配置；若插件缺失，阅读 https://github.com/zhongerxin/iPhone-use 的 README 按安装说明操作。安装后重新打开宿主会话，确认 iphone-use-setup Skill 和 PUA 工具入口可用；不启动或检查手机服务，不操作设备，不要索取或记录凭据。") : `请为知途工作台检查并配置${name}集成，用于${purposes[provider]}。先检查当前宿主可用的官方工具、连接方式和授权状态，复用已有配置；缺少账号授权或目标资源时向用户说明。不要输出、复制、保存或提交凭据，不要修改远端数据。完成后使用用户授权的目标进行真实只读验证，准确记录验证范围和结果；未验证不得标记已集成。验证后通过知途 /api/integrations/${provider}/report 记录状态、工具、Agent、read_only 和简短脱敏证据；检查失败或缺少工具时如实报告。${details ? `\n\n当前检测信息：\n${details}` : ""}`;
    try { await navigator.clipboard.writeText(prompt); showToast("接入提示已复制"); }
    catch (_) { window.prompt("复制以下接入提示", prompt); }
  }
  async function loadDataLocation() {
    const stateEl = $("#data-location-state"); stateEl.textContent = "读取中"; stateEl.dataset.state = "unknown";
    try {
      const response = await fetch("/api/data-location", { cache: "no-store" });
      if (!response.ok) throw new Error();
      const data = await response.json();
      dataLocationRevision = data.revision ?? null;
      $("#data-location-current").textContent = txt(data.current_path, "未提供");
      $("#data-location-default").textContent = txt(data.default_path, "未提供");
      const legacyPath = data.legacy_path || data.migration?.legacy_path;
      $("#data-location-legacy-row").hidden = !(data.legacy_exists && legacyPath);
      if (data.legacy_exists && legacyPath) $("#data-location-legacy").textContent = txt(legacyPath);
      const backupPath = data.backup_path || data.migration?.backup_path;
      $("#data-location-backup-row").hidden = !backupPath;
      if (backupPath) $("#data-location-backup").textContent = txt(backupPath);
      $("#data-location-target").value = data.current_path || data.default_path || "";
      stateEl.textContent = data.is_default ? "默认位置" : "自定义位置"; stateEl.dataset.state = "success";
    } catch (_) {
      $("#data-location-current").textContent = "暂不可读取"; $("#data-location-default").textContent = "~/.waylume";
      stateEl.textContent = "未核实"; stateEl.dataset.state = "unknown";
    }
  }
  async function previewDataLocation(event) {
    event.preventDefault();
    const targetPath = $("#data-location-target").value.trim(), result = $("#data-location-preview-result"), button = $("#data-location-preview");
    if (!targetPath) { result.textContent = "请输入目标目录。"; return; }
    button.disabled = true; result.textContent = "正在检查目标目录…"; $("#data-location-migrate").disabled = true;
    try {
      const data = await postAction("/api/data-location/preview", { target_path: targetPath, expected_revision: dataLocationRevision });
      dataLocationPreview = data;
      const risks = Array.isArray(data.risks) ? data.risks.map(value => safeText(value, 180)).filter(Boolean) : [];
      const canMigrate = data.can_migrate === true && !!data.preview_id && !!data.confirmation_token && data.source_readable === true && data.target_parent_writable === true && (!data.target_exists || data.target_empty === true);
      result.textContent = [canMigrate ? "目录检查通过。迁移将创建暂存副本、校验后切换，并保留原目录备份。" : "目录检查未通过，当前不能迁移。", data.source_path && `当前目录：${safeText(data.source_path, 240)}`, data.target_path && `目标目录：${safeText(data.target_path, 240)}`, data.estimated_files != null && `预计文件：${data.estimated_files}`, data.estimated_bytes != null && `预计数据量：${data.estimated_bytes} 字节`, data.confirmation_required && "执行前需要再次确认。", ...risks].filter(Boolean).join("\n");
      $("#data-location-migrate").disabled = !canMigrate;
    } catch (error) { dataLocationPreview = null; result.textContent = error.message || "目录检查失败；未执行迁移。"; }
    finally { button.disabled = false; }
  }
  async function migrateDataLocation() {
    const preview = dataLocationPreview;
    if (preview?.can_migrate !== true || !preview.preview_id || !preview.confirmation_token || preview.source_readable !== true || preview.target_parent_writable !== true || (preview.target_exists && preview.target_empty !== true)) return;
    if (!window.confirm("确认迁移个人工作台数据？服务会先校验新目录，成功后保留旧目录备份。")) return;
    const button = $("#data-location-migrate"), result = $("#data-location-preview-result"); button.disabled = true;
    result.textContent = "迁移已提交，正在等待校验结果…";
    try {
      let data = await postAction("/api/data-location/migrate", { target_path: $("#data-location-target").value.trim(), preview_id: preview.preview_id, confirmation_token: preview.confirmation_token, expected_revision: preview.revision ?? dataLocationRevision });
      if (data.job_id) {
        const deadline = Date.now() + 120000;
        while (Date.now() < deadline && !["verified", "succeeded", "failed", "rolled_back"].includes(data.status)) {
          await new Promise(resolve => setTimeout(resolve, 1000));
          const response = await fetch(`/api/data-location/migrations/${encodeURIComponent(data.job_id)}`, { cache: "no-store" });
          if (!response.ok) throw new Error("无法读取迁移状态；请检查工作台数据位置，不要重复提交迁移。");
          data = await response.json();
          result.textContent = `迁移状态：${data.status || "处理中"}${data.progress != null ? ` · ${data.progress}%` : ""}${data.message ? `\n${safeText(data.message, 300)}` : ""}`;
        }
      }
      if (data.status === "verified" && data.verification?.verified === true) {
        result.textContent = `迁移并校验成功。旧目录备份：${txt(data.backup_path, "已保留")}`; await loadDataLocation();
      } else if (["failed", "rolled_back"].includes(data.status)) {
        result.textContent = `迁移未完成：${safeText(data.message || data.error || data.status, 300)}${data.rollback?.succeeded ? "\n已恢复原目录。" : ""}`;
      } else { result.textContent = "迁移结果尚未确认；请稍后查看状态，勿重复发起。"; }
    } catch (error) { result.textContent = error.message || "迁移失败；请先核对当前数据位置。"; }
    finally { button.disabled = true; dataLocationPreview = null; }
  }
  function openSettings() {
    $("#settings-dialog").showModal(); loadDataLocation(); loadIntegrationStatuses();
    $("#settings-work-hours-value").textContent = `${workHours.start}–${workHours.end}`;
    $("#settings-theme").value = document.body.dataset.theme || "minimal";
  }
  function openWorkHours() {
    $("#work-hours-start").value = workHours.start;
    $("#work-hours-end").value = workHours.end;
    $("#work-hours-feedback").textContent = "";
    $("#work-hours-dialog").showModal();
  }
  async function saveWorkHours(event) {
    event.preventDefault();
    const start = $("#work-hours-start").value, end = $("#work-hours-end").value;
    const feedback = $("#work-hours-feedback");
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(start) || !/^([01]\d|2[0-3]):[0-5]\d$/.test(end) || start >= end) {
      feedback.textContent = "结束时间需晚于开始时间，请检查时间范围。"; feedback.classList.add("error"); return;
    }
    const button = $("#work-hours-form button[type=submit]"); button.disabled = true;
    try {
      const result = await postAction("/api/preferences/work-hours", { start, end });
      workHours = result.work_hours; $("#work-hours-dialog").close(); renderWorktimeProgress();
      $("#settings-work-hours-value").textContent = `${workHours.start}–${workHours.end}`;
      if (state.data) state.data.revision = result.revision;
      showToast(`工作时间已保存：${start}–${end}`);
    } catch (error) { feedback.textContent = error.message || "保存失败，请重试。"; feedback.classList.add("error"); }
    finally { button.disabled = false; }
  }
  async function refreshInspectionSchedule() {
    try {
      const response = await fetch("/api/inspection-schedule", { cache: "no-store" });
      if (!response.ok) throw new Error("schedule_unavailable");
      const schedule = await response.json();
      const epoch = Date.parse(schedule.server_time);
      if (!schedule.available || Number.isNaN(epoch)) throw new Error("schedule_unsynced");
      inspectionClock.schedule = schedule;
      inspectionClock.serverEpoch = epoch;
      inspectionClock.syncedAt = performance.now();
      inspectionClock.nextRunAt = schedule.next_run_at || schedule.nextRunAt || null;
    } catch (_) {
      inspectionClock.schedule = null; inspectionClock.serverEpoch = null; inspectionClock.syncedAt = null;
    }
    renderInspectionRun();
    renderInspectionPlan();
  }

  function inspectionFailureDetail(event = {}, payload = {}) {
    const candidates = [event.failure_summary, event.error_summary, event.detail, event.error, payload.failure_summary, payload.error_summary, payload.detail, payload.error];
    for (const value of candidates) {
      if (typeof value === "string" && value.trim()) return safeText(value, 220);
      if (value && typeof value === "object" && !Array.isArray(value)) {
        const summary = value.summary || value.message || value.error || value.detail;
        if (typeof summary === "string" && summary.trim()) return safeText(summary, 220);
      }
    }
    return "";
  }
  function inspectionMessage(status, event = {}, payload = {}) {
    const joined = payload.joined_existing === true;
    const failureDetail = inspectionFailureDetail(event, payload);
    if (status === "paused") return "巡检已暂停";
    if (status === "queued") return joined ? "已加入当前队列 · 尚未执行" : "已排队 · 尚未执行";
    if (status === "running") return joined ? "已加入正在进行的巡检" : "巡检中";
    if (status === "succeeded") return "本时段巡检已完成";
    if (status === "partial") return `上次巡检部分完成${failureDetail ? ` · ${failureDetail}` : ""}`;
    if (status === "failed") return `巡检失败，可重试${failureDetail ? ` · ${failureDetail}` : ""}`;
    if (status === "awaiting_confirmation") return "等待巡检结果确认";
    return "";
  }
  function applyInspectionStatus(payload = {}, fromRun = false) {
    const active = payload.active_event || payload.event || null;
    const event = active || payload.last_event || {};
    const status = event.status || payload.status || payload.slot_state || "unknown";
    inspectionRun.slotId = event.slot_id || payload.slot_id || inspectionRun.slotId;
    inspectionRun.eventId = event.event_id || payload.event_id || inspectionRun.eventId;
    if (Object.prototype.hasOwnProperty.call(payload, "next_run_at")) inspectionRun.nextRunAt = payload.next_run_at;
    inspectionRun.enabled = typeof payload.enabled === "boolean" ? payload.enabled : inspectionRun.enabled;
    inspectionRun.windowOpen = typeof payload.window_open === "boolean" ? payload.window_open : inspectionRun.windowOpen;
    const recognized = ["queued", "running", "succeeded", "partial", "failed", "awaiting_confirmation", "paused"].includes(status);
    if (!recognized) {
      inspectionRun.state = inspectionRun.enabled === false ? "paused" : "unknown";
      inspectionRun.message = inspectionRun.enabled === false ? "巡检已暂停" : "巡检状态未同步";
      renderInspectionRun();
      renderInspectionPlan();
      return false;
    }
    inspectionRun.state = status;
    const serverEpoch = Date.parse(payload.server_time || payload.observed_at || "");
    if (!Number.isNaN(serverEpoch)) { inspectionRun.serverEpoch = serverEpoch; inspectionRun.syncedAt = performance.now(); }
    const message = inspectionMessage(status, event, payload);
    if (message) inspectionRun.message = message;
    else if (payload.enabled === false) inspectionRun.message = "巡检已暂停";
    else if (payload.window_open === false) inspectionRun.message = "当前不在巡检时段";
    else if (fromRun) inspectionRun.message = "已受理 · 等待巡检状态";
    else inspectionRun.message = "巡检状态已同步";
    renderInspectionRun();
    renderInspectionPlan();
    return true;
  }
  function renderInspectionRun() {
    const button = $("#inspection-run"), status = $("#inspection-run-status");
    const busyState = ["queued", "running", "awaiting_confirmation"].includes(inspectionRun.state);
    const succeeded = inspectionRun.state === "succeeded" && inspectionRun.slotId;
    const paused = inspectionRun.state === "paused" || inspectionRun.enabled === false || inspectionClock.schedule?.state === "paused" || inspectionClock.schedule?.enabled === false;
    button.textContent = paused ? "巡检已暂停" : inspectionRun.state === "running" ? "巡检中" : succeeded ? "本时段已完成" : "立即巡检";
    button.disabled = inspectionRun.busy || busyState || !!succeeded || paused || inspectionRun.windowOpen === false;
    const plan = $("#inspection-plan");
    const duplicate = plan && inspectionRun.message && plan.textContent && (inspectionRun.message.trim() === plan.textContent.trim());
    status.textContent = duplicate ? "" : inspectionRun.message;
    status.dataset.state = inspectionRun.state;
  }
  async function refreshInspectionStatus() {
    try {
      const response = await fetch("/api/bug-inspection/status", { cache: "no-store" });
      if (!response.ok) throw new Error("inspection_status_unavailable");
      applyInspectionStatus(await response.json());
    } catch (_) {
      inspectionRun.message = inspectionRun.eventId
        ? "巡检状态暂不可用 · 保留最近状态"
        : "巡检状态暂不可用";
      renderInspectionRun();
    }
  }
  async function runInspection() {
    if (inspectionRun.busy || ["queued", "running", "awaiting_confirmation", "succeeded", "paused"].includes(inspectionRun.state) || inspectionRun.enabled === false || inspectionRun.windowOpen === false || inspectionClock.schedule?.state === "paused" || inspectionClock.schedule?.enabled === false) return;
    inspectionRun.busy = true;
    inspectionRun.message = "正在提交巡检请求…";
    renderInspectionRun();
    try {
      const response = await fetch("/api/bug-inspection/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `http_${response.status}`);
      const applied = applyInspectionStatus(payload, true);
      if (!applied) {
        inspectionRun.state = "unknown";
        inspectionRun.message = "请求已返回 · 巡检状态未同步";
        renderInspectionRun();
      }
      await refreshInspectionStatus();
    } catch (_) {
      inspectionRun.message = inspectionRun.eventId
        ? "请求未确认 · 保留最近巡检状态"
        : "巡检请求暂不可用";
      renderInspectionRun();
    } finally {
      inspectionRun.busy = false;
      renderInspectionRun();
    }
  }

  function sourceCategory(source) {
    const category = source.overrides?.category || source.category;
    if (category) return ({ cloud_document: "云文档", web_link: "网页链接", acceptance_reference: "验收参考", file: "本地文件" })[category] || category;
    if (source.type === "file") return "本地文件";
    try {
      const host = new URL(source.url).hostname.toLowerCase();
      return ["docs.qq.com", "kdocs.cn", "feishu.cn", "larksuite.com", "yuque.com", "docs.google.com", "figma.com"].some(domain => host === domain || host.endsWith(`.${domain}`)) ? "云文档" : "网页链接";
    }
    catch (_) { return "网页链接"; }
  }
  function safeHttpUrl(value) {
    try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
    catch (_) { return null; }
  }
  function makeSourceRow(source, active) {
    const row = document.createElement("li"); row.className = "source-row";
    const info = document.createElement("div"); info.className = "source-info";
    const name = document.createElement("strong"); name.className = "source-name"; name.textContent = source.overrides?.name || source.name || source.filename || source.url || "未命名来源";
    const lifecycle = source.suspected_deprecated || source.lifecycle_status === "suspected_deprecated" ? "疑似废弃" : source.lifecycle_status === "deprecated" ? "已废弃" : "";
    const verificationState = source.verification_state || source.overrides?.verification_state || "not_checked";
    const verificationLabels = { not_checked: "未核查", verified: "已核验", failed: "核验失败" };
    const kind = document.createElement("span"); kind.className = "source-kind"; kind.textContent = `${sourceCategory(source)} · ${verificationLabels[verificationState] || verificationState}${lifecycle ? ` · ${lifecycle}` : ""}`;
    info.append(name, kind);
    if (source.lifecycle_reason) { const reason = document.createElement("span"); reason.className = "source-kind"; reason.textContent = source.lifecycle_reason; info.append(reason); }
    const readStatus = source.read_state || source.read_status || "not_checked";
    const readLabels = { success: "读取成功", succeeded: "读取成功", complete: "读取成功", partial: "部分读取", failed: "读取失败", error: "读取失败", running: "正在读取", in_progress: "正在读取", not_checked: "尚未读取", not_read: "尚未读取", unknown: "读取状态未知" };
    const readState = document.createElement("span"); readState.className = `source-kind source-read-state source-read-${escClass(readStatus)}`;
    readState.textContent = readLabels[readStatus] || "读取状态未知";
    const gaps = Array.isArray(source.read_gap) ? source.read_gap : source.read_gap ? [source.read_gap] : [];
    if (gaps.length && ["failed", "error", "partial"].includes(readStatus)) readState.textContent += ` · 缺口：${gaps.map(item => typeof item === "string" ? item : item.description || item.message || JSON.stringify(item)).join("；")}`;
    info.append(readState);
    const attemptedAt = source.latest_read_attempt_at || source.last_read_at;
    if (attemptedAt) { const attempted = document.createElement("span"); attempted.className = "source-kind"; attempted.textContent = `最近尝试读取 ${dateLabel(attemptedAt)}`; info.append(attempted); }
    const successfulAt = source.last_successful_read_at;
    if (successfulAt) { const succeeded = document.createElement("span"); succeeded.className = "source-kind"; succeeded.textContent = `上次成功读取 ${dateLabel(successfulAt)}`; info.append(succeeded); }
    if (source.last_verified_at) { const checked = document.createElement("span"); checked.className = "source-kind"; checked.textContent = `最近核验 ${dateLabel(source.last_verified_at)}`; info.append(checked); }
    if (source.overrides?.purpose || source.purpose) { const purpose = document.createElement("span"); purpose.className = "source-kind"; purpose.textContent = source.overrides?.purpose || source.purpose; info.append(purpose); }
    if (source.type === "link" && source.url) {
      const urlText = document.createElement("span"); urlText.className = "source-url"; urlText.textContent = source.url; info.append(urlText);
    }
    const actions = document.createElement("div"); actions.className = "source-row-actions";
    const open = document.createElement("a"); open.className = "source-text-action"; open.textContent = "打开";
    if (active) {
      const openPath = source.type === "link" ? source.open_path : source.download_url;
      const safeLocalPath = typeof openPath === "string" && /^\/api\/sources\/[A-Za-z0-9_-]{1,100}\/(?:open|file)$/.test(openPath);
      if (safeLocalPath && source.type === "link" && safeHttpUrl(source.url || "")) {
        open.href = source.url; open.target = "_blank"; open.rel = "noopener noreferrer";
      } else if (safeLocalPath && source.type !== "link" && window.workbenchTransport?.openSource) {
        open.href = "#";
        open.addEventListener("click", event => { event.preventDefault(); window.workbenchTransport.openSource(source, openPath); });
      } else { open.href = "#"; open.setAttribute("aria-disabled", "true"); open.addEventListener("click", event => event.preventDefault()); }
      actions.append(open);
    }
    const toggle = document.createElement("button"); toggle.type = "button"; toggle.className = "source-text-action";
    toggle.textContent = active ? "移出" : "恢复"; toggle.dataset.sourceAction = active ? "remove" : "restore"; toggle.dataset.sourceId = source.id;
    const lifecycleOverride = document.createElement("button"); lifecycleOverride.type = "button"; lifecycleOverride.className = "source-text-action";
    lifecycleOverride.textContent = source.lifecycle_override === "active" ? "非废弃·已锁定" : "非废弃";
    lifecycleOverride.dataset.sourceAction = "nondeprecated"; lifecycleOverride.dataset.sourceId = source.id;
    actions.append(lifecycleOverride, toggle); row.append(info, actions); return row;
  }
  function renderSources() {
    const active = state.sources.filter(source => source.status !== "removed" && source.active !== false);
    const removed = state.sources.filter(source => source.status === "removed" || source.active === false);
    $("#source-count").textContent = String(active.length);
    $("#removed-source-count").textContent = String(removed.length);
    $("#source-active-list").replaceChildren(...active.map(source => makeSourceRow(source, true)));
    $("#source-removed-list").replaceChildren(...removed.map(source => makeSourceRow(source, false)));
    $("#source-active-empty").hidden = active.length > 0;
    $("#removed-sources-section").hidden = removed.length === 0;
  }
  function sourceFeedback(message, kind = "") {
    const feedback = $("#source-feedback"); feedback.textContent = message; feedback.className = `source-feedback${kind ? ` ${kind}` : ""}`;
  }
  function sourceError(payload, status) {
    const errors = {
      source_duplicate: "这条来源已在清单中。", invalid_source_url: "链接格式无效，请使用 http 或 https 地址。",
      invalid_source_category: "资料类别无效。", invalid_source_name: "请填写有效的来源名称。",
      source_file_size_invalid: "文件为空或超过 15 MiB 大小限制。", unsupported_source_file_type: "当前文件类型不受支持。",
      source_file_type_mismatch: "文件内容与扩展名不符。", invalid_source_filename: "文件名无效。",
      source_archive_limits_exceeded: "Office 文件内容超出安全限制。", macros_not_allowed: "含宏文件不能加入来源清单。",
      source_text_must_be_utf8: "文本文件必须使用 UTF-8 编码。", source_not_found: "找不到这条来源，请刷新清单。",
      source_file_unavailable: "托管文件暂时不可用。", payload_too_large: "文件超过上传请求大小限制。"
    };
    return payload.message || errors[payload.error] || `操作失败，请重试（${payload.error || status}）`;
  }
  async function sourceRequest(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", ...options });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(sourceError(payload, response.status));
    return payload;
  }
  async function loadSources() {
    try {
      const data = await sourceRequest("/api/sources?include_removed=1");
      state.sources = Array.isArray(data.sources) ? data.sources : [];
      renderSources(); sourceFeedback("来源清单已更新。");
    } catch (error) {
      sourceFeedback(error.message || "无法读取需求来源，请确认本地服务正常。", "error");
    }
  }
  function setSourcesBusy(busy, message = "") {
    state.sourcesBusy = busy;
    ["#source-add-file", "#source-toggle-link", "#source-link-submit"].forEach(selector => { const button = $(selector); if (button) button.disabled = busy; });
    $$("[data-source-action]").forEach(button => { button.disabled = busy; });
    if (message) sourceFeedback(message, busy ? "busy" : "");
  }
  async function encodeFile(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    return btoa(binary);
  }
  async function uploadSourceFile(file) {
    if (!file) return;
    const maxBytes = 15 * 1024 * 1024;
    if (file.size > maxBytes) { sourceFeedback("文件超过 15 MiB，请选择更小的文件。", "error"); return; }
    setSourcesBusy(true, `正在添加“${file.name}”…`);
    try {
      const content_base64 = await encodeFile(file);
      await sourceRequest("/api/sources/file", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename: file.name, content_base64 }) });
      await loadSources(); sourceFeedback(`已添加文件“${file.name}”。`);
    } catch (error) { sourceFeedback(error.message || "文件添加失败，请重试。", "error"); }
    finally { setSourcesBusy(false); $("#source-file-input").value = ""; }
  }
  async function submitSourceLink(event) {
    event.preventDefault();
    const name = $("#source-link-name").value.trim(), url = $("#source-link-url").value.trim(), category = $("#source-link-category").value;
    if (!name || !url) { sourceFeedback("请填写名称和链接。", "error"); return; }
    if (!safeHttpUrl(url)) { sourceFeedback("链接格式无效，请使用 http 或 https 地址。", "error"); return; }
    setSourcesBusy(true, "正在添加链接…");
    try {
      await sourceRequest("/api/sources/link", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, url, category }) });
      $("#source-link-name").value = ""; $("#source-link-url").value = ""; $("#source-link-form").hidden = true; $("#source-toggle-link").setAttribute("aria-expanded", "false");
      await loadSources(); sourceFeedback("已添加需求来源链接。");
    } catch (error) { sourceFeedback(error.message || "链接添加失败，请重试。", "error"); }
    finally { setSourcesBusy(false); }
  }
  async function changeSourceActive(sourceId, action) {
    if (state.sourcesBusy) return;
    const route = `/api/sources/${encodeURIComponent(sourceId)}${action === "restore" ? "/restore" : ""}`;
    setSourcesBusy(true, action === "restore" ? "正在恢复来源…" : "正在移出清单…");
    try {
      if (action === "restore") await sourceRequest(route, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      else await sourceRequest(route, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}" });
      await loadSources(); sourceFeedback(action === "restore" ? "来源已恢复到清单。" : "来源已移出清单，可在下方恢复。");
    } catch (error) { sourceFeedback(error.message || "来源状态更新失败，请重试。", "error"); }
    finally { setSourcesBusy(false); }
  }
  async function setSourceLifecycle(sourceId) {
    const source = state.sources.find(item => item.id === sourceId);
    if (!source || state.sourcesBusy) return;
    const lifecycleOverride = source.lifecycle_override === "active" ? null : "active";
    setSourcesBusy(true, lifecycleOverride ? "正在锁定来源为非废弃…" : "正在取消来源状态锁定…");
    try {
      await sourceRequest(`/api/sources/${encodeURIComponent(sourceId)}/lifecycle`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lifecycle_override: lifecycleOverride }) });
      await loadSources(); sourceFeedback(lifecycleOverride ? "来源已设为非废弃并锁定。" : "来源状态锁定已取消。");
    } catch (error) { sourceFeedback(error.message || "来源状态更新失败。", "error"); }
    finally { setSourcesBusy(false); }
  }
  function updateThemeButton() {
    const theme = document.body.dataset.theme || "minimal";
    const labels = { minimal: "简洁风", wood: "拟物风", dark: "深色简洁" };
    const next = theme === "minimal" ? "深色简洁" : "简洁风";
    const button = $("#theme-toggle");
    button.textContent = `风格·${theme === "dark" ? "深色" : labels[theme]}`;
    button.setAttribute("aria-label", `当前风格：${labels[theme]}。点击切换到${next}`);
    button.setAttribute("aria-pressed", String(theme === "dark"));
  }

  function makeFollowupPreview(record) {
    const preview = previewFollowup(record);
    if (!preview) return null;
    const block = document.createElement("div"); block.className = "followup-preview";
    const head = document.createElement("div"); head.className = "followup-preview-head";
    const label = document.createElement("span"); label.textContent = "跟进提示词";
    const status = document.createElement("span"); status.className = `followup-state ${escClass(preview.status)}`;
    status.textContent = ({ pending: "待跟进", claimed: "处理中", processing: "处理中", handled: "已处理", failed: "执行失败" })[preview.status] || "待查看";
    head.append(label, status);
    const prompt = document.createElement("p"); prompt.className = "followup-prompt"; prompt.textContent = txt(preview.prompt);
    block.append(head, prompt);
    return block;
  }

  function briefClauses(value) {
    return txt(value, "").split(/(?<=[。！？.!?；;])\s*|[\r\n]+/).map(clause => clause.trim()).filter(Boolean);
  }
  function cleanBriefClause(clause) {
    let text = clause.trim().replace(/[。！？.!?；;]+$/g, "");
    const reachable = text.match(/(?:Debug\s*)?构建\s*(?:可达|已可达|可进入)\s*(.+)$/i);
    if (reachable) text = `${reachable[1].replace(/\s+/g, "")}可进入`;
    if (/^(?:Figma|视觉稿)(?:\s*全量)?(?:画板|界面|页面)和状态未逐项验收$/i.test(text)) text = "视觉状态待补验收";
    if (/(?:\/Users\/|\/(?:private\/)?(?:tmp|var|home)\/|\b[0-9a-f]{12,}\b|当前\s*HEAD|\bHEAD\b|\bcommit\b|提交哈希|源码路径|调试红字)/i.test(text)) return "";
    return text;
  }
  function cardBrief(record) {
    if (record.type === "task" && record.latest_brief) return txt(record.latest_brief);
    const currentStatus = statusLabel(effectiveStatus(record));
    const candidates = [record.status_detail, ...values(record.notes), record.next_step];
    const statusNorm = currentStatus.toLocaleLowerCase("zh-CN").replace(/[\s，。；;、:：]+/g, "");
    const rawStatus = String(record.status || "").toLocaleLowerCase("zh-CN");
    const clauses = candidates.flatMap(briefClauses).map(cleanBriefClause).filter(Boolean);
    const selected = [];
    for (const clause of clauses) {
      const normalized = clause.toLocaleLowerCase("zh-CN").replace(/[\s，。；;、:：]+/g, "");
      if (normalized === statusNorm || normalized === rawStatus || selected.includes(clause)) continue;
      selected.push(clause);
      if (selected.length === 2) break;
    }
    if (selected.length) return selected.join("；");
    if (record.type === "bug") return `来源状态：${statusLabel(sourceStatus(record))}；我方阶段：${txt(record.our_status || record.triage, "未记录")}`;
    return `当前状态：${currentStatus}`;
  }

  function makeCard(record) {
    const article = document.createElement("article");
    article.dataset.recordId = record.id;
    const pin = document.createElement("span"); pin.className = "pushpin"; pin.setAttribute("aria-hidden", "true"); article.append(pin);
    const done = isCompleted(record), cancelled = isCancelled(record), terminal = done || cancelled, busy = state.busy.has(record.id);
    article.className = `record-card${terminal ? " record-completed record-terminal" : ""}${cancelled ? " record-cancelled" : ""}${record.transfer_disposition === "transfer_pending" ? " transfer-pending" : ""}${busy ? " is-busy" : ""}`;
    const head = document.createElement("div"); head.className = "record-head";
    const idLabel = document.createElement("span"); idLabel.className = "record-id"; idLabel.textContent = record.display_id || record.id; idLabel.title = record.display_id && record.display_id !== record.id ? `原始编号：${record.id}` : record.id; head.append(idLabel);
    const status = document.createElement("span"); status.className = `status-pill ${record.transfer_disposition === "transfer_pending" ? "transfer-pending" : escClass(record.type === "bug" ? (record.platform_status || record.status) : effectiveStatus(record))}`;
    const statusCode = effectiveStatus(record);
    status.textContent = record.transfer_disposition === "transfer_pending" ? "待转交" : record.transfer_disposition === "transferred" ? "已转交" : cancelled ? "已取消" : done ? "已完成" : statusLabel(statusCode === "in_progress" || statusCode === "on_hold" || record.type === "task" ? statusCode : sourceStatus(record)); head.append(status);
    const open = document.createElement("button"); open.type = "button"; open.className = "record-title-button"; open.dataset.openRecord = record.id; open.setAttribute("aria-label", `查看详情：${record.title}`);
    const title = document.createElement("span"); title.className = "record-title"; title.textContent = record.title; open.append(title);
    const summary = document.createElement("p"); summary.className = "card-summary"; summary.textContent = record.latest_brief || cardBrief(record);
    article.append(head, open, summary);
    if (effectiveStatus(record) === "in_progress" && record.current_agent_session_id) {
      const processing = document.createElement("p"); processing.className = "agent-processing";
      processing.textContent = `${txt(record.current_agent_name, "Agent")}正在处理中`;
      article.append(processing);
    }
    const preview = makeFollowupPreview(record); if (preview) article.append(preview);
    const actions = document.createElement("div"); actions.className = "card-actions";
    const complete = document.createElement("button"); complete.type = "button"; complete.className = terminal ? "complete-button is-complete" : "complete-button";
    complete.dataset.completeId = record.id; complete.textContent = cancelled ? "已取消" : done ? (hasManualCompletion(record) ? "撤销完成" : "✓ 已完成") : "完成";
    complete.disabled = busy || (terminal && (!done || !hasManualCompletion(record))); complete.setAttribute("aria-pressed", String(done));
    const followup = document.createElement("button"); followup.type = "button"; followup.className = "mini-button"; followup.dataset.followupId = record.id; followup.textContent = "跟进"; followup.disabled = busy;
    if (record.transfer_disposition === "transfer_pending") { const transfer = document.createElement("button"); transfer.type = "button"; transfer.className = "transfer-button"; transfer.dataset.transferId = record.id; transfer.textContent = "已转交"; transfer.disabled = busy; actions.append(followup, transfer); }
    else actions.append(followup, complete);
    article.append(actions);
    return article;
  }
  function populateMasonryList(list, cards) {
    if (!masonryLists.has(list)) { masonryLists.add(list); masonryObserver.observe(list); }
    const columns = [document.createElement("div"), document.createElement("div")];
    columns.forEach(column => { column.className = "masonry-column"; });
    list.replaceChildren(...columns);
    const heights = [0, columns[1].clientWidth > 0 ? 0 : Number.POSITIVE_INFINITY];
    const canMeasure = list.clientWidth > 0;
    cards.forEach((card, index) => {
      card.dataset.masonryOrder = String(index);
      const columnIndex = canMeasure ? model.shortestMasonryColumn(heights) : index % 2;
      const column = columns[columnIndex];
      column.append(card);
      heights[columnIndex] += card.offsetHeight + (parseFloat(getComputedStyle(column).rowGap) || 0);
    });
  }
  function relayoutMasonryList(list) {
    if (!list?.classList.contains("record-list")) return;
    const cards = [...list.querySelectorAll(":scope > .masonry-column > .record-card")]
      .sort((a, b) => Number(a.dataset.masonryOrder) - Number(b.dataset.masonryOrder));
    if (cards.length) populateMasonryList(list, cards);
  }
  const masonryWidths = new WeakMap();
  const masonryLists = new Set();
  const masonryObserver = new ResizeObserver(entries => {
    for (const entry of entries) {
      const width = entry.contentRect.width;
      if (masonryWidths.get(entry.target) !== width) {
        masonryWidths.set(entry.target, width);
        relayoutMasonryList(entry.target);
      }
    }
  });
  function pruneMasonryObservers() {
    for (const list of masonryLists) if (!list.isConnected) { masonryObserver.unobserve(list); masonryLists.delete(list); masonryWidths.delete(list); }
  }
  document.fonts?.ready?.then(() => $$(".record-list").forEach(relayoutMasonryList));

  function renderGroup(group) {
    const records = sortRecords(groupRecords(group));
    const unfinished = records.filter(record => !isTerminal(record));
    const completed = records.filter(isTerminal);
    const list = $(group.list);
    $(group.count).textContent = `${records.length} 项`;
    if (group.key === "work" || group.key === "bug") {
      const completion = model.partitionTerminalRecords(completed);
      populateMasonryList(list, [...unfinished, ...completion.todayRecords].map(makeCard));
      $(group.empty).hidden = unfinished.length > 0 || completion.todayRecords.length > 0;
      const fold = $(group.done); fold.hidden = completion.outerFoldHidden;
      $(group.doneCount).textContent = String(completion.groups.reduce((count, item) => count + item.records.length, 0));
      const dateList = $(group.doneList); dateList.className = "completed-date-list";
      dateList.replaceChildren(...completion.groups.map(({ date, records: rows, open }) => {
        const section = document.createElement("details"); section.className = "completion-date-group"; section.dataset.preserveKey = `${group.key}-completed-${date}`;
        section.open = open;
        const allCancelled = rows.every(isCancelled), allCompleted = rows.every(isCompleted);
        const dateKind = allCancelled ? "取消日期" : allCompleted ? "完成日期" : "终结日期";
        const summary = document.createElement("summary"); summary.textContent = date === "unknown" ? `${dateKind}未知 · ${rows.length}` : `${dateKind} ${date} · ${rows.length}`;
        const doneList = document.createElement("div"); doneList.className = "record-list completed-list"; populateMasonryList(doneList, rows.map(makeCard));
        section.append(summary, doneList); return section;
      }));
    } else {
      populateMasonryList(list, unfinished.map(makeCard));
      $(group.empty).hidden = unfinished.length > 0;
      const fold = $(group.done); fold.hidden = completed.length === 0;
      $(group.doneCount).textContent = String(completed.length);
      populateMasonryList($(group.doneList), completed.map(makeCard));
    }
  }
  function renderTaskBatches() {
    const allTasks = sortTaskRecords((state.data?.delegated || [])
      .filter(record => state.showArchived || effectiveStatus(record) !== "archived")
      .filter(matchesSearch));
    const batches = Array.isArray(state.data?.batches) ? state.data.batches : [];
    const currentBatchId = state.data?.current_batch_id || batches.find(batch => batch.state === "current")?.id || null;
    const partition = model.partitionTaskBatches(allTasks, batches, currentBatchId, state.data?.legacy_task_ids || []);
    const { current, currentTasks, currentBatchTasks, legacyOpen, legacyDone, pastBatches, unlisted, unbatchedUnknown } = partition;

    $("#task-count").textContent = `${allTasks.length} 项`;
    const currentSection = $("#task-current-section");
    currentSection.hidden = !currentBatchId || currentTasks.length === 0;
    const currentEnded = currentBatchTasks.length > 0 && currentBatchTasks.every(isTerminal);
    const currentComplete = currentEnded && currentBatchTasks.every(isCompleted);
    $("#task-current-state").textContent = current ? (currentComplete ? "本批已完成" : currentEnded ? "本批已结束" : "正在处理") : "批次状态同步中";
    $("#task-current-state").className = currentEnded ? "task-batch-state is-complete" : "task-batch-state";
    populateMasonryList($("#task-current-records"), sortTaskRecords(currentTasks).map(record => makeTaskCard(record, true)));

    $("#task-legacy-section").hidden = legacyOpen.length === 0;
    populateMasonryList($("#task-legacy-records"), sortTaskRecords(legacyOpen).map(record => makeTaskCard(record, false)));
    $("#task-legacy-completed").hidden = legacyDone.length === 0;
    $("#task-legacy-completed-count").textContent = String(legacyDone.length);
    populateMasonryList($("#task-legacy-completed-list"), sortTaskRecords(legacyDone).map(record => makeTaskCard(record, false)));

    const pastContainer = $("#task-past-batch-list");
    const renderedPast = pastBatches.map(item => makePastBatch(item.batch, item.records));
    for (const [id, records] of unlisted) renderedPast.push(makePastBatch({ id, state: "past" }, records));
    if (unbatchedUnknown.length) renderedPast.push(makePastBatch({ label: "批次未标记" }, unbatchedUnknown));
    pastContainer.replaceChildren(...renderedPast);
    $("#task-past-count").textContent = String(renderedPast.length);
    $("#task-past-batches").hidden = renderedPast.length === 0;
    $("#task-empty").hidden = allTasks.length > 0;
  }
  function makeTaskCard(record, inCurrentBatch) {
    const card = makeCard(record);
    card.classList.add("task-card");
    if (inCurrentBatch && isCompleted(record)) card.classList.add("batch-task-completed");
    const summary = card.querySelector(".card-summary");
    if (summary) {
      summary.classList.add("task-brief");
      summary.tabIndex = 0;
      summary.setAttribute("aria-label", `最新简报：${record.latest_brief ? txt(record.latest_brief) : cardBrief(record)}`);
    }
    return card;
  }
  function makePastBatch(batch, records) {
    const details = document.createElement("details"); details.className = "task-past-batch";
    const summary = document.createElement("summary");
    const title = document.createElement("span"); title.textContent = txt(batch.label || batch.title, "历史批次");
    const count = document.createElement("span"); count.className = "fold-count"; count.textContent = String(records.length);
    const stateLabel = document.createElement("span"); stateLabel.className = "summary-hint";
    stateLabel.textContent = records.every(isCompleted) ? "已完成" : records.every(isTerminal) ? "已结束" : "历史任务";
    summary.append(title, count, stateLabel); details.append(summary);
    const list = document.createElement("div"); list.className = "record-list completed-list";
    populateMasonryList(list, sortTaskRecords(records).map(record => makeTaskCard(record, false)));
    details.append(list); return details;
  }
  function render() {
    if (!state.data) return;
    const detail = $("#detail"), detailScroll = detail.scrollTop;
    const detailFocus = detail.contains(document.activeElement) ? document.activeElement.dataset?.dialogFocus : null;
    const preservedFoldStates = new Map($$("details[data-preserve-key]").map(node => [node.dataset.preserveKey, node.open]));
    const openKeys = new Set($$("details[open]").map(node => node.dataset.preserveKey || node.id).filter(Boolean));
    for (const group of groups) if (group.key !== "task") renderGroup(group);
    renderTaskBatches();
    setText("#revision", `r${state.data.revision ?? 0}`);
    const selected = findRecord(state.selectedId);
    if (selected && $("#record-dialog").open) {
      renderDetail(selected); detail.scrollTop = detailScroll;
      if (detailFocus) $(`[data-dialog-focus="${CSS.escape(detailFocus)}"]`, detail)?.focus({ preventScroll: true });
    }
    for (const node of $$("details")) {
      const key = node.dataset.preserveKey || node.id;
      if (key && preservedFoldStates.has(key)) {
        if (preservedFoldStates.get(key)) suppressDetailMotion(node);
        node.open = preservedFoldStates.get(key);
      } else if (key && openKeys.has(key)) { suppressDetailMotion(node); node.open = true; }
    }
    pruneMasonryObservers();
    if (selected && $("#record-dialog").open && $(".item-context-section", detail)?.open) loadItemContext(selected);
  }
  function suppressDetailMotion(node) {
    node.dataset.restoring = "true";
    requestAnimationFrame(() => { delete node.dataset.restoring; });
  }
  function rerenderPreservingCardFocus() {
    const active = document.activeElement;
    let selector = null;
    for (const attribute of ["data-open-record", "data-followup-id", "data-complete-id"]) {
      const value = active?.getAttribute?.(attribute);
      if (value) { selector = `[${attribute}="${CSS.escape(value)}"]`; break; }
    }
    render();
    if (selector) $(selector)?.focus({ preventScroll: true });
  }
  function findRecord(id) {
    if (!id || !state.data) return null;
    for (const group of groups) {
      const found = (state.data[group.field] || []).find(record => record.id === id);
      if (found) return found;
    }
    return null;
  }

  function addDetailSection(parent, title, content, asList = false) {
    const section = document.createElement("section"); section.className = "detail-section";
    const heading = document.createElement("h3"); heading.textContent = title; section.append(heading);
    if (asList) {
      const list = document.createElement("ul"); list.className = "detail-list";
      const items = values(content);
      if (!items.length) { const item = document.createElement("li"); item.textContent = "未取得执行记录"; list.append(item); }
      items.forEach(value => { const item = document.createElement("li"); item.textContent = txt(value); list.append(item); });
      section.append(list);
    } else {
      const paragraph = document.createElement("p"); paragraph.textContent = txt(content); section.append(paragraph);
    }
    parent.append(section);
  }
  function detailSection(title, className = "") {
    const section = document.createElement("section"); section.className = `detail-section ${className}`.trim();
    const heading = document.createElement("h3"); heading.textContent = title; section.append(heading);
    return section;
  }
  function appendOverviewField(parent, label, value, fallback = "未记录") {
    const row = document.createElement("div"); row.className = "detail-overview-field";
    const name = document.createElement("span"); name.textContent = label;
    const content = document.createElement("strong"); content.textContent = txt(value, fallback);
    row.append(name, content); parent.append(row);
  }
  function appendRelatedRecords(parent, record) {
    const related = values(record.related_ids).map(findRecord).filter(Boolean);
    const scopeById = new Map(values(state.scopeCatalog?.scopes).map(scope => [scope.id, scope]));
    const scopes = values(record.related_ids).map(id => scopeById.get(id)).filter(Boolean);
    if (!related.length && !scopes.length) return;
    const section = detailSection("关联事项与职责范围", "related-record-section");
    const list = document.createElement("div"); list.className = "related-record-list";
    for (const item of related) {
      const button = document.createElement("button"); button.type = "button"; button.className = "related-record-link";
      button.dataset.openRecord = item.id; button.textContent = `${item.id} · ${item.title}`;
      list.append(button);
    }
    for (const scope of scopes) {
      const tag = document.createElement("span"); tag.className = `scope-tag scope-${scope.status}`;
      tag.textContent = `${scope.id} · ${scope.name} · ${scopeStatusLabel(scope.status)}`;
      list.append(tag);
    }
    section.append(list); parent.append(section);
  }
  function scopeStatusLabel(status) { return ({ confirmed: "已确认", candidate: "候选", not_ours: "不属我方" })[status] || "未分类"; }
  function renderScopeCatalog() {
    const list = $("#scopes-list"); if (!list) return;
    const scopes = values(state.scopeCatalog?.scopes);
    if (!scopes.length) { list.replaceChildren(Object.assign(document.createElement("p"), { textContent: "职责目录暂不可用。" })); return; }
    list.replaceChildren();
    for (const scope of scopes) {
      const card = document.createElement("section"); card.className = "scope-card";
      const heading = document.createElement("div"); heading.className = "scope-card-heading";
      const title = document.createElement("h3"); title.textContent = scope.name;
      const status = document.createElement("span"); status.className = `scope-tag scope-${scope.status}`; status.textContent = scopeStatusLabel(scope.status);
      heading.append(title, status); card.append(heading);
      const id = document.createElement("p"); id.className = "scope-id"; id.textContent = `${scope.id} · v${scope.version}`; card.append(id);
      for (const [label, valuesForField] of [["包含", scope.in_scope], ["不包含", scope.out_of_scope]]) {
        const line = document.createElement("p"); line.className = "scope-boundary";
        const strong = document.createElement("strong"); strong.textContent = `${label}：`;
        line.append(strong, document.createTextNode(values(valuesForField).join("；") || "暂无")); card.append(line);
      }
      for (const evidence of values(scope.evidence)) {
        const source = document.createElement("p"); source.className = "scope-evidence";
        source.textContent = `依据（${evidence.date || "日期未记录"}）：${evidence.summary || evidence.reference || "未记录"}`; card.append(source);
        if (evidence.reference) {
          const refs = document.createElement("p"); refs.className = "scope-evidence-reference";
          const parts = String(evidence.reference).split(/\s*\|\s*/).filter(Boolean);
          parts.forEach((reference, index) => {
            if (index) refs.append(document.createTextNode(" · "));
            if (/^https:\/\//i.test(reference)) {
              const link = document.createElement("a"); link.href = reference; link.target = "_blank"; link.rel = "noopener noreferrer";
              link.textContent = reference; refs.append(link);
            } else refs.append(document.createTextNode(reference));
          });
          card.append(refs);
        }
      }
      const linked = values(scope.related_ids);
      if (linked.length) { const refs = document.createElement("p"); refs.className = "scope-id"; refs.textContent = `关联：${linked.join("、")}`; card.append(refs); }
      list.append(card);
    }
  }
  function missingBugFieldText(detail, key) {
    const state = detail?.field_states?.[key];
    if (state === "not_provided") return "平台未提供此项内容";
    if (state === "parse_failed") return "此项解析失败，尚未取得内容";
    return "尚未取得此项内容";
  }
  function appendBugPlatformDetail(parent, record) {
    const section = detailSection("BUG详情", "bug-platform-detail");
    const detail = record.bug_detail;
    if (!detail || detail.read_state === "failed") {
      const message = document.createElement("p");
      message.textContent = detail?.read_state === "failed" ? `平台 BUG 详情读取失败${detail.error_summary ? `：${txt(detail.error_summary)}` : "。"}` : "平台 BUG 详情尚未读取。";
      section.append(message);
    } else {
      if (detail.read_state === "partial") {
        const note = document.createElement("p"); note.className = "bug-evidence-note";
        note.textContent = `平台详情部分读取${values(detail.gaps).length ? ` · 未取得：${values(detail.gaps).map(txt).join("、")}` : " · 部分字段尚未取得"}`;
        section.append(note);
      }
      const fields = [["问题描述", "description", detail.description], ["复现步骤", "steps", detail.steps], ["预期结果", "expected", detail.expected], ["实际结果", "actual", detail.actual]];
      fields.forEach(([label, key, value]) => {
        const block = document.createElement("div"); block.className = "bug-detail-field";
        const name = document.createElement("strong"); name.textContent = label; block.append(name);
        if (Array.isArray(value)) {
          const list = document.createElement("ol"); value.forEach(item => { const li = document.createElement("li"); li.textContent = txt(item, missingBugFieldText(detail, key)); list.append(li); });
          if (!value.length) { const empty = document.createElement("p"); empty.textContent = missingBugFieldText(detail, key); block.append(empty); } else block.append(list);
        } else {
          const text = document.createElement("p"); text.textContent = txt(value, missingBugFieldText(detail, key)); block.append(text);
        }
        section.append(block);
      });
      if (detail.read_at) { const readAt = document.createElement("p"); readAt.className = "bug-evidence-note"; readAt.textContent = `平台读取时间：${dateLabel(detail.read_at)}`; section.append(readAt); }
    }
    parent.append(section);
  }
  function safeAttachmentHref(record, attachment) {
    const href = attachment?.download_url;
    if (typeof href !== "string") return null;
    const prefix = `/api/items/${encodeURIComponent(record.id)}/attachments/`;
    if (!href.startsWith(prefix)) return null;
    const filename = href.slice(prefix.length);
    if (!filename || filename.includes("/") || filename.includes("\\") || filename === "." || filename === ".." || !/^[A-Za-z0-9._-]+$/.test(filename)) return null;
    return href;
  }
  function appendBugAttachments(parent, record) {
    const section = detailSection("BUG附件列表", "bug-source-attachments");
    const items = values(record.source_attachments);
    const detail = record.bug_detail || {};
    const readState = detail.attachments_read_state;
    const gaps = values(detail.attachments_gaps);
    if (readState === "partial" || readState === "failed") {
      const note = document.createElement("p"); note.className = "bug-evidence-note";
      const stateText = readState === "failed" ? "附件列表读取失败" : "附件列表部分读取";
      note.textContent = `${stateText}${gaps.length ? ` · ${gaps.map(txt).join("、")}` : ""}`;
      section.append(note);
    }
    if (!items.length) {
      const message = document.createElement("p");
      message.textContent = readState === "success" ? "平台未提供附件。" : readState === "failed" ? "附件列表读取失败，附件情况未知。" : readState === "partial" ? "附件列表部分读取，可能有遗漏。" : "附件列表尚未读取。";
      section.append(message);
    }
    items.forEach(attachment => {
      const row = document.createElement("div"); row.className = "bug-attachment";
      const title = document.createElement("strong"); title.className = "bug-attachment-name"; title.textContent = txt(attachment.display_name, "未命名附件"); row.append(title);
      const meta = document.createElement("p");
      const type = txt(attachment.mime_type, "类型未知");
      const size = Number.isFinite(Number(attachment.size_bytes)) && Number(attachment.size_bytes) >= 0 ? `${(Number(attachment.size_bytes) / 1024).toFixed(1)} KB` : "大小未知";
      const status = ({ downloaded: "已归档", no_attachment: "平台无附件", failed: "归档失败", unsupported: "暂不支持归档" })[attachment.download_state] || "归档状态未知";
      const failureHint = attachment.download_state === "failed" ? ` · ${txt(attachment.error_summary, "详情见事项上下文")}` : "";
      meta.textContent = `${type} · ${size} · ${status}${failureHint}`; row.append(meta);
      const href = attachment.download_state === "downloaded" ? safeAttachmentHref(record, attachment) : null;
      if (href && window.workbenchTransport?.renderAttachment) {
        window.workbenchTransport.renderAttachment({ row, href, type, name: txt(attachment.display_name) });
      } else if (href && type.startsWith("image/")) {
        const link = document.createElement("a"); link.className = "bug-attachment-preview"; link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer"; link.setAttribute("aria-label", `查看图片：${txt(attachment.display_name)}`);
        const image = document.createElement("img"); image.src = href; image.alt = txt(attachment.display_name); image.loading = "lazy"; link.append(image); row.append(link);
      } else if (href && type.startsWith("video/")) {
        const video = document.createElement("video"); video.className = "bug-attachment-video"; video.controls = true; video.preload = "none"; video.src = href; video.setAttribute("aria-label", `播放视频：${txt(attachment.display_name)}`); row.append(video);
      } else if (href) {
        const link = document.createElement("a"); link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = "打开本地归档附件"; row.append(link);
      } else if (attachment.download_state === "downloaded") {
        const note = document.createElement("p"); note.textContent = "本地附件地址未通过安全校验。"; row.append(note);
      }
      section.append(row);
    });
    parent.append(section);
  }
  function taskUpdateText(item) {
    if (typeof item === "string") return item;
    if (!item || typeof item !== "object") return txt(item);
    const kind = item.kind === "result" ? "结果" : "进展";
    const outcome = ({ success: "成功", failure: "失败" })[item.outcome] || item.outcome;
    const parts = [kind, dateLabel(item.at), item.actor, item.message, outcome];
    if (Array.isArray(item.evidence) && item.evidence.length) parts.push(`证据：${item.evidence.map(txt).join("；")}`);
    return parts.filter(Boolean).join(" · ");
  }
  function renderDetail(record) {
    const detail = $("#detail"); detail.replaceChildren();
    const head = document.createElement("div"); head.className = "detail-head";
    const title = document.createElement("h2"); title.id = "dialog-title"; title.textContent = record.title;
    if (isCancelled(record)) title.classList.add("record-cancelled-title");
    const pill = document.createElement("span"); pill.className = `status-pill ${escClass(effectiveStatus(record))}`; pill.textContent = statusLabel(effectiveStatus(record));
    const idButton = document.createElement("button"); idButton.type = "button"; idButton.className = "detail-id-copy"; idButton.textContent = record.display_id || record.id; idButton.title = "点击复制事项 ID";
    idButton.addEventListener("click", async () => { try { await navigator.clipboard.writeText(record.display_id || record.id); showToast("ID 已复制"); } catch (_) { showToast("复制失败，请手动复制", "error"); } });
    head.append(title, pill); detail.append(idButton, head);
    const statusLine = document.createElement("p"); statusLine.className = "detail-status-line";
    statusLine.textContent = record.type === "bug" && record.platform_status
      ? `平台上次读取：${statusLabel(record.platform_status)} · ${dateLabel(record.observed_at, "读取时间未记录")}`
      : `更新于 ${dateLabel(record.updated_at, "时间未记录")}`;
    const overview = document.createElement("section"); overview.className = "detail-overview";
    const overviewTitle = document.createElement("h3"); overviewTitle.textContent = "目前进展";
    const conclusion = document.createElement("p"); conclusion.className = "detail-overview-conclusion";
    conclusion.textContent = txt(record.latest_brief || record.status_detail, "暂未记录进展");
    const overviewGrid = document.createElement("div"); overviewGrid.className = "detail-overview-grid";
    const handoff = record.handoff && typeof record.handoff === "object" && !Array.isArray(record.handoff) ? record.handoff : {};
    const fields = [
      ["要做什么", handoff.goal || record.initial_brief || (record.type === "bug" && record.bug_detail?.description)],
      ["当前阶段", record.phase],
      ["已验证", handoff.verified],
      ["尚未验证", handoff.unverified],
      ["接下来", handoff.next_step || record.next_action || record.next_step],
      ["如何算完成", handoff.acceptance],
      ["遇到的问题", handoff.blocker || record.blocked_reason],
      ["继续的条件", handoff.resume_condition]
    ];
    fields.forEach(([label, value]) => { if (typeof value === "string" && value.trim()) appendOverviewField(overviewGrid, label, value); });
    if (handoff.updated_at) {
      const updated = document.createElement("p"); updated.className = "handoff-gap";
      updated.textContent = `交接信息更新于 ${dateLabel(handoff.updated_at, "时间未记录")}`;
      overviewGrid.append(updated);
    }
    if (!isTerminal(record) && (!(handoff.next_step || record.next_action || record.next_step) || !handoff.acceptance)) {
      const gap = document.createElement("p"); gap.className = "handoff-gap";
      gap.textContent = "交接信息还不完整，详情及历史记录中可能有更多依据。"; overviewGrid.append(gap);
    }
    overview.append(overviewTitle, conclusion, overviewGrid);
    detail.append(head, statusLine, overview);
    const followupSummary = model.summarizeFollowups(followups(record));
    if (followupSummary.activeCount) {
      const activeBox = document.createElement("section"); activeBox.className = "active-followup-summary";
      const activeLabel = document.createElement("strong"); activeLabel.textContent = `待跟进/处理中 ${followupSummary.activeCount} 条`;
      const activeCounts = document.createElement("span"); activeCounts.textContent = `待跟进 ${followupSummary.pendingCount} · 处理中 ${followupSummary.processingCount}`;
      const activePrompt = document.createElement("p"); activePrompt.textContent = txt(followupSummary.latestActive?.prompt, "提示内容未记录"); activePrompt.title = activePrompt.textContent;
      activeBox.append(activeLabel, activeCounts, activePrompt); detail.append(activeBox);
    }
    const meta = document.createElement("div"); meta.className = "detail-meta";
    const terminalDate = record.completed_at ? dateLabel(record.completed_at) : "未取得执行记录";
    const metaFields = [["事项编号", record.id], [isCancelled(record) ? "取消日期" : "完成日期", isTerminal(record) ? terminalDate : "未终结"], ["负责人", record.owner || record.assignee || "未注明"], ["更新时间", dateLabel(record.updated_at, "未提供")],
      ["当前执行代理", record.current_agent_name || "未提供"], ["当前 agent 会话", record.current_agent_session_id || "未关联"], ["最近执行代理", record.last_agent_name || "未提供"], ["最近 agent 会话", record.last_agent_session_id || "未关联"],
      ["源系统更新时间", record.source_updated_at ? dateLabel(record.source_updated_at) : "平台未提供更新时间"],
      ["读取 / 观察时间", dateLabel(record.observed_at)], ["验证发生时间", dateLabel(record.evidence_at)]];
    metaFields.forEach(([label, value]) => { const cell = document.createElement("div"); cell.className = "detail-meta-cell"; const name = document.createElement("span"); name.textContent = label; const content = document.createElement("strong"); content.textContent = value; cell.append(name, content); meta.append(cell); });
    if (record.type === "bug") {
      const bugFold = document.createElement("details"); bugFold.className = "detail-history-fold"; bugFold.dataset.preserveKey = `bug-source-${record.id}`;
      const bugSummary = document.createElement("summary"); bugSummary.textContent = "原始 BUG 信息与附件"; bugSummary.dataset.dialogFocus = "bug-source";
      const bugBody = document.createElement("div"); bugBody.className = "detail-history-body";
      const infoSection = detailSection("BUG信息", "bug-info-section"); infoSection.append(meta); bugBody.append(infoSection);
      appendBugPlatformDetail(bugBody, record);
      appendBugAttachments(bugBody, record);
      bugFold.append(bugSummary, bugBody); detail.append(bugFold);
    }
    appendRelatedRecords(detail, record);
    const contextSection = document.createElement("details"); contextSection.className = "detail-history-fold item-context-section"; contextSection.dataset.preserveKey = `context-${record.id}`;
    const contextHeading = document.createElement("summary"); contextHeading.textContent = "事项上下文"; contextHeading.dataset.dialogFocus = "context";
    const contextBody = document.createElement("div"); contextBody.className = "context-markdown"; contextBody.dataset.contextBody = "true";
    const cachedContext = model.contextPlaceholder(state.contexts.get(record.id), record.revision);
    if (cachedContext) {
      window.renderSafeMarkdown(contextBody, cachedContext.markdown);
      contextBody.dataset.contextRevision = String(state.contexts.get(record.id).revision);
      contextBody.dataset.contextStale = String(!cachedContext.isCurrent);
    } else contextBody.textContent = "展开后读取完整事项上下文。";
    const contextContent = document.createElement("div"); contextContent.className = "detail-history-body";
    contextContent.append(contextBody);
    contextSection.addEventListener("toggle", () => { if (contextSection.open) loadItemContext(record); });
    contextSection.append(contextHeading, contextContent); detail.append(contextSection);

    const supplemental = document.createElement("details"); supplemental.className = "detail-history-fold"; supplemental.dataset.preserveKey = `supplemental-${record.id}`;
    const supplementalTitle = document.createElement("summary"); supplementalTitle.textContent = "补充记录与证据"; supplementalTitle.dataset.dialogFocus = "supplemental";
    const supplementalBody = document.createElement("div"); supplementalBody.className = "detail-history-body";
    if (record.type !== "bug") supplementalBody.append(meta);
    if (record.type === "task") {
      addDetailSection(supplementalBody, "初始要求", record.initial_brief || record.notes, true);
      if (isCompleted(record) && record.final_result !== undefined && record.final_result !== null) addDetailSection(supplementalBody, "本项结果", taskUpdateText(record.final_result));
    }
    if (record.manual_status_conflict) addDetailSection(supplementalBody, "来源状态与手动完成冲突", record.manual_status_conflict);
    if (record.blocked_reason) addDetailSection(supplementalBody, "阻塞与解除条件", record.blocked_reason);
    if (record.next_step) addDetailSection(supplementalBody, "下一步", record.next_step);
    if (record.verification_state) addDetailSection(supplementalBody, "验证边界", record.verification_state);
    const progress = values(record.progress).map(taskUpdateText);
    if (progress.length) addDetailSection(supplementalBody, `结构化进展（${progress.length}）`, progress, true);
    addDetailSection(supplementalBody, "备注", record.notes, true);
    addDetailSection(supplementalBody, "证据", record.evidence, true);
    const history = values(record.history).map(item => typeof item === "string" ? item : `${item.at || "时间未提供"} · ${item.status || "状态未提供"} · ${item.note || ""}`);
    if (history.length) addDetailSection(supplementalBody, "状态历史", history, true);
    supplemental.append(supplementalTitle, supplementalBody); detail.append(supplemental);
    const historyFollowups = followups(record).slice().sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    const followupList = historyFollowups.map(item => `${({ pending: "待跟进", processing: "处理中", handled: "已处理", failed: "执行失败" })[item.status] || "跟进"} · ${dateLabel(item.created_at)} · ${item.prompt}${item.result ? `\n结果：${item.result}` : ""}`);
    const followupHistory = document.createElement("details"); followupHistory.className = "detail-history-fold"; followupHistory.dataset.preserveKey = `followups-${record.id}`;
    followupHistory.open = followupSummary.activeCount > 0;
    const followupHeading = document.createElement("summary"); followupHeading.textContent = `跟进历史（${historyFollowups.length}）`; followupHeading.dataset.dialogFocus = "followups";
    const followupBody = document.createElement("div"); followupBody.className = "detail-history-body";
    addDetailSection(followupBody, "用户跟进", followupList, true);
    followupHistory.append(followupHeading, followupBody); detail.append(followupHistory);
    const source = document.createElement("section"); source.className = "detail-section";
    const sourceTitle = document.createElement("h3"); sourceTitle.textContent = "来源与责任依据"; source.append(sourceTitle);
    const sourceChip = document.createElement("span"); sourceChip.className = "source-chip"; sourceChip.textContent = sourceLabel(record); source.append(sourceChip);
    if (record.responsibility_evidence) { const evidence = document.createElement("p"); evidence.textContent = record.responsibility_evidence; source.append(evidence); }
    if (record.source) { const original = document.createElement("p"); original.textContent = txt(record.source); source.append(original); }
    if (record.source_ref) { const ref = document.createElement("p"); ref.className = "source-ref"; ref.textContent = txt(record.source_ref); source.append(ref); }
    supplementalBody.append(source);
  }
  function openDetail(id) {
    const record = findRecord(id); if (!record) return;
    state.selectedId = id; renderDetail(record); $("#record-dialog").showModal();
  }
  async function loadItemContext(record) {
    const id = record.id, expectedRevision = String(record.revision ?? "unversioned");
    const cached = state.contexts.get(id);
    if (cached && String(cached.revision) === expectedRevision) return;
    const requestKey = `${id}:${expectedRevision}`;
    if (state.contextRequests.has(requestKey)) return state.contextRequests.get(requestKey);
    const target = $("[data-context-body='true']", $("#detail"));
    if (!target || state.selectedId !== id) return;
    const hasPreviousMarkdown = !!model.contextPlaceholder(cached, expectedRevision);
    if (!hasPreviousMarkdown) target.textContent = "正在读取事项上下文…";
    const operation = (async () => {
      try {
        const response = await fetch(`/api/items/${encodeURIComponent(id)}/context`, { cache: "no-store" });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.message || payload.error || `HTTP ${response.status}`);
        const markdown = typeof payload.context_markdown === "string" ? payload.context_markdown : "";
        const revision = String(payload.record?.revision ?? payload.revision ?? expectedRevision);
        if (revision === expectedRevision) {
          state.contexts.set(id, { revision, markdown });
          if (state.selectedId === id && $("#record-dialog").open && target.isConnected) {
            model.replaceContextPreservingScroll($("#detail"), target, markdown || "暂无事项上下文内容。", window.renderSafeMarkdown);
          }
        } else if (state.selectedId === id && target.isConnected) {
          target.textContent = "事项在读取期间已更新，请刷新后重新打开上下文。";
        }
      } catch (error) {
        if (state.selectedId === id && target.isConnected && !hasPreviousMarkdown) target.textContent = `上下文读取失败：${error.message || "请稍后重试"}`;
      } finally { state.contextRequests.delete(requestKey); }
    })();
    state.contextRequests.set(requestKey, operation);
    return operation;
  }
  function showToast(message, kind = "success") {
    const toast = $("#toast"); toast.textContent = message; toast.className = `toast ${kind}`; toast.hidden = false;
    clearTimeout(state.toastTimer); state.toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3400);
  }

  async function postAction(path, body) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const errors = { record_not_found: "找不到该工作项", invalid_completion: "完成状态无效", empty_prompt: "提示词不能为空", prompt_too_long: "提示词过长", unsupported_method: "当前操作不受支持" };
      throw new Error(payload.message || errors[payload.error] || `操作失败，请重试（${payload.error || response.status}）`);
    }
    return payload;
  }
  function setRecordBusy(id, busy) {
    const card = $(`[data-record-id="${CSS.escape(id)}"]`);
    if (!card) return;
    card.classList.toggle("is-busy", busy);
    $$('button, textarea', card).forEach(control => {
      if (control.dataset.completeId) {
        const record = findRecord(id);
        control.disabled = busy || !!(record && isCompleted(record) && !hasManualCompletion(record));
      } else control.disabled = busy;
    });
  }
  async function submitCompletion(button) {
    const id = button.dataset.completeId, record = findRecord(id); if (!record || state.busy.has(id)) return;
    const completing = !isCompleted(record); state.busy.add(id); setRecordBusy(id, true);
    try {
      await postAction(`/api/items/${encodeURIComponent(id)}/complete`, { completed: completing });
      showToast(completing ? "已标记为我方完成" : "已恢复为未完成");
      await refresh();
    } catch (error) { showToast(error.message || "更新失败，请重试", "error"); }
    finally { state.busy.delete(id); setRecordBusy(id, false); }
  }
  async function submitTransfer(button) {
    const id = button.dataset.transferId; if (!findRecord(id) || state.busy.has(id)) return;
    state.busy.add(id); setRecordBusy(id, true);
    try { await postAction(`/api/items/${encodeURIComponent(id)}/transfer`, {}); showToast("已在个人工作台标记为已转交"); await refresh(); }
    catch (error) { showToast(error.message || "转交状态未更新", "error"); }
    finally { state.busy.delete(id); setRecordBusy(id, false); }
  }
  function openFollowup(id) {
    const record = findRecord(id); if (!record) return;
    state.followupId = id;
    $("#followup-dialog-title").textContent = record.title;
    const input = $("#followup-input"); input.value = state.drafts[id] || "";
    $("#followup-dialog").showModal(); input.focus();
  }
  function closeFollowup() { $("#followup-dialog").close(); state.followupId = null; }
  async function submitFollowup(event) {
    event.preventDefault();
    const id = state.followupId, input = $("#followup-input"), prompt = input.value.trim();
    if (!id) return;
    if (!prompt) { showToast("先写下要跟进的提示词", "error"); input.focus(); return; }
    if (state.busy.has(id)) return;
    state.drafts[id] = input.value; state.busy.add(id); setRecordBusy(id, true);
    try {
      await postAction(`/api/items/${encodeURIComponent(id)}/followups`, { prompt });
      delete state.drafts[id]; closeFollowup(); showToast("跟进提示词已提交"); await refresh();
    } catch (error) { showToast(error.message || "提交失败，请重试", "error"); }
    finally { state.busy.delete(id); setRecordBusy(id, false); }
  }

  async function refresh() {
    const freshness = $("#freshness");
    try {
      const response = await fetch("/api/dashboard", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const scopeChanged = Number(state.scopeCatalog?.revision || 0) !== Number(data.scope_catalog?.revision || 0);
      state.scopeCatalog = data.scope_catalog || state.scopeCatalog;
      if ($("#scopes-dialog")?.open) renderScopeCatalog();
      const changed = !state.data || Number(state.data.revision) !== Number(data.revision) || scopeChanged;
      state.data = data;
      const hours = data.preferences?.work_hours;
      if (hours?.start && hours?.end) workHours = { start: hours.start, end: hours.end, timezone: "Asia/Shanghai" };
      renderWorktimeProgress();
      const when = new Date(data.generated_at);
      const time = Number.isNaN(when.getTime()) ? data.generated_at : new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(when);
      const sync = data.sync?.bugs, stale = !!(sync?.stale || sync?.error);
      freshness.classList.toggle("stale", stale); freshness.lastElementChild.textContent = stale ? `数据可能过期 · ${time}` : `已更新 ${time}`;
      const banner = $("#state-banner"); banner.hidden = !stale;
      if (stale) banner.textContent = `BUG 快照读取失败或已标旧；保留最近成功快照（${txt(sync?.last_successful_snapshot_at)}）。`;
      setText("#revision", `r${data.revision ?? 0}`);
      if (changed) rerenderPreservingCardFocus();
    } catch (error) {
      freshness.classList.add("stale"); freshness.lastElementChild.textContent = "连接本地数据服务失败";
      const banner = $("#state-banner"); banner.hidden = false; banner.textContent = "无法读取本地结构化数据；请确认本地服务正在运行。";
    }
  }

  $("#search").addEventListener("input", event => { state.query = event.target.value; render(); });
  $("#show-archived").addEventListener("change", event => { state.showArchived = event.target.checked; render(); });
  $("#refresh").addEventListener("click", refresh);
  $("#worktime-open").addEventListener("click", openWorkHours);
  $("#settings-open").addEventListener("click", openSettings);
  $("#settings-close").addEventListener("click", () => $("#settings-dialog").close());
  $("#settings-dialog").addEventListener("click", event => { if (event.target === $("#settings-dialog")) $("#settings-dialog").close(); });
  $("#settings-work-hours-open").addEventListener("click", openWorkHours);
  $("#settings-theme").addEventListener("change", event => {
    document.body.dataset.theme = event.target.value;
    try { themeStorage.setItem("mcloud-dashboard-theme", event.target.value); } catch (_) {}
    updateThemeButton();
  });
  $("#data-location-form").addEventListener("submit", previewDataLocation);
  $("#data-location-migrate").addEventListener("click", migrateDataLocation);
  $("#integrations-refresh").addEventListener("click", async event => {
    const button = event.currentTarget; button.disabled = true; button.textContent = "检查中…";
    try {
      for (const [provider] of integrationProviders.filter(([key]) => ["feishu", "tencent_docs", "kdocs", "figma", "git", "iphone_use"].includes(key))) {
        try { await postAction(`/api/integrations/${encodeURIComponent(provider)}/check`, {}); }
        catch (_) { /* Keep checking the remaining independent integrations. */ }
      }
      for (let attempt = 0; attempt < 12; attempt += 1) {
        await loadIntegrationStatuses();
        const response = await fetch("/api/integrations/status", { cache: "no-store" });
        if (!response.ok) break;
        const payload = await response.json(), providers = payload.providers || payload.integrations || {};
        if (!integrationProviders.some(([key]) => ["checking", "queued"].includes(providers[key]?.status))) break;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    } catch (error) { showToast(error.message || "部分工具检查未能启动", "error"); }
    finally { button.disabled = false; button.textContent = "检查全部"; await loadIntegrationStatuses(); }
  });
  $("#integration-list").addEventListener("click", event => {
    const check = event.target.closest("[data-integration-check]");
    const copy = event.target.closest("[data-integration-prompt]");
    if (check) checkIntegration(check.dataset.integrationCheck, check);
    if (copy) copyIntegrationPrompt(copy.dataset.integrationPrompt);
  });
  $("#work-hours-close").addEventListener("click", () => $("#work-hours-dialog").close());
  $("#work-hours-cancel").addEventListener("click", () => $("#work-hours-dialog").close());
  $("#work-hours-form").addEventListener("submit", saveWorkHours);
  updateThemeButton();
  $("#theme-toggle").addEventListener("click", () => {
    const current = document.body.dataset.theme || "minimal";
    const next = current === "minimal" ? "dark" : "minimal";
    document.body.dataset.theme = next;
    try { themeStorage.setItem("mcloud-dashboard-theme", next); } catch (_) {}
    $("#settings-theme").value = next;
    updateThemeButton();
  });
  $("#sources-open").addEventListener("click", () => { $("#sources-dialog").showModal(); loadSources(); });
  const bugSourcesBtn = $("#bug-sources-open");
  if (bugSourcesBtn) {
    bugSourcesBtn.addEventListener("click", () => { $("#sources-dialog").showModal(); loadSources(); });
  }
  $("#scopes-open").addEventListener("click", async () => {
    $("#scopes-dialog").showModal(); renderScopeCatalog();
    if (state.scopeCatalog) return;
    try { const response = await fetch("/api/scopes", { cache: "no-store" }); if (!response.ok) throw new Error(); state.scopeCatalog = await response.json(); renderScopeCatalog(); }
    catch (_) { $("#scopes-list").textContent = "职责目录暂不可用，请稍后重试。"; }
  });
  $("#scopes-close").addEventListener("click", () => $("#scopes-dialog").close());
  $("#scopes-dialog").addEventListener("click", event => { if (event.target === $("#scopes-dialog")) $("#scopes-dialog").close(); });
  $("#sources-close").addEventListener("click", () => $("#sources-dialog").close());
  $("#sources-dialog").addEventListener("click", event => { if (event.target === $("#sources-dialog")) $("#sources-dialog").close(); });
  $("#source-add-file").addEventListener("click", () => $("#source-file-input").click());
  $("#source-file-input").addEventListener("change", event => uploadSourceFile(event.target.files?.[0]));
  $("#source-toggle-link").addEventListener("click", () => {
    const form = $("#source-link-form"), show = form.hidden; form.hidden = !show;
    $("#source-toggle-link").setAttribute("aria-expanded", String(show));
    if (show) $("#source-link-name").focus();
  });
  $("#source-link-cancel").addEventListener("click", () => { $("#source-link-form").hidden = true; $("#source-toggle-link").setAttribute("aria-expanded", "false"); });
  $("#source-link-form").addEventListener("submit", submitSourceLink);
  function handleSourceAction(button) {
    if (!button) return;
    if (button.dataset.sourceAction === "nondeprecated") return setSourceLifecycle(button.dataset.sourceId);
    return changeSourceActive(button.dataset.sourceId, button.dataset.sourceAction);
  }
  $("#source-active-list").addEventListener("click", event => handleSourceAction(event.target.closest("[data-source-action]")));
  $("#source-removed-list").addEventListener("click", event => handleSourceAction(event.target.closest("[data-source-action]")));
  $("#dialog-close").addEventListener("click", () => $("#record-dialog").close());
  $("#followup-close").addEventListener("click", closeFollowup);
  $("#followup-cancel").addEventListener("click", closeFollowup);
  $("#followup-input").addEventListener("input", event => { if (state.followupId) state.drafts[state.followupId] = event.target.value; });
  $("#followup-dialog-form").addEventListener("submit", submitFollowup);
  $("#followup-dialog").addEventListener("click", event => { if (event.target === $("#followup-dialog")) closeFollowup(); });
  $("#record-dialog").addEventListener("click", event => { if (event.target === $("#record-dialog")) $("#record-dialog").close(); });
  document.addEventListener("click", event => {
    const open = event.target.closest("[data-open-record]"); if (open) openDetail(open.dataset.openRecord);
    if (!event.target.closest("button,a,input,select,textarea,summary")) { const card = event.target.closest(".record-card"); if (card) openDetail(card.dataset.recordId); }
    const transfer = event.target.closest("[data-transfer-id]"); if (transfer) submitTransfer(transfer);
    const complete = event.target.closest("[data-complete-id]"); if (complete) submitCompletion(complete);
    const followup = event.target.closest("[data-followup-id]"); if (followup) openFollowup(followup.dataset.followupId);
  });
  document.addEventListener("toggle", event => {
    if (event.target.matches("details") && event.target.open) {
      requestAnimationFrame(() => $$(".record-list", event.target).forEach(relayoutMasonryList));
    }
  }, true);
  document.addEventListener("submit", event => {
    if (event.target.matches(".followup-form")) event.preventDefault();
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && $("#record-dialog").open) $("#record-dialog").close(); });
  $("#inspection-run").addEventListener("click", runInspection);
  refresh(); refreshInspectionSchedule(); refreshInspectionStatus();
  window.setInterval(() => { if (!document.hidden) refresh(); }, 3000);
  window.setInterval(() => { if (!document.hidden) refreshInspectionSchedule(); }, 30000);
  window.setInterval(() => { if (!document.hidden) refreshInspectionStatus(); }, 3000);
  window.setInterval(renderInspectionPlan, 1000);
  window.setInterval(renderWorktimeProgress, 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); refreshInspectionSchedule(); refreshInspectionStatus(); } });
})();
