# Agent 任务卡信息契约（共享黑板）

> 适用：知途工作台里由 agent 接手的 BUG / 需求 / 委托任务。本文说明 agent 接手时能读到什么、需要写回什么、哪里会冗余，以及任务卡作为“共享黑板”的固定规则。
> 更新：2026-11-20

## 1. 接手时 agent 拿到的信息

| 入口 | 内容 | 用途 |
| --- | --- | --- |
| `dashboardctl.py dispatch-card <ID>` / `GET /api/items/<ID>/dispatch-card` | 当前阶段、阶段状态、下一动作（action/role）、缺失输出、阶段锁、角色绑定、派发指令、并行 Git 要求 | 决定“现在该谁做什么”，派发前必读 |
| `dashboardctl.py task-context <ID>` | 原始诉求（initial_brief）、一句话现状（status_line）、最新简报（latest_brief）、执行进展、最终结果、跟进、证据、关联事项 | 恢复上下文、续做或验收 |
| `GET /api/items/<ID>/context`（详情里的“事项上下文”） | Markdown：当前现状、目标、迭代、流程（六阶段）、阻塞、下一步、备注/证据、状态历史、执行进展、跟进、最终结果、附件 | 给 agent 的完整阅读面 |
| `dashboardctl.py workflow show <ID>` | 阶段、gate 输出、历史、锁 | 需要精确阶段细节时 |
| `handoff_view`（context 响应内） | 交接字段（goal/verified/unverified/next_step/acceptance/blocker/resume_condition）、摘要、缺失项 | 快速判断交接是否完整 |

结论：**身份、目标、当前阶段、下一步、边界、证据、历史都在**，足以独立接手；agent 不需要靠聊天记忆或二次猜测。

## 2. 已知冗余与边界

- `task-context` 同时返回 `record` 与扁平字段（initial_brief/latest_brief/progress/final_result/evidence），字段有重复；以 `record` 为准，扁平字段方便快速读取。
- `latest_brief` 是最近一条进展的全文，会随每次 progress 变化；**不是**给首页看的一句话现状。
- `progress` 与 `history` 会持续增长，context 有 256KB 上限；超限时上下文接口返回 413，需清理/归档旧进展。
- 详情上下文会重复展示部分进展与最终结果，这是为可读性保留的重复，不是数据重复。

## 3. 固定规则：任务结束前的一句话现状

**每个 agent 在把任务标记完成 / 交付结果之前，必须先写一句“当前现状”**，用于任务卡在首页的显示和详情顶部；详细内容仍写在进展/结果里。

- 长度 ≤ 200 字符，单行，不换行；写“现在做到哪、下一步/阻塞是什么”，不写过程流水账。
- 写入方式：
  - `dashboardctl.py task-result <ID> --summary "一句话现状" --message "详细结果…"` —— **`--summary` 必填**，缺失会报 `status_line_required`；
  - 过程中可随时更新：`dashboardctl.py progress <ID> --summary "一句话现状" --message "…"`；
  - 单独修正：`dashboardctl.py summary <ID> "一句话现状"`。
- 展示位置：首页任务卡摘要（优先于 latest_brief）、详情标题下的“当前现状”块、agent 上下文 Markdown 的“当前现状（一句话）”。

## 4. Agent 与任务卡的交互顺序

1. `dispatch-card` 读当前阶段与下一动作；未启动流程时按卡片决定是否 `workflow start`。
2. `task-context` + 事项上下文恢复现场；检查 `handoff` 的 missing 字段。
3. 执行中 `progress` 记录里程碑（可带 summary）。
4. 结束前 `task-result --summary`（必填）+ 证据，更新 `handoff`（verified/unverified/next_step/acceptance）。
5. 不直接改 `status` 冒充阶段推进；阶段推进走 workflow；交付/关闭只由用户手动完成。

## 5. 审计结论与后续

- 足够：任务身份、阶段、输出要求、锁、证据、历史、交接字段完整，agent 能判断“该做什么、做到哪、还缺什么”。
- 已补：一句话现状字段与强制规则（本文件第 3 节），首页/详情/上下文三处一致。
- 待观察：progress 增长导致 context 超限；后续可在上下文里只保留最近 N 条进展 + 链接到完整历史。
