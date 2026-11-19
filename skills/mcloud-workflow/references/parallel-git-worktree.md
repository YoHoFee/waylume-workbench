# 并行 Git 工作区协议（Worktree）

状态：**所有从知途工作台领取 mCloud 代码任务的 Agent 必读**。本文件是 `~/.dsh/WORK_ASSISTANT.md` §7 的执行扩展；§7 的分支基线、命名、禁止推送、合并方式继续生效，本文件只增加“并行工作区”规则，不另立一套分支制度。

## 0. 一句话规则

**主 checkout 只做集成；每个需求/BUG 一个独立本地分支 + 一个独立 worktree；一个 worktree 只给一个代码 writer；最后统一合并到 develop。**

## 1. 角色与所有权

| 角色 | 工作位置 | 职责 |
|---|---|---|
| Lead / 集成 owner | 主 checkout 或专用 integration worktree | 分配分支/worktree、合并、构建、推送、清理 |
| Task dev writer | 自己的 worktree | 只在自己分支改代码、提交、报告 |
| Auditor / PM / 只读分析 | 自己的 worktree 或主 checkout 只读 | 不切换分支、不 clean、不改文件 |

- **同一任务同一时间只能有一个代码 writer。**
- Lead 可以在一个 integration worktree 中串行合并多个任务，但不要在主 checkout 直接写业务代码。
- 同一台真机/模拟器同一时间只能有一个构建/验收 owner。

## 2. 分支命名

保留 `~/.dsh/WORK_ASSISTANT.md` §7 的 `local/` 前缀和“不推远端”规则。

| 任务 | 分支名 |
|---|---|
| 需求 | `local/<简短需求名>`（沿用现有规则） |
| BUG | `local/BUG<BUG编号>-<简短说明>` |
| 工作台 work 事项 | `local/work-<record_id>-<简短说明>` |

分支名可以含中文；**worktree 目录名必须用 ASCII**，建议：

```text
~/.codex/worktrees/mcloud-<record_id>-<short-ascii-slug>
```

## 3. 创建 worktree

由 Lead 或任务 owner 在**主 checkout**执行，基于最新干净基线：

```sh
cd /Users/mr.yao/Documents/Project/mcloud_ios_client_retry
git fetch origin mCloud_13.4.2_develop

# 新分支
git worktree add -b "local/BUG20261118716835-语音动效" \
  "$HOME/.codex/worktrees/mcloud-BUG20261118716835" \
  origin/mCloud_13.4.2_develop

# 分支已存在时
git worktree add "$HOME/.codex/worktrees/mcloud-BUG20261118716835" \
  "local/BUG20261118716835-语音动效"
```

规则：

- 分支必须从最新 `origin/mCloud_13.4.2_develop`（或任务明确指定的基线）切出，禁止从当前 HEAD、其他需求分支、integration 分支切出。
- 一个 worktree 只检出该任务的唯一分支；不能和其他 worktree 共用同一分支。
- 一个仓库里不重复创建同一分支的第二个 worktree；Git 会拒绝，不要用强制绕过。
- 创建后立即把分支、基线、worktree 路径写入该工作台事项的 context/progress。

**Gemini 桥接强制参数**：派发开发任务时必须传
`--worktree <路径> --branch <分支>`；`gemini-agent` 对 `--workflow-role dev` 缺少 worktree 会直接失败，不允许回落到主 checkout。

## 4. 开始开发前必须确认

每个 Agent 开工前检查事项上下文是否包含：

```markdown
## Git 工作区
- record_id: <工作台事项 ID>
- branch: local/...
- base: origin/mCloud_13.4.2_develop@<sha>
- worktree: ~/.codex/worktrees/mcloud-...
- owner: <agent/session>
- build_owner: <构建/真机 owner>
```

- 如果缺少这些字段：**不要直接改代码**。由 Lead 先分配，或按本节创建后补写；不要凭记忆猜分支。
- 如果发现分支已被另一个 worktree 检出，停止并报告，不要抢切、不要删除。
- 如果 worktree 路径不在预期的 `~/.codex/worktrees/` 下，先确认是否历史目录；不要在主 checkout 里新建代码改动。

## 5. 开发中规则

1. **只在自己的 worktree 里操作**；不切到其他 worktree，不使用主 checkout 写业务代码。
2. 不使用跨 worktree 的 `git clean -fd`、`git reset --hard`、`git stash`、`git checkout` 去改别人的目录。
3. 小步提交，提交信息包含任务/BUG 编号；不要长期保留大量未提交改动。
4. 发现需要改共享文件（见 §7）时，先停下与 Lead 协调，不要各自并发改。
5. 完成或阻塞时，把分支、commit、worktree、验证结果写回原工作台事项，不要只留在聊天里。

## 6. 构建、Pods 与设备隔离

worktree 只隔离源码，不自动隔离构建环境：

- 每个 worktree 使用独立的 `Pods` 和 `Podfile.lock` 副本；不要在多个 worktree 同时执行 `pod install`。
- 每个 worktree 使用独立 `-derivedDataPath`，不要共用同一个 DerivedData。
- 个人调试 workspace / `prepare.rb` 必须指向该 worktree 的源码根，不能继续写死主 checkout；构建前先确认 source root。
- 同一时间只允许一个 owner 构建、安装、操作真机/模拟器；其它 Agent 只读或等待。
- 共享 CocoaPods 缓存可以共用，但安装过程要串行，避免同时改缓存。

## 7. 共享文件单点负责

以下文件/目录即使 worktree 隔离，也会在合并时冲突，必须由 Lead 或指定 owner 统一改：

- `mCloud_iPhone/mCloud_iPhone.xcodeproj/project.pbxproj`
- `Podfile` / `Podfile.lock`
- 资源索引、全局 xcconfig、工程级 scheme
- 公共头文件/公共基础库的接口

任务需要新增文件或资源时：
- 在自己的 worktree 完成源码/资源本身；
- 把 pbxproj 的登记需求报告给 Lead；
- 由 Lead 在集成时统一登记、解决冲突，再构建。不要两个 Agent 同时改 pbxproj。

## 8. 合并与推送

合并只由 Lead/集成 owner 在干净的 integration worktree 或主 checkout 执行：

1. `git fetch origin mCloud_13.4.2_develop`，确认基线。
2. 按任务顺序 `git cherry-pick <branch 上的 commit>`；不要默认生成 `Merge branch 'local/...'`。
3. 在集成后的代码上构建/验收；失败则停止，不推送。
4. 只有构建/验收通过后，才把集成分支快进/更新到 `mCloud_13.4.2_develop` 并推送远端。
5. 不把 `local/` 或中文分支推到远端；不使用 `git push --force` 改 develop。

## 9. 清理

任务合并或明确废弃后：

```sh
git -C /Users/mr.yao/Documents/Project/mcloud_ios_client_retry worktree remove \
  "$HOME/.codex/worktrees/mcloud-..."
git -C /Users/mr.yao/Documents/Project/mcloud_ios_client_retry branch -d "local/..."
```

- 只删除已合并/已废弃且没有其他 worktree 检出的分支。
- 删除前确认没有 Agent 正在使用；不确定就先只移除 worktree，保留分支。
- 清理结果写回工作台事项。

## 10. 并发矩阵

| 场景 | 是否可并发 | 规则 |
|---|---|---|
| 不同需求/BUG，不同模块 | ✅ | 各自 worktree + 分支 |
| 不同任务，但都要改 pbxproj/Podfile | ⚠️ | 源码可并行，共享文件由 Lead 统一集成 |
| 同一模块同一文件 | ❌ | 一个代码 writer；其余只读 |
| 多个 Agent 构建同一工程 | ❌ | 独立 DerivedData 也需串行开始/结束，设备锁 |
| 真机/模拟器安装/验收 | ❌ | 同一时间一个设备 owner |
| 多个只读审计 | ✅ | 不切分支、不写文件、不 clean |

## 11. 任务上下文模板

Lead 在派发开发任务时，必须把下面内容放进 prompt/工作台 context；这是 Agent 自动获知分支规则的唯一入口：

```markdown
## Git 工作区（必读）
- record_id: <ID>
- branch: local/...
- base: origin/mCloud_13.4.2_develop@<sha>
- worktree: ~/.codex/worktrees/mcloud-...
- owner: <agent/session>
- build_owner: <owner>
- shared_files: <none|pbxproj|Podfile|...>
- integration_owner: lead
```

Agent 收到任务后先读本文件；若上述字段缺失，先向 Lead 补分配，不要在错误的目录开始开发。

## 12. 开工检查清单

- [ ] 工作台事项 ID 已解析，不是重复事项
- [ ] 分支从最新 `origin/mCloud_13.4.2_develop` 切出
- [ ] 分支只被当前 worktree 检出
- [ ] worktree 路径在 `~/.codex/worktrees/` 且已写入事项
- [ ] 当前 worktree 有独立 Pods/DerivedData 计划
- [ ] 共享文件（pbxproj/Podfile）需要改时已通知 Lead
- [ ] 构建/真机 owner 已确认
- [ ] 完成时已提交并报告 branch/commit/worktree/验证
