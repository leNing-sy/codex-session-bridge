# Codex Session Bridge

[![Release](https://img.shields.io/github/v/release/leNing-sy/codex-session-bridge?label=release)](https://github.com/leNing-sy/codex-session-bridge/releases/latest)
[![Windows Build](https://github.com/leNing-sy/codex-session-bridge/actions/workflows/build-exe.yml/badge.svg)](https://github.com/leNing-sy/codex-session-bridge/actions/workflows/build-exe.yml)
[![License](https://img.shields.io/github/license/leNing-sy/codex-session-bridge)](LICENSE)

在 **Codex、Claude Code 和 OpenCode** 之间迁移本地历史会话，让转换后的会话出现在目标工具的历史列表中，并尽可能保留可见上下文继续对话。

Windows 用户可以直接下载免安装程序，无需安装 Python：

📥 **[下载最新版 `session-convert.exe`](https://github.com/leNing-sy/codex-session-bridge/releases/latest)**

> 当前版本：`v0.4.4`。EXE 由 GitHub Actions 从对应标签的源码自动测试、构建并附加到 Release；目前没有代码签名，Windows SmartScreen 可能显示提示。

> [!CAUTION]
> **转换前请先备份重要会话。** 至少保留来源会话文件；首次使用某个转换方向、客户端刚升级或准备使用 `overwrite` 时，建议同时备份目标端会话目录和状态数据库。
>
> Codex、Claude Code 和 OpenCode 的本地存储格式可能随版本变化。本项目会尽力使用保守的冲突策略和原子写入降低风险，但无法对所有客户端版本、非标准会话或突发中断作出绝对保证。因未备份、强制覆盖、格式变化或不当操作导致的数据异常，需由使用者结合自己的备份进行恢复。不可替代的会话请先用副本验证。

## 🔄 支持范围

| 转换方向 | 转换后的结果 | 当前验证状态 |
|---|---|---|
| OpenCode -> Codex | 自动安装并注册到 Codex 历史列表 | ✅ 已使用真实会话验证导入和续聊 |
| Claude Code -> Codex | 自动安装并注册到 Codex 历史列表 | ✅ 已使用真实历史会话验证重启可见和续聊 |
| Codex -> Claude Code | 自动写入 Claude Code 会话目录；有桌面端模板时同时注册桌面列表 | 🧪 已有自动测试，尚未完成真实 Claude 客户端续聊验证 |
| Codex -> OpenCode | 生成 OpenCode 导出 JSON | 🧪 已有自动测试；需要手动执行 `opencode import` |

这里的“迁移”是迁移**可见对话上下文**，不是迁移账号、订阅、模型状态、权限授权或云端会话身份。

## 🚀 Windows 快速使用

1. 从 [Releases](https://github.com/leNing-sy/codex-session-bridge/releases/latest) 下载 `session-convert.exe`。
2. 按顶部安全提示备份重要会话。
3. 完全退出将被写入的目标应用，避免会话文件或状态数据库被占用。
4. 双击 EXE，选择转换方向，再从最近会话列表中选择一条会话。
5. 转换完成后重新打开目标应用，先确认历史内容完整、消息角色正确，再继续对话。

交互菜单包含四个方向：

```text
1. Codex    -> Claude
2. Claude   -> Codex
3. OpenCode -> Codex
4. Codex    -> OpenCode
```

前三个方向会尽力把结果直接安装到目标工具。`Codex -> OpenCode` 会生成导出文件，并显示需要执行的命令：

```powershell
opencode import "<导出文件路径>"
```

OpenCode 不会自动扫描外部 JSON，因此这一步不能省略。

## 📂 转换后在哪里打开

### Codex

Claude Code 或 OpenCode 转入 Codex 后，工具会创建 Codex rollout，更新 `session_index.jsonl`，并注册桌面端实际使用的 `state_5.sqlite` 会话记录。重新打开 Codex 后，应能在历史列表中看到并继续该任务。

### Claude Code

Codex 转入 Claude Code 后，可在对应项目目录运行：

```powershell
claude -r
```

也可以使用转换结果中的会话 ID：

```powershell
claude --resume <session-id>
```

如果本机存在 Claude 桌面应用及可复用的注册模板，转换器会同时注册桌面历史条目；重新打开桌面应用后生效。没有桌面端模板时，CLI 会话仍会生成，但桌面历史列表注册会跳过。

### OpenCode

OpenCode -> Codex 只读访问 OpenCode 的 SQLite 数据库，不修改源数据库。Codex -> OpenCode 则只生成标准导出 JSON，必须再运行 `opencode import`。

## 💬 为什么转换后的 Codex 会话可以继续聊

简单拼接聊天文本通常只能“看”，不能稳定续聊。Codex Session Bridge 会生成 Codex 所需的会话元数据、用户/助手消息和回合结束事件，并以桌面端能够识别的来源信息注册任务。

`v0.4.4` 还专门处理了几类会破坏续聊的问题：

- Claude Code / OpenCode -> Codex 只导入用户可见输入和助手最终回复，不重放来源端隐藏的 thinking、工具调用和工具结果。
- 过滤 Claude Code 的斜杠命令、本地命令输出、系统提醒、子代理和元记录。
- 过滤 Codex 注入的 `AGENTS.md`、环境信息、浏览器上下文等隐藏消息。
- 去除恢复会话时重复写入但尚未得到回答的用户输入，同时保留真正的重复提问。
- 使用稳定的跨格式会话 ID；同一来源再次转换会命中同一个目标，而不是默认制造副本。
- Claude Code / OpenCode 导入 Codex 时使用桌面端可识别的 `vscode / codex_work_desktop` 来源信息，重启后仍可见。

## 🧭 重复会话和覆盖策略

默认冲突策略是 `skip`：目标会话已经存在时直接跳过，不覆盖、不重复注册，也不创建新副本。

命令行支持四种策略：

| 策略 | 行为 |
|---|---|
| `skip` | 默认。目标存在时不做修改 |
| `update` | 独立转换器无法可靠证明目标端没有新增对话，因此会保守跳过 |
| `overwrite` | 原 ID 覆盖目标，可能丢失目标端续聊内容 |
| `fork` | 生成新 ID，明确创建一份副本 |

除非已经确认目标会话没有需要保留的新内容，否则不要使用 `overwrite`。只有确实需要两份独立会话时才使用 `fork`。

## 📦 会保留什么

主要保留：

- 用户可见消息与助手最终回复
- 对话顺序、标题、原始工作目录和基础时间信息
- Claude Code 与 Codex 之间的 base64 图片消息
- Codex -> Claude Code 时可选的推理摘要

默认不会保留：

- 来源模型的隐藏推理过程和签名
- Claude Code / OpenCode 的内部工具调用链、工具结果和子代理记录
- 原平台的审批、权限、沙箱和运行状态
- 音频等尚未支持的多媒体内容

这样做是有意的：内部执行记录来自另一个运行时，把它们伪装成目标工具的原生历史，不仅容易造成角色错位，还可能让后续请求异常膨胀或无法继续对话。

## 🛡️ 安全说明

- 转换在本机完成，转换脚本本身不上传会话内容。
- OpenCode -> Codex 以只读模式打开 OpenCode 数据库。
- 文件输出使用临时文件后再替换，避免写到一半留下残缺文件。
- 默认 `skip` 冲突策略，防止重复执行时覆盖已有会话。
- 自动化测试只使用隔离的临时目录，不应向真实会话目录写入测试数据。

免安装 EXE 会写入目标工具的会话目录和桌面状态数据库，但它不是完整的备份软件。请先阅读页面顶部的备份与风险提示，并保留可恢复副本。

## ⌨️ 命令行使用

EXE 也可以在终端中运行；如果直接使用源码，把下面的 `session-convert.exe` 换成 `python scripts/session_convert.py`。

```powershell
# 列出最近会话
session-convert.exe list all

# 使用各来源中最新的会话
session-convert.exe codex2claude --latest
session-convert.exe claude2codex --latest
session-convert.exe opencode2codex --latest
session-convert.exe codex2opencode --latest

# 指定输入或来源 ID
session-convert.exe codex2claude <rollout-*.jsonl>
session-convert.exe claude2codex <claude-session.jsonl>
session-convert.exe opencode2codex <ses_会话ID>
session-convert.exe codex2opencode <rollout-*.jsonl>
```

使用 `-o <文件>` 时只生成输出文件，不安装到目标会话目录。所有转换命令都支持：

```powershell
--on-conflict skip|update|overwrite|fork
```

Codex -> Claude Code 还支持：

```powershell
--title <标题>
--claude-cwd <路径>
--reasoning thinking|text|skip
--include-context
```

完整参数、格式说明和限制见 [`scripts/README.md`](scripts/README.md)。

## 🐍 Python 导入器

仓库还包含面向自动化和开发者的 `codex-bridge` CLI。它目前专注于 **OpenCode / Claude Code -> Codex**，提供干跑、安装前备份、安装后验证以及失败回滚，比单文件转换器更适合需要严格检查的导入流程。

需要 Python 3.11 或更高版本：

```powershell
git clone https://github.com/leNing-sy/codex-session-bridge.git
cd codex-session-bridge
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

示例：

```powershell
# 列出来源会话
.venv\Scripts\python -m codex_bridge list-opencode
.venv\Scripts\python -m codex_bridge list-claude

# 不带 --write 时只生成并检查计划，不写入 Codex
.venv\Scripts\python -m codex_bridge import-opencode <session-id>
.venv\Scripts\python -m codex_bridge import-claude <session-id>

# 正式安装
.venv\Scripts\python -m codex_bridge import-opencode <session-id> --write
.venv\Scripts\python -m codex_bridge import-claude <session-id> --write

# 验证已安装会话
.venv\Scripts\python -m codex_bridge verify <codex-session-id>
```

Python 导入器的备份默认位于：

```text
~/.codex/backups/codex-session-bridge/
```

## ✅ 测试与构建

当前测试共 74 项，全部通过，其中 3 项依赖可选加密组件（Windsurf Cascade 解密）的测试在未安装 `cryptography` 时跳过。Windows Release 工作流会在云端安装构建依赖、运行测试、生成单文件 EXE，并对 EXE 执行 `--help` 冒烟检查。

开发者可以运行：

```powershell
python -m unittest discover -s tests -v
```

项目的 Windows 可执行文件优先通过 [GitHub Actions](.github/workflows/build-exe.yml) 构建，无需为了使用转换器而在本机安装 PyInstaller。

## 🤝 项目来源

本项目基于 [vibheksoni/session-export](https://github.com/vibheksoni/session-export) 的会话解析与转换基础继续开发，保留上游 Git 历史和 MIT 许可证。Codex 桌面注册、可续聊 rollout 生成、冲突处理、真实会话兼容修复与 Windows 单文件工具由本项目补充。

- 上游关系与改动边界：[`UPSTREAM.md`](UPSTREAM.md)
- 详细文档：[`docs/`](docs/)
- 许可证：[`LICENSE`](LICENSE)

如果遇到转换后不可见、消息角色异常、重复会话或无法续聊，请在 [Issues](https://github.com/leNing-sy/codex-session-bridge/issues) 中说明转换方向、来源工具版本和目标工具版本。提交日志或会话样本前，请先删除账号信息、路径、密钥和私人对话内容。
