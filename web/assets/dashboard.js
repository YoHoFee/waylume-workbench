(() => {
  const state = { data: null, query: "", showArchived: false, selectedId: null, toastTimer: null, busy: new Set(), sources: [], sourcesBusy: false, libraryExpanded: new Set(), libraryGroup: null, libraryFile: null, contexts: new Map(), contextRequests: new Map(), scopeCatalog: null,
    workflows: new Map(), workflowRequests: new Map(), workflowStage: new Map(), workflowEntered: new Set(), workflowBusy: new Set(),
    iterations: [], currentIterationId: null, archiveScopeTab: "iteration", libraryAddContext: null, libraryHighlight: null,
    sourcesRevision: null, workReleasedOpen: false, workReleasedGroups: [], workReleasedExpanded: new Set(), archiveReleasedExpanded: new Set(),
    iterationEditId: null, iterationBusy: false, sourceEdit: null, textViewerText: "",
    textViewerCopyText: "", textViewerCopyLabel: "复制全文", workbenchVersion: "",
    autoName: { timer: null, controller: null, token: 0, userEdited: false, lastUrl: "" } };
  const inspectionClock = { schedule: null, serverEpoch: null, syncedAt: null, nextRunAt: null };
  const inspectionRun = { busy: false, state: "unknown", slotId: null, eventId: null, enabled: null, windowOpen: null, nextRunAt: null, serverEpoch: null, syncedAt: null, message: "正在读取巡检状态…", announce: "" };
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
    in_progress: "进行中", developing: "开发中", testing: "测试中", on_hold: "搁置", awaiting_acceptance: "待验收", blocked: "阻塞", completed: "已完成", cancelled: "已取消", partial: "部分完成", pending_release: "待发布",
    pending: "待处理", unknown: "未核实", archived: "已归档", platform_testing: "平台测试中", submitted_for_testing: "我方已提交测试",
    owner_changed: "责任人变更"
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
  let scrollChrome = null;

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
  function isOwnerChanged(record) { return effectiveStatus(record) === "owner_changed"; }
  function isTerminal(record) { return isCompleted(record) || isCancelled(record) || isOwnerChanged(record); }
  const beijingDate = value => model.beijingDate(value);
  function hasManualCompletion(record) { return record.manual_completed === true || record.effective_status_basis === "manual"; }
  function groupRecords(group) {
    const source = state.data?.[group.field] || [];
    return source.filter(record => state.showArchived || effectiveStatus(record) !== "archived").filter(matchesSearch);
  }
  function matchesSearch(record) {
    const query = state.query.trim().toLocaleLowerCase("zh-CN");
    if (!query) return true;
    return [record.id, record.display_id, recordDisplayId(record), record.title, record.status, record.source_status, record.status_detail, record.owner, record.assignee,
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
      const aActive = ["in_progress", "developing", "testing"].includes(effectiveStatus(a)), bActive = ["in_progress", "developing", "testing"].includes(effectiveStatus(b));
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
  const BUILD_ID = (() => {
    const node = [...document.scripts].find(script => /dashboard\.js/.test(script.src || ""));
    try { return node ? new URL(node.src).searchParams.get("v") || "" : ""; } catch (_) { return ""; }
  })();

  function setWorkbenchVersion(value, build = "") {
    const version = String(value ?? "").trim();
    state.workbenchVersion = /^\d+\.\d+\.\d+$/.test(version) ? version : "";
    const target = $("#footer-version");
    if (target) {
      const shortBuild = build ? String(build).replace(/^\d{8}-/, "") : "";
      target.textContent = state.workbenchVersion ? `v${state.workbenchVersion}${shortBuild ? ` · ${shortBuild}` : ""}` : "v—";
      target.dataset.build = build || "";
      target.title = build ? `构建 ${build}` : "";
    }
  }
  async function refreshWorkbenchVersion() {
    try {
      const response = await fetch("/api/version", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setWorkbenchVersion(data.version, data.build);
    } catch (_) {
      if (!state.workbenchVersion) setWorkbenchVersion("");
    }
  }
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
  function inspectionEffectiveState() {
    const schedule = inspectionClock.schedule;
    return schedule?.state === "paused" || schedule?.enabled === false || inspectionRun.state === "paused" || inspectionRun.enabled === false
      ? "paused" : inspectionRun.state;
  }
  // One short line is the whole status surface. The inspection list dialog
  // carries the details, so the bar never repeats the same sentence twice.
  function inspectionStateText(state) {
    return ({ running: "正在运行", queued: "已排队", awaiting_confirmation: "等待结果", failed: "上次巡检失败", partial: "上次巡检部分完成", succeeded: "本时段已完成", paused: "已暂停" })[state] || "";
  }
  function inspectionNextLabel() {
    const schedule = inspectionClock.schedule;
    const syncedEpoch = inspectionClock.serverEpoch || inspectionRun.serverEpoch;
    if (!schedule || !schedule.available || schedule.state === "unknown" || !syncedEpoch) return { synced: false, label: "" };
    const syncedAt = inspectionClock.syncedAt || inspectionRun.syncedAt;
    const now = syncedEpoch + (performance.now() - syncedAt);
    const authoritativeAt = inspectionRun.nextRunAt || inspectionClock.nextRunAt || schedule.next_run_at || schedule.nextRunAt;
    let targetEpoch = authoritativeAt ? Date.parse(authoritativeAt) : NaN;
    if (Number.isNaN(targetEpoch) && Array.isArray(schedule.times)) {
      const next = nextInspection(now, schedule.times, schedule.timezone, Array.isArray(schedule.weekdays) ? schedule.weekdays : []);
      targetEpoch = next?.epoch;
    }
    if (!targetEpoch) return { synced: true, label: "" };
    const label = new Intl.DateTimeFormat("zh-CN", { timeZone: schedule.timezone, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(targetEpoch));
    return { synced: true, label };
  }
  let lastPlanKey = "";
  function renderInspectionPlan() {
    if (document.hidden) return;
    const plan = $("#inspection-plan");
    if (!plan) return;
    const state = inspectionEffectiveState();
    const time = inspectionNextLabel();
    let text;
    if (inspectionRun.busy) text = "巡检 · 正在提交请求…";
    else if (state === "paused") text = "巡检 · 已暂停";
    else if (state === "unknown") text = "巡检 · 状态未同步";
    else {
      const label = inspectionStateText(state);
      if (["running", "queued", "awaiting_confirmation"].includes(state)) text = `巡检 · ${label}`;
      else text = time.label ? `巡检 · ${label} · 下次 ${time.label}` : `巡检 · ${label} · 下次时间未同步`;
    }
    const detail = state === "failed" && inspectionRun.message ? `${inspectionRun.message} · ` : "";
    const title = `${detail}查看今日巡检记录`;
    const key = `${state}|${text}|${title}|${inspectionRun.busy}|${inspectionRun.windowOpen}`;
    if (key !== lastPlanKey) {
      lastPlanKey = key;
      plan.textContent = text;
      plan.dataset.state = state || "unknown";
      plan.title = title;
    }
    renderInspectionRun();
  }
  let lastWorktimeKey = "";
  function renderWorktimeProgress() {
    if (document.hidden) return;
    const percent = model.workPeriodProgress(Date.now(), workHours.start, workHours.end);
    const key = `${percent}|${workHours.start}|${workHours.end}`;
    if (key === lastWorktimeKey) return;
    lastWorktimeKey = key;
    $("#worktime-fill").style.transform = `scaleX(${percent / 100})`;
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
    const previousState = inspectionRun.state;
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
    // A finished run means a new summary may exist; refresh only on the edge.
    if (status === "succeeded" && previousState !== "succeeded" && $("#inspection-dialog")?.open) loadInspectionHistory();
    return true;
  }
  function renderInspectionRun() {
    const button = $("#inspection-run"), status = $("#inspection-run-status");
    if (!button || !status) return;
    const state = inspectionEffectiveState();
    const busyState = ["queued", "running", "awaiting_confirmation"].includes(state);
    const succeeded = state === "succeeded" && inspectionRun.slotId;
    const paused = state === "paused";
    button.textContent = "立即巡检";
    button.disabled = inspectionRun.busy || busyState || !!succeeded || paused || inspectionRun.windowOpen === false;
    button.title = paused ? "巡检已暂停"
      : busyState ? "巡检正在进行或已排队"
      : succeeded ? "本时段巡检已完成，下次巡检会自动开始"
      : inspectionRun.windowOpen === false ? "当前不在巡检时段"
      : "立即发起一轮巡检";
    // Screen-reader announcements stay concise and never repeat the visible line.
    // Only write on change so the 1s tick cannot re-announce the same message.
    const announcement = inspectionRun.announce || "";
    if (status.textContent !== announcement) status.textContent = announcement;
    status.dataset.state = state || "unknown";
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
      inspectionRun.announce = inspectionRun.message;
      renderInspectionRun();
    }
  }
  async function runInspection() {
    if (inspectionRun.busy || ["queued", "running", "awaiting_confirmation", "succeeded", "paused"].includes(inspectionEffectiveState()) || inspectionRun.enabled === false || inspectionRun.windowOpen === false || inspectionClock.schedule?.state === "paused" || inspectionClock.schedule?.enabled === false) return;
    inspectionRun.busy = true;
    inspectionRun.announce = "正在提交巡检请求";
    renderInspectionRun();
    renderInspectionPlan();
    try {
      const response = await fetch("/api/bug-inspection/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `http_${response.status}`);
      const applied = applyInspectionStatus(payload, true);
      inspectionRun.announce = applied ? "巡检请求已受理" : "请求已返回 · 巡检状态未同步";
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
      inspectionRun.announce = inspectionRun.message;
      renderInspectionRun();
    } finally {
      inspectionRun.busy = false;
      renderInspectionRun();
      renderInspectionPlan();
    }
  }

  const inspectionHistory = { loading: false, token: 0 };
  function inspectionRecordId(id) {
    const value = String(id ?? "");
    return /^[0-9]+$/.test(value) ? `#${value}` : value;
  }
  function inspectionRunStatusLabel(status) {
    return ({ succeeded: "已完成", partial: "部分完成", blocked: "受阻", failed: "失败", paused: "已暂停", running: "进行中" })[status] || "状态未知";
  }
  function inspectionClockText(at) {
    const epoch = Date.parse(at || "");
    if (Number.isNaN(epoch)) return "时间未知";
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(epoch));
  }
  function inspectionDateText(date) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(date || ""));
    return match ? `${match[1]} 年 ${Number(match[2])} 月 ${Number(match[3])} 日` : "今日";
  }
  function makeInspectionRunGroup(label, items) {
    const section = document.createElement("section");
    section.className = "inspection-run-group";
    const head = document.createElement("h4");
    head.className = "inspection-run-group-head";
    const name = document.createElement("span");
    name.textContent = label;
    const count = document.createElement("span");
    count.className = "inspection-run-group-count";
    count.textContent = `${items.length} 项`;
    head.append(name, count);
    section.append(head);
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "inspection-run-group-empty";
      empty.textContent = "无新增";
      section.append(empty);
      return section;
    }
    const list = document.createElement("div");
    list.className = "inspection-run-items";
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "inspection-run-item";
      button.dataset.openRecord = String(item.id);
      const title = txt(item.title, "未命名事项");
      button.setAttribute("aria-label", `查看事项：${inspectionRecordId(item.id)} ${title}`);
      const id = document.createElement("span");
      id.className = "inspection-run-item-id";
      id.textContent = inspectionRecordId(item.id);
      const text = document.createElement("span");
      text.className = "inspection-run-item-title";
      text.textContent = title;
      button.append(id, text);
      list.append(button);
    }
    section.append(list);
    return section;
  }
  function makeInspectionRunCard(run = {}) {
    const card = document.createElement("article");
    card.className = "inspection-run-card";
    const head = document.createElement("header");
    head.className = "inspection-run-card-head";
    const time = document.createElement("time");
    time.className = "inspection-run-time";
    time.textContent = inspectionClockText(run.at);
    if (run.at) time.dateTime = String(run.at);
    const status = document.createElement("span");
    status.className = `inspection-run-status-pill is-${escClass(run.status || "unknown")}`;
    status.textContent = inspectionRunStatusLabel(run.status);
    head.append(time, status);
    card.append(head);
    const bugs = Array.isArray(run.new_bugs) ? run.new_bugs : [];
    const requirements = Array.isArray(run.new_requirements) ? run.new_requirements : [];
    card.append(makeInspectionRunGroup("新增 BUG", bugs), makeInspectionRunGroup("新增需求", requirements));
    const notes = String(run.notes || "").trim();
    if (notes) {
      const note = document.createElement("p");
      note.className = "inspection-run-notes";
      const label = document.createElement("span");
      label.className = "inspection-run-notes-label";
      label.textContent = "备注";
      const text = document.createElement("span");
      text.textContent = notes;
      note.append(label, text);
      card.append(note);
    }
    return card;
  }
  function renderInspectionHistory(payload = {}) {
    const runs = Array.isArray(payload.runs) ? payload.runs : [];
    const list = $("#inspection-run-list"), empty = $("#inspection-empty");
    const feedback = $("#inspection-feedback"), summary = $("#inspection-summary");
    const date = $("#inspection-dialog-date");
    if (date) date.textContent = `${inspectionDateText(payload.date)} · 按时间倒序`;
    if (summary) summary.hidden = false;
    $("#inspection-bug-count").textContent = String(Number(payload.new_bug_count) || 0);
    $("#inspection-requirement-count").textContent = String(Number(payload.new_requirement_count) || 0);
    feedback.textContent = "";
    feedback.hidden = true;
    empty.hidden = runs.length > 0;
    list.replaceChildren(...runs.map(makeInspectionRunCard));
  }
  async function loadInspectionHistory() {
    const feedback = $("#inspection-feedback");
    const token = ++inspectionHistory.token;
    inspectionHistory.loading = true;
    feedback.hidden = false;
    feedback.classList.remove("error");
    feedback.textContent = "正在读取今日巡检记录…";
    try {
      const response = await fetch("/api/inspections/today", { cache: "no-store" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      if (token !== inspectionHistory.token) return;
      renderInspectionHistory(payload);
    } catch (_) {
      if (token !== inspectionHistory.token) return;
      feedback.classList.add("error");
      feedback.textContent = "巡检记录暂不可用，请稍后重试。";
      $("#inspection-summary").hidden = true;
      $("#inspection-run-list").replaceChildren();
      $("#inspection-empty").hidden = true;
    } finally {
      if (token === inspectionHistory.token) inspectionHistory.loading = false;
    }
  }
  function openInspectionHistory() {
    const dialog = $("#inspection-dialog");
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    loadInspectionHistory();
  }

  const libraryReadLabels = { success: "读取成功", succeeded: "读取成功", complete: "读取成功", partial: "部分读取", failed: "读取失败", error: "读取失败", running: "正在读取", in_progress: "正在读取", not_checked: "尚未读取", not_read: "尚未读取", unknown: "读取状态未知" };
  function sourceCategory(source) {
    const category = source.overrides?.category || source.category;
    if (category) return ({ cloud_document: "云文档", web_link: "网页链接", acceptance_reference: "验收参考", file: "本地文件", requirement_doc: "需求文档", figma: "Figma", codesign: "CoDesign", api_doc: "接口文档" })[category] || category;
    if (source.type === "file") return "本地文件";
    if (source.slot && model.librarySlotLabels[source.slot]) return model.librarySlotLabels[source.slot];
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
  const logoAliases = { analytics_requirement: "requirement_doc", analytics_acceptance: "testcase", prototype: "prototype" };
  function sourceLogoKind(source) {
    const kind = logoAliases[source.logo_kind || source.slot] || source.logo_kind || source.slot;
    return ["requirement_doc", "figma", "codesign", "api_doc", "file", "link"].includes(kind) ? kind : (source.type === "file" ? "file" : "link");
  }
  function makeSourceLogo(sourceOrKind) {
    const kind = typeof sourceOrKind === "string" ? (logoAliases[sourceOrKind] || sourceOrKind) : sourceLogoKind(sourceOrKind);
    const safeKind = ["requirement_doc", "figma", "codesign", "prototype", "testcase", "supplementary",
      "api_doc", "architecture", "file", "link"].includes(kind) ? kind : "link";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "library-logo-icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#library-logo-${safeKind}`);
    svg.append(use);
    return svg;
  }
  function sourceDisplayName(source) {
    return source.overrides?.name || source.name || source.filename || source.url || "未命名资料";
  }
  function sourceLinkText(source) {
    if (source.type === "file") return source.filename || sourceDisplayName(source);
    return source.url || sourceDisplayName(source);
  }
  function sourceOpenHref(source) {
    const safeApiPath = /^\/api\/sources\/[A-Za-z0-9_-]{1,100}\/(?:open|file)$/;
    if (source.type === "link") {
      if (typeof source.open_path === "string" && safeApiPath.test(source.open_path)) return source.open_path;
      return safeHttpUrl(source.url || "");
    }
    if (source.type === "file" && typeof source.download_url === "string" && safeApiPath.test(source.download_url)) return source.download_url;
    return null;
  }
  function makeSourceOpenLink(source, label, className = "library-text-link") {
    const link = document.createElement("a");
    link.className = className;
    link.textContent = label;
    link.title = label || sourceLinkText(source);
    const href = sourceOpenHref(source);
    if (!href) {
      link.href = "#";
      link.setAttribute("aria-disabled", "true");
      link.addEventListener("click", event => event.preventDefault());
      return link;
    }
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    if (source.type === "file" && window.workbenchTransport?.openSource) {
      link.addEventListener("click", event => {
        event.preventDefault();
        window.workbenchTransport.openSource(source, source.download_url);
      });
    }
    return link;
  }
  function sourceLastInspectionSuccess(source) {
    return source.last_inspection_success_at || source.last_successful_read_at || null;
  }
  function makeInspectionStatus(source, compact = false, groupLastSuccess = null) {
    const holder = document.createElement("span");
    holder.className = `library-inspection-status${compact ? " is-compact" : ""}`;
    if (source.inspection_active) {
      const badge = document.createElement("span");
      badge.className = "library-inspection-badge is-active";
      const dot = document.createElement("span"); dot.className = "library-inspection-dot"; dot.setAttribute("aria-hidden", "true");
      const text = document.createElement("span"); text.textContent = "巡检包含";
      badge.append(dot, text);
      holder.append(badge);
    } else {
      const off = document.createElement("span");
      off.className = "library-inspection-off";
      off.textContent = source.inspection_eligible === false ? "历史版本不巡检" : "未加入巡检";
      holder.append(off);
    }
    const success = sourceLastInspectionSuccess(source) || (source.inspection_active ? groupLastSuccess : null);
    if (success) {
      const time = document.createElement("span");
      time.className = "library-inspection-time";
      time.textContent = `上次巡检成功 ${dateLabel(success)}`;
      holder.append(time);
    }
    return holder;
  }
  function makeInspectionToggle(source) {
    if (source.inspection_eligible === false) return null;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-inspection-toggle";
    button.dataset.sourceAction = "inspection";
    button.dataset.sourceId = source.id;
    button.textContent = source.inspection_active ? "移出巡检" : "加入巡检";
    button.title = ["requirement_doc", "analytics_requirement"].includes(source.slot)
      ? "仅影响这条资料所在的版本；需求/埋点需求表只巡检最新与上一版。"
      : "将这条资料加入或移出巡检。";
    return button;
  }
  function iterationName(iteration) { return model.iterationName(iteration); }
  function iterationStatusLabel(status) { return model.iterationStatusLabel(status); }
  function iterationVersionType(iteration) { return model.iterationVersionType(iteration); }
  function versionTypeLabel(iterationOrType) { return model.versionTypeLabel(iterationOrType); }
  function recordDisplayId(record) {
    const raw = record?.display_id || record?.id || "";
    return /^[0-9]+$/.test(String(raw)) ? `#${raw}` : String(raw);
  }
  function iterationStatusPill(iteration) {
    const pill = document.createElement("span");
    pill.className = `iteration-status-pill ${escClass(iteration?.status || "unknown")}`;
    pill.textContent = iterationStatusLabel(iteration?.status);
    return pill;
  }
  const ITERATION_STATUS_CHOICES = ["planning", "active", "pending_release", "released", "archived"];
  function iterationStatusSelect(iteration, busy) {
    const status = iteration?.status;
    const known = ITERATION_STATUS_CHOICES.includes(status);
    const select = document.createElement("select");
    select.className = `iteration-manager-status-select is-${known ? status : "unknown"}`;
    select.dataset.iterationStatus = iteration.id;
    select.disabled = busy;
    select.title = "切换版本状态；规划中 / 进行中会随需求数量自动变化";
    select.setAttribute("aria-label", `版本「${iterationName(iteration)}」的状态，当前为${iterationStatusLabel(status)}；可切换状态`);
    select.setAttribute("aria-describedby", "iteration-manager-status-note");
    select.append(...ITERATION_STATUS_CHOICES.map(value => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = iterationStatusLabel(value);
      return option;
    }));
    if (known) select.value = status;
    else {
      const option = document.createElement("option");
      option.value = status || "unknown";
      option.textContent = iterationStatusLabel(status);
      option.disabled = true;
      select.prepend(option);
      select.value = option.value;
    }
    return select;
  }
  function versionTypePill(iteration) {
    const type = iterationVersionType(iteration);
    const pill = document.createElement("span");
    pill.className = `version-type-pill ${type}`;
    pill.textContent = versionTypeLabel(type);
    pill.title = type === "analytics" ? "埋点版本：固定资料位为埋点需求表与埋点验收表" : "迭代版本：固定资料位为需求列表、Figma、高保真与测试用例";
    return pill;
  }
  function orderedIterations() { return model.orderedIterations(state.iterations || []); }
  function iterationById(id) { return (state.iterations || []).find(item => item.id === id) || null; }
  function slotDefinitions(scope, iteration = null) {
    return scope === "global" ? model.globalArchiveSlots : model.versionSlotDefinitions(iteration);
  }
  function slotLabel(slot) { return model.archiveSlotLabels[slot] || "补充材料"; }
  function scopeLabel(scope) { return scope === "global" ? "公共材料" : "版本资料"; }
  function sourceLocationText(source) {
    const scope = model.sourceScope(source);
    if (scope === "global") {
      return `公共材料 · ${source.slot === "api_doc" ? "接口文档" : slotLabel(source.slot)}`;
    }
    const iteration = iterationById(source.iteration_id);
    return `${iteration ? iterationName(iteration) : "未归入版本"} · ${slotLabel(source.slot)}`;
  }
  function appendMixedGroupBadge(parent, group, context = {}) {
    if (!group || !context.mixedGroups?.has(group.group_id)) return;
    const badge = document.createElement("span");
    badge.className = "archive-warning-pill";
    badge.textContent = "跨版本组";
    badge.title = "同一资料组的不同版本目前分布在多个版本；移动任意一条会整组移动并修正归属。";
    parent.append(badge);
  }
  function makeTextButton(label, className = "library-text-button") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    return button;
  }
  function maskedNoteText(note) {
    const text = String(note || "").trim();
    if (!text) return "";
    return text.split(/\r?\n/).map(line => {
      const labeled = line.match(/^([^：:\n]{1,24}[：:])/);
      return labeled ? `${labeled[1]}••••` : "••••";
    }).join("\n");
  }
  function makeSourceNote(source) {
    const note = String(source.note || "").trim();
    if (!note) return null;
    const masked = source.note_masked === true;
    const holder = document.createElement("div");
    holder.className = "library-card-note";
    const label = document.createElement("span");
    label.className = "library-card-note-label";
    label.textContent = "备注";
    const text = document.createElement("p");
    text.className = "library-card-note-text";
    holder.append(label, text);
    const render = revealed => { text.textContent = masked && !revealed ? maskedNoteText(note) : note; };
    render(false);
    if (masked) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "library-note-toggle";
      toggle.textContent = "显示";
      toggle.setAttribute("aria-label", "显示备注");
      toggle.setAttribute("aria-pressed", "false");
      toggle.addEventListener("click", () => {
        const revealed = toggle.getAttribute("aria-pressed") !== "true";
        toggle.setAttribute("aria-pressed", String(revealed));
        toggle.setAttribute("aria-label", revealed ? "隐藏备注" : "显示备注");
        toggle.textContent = revealed ? "隐藏" : "显示";
        render(revealed);
      });
      holder.append(toggle);
    }
    return holder;
  }
  function groupHistoryKey(groupId, context = {}) {
    const scope = context.scope || "iteration";
    const iterationId = context.iteration?.id || context.iterationId || (scope === "global" ? "global" : "");
    return `${scope}:${iterationId}:${groupId}`;
  }
  function libraryHistoryId(historyKey) {
    return `library-history-${cssSafe(historyKey)}`;
  }
  function makeVersionToggle(group, historyId, historyKey) {
    if (!group || !(group.history_count > 0)) return null;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-version-toggle";
    button.dataset.libraryVersions = historyKey;
    button.setAttribute("aria-expanded", String(state.libraryExpanded.has(historyKey)));
    button.setAttribute("aria-controls", historyId);
    button.textContent = `还有 ${group.history_count} 个版本`;
    return button;
  }
  function makeVersionRow(source, groupId) {
    const row = document.createElement("li");
    row.className = "library-version-row";
    const open = makeSourceOpenLink(source, "", "library-version-open");
    const label = document.createElement("span"); label.className = "library-version-label"; label.textContent = source.version_label || `v${source.version_no || 1}`;
    const time = document.createElement("span"); time.className = "library-version-time"; time.textContent = dateLabel(source.added_at, "时间未知");
    open.append(label, time);
    const meta = document.createElement("div"); meta.className = "library-version-meta";
    if (source.is_latest) {
      const latest = document.createElement("span"); latest.className = "library-latest-mark"; latest.textContent = "最新"; meta.append(latest);
    }
    meta.append(makeInspectionStatus(source, true));
    const toggle = makeInspectionToggle(source); if (toggle) meta.append(toggle);
    row.append(open, meta);
    return row;
  }
  function makeVersionHistory(group, historyKey) {
    const historyId = libraryHistoryId(historyKey);
    const wrapper = document.createElement("div");
    wrapper.className = "library-history";
    wrapper.id = historyId;
    wrapper.hidden = !state.libraryExpanded.has(historyKey);
    const list = document.createElement("ol");
    list.className = "library-version-list";
    list.append(...group.versions.map(source => makeVersionRow(source, group.group_id)));
    wrapper.append(list);
    return wrapper;
  }
  function makeSlotCard(slot, context = {}) {
    const scope = context.scope || "iteration";
    const iterationId = context.iteration?.id || null;
    const article = document.createElement("article");
    article.className = `library-card${slot.empty ? " is-empty" : ""}`;
    article.dataset.slot = slot.slot;
    if (iterationId) article.dataset.iterationId = iterationId;
    article.dataset.scope = scope;
    const titleId = `library-slot-title-${scope}-${iterationId || "global"}-${slot.slot}`;
    article.setAttribute("aria-labelledby", titleId);
    const head = document.createElement("div"); head.className = "library-card-head";
    const logo = document.createElement("span"); logo.className = "library-logo"; logo.append(makeSourceLogo(slot.card || slot.slot));
    const type = document.createElement("span"); type.className = "library-card-type"; type.id = titleId; type.textContent = slot.label;
    head.append(logo, type);
    if (!slot.empty && (slot.card.suspected_deprecated || slot.card.lifecycle_status === "suspected_deprecated" || slot.card.lifecycle_status === "deprecated")) {
      const lifecycle = document.createElement("span"); lifecycle.className = "library-lifecycle-mark";
      lifecycle.textContent = slot.card.lifecycle_status === "deprecated" ? "已废弃" : "疑似废弃"; head.append(lifecycle);
    }
    if (!slot.empty && slot.card.is_latest) {
      const latest = document.createElement("span"); latest.className = "library-latest-mark"; latest.textContent = "最新"; head.append(latest);
    }
    if (!slot.empty) appendMixedGroupBadge(head, slot.group, context);
    article.append(head);
    if (slot.empty) {
      const name = document.createElement("h4"); name.className = "library-card-name is-empty"; name.textContent = `尚未添加${slot.label}`;
      const hint = document.createElement("p"); hint.className = "library-card-hint"; hint.textContent = "添加后在这里展示最新版本，历史版本仍会完整保留。";
      const actions = document.createElement("div"); actions.className = "library-card-actions";
      const add = makeTextButton(`添加${slot.label}`, "library-card-add");
      add.dataset.sourceAction = "add-slot"; add.dataset.slot = slot.slot;
      add.dataset.scope = scope; add.dataset.iterationId = iterationId || "";
      actions.append(add); article.append(name, hint, actions);
      return article;
    }
    const card = slot.card;
    article.dataset.groupId = card.group_id;
    article.dataset.sourceId = card.id;
    article.title = "点击卡片编辑名称与链接";
    const name = document.createElement("h4"); name.className = "library-card-name"; name.textContent = sourceDisplayName(card);
    const link = makeSourceOpenLink(card, sourceLinkText(card), "library-card-link");
    const historyKey = groupHistoryKey(card.group_id, context);
    const historyId = libraryHistoryId(historyKey);
    const subtitle = makeVersionToggle(slot.group, historyId, historyKey);
    const inspection = document.createElement("div"); inspection.className = "library-inspection";
    inspection.append(makeInspectionStatus(card, false, slot.group.last_inspection_success_at));
    const inspectionToggle = makeInspectionToggle(card); if (inspectionToggle) inspection.append(inspectionToggle);
    const actions = document.createElement("div"); actions.className = "library-card-actions";
    const addVersion = makeTextButton("添加新版本");
    addVersion.dataset.sourceAction = "add-version";
    addVersion.dataset.groupId = card.group_id; addVersion.dataset.slot = slot.slot; addVersion.dataset.name = sourceDisplayName(card);
    addVersion.dataset.scope = scope; addVersion.dataset.iterationId = iterationId || "";
    const open = makeSourceOpenLink(card, "打开", "library-text-button");
    const relocate = makeTextButton("移动", "library-text-button source-relocate-btn");
    relocate.title = "把整组材料移动到另一个版本或公共材料";
    relocate.addEventListener("click", () => openRelocateDialog(card));
    const edit = makeTextButton("编辑");
    edit.dataset.sourceAction = "edit"; edit.dataset.sourceId = card.id;
    edit.setAttribute("aria-label", `编辑${sourceDisplayName(card)}的名称、链接与备注`);
    actions.append(addVersion, open, edit, relocate);
    article.append(name, link);
    if (subtitle) article.append(subtitle);
    const note = makeSourceNote(card);
    if (note) article.append(note);
    article.append(inspection, actions, makeVersionHistory(slot.group, historyKey));
    return article;
  }
  function sourceMetaLine(source) {
    const line = document.createElement("span"); line.className = "library-row-meta";
    const parts = [sourceCategory(source)];
    const readStatus = source.read_state || source.read_status || "not_checked";
    parts.push(libraryReadLabels[readStatus] || "读取状态未知");
    if (source.suspected_deprecated || source.lifecycle_status === "suspected_deprecated") parts.push("疑似废弃");
    else if (source.lifecycle_status === "deprecated") parts.push("已废弃");
    if (source.verification_state === "verified") parts.push("已核验");
    line.textContent = parts.join(" · ");
    return line;
  }
  function makeMaterialRow(group, context = {}) {
    const source = group.card;
    const scope = context.scope || model.sourceScope(source);
    const iterationId = context.iteration?.id || source.iteration_id || "";
    const row = document.createElement("li");
    row.className = "library-row archive-material-row";
    row.dataset.groupId = group.group_id;
    row.dataset.scope = scope;
    if (iterationId) row.dataset.iterationId = iterationId;
    const logo = document.createElement("span"); logo.className = "library-logo"; logo.append(makeSourceLogo(source));
    const info = document.createElement("div"); info.className = "library-row-info";
    const titleLine = document.createElement("div"); titleLine.className = "library-row-title";
    const name = document.createElement("strong"); name.className = "library-row-name"; name.textContent = sourceDisplayName(source);
    const type = document.createElement("span"); type.className = "library-type-pill";
    type.textContent = source.slot === "supplementary" || !source.slot ? "补充材料" : slotLabel(source.slot);
    titleLine.append(name, type);
    appendMixedGroupBadge(titleLine, group, context);
    const link = makeSourceOpenLink(source, sourceLinkText(source), "library-row-link");
    const meta = document.createElement("div"); meta.className = "library-row-meta-line";
    meta.append(sourceMetaLine(source), makeInspectionStatus(source, true, group.last_inspection_success_at));
    const historyKey = groupHistoryKey(group.group_id, context);
    const historyId = libraryHistoryId(historyKey);
    const subtitle = makeVersionToggle(group, historyId, historyKey);
    const history = makeVersionHistory(group, historyKey);
    info.append(titleLine, link, meta);
    if (subtitle) info.append(subtitle);
    info.append(history);
    const actions = document.createElement("div"); actions.className = "library-row-actions";
    actions.append(makeSourceOpenLink(source, "打开", "library-text-button"));
    const addVersion = makeTextButton("添加新版本");
    addVersion.dataset.sourceAction = "add-version";
    addVersion.dataset.groupId = group.group_id; addVersion.dataset.slot = group.slot || "";
    addVersion.dataset.name = sourceDisplayName(source);
    addVersion.dataset.scope = scope; addVersion.dataset.iterationId = String(iterationId || "");
    actions.append(addVersion);
    const inspectionToggle = makeInspectionToggle(source); if (inspectionToggle) actions.append(inspectionToggle);
    const remove = makeTextButton("移出资料", "library-text-button is-muted");
    remove.title = "移出后可随时恢复"; remove.dataset.sourceAction = "remove"; remove.dataset.sourceId = source.id;
    const relocate = makeTextButton("移动", "library-text-button source-relocate-btn");
    relocate.title = "把整组材料移动到另一个版本或公共材料";
    relocate.addEventListener("click", () => openRelocateDialog(source));
    actions.append(remove, relocate);
    row.append(logo, info, actions);
    return row;
  }
  function makeAttachmentRow(item, context = {}) {
    const row = document.createElement("li");
    row.className = "library-row archive-material-row is-attachment";
    const logo = document.createElement("span"); logo.className = "library-logo"; logo.append(makeSourceLogo({ slot: "supplementary" }));
    const info = document.createElement("div"); info.className = "library-row-info";
    const titleLine = document.createElement("div"); titleLine.className = "library-row-title";
    const name = document.createElement("strong"); name.className = "library-row-name"; name.textContent = item.filename || item.name || "补充附件";
    const type = document.createElement("span"); type.className = "library-type-pill"; type.textContent = "附件";
    titleLine.append(name, type);
    const href = item.download_url || item.url || "#";
    const link = document.createElement("a");
    link.className = "library-row-link"; link.textContent = item.download_url ? "打开附件" : "打开";
    link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
    if (href === "#") link.setAttribute("aria-disabled", "true");
    const summary = document.createElement("p"); summary.className = "library-row-meta";
    summary.textContent = item.summary || item.notes || `附件位于「${item.source_name || "材料"}」下，随所属材料移动。`;
    info.append(titleLine, link, summary);
    row.append(logo, info);
    return row;
  }
  function makeRemovedRow(source) {
    const row = document.createElement("li");
    row.className = "library-row is-removed";
    const logo = document.createElement("span"); logo.className = "library-logo"; logo.append(makeSourceLogo(source));
    const info = document.createElement("div"); info.className = "library-row-info";
    const name = document.createElement("strong"); name.className = "library-row-name"; name.textContent = sourceDisplayName(source);
    const meta = document.createElement("span"); meta.className = "library-row-meta";
    meta.textContent = `${sourceCategory(source)} · ${dateLabel(source.removed_at || source.updated_at, "已移出")}`;
    info.append(name, meta);
    const actions = document.createElement("div"); actions.className = "library-row-actions";
    const restore = document.createElement("button"); restore.type = "button"; restore.className = "library-text-button";
    restore.textContent = "恢复资料"; restore.dataset.sourceAction = "restore"; restore.dataset.sourceId = source.id;
    actions.append(restore);
    row.append(logo, info, actions);
    return row;
  }
  function makeArchiveAddButton(scope, iterationId, slot, label) {
    const button = makeTextButton(label || "添加资料");
    button.dataset.sourceAction = "add-context";
    button.dataset.scope = scope;
    button.dataset.iterationId = iterationId || "";
    button.dataset.slot = slot || "supplementary";
    return button;
  }
  function makeArchiveSection(section, options = {}) {
    const scope = section.scope;
    const iteration = section.iteration;
    const released = !!options.released;
    const block = document.createElement(released ? "details" : "section");
    block.className = `archive-block${scope === "global" ? " archive-block-global" : ""}${released ? " archive-block-released" : ""}`;
    block.dataset.scope = scope;
    if (iteration) block.dataset.iterationId = iteration.id;
    if (released) {
      block.dataset.preserveKey = `archive-released-${iteration.id}`;
      block.open = state.archiveReleasedExpanded.has(iteration.id);
    }

    const head = document.createElement(released ? "summary" : "header");
    head.className = "archive-block-head";
    const copy = document.createElement(released ? "span" : "div");
    copy.className = "archive-block-copy";
    const kicker = document.createElement("span");
    kicker.className = "archive-block-kicker";
    kicker.textContent = scope === "global" ? "跨版本共享" : released ? "已发布版本" : "版本资料";
    const titleLine = document.createElement(released ? "span" : "div");
    titleLine.className = "archive-block-title-line";
    const title = document.createElement(released ? "span" : "h3");
    title.className = "archive-block-title";
    title.textContent = scope === "global" ? "公共材料" : iterationName(iteration);
    titleLine.append(title);
    if (iteration) titleLine.append(versionTypePill(iteration), iterationStatusPill(iteration));
    const tag = iteration ? model.iterationSecondaryTag(iteration) : "";
    if (tag) title.title = `版本标识：${tag}`;
    const meta = document.createElement(released ? "span" : "p");
    meta.className = "archive-block-meta";
    meta.textContent = scope === "global"
      ? `接口文档 1 个固定位置 · 补充材料 ${section.supplementaryCount} 项`
      : `${section.slots.length} 个固定位置（${versionTypeLabel(iteration)}）· 已填 ${section.fixedCount} 个 · 补充材料 ${section.supplementaryCount} 项`;
    copy.append(kicker, titleLine, meta);
    head.append(copy);
    if (!released) {
      const actions = document.createElement("div");
      actions.className = "archive-block-actions";
      if (scope === "iteration" && iteration) {
        actions.append(makeArchiveAddButton("iteration", iteration.id, "supplementary", "添加资料"));
      } else {
        actions.append(makeArchiveAddButton("global", null, "supplementary", "添加资料"));
      }
      head.append(actions);
    }

    const slotGrid = document.createElement("div");
    slotGrid.className = "library-slot-grid";
    slotGrid.append(...section.slots.map(slot => makeSlotCard(slot, { scope, iteration, mixedGroups: section.mixedGroups })));

    const supplementary = document.createElement("section");
    supplementary.className = "archive-supplementary";
    const suppHead = document.createElement("header");
    suppHead.className = "archive-supplementary-head";
    const suppTitle = document.createElement("h4");
    suppTitle.textContent = "补充材料";
    const suppCount = document.createElement("span");
    suppCount.className = "library-count";
    suppCount.textContent = String(section.supplementaryCount);
    const suppGrow = document.createElement("div");
    suppGrow.className = "archive-supplementary-title";
    suppGrow.append(suppTitle, suppCount);
    const suppActions = document.createElement("div");
    suppActions.className = "archive-block-actions";
    suppActions.append(makeArchiveAddButton(scope, iteration?.id || null, "supplementary", "添加补充材料"));
    suppHead.append(suppGrow, suppActions);
    const list = document.createElement("ul");
    list.className = "library-list archive-material-list";
    const rows = [
      ...section.supplementaryGroups.map(group => makeMaterialRow(group, { scope, iteration, mixedGroups: section.mixedGroups })),
      ...section.attachments.map(item => makeAttachmentRow(item, { scope, iteration }))
    ];
    list.append(...rows);
    supplementary.append(suppHead, list);
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "library-empty archive-supplementary-empty";
      empty.textContent = scope === "global" ? "公共材料暂无补充材料。" : "这个版本暂无补充材料。";
      supplementary.append(empty);
    }

    block.append(head, slotGrid, supplementary);

    if (section.removed.length) {
      const removed = document.createElement("details");
      removed.className = "library-fold archive-removed-fold";
      const summary = document.createElement("summary");
      const label = document.createElement("span");
      label.textContent = "已移出";
      const count = document.createElement("span");
      count.className = "library-count";
      count.textContent = String(section.removed.length);
      const hint = document.createElement("span");
      hint.className = "library-summary-hint";
      hint.textContent = "数据仍保留，可随时恢复";
      summary.append(label, count, hint);
      const removedList = document.createElement("ul");
      removedList.className = "library-list";
      removedList.append(...section.removed.map(makeRemovedRow));
      removed.append(summary, removedList);
      block.append(removed);
    }
    return block;
  }
  function defaultAddContext(scope) {
    if (scope === "global") return { scope: "global", iterationId: null, slot: "supplementary" };
    const first = orderedIterations()[0] || null;
    return { scope: "iteration", iterationId: first?.id || null, slot: "supplementary" };
  }
  function renderLibraryAddSlotOptions() {
    const select = $("#library-add-slot");
    if (!select) return;
    const context = state.libraryAddContext || defaultAddContext(state.archiveScopeTab);
    const target = context.scope === "iteration"
      ? iterationById(state.libraryGroup?.iterationId || context.iterationId)
      : null;
    const definitions = slotDefinitions(context.scope, target);
    const options = definitions.map(definition => ({ value: definition.id, label: definition.label }));
    options.push({ value: "supplementary", label: "补充材料" });
    select.replaceChildren(...options.map(option => {
      const node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      return node;
    }));
    const rawPreferred = state.libraryGroup?.slot || context.slot || "supplementary";
    const preferred = rawPreferred === "codesign" ? "prototype" : rawPreferred;
    select.value = options.some(option => option.value === preferred) ? preferred : "supplementary";
    select.dataset.scope = context.scope;
    select.disabled = !!state.libraryGroup;
  }
  function updateLibraryAddContext() {
    const node = $("#library-add-context");
    if (!node) return;
    const context = state.libraryAddContext || defaultAddContext(state.archiveScopeTab);
    const slot = state.libraryGroup?.slot || $("#library-add-slot")?.value || context.slot || "supplementary";
    if (state.libraryGroup) {
      const groupIteration = iterationById(state.libraryGroup.iterationId || context.iterationId);
      const where = context.scope === "global"
        ? "公共材料"
        : (groupIteration ? `${iterationName(groupIteration)}（${versionTypeLabel(groupIteration)}版本）` : "未归入版本");
      node.textContent = `为「${state.libraryGroup.name}」添加新版本；归属保持为：${where} · ${slotLabel(slot)}。`;
      return;
    }
    if (context.scope === "global") {
      node.textContent = `添加到：公共材料 · ${slotLabel(slot)}。`;
    } else {
      const iteration = iterationById(context.iterationId);
      node.textContent = iteration
        ? `添加到：${iterationName(iteration)}（${versionTypeLabel(iteration)}版本） · ${slotLabel(slot)}。`
        : "还没有版本可添加资料；请先在「编辑版本」中新建版本。";
    }
  }
  function makeReleasedArchiveHeading(count) {
    const heading = document.createElement("div");
    heading.className = "archive-released-heading";
    const title = document.createElement("span");
    title.className = "archive-released-title";
    title.textContent = "已发布版本";
    const hint = document.createElement("span");
    hint.className = "archive-released-hint";
    hint.textContent = `${count} 个版本 · 默认折叠，点击版本名展开`;
    heading.append(title, hint);
    return heading;
  }
  function renderSources() {
    const shell = $("#sources-dialog .library-shell");
    const scrollTop = shell?.scrollTop || 0;
    const archive = model.archiveView(state.sources, state.iterations);
    const activeScope = state.archiveScopeTab === "global" ? "global" : "iteration";
    $$(".archive-tab-btn").forEach(btn => {
      const isActive = btn.dataset.scope === activeScope;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", String(isActive));
      btn.tabIndex = isActive ? 0 : -1;
    });

    const body = $("#archive-body");
    body.setAttribute("role", "tabpanel");
    body.setAttribute("aria-labelledby", activeScope === "global" ? "tab-archive-global" : "tab-archive-iteration");
    let blocks;
    if (activeScope === "global") {
      blocks = [makeArchiveSection(archive.global)];
    } else {
      const activeSections = archive.iterations.filter(section => !model.iterationIsReleased(section.iteration));
      const releasedSections = archive.iterations.filter(section => model.iterationIsReleased(section.iteration));
      blocks = activeSections.map(section => makeArchiveSection(section));
      if (releasedSections.length) {
        blocks.push(makeReleasedArchiveHeading(releasedSections.length));
        blocks.push(...releasedSections.map(section => makeArchiveSection(section, { released: true })));
      }
    }
    body.replaceChildren(...blocks);
    if (shell) shell.scrollTop = scrollTop;

    const empty = $("#archive-empty");
    const noVersions = activeScope === "iteration" && archive.iterations.length === 0;
    empty.hidden = !noVersions;
    if (noVersions) empty.textContent = "还没有版本。请先在「编辑版本」中新建版本，再把资料按版本归档。";

    if (!state.libraryAddContext || (state.libraryAddContext.scope !== activeScope)) {
      state.libraryAddContext = defaultAddContext(activeScope);
    }
    if (state.libraryAddContext.scope === "iteration" && !iterationById(state.libraryAddContext.iterationId)) {
      state.libraryAddContext = defaultAddContext("iteration");
    }
    renderLibraryAddSlotOptions();
    updateLibraryAddContext();

    if (state.libraryHighlight) {
      const target = body.querySelector(`[data-group-id="${CSS.escape(state.libraryHighlight)}"]`);
      if (target) {
        target.classList.add("is-highlighted");
        target.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      state.libraryHighlight = null;
    }
  }
  function sourceFeedback(message, kind = "") {
    const feedback = $("#source-feedback"); feedback.textContent = message; feedback.className = `source-feedback library-feedback${kind ? ` ${kind}` : ""}`;
  }
  function sourceError(payload, status) {
    const errors = {
      source_duplicate: "这条链接已在资料馆中，请更换链接或名称。", invalid_source_url: "链接格式无效，请使用 http 或 https 地址。",
      invalid_source_update: "没有可保存的修改，请检查名称与链接。", invalid_source_type: "文件资料不能修改链接。",
      invalid_source_category: "资料类别无效。", invalid_source_name: "请填写有效的资料名称。",
      source_file_size_invalid: "文件为空或超过 15 MiB 大小限制。", unsupported_source_file_type: "当前文件类型不受支持。",
      source_file_type_mismatch: "文件内容与扩展名不符。", invalid_source_filename: "文件名无效。",
      source_archive_limits_exceeded: "Office 文件内容超出安全限制。", macros_not_allowed: "含宏文件不能加入资料馆。",
      source_text_must_be_utf8: "文本文件必须使用 UTF-8 编码。", source_not_found: "找不到这条资料，请刷新资料馆。",
      source_file_unavailable: "托管文件暂时不可用。", payload_too_large: "文件超过上传请求大小限制。",
      invalid_source_slot: "资料类型无效。", invalid_source_group: "资料版本组无效。", invalid_source_version: "版本说明无效。",
      source_group_not_found: "找不到要追加版本的资料组，请刷新后重试。", source_group_slot_conflict: "该资料组已属于其他固定资料位。",
      source_group_location_conflict: "同一资料组的版本必须归属同一个版本或公共材料，请整组移动。",
      invalid_iteration_id: "目标版本无效，请刷新后重试。",
      invalid_source_inspection: "巡检状态无效。"
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
      const data = await sourceRequest("/api/sources?include_removed=1&all_versions=1");
      const revision = String(data.revision ?? "");
      const changed = state.sourcesRevision !== revision;
      state.sources = Array.isArray(data.sources) ? data.sources : [];
      state.sourcesRevision = revision;
      if (changed || !$("#archive-body")?.childElementCount) renderSources();
      sourceFeedback("资料馆已更新。");
    } catch (error) {
      sourceFeedback(error.message || "无法读取资料馆，请确认本地服务正常。", "error");
    }
  }
  function setSourcesBusy(busy, message = "") {
    state.sourcesBusy = busy;
    ["#source-add-file", "#source-link-submit", "#library-add-cancel", "#library-file-clear", "#library-add-slot", "#source-link-note", "#source-link-note-masked"].forEach(selector => {
      const control = $(selector); if (control) control.disabled = busy || (selector === "#library-add-slot" && !!state.libraryGroup);
    });
    $$("[data-source-action]").forEach(button => { button.disabled = busy; });
    $$("[data-source-slot]").forEach(select => { select.disabled = busy; });
    if (message) sourceFeedback(message, busy ? "busy" : "");
  }
  async function encodeFile(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    return btoa(binary);
  }
  function selectLibraryFile(file) {
    state.libraryFile = file || null;
    $("#library-file-name").textContent = file ? file.name : "未选择文件";
    $("#library-file-clear").hidden = !file;
  }
  function setSourceNameHint(message, kind = "") {
    const hint = $("#source-name-hint");
    if (!hint) return;
    hint.textContent = message;
    hint.className = `library-name-hint${kind ? ` ${kind}` : ""}`;
  }
  function cancelSourceNamePreview() {
    clearTimeout(state.autoName.timer);
    state.autoName.timer = null;
    state.autoName.controller?.abort();
    state.autoName.controller = null;
    state.autoName.token += 1;
  }
  function resetSourceNamePreview(name = "") {
    cancelSourceNamePreview();
    state.autoName.userEdited = Boolean(String(name || "").trim());
    state.autoName.lastUrl = "";
    setSourceNameHint("");
  }
  async function previewSourceName(url) {
    const token = ++state.autoName.token;
    const controller = new AbortController();
    state.autoName.controller = controller;
    try {
      const response = await fetch(`/api/sources/preview?url=${encodeURIComponent(url)}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "preview_failed");
      if (token !== state.autoName.token) return;
      const title = typeof payload.title === "string" ? payload.title.trim() : "";
      const nameInput = $("#source-link-name");
      if (!title) { state.autoName.lastUrl = ""; setSourceNameHint("未能从链接取得标题，请手动填写名称。", "error"); return; }
      if (!nameInput.value.trim() && !state.autoName.userEdited) {
        nameInput.value = title.slice(0, 200);
        setSourceNameHint("已按链接填入名称，可手动修改。", "success");
      }
    } catch (error) {
      if (error.name === "AbortError" || token !== state.autoName.token) return;
      state.autoName.lastUrl = "";
      setSourceNameHint(error.message === "invalid_source_url"
        ? "该链接不是可公开访问的地址，请手动填写名称。"
        : "未能自动取名，请手动填写。", "error");
    } finally {
      if (state.autoName.controller === controller) state.autoName.controller = null;
    }
  }
  function scheduleSourceNamePreview() {
    const urlInput = $("#source-link-url");
    const nameInput = $("#source-link-name");
    if (!urlInput || !nameInput) return;
    clearTimeout(state.autoName.timer);
    state.autoName.token += 1;
    state.autoName.controller?.abort();
    state.autoName.controller = null;
    if (state.libraryFile) { setSourceNameHint(""); return; }
    const url = urlInput.value.trim();
    if (!url) { state.autoName.lastUrl = ""; setSourceNameHint(""); return; }
    if (!safeHttpUrl(url)) { setSourceNameHint("链接格式无效，请使用 http 或 https 地址。", "error"); return; }
    if (state.autoName.userEdited || nameInput.value.trim()) { setSourceNameHint(""); return; }
    if (url === state.autoName.lastUrl) return;
    state.autoName.lastUrl = url;
    setSourceNameHint("正在按链接取名字…", "busy");
    state.autoName.timer = window.setTimeout(() => previewSourceName(url), 520);
  }
  function openLibraryAdd({ slot = "", group = null, name = "", scope = null, iterationId = null } = {}) {
    const card = group?.card || null;
    const resolvedScope = scope || (card ? model.sourceScope(card) : null) || state.archiveScopeTab || "iteration";
    const resolvedIterationId = iterationId || card?.iteration_id || (resolvedScope === "iteration" ? defaultAddContext("iteration").iterationId : null);
    const groupSlot = group?.slot || (card && card.slot) || slot || "supplementary";
    state.libraryGroup = group ? {
      group_id: group.group_id, slot: groupSlot, name: group.name || card?.name || name,
      scope: resolvedScope, iterationId: resolvedIterationId
    } : null;
    state.libraryAddContext = { scope: resolvedScope === "global" ? "global" : "iteration", iterationId: resolvedIterationId, slot: groupSlot };
    const displayName = state.libraryGroup?.name || name || "";
    selectLibraryFile(null);
    $("#source-file-input").value = "";
    $("#source-link-name").value = displayName;
    $("#source-link-url").value = "";
    $("#library-add-version").value = "";
    $("#source-link-note").value = "";
    $("#source-link-note-masked").checked = false;
    resetSourceNamePreview(displayName);
    renderLibraryAddSlotOptions();
    $("#library-add-slot").value = groupSlot;
    updateLibraryAddContext();
    $("#library-add-fold").open = true;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    $("#library-add-fold").scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" });
    $("#source-link-name").focus({ preventScroll: true });
  }
  function resetLibraryAdd(close = true) {
    state.libraryGroup = null;
    cancelSourceNamePreview();
    selectLibraryFile(null);
    $("#source-link-form").reset();
    setSourceNameHint("");
    $("#library-add-slot").disabled = false;
    if (close) $("#library-add-fold").open = false;
    renderLibraryAddSlotOptions();
    updateLibraryAddContext();
  }
  async function submitLibraryEntry(event) {
    event.preventDefault();
    if (state.sourcesBusy) return;
    const file = state.libraryFile;
    const group = state.libraryGroup;
    const context = state.libraryAddContext || defaultAddContext(state.archiveScopeTab);
    const name = $("#source-link-name").value.trim();
    const url = $("#source-link-url").value.trim();
    if (!file && !name) { sourceFeedback("请填写资料名称。", "error"); return; }
    if (!file && !url) { sourceFeedback("请填写资料链接，或选择一个文件。", "error"); return; }
    if (!file && !safeHttpUrl(url)) { sourceFeedback("链接格式无效，请使用 http 或 https 地址。", "error"); return; }
    if (file && file.size > 15 * 1024 * 1024) { sourceFeedback("文件超过 15 MiB，请选择更小的文件。", "error"); return; }
    const scope = context.scope === "global" ? "global" : "iteration";
    const iteration_id = scope === "iteration" ? context.iterationId : null;
    if (scope === "iteration" && !iteration_id) { sourceFeedback("请先在「编辑版本」中新建版本，再添加版本资料。", "error"); return; }
    const slot = group?.slot || $("#library-add-slot").value || "supplementary";
    const versionLabel = $("#library-add-version").value.trim() || null;
    const note = $("#source-link-note").value.trim();
    const noteMasked = note ? $("#source-link-note-masked").checked : false;
    const category = model.archiveSlotLabels[slot] || "补充材料";
    setSourcesBusy(true, file ? `正在添加“${file.name}”…` : "正在添加资料…");
    try {
      let noteError = null;
      if (file) {
        const content_base64 = await encodeFile(file);
        const created = await sourceRequest("/api/sources/file", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          filename: file.name, content_base64, category, name: name || file.name, slot, group_id: group?.group_id || null, version_label: versionLabel,
          scope, iteration_id
        }) });
        if (note || noteMasked) {
          try {
            await sourceRequest(`/api/sources/${encodeURIComponent(created.source.id)}/edit`, {
              method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note, note_masked: noteMasked })
            });
          } catch (error) { noteError = error; }
        }
      } else {
        await sourceRequest("/api/sources/link", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          name, url, category, slot, group_id: group?.group_id || null, version_label: versionLabel,
          scope, iteration_id, note, note_masked: noteMasked
        }) });
      }
      await loadSources();
      if (noteError) sourceFeedback(`文件已添加，但备注保存失败：${noteError.message || "请重试"}。可点击资料卡重新编辑。`, "error");
      else sourceFeedback(file ? `已添加文件“${file.name}”。` : "已添加资料。");
      resetLibraryAdd();
    } catch (error) {
      sourceFeedback(error.message || "资料添加失败，请重试。", "error");
    } finally {
      setSourcesBusy(false);
    }
  }
  async function toggleSourceInspection(sourceId) {
    const source = state.sources.find(item => item.id === sourceId);
    if (!source || state.sourcesBusy) return;
    const enabled = !(source.inspection_enabled === true);
    setSourcesBusy(true, enabled ? "正在加入巡检…" : "正在移出巡检…");
    try {
      await sourceRequest(`/api/sources/${encodeURIComponent(sourceId)}/inspection`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) });
      await loadSources();
      sourceFeedback(enabled ? "已加入巡检。" : "已移出巡检。");
    } catch (error) { sourceFeedback(error.message || "巡检状态更新失败，请重试。", "error"); }
    finally { setSourcesBusy(false); }
  }
  async function changeSourceActive(sourceId, action) {
    if (state.sourcesBusy) return;
    const route = `/api/sources/${encodeURIComponent(sourceId)}${action === "restore" ? "/restore" : ""}`;
    setSourcesBusy(true, action === "restore" ? "正在恢复资料…" : "正在移出资料…");
    try {
      if (action === "restore") await sourceRequest(route, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      else await sourceRequest(route, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}" });
      await loadSources(); sourceFeedback(action === "restore" ? "资料已恢复。" : "资料已移出，可在下方恢复。");
    } catch (error) { sourceFeedback(error.message || "资料状态更新失败，请重试。", "error"); }
    finally { setSourcesBusy(false); }
  }
  function handleSourceAction(button) {
    if (!button) return;
    if (button.dataset.sourceAction === "inspection") return toggleSourceInspection(button.dataset.sourceId);
    if (button.dataset.sourceAction === "remove" || button.dataset.sourceAction === "restore") return changeSourceActive(button.dataset.sourceId, button.dataset.sourceAction);
    if (button.dataset.sourceAction === "edit") {
      return openSourceEdit(state.sources.find(item => item.id === button.dataset.sourceId));
    }
    if (button.dataset.sourceAction === "add-context" || button.dataset.sourceAction === "add-slot") {
      return openLibraryAdd({
        slot: button.dataset.slot || "supplementary",
        scope: button.dataset.scope || state.archiveScopeTab,
        iterationId: button.dataset.iterationId || null
      });
    }
    if (button.dataset.sourceAction === "add-version") {
      const card = state.sources.find(source => source.id === button.dataset.sourceId)
        || state.sources.find(source => (source.group_id || source.id) === button.dataset.groupId);
      return openLibraryAdd({
        slot: button.dataset.slot || "",
        scope: button.dataset.scope || null,
        iterationId: button.dataset.iterationId || null,
        group: {
          group_id: button.dataset.groupId,
          slot: button.dataset.slot || null,
          name: button.dataset.name || "",
          card
        }
      });
    }
  }
  function handleLibraryClick(event) {
    const versionToggle = event.target.closest("[data-library-versions]");
    if (versionToggle) {
      const historyKey = versionToggle.dataset.libraryVersions;
      const expanded = !state.libraryExpanded.has(historyKey);
      if (expanded) state.libraryExpanded.add(historyKey);
      else state.libraryExpanded.delete(historyKey);
      versionToggle.setAttribute("aria-expanded", String(expanded));
      const history = document.getElementById(libraryHistoryId(historyKey));
      if (history) history.hidden = !expanded;
      return;
    }
    handleSourceAction(event.target.closest("[data-source-action]"));
    if (event.target.closest("button,a,input,select,textarea,summary,details,.library-history")) return;
    const card = event.target.closest(".library-card[data-source-id]");
    if (!card) return;
    openSourceEdit(state.sources.find(item => item.id === card.dataset.sourceId));
  }
  function sourceEditFeedback(message, kind = "") {
    const node = $("#source-edit-feedback");
    if (!node) return;
    node.textContent = message;
    node.className = `source-edit-feedback${kind ? ` ${kind}` : ""}`;
  }
  function openSourceEdit(source) {
    if (!source || state.sourcesBusy) return;
    state.sourceEdit = source;
    const isFile = source.type === "file";
    $("#source-edit-title").textContent = isFile ? "编辑文件资料" : "编辑资料";
    $("#source-edit-context").textContent = `${sourceDisplayName(source)} · ${sourceLocationText(source)}`;
    $("#source-edit-name").value = sourceDisplayName(source);
    $("#source-edit-url").value = isFile ? "" : (source.url || "");
    $("#source-edit-url-wrap").hidden = isFile;
    $("#source-edit-note-input").value = source.note || "";
    $("#source-edit-note-masked").checked = source.note_masked === true;
    $("#source-edit-note").hidden = !isFile;
    sourceEditFeedback("");
    $("#source-edit-dialog").showModal();
    $("#source-edit-name").focus({ preventScroll: true });
    $("#source-edit-name").select();
  }
  function closeSourceEdit() {
    state.sourceEdit = null;
    if ($("#source-edit-dialog")?.open) $("#source-edit-dialog").close();
  }
  async function submitSourceEdit(event) {
    event.preventDefault();
    const source = state.sourceEdit;
    if (!source) return;
    const isFile = source.type === "file";
    const name = $("#source-edit-name").value.trim();
    const url = isFile ? "" : $("#source-edit-url").value.trim();
    const note = $("#source-edit-note-input").value.trim();
    const noteMasked = note ? $("#source-edit-note-masked").checked : false;
    if (!name) { sourceEditFeedback("请填写资料名称。", "error"); $("#source-edit-name").focus(); return; }
    if (!isFile && !url) { sourceEditFeedback("请填写资料链接。", "error"); $("#source-edit-url").focus(); return; }
    if (!isFile && !safeHttpUrl(url)) { sourceEditFeedback("链接格式无效，请使用 http 或 https 地址。", "error"); $("#source-edit-url").focus(); return; }
    const button = $("#source-edit-submit");
    button.disabled = true;
    sourceEditFeedback("正在保存…", "busy");
    try {
      const payload = isFile ? { name, note, note_masked: noteMasked } : { name, url, note, note_masked: noteMasked };
      await sourceRequest(`/api/sources/${encodeURIComponent(source.id)}/edit`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      });
      if (state.sourceEdit?.id === source.id) closeSourceEdit();
      await loadSources();
      sourceFeedback("资料已更新，版本与归属保持不变。");
      showToast("资料已更新");
    } catch (error) {
      sourceEditFeedback(error.message || "保存失败，请重试。", "error");
    } finally {
      button.disabled = false;
    }
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
    const ownerChanged = isOwnerChanged(record);
    const done = isCompleted(record) || ownerChanged, cancelled = isCancelled(record), terminal = done || cancelled, busy = state.busy.has(record.id);
    const pendingRelease = effectiveStatus(record) === "pending_release";
    article.className = `record-card${terminal ? " record-completed record-terminal" : ""}${cancelled ? " record-cancelled" : ""}${pendingRelease ? " record-pending-release" : ""}${record.transfer_disposition === "transfer_pending" ? " transfer-pending" : ""}${busy ? " is-busy" : ""}`;
    const head = document.createElement("div"); head.className = "record-head";
    const idLabel = document.createElement("span"); idLabel.className = "record-id"; idLabel.textContent = recordDisplayId(record); idLabel.title = record.display_id && record.display_id !== record.id ? `原始编号：${record.id}` : record.id; head.append(idLabel);
    const status = document.createElement("span"); status.className = `status-pill ${record.transfer_disposition === "transfer_pending" ? "transfer-pending" : escClass(record.type === "bug" ? (record.platform_status || record.status) : effectiveStatus(record))}`;
    const statusCode = effectiveStatus(record);
    status.textContent = record.transfer_disposition === "transfer_pending" ? "待转交" : record.transfer_disposition === "transferred" ? "责任转移" : ownerChanged ? "责任人变更" : cancelled ? "已取消" : done ? "已完成" : statusLabel(["in_progress", "developing", "testing", "on_hold", "pending_release"].includes(statusCode) || record.type === "task" ? statusCode : sourceStatus(record)); head.append(status);
    const chip = model.workflowChip(record);
    if (chip) {
      const stageChip = document.createElement("span");
      stageChip.className = "stage-chip"; stageChip.dataset.stage = chip.stageId; stageChip.dataset.status = chip.status;
      // 展示层：把流程阶段写成“第 n/6 阶段 + 名称”，并用六格细轨标出位置；
      // 不改变阶段数据、状态机或详情里的 stepper。
      const stages = model.workflowStagesFallback || [];
      const stageIndex = stages.findIndex(stage => stage.id === chip.stageId);
      const stageTotal = stages.length;
      const stagePosition = stageIndex >= 0 ? `${stageIndex + 1}/${stageTotal}` : "";
      const stageNote = chip.status === "rework" ? ` · ${chip.statusLabel}` : "";
      const stageTitle = stagePosition ? chip.title.replace("流水线：", `流水线第 ${stageIndex + 1}/${stageTotal} 阶段：`) : chip.title;
      stageChip.textContent = `${stagePosition ? `阶段 ${stagePosition} · ` : "阶段 · "}${chip.label}${stageNote}`;
      stageChip.title = stageTitle; stageChip.setAttribute("aria-label", stageTitle);
      if (stagePosition) {
        const rail = document.createElement("span"); rail.className = "stage-rail"; rail.setAttribute("aria-hidden", "true");
        for (let index = 0; index < stageTotal; index += 1) {
          const tick = document.createElement("i"); if (index <= stageIndex) tick.className = "is-on"; rail.append(tick);
        }
        stageChip.append(rail);
      }
      head.append(stageChip);
    }
    if (record.reopen_count > 0) {
      const rBadge = document.createElement("span");
      rBadge.className = "reopen-badge";
      rBadge.textContent = `R${record.reopen_count + 1}`;
      rBadge.title = `已进行 ${record.reopen_count} 轮二次修改`;
      head.append(rBadge);
    }
    const open = document.createElement("button"); open.type = "button"; open.className = "record-title-button"; open.dataset.openRecord = record.id; open.setAttribute("aria-label", `查看详情：${record.title}`);
    const title = document.createElement("span"); title.className = "record-title"; title.textContent = record.title; open.append(title);
    const summary = document.createElement("p"); summary.className = "card-summary"; summary.textContent = record.status_line || record.latest_brief || cardBrief(record);
    article.append(head, open, summary);
    if (["in_progress", "developing", "testing"].includes(effectiveStatus(record)) && record.current_agent_session_id) {
      const processing = document.createElement("p"); processing.className = "agent-processing";
      processing.textContent = `${txt(record.current_agent_name, "Agent")}正在处理中`;
      article.append(processing);
    }
    const preview = makeFollowupPreview(record); if (preview) article.append(preview);
    const actions = document.createElement("div"); actions.className = "card-actions";
    const complete = document.createElement("button"); complete.type = "button"; complete.className = terminal ? "complete-button is-complete" : "complete-button";
    complete.dataset.completeId = record.id; complete.textContent = cancelled ? "已取消" : done ? (hasManualCompletion(record) ? "撤销完成" : "✓ 已完成") : "完成";
    complete.disabled = busy || (terminal && (!done || !hasManualCompletion(record))); complete.setAttribute("aria-pressed", String(done));
    // 跟进与发起修改入口已从卡片移除；历史与二次修改数据仍在详情只读展示。
    if (record.transfer_disposition === "transfer_pending") {
      const transfer = document.createElement("button"); transfer.type = "button"; transfer.className = "transfer-button"; transfer.dataset.transferId = record.id; transfer.textContent = "已转交"; transfer.disabled = busy;
      actions.append(transfer);
    } else actions.append(complete);
    article.append(actions);
    return article;
  }
  function populateMasonryList(list, cards) {
    if (!masonryLists.has(list)) { masonryLists.add(list); masonryObserver.observe(list); }
    const columns = [document.createElement("div"), document.createElement("div")];
    columns.forEach(column => { column.className = "masonry-column"; });
    list.replaceChildren(...columns);
    // At narrow widths CSS hides the second column. Never place cards into a
    // column that has no layout box, otherwise they disappear visually.
    const visibleColumns = columns.map(column => getComputedStyle(column).display !== "none");
    // Row-first: 0→left, 1→right, 2→left… No height measurement is needed, so
    // rebuilding long lists stays free of layout reads. Inline order also keeps
    // the single-column reading order correct beyond the CSS 0–47 fallback.
    cards.forEach((card, index) => {
      card.dataset.masonryOrder = String(index);
      card.style.order = String(index);
      columns[model.masonryColumnIndex([], visibleColumns, index)].append(card);
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

  // Topbar road (2026-11-20 redo): four long, soft lane curves that run the
  // full height of the header — from below the bottom edge to above the top
  // edge — and share one continuous geometry for every header height. Near
  // lanes spread wide, far lanes keep a visible gap, so the fan reads as a
  // road receding into the distance rather than a closed crescent. Solid and
  // short-dashed lanes are combined. Each lane keeps its own gentle bow and
  // sway phase so the four curves never collapse onto one trajectory.
  // Idle flow never stops; scroll nudges the phase; compression bends the
  // same curves in place instead of switching to another band. Canvas, rAF,
  // hidden-page pause and reduced-motion static frame are kept.
  function setupTopbarWaves(topbar) {
    const host = $(".topbar-waves", topbar);
    const canvas = host ? $("canvas", host) : null;
    const noop = { pulse() {}, nudge() {} };
    if (!canvas || typeof canvas.getContext !== "function") return noop;
    const ctx = canvas.getContext("2d");
    if (!ctx) return noop;
    const reduceMotion = window.matchMedia
      ? window.matchMedia("(prefers-reduced-motion: reduce)")
      : { matches: false };
    const PULSE_MS = 2100;
    const clamp01 = value => Math.min(1, Math.max(0, value));
    let width = 1, height = 1, dpr = 1;
    let road = null;
    let frame = 0;
    let running = false;
    let resizeFrame = 0;
    let lastPaint = 0;
    let surgeAt = -1;
    let surgeStrength = 1;
    let scrollPhase = 0;

    function measureRoad() {
      const styles = window.getComputedStyle ? getComputedStyle(topbar) : null;
      const rawP = styles ? parseFloat(styles.getPropertyValue("--topbar-p")) : NaN;
      if (Number.isFinite(rawP)) return { compressed: clamp01(rawP) };
      // 兜底：按顶栏实际高度估算压缩进度（窄屏换行时高度会大于展开值）。
      const rect = topbar.getBoundingClientRect();
      const expanded = styles ? parseFloat(styles.getPropertyValue("--topbar-expanded")) || 118 : 118;
      const compact = styles ? parseFloat(styles.getPropertyValue("--topbar-compact")) || 64 : 64;
      return { compressed: compact < expanded ? clamp01((expanded - rect.height) / (expanded - compact)) : 0 };
    }
    function resize() {
      const rect = topbar.getBoundingClientRect();
      const nextWidth = Math.max(1, Math.round(rect.width));
      const nextHeight = Math.max(1, Math.round(rect.height));
      const nextDpr = Math.min(window.devicePixelRatio || 1, 2);
      const changed = nextWidth !== width || nextHeight !== height || nextDpr !== dpr;
      width = nextWidth; height = nextHeight; dpr = nextDpr;
      if (changed) {
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      road = measureRoad();
      draw(reduceMotion.matches ? 0 : performance.now(), reduceMotion.matches ? 0 : surgeLevel(performance.now()));
    }
    // One slow hump: 0 → peak → 0 with zero velocity at both ends.
    function surgeLevel(now) {
      if (surgeAt < 0) return 0;
      const progress = (now - surgeAt) / PULSE_MS;
      if (progress >= 1) { surgeAt = -1; return 0; }
      return (0.5 - 0.5 * Math.cos(progress * Math.PI * 2)) * surgeStrength;
    }
    function draw(now, surge) {
      ctx.clearRect(0, 0, width, height);
      if (!road) road = measureRoad();
      const w = width, h = height;
      if (w < 80 || h < 24) return;
      const dark = document.body.dataset.theme === "dark";
      // 低饱和蓝/墨：只作为玻璃底色里的纹理，不抢品牌与按钮。
      const blue = dark ? { r: 138, g: 168, b: 198 } : { r: 66, g: 99, b: 132 };
      const ink = dark ? { r: 150, g: 164, b: 184 } : { r: 70, g: 82, b: 98 };
      const time = now * 0.001;
      // 静止时仍持续推进：形状相位、呼吸与虚线偏移共用同一时间源。
      const phase = time * 0.3 + scrollPhase + surge * 0.55;
      const breathe = 0.86 + 0.14 * Math.sin(time * 0.9 + 0.6);
      const compressed = road.compressed || 0;
      // 窄屏上线条会经过标题与按钮，整体再压暗一档，保持品牌优先。
      const narrowFade = w < 480 ? 0.72 : 1;
      // 透视：底边（近处）相邻车道间距约 0.20w，顶边（远处）保留约 0.05w，
      // 四条线收向地平线但不会并成一点，整体读作“远处的路”而非香蕉。
      const spreadAt = t => {
        const near = w * (0.205 - compressed * 0.010);
        const far = w * (0.052 - compressed * 0.003);
        // 远端不过度收拢；底边略微外扩，让路在近处张开。
        return far + (near - far) * Math.pow(Math.max(0, 1 - t), 0.92);
      };
      // 中线只做很轻的右移加速弯（二次项），曲率舒展，避免和车道一起卷成月牙。
      const centerAt = t => w * (0.405 + 0.17 * t + 0.06 * t * t);
      const laneX = (t, lane) => {
        // 端点归零的包络：上下边缘的开口与间距由 spreadAt 精确控制。
        const envelope = Math.sin(Math.PI * t);
        // 每条线独立的相位/幅度，共用中线但不共用同一条弯曲线。
        const sway = w * lane.sway * envelope * Math.sin(phase * 1.3 + lane.phase + t * 2.4);
        // 压缩不换组：沿同一条曲线把中段轻轻推弯，高度变化时连续变形。
        const bend = w * (0.020 * compressed + 0.016 * surge) * envelope * lane.bend;
        const bow = w * lane.bow * envelope;
        return centerAt(t) + sway + bend + bow + lane.offset * spreadAt(t);
      };
      const startT = -0.05, endT = 1.05, segments = 64;
      const lanes = [
        { offset: -1.55, bend: 1.05, bow: -0.010, sway: 0.010, phase: 0, rgb: blue, alpha: 0.26, width: 1.35, dash: null },
        { offset: -0.5, bend: 0.9, bow: 0.006, sway: 0.012, phase: 0.9, rgb: ink, alpha: 0.19, width: 1.1, dash: null },
        { offset: 0.5, bend: 1.14, bow: -0.005, sway: 0.011, phase: 1.9, rgb: blue, alpha: 0.24, width: 1.25, dash: [14, 22] },
        { offset: 1.55, bend: 0.86, bow: 0.012, sway: 0.013, phase: 2.7, rgb: ink, alpha: 0.18, width: 1.5, dash: null }
      ];
      // 上下边缘渐隐，让线像从玻璃外延伸进来，而不是在边框处硬截断。
      if (!draw._gradCache || draw._gradH !== h || draw._gradDark !== dark) {
        const color = (rgb, alpha) => `rgba(${rgb.r},${rgb.g},${rgb.b},${alpha})`;
        const makeFade = rgb => {
          const fade = ctx.createLinearGradient(0, h, 0, 0);
          fade.addColorStop(0, color(rgb, 0));
          fade.addColorStop(0.08, color(rgb, 1));
          fade.addColorStop(0.92, color(rgb, 1));
          fade.addColorStop(1, color(rgb, 0));
          return fade;
        };
        draw._gradCache = { blue: makeFade(blue), ink: makeFade(ink) };
        draw._gradH = h;
        draw._gradDark = dark;
      }
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      for (const lane of lanes) {
        ctx.strokeStyle = lane.rgb === blue ? draw._gradCache.blue : draw._gradCache.ink;
        ctx.lineWidth = lane.width;
        ctx.globalAlpha = Math.min(0.34, lane.alpha * breathe * (1 + surge * 0.4)) * narrowFade;
        ctx.setLineDash(lane.dash || []);
        // 虚线沿道路缓慢行进，是静止状态里最容易被看到的持续运动。
        ctx.lineDashOffset = lane.dash ? -(time * 16 + scrollPhase * 4) : 0;
        ctx.beginPath();
        for (let index = 0; index <= segments; index += 1) {
          const t = startT + (endT - startT) * (index / segments);
          const x = laneX(t, lane), y = h * (1 - t);
          if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
        }
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
    function tick(now) {
      if (document.hidden) { stop(); return; }
      if (reduceMotion.matches) { stop(); draw(0, 0); return; }
      const surge = surgeLevel(now);
      if (now - lastPaint >= (surge > 0.01 ? 22 : 50)) {
        lastPaint = now;
        draw(now, surge);
      }
      frame = requestAnimationFrame(tick);
    }
    function start() {
      if (running || document.hidden || reduceMotion.matches) return;
      running = true;
      lastPaint = 0;
      frame = requestAnimationFrame(tick);
    }
    function stop() {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      running = false;
    }
    function pulse(strength = 1) {
      if (reduceMotion.matches) return;
      const now = performance.now();
      if (surgeAt >= 0 && now - surgeAt < PULSE_MS * 0.6) return;
      surgeAt = now;
      surgeStrength = Math.max(0.55, Math.min(1, strength));
      start();
    }
    // 滚动本身推动道路相位：只要页面在动，线条就跟着连续流动、不冻结。
    function nudge(dy) {
      if (reduceMotion.matches || !Number.isFinite(dy) || Math.abs(dy) < 0.5) return;
      scrollPhase = Math.max(-8, Math.min(8, scrollPhase + dy * 0.0018));
      if (!running) start();
    }
    function scheduleResize() {
      if (resizeFrame) return;
      resizeFrame = requestAnimationFrame(() => { resizeFrame = 0; resize(); });
    }

    if (window.ResizeObserver) new ResizeObserver(scheduleResize).observe(topbar);
    if (window.MutationObserver) new MutationObserver(() => {
      if (!running) draw(reduceMotion.matches ? 0 : performance.now(), 0);
    }).observe(document.body, { attributes: true, attributeFilter: ["data-theme"] });
    document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); else start(); });
    reduceMotion.addEventListener?.("change", () => {
      if (reduceMotion.matches) { stop(); draw(0, 0); }
      else start();
    });
    document.fonts?.ready?.then(scheduleResize);
    resize();
    if (!reduceMotion.matches && !document.hidden) start();
    return { pulse, nudge, resize: scheduleResize };
  }

  // Scroll chrome: a fixed header that compresses on downward scrolling and
  // restores on upward scrolling, plus one shared overlay scrollbar for the
  // page and every dialog scroll surface. Everything is driven by cached
  // geometry so scroll frames only read scrollTop and write CSS
  // variables/classes; no layout is read inside the scroll handler.
  function setupScrollChrome() {
    const topbar = $(".topbar");
    const pageRail = $("#page-scroll-rail");
    const detailRail = $("#detail-scroll-rail");
    const detail = $("#detail");
    const dialog = $("#record-dialog");
    if (!topbar || !pageRail) return null;
    const waves = setupTopbarWaves(topbar);

    const reduceMotion = window.matchMedia
      ? window.matchMedia("(prefers-reduced-motion: reduce)")
      : { matches: false };
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const setVar = (node, name, value) => { if (node) node.style.setProperty(name, value); };
    // 光束长度不按内容比例缩短：它是一束投在右缘的光，光晕至少覆盖轨道一半。
    const beamSize = track => track > 1 ? Math.max(0, Math.min(track - 12, Math.max(72, track * 0.56))) : 0;
    const paintFlow = (rail, flow) => {
      const wave = Math.sin(flow.value * Math.PI * 2);
      setVar(rail, "--rail-flow", flow.value.toFixed(3));
      setVar(rail, "--rail-core-shift", wave.toFixed(3));
      setVar(rail, "--rail-energy", (0.55 + 0.45 * Math.abs(wave)).toFixed(3));
    };
    const pageGeo = { max: 0, thumb: 0, travel: 0 };
    const detailGeo = { max: 0, thumb: 0, travel: 0 };
    const observers = [];
    const dialogRails = [];
    // Continuous light-flow phase for the edge beam: scroll delta advances it,
    // and the core offset / halo energy follow so the beam keeps changing
    // strength and position while the page moves.
    const pageFlow = { value: 0.5 };
    const detailFlow = { value: 0.5 };

    let lastScrollY = window.scrollY;
    let lastDetailTop = detail ? detail.scrollTop : 0;
    let downRun = 0;
    let upRun = 0;
    let topbarP = window.scrollY > 24 ? 1 : 0;
    let topbarTarget = topbarP;
    let topbarFrame = 0;
    let scrollFrame = 0;
    let measureFrame = 0;
    let detailFrame = 0;
    let detailMeasureFrame = 0;

    function nudgeFlow(flow, delta, rail) {
      if (!rail || !Number.isFinite(delta) || Math.abs(delta) < 0.01) return;
      // The gradient phase keeps moving with the content: scrolling shifts the
      // bright band inside the thumb instead of only translating the thumb.
      flow.value = (flow.value + delta / 260) % 1;
      if (flow.value < 0) flow.value += 1;
      paintFlow(rail, flow);
    }

    function paintTopbar(value) {
      topbarP = clamp(value, 0, 1);
      topbar.style.setProperty("--topbar-p", topbarP.toFixed(3));
      const scrolled = topbarP > 0.02;
      if (topbar.classList.contains("is-scrolled") !== scrolled) topbar.classList.toggle("is-scrolled", scrolled);
    }

    function aimTopbar(value) {
      const changed = Math.abs(value - topbarTarget) >= 0.5;
      topbarTarget = value;
      if (changed) waves.pulse(1);
      if (reduceMotion.matches) { paintTopbar(value); return; }
      if (topbarFrame || Math.abs(topbarTarget - topbarP) < 0.003) return;
      const step = () => {
        if (reduceMotion.matches) { paintTopbar(topbarTarget); topbarFrame = 0; return; }
        const diff = topbarTarget - topbarP;
        if (Math.abs(diff) < 0.004) { paintTopbar(topbarTarget); topbarFrame = 0; return; }
        paintTopbar(topbarP + diff * 0.24);
        topbarFrame = requestAnimationFrame(step);
      };
      topbarFrame = requestAnimationFrame(step);
    }

    function measurePageRail() {
      const doc = document.scrollingElement || document.documentElement;
      const viewport = Math.max(1, window.innerHeight);
      const scrollHeight = Math.max(doc.scrollHeight, viewport);
      const max = Math.max(0, scrollHeight - viewport);
      const railHeight = pageRail.getBoundingClientRect().height || viewport;
      const track = Math.max(0, railHeight - 24);
      const thumb = max > 1 ? beamSize(track) : 0;
      pageGeo.max = max;
      pageGeo.thumb = thumb;
      pageGeo.travel = Math.max(0, track - thumb);
      setVar(pageRail, "--rail-size", `${thumb}px`);
      setVar(pageRail, "--rail-travel", `${pageGeo.travel}px`);
      paintFlow(pageRail, pageFlow);
      const active = max > 1 && thumb > 0;
      pageRail.classList.toggle("is-hidden", !active);
      pageRail.classList.toggle("is-active", active);
      updatePageChrome(true);
    }

    function updatePageChrome(measureOnly = false) {
      const y = Math.max(0, window.scrollY);
      if (!measureOnly) {
        const dy = y - lastScrollY;
        lastScrollY = y;
        nudgeFlow(pageFlow, dy, pageRail);
        waves.nudge(dy);
        if (y <= 8) {
          downRun = 0;
          upRun = 0;
          aimTopbar(0);
        } else if (dy > 0.5) {
          downRun += dy;
          upRun = 0;
          if (downRun >= 18) aimTopbar(1);
        } else if (dy < -0.5) {
          upRun -= dy;
          downRun = 0;
          if (upRun >= 18) aimTopbar(0);
        }
      }
      const progress = pageGeo.max > 1 ? clamp(y / pageGeo.max, 0, 1) : 0;
      setVar(pageRail, "--rail-progress", progress.toFixed(4));
    }

    function measureDetailRail() {
      if (!detailRail || !detail || !dialog?.open) {
        detailRail?.classList.add("is-hidden");
        detailRail?.classList.remove("is-active");
        return;
      }
      const shell = $(".record-dialog .dialog-shell");
      if (!shell) return;
      const shellRect = shell.getBoundingClientRect();
      const rect = detail.getBoundingClientRect();
      if (rect.height < 1 || rect.width < 1) {
        detailRail.classList.add("is-hidden");
        detailRail.classList.remove("is-active");
        return;
      }
      const track = Math.max(0, rect.height - 16);
      const scrollHeight = Math.max(detail.scrollHeight, detail.clientHeight, 1);
      const max = Math.max(0, scrollHeight - detail.clientHeight);
      const thumb = max > 1 ? beamSize(track) : 0;
      detailGeo.max = max;
      detailGeo.thumb = thumb;
      detailGeo.travel = Math.max(0, track - thumb);
      detailRail.style.top = `${Math.max(0, rect.top - shellRect.top)}px`;
      detailRail.style.height = `${rect.height}px`;
      setVar(detailRail, "--rail-size", `${thumb}px`);
      setVar(detailRail, "--rail-travel", `${detailGeo.travel}px`);
      paintFlow(detailRail, detailFlow);
      const active = max > 1 && thumb > 0;
      detailRail.classList.toggle("is-hidden", !active);
      detailRail.classList.toggle("is-active", active);
      lastDetailTop = detail.scrollTop;
      updateDetailRail();
    }

    function updateDetailRail() {
      if (!detailRail || !dialog?.open) return;
      const top = detail.scrollTop;
      nudgeFlow(detailFlow, top - lastDetailTop, detailRail);
      lastDetailTop = top;
      const progress = detailGeo.max > 1 ? clamp(top / detailGeo.max, 0, 1) : 0;
      setVar(detailRail, "--rail-progress", progress.toFixed(4));
    }

    function scheduleScrollFrame() {
      if (scrollFrame) return;
      scrollFrame = requestAnimationFrame(() => { scrollFrame = 0; updatePageChrome(); });
    }

    function scheduleMeasure() {
      if (measureFrame) return;
      measureFrame = requestAnimationFrame(() => {
        measureFrame = 0;
        measurePageRail();
        measureDetailRail();
        for (const rail of dialogRails) rail.measure();
      });
    }

    function scheduleDetailMeasure() {
      if (detailMeasureFrame) return;
      detailMeasureFrame = requestAnimationFrame(() => {
        detailMeasureFrame = 0;
        measureDetailRail();
      });
    }

    function bindRail(rail, geo, options) {
      if (!rail) return;
      let drag = null;
      let pendingFrame = 0;
      let pendingTop = 0;
      const apply = () => {
        pendingFrame = 0;
        const top = clamp(pendingTop, 0, geo.max);
        options.setScroll(top);
        const progress = geo.max > 1 ? top / geo.max : 0;
        setVar(rail, "--rail-progress", progress.toFixed(4));
      };
      rail.addEventListener("pointerdown", event => {
        if (rail.classList.contains("is-hidden") || geo.max <= 1) return;
        if (event.button !== undefined && event.button !== 0) return;
        const rect = rail.getBoundingClientRect();
        const trackTop = rect.top + options.pad;
        const thumbHeight = Math.max(1, geo.thumb);
        const trackHeight = Math.max(thumbHeight, rect.height - options.pad * 2);
        const travel = Math.max(1, trackHeight - thumbHeight);
        const current = clamp(options.getScroll(), 0, geo.max);
        const thumbTop = trackTop + (current / geo.max) * travel;
        const inThumb = event.clientY >= thumbTop && event.clientY <= thumbTop + thumbHeight;
        let startScroll = current;
        if (!inThumb) {
          const ratio = clamp((event.clientY - trackTop - thumbHeight / 2) / travel, 0, 1);
          startScroll = ratio * geo.max;
          pendingTop = startScroll;
          apply();
        }
        drag = { startY: event.clientY, startScroll, travel };
        rail.classList.add("is-dragging");
        try { rail.setPointerCapture(event.pointerId); } catch (_) {}
        event.preventDefault();
      }, { passive: false });
      rail.addEventListener("pointermove", event => {
        if (!drag) return;
        pendingTop = drag.startScroll + ((event.clientY - drag.startY) / drag.travel) * geo.max;
        if (!pendingFrame) pendingFrame = requestAnimationFrame(apply);
        event.preventDefault();
      }, { passive: false });
      const end = event => {
        if (!drag) return;
        drag = null;
        rail.classList.remove("is-dragging");
        try { if (rail.hasPointerCapture?.(event.pointerId)) rail.releasePointerCapture(event.pointerId); } catch (_) {}
      };
      rail.addEventListener("pointerup", end);
      rail.addEventListener("pointercancel", end);
      rail.addEventListener("lostpointercapture", end);
    }

    // Every dialog scroll surface shares the same 3px gradient rail. The rail
    // is created lazily inside its dialog, aligned to the scroll box, and
    // re-measured on dialog toggle, resize and content mutations. Native
    // scrollbars on these boxes are hidden in CSS so there is never a double
    // scrollbar; wheel, touch and keyboard scrolling keep their native paths.
    function setupDialogRail(dialogSelector, containerSelector) {
      const dialog = $(dialogSelector);
      const container = dialog ? $(containerSelector, dialog) : null;
      if (!dialog || !container) return null;
      const rail = document.createElement("div");
      rail.className = "scroll-rail dialog-scroll-rail";
      rail.setAttribute("aria-hidden", "true");
      const thumb = document.createElement("span");
      thumb.className = "scroll-rail-thumb";
      rail.append(thumb);
      dialog.append(rail);
      const geo = { max: 0, thumb: 0, travel: 0 };
      const flow = { value: 0.5 };
      let lastTop = container.scrollTop;
      let measureFrame = 0;
      let updateFrame = 0;
      function update() {
        if (updateFrame) return;
        updateFrame = requestAnimationFrame(() => {
          updateFrame = 0;
          const top = container.scrollTop;
          nudgeFlow(flow, top - lastTop, rail);
          lastTop = top;
          const progress = geo.max > 1 ? clamp(top / geo.max, 0, 1) : 0;
          setVar(rail, "--rail-progress", progress.toFixed(4));
        });
      }
      function measure() {
        if (!dialog.open) {
          rail.classList.add("is-hidden");
          rail.classList.remove("is-active");
          return;
        }
        const hostRect = dialog.getBoundingClientRect();
        const rect = container.getBoundingClientRect();
        if (rect.height < 1 || rect.width < 1) {
          rail.classList.add("is-hidden");
          rail.classList.remove("is-active");
          return;
        }
        const track = Math.max(0, rect.height - 16);
        const scrollHeight = Math.max(container.scrollHeight, container.clientHeight, 1);
        const max = Math.max(0, container.scrollHeight - container.clientHeight);
        const thumbSize = max > 1 ? beamSize(track) : 0;
        geo.max = max;
        geo.thumb = thumbSize;
        geo.travel = Math.max(0, track - thumbSize);
        rail.style.top = `${Math.max(0, rect.top - hostRect.top)}px`;
        rail.style.height = `${rect.height}px`;
        rail.style.right = `${Math.max(0, hostRect.right - rect.right)}px`;
        setVar(rail, "--rail-size", `${thumbSize}px`);
        setVar(rail, "--rail-travel", `${geo.travel}px`);
        paintFlow(rail, flow);
        const active = max > 1 && thumbSize > 0;
        rail.classList.toggle("is-hidden", !active);
        rail.classList.toggle("is-active", active);
        lastTop = container.scrollTop;
        update();
      }
      function scheduleMeasure() {
        if (measureFrame) return;
        measureFrame = requestAnimationFrame(() => { measureFrame = 0; measure(); });
      }
      container.addEventListener("scroll", update, { passive: true });
      dialog.addEventListener("toggle", () => {
        scheduleMeasure();
        window.setTimeout(scheduleMeasure, 260);
      });
      if (window.ResizeObserver) {
        const observer = new ResizeObserver(scheduleMeasure);
        observer.observe(dialog);
        observer.observe(container);
        observers.push(observer);
      }
      if (window.MutationObserver) {
        const observer = new MutationObserver(scheduleMeasure);
        observer.observe(container, { childList: true, subtree: true, characterData: true });
        observers.push(observer);
      }
      bindRail(rail, geo, {
        pad: 8,
        getScroll: () => container.scrollTop,
        setScroll: value => { container.scrollTop = value; }
      });
      dialogRails.push({ measure: scheduleMeasure, rail, container });
      return rail;
    }

    [
      ["#sources-dialog", ".library-shell"],
      ["#inspection-dialog", ".inspection-dialog-shell"],
      ["#settings-dialog", ".sources-shell"],
      ["#scopes-dialog", ".sources-shell"],
      ["#text-viewer-dialog", ".text-viewer-content"],
      ["#iteration-manager-dialog", ".iteration-manager-list"],
      ["#iteration-dialog", ".dialog-shell"],
      ["#iteration-edit-dialog", ".dialog-shell"],
      ["#source-edit-dialog", ".source-edit-form"],
      ["#source-relocate-dialog", ".dialog-shell"],
      ["#work-hours-dialog", ".work-hours-form"]
    ].forEach(([dialog, container]) => setupDialogRail(dialog, container));

    paintTopbar(topbarP);

    window.addEventListener("scroll", scheduleScrollFrame, { passive: true });
    window.addEventListener("resize", scheduleMeasure, { passive: true });
    detail?.addEventListener("scroll", () => {
      if (detailFrame) return;
      detailFrame = requestAnimationFrame(() => { detailFrame = 0; updateDetailRail(); });
    }, { passive: true });
    dialog?.addEventListener("toggle", () => {
      scheduleMeasure();
      window.setTimeout(scheduleMeasure, 240);
    });
    dialog?.addEventListener("close", () => {
      detailRail?.classList.add("is-hidden");
      detailRail?.classList.remove("is-active");
    });

    if (window.ResizeObserver) {
      const observer = new ResizeObserver(scheduleMeasure);
      observer.observe(document.body);
      const shell = $(".shell");
      if (shell) observer.observe(shell);
      observers.push(observer);
      if (detail) {
        const detailObserver = new ResizeObserver(scheduleDetailMeasure);
        detailObserver.observe(detail);
        observers.push(detailObserver);
      }
    }
    if (detail && window.MutationObserver) {
      const mutationObserver = new MutationObserver(scheduleDetailMeasure);
      mutationObserver.observe(detail, { childList: true, subtree: true, attributes: true, characterData: true });
      observers.push(mutationObserver);
    }
    document.fonts?.ready?.then(scheduleMeasure);

    bindRail(pageRail, pageGeo, {
      pad: 12,
      getScroll: () => window.scrollY,
      setScroll: value => window.scrollTo(0, value)
    });
    bindRail(detailRail, detailGeo, {
      pad: 8,
      getScroll: () => detail?.scrollTop || 0,
      setScroll: value => { if (detail) detail.scrollTop = value; }
    });

    scrollChrome = {
      refresh: scheduleMeasure,
      refreshDetail: () => {
        scheduleDetailMeasure();
        window.setTimeout(scheduleDetailMeasure, 240);
      },
      observers
    };
    scheduleMeasure();
    return scrollChrome;
  }

  function renderCompletedGroups(group, completion) {
    const fold = $(group.done);
    fold.hidden = completion.outerFoldHidden;
    $(group.doneCount).textContent = String(completion.groups.reduce((count, item) => count + item.records.length, 0));
    const dateList = $(group.doneList);
    dateList.className = "completed-date-list";
    dateList.replaceChildren(...completion.groups.map(({ date, records: rows, open }) => {
      const section = document.createElement("details");
      section.className = "completion-date-group";
      section.dataset.preserveKey = `${group.key}-completed-${date}`;
      section.open = open;
      const allCancelled = rows.every(isCancelled), allCompleted = rows.every(isCompleted);
      const dateKind = allCancelled ? "取消日期" : allCompleted ? "完成日期" : "终结日期";
      const summary = document.createElement("summary");
      summary.textContent = date === "unknown" ? `${dateKind}未知 · ${rows.length}` : `${dateKind} ${date} · ${rows.length}`;
      const doneList = document.createElement("div");
      doneList.className = "record-list completed-list";
      populateMasonryList(doneList, rows.map(makeCard));
      section.append(summary, doneList);
      return section;
    }));
  }
  function makeWorkIterationBlock(item) {
    const iteration = item.iteration;
    const section = document.createElement("section");
    section.className = "iteration-work-block";
    section.dataset.iterationId = iteration.id;
    const titleId = `iteration-work-title-${cssSafe(iteration.id)}`;
    section.setAttribute("aria-labelledby", titleId);
    const head = document.createElement("header");
    head.className = "iteration-work-head";
    const copy = document.createElement("div");
    copy.className = "iteration-work-copy";
    const title = document.createElement("h3");
    title.className = "iteration-work-title";
    title.id = titleId;
    title.textContent = iterationName(iteration);
    const tag = model.iterationSecondaryTag(iteration);
    if (tag) title.title = `版本标识：${tag}`;
    copy.append(title, versionTypePill(iteration), iterationStatusPill(iteration));
    const meta = document.createElement("span");
    meta.className = "iteration-work-meta";
    const date = iteration.target_release_date ? ` · 计划发布 ${iteration.target_release_date}` : "";
    meta.textContent = `${item.records.length} 项 · 已完成 ${item.completed.length}${date}`;
    head.append(copy, meta);
    const blockList = document.createElement("div");
    blockList.className = "record-list iteration-work-list";
    populateMasonryList(blockList, item.visibleRecords.map(makeCard));
    section.append(head, blockList);
    if (item.isReleased && item.foldedRecords.length) {
      const fold = document.createElement("details");
      fold.className = "completed-fold iteration-inner-fold";
      fold.dataset.preserveKey = `iteration-completed-${iteration.id}`;
      const summary = document.createElement("summary");
      const label = document.createElement("span");
      label.textContent = "本版本已完成";
      const count = document.createElement("span");
      count.className = "fold-count";
      count.textContent = String(item.foldedRecords.length);
      const hint = document.createElement("span");
      hint.className = "summary-hint";
      hint.textContent = "展开查看";
      summary.append(label, count, hint);
      const doneList = document.createElement("div");
      doneList.className = "record-list completed-list";
      populateMasonryList(doneList, item.foldedRecords.map(makeCard));
      fold.append(summary, doneList);
      section.append(fold);
    }
    return section;
  }
  // Patrol-created requirements have no version yet. They keep the exact
  // version block/card language but are not attached to any version object.
  function makeUnassignedWorkBlock(item) {
    const section = document.createElement("section");
    section.className = "iteration-work-block is-unassigned";
    section.dataset.unassigned = "true";
    const titleId = "iteration-work-title-unassigned";
    section.setAttribute("aria-labelledby", titleId);
    const head = document.createElement("header");
    head.className = "iteration-work-head";
    const copy = document.createElement("div");
    copy.className = "iteration-work-copy";
    const title = document.createElement("h3");
    title.className = "iteration-work-title";
    title.id = titleId;
    title.textContent = "未指定版本";
    copy.append(title);
    const meta = document.createElement("span");
    meta.className = "iteration-work-meta";
    meta.textContent = `${item.records.length} 项 · 已完成 ${item.completed.length}`;
    head.append(copy, meta);
    const blockList = document.createElement("div");
    blockList.className = "record-list iteration-work-list";
    populateMasonryList(blockList, item.records.map(makeCard));
    section.append(head, blockList);
    return section;
  }
  function makeReleasedVersionBody(item) {
    const body = document.createElement("div");
    body.className = "record-list released-version-records";
    populateMasonryList(body, item.records.map(makeCard));
    return body;
  }
  function renderWorkReleasedVersions(groups) {
    const section = $("#work-released-section");
    const toggle = $("#work-released-toggle");
    const list = $("#work-released-list");
    const count = $("#work-released-count");
    const hint = $("#work-released-search-hint");
    if (!section || !toggle || !list || !count) return;
    section.hidden = groups.length === 0;
    count.textContent = String(groups.length);
    const searchActive = !!state.query.trim();
    const searchForced = searchActive && groups.length > 0;
    const open = groups.length > 0 && model.releasedEntryOpen(state.workReleasedOpen, searchActive, groups.length);
    if (hint) hint.hidden = !searchForced;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.classList.toggle("is-open", open);
    toggle.classList.toggle("is-search-forced", searchForced);
    toggle.title = searchForced ? "搜索命中已发布版本，已临时展开；清空搜索后恢复原状态" : "";
    list.hidden = !open;
    if (!open) {
      list.replaceChildren();
      return;
    }
    list.replaceChildren(...groups.map(item => {
      const details = document.createElement("details");
      details.className = "released-version-card";
      details.dataset.iterationId = item.iteration.id;
      details.dataset.preserveKey = `work-released-${item.iteration.id}`;
      details.open = model.releasedVersionOpen(state.workReleasedExpanded.has(item.iteration.id), searchActive, true);
      const summary = document.createElement("summary");
      const name = document.createElement("span");
      name.className = "released-version-name";
      name.textContent = iterationName(item.iteration);
      const meta = document.createElement("span");
      meta.className = "released-version-meta";
      meta.textContent = `${item.records.length} 项`;
      summary.append(name, versionTypePill(item.iteration), iterationStatusPill(item.iteration), meta);
      details._releasedGroup = item;
      // Build cards only when the version is actually open. Closed versions
      // keep just their summary until first expansion.
      details.append(summary);
      if (details.open) details.append(makeReleasedVersionBody(item));
      return details;
    }));
  }
  function toggleWorkReleased() {
    // While search results force the list open, the click must not hide the
    // matched cards; the manual state is restored after clearing the search.
    if (state.query.trim() && (state.workReleasedGroups || []).length) return;
    state.workReleasedOpen = !state.workReleasedOpen;
    renderWorkReleasedVersions(state.workReleasedGroups || []);
  }
  function renderGroup(group) {
    const records = sortRecords(groupRecords(group));
    const unfinished = records.filter(record => !isTerminal(record));
    const completed = records.filter(isTerminal);
    const list = $(group.list);
    $(group.count).textContent = `${records.length} 项`;
    if (group.key === "work") {
      const iterContainer = $("#work-iteration-container");
      const iterations = orderedIterations();

      if (model.partitionIterationRecords) {
        const partition = model.partitionIterationRecords(records, iterations);
        const blocks = partition.activeGroups
          .filter(item => item.records.length)
          .map(item => makeWorkIterationBlock(item));
        // The unassigned group is intentionally not a version: it never enters
        // the active/released groups and disappears once every record is filed.
        if (partition.unassigned?.records.length) blocks.push(makeUnassignedWorkBlock(partition.unassigned));
        if (iterContainer) {
          iterContainer.replaceChildren(...blocks);
          iterContainer.hidden = blocks.length === 0;
        }
        state.workReleasedGroups = partition.releasedGroups.filter(item => item.records.length);
        renderWorkReleasedVersions(state.workReleasedGroups);

        const completion = model.partitionTerminalRecords(partition.backlog.completed);
        populateMasonryList(list, [...partition.backlog.unfinished, ...completion.todayRecords].map(makeCard));
        $(group.empty).hidden = blocks.length > 0 || state.workReleasedGroups.length > 0 || partition.backlog.unfinished.length > 0 || completion.todayRecords.length > 0;
        renderCompletedGroups(group, completion);
      } else {
        if (iterContainer) iterContainer.hidden = true;
        state.workReleasedGroups = [];
        renderWorkReleasedVersions([]);
        const completion = model.partitionTerminalRecords(completed);
        populateMasonryList(list, [...unfinished, ...completion.todayRecords].map(makeCard));
        $(group.empty).hidden = unfinished.length > 0 || completion.todayRecords.length > 0;
        renderCompletedGroups(group, completion);
      }
    } else if (group.key === "bug") {
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
    // Delegated tasks keep their original batch model. Versions never apply here.
    const batches = Array.isArray(state.data?.batches) ? state.data.batches : [];
    const currentBatchId = state.data?.current_batch_id || batches.find(batch => batch.state === "current")?.id || null;
    const partition = model.partitionTaskBatches(allTasks, batches, currentBatchId, state.data?.legacy_task_ids || []);
    const { current, currentTasks, currentBatchTasks, legacyOpen, legacyDone, pastBatches, unlisted, unbatchedUnknown } = partition;

    $("#task-count").textContent = `${allTasks.length} 项`;
    const currentSection = $("#task-current-section");
    currentSection.hidden = !currentBatchId || currentTasks.length === 0;
    const currentEnded = currentBatchTasks.length > 0 && currentBatchTasks.every(isTerminal);
    const currentComplete = currentEnded && currentBatchTasks.every(isCompleted);
    $("#task-current-state").textContent = current ? (currentComplete ? "已完成" : currentEnded ? "已结束" : "正在处理") : "状态同步中";
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
      summary.setAttribute("aria-label", `当前现状：${record.status_line ? txt(record.status_line) : record.latest_brief ? txt(record.latest_brief) : cardBrief(record)}`);
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
      const cachedWorkflow = state.workflows.get(selected.id);
      // 详情打开时若流程接口曾返回 unavailable，或记录 revision 已变化，必须丢弃缓存重取；
      // 否则外部 CLI 启动/推进流水线后，已打开的详情会一直显示“未启用标准流程”。
      if (cachedWorkflow && (cachedWorkflow.unavailable || cachedWorkflow.record_revision === undefined ||
          String(cachedWorkflow.record_revision) !== String(selected.revision))) {
        state.workflows.delete(selected.id);
      }
      renderDetail(selected); detail.scrollTop = detailScroll;
      if (detailFocus) $(`[data-dialog-focus="${CSS.escape(detailFocus)}"]`, detail)?.focus({ preventScroll: true });
      loadItemWorkflow(selected);
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
    const scrollX = window.scrollX, scrollY = window.scrollY;
    let selector = null;
    for (const attribute of ["data-open-record", "data-complete-id"]) {
      const value = active?.getAttribute?.(attribute);
      if (value) { selector = `[${attribute}="${CSS.escape(value)}"]`; break; }
    }
    render();
    if (window.scrollX !== scrollX || window.scrollY !== scrollY) window.scrollTo(scrollX, scrollY);
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
  const cssSafe = value => String(value || "").replace(/[^a-z0-9_-]/gi, "_");
  const workflowPanelId = (recordId, stageId) => `workflow-panel-${cssSafe(recordId)}-${cssSafe(stageId)}`;
  function workflowGlyph(status) { return ({ done: "✓", active: "●", pending: "○", blocked: "⚠", rework: "↺" })[status] || "○"; }
  const evidenceTextFile = /\.(?:md|markdown|txt|log|json|jsonl|diff|patch|py|js|mjs|ts|tsx|jsx|css|html|htm|sh|csv|ya?ml|xml|ini|toml)$/i;
  function evidenceIsUrl(value) { return /^https?:\/\//i.test(String(value || "").trim()); }
  function evidenceIsLocalPath(value) {
    const text = String(value || "").trim();
    if (!text || evidenceIsUrl(text) || text.includes("\n")) return false;
    if (/^file:\/\//i.test(text)) return true;
    if (/^(?:~\/|\/|\.{1,2}\/)/.test(text)) return true;
    if (/^[A-Za-z]:[\\/]/.test(text)) return true;
    return evidenceTextFile.test(text);
  }
  function makeWorkflowEvidenceList(recordId, entries) {
    if (!Array.isArray(entries) || !entries.length) return null;
    const list = document.createElement("ul");
    list.className = "workflow-evidence";
    list.setAttribute("aria-label", "证据");
    entries.forEach(value => {
      const text = String(value);
      const item = document.createElement("li");
      if (evidenceIsUrl(text)) {
        item.classList.add("is-link");
        const link = document.createElement("a");
        link.href = text; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = text;
        item.append(link);
      } else if (evidenceIsLocalPath(text)) {
        item.classList.add("is-file");
        const button = document.createElement("button");
        button.type = "button"; button.className = "workflow-evidence-file";
        button.dataset.evidenceRecord = recordId; button.dataset.evidencePath = text;
        button.title = "查看证据全文";
        const label = document.createElement("span"); label.className = "workflow-evidence-path"; label.textContent = text;
        const open = document.createElement("span"); open.className = "workflow-evidence-open"; open.textContent = "查看全文";
        button.append(label, open); item.append(button);
      } else {
        item.textContent = text;
      }
      list.append(item);
    });
    return list;
  }
  function workflowOutputText(value) {
    if (value === undefined || value === null) return "";
    if (typeof value === "string") return value.trim();
    if (Array.isArray(value)) return value.map(workflowOutputText).filter(Boolean).join("\n");
    if (typeof value === "object") {
      try { return JSON.stringify(value, null, 2); } catch (_) { return ""; }
    }
    return String(value);
  }
  // 宽表格沿用自绘滚动条的语言：一条横向光束跟随表格的横向滚动。
  function enhanceTableScrollers(root) {
    if (!root?.querySelectorAll) return;
    root.querySelectorAll(".markdown-table-frame").forEach(frame => {
      if (frame.dataset.tableRailReady) return;
      const wrap = frame.querySelector(".markdown-table-wrap");
      if (!wrap) return;
      frame.dataset.tableRailReady = "true";
      const rail = document.createElement("div");
      rail.className = "table-scroll-rail";
      rail.setAttribute("aria-hidden", "true");
      const thumb = document.createElement("span");
      thumb.className = "table-scroll-thumb";
      rail.append(thumb);
      frame.append(rail);
      let max = 0, size = 0, travel = 0, paintFrame = 0;
      const paint = () => {
        const progress = max > 1 ? Math.min(1, Math.max(0, wrap.scrollLeft / max)) : 0;
        rail.style.setProperty("--table-progress", progress.toFixed(4));
      };
      const measure = () => {
        if (wrap.clientWidth < 1) { max = 0; rail.classList.remove("is-active"); return; }
        const track = Math.max(1, wrap.clientWidth);
        max = Math.max(0, wrap.scrollWidth - wrap.clientWidth);
        size = max > 1 ? Math.max(0, Math.min(track - 10, Math.max(72, track * 0.56))) : 0;
        travel = Math.max(0, track - size);
        rail.style.setProperty("--table-size", `${size}px`);
        rail.style.setProperty("--table-travel", `${travel}px`);
        rail.classList.toggle("is-active", max > 1);
        paint();
      };
      const schedulePaint = () => {
        if (paintFrame) return;
        paintFrame = requestAnimationFrame(() => { paintFrame = 0; paint(); });
      };
      let drag = null, dragFrame = 0, pendingLeft = 0;
      const applyDrag = () => {
        dragFrame = 0;
        wrap.scrollLeft = pendingLeft;
        paint();
      };
      rail.addEventListener("pointerdown", event => {
        if (max <= 1 || (event.button !== undefined && event.button !== 0)) return;
        const rect = rail.getBoundingClientRect();
        const thumbLeft = rect.left + (max > 1 ? (wrap.scrollLeft / max) * travel : 0);
        const inThumb = event.clientX >= thumbLeft && event.clientX <= thumbLeft + size;
        let startScroll = wrap.scrollLeft;
        if (!inThumb) {
          const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left - size / 2) / Math.max(1, travel)));
          startScroll = ratio * max;
          wrap.scrollLeft = startScroll;
          paint();
        }
        drag = { startX: event.clientX, startScroll };
        rail.classList.add("is-dragging");
        try { rail.setPointerCapture(event.pointerId); } catch (_) {}
        event.preventDefault();
      }, { passive: false });
      rail.addEventListener("pointermove", event => {
        if (!drag) return;
        pendingLeft = drag.startScroll + ((event.clientX - drag.startX) / Math.max(1, travel)) * max;
        if (!dragFrame) dragFrame = requestAnimationFrame(applyDrag);
        event.preventDefault();
      }, { passive: false });
      const end = event => {
        if (!drag) return;
        drag = null;
        rail.classList.remove("is-dragging");
        try { if (rail.hasPointerCapture?.(event.pointerId)) rail.releasePointerCapture(event.pointerId); } catch (_) {}
      };
      rail.addEventListener("pointerup", end);
      rail.addEventListener("pointercancel", end);
      rail.addEventListener("lostpointercapture", end);
      wrap.addEventListener("scroll", schedulePaint, { passive: true });
      if (window.ResizeObserver) new ResizeObserver(measure).observe(wrap);
      measure();
    });
  }
  function openTextViewer({ kicker = "全文", title = "查看全文", meta = "", text = "", note = "", copyText = null, copyLabel = "复制全文", markdown = false } = {}) {
    state.textViewerText = String(text ?? "");
    state.textViewerCopyText = copyText === null ? state.textViewerText : String(copyText ?? "");
    state.textViewerCopyLabel = copyLabel || "复制全文";
    $("#text-viewer-kicker").textContent = kicker;
    $("#text-viewer-title").textContent = title;
    $("#text-viewer-meta").textContent = meta;
    const content = $("#text-viewer-content");
    const renderMarkdown = markdown || (typeof window.markdownHasTable === "function" && window.markdownHasTable(state.textViewerText));
    content.classList.toggle("is-markdown", renderMarkdown);
    content.classList.toggle("is-plain", !renderMarkdown);
    if (renderMarkdown && typeof window.renderSafeMarkdown === "function") {
      content.replaceChildren();
      window.renderSafeMarkdown(content, state.textViewerText);
      enhanceTableScrollers(content);
    } else {
      content.textContent = state.textViewerText;
    }
    $("#text-viewer-state").textContent = note;
    const copy = $("#text-viewer-copy");
    if (copy) copy.textContent = state.textViewerCopyLabel;
    if (!$("#text-viewer-dialog").open) $("#text-viewer-dialog").showModal();
    $("#text-viewer-content").focus({ preventScroll: true });
  }
  async function fetchEvidenceText(recordId, evidencePath) {
    const response = await fetch(`/api/items/${encodeURIComponent(recordId)}/evidence-file?path=${encodeURIComponent(evidencePath)}`, { cache: "no-store" });
    if (response.status === 404) {
      const error = new Error("evidence_not_found");
      error.code = "missing";
      throw error;
    }
    const contentType = response.headers.get("Content-Type") || "";
    if (!response.ok || !contentType.startsWith("text/plain")) {
      const error = new Error(`HTTP ${response.status}`);
      error.code = "unreadable";
      throw error;
    }
    return response.text();
  }
  async function openLocalFileViewer(recordId, evidencePath, options = {}) {
    const path = String(evidencePath || "");
    const name = path.split(/[\\/]/).filter(Boolean).pop() || "本地文件";
    const kicker = options.kicker || "证据全文";
    const title = options.title || name;
    const meta = options.meta || path;
    openTextViewer({
      kicker, title, meta, text: "正在读取…", note: "正在读取本地报告文件…",
      copyText: path, copyLabel: "复制路径"
    });
    try {
      const text = await fetchEvidenceText(recordId, path);
      openTextViewer({
        kicker, title, meta: `${meta} · ${text.length} 字符`, text,
        note: "只读 · 已读取本地报告文件。",
        markdown: /\.(?:md|markdown)$/i.test(path)
      });
    } catch (error) {
      const missing = error.code === "missing";
      const message = missing
        ? `报告文件已不存在（原路径：${path}）`
        : `暂时无法读取报告文件（${error.message || "读取失败"}）。\n原路径：${path}`;
      openTextViewer({
        kicker, title, meta, text: message, markdown: false,
        note: missing ? "报告文件已不存在；可复制原路径后人工查找。" : "读取失败；可复制原路径后重试。",
        copyText: path, copyLabel: "复制路径"
      });
    }
  }
  function legacyCopyText(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    Object.assign(area.style, { position: "fixed", top: "0", left: "0", width: "1px", height: "1px", opacity: "0" });
    document.body.append(area);
    area.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (_) {}
    area.remove();
    return copied;
  }
  async function copyTextViewerText() {
    const button = $("#text-viewer-copy");
    const label = state.textViewerCopyLabel || "复制全文";
    try {
      const copyText = state.textViewerCopyText || state.textViewerText;
      let copied = false;
      if (navigator.clipboard?.writeText) {
        try { await navigator.clipboard.writeText(copyText); copied = true; } catch (_) {}
      }
      if (!copied) copied = legacyCopyText(copyText);
      if (!copied) throw new Error("copy_failed");
      button.textContent = "已复制";
      window.setTimeout(() => { button.textContent = label; }, 1600);
    } catch (_) {
      $("#text-viewer-state").textContent = "复制失败，请手动选择文本复制。";
    }
  }
  // 审计阶段只保留一组行：状态、名称、负责角色与「查看全文」在同一行，
  // 取代原先重复的子步骤清单、阶段输出清单与报告/回应清单。
  const workflowAuditOutputKeys = ["audit_report", "dev_response"];
  const workflowAuditSubstepIds = { audit_report: "audit", dev_response: "response" };
  const workflowAuditRoleFallbacks = { audit_report: "Auditor", dev_response: "Dev" };
  function makeWorkflowAuditRows(record, step) {
    const gateByKey = new Map((step.checklist || []).map(gate => [gate.key, gate]));
    const box = document.createElement("div");
    box.className = "workflow-audit-box";
    const title = document.createElement("h4"); title.textContent = "审计报告与开发回应";
    const list = document.createElement("ul");
    list.className = "workflow-audit-list";
    list.setAttribute("aria-label", "审计报告与开发回应");
    box.append(title, list);
    workflowAuditOutputKeys.forEach(key => {
      const gate = gateByKey.get(key) || null;
      const sub = (step.substeps || []).find(item => item.id === workflowAuditSubstepIds[key]) || null;
      const name = gate?.label || model.workflowOutputLabel(key);
      // 子步骤视图不携带 output 字段，按固定 4a/4b 语义取角色；缺失时用门禁角色。
      const role = sub?.roleLabel || workflowAuditRoleFallbacks[key] || step.roleLabel || step.actor || "未指定";
      const text = workflowOutputText(step.outputs?.[key]);
      const done = gate ? gate.done : sub ? sub.status === "done" : text ? true : null;
      const state = done === null ? "未取得" : done ? "已完成" : "缺失";
      const mark = done === null ? "·" : done ? "✓" : "✗";
      const row = document.createElement("li");
      row.className = "workflow-audit-row";
      row.dataset.output = key;
      row.dataset.done = done === null ? "unknown" : String(!!done);
      const open = document.createElement("button");
      open.type = "button"; open.className = "workflow-audit-open"; open.disabled = !text;
      open.setAttribute("aria-label", text ? `查看${name}全文（${role}，${state}）` : `${name}（${role}，${state}），暂无全文`);
      const markEl = document.createElement("span"); markEl.className = "workflow-audit-mark"; markEl.setAttribute("aria-hidden", "true"); markEl.textContent = mark;
      const copy = document.createElement("span"); copy.className = "workflow-audit-copy";
      const nameEl = document.createElement("span"); nameEl.className = "workflow-audit-name"; nameEl.textContent = name;
      const roleEl = document.createElement("span"); roleEl.className = "workflow-audit-role"; roleEl.textContent = role;
      copy.append(nameEl, roleEl);
      const stateEl = document.createElement("span"); stateEl.className = "workflow-audit-state"; stateEl.textContent = state;
      const linkEl = document.createElement("span"); linkEl.className = "workflow-audit-link"; linkEl.textContent = "查看全文";
      open.append(markEl, copy, stateEl, linkEl);
      if (text) {
        const isLocalPath = evidenceIsLocalPath(text) && !/[\r\n]/.test(text);
        open.addEventListener("click", () => {
          if (isLocalPath) {
            openLocalFileViewer(record.id, text, {
              kicker: "阶段输出", title: name,
              meta: `${recordDisplayId(record)} · ${step.label}`
            });
            return;
          }
          openTextViewer({
            kicker: "阶段输出", title: name, meta: `${recordDisplayId(record)} · ${step.label}`,
            text, note: "只读 · 阶段输出全文", markdown: true
          });
        });
      } else {
        open.title = `暂无${name}全文`;
      }
      row.append(open);
      list.append(row);
    });
    return box;
  }

  function makeWorkflowSection(record) {
    const section = document.createElement("section");
    section.className = "detail-workflow";
    section.dataset.workflowSection = record.id;
    section.setAttribute("aria-labelledby", `workflow-title-${cssSafe(record.id)}`);
    if (!state.workflowEntered.has(record.id)) { state.workflowEntered.add(record.id); section.classList.add("workflow-enter"); }
    renderWorkflowInto(section, record);
    return section;
  }
  function workflowRetryButton(record) {
    const button = document.createElement("button");
    button.type = "button"; button.className = "workflow-retry"; button.dataset.workflowRetry = record.id; button.textContent = "重试读取";
    return button;
  }
  function renderWorkflowInto(section, record) {
    const detail = $("#detail");
    const scrollTop = detail ? detail.scrollTop : 0;
    const focusedStage = document.activeElement?.dataset?.workflowStage || "";
    const view = model.workflowView(record, state.workflows.get(record.id) || null);
    const nodes = [];
    const header = document.createElement("div"); header.className = "workflow-head";
    const title = document.createElement("h3"); title.id = `workflow-title-${cssSafe(record.id)}`; title.textContent = "流水线";
    header.append(title);
    if (view.enabled) {
      const meta = document.createElement("span"); meta.className = "workflow-head-meta";
      meta.textContent = `第 ${view.stageIndex || 1} / ${view.stageCount} 步 · ${view.statusLabel}`;
      header.append(meta);
      if (view.reworkCount > 0) { const badge = document.createElement("span"); badge.className = "workflow-rework-badge"; badge.textContent = `退回 ×${view.reworkCount}`; header.append(badge); }
    }
    nodes.push(header);
    if (!view.enabled) {
      const empty = document.createElement("div"); empty.className = "workflow-disabled";
      const emptyTitle = document.createElement("p"); emptyTitle.className = "workflow-disabled-title"; emptyTitle.textContent = "未启用标准流程";
      const hint = document.createElement("p"); hint.className = "workflow-disabled-hint";
      hint.textContent = view.detailState === "unavailable"
        ? "流程接口暂时不可用，无法确认是否已启用，可稍后重试。"
        : "这条记录创建于流水线启用之前。启用后从“接单/立项”开始，由 Lead 决定后续推进，不会自动填写内容。";
      const start = document.createElement("button"); start.type = "button"; start.className = "workflow-start"; start.dataset.workflowStart = record.id;
      const busy = state.workflowBusy.has(record.id);
      start.textContent = busy ? "启用中…" : "启用标准流程"; start.disabled = busy;
      empty.append(emptyTitle, hint, start);
      if (view.detailState === "unavailable") empty.append(workflowRetryButton(record));
      nodes.push(empty);
      section.dataset.state = "disabled";
    } else {
      const selectedStage = state.workflowStage.get(record.id) || view.stageId;
      const list = document.createElement("ol"); list.className = "workflow-steps"; list.setAttribute("aria-label", "六阶段流水线");
      view.steps.forEach((step, index) => {
        const item = document.createElement("li");
        item.className = "workflow-step"; item.dataset.status = step.status; item.dataset.stage = step.id;
        item.style.setProperty("--workflow-delay", String(index));
        if (step.current) item.setAttribute("aria-current", "step");
        const button = document.createElement("button"); button.type = "button"; button.className = "workflow-step-button";
        button.dataset.workflowStage = step.id; button.dataset.dialogFocus = `workflow-${cssSafe(step.id)}`;
        button.setAttribute("aria-expanded", String(step.id === selectedStage));
        button.setAttribute("aria-controls", workflowPanelId(record.id, step.id));
        button.setAttribute("aria-label", `第 ${step.index} 步 ${step.label}，负责人 ${step.owner}，${step.statusLabel}${step.current ? "，当前阶段" : ""}`);
        const marker = document.createElement("span"); marker.className = "workflow-step-marker"; marker.setAttribute("aria-hidden", "true");
        const markerIndex = document.createElement("span"); markerIndex.className = "workflow-step-index"; markerIndex.textContent = String(step.index); marker.append(markerIndex);
        const copy = document.createElement("span"); copy.className = "workflow-step-copy";
        const label = document.createElement("span"); label.className = "workflow-step-label"; label.textContent = step.short || step.label;
        const owner = document.createElement("span"); owner.className = "workflow-step-owner"; owner.textContent = step.owner; owner.title = step.owner;
        const stateRow = document.createElement("span"); stateRow.className = "workflow-step-state"; stateRow.dataset.status = step.status;
        const glyph = document.createElement("span"); glyph.className = "workflow-step-glyph"; glyph.setAttribute("aria-hidden", "true"); glyph.textContent = workflowGlyph(step.status);
        const stateText = document.createElement("span"); stateText.textContent = step.statusLabel;
        stateRow.append(glyph, stateText);
        copy.append(label, owner, stateRow);
        button.append(marker, copy);
        item.append(button);
        list.append(item);
      });
      nodes.push(list);
      if (view.detailState === "pending") {
        const loading = document.createElement("p"); loading.className = "workflow-loading"; loading.setAttribute("role", "status");
        loading.textContent = "正在读取流程详情…"; nodes.push(loading);
      } else if (view.detailState === "unavailable") {
        const failed = document.createElement("p"); failed.className = "workflow-loading is-error"; failed.setAttribute("role", "status");
        failed.textContent = "流程详情暂时读取失败，仅显示列表摘要。"; failed.append(workflowRetryButton(record)); nodes.push(failed);
      }
      const panels = document.createElement("div"); panels.className = "workflow-panels";
      view.steps.forEach(step => panels.append(makeWorkflowStagePanel(record, step, step.id === selectedStage)));
      nodes.push(panels);
      if (view.history.length) nodes.push(makeWorkflowHistory(record, view));
      section.dataset.state = view.stageStatus || "unknown";
    }
    section.replaceChildren(...nodes);
    if (detail && detail.contains(section)) detail.scrollTop = scrollTop;
    if (focusedStage) $(`[data-workflow-stage="${CSS.escape(focusedStage)}"]`, section)?.focus({ preventScroll: true });
  }
  function makeWorkflowStagePanel(record, step, open) {
    const panel = document.createElement("div");
    panel.className = "workflow-stage-panel"; panel.id = workflowPanelId(record.id, step.id); panel.dataset.stage = step.id; panel.hidden = !open;
    const meta = document.createElement("p"); meta.className = "workflow-stage-meta";
    const addMeta = (name, value) => {
      const item = document.createElement("span"); item.className = "workflow-meta-item";
      const key = document.createElement("span"); key.textContent = name;
      const strong = document.createElement("strong"); strong.textContent = value;
      item.append(key, strong); meta.append(item);
    };
    addMeta(step.current ? "当前阶段" : "阶段", step.label);
    addMeta("负责人", step.owner);
    addMeta("下一步", step.nextLabel);
    if (!step.current) addMeta("状态", step.statusLabel);
    panel.append(meta);
    if (step.current) {
      const lockRow = document.createElement("div"); lockRow.className = "workflow-lock";
      const label = document.createElement("span"); label.className = "workflow-lock-label";
      const detail = document.createElement("span"); detail.className = "workflow-lock-detail";
      if (step.lock) {
        lockRow.dataset.state = step.lock.expired ? "expired" : "locked";
        label.textContent = step.lock.expired ? "锁已过期" : "已锁定";
        detail.textContent = step.lock.expired
          ? `${step.lock.actor || "未知"} · ${step.lock.roleLabel} · 可接管`
          : `${step.lock.actor || "未知"} · ${step.lock.roleLabel} · 剩余 ${step.lock.remainingMinutes} 分钟`;
        lockRow.title = step.lock.expiresAt ? `锁定到期：${step.lock.expiresAt}` : "";
      } else {
        lockRow.dataset.state = "unlocked";
        label.textContent = "未锁定";
        detail.textContent = `等待 ${step.owner} 接手`;
      }
      lockRow.append(label, detail); panel.append(lockRow);
    }
    // 审计阶段把子步骤、阶段输出与报告/回应并入同一组行；其余阶段保持原样。
    if (step.id === "audit_response") {
      const auditRows = makeWorkflowAuditRows(record, step);
      if (auditRows) panel.append(auditRows);
    } else {
      if (step.substeps.length) {
        const subBox = document.createElement("div"); subBox.className = "workflow-stage-substeps-box";
        const subTitle = document.createElement("h4"); subTitle.textContent = "子步骤";
        const subList = document.createElement("ul"); subList.className = "workflow-stage-substeps";
        subList.setAttribute("aria-label", `${step.label}子步骤`);
        step.substeps.forEach(sub => {
          const subItem = document.createElement("li"); subItem.dataset.status = sub.status;
          const subIndex = document.createElement("span"); subIndex.className = "workflow-substep-index"; subIndex.textContent = sub.indexLabel;
          const subLabel = document.createElement("span"); subLabel.className = "workflow-substep-label"; subLabel.textContent = sub.label;
          const subRole = document.createElement("span"); subRole.className = "workflow-substep-role"; subRole.textContent = sub.roleLabel;
          const subState = document.createElement("span"); subState.className = "workflow-substep-state"; subState.textContent = sub.statusLabel;
          subItem.append(subIndex, subLabel, subRole, subState);
          subList.append(subItem);
        });
        subBox.append(subTitle, subList); panel.append(subBox);
      }
      const gates = document.createElement("div"); gates.className = "workflow-gates-box";
      const gatesTitle = document.createElement("h4"); gatesTitle.textContent = "阶段输出";
      const gateList = document.createElement("ul"); gateList.className = "workflow-gates"; gateList.setAttribute("aria-label", `${step.label}阶段输出检查`);
      if (!step.checklist.length) {
        const item = document.createElement("li"); item.className = "workflow-gate"; item.dataset.done = "unknown";
        const mark = document.createElement("span"); mark.className = "workflow-gate-mark"; mark.setAttribute("aria-hidden", "true"); mark.textContent = "·";
        const name = document.createElement("span"); name.className = "workflow-gate-label"; name.textContent = "该阶段未定义输出要求";
        item.append(mark, name); gateList.append(item);
      }
      step.checklist.forEach(gate => {
        const item = document.createElement("li"); item.className = "workflow-gate"; item.dataset.done = gate.done === null ? "unknown" : String(gate.done);
        const mark = document.createElement("span"); mark.className = "workflow-gate-mark"; mark.setAttribute("aria-hidden", "true"); mark.textContent = gate.done === null ? "·" : gate.done ? "✓" : "✗";
        const name = document.createElement("span"); name.className = "workflow-gate-label"; name.textContent = gate.label;
        const stateText = document.createElement("span"); stateText.className = "workflow-gate-state"; stateText.textContent = gate.done === null ? "未取得" : gate.done ? "已完成" : "缺失";
        item.append(mark, name, stateText); gateList.append(item);
      });
      gates.append(gatesTitle, gateList); panel.append(gates);
    }
    const note = document.createElement("div"); note.className = "workflow-note";
    const noteLabel = document.createElement("span"); noteLabel.className = "workflow-note-label"; noteLabel.textContent = "最近记录";
    const noteText = document.createElement("p"); noteText.className = "workflow-note-text";
    noteText.textContent = step.note || (step.at || step.actor ? "该阶段暂无文字说明。" : "尚未记录阶段说明。");
    note.append(noteLabel, noteText);
    if (step.note && (step.actor || step.at || step.roleLabel)) {
      const who = document.createElement("span"); who.className = "workflow-note-meta";
      who.textContent = [step.actor, step.roleLabel, step.at ? dateLabel(step.at) : ""].filter(Boolean).join(" · ");
      note.append(who);
    }
    const evidence = makeWorkflowEvidenceList(record.id, step.evidence);
    if (evidence) note.append(evidence);
    panel.append(note);
    return panel;
  }
  function makeWorkflowHistory(record, view) {
    const fold = document.createElement("details"); fold.className = "detail-history-fold workflow-history"; fold.dataset.preserveKey = `workflow-history-${record.id}`;
    const summary = document.createElement("summary"); summary.textContent = `阶段历史（${view.history.length}）`; summary.dataset.dialogFocus = "workflow-history";
    const body = document.createElement("div"); body.className = "detail-history-body";
    const list = document.createElement("ol"); list.className = "workflow-history-list";
    [...view.history].reverse().forEach(item => {
      const row = document.createElement("li"); row.className = "workflow-history-row"; row.dataset.action = item.action;
      const head = document.createElement("p"); head.className = "workflow-history-head";
      const time = document.createElement("time"); time.className = "workflow-history-time"; time.textContent = dateLabel(item.at, "时间未记录"); if (item.at) time.dateTime = item.at;
      const action = document.createElement("span"); action.className = "workflow-history-action"; action.textContent = item.actionLabel;
      const who = document.createElement("span"); who.className = "workflow-history-who"; who.textContent = [item.actor, item.roleLabel, item.stageLabel].filter(Boolean).join(" · ");
      head.append(time, action, who); row.append(head);
      if (item.note) { const note = document.createElement("p"); note.className = "workflow-history-note"; note.textContent = item.note; row.append(note); }
      const evidence = makeWorkflowEvidenceList(record.id, item.evidence);
      if (evidence) row.append(evidence);
      list.append(row);
    });
    body.append(list); fold.append(summary, body);
    return fold;
  }
  function selectWorkflowStage(button) {
    const section = button.closest("[data-workflow-section]");
    if (!section) return;
    const recordId = section.dataset.workflowSection, stageId = button.dataset.workflowStage;
    state.workflowStage.set(recordId, stageId);
    $$("[data-workflow-stage]", section).forEach(node => node.setAttribute("aria-expanded", String(node.dataset.workflowStage === stageId)));
    $$(".workflow-stage-panel", section).forEach(panel => { panel.hidden = panel.dataset.stage !== stageId; });
  }
  async function loadItemWorkflow(record) {
    const id = record.id;
    if (state.workflows.has(id)) return null;
    if (state.workflowRequests.has(id)) return state.workflowRequests.get(id);
    const operation = (async () => {
      let payload = null;
      try {
        const response = await fetch(`/api/items/${encodeURIComponent(id)}/workflow`, { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        payload = await response.json();
        if (!payload || typeof payload !== "object") throw new Error("invalid_payload");
      } catch (_) {
        payload = { unavailable: true };
      }
      state.workflows.set(id, payload);
      state.workflowRequests.delete(id);
      const current = findRecord(id) || record;
      const section = $(`[data-workflow-section="${CSS.escape(id)}"]`, $("#detail"));
      if (section && state.selectedId === id && $("#record-dialog").open) renderWorkflowInto(section, current);
      return payload;
    })();
    state.workflowRequests.set(id, operation);
    return operation;
  }
  function retryWorkflow(id) {
    state.workflows.delete(id);
    const record = findRecord(id);
    const section = $(`[data-workflow-section="${CSS.escape(id)}"]`, $("#detail"));
    if (record && section) renderWorkflowInto(section, record);
    if (record) loadItemWorkflow(record);
  }
  async function startWorkflow(button) {
    const id = button.dataset.workflowStart, record = findRecord(id);
    if (!record || state.workflowBusy.has(id)) return;
    state.workflowBusy.add(id); button.disabled = true; button.textContent = "启用中…";
    try {
      const response = await fetch(`/api/items/${encodeURIComponent(id)}/workflow/start`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_id: record.type === "bug" ? "bug-standard" : "dev-standard", actor_role: "lead", actor: "user", expected_revision: record.revision })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const errors = { record_not_found: "找不到该事项", workflow_terminal: "流程已结束", invalid_role: "当前角色不能启动流程", revision_conflict: "事项已更新，请刷新后重试" };
        throw new Error(payload.message || errors[payload.error] || (response.status === 404 || response.status === 405 || response.status === 501 ? "当前服务版本还不支持流水线接口" : `启用失败（${payload.error || response.status}）`));
      }
      if (payload.record && typeof payload.record === "object") Object.assign(record, payload.record);
      if (payload.workflow && typeof payload.workflow === "object") state.workflows.set(id, { workflow: payload.workflow, template: payload.template || null });
      else state.workflows.delete(id);
      state.workflowStage.delete(id);
      showToast("已启用标准流程");
      await refresh();
      const current = findRecord(id) || record;
      const section = $(`[data-workflow-section="${CSS.escape(id)}"]`, $("#detail"));
      if (section && state.selectedId === id) renderWorkflowInto(section, current);
      if (!state.workflows.has(id)) await loadItemWorkflow(current);
    } catch (error) {
      showToast(error.message || "启用失败，请重试", "error");
      if (button.isConnected) { button.disabled = false; button.textContent = "启用标准流程"; }
    } finally { state.workflowBusy.delete(id); }
  }

  function renderDetail(record) {
    const detail = $("#detail"); detail.replaceChildren();
    const head = document.createElement("div"); head.className = "detail-head";
    const title = document.createElement("h2"); title.id = "dialog-title"; title.textContent = record.title;
    if (isCancelled(record)) title.classList.add("record-cancelled-title");
    const pill = document.createElement("span"); pill.className = `status-pill ${escClass(effectiveStatus(record))}`; pill.textContent = statusLabel(effectiveStatus(record));
    const idButton = document.createElement("button"); idButton.type = "button"; idButton.className = "detail-id-copy"; idButton.textContent = recordDisplayId(record); idButton.title = "点击复制事项 ID";
    idButton.addEventListener("click", async () => { try { await navigator.clipboard.writeText(record.id); showToast("ID 已复制"); } catch (_) { showToast("复制失败，请手动复制", "error"); } });
    head.append(title, pill); detail.append(idButton, head);
    if (record.type !== "task") {
      const iterAssignWrap = document.createElement("div");
      iterAssignWrap.className = "detail-iteration-assign";
      const iterLabel = document.createElement("span");
      iterLabel.textContent = "所属版本：";
      const iterSelect = document.createElement("select");
      iterSelect.className = "detail-iteration-select";
      iterSelect.setAttribute("aria-label", "调整所属版本");
      const none = document.createElement("option");
      none.value = "";
      none.textContent = "未归入版本";
      iterSelect.append(none);
      orderedIterations().forEach(item => {
        const opt = document.createElement("option");
        opt.value = item.id;
        opt.textContent = `${iterationName(item)} · ${versionTypeLabel(item)}版本 · ${iterationStatusLabel(item.status)}`;
        if (item.id === record.iteration_id) opt.selected = true;
        iterSelect.append(opt);
      });
      if (!iterationById(record.iteration_id)) iterSelect.value = "";
      iterSelect.addEventListener("change", async (e) => {
        const newIterId = e.target.value || null;
        try {
          const res = await fetch(`/api/items/${encodeURIComponent(record.id)}/iteration`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ iteration_id: newIterId })
          });
          if (!res.ok) throw new Error("版本切换失败");
          showToast("已更新需求所属版本");
          record.iteration_id = newIterId;
          await refresh();
        } catch (err) {
          showToast(err.message, "error");
          iterSelect.value = record.iteration_id || "";
        }
      });
      iterAssignWrap.append(iterLabel, iterSelect);
      head.append(iterAssignWrap);
    }
    const statusLine = document.createElement("p"); statusLine.className = "detail-status-line";
    statusLine.textContent = record.type === "bug" && record.platform_status
      ? `平台上次读取：${statusLabel(record.platform_status)} · ${dateLabel(record.observed_at, "读取时间未记录")}`
      : `更新于 ${dateLabel(record.updated_at, "时间未记录")}`;
    const currentStatus = document.createElement("section"); currentStatus.className = "detail-current-status";
    const currentStatusLabel = document.createElement("span"); currentStatusLabel.className = "detail-current-status-label"; currentStatusLabel.textContent = "当前现状";
    const currentStatusText = document.createElement("p"); currentStatusText.className = "detail-current-status-text";
    currentStatusText.textContent = txt(record.status_line || record.latest_brief || record.status_detail, "暂未记录当前现状");
    currentStatus.append(currentStatusLabel, currentStatusText);
    const overview = document.createElement("section"); overview.className = "detail-overview";
    const overviewTitle = document.createElement("h3"); overviewTitle.textContent = "交接信息";
    const conclusion = document.createElement("p"); conclusion.className = "detail-overview-conclusion";
    const progressText = txt(record.latest_brief || record.status_detail, "");
    conclusion.textContent = progressText;
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
    overview.append(overviewTitle);
    // 顶部「当前现状」已经展示同一句时不再重复；交接字段始终保留。
    if (conclusion.textContent && conclusion.textContent.trim() !== currentStatusText.textContent.trim()) overview.append(conclusion);
    overview.append(overviewGrid);
    detail.append(head, statusLine, currentStatus, makeWorkflowSection(record), overview);
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
    const metaFields = [["事项编号", recordDisplayId(record)], [isCancelled(record) ? "取消日期" : "完成日期", isTerminal(record) ? terminalDate : "未终结"], ["负责人", record.owner || record.assignee || "未注明"], ["更新时间", dateLabel(record.updated_at, "未提供")],
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
      enhanceTableScrollers(contextBody);
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
    if (record.reopen_count > 0 || (record.reopen_history && record.reopen_history.length)) {
      const reworkFold = document.createElement("details");
      reworkFold.className = "detail-history-fold rework-history-fold";
      reworkFold.open = true;
      const reworkSummary = document.createElement("summary");
      reworkSummary.textContent = `二次修改与再审计记录（共 ${record.reopen_count || record.reopen_history.length} 轮）`;
      reworkSummary.dataset.dialogFocus = "rework-history";
      const reworkBody = document.createElement("div");
      reworkBody.className = "detail-history-body";
      const historyList = (record.reopen_history || []).map((round, idx) => {
        const modeLabel = round.audit_mode === "lightweight" ? "轻量自审复核" : "全量静态审计";
        const statusText = round.completed_at ? `已完成（${dateLabel(round.completed_at)}）` : "正在修改中";
        return `第 ${round.round || idx + 1} 轮 · ${dateLabel(round.reopened_at)} · [${modeLabel}] · ${statusText}\n修改原因：${round.reason || "未填写"}`;
      });
      addDetailSection(reworkBody, "修改记录", historyList, true);
      reworkFold.append(reworkSummary, reworkBody);
      detail.append(reworkFold);
    }
    const attachFold = document.createElement("details");
    attachFold.className = "detail-history-fold task-attachments-fold";
    const attachments = Array.isArray(record.attachments) ? record.attachments : [];
    attachFold.open = attachments.length > 0;
    const attachSummary = document.createElement("summary");
    attachSummary.textContent = `补充材料与截图（${attachments.length}）`;
    attachSummary.dataset.dialogFocus = "attachments";
    const attachBody = document.createElement("div");
    attachBody.className = "detail-history-body";
    if (attachments.length) {
      const attachGrid = document.createElement("div");
      attachGrid.className = "supplementary-grid";
      attachments.forEach(att => {
        const card = document.createElement("div");
        card.className = "supplementary-card";
        const isImg = att.mime_type?.startsWith("image/") || /\.(png|jpe?g|gif|webp)$/i.test(att.filename || "");
        if (isImg && att.download_url) {
          const img = document.createElement("img");
          img.src = att.download_url;
          img.alt = att.summary || att.filename;
          img.className = "supplementary-thumb";
          card.append(img);
        }
        const fname = document.createElement("a");
        fname.href = att.download_url || "#";
        fname.target = "_blank";
        fname.rel = "noopener noreferrer";
        fname.className = "supplementary-name";
        fname.textContent = att.filename;
        const fsummary = document.createElement("p");
        fsummary.className = "supplementary-summary";
        fsummary.textContent = att.summary || "暂无说明";
        card.append(fname, fsummary);
        attachGrid.append(card);
      });
      attachBody.append(attachGrid);
    }
    const addAttachBtn = document.createElement("button");
    addAttachBtn.type = "button";
    addAttachBtn.className = "source-action";
    addAttachBtn.dataset.uploadTaskAttachment = record.id;
    addAttachBtn.textContent = "+ 上传截图 / 补充文件";
    attachBody.append(addAttachBtn);
    attachFold.append(attachSummary, attachBody);
    detail.append(attachFold);
  }
  let detailCloseTimer = 0;
  const detailReduceMotion = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : { matches: false };
  // 详情统一走收起动画；重复点击、ESC、点遮罩都复用同一个关闭入口。
  function closeDetailDialog() {
    const dialog = $("#record-dialog");
    if (!dialog?.open || dialog.classList.contains("is-closing")) return;
    if (detailReduceMotion.matches) { dialog.close(); return; }
    dialog.classList.add("is-closing");
    detailCloseTimer = window.setTimeout(() => {
      detailCloseTimer = 0;
      dialog.classList.remove("is-closing");
      if (dialog.open) dialog.close();
    }, 200);
  }
  function openDetail(id) {
    const record = findRecord(id); if (!record) return;
    const dialog = $("#record-dialog");
    if (detailCloseTimer) { clearTimeout(detailCloseTimer); detailCloseTimer = 0; }
    dialog.classList.remove("is-closing");
    state.selectedId = id; renderDetail(record);
    if (!dialog.open) dialog.showModal();
    scrollChrome?.refreshDetail(); loadItemWorkflow(record);
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
            enhanceTableScrollers(target);
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
  function showToast(message, kind = "success", action = null) {
    const toast = $("#toast");
    const text = document.createElement("span");
    text.textContent = message;
    toast.replaceChildren(text);
    if (action?.label && typeof action.onClick === "function") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "toast-action";
      button.textContent = action.label;
      button.addEventListener("click", () => {
        toast.hidden = true;
        action.onClick();
      });
      toast.append(button);
    }
    toast.className = `toast ${kind}`;
    toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => { toast.hidden = true; }, action ? 8000 : 3400);
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
  async function refresh() {
    const freshness = $("#freshness");
    try {
      const sinceToken = state.data
        ? `${state.data.revision ?? 0}:${state.scopeCatalog?.revision || 0}:${BUILD_ID || ""}`
        : "";
      const url = sinceToken ? `/api/dashboard?since_revision=${encodeURIComponent(sinceToken)}` : "/api/dashboard";
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data.build && BUILD_ID && data.build !== BUILD_ID) {
        const key = `waylume-build:${data.build}`;
        try {
          if (!sessionStorage.getItem(key)) { sessionStorage.setItem(key, "1"); location.reload(); return; }
        } catch (_) { location.reload(); return; }
      }
      if (data.unchanged) {
        const when = new Date(data.generated_at);
        const time = Number.isNaN(when.getTime()) ? data.generated_at : new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(when);
        const sync = data.sync?.bugs, stale = !!(sync?.stale || sync?.error);
        freshness.classList.toggle("stale", stale); freshness.lastElementChild.textContent = stale ? `数据可能过期 · ${time}` : `已更新 ${time}`;
        const banner = $("#state-banner"); banner.hidden = !stale;
        if (stale) banner.textContent = `BUG 快照读取失败或已标旧；保留最近成功快照（${txt(sync?.last_successful_snapshot_at)}）。`;
        return;
      }
      const scopeChanged = Number(state.scopeCatalog?.revision || 0) !== Number(data.scope_catalog?.revision || 0);
      state.scopeCatalog = data.scope_catalog || state.scopeCatalog;
      if ($("#scopes-dialog")?.open) renderScopeCatalog();
      // A newer asset bundle is deployed: reload once so an already-open tab
      // never keeps showing an older UI after an update.
      const changed = !state.data || Number(state.data.revision) !== Number(data.revision) || scopeChanged;
      state.data = data;
      if (data.version) setWorkbenchVersion(data.version, data.build);
      else if (!state.workbenchVersion) refreshWorkbenchVersion();
      state.iterations = model.orderedIterations(Array.isArray(data.iterations) ? data.iterations : []);
      state.currentIterationId = data.current_iteration_id || null;
      if (changed) renderIterationManager();
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

  function iterationDemandCount(iterationId) {
    return (state.data?.requirements || []).filter(record => record.iteration_id === iterationId).length;
  }
  function makeManagerAction(label, action, iterationId, extra = {}) {
    const button = makeTextButton(label, `iteration-manager-action${extra.className ? ` ${extra.className}` : ""}`);
    button.dataset.iterationAction = action;
    button.dataset.iterationId = iterationId;
    if (extra.disabled) button.disabled = true;
    if (extra.title) button.title = extra.title;
    if (extra.ariaLabel) button.setAttribute("aria-label", extra.ariaLabel);
    return button;
  }
  function renderIterationManager() {
    const list = $("#iteration-manager-list");
    if (!list) return;
    const iterations = orderedIterations();
    const empty = $("#iteration-manager-empty");
    const count = $("#iteration-manager-count");
    const loading = $("#iteration-manager-loading");
    const busy = !!state.iterationBusy;
    if (empty) empty.hidden = iterations.length > 0;
    if (count) count.textContent = `${iterations.length} 个版本`;
    if (loading) loading.hidden = !busy;
    list.setAttribute("aria-busy", String(busy));
    $("#iteration-manager-dialog")?.classList.toggle("is-busy", busy);
    list.replaceChildren(...iterations.map((iteration, index) => {
      const row = document.createElement("li");
      row.className = "iteration-manager-row";
      row.dataset.iterationId = iteration.id;
      const released = model.iterationIsReleased(iteration);
      row.classList.toggle("is-released", released);
      const position = document.createElement("span");
      position.className = "iteration-manager-index";
      position.textContent = String(index + 1).padStart(2, "0");
      position.setAttribute("aria-hidden", "true");
      const copy = document.createElement("div");
      copy.className = "iteration-manager-copy";
      const titleLine = document.createElement("div");
      titleLine.className = "iteration-manager-title-line";
      const versionLabel = iterationName(iteration);
      const name = document.createElement("strong");
      name.className = "iteration-manager-name";
      name.textContent = versionLabel;
      titleLine.append(name, versionTypePill(iteration), iterationStatusSelect(iteration, busy));
      const tag = model.iterationSecondaryTag(iteration);
      if (tag) name.title = `版本标识：${tag}`;
      const meta = document.createElement("span");
      meta.className = "iteration-manager-meta";
      const date = iteration.target_release_date ? ` · 计划发布 ${iteration.target_release_date}` : "";
      meta.textContent = `${iterationDemandCount(iteration.id)} 个需求${date}`;
      copy.append(titleLine, meta);
      const actions = document.createElement("div");
      actions.className = "iteration-manager-actions";
      actions.append(
        makeManagerAction("上移", "up", iteration.id, { disabled: busy || index === 0, title: "上移版本（更靠前、更新）", ariaLabel: `上移 ${versionLabel}` }),
        makeManagerAction("下移", "down", iteration.id, { disabled: busy || index === iterations.length - 1, title: "下移版本（更靠后、更旧）", ariaLabel: `下移 ${versionLabel}` }),
        makeManagerAction("编辑", "edit", iteration.id, { disabled: busy, title: "修改版本名、日期或描述", ariaLabel: `编辑 ${versionLabel}` }),
        makeManagerAction(iterationVersionType(iteration) === "analytics" ? "转为迭代" : "转为埋点",
          "set-type", iteration.id, {
            disabled: busy,
            title: iterationVersionType(iteration) === "analytics"
              ? "切换为迭代类型；埋点资料会映射回需求列表/测试用例，并先备份数据"
              : "切换为埋点类型；需求列表/测试用例会映射为埋点需求表/验收表，并先备份数据",
            ariaLabel: `切换类型 ${versionLabel}`
          })
      );
      if (!released) {
        actions.append(makeManagerAction("标记发布", "release", iteration.id, { className: "is-release", disabled: busy, title: "发布后该版本需求不再支持直接二次修改", ariaLabel: `标记发布 ${versionLabel}` }));
      }
      row.append(position, copy, actions);
      return row;
    }));
  }
  function setIterationManagerBusy(busy) {
    state.iterationBusy = busy;
    renderIterationManager();
  }
  async function reorderIteration(iterationId, direction) {
    if (state.iterationBusy) return;
    const ids = orderedIterations().map(iteration => iteration.id);
    const index = ids.indexOf(iterationId);
    const target = direction === "up" ? index - 1 : index + 1;
    if (index < 0 || target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    setIterationManagerBusy(true);
    try {
      await postAction("/api/iterations/order", { ids });
      await refresh();
      showToast("版本顺序已更新");
    } catch (error) {
      showToast(error.message || "版本顺序更新失败", "error");
    } finally {
      setIterationManagerBusy(false);
    }
  }
  function iterationError(payload, fallback) {
    const errors = {
      iteration_duplicate: "已存在同名版本，请换一个版本名。",
      iteration_not_found: "找不到这个版本，请刷新后重试。",
      invalid_iteration_title: "请填写有效的版本名。",
      invalid_iteration_version_tag: "请填写有效的版本名。",
      invalid_iteration_version_type: "版本类型无效，请刷新后重试。",
      invalid_iteration_patch: "版本类型只能通过「转为埋点/转为迭代」调整。",
      invalid_iteration_status: "版本状态无效，请刷新后重试。",
      iteration_type_verification_failed: "类型转换后的资料核对未通过，请用备份回滚并联系维护者。",
      invalid_iteration_order: "版本顺序无效，请刷新后重试。",
      iteration_order_mismatch: "版本列表已变化，请刷新后重试。",
      missing_iteration_id: "请选择目标版本。"
    };
    return errors[payload.error] || payload.message || fallback;
  }
  async function iterationRequest(path, body) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(iterationError(payload, "操作失败，请重试。"));
    return payload;
  }
  function openIterationEditDialog(iterationId) {
    const iteration = iterationById(iterationId) || orderedIterations()[0];
    if (!iteration) return;
    state.iterationEditId = iteration.id;
    $("#iteration-edit-id").value = iteration.id;
    $("#iteration-edit-title-input").value = iterationName(iteration);
    const editType = $("#iteration-edit-type");
    if (editType) editType.textContent = `${versionTypeLabel(iteration)}版本 · 类型切换请在版本列表使用「转为埋点/转为迭代」`;
    $("#iteration-edit-date-input").value = iteration.target_release_date || "";
    $("#iteration-edit-desc-input").value = iteration.description || "";
    $("#iteration-edit-feedback").textContent = "";
    $("#iteration-edit-feedback").className = "source-feedback";
    $("#iteration-edit-dialog").showModal();
    $("#iteration-edit-title-input").focus();
  }
  async function submitIterationEditForm(event) {
    event.preventDefault();
    const iterationId = $("#iteration-edit-id").value;
    if (!iterationId) return;
    const title = $("#iteration-edit-title-input").value.trim();
    const target_release_date = $("#iteration-edit-date-input").value || null;
    const description = $("#iteration-edit-desc-input").value.trim();
    const feedback = $("#iteration-edit-feedback");
    const submitBtn = $("#iteration-edit-submit");
    if (!title) { feedback.textContent = "请填写版本名。"; feedback.className = "source-feedback error"; return; }
    submitBtn.disabled = true;
    feedback.textContent = "正在保存版本信息…";
    feedback.className = "source-feedback busy";
    try {
      // One editable name: version_tag stays as the compatible identifier.
      await iterationRequest(`/api/iterations/${encodeURIComponent(iterationId)}/update`, {
        title, version_tag: title, target_release_date, description
      });
      $("#iteration-edit-dialog").close();
      showToast("版本信息已更新");
      await refresh();
      if ($("#sources-dialog").open) await loadSources();
    } catch (error) {
      feedback.textContent = error.message;
      feedback.className = "source-feedback error";
    } finally {
      submitBtn.disabled = false;
    }
  }
  function openIterationCreateDialog() {
    const iterations = orderedIterations(); // display order: newest first
    $("#iteration-feedback").textContent = "";
    $("#iteration-feedback").className = "source-feedback";
    $("#iteration-form").reset();
    const typeSelect = $("#iteration-type-input");
    if (typeSelect) typeSelect.value = "iteration";
    const select = $("#iteration-order-select");
    const count = iterations.length;
    // UI positions are display positions (0 = newest). The backend `order`
    // field stays ascending, so display index k maps to count - k.
    const options = [{ value: String(count), label: "放在最前（最新）" }];
    iterations.forEach((iteration, index) => {
      options.push({ value: String(count - index - 1), label: `放在「${iterationName(iteration)}」之后` });
    });
    select.replaceChildren(...options.map(option => {
      const node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      return node;
    }));
    select.value = String(count);
    $("#iteration-dialog").showModal();
    $("#iteration-title-input").focus();
  }
  async function submitIterationForm(event) {
    event.preventDefault();
    const title = $("#iteration-title-input").value.trim();
    const order = Number($("#iteration-order-select").value || 0);
    const version_type = $("#iteration-type-input").value === "analytics" ? "analytics" : "iteration";
    const target_release_date = $("#iteration-date-input").value || null;
    const description = $("#iteration-desc-input").value.trim() || null;
    const feedback = $("#iteration-feedback");
    const submitBtn = $("#iteration-submit");
    if (!title) { feedback.textContent = "请填写版本名。"; feedback.className = "source-feedback error"; return; }
    submitBtn.disabled = true;
    feedback.textContent = "正在创建版本…";
    feedback.className = "source-feedback busy";
    try {
      const result = await iterationRequest("/api/iterations", {
        version_tag: title, title, target_release_date, description, order, version_type
      });
      const createdName = iterationName(result.iteration || { title });
      $("#iteration-dialog").close();
      $("#iteration-form").reset();
      showToast(`已创建${versionTypeLabel(version_type)}版本：${createdName}`);
      await refresh();
      if ($("#sources-dialog").open) await loadSources();
    } catch (error) {
      feedback.textContent = error.message || "创建版本失败，请重试。";
      feedback.className = "source-feedback error";
    } finally {
      submitBtn.disabled = false;
    }
  }
  async function handleReleaseIteration(iterationId) {
    if (state.iterationBusy) return;
    const iteration = iterationById(iterationId);
    if (!iteration) return;
    if (!window.confirm(`确认将版本「${iterationName(iteration)}」标记为已发布？\n发布后该版本需求不再支持直接二次修改。`)) return;
    setIterationManagerBusy(true);
    try {
      await iterationRequest(`/api/iterations/${encodeURIComponent(iteration.id)}/release`, {});
      showToast(`版本「${iterationName(iteration)}」已标记发布`);
      await refresh();
    } catch (error) {
      showToast(error.message || "标记发布失败", "error");
    } finally {
      setIterationManagerBusy(false);
    }
  }
  function iterationStatusErrorMessage(error) {
    if (!error || error.name === "TypeError" || /Failed to fetch|NetworkError|Load failed/i.test(error.message || "")) {
      return "无法连接本地服务，版本状态未修改，请确认服务正在运行。";
    }
    return error.message || "版本状态更新失败，请重试。";
  }
  async function changeIterationStatus(iterationId, status) {
    if (state.iterationBusy) return;
    const iteration = iterationById(iterationId);
    if (!iteration || iteration.status === status) return;
    if (status === "released" && !window.confirm(`确认将版本「${iterationName(iteration)}」标记为已发布？\n发布后该版本需求不再支持直接二次修改。`)) {
      renderIterationManager();
      return;
    }
    state.iterationBusy = true;
    renderIterationManager();
    try {
      const payload = await iterationRequest(`/api/iterations/${encodeURIComponent(iterationId)}/update`, { status });
      if (payload.iteration?.id) {
        const index = state.iterations.findIndex(item => item.id === payload.iteration.id);
        if (index >= 0) state.iterations[index] = { ...state.iterations[index], ...payload.iteration };
      }
      const derived = status === "planning" || status === "active";
      showToast(`版本「${iterationName(iteration)}」已切换为${iterationStatusLabel(status)}${derived ? "；该状态会随需求数量自动变化" : ""}`);
      await refresh();
    } catch (error) {
      showToast(iterationStatusErrorMessage(error), "error");
    } finally {
      state.iterationBusy = false;
      renderIterationManager();
    }
  }
  async function convertIterationType(iterationId) {
    if (state.iterationBusy) return;
    const iteration = iterationById(iterationId);
    if (!iteration) return;
    const targetType = iterationVersionType(iteration) === "analytics" ? "iteration" : "analytics";
    const targetLabel = versionTypeLabel(targetType);
    const detail = targetType === "analytics"
      ? "需求列表会映射为埋点需求表，测试用例映射为埋点验收表；Figma/高保真等迭代固定资料会保留为补充材料。"
      : "埋点需求表会映射回需求列表，埋点验收表映射回测试用例；补充材料保持不变。";
    if (!window.confirm(`确认把版本「${iterationName(iteration)}」切换为${targetLabel}类型？\n${detail}\n\n转换前会自动备份 store，版本 ID 与版本名不变。`)) return;
    setIterationManagerBusy(true);
    try {
      const result = await iterationRequest(`/api/iterations/${encodeURIComponent(iteration.id)}/type`, { version_type: targetType });
      if (result.backup_path) console.info("版本类型转换备份：", result.backup_path);
      showToast(`已切换为${targetLabel}类型，转换前已备份`, "success");
      await refresh();
      if ($("#sources-dialog").open) await loadSources();
    } catch (error) {
      showToast(error.message || "版本类型切换失败", "error");
    } finally {
      setIterationManagerBusy(false);
    }
  }

  function relocateSlotOptions(scope, iteration = null) {
    const definitions = slotDefinitions(scope, iteration).map(definition => ({ value: definition.id, label: definition.label }));
    definitions.push({ value: "supplementary", label: "补充材料" });
    return definitions;
  }
  function openRelocateDialog(source) {
    if (!source) return;
    state.relocateTargetSource = source;
    const dialog = $("#source-relocate-dialog");
    if (!dialog) return;
    const groupId = source.group_id || source.id;
    const groupSize = state.sources.filter(item => (item.group_id || item.id) === groupId).length;
    $("#relocate-source-title").textContent = `移动材料：${source.name || source.filename || source.id}`;
    $("#relocate-current-location").textContent = `当前位置：${sourceLocationText(source)}`;
    const groupHint = $("#relocate-group-hint");
    groupHint.hidden = groupSize <= 1;
    groupHint.textContent = groupSize > 1
      ? `这条材料共有 ${groupSize} 个版本，移动会把整组一起移到目标位置。`
      : "";
    const scopeSelect = $("#relocate-scope-select");
    const iterSelect = $("#relocate-iteration-select");
    const slotSelect = $("#relocate-slot-select");
    const iterWrap = $("#relocate-iteration-wrap");
    const currentScope = model.sourceScope(source);
    scopeSelect.value = currentScope;
    iterSelect.replaceChildren(...orderedIterations().map(iteration => {
      const option = document.createElement("option");
      option.value = iteration.id;
      option.textContent = `${iterationName(iteration)} · ${versionTypeLabel(iteration)}版本`;
      return option;
    }));
    if (source.iteration_id && iterationById(source.iteration_id)) iterSelect.value = source.iteration_id;

    function syncSlots() {
      const isGlobal = scopeSelect.value === "global";
      iterWrap.hidden = isGlobal;
      const targetIteration = isGlobal ? null : iterationById(iterSelect.value);
      const options = relocateSlotOptions(isGlobal ? "global" : "iteration", targetIteration);
      slotSelect.replaceChildren(...options.map(option => {
        const node = document.createElement("option");
        node.value = option.value;
        node.textContent = option.label;
        return node;
      }));
      const preferred = source.slot === "codesign" ? "prototype" : source.slot;
      slotSelect.value = options.some(option => option.value === preferred) ? preferred : "supplementary";
    }
    scopeSelect.onchange = syncSlots;
    iterSelect.onchange = syncSlots;
    syncSlots();
    $("#relocate-feedback").textContent = "";
    $("#relocate-feedback").className = "source-feedback";
    dialog.showModal();
    iterSelect.focus({ preventScroll: true });
  }
  async function submitRelocateForm(event) {
    event.preventDefault();
    const source = state.relocateTargetSource;
    if (!source) return;
    const scope = $("#relocate-scope-select").value;
    const iterationId = scope === "iteration" ? $("#relocate-iteration-select").value : null;
    const slot = $("#relocate-slot-select").value || "supplementary";
    const feedback = $("#relocate-feedback");
    const submitBtn = $("#relocate-submit");
    if (scope === "iteration" && !iterationId) {
      feedback.textContent = "请先选择目标版本。";
      feedback.className = "source-feedback error";
      return;
    }
    const previous = { scope: model.sourceScope(source), iteration_id: source.iteration_id || null, slot: source.slot || "supplementary" };
    submitBtn.disabled = true;
    feedback.textContent = "正在移动材料…";
    feedback.className = "source-feedback busy";
    try {
      await iterationRequest(`/api/sources/${encodeURIComponent(source.id)}/location`, {
        scope, iteration_id: iterationId, slot
      });
      $("#source-relocate-dialog").close();
      state.archiveScopeTab = scope;
      state.libraryHighlight = source.group_id || source.id;
      await loadSources();
      await refresh();
      const target = scope === "global" ? "公共材料" : `${iterationName(iterationById(iterationId))} · ${slotLabel(slot)}`;
      showToast(`已移动到${target}`, "success", {
        label: "撤销",
        onClick: async () => {
          try {
            await iterationRequest(`/api/sources/${encodeURIComponent(source.id)}/location`, {
              scope: previous.scope, iteration_id: previous.iteration_id, slot: previous.slot
            });
            await loadSources();
            showToast("已撤销移动");
          } catch (error) {
            showToast(error.message || "撤销失败", "error");
          }
        }
      });
    } catch (error) {
      feedback.textContent = error.message;
      feedback.className = "source-feedback error";
    } finally {
      submitBtn.disabled = false;
    }
  }

  async function openUploadTaskAttachment(recordId) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*,.pdf,.txt,.md";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      const summary = window.prompt(`请为文件「${file.name}」输入简短说明（如 PM 沟通结论）：`, "") || file.name;
      showToast(`正在上传“${file.name}”…`);
      try {
        const content_base64 = await encodeFile(file);
        await postAction(`/api/items/${encodeURIComponent(recordId)}/attachments`, {
          filename: file.name,
          content_base64,
          summary
        });
        showToast(`已上传补充材料“${file.name}”`);
        await refresh();
        if ($("#record-dialog").open) openDetail(recordId);
      } catch (err) {
        showToast(err.message || "上传失败", "error");
      }
    };
    input.click();
  }

  let searchTimer = 0;
  $("#search").addEventListener("input", event => {
    state.query = event.target.value;
    if (searchTimer) { clearTimeout(searchTimer); searchTimer = 0; }
    if (!state.query) { render(); return; }
    searchTimer = setTimeout(() => { searchTimer = 0; render(); }, 100);
  });
  $("#show-archived").addEventListener("change", event => { state.showArchived = event.target.checked; render(); });
  $("#refresh").addEventListener("click", () => { refresh(); refreshWorkbenchVersion(); });
  $("#work-released-toggle").addEventListener("click", toggleWorkReleased);
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
  $("#source-file-input").addEventListener("change", event => { selectLibraryFile(event.target.files?.[0]); scheduleSourceNamePreview(); });
  $("#library-file-clear").addEventListener("click", () => { $("#source-file-input").value = ""; selectLibraryFile(null); scheduleSourceNamePreview(); });
  $("#library-add-cancel").addEventListener("click", () => resetLibraryAdd(true));
  $("#source-link-form").addEventListener("submit", submitLibraryEntry);
  $("#source-link-form").addEventListener("reset", () => { cancelSourceNamePreview(); selectLibraryFile(null); setSourceNameHint(""); });
  $("#source-link-url").addEventListener("input", scheduleSourceNamePreview);
  $("#source-link-name").addEventListener("input", () => {
    state.autoName.userEdited = true;
    cancelSourceNamePreview();
    setSourceNameHint("");
  });
  $("#source-edit-close").addEventListener("click", closeSourceEdit);
  $("#source-edit-cancel").addEventListener("click", closeSourceEdit);
  $("#source-edit-dialog").addEventListener("click", event => { if (event.target === $("#source-edit-dialog")) closeSourceEdit(); });
  $("#source-edit-form").addEventListener("submit", submitSourceEdit);
  $("#archive-body").addEventListener("click", handleLibraryClick);
  $("#library-add-slot").addEventListener("change", updateLibraryAddContext);
  $("#dialog-close").addEventListener("click", closeDetailDialog);
  $("#record-dialog").addEventListener("click", event => { if (event.target === $("#record-dialog")) closeDetailDialog(); });
  $("#record-dialog").addEventListener("cancel", event => {
    if (!$("#record-dialog").open) return;
    event.preventDefault();
    closeDetailDialog();
  });
  $("#text-viewer-close").addEventListener("click", () => $("#text-viewer-dialog").close());
  $("#text-viewer-copy").addEventListener("click", copyTextViewerText);
  $("#text-viewer-dialog").addEventListener("click", event => { if (event.target === $("#text-viewer-dialog")) $("#text-viewer-dialog").close(); });
  $("#record-dialog").addEventListener("close", () => {
    if (detailCloseTimer) { clearTimeout(detailCloseTimer); detailCloseTimer = 0; }
    $("#record-dialog").classList.remove("is-closing");
    if (state.selectedId) state.workflowEntered.delete(state.selectedId);
  });
  document.addEventListener("click", event => {
    const open = event.target.closest("[data-open-record]"); if (open) openDetail(open.dataset.openRecord);
    if (!event.target.closest("button,a,input,select,textarea,summary")) { const card = event.target.closest(".record-card"); if (card) openDetail(card.dataset.recordId); }
    const workflowStage = event.target.closest("[data-workflow-stage]"); if (workflowStage) selectWorkflowStage(workflowStage);
    const workflowStart = event.target.closest("[data-workflow-start]"); if (workflowStart) startWorkflow(workflowStart);
    const workflowRetry = event.target.closest("[data-workflow-retry]"); if (workflowRetry) retryWorkflow(workflowRetry.dataset.workflowRetry);
    const evidenceFile = event.target.closest("[data-evidence-record]");
    if (evidenceFile) openLocalFileViewer(evidenceFile.dataset.evidenceRecord, evidenceFile.dataset.evidencePath, { kicker: "证据全文" });
    const transfer = event.target.closest("[data-transfer-id]"); if (transfer) submitTransfer(transfer);
    const complete = event.target.closest("[data-complete-id]"); if (complete) submitCompletion(complete);
    const uploadTaskAtt = event.target.closest("[data-upload-task-attachment]"); if (uploadTaskAtt) openUploadTaskAttachment(uploadTaskAtt.dataset.uploadTaskAttachment);
  });
  $("#iteration-manager-open").addEventListener("click", () => {
    renderIterationManager();
    $("#iteration-manager-dialog").showModal();
  });
  const managerClose = $("#iteration-manager-close");
  if (managerClose) managerClose.addEventListener("click", () => $("#iteration-manager-dialog").close());
  const managerDialog = $("#iteration-manager-dialog");
  if (managerDialog) managerDialog.addEventListener("click", event => { if (event.target === managerDialog) managerDialog.close(); });
  $("#iteration-manager-list").addEventListener("click", event => {
    const button = event.target.closest("[data-iteration-action]");
    if (!button || button.disabled) return;
    const iterationId = button.dataset.iterationId;
    const action = button.dataset.iterationAction;
    if (action === "up" || action === "down") return reorderIteration(iterationId, action);
    if (action === "edit") return openIterationEditDialog(iterationId);
    if (action === "release") return handleReleaseIteration(iterationId);
    if (action === "set-type") return convertIterationType(iterationId);
  });
  $("#iteration-manager-list").addEventListener("change", event => {
    const select = event.target.closest("[data-iteration-status]");
    if (!select || select.disabled) return;
    changeIterationStatus(select.dataset.iterationStatus, select.value);
  });
  const iterCreateBtn = $("#iteration-create-open");
  if (iterCreateBtn) iterCreateBtn.addEventListener("click", openIterationCreateDialog);
  const iterCloseBtn = $("#iteration-dialog-close");
  if (iterCloseBtn) iterCloseBtn.addEventListener("click", () => $("#iteration-dialog").close());
  const iterCancelBtn = $("#iteration-cancel");
  if (iterCancelBtn) iterCancelBtn.addEventListener("click", () => $("#iteration-dialog").close());
  const iterForm = $("#iteration-form");
  if (iterForm) iterForm.addEventListener("submit", submitIterationForm);
  const iterEditCloseBtn = $("#iteration-edit-close");
  if (iterEditCloseBtn) iterEditCloseBtn.addEventListener("click", () => $("#iteration-edit-dialog").close());
  const iterEditCancelBtn = $("#iteration-edit-cancel");
  if (iterEditCancelBtn) iterEditCancelBtn.addEventListener("click", () => $("#iteration-edit-dialog").close());
  const iterEditForm = $("#iteration-edit-form");
  if (iterEditForm) iterEditForm.addEventListener("submit", submitIterationEditForm);
  const iterEditDialog = $("#iteration-edit-dialog");
  if (iterEditDialog) iterEditDialog.addEventListener("click", event => { if (event.target === iterEditDialog) iterEditDialog.close(); });
  const relocateCloseBtn = $("#relocate-dialog-close");
  if (relocateCloseBtn) relocateCloseBtn.addEventListener("click", () => $("#source-relocate-dialog").close());
  const relocateCancelBtn = $("#relocate-cancel");
  if (relocateCancelBtn) relocateCancelBtn.addEventListener("click", () => $("#source-relocate-dialog").close());
  const relocateForm = $("#relocate-form");
  if (relocateForm) relocateForm.addEventListener("submit", submitRelocateForm);
  const relocateDialog = $("#source-relocate-dialog");
  if (relocateDialog) relocateDialog.addEventListener("click", event => { if (event.target === relocateDialog) relocateDialog.close(); });
  const archiveTabs = $("#archive-scope-tabs");
  if (archiveTabs) {
    const activateTab = btn => {
      if (!btn) return;
      state.archiveScopeTab = btn.dataset.scope;
      renderSources();
      $(`#${btn.id}`)?.focus({ preventScroll: true });
    };
    archiveTabs.addEventListener("click", event => {
      const btn = event.target.closest(".archive-tab-btn");
      if (btn) activateTab(btn);
    });
    archiveTabs.addEventListener("keydown", event => {
      const tabs = $$(".archive-tab-btn", archiveTabs);
      if (!tabs.length) return;
      const current = tabs.indexOf(event.target.closest(".archive-tab-btn"));
      if (current < 0) return;
      let next = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = tabs[(current + 1) % tabs.length];
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = tabs[(current - 1 + tabs.length) % tabs.length];
      else if (event.key === "Home") next = tabs[0];
      else if (event.key === "End") next = tabs[tabs.length - 1];
      if (!next) return;
      event.preventDefault();
      activateTab(next);
    });
  }
  document.addEventListener("toggle", event => {
    const target = event.target;
    if (target.matches?.("details.released-version-card")) {
      const iterationId = target.dataset.iterationId;
      if (target.open) {
        state.workReleasedExpanded.add(iterationId);
        if (!target.querySelector(".released-version-records") && target._releasedGroup) {
          target.append(makeReleasedVersionBody(target._releasedGroup));
        }
      } else {
        state.workReleasedExpanded.delete(iterationId);
      }
    }
    if (target.matches?.("details.archive-block-released")) {
      const iterationId = target.dataset.iterationId;
      if (target.open) state.archiveReleasedExpanded.add(iterationId);
      else state.archiveReleasedExpanded.delete(iterationId);
    }
    if (target.matches?.("details") && target.open) {
      requestAnimationFrame(() => $$(".record-list", target).forEach(relayoutMasonryList));
    }
  }, true);
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    if ($("#text-viewer-dialog")?.open || $("#source-edit-dialog")?.open) return;
    if ($("#record-dialog").open) closeDetailDialog();
  });
  document.addEventListener("pointerdown", () => { document.body.dataset.inputMode = "pointer"; }, true);
  document.addEventListener("keydown", event => {
    if (event.key === "Tab") document.body.dataset.inputMode = "keyboard";
  }, true);
  $("#inspection-run").addEventListener("click", runInspection);
  $("#inspection-plan").addEventListener("click", openInspectionHistory);
  $("#inspection-dialog-close").addEventListener("click", () => $("#inspection-dialog").close());
  $("#inspection-dialog").addEventListener("click", event => {
    if (event.target === $("#inspection-dialog")) $("#inspection-dialog").close();
  });
  setupScrollChrome();
  refreshWorkbenchVersion();
  refresh(); refreshInspectionSchedule(); refreshInspectionStatus();
  let realtimeSource = null;
  function connectRealtime() {
    if (typeof EventSource === "undefined") return;
    try {
      if (realtimeSource) realtimeSource.close();
      realtimeSource = new EventSource("/api/events");
      realtimeSource.onmessage = event => {
        let payload = null; try { payload = JSON.parse(event.data || "{}"); } catch (_) { return; }
        if (payload && Number(payload.revision || 0) !== Number(state.data?.revision || 0)) refresh();
      };
      realtimeSource.onerror = () => { try { realtimeSource.close(); } catch (_) {} realtimeSource = null; window.setTimeout(connectRealtime, 3000); };
    } catch (_) { window.setTimeout(connectRealtime, 3000); }
  }
  connectRealtime();
  window.setInterval(() => { if (!document.hidden) refresh(); }, 3000);
  window.setInterval(() => { if (!document.hidden) refreshInspectionSchedule(); }, 30000);
  window.setInterval(() => { if (!document.hidden) refreshInspectionStatus(); }, 3000);
  window.setInterval(renderInspectionPlan, 1000);
  window.setInterval(renderWorktimeProgress, 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); refreshInspectionSchedule(); refreshInspectionStatus(); } });
})();
