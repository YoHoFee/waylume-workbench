# 知途六阶段流水线（实施契约）

版本：v1.0  
定稿：2026-11-12  
状态：已实现；自动化测试通过，用户验收待完成  
关联事项：AGENT-WAYLUME-PIPELINE-20261112  
关联需求：ZT-R020

## 1. 目标

给知途事项增加一层“流程执行层”，让每个任务都能回答：

- 当前走到哪一步；
- 这一步谁负责、下一步是谁；
- 进入下一步还缺什么；
- 谁推进/退回/阻塞了、依据是什么。

约束：
- 不破坏现有 status、列表、BUG 同步和旧记录；
- 不把流程阶段混进 status；
- 巡检只做分拣建项，不建独立流水线；
- 审计仍只做静态审计，代码审计报告仅供参考，最终代码决定权在开发；
- 需求决定权在 PM；Lead 只做流程推进与流程审计。

## 2. 六阶段

~~~text
1 intake          接单/立项             Lead
2 requirement     需求分析与 PM 结论     PM
3 development     开发                  Dev
4 audit_response  静态审计与开发回应     Auditor → Dev
5 acceptance      PM 验收               PM
6 delivery        交付关闭              Lead
~~~

audit_response 内部子步骤：
- 4a audit：Auditor 输出静态审计报告（ADVISORY）
- 4b response：Dev 逐条回应采纳/不采纳/待确认

requirement 内部子步骤（不单独计阶段）：
- 2a 需求理解/双跑差异
- 2b PM 最终结论

### 模板

初始只提供两个模板：

- dev-standard：适用于 task、work
- bug-standard：适用于 bug

巡检不提供模板；巡检发现后按类型创建事项并启动对应模板。

### dev-standard 阶段定义

| 阶段 | label | submit roles | advance roles | required outputs | next | rework targets |
|---|---|---|---|---|---|---|
| intake | 接单/立项 | lead | lead | scope, decision_owner | requirement | — |
| requirement | 需求分析与PM结论 | pm | pm, lead | requirement_brief, acceptance_criteria, pm_decision | development | intake |
| development | 开发 | dev | dev, lead | dev_summary, branch, self_check | audit_response | requirement |
| audit_response | 静态审计与开发回应 | auditor, dev | lead | audit_report, dev_response | acceptance | development |
| acceptance | PM验收 | pm | pm, lead | pm_acceptance | delivery | development |
| delivery | 交付关闭 | lead | lead | delivery_evidence | — | acceptance |

bug-standard 复用同一套阶段，label 可改为 BUG 语境，但阶段 id 保持一致，避免前端分叉。

## 3. 数据模型

### 3.1 记录字段

在现有记录上增加：

~~~json
{
  "workflow_template_id": "dev-standard",
  "workflow_stage": "development",
  "workflow_stage_status": "active",
  "workflow": {
    "template_id": "dev-standard",
    "template_version": 1,
    "stage": "development",
    "stage_status": "active",
    "substage": null,
    "started_at": "2026-11-12T11:00:00+08:00",
    "updated_at": "2026-11-12T12:00:00+08:00",
    "rework_count": 0,
    "lock": {
      "stage": "development",
      "actor": "gemini",
      "role": "dev",
      "session_id": null,
      "acquired_at": "2026-11-12T12:00:00+08:00",
      "heartbeat_at": "2026-11-12T12:10:00+08:00",
      "expires_at": "2026-11-12T12:40:00+08:00"
    },
    "gates": {
      "development": {
        "status": "active",
        "actor": "gemini",
        "role": "dev",
        "at": "2026-11-12T12:00:00+08:00",
        "note": "实现完成",
        "evidence": ["branch", "diff"],
        "outputs": {
          "dev_summary": "...",
          "branch": "local/...",
          "self_check": "..."
        }
      }
    },
    "history": [
      {
        "at": "2026-11-12T11:00:00+08:00",
        "action": "start",
        "stage": "intake",
        "actor": "lead",
        "role": "lead",
        "note": "启动标准流程",
        "evidence": []
      }
    ]
  }
}
~~~

- workflow_stage / workflow_stage_status 是列表查询和卡片展示用的摘要字段。
- workflow.history 可能增长，列表 payload 不返回完整 history；详情接口返回。
- gates 用于保存每个阶段的输出/证据；进入下一阶段前必须满足模板 required outputs。
- rework_count 记录退回次数。
- 旧记录没有 workflow 时正常读取；不强制迁移。

### 3.2 状态映射

status 保持粗粒度，阶段变化时同步到合理值：

| stage | 默认 status |
|---|---|
| intake | 保持 pending/in_progress |
| requirement | in_progress |
| development | developing |
| audit_response | in_progress |
| acceptance | awaiting_acceptance |
| delivery | work/bug: pending_release；task: in_progress |
| close | completed |

- blocked 是旁路：设置 status=blocked，记录 blocked_from_stage；解除后回原阶段并按映射恢复 status。
- cancelled/archived 终止流程，不再允许推进。
- 阶段映射由服务端统一计算，前端不自行猜。

## 4. 模板与转换规则

模板是代码内固定定义，版本化；初始放 workflow.py（新模块），供 dashboardctl.py 和 server.py 复用。

每个 stage 定义：
~~~json
{
  "id": "audit_response",
  "label": "静态审计与开发回应",
  "short": "审计",
  "submit_roles": ["auditor", "dev"],
  "advance_roles": ["lead"],
  "required_outputs": ["audit_report", "dev_response"],
  "substeps": [
    {"id": "audit", "label": "静态审计", "role": "auditor"},
    {"id": "response", "label": "开发回应", "role": "dev"}
  ],
  "next": "acceptance",
  "rework_to": ["development"]
}
~~~

转换规则：
1. start：无 workflow 的记录启动模板，stage=intake，写 history。
2. submit：记录当前 stage 的 outputs/note/evidence；角色必须属于 submit_roles；不改变 stage。
3. advance：当前 stage 的所有 required_outputs 必须已提交；角色必须属于 advance_roles；目标必须是 next。
4. rework：目标必须属于当前 stage rework_to；角色必须属于 advance_roles；rework_count+1，写 history。
5. block：stage 不变，stage_status=blocked，status=blocked；记录原因。
6. unblock：恢复 stage_status=active 和映射 status。
7. close：只允许 delivery 阶段，且必须已提交 delivery_evidence；status=completed，stage_status=done。
8. 所有写操作必须 CAS：expected_revision 与当前 record revision 一致，否则 409。
9. 所有转换写 history；不覆盖旧 history。
10. 禁止从终态（completed/cancelled/archived）推进。
11. claim：锁定当前阶段，TTL 30 分钟；同一阶段已有其他 actor 的有效锁时返回 stage_locked。
12. submit/advance 在没有锁时自动 claim；子步骤角色完成后自动释放锁，避免 auditor/dev 互相锁死。
13. heartbeat 续锁；release 释放；推进到下一阶段时自动释放旧锁。
14. 锁有效时 effective status 使用阶段映射，避免“有 agent 在干活但面板显示搁置”。

### 4.1 角色校验

- 角色名固定：lead、pm、dev、auditor。
- 这是本地协作约束，不是身份认证；用于防止 Agent 越权推进，真实责任人仍记录在 actor。
- Lead 可以推进流程，但不能替 PM 填写 pm_decision/pm_acceptance，也不能替 Dev 填写 dev_response；服务端不校验内容作者，但 UI/提示词必须明确，审计 history 可追溯。

## 5. CLI 契约

在 dashboardctl.py 增加子命令：

~~~sh
workflow templates
workflow show <ID>
workflow start <ID> --template dev-standard --actor-role lead --actor lead [--expected-revision N]
workflow claim <ID> --actor-role dev --actor gemini [--expected-revision N]
workflow heartbeat <ID> --actor-role dev --actor gemini [--expected-revision N]
workflow release <ID> --actor-role dev --actor gemini [--expected-revision N]
workflow submit <ID> --output key=value --output key2=value2 \
  --actor-role dev --actor gemini --note "..." --evidence "..." [--expected-revision N]
workflow advance <ID> --actor-role dev --actor gemini --note "..." --evidence "..." [--expected-revision N]
workflow rework <ID> --to development --actor-role pm --actor xiao-zhi --note "..." --evidence "..." [--expected-revision N]
workflow block <ID> --actor-role lead --actor lead --note "原因" [--expected-revision N]
workflow unblock <ID> --actor-role lead --actor lead --note "解除原因" [--expected-revision N]
workflow close <ID> --actor-role lead --actor lead --note "..." --evidence "..." [--expected-revision N]
~~~

约定：
- --output 可重复；值按字符串保存。
- submit 输出到当前 stage 的 gates[stage].outputs，并写 history action=submit。
- advance 成功后返回 record、workflow、revision。
- 错误码用 ValueError 风格，服务端映射 400/409：
  - workflow_not_started
  - workflow_terminal
  - invalid_stage
  - invalid_transition
  - invalid_role
  - missing_outputs
  - revision_conflict
  - workflow_blocked

## 6. HTTP 契约

### 6.1 模板

~~~text
GET /api/workflow/templates
GET /api/workflow/templates/<id>
~~~

响应：
~~~json
{"templates": [{"id": "dev-standard", "version": 1, "name": "...", "stages": []}]}
~~~

### 6.2 单事项 workflow

~~~text
GET /api/items/<ID>/workflow
~~~

响应：
~~~json
{
  "record_revision": 12,
  "store_revision": 3550,
  "workflow": {},
  "template": {}
}
~~~

workflow 为完整对象（含 history）；template 为模板定义。旧记录无 workflow：workflow null，HTTP 200。

### 6.2.1 证据全文（只读）

~~~text
GET /api/items/<ID>/evidence-file?path=<URL-encoded evidence string>
~~~

仅允许读取该事项 `workflow.gates[*].evidence` 与 `workflow.history[*].evidence` 中**逐字记录过**的路径；请求值必须与记录值一致（去除首尾空白后比较）。允许根目录为当前数据目录、`~/.waylume`、`/tmp`（含 macOS `/private/tmp`）与项目仓库；相对路径先按项目仓库、再按数据目录解析。拒绝包含 `..` 的路径和解析后逃逸出允许根目录的路径（含符号链接）。文件必须是普通文件、只读、≤128 KiB、UTF-8 文本。

成功返回 `text/plain; charset=utf-8` 原文；未记录/不存在/目录返回 404，超限或非文本返回 415。该接口只读，不写 events、不修改 evidence。

### 6.3 写操作

~~~text
POST /api/items/<ID>/workflow/start
POST /api/items/<ID>/workflow/claim
POST /api/items/<ID>/workflow/heartbeat
POST /api/items/<ID>/workflow/release
POST /api/items/<ID>/workflow/submit
POST /api/items/<ID>/workflow/advance
POST /api/items/<ID>/workflow/rework
POST /api/items/<ID>/workflow/block
POST /api/items/<ID>/workflow/unblock
POST /api/items/<ID>/workflow/close
~~~

请求体统一：
~~~json
{
  "expected_revision": 12,
  "actor_role": "dev",
  "actor": "gemini",
  "note": "...",
  "evidence": ["..."],
  "outputs": {"key": "value"},
  "to": "development",
  "template_id": "dev-standard"
}
~~~

成功响应：
~~~json
{"revision": 13, "record": {}, "workflow": {}}
~~~

错误：
- 400：invalid_transition / invalid_role / missing_outputs / invalid_stage
- 409：revision_conflict
- 404：record_not_found

## 7. 列表 payload

/api/dashboard 的每条记录增加摘要字段：

~~~json
{
  "workflow_template_id": "dev-standard",
  "workflow_stage": "development",
  "workflow_stage_status": "active",
  "workflow_stage_index": 3,
  "workflow_stage_label": "开发",
  "workflow_stage_short": "开发",
  "workflow_rework_count": 0
}
~~~

列表不返回完整 workflow.history，避免 payload 膨胀；详情按需请求 §6.2。

## 8. UI 契约

### 8.1 详情卡

在事项详情标题和“交接信息”之间加入“流水线”section：

- 桌面端横向 6 步 stepper；窄屏纵向/可横向滚动。
- 每一步显示序号、短 label、owner 角色。
- 状态：done 实心/勾选；active 高亮 + 轻脉冲；pending 弱化；blocked 红色/警示；rework 橙色/回退标记。
- 当前 stage 下方显示：
  - 当前阶段：开发 · 负责人：Dev · 下一步：静态审计
  - 锁定状态：已锁定 · Gemini · Dev · 剩余 25 分钟 / 未锁定 · 等待 Dev 接手 / 锁已过期 · 可接管
  - gate checklist：required outputs 已完成/缺失
  - 最近一条 note/evidence
- stepper 只保留六个主阶段；第 4 阶段的 4a 静态审计 / 4b 开发回应、第 2 阶段的 需求理解 / PM结论 放在当前阶段面板内显示，避免六个窄列里再塞子状态。
- 阶段历史折叠：时间、动作、actor、角色、note、evidence。
- 旧记录显示“未启用标准流程”，提供“启用标准流程”入口（只调用 start API，不自动填内容）。

### 8.2 卡片

- 事项卡片在状态 pill 旁增加阶段 chip：接单 / 需求 / 开发 / 审计 / 验收 / 交付。
- 只对有 workflow 的记录显示；无 workflow 不显示空 chip。
- chip 颜色与详情 stepper 一致，暗色主题适配。

### 8.3 动画

- stepper 进入：stage 依次淡入/上移，总时长 <= 350ms。
- 当前阶段圆点：轻微脉冲 1.8s 循环；只动画 opacity/transform，不触发布局。
- 阶段切换后：旧 stage 收缩、新 stage 高亮，使用 CSS transition。
- 尊重 prefers-reduced-motion: reduce，关闭循环和位移。
- 不使用 JS requestAnimationFrame 长循环；纯 CSS 动画优先。
- 不能阻塞 detail 打开、滚动或键盘操作。

### 8.4 可访问性

- stepper 使用 ol/li，当前项 aria-current="step"。
- 状态不能只靠颜色：accent + 文本/图标。
- 键盘可聚焦阶段节点，Enter 展开该阶段 gate/历史。
- 暗色/亮色均满足可读性。

## 9. 兼容与迁移

- 旧记录不改数据；workflow 缺省 null。
- 新 task/work 默认 dev-standard，bug 默认 bug-standard；只有显式 start 才写入 workflow。
- 旧记录 start 时允许指定当前 stage；默认按 status 映射：
  - completed → delivery（只读，不再推进）
  - awaiting_acceptance → acceptance
  - developing → development
  - blocked → intake + blocked
  - 其它 → intake
- 不自动批量迁移；不修改旧 history/status。
- backend.render_context 增加 workflow 摘要（stage/status/next/最近 gate），供 Agent 读取。

## 10. 验收

后端：
- workflow.py 模板/转换单测；
- dashboardctl 命令测试：start/submit/advance/rework/block/unblock/close、角色拒绝、缺失 outputs、CAS、旧记录；
- server API 测试：GET 模板/workflow、POST 流转、409。
- 全部现有 123 个 Python 测试保持通过。

前端：
- dashboard-model 测试覆盖 workflow summary/step 计算（若新增 helper）。
- test_frontend_contract.py 覆盖新 API/字段。
- UI fixture 验证：旧记录、active、blocked、rework、stage 4 substeps、暗色主题。
- 动画 reduced-motion 检查。

兼容：
- 不修改现有 status 枚举语义；
- 不覆盖未提交改动；
- 不 commit/push；
- 不重启用户正在使用的工作台服务。

## 11. 非目标

- 不做用户登录/权限系统；
- 不做自动 Agent 调度器；
- 不做巡检专属流水线；
- 不把代码审计升级为强制 gate（审计仍仅供参考，开发决定是否修改）；
- 不重写现有详情卡的所有 section。
