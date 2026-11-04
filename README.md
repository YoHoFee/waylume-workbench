# 知途（Waylume）工作台

知途是多 Agent 共用的任务台与上下文中心。工作需求、BUG 与用户委托任务共用一套本地数据，分别保留来源、状态、跟进和可交接记录；Agent 可从事项恢复上下文并回写进展。企业私有资料和个人记录保存在用户数据目录，不进入本仓库。

## 安装工作助理 Skill

需要 macOS、Git、Python 3.9 或更新版本（只用标准库）。先克隆本仓库，再运行检查；检查不会写入文件：

克隆地址使用 GitHub SSH。新电脑需先获得仓库访问权限，并配置可用的 SSH Key；若没有权限或 SSH 未配置，先按 GitHub 提示完成授权，安装器不会代为配置凭据。

```sh
git clone git@github.com:YoHoFee/waylume-workbench.git waylume-workbench
cd waylume-workbench
python3 scripts/install_waylume.py --check
```

检查会区分宿主程序是否存在、Skill 文件是否安装、运行时是否能发现它，并回报本机工作台服务状态。确认目标后运行完整安装：

```sh
python3 scripts/install_waylume.py --install
```

完整安装安全地初始化空数据区、为本机已检测到的 Codex/Antigravity/DSH 链接 Skill，并安装、启动当前用户的 macOS LaunchAgent。本命令不会导入旧工作台数据；旧数据仅在用户明确选择迁移后导入。也可以指定宿主：`python3 scripts/install_waylume.py --install --hosts codex antigravity`。安装前会验证选定的数据目录，拒绝把数据写入代码仓库或其父目录。安装器只在目标为空时创建指向本仓库 Skill 的符号链接。遇到同名文件或指向其他位置的链接会停止并报告，不覆盖、不删除已有文件，也不改系统安全设置。更新本仓库后重跑安装命令即可；若仓库移动，重新运行安装器以刷新链接。

### 宿主状态与实际验证

- **Codex**：检测 `codex` 命令及 `~/.codex/skills/`。安装成功表示文件已链接；重新打开 Agent 后才可验证技能发现与读取。
- **Gemini / Antigravity**：检测 `agy` 命令或 Antigravity 应用，并检查 `~/.gemini/config/skills/`。安装成功表示文件已链接；需重开会话验证实际发现。
- **DeepSeek Harness（DSH）**：检测本机 DSH 应用。按共享 Agent Skills 目录 `~/.agents/skills/` 安装；文件安装与 DSH 当前版本实际读取是两项不同结果。若本机版本没有从该目录加载 Skill，安装器会如实显示“运行时未验证”，不会声称已生效。需在新会话中检查是否发现并加载 `waylume-assistant`。

上述宿主检测只检查列出的命令、应用位置和 Skill 目录，不是全盘搜索，也不能证明 MCP/连接器已登录、获准访问目标文档或当前宿主版本实际加载了 Skill；以检查输出和新会话中的实际验证为准。

安装器不会代替登录、配置连接器、授权 API、安装宿主应用或启动新会话。未登录时宿主可能无法执行外部工作，但本地 Skill 安装本身不需要账号登录。

## 安装器维护

```sh
python3 scripts/install_waylume.py --check
python3 scripts/install_waylume.py --install --hosts codex antigravity
python3 scripts/install_waylume.py --uninstall
python3 manage_service.py status
python3 manage_service.py stop
python3 manage_service.py start
```

卸载会停止并移除本项目拥有的 LaunchAgent，以及仍指向本仓库 Skill 的链接；**个人数据与设置保留**。若 LaunchAgent 或链接不再由本安装器拥有，会拒绝删除。安装前无需备份，因为安装器不覆盖已有目标；如需改变既有安装，先手动备份并确认其来源。失败时查看输出的目标路径与原因，修正宿主目录/权限后重试；已完成的安全步骤可幂等重跑。`--check` 是只读诊断。

需要在隔离目录演练安装器时，可覆盖目标 home 和数据路径；此参数不会更改当前用户目录：

```sh
python3 scripts/install_waylume.py --check --home /tmp/waylume-home --data-dir /tmp/waylume-data
```

## 数据与设置

默认个人数据目录为 `~/.waylume`（按当前用户主目录展开，不写死用户名）；CLI/服务共同支持 `WAYLUME_DATA_DIR` 覆盖。用户设置保存在系统用户配置目录（macOS：`~/Library/Application Support/Waylume/settings.json`）。新安装仅创建空数据，不复制别台机器的私有事项。旧运行目录只作为可选迁移来源；不会自动迁移或删除，迁移前后由用户确认目标与完整性。已有工作台数据、个人/企业事项、来源、工单附件、截图、日志和认证资料均属于用户数据，不得提交到 Git；仓库只放通用程序与不含真实个人/企业记录的示例和测试数据。企业来源和具体责任范围从用户数据区读取，不能把本机角色/工单写进通用 Skill。

## Skill 内容索引

- [工作助理职责与边界](skills/waylume-assistant/references/role-and-boundaries.md)
- [BUG 分析与处理流程](skills/waylume-assistant/references/bug-workflow.md)
- [失败经验与问题指引](skills/waylume-assistant/references/troubleshooting.md)
- [长期记忆与事项记录政策](skills/waylume-assistant/references/memory-policy.md)
- [Codex、Gemini/Antigravity、DSH 接入](skills/waylume-assistant/references/host-integration.md)

Skill 只说明如何协作，不替代当前用户授权、项目内规则、真实工具状态或公司的原始需求来源。工作台数据/API/界面另由产品代码维护；不要把业务记录写进 Skill 文档。
