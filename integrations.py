"""Manual, read-only document-tool health checks with secret-safe persistence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import waylume_paths


PROVIDERS = ("feishu", "tencent_docs", "kdocs", "figma", "jira", "linear", "git", "iphone_use")
_NAMES = {"feishu": "飞书", "tencent_docs": "腾讯文档", "kdocs": "金山文档", "figma": "Figma"}
_PROMPTS = {
    "feishu": "检查当前安装的 lark-cli 与飞书 MCP 只读能力；先执行本地身份/连通性检查，再读取一份用户明确指定的文档元信息验证权限。不要输出或记录凭据。",
    "tencent_docs": "检查当前环境是否有腾讯文档的只读连接器；若有，用用户明确指定的表格执行一次只读元信息读取并记录结果；若无，说明缺少的官方工具/授权，不要改写文档。不要输出凭据。",
    "kdocs": "检查 kdocs-cli 认证与金山文档只读搜索/元信息读取能力；用无写入的请求验证服务可用，再单独确认用户指定文档权限。不要读取无关正文，不要输出凭据。",
    "jira": "检查 Jira 只读连接器和用户指定项目的读取权限，不输出凭据。",
    "linear": "检查 Linear 只读连接器和用户指定团队或事项的读取权限，不输出凭据。",
    "git": "检查 Git 工具及用户指定仓库的只读访问，不输出凭据。",
    "iphone_use": "请帮助安装和配置 iPhone Use。先检查当前宿主是否已提供 iphone-use-setup Skill 和 PUA 工具，并调用 pua_setup(status)；复用现有配置、构建和活动任务。若插件缺失，阅读 https://github.com/zhongerxin/iPhone-use 的 README，按其安装说明完成安装并重连会话；安装后重新打开宿主会话，确认 iphone-use-setup Skill 和 PUA 工具入口可用；不启动或检查手机服务，不操作设备，不索取凭据。",
    "figma": "检查当前宿主 Agent 是否提供 Figma 只读 MCP 工具；本地工作台服务不能直接探测或调用宿主 MCP。若工具可用，请按用户指定的文件/节点执行一次只读元信息读取，并只记录连接状态和验证范围；工具不可用时报告宿主工具依赖。仅账号身份检查不能证明目标文件权限，不要写入 Figma、输出或保存凭据。",
}
_AGENT_STATUSES = {"passed", "failed", "unknown", "not_configured"}


def _safe_text(value: Any, limit: int = 240) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_integration_evidence")
    value = " ".join(value.split())
    # Evidence is a short human-readable outcome, never a place for raw logs,
    # URLs, tokens, cookies, or document content.
    if len(value) > limit or re.search(r"(?i)(bearer\s+|token\s*[:=]|secret\s*[:=]|cookie\s*[:=]|https?://|[A-Za-z0-9_-]{32,})", value):
        raise ValueError("unsafe_integration_evidence")
    return value


def _status_path() -> Path:
    return waylume_paths.data_dir() / "integrations" / "status.json"


def _probe_environment() -> dict[str, str]:
    """Include standard per-user and package-manager bins in service probes."""
    env = dict(os.environ)
    extra = [str(Path.home() / ".local" / "bin"), "/usr/local/bin", "/opt/homebrew/bin"]
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), *extra])
    return env


def _find_cli(name: str) -> str | None:
    return shutil.which(name, path=_probe_environment()["PATH"])


def _blank(provider: str) -> dict[str, Any]:
    command = {"feishu": "lark-cli", "kdocs": "kdocs-cli"}.get(provider)
    present = bool(command and _find_cli(command))
    if provider == "tencent_docs":
        state = "unknown"
        reason, next_step, missing = "host_connector_not_probeable", "本地服务没有该工具的安全命令行探针；由宿主 Agent 使用官方只读连接器完成一次实际读取后再确认。", ["可调用的只读连接器", "指定文档的读取权限"]
    elif provider == "figma":
        state = "unknown"
        reason, next_step, missing = "host_agent_check_required", "本地服务不能直接探测宿主 MCP；请由宿主 Agent 检查当前会话是否提供 Figma 只读工具，并按指定文件/节点验证权限。", ["宿主 Agent 的 Figma MCP 只读调用", "指定文件/节点的读取权限"]
    elif provider in {"jira", "linear", "git"}:
        state = "not_configured"
        reason, next_step, missing = "host_agent_check_required", "请由宿主 Agent 使用对应工具执行授权范围内的真实只读验证。", ["宿主只读工具", "目标资源访问验证"]
    elif present:
        state = "unknown"
        reason, next_step, missing = "manual_check_not_run", f"已检测到 {command}；点击重新检查执行只读连通性探测。", ["文档级读取权限仍需按指定文档单独验证"]
    else:
        state = "not_configured"
        reason, next_step, missing = "tool_not_installed", f"当前电脑未发现 {command}；请在宿主 Agent 安装官方工具并完成授权，再执行真实只读验证。", [command or "官方只读连接器", "用户授权"]
    return {"status": state, "checked_at": None, "reason_code": reason,
            "missing_capabilities": missing, "safe_next_step": next_step,
            "prompt_context": _PROMPTS[provider]}


def _load() -> dict[str, Any]:
    try:
        value = json.loads(_status_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    items = {provider: {**_blank(provider), **(value.get(provider) if isinstance(value.get(provider), dict) else {})}
             for provider in PROVIDERS}
    # Old versions recorded an unsupported local probe as "not configured".
    # It says nothing about the host connector, so do not present it as fact.
    for provider in ("tencent_docs", "figma"):
        item = items[provider]
        if item.get("verification_method") == "local_readonly_probe" and item.get("reason_code") in {"host_connector_not_probeable", "host_agent_check_required"}:
            item.update({"status": "unknown", "checked_at": None, "verification_method": None})
    return items


def _save(items: dict[str, Any]) -> None:
    path = _status_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = (json.dumps(items, ensure_ascii=False, indent=2) + "\n").encode()
    fd, name = tempfile.mkstemp(prefix=".status.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def status() -> dict[str, Any]:
    return {"providers": _load()}


def report_agent_check(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a short result observed by a host agent through a real read-only connector."""
    if provider not in PROVIDERS:
        raise ValueError("unknown_provider")
    allowed = {"status", "tool", "agent", "read_only", "evidence_summary", "missing_capabilities", "reason_code"}
    if set(payload) - allowed:
        raise ValueError("unsupported_integration_fields")
    state = payload.get("status")
    if state not in _AGENT_STATUSES:
        raise ValueError("invalid_integration_status")
    if state == "passed" and payload.get("read_only") is not True:
        raise ValueError("passed_requires_readonly_evidence")
    tool = _safe_text(payload.get("tool"), 80)
    agent = _safe_text(payload.get("agent"), 80)
    summary = _safe_text(payload.get("evidence_summary"), 240)
    reason = payload.get("reason_code", "agent_reported")
    if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", reason):
        raise ValueError("invalid_integration_reason")
    missing = payload.get("missing_capabilities", [])
    if not isinstance(missing, list) or len(missing) > 12:
        raise ValueError("invalid_integration_capabilities")
    missing = [_safe_text(item, 100) for item in missing]
    item = {
        "status": state,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reason_code": reason,
        "missing_capabilities": missing,
        "verification_method": "host_agent_readonly_report",
        "safe_next_step": "由宿主 Agent 根据提示词继续检查；不要提交原始凭据或文档正文。" if state != "passed" else "只读连接已由宿主 Agent 实测通过；具体文档权限仍按目标文档核验。",
        "prompt_context": _PROMPTS[provider],
        "evidence": {"tool": tool, "agent": agent, "read_only": bool(payload.get("read_only")), "summary": summary},
    }
    values = _load()
    values[provider] = item
    _save(values)
    return {"provider": item}


def _probe_feishu() -> tuple[str, str, list[str], str]:
    command = _find_cli("lark-cli")
    if not command:
        return "unknown", "tool_not_installed", ["lark-cli 或可用飞书只读连接器"], "请安装官方飞书工具并完成授权后重试。"
    try:
        run = subprocess.run([command, "doctor"], capture_output=True, text=True, timeout=18, check=False, env=_probe_environment())
        if run.returncode != 0:
            return "failed", "readonly_probe_failed", ["飞书只读连通性验证"], "请在宿主 Agent 检查 lark-cli 的认证和网络，然后重新运行只读验证。"
        payload = json.loads(run.stdout)
        checks = payload.get("checks", []) if isinstance(payload, dict) else []
        network = [item for item in checks if isinstance(item, dict) and any(key in str(item.get("name", "")).lower() for key in ("endpoint", "network", "mcp"))]
        if payload.get("ok") is True and network and all(item.get("status") == "pass" for item in network):
            return "passed", "readonly_probe_passed", ["具体文档访问权仍需按目标文档核验"], "飞书服务只读连通性已验证；实际文档权限按所选文档继续核验。"
        return "failed", "readonly_probe_incomplete", ["飞书服务端只读连通性"], "本次探测未能确认飞书服务端连通；请检查本机工具授权/网络后重试。"
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return "failed", "readonly_probe_unavailable", ["飞书服务端只读连通性"], "只读检查未完成；请检查 lark-cli 和网络后重试。"


def _probe_kdocs() -> tuple[str, str, list[str], str]:
    command = _find_cli("kdocs-cli")
    if not command:
        return "unknown", "tool_not_installed", ["kdocs-cli 或可用金山文档只读连接器"], "请安装金山文档官方工具并完成授权后重试。"
    # A unique filename-only search is read-only, requests at most one result,
    # and avoids fetching document contents or exposing returned metadata.
    marker = "waylume-health-" + secrets.token_hex(8)
    args = {"keyword": marker, "type": "file_name", "page_size": 1}
    try:
        run = subprocess.run([command, "drive", "search-files", json.dumps(args)], capture_output=True, text=True, timeout=20, check=False, env=_probe_environment())
        if run.returncode != 0:
            return "failed", "readonly_probe_failed", ["金山文档只读搜索权限"], "只读搜索未成功；请在宿主 Agent 检查 kdocs-cli 授权和网络。"
        payload = json.loads(run.stdout)
        if isinstance(payload, dict) and not any(payload.get(key) for key in ("error", "errors")) and payload.get("success", True) is not False:
            return "passed", "readonly_probe_passed", ["具体文档正文/元信息权限仍需按目标文档核验"], "金山文档只读搜索已验证；尚未读取任何文档正文。"
        return "failed", "readonly_probe_incomplete", ["金山文档服务端只读搜索"], "只读搜索未返回可确认的成功结果；请检查授权后重试。"
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return "failed", "readonly_probe_unavailable", ["金山文档服务端只读搜索"], "只读检查未完成；请检查 kdocs-cli 和网络后重试。"


def _probe_iphone_use() -> tuple[str, str, list[str], str]:
    """Check the installed plugin bundle only; never start a phone service."""
    cache = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "plugins/cache/iphone-use-local/iphone-use"
    try:
        for manifest in cache.glob("*/.codex-plugin/plugin.json"):
            root = manifest.parent.parent
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if (data.get("name") == "iphone-use" and (root / "skills/iphone-use-setup/SKILL.md").is_file()
                    and (root / "server/iphone_use.py").is_file() and (root / ".mcp.json").is_file()):
                return "passed", "plugin_files_present", ["宿主实际加载未验证"], "已检测到 iPhone Use 插件文件；当前宿主是否加载需单独验证。"
    except (OSError, ValueError, TypeError, AttributeError):
        return "unknown", "plugin_probe_unavailable", ["插件安装状态"], "插件检查未完成；请由宿主 Agent 检查。"
    return "unknown", "plugin_not_found", ["插件安装状态"], "未在已知的 Codex 插件缓存中找到 iPhone Use；其他宿主安装状态未知。"


def _probe_git() -> tuple[str, str, list[str], str]:
    """Verify the Git executable locally; do not contact or alter remotes."""
    command = _find_cli("git")
    if not command:
        return "unknown", "tool_not_found", ["Git 可执行程序"], "当前服务环境未找到 Git，不能据此判断其他宿主的状态。"
    try:
        env = _probe_environment()
        version = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=10, check=False, env=env)
        if version.returncode != 0 or not re.match(r"^git version \d", version.stdout.strip()):
            return "failed", "readonly_probe_failed", ["Git 本机只读验证"], "Git 版本检查未成功。"
        return "passed", "readonly_probe_passed", ["仓库访问及远端权限未验证"], "Git 可运行；仓库访问及远端权限未验证。"
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return "unknown", "readonly_probe_unavailable", ["Git 本机只读验证"], "Git 检查未能运行或超时。"


def _probe_tencent_docs() -> tuple[str, str, list[str], str]:
    """Use the installed plugin's own read-only discovery and account check.

    Only its fixed, English success line is interpreted. Raw output is never
    persisted: it can contain diagnostics from a remote service or runtime.
    """
    node = shutil.which("node")
    cache = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "plugins/cache/akashmahedy-plugins/tencent-docs"
    try:
        candidates = [p for p in cache.glob("*/scripts/setup.mjs") if p.is_file() and re.fullmatch(r"\d+(?:\.\d+){1,3}", p.parent.parent.name)]
        candidates.sort(key=lambda p: tuple(int(n) for n in p.parent.parent.name.split(".")), reverse=True)
    except OSError:
        candidates = []
    if candidates and not node:
        # The installed plugin pins the Node runtime because GUI/LaunchAgent PATH
        # may omit it. Read only the executable path; never inspect credentials.
        try:
            config = json.loads((candidates[0].parent.parent / ".mcp.json").read_text(encoding="utf-8"))
            configured = config.get("mcpServers", {}).get("tencent_docs", {}).get("command", "")
            candidate = Path(configured)
            if candidate.is_absolute() and candidate.name == "node" and candidate.is_file() and os.access(candidate, os.X_OK):
                node = str(candidate)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    if not node or not candidates:
        return "unknown", "local_probe_unavailable", ["腾讯文档插件诊断入口或 Node"], "未找到插件诊断入口或 Node；不能据此判断宿主是否已集成。"
    try:
        run = subprocess.run([node, str(candidates[0]), "--check", "--lang=en"], capture_output=True, text=True, timeout=30, check=False)
        if run.returncode != 0:
            return "failed", "readonly_probe_failed", ["腾讯文档只读连接与账户检查"], "腾讯文档只读检查未成功；请检查插件、授权与网络。"
        match = re.fullmatch(r"Connection OK\. Tools discovered: (\d+); (account checked|discovery only)\.\s*", run.stdout.strip())
        if not match or int(match.group(1)) < 1:
            return "unknown", "readonly_probe_unrecognized", ["可确认的腾讯文档账户检查"], "诊断输出无法确认账户状态；请检查插件版本。"
        if match.group(2) != "account checked":
            return "unknown", "discovery_only", ["腾讯文档账户只读检查"], "已发现工具，但插件未能验证账户。"
        return "passed", "readonly_probe_passed", ["具体文档权限仍需单独验证"], "腾讯文档插件只读发现及账户检查通过；尚未验证具体文档权限。"
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return "unknown", "readonly_probe_unavailable", ["腾讯文档只读检查"], "腾讯文档检查未能运行或超时，请检查插件和网络。"


def check(provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError("unknown_provider")
    if provider in {"figma", "jira", "linear"}:
        # A local service cannot inspect the host Agent MCP tools. Preserve the
        # last host evidence instead of replacing it with a fabricated result.
        existing = _load()[provider]
        return {"provider": existing, "check_outcome": "host_agent_required"}
    if provider == "feishu":
        state, reason, missing, next_step = _probe_feishu()
    elif provider == "kdocs":
        state, reason, missing, next_step = _probe_kdocs()
    elif provider == "tencent_docs":
        state, reason, missing, next_step = _probe_tencent_docs()
    elif provider == "git":
        state, reason, missing, next_step = _probe_git()
    elif provider == "iphone_use":
        state, reason, missing, next_step = _probe_iphone_use()
    else:
        base = _blank(provider)
        state, reason = base["status"], base["reason_code"]
        missing, next_step = base["missing_capabilities"], base["safe_next_step"]
    item = {
        "status": state, "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reason_code": reason, "missing_capabilities": missing,
        "verification_method": "local_readonly_probe",
        "safe_next_step": next_step, "prompt_context": _PROMPTS[provider],
    }
    values = _load(); values[provider] = item; _save(values)
    return {"provider": item}
