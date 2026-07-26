# Codex Session Bridge

把 OpenCode、Claude Code 等 AI 编程工具的历史会话导入 Codex，并确保会话在桌面端中可见、可翻页、可继续聊天。

当前优先支持 **OpenCode -> Codex**。项目基于 UniSessions 的来源解析能力开发，但重写了 Codex 生成、安装、注册、验证和回滚流程。

> **直接下载**：不想装 Python？去 [Releases 页面](../../releases/latest)下载
> `session-convert.exe`（Windows 64 位，免安装），双击即进交互模式，一个软件
> 覆盖全部四个方向：Codex <-> Claude 双向、OpenCode -> Codex、Codex -> OpenCode。exe 由 GitHub Actions 从源码自动构建（构建日志
> 公开可查）；未做代码签名，首次运行遇到 SmartScreen 提示时点
> "更多信息 → 仍要运行"即可。

## 当前状态

已完成：

- 直接只读 OpenCode 正在使用的 SQLite 数据库，无需先执行 `opencode export`。
- 将相邻的助手消息片段归并到对应用户回合。
- 生成 Codex 桌面端需要的完整回合事件：
  - `task_started`
  - `user_message`
  - `agent_message`
  - `task_complete`
- 使用基于原始时间戳的 UUID v7 会话 ID。
- 注册 Codex 标题、预览、模型和可见状态。
- 更新 `session_index.jsonl`。
- 修改前在线备份 `state_5.sqlite`、索引和已有 rollout。
- 写入失败或验证失败时自动回滚。
- 安装后验证轮次数、消息数、索引和数据库状态。
- 预览支持 Claude Code JSONL 会话，复用同一套 Codex 安装、备份和验证流程。
- 修复共享提取层的四类泄漏：Claude 子代理/isMeta/斜杠命令噪声混入、Codex 隐藏
  上下文标记漏匹配、恢复会话重复输入、增量导出的路径计算错误（均有回归测试）。

下一阶段：

- 使用真实 Claude Code 会话完成桌面端续聊验证。
- 批量导入、筛选和冲突处理。
- Codex 版本兼容检测。
- 更完整的附件和工具摘要保留。

## 安装

需要 Python 3.11 或更高版本。

```powershell
git clone https://github.com/leNing-sy/codex-session-bridge.git
cd codex-session-bridge
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

## 使用

列出最近的 OpenCode 会话：

```powershell
.venv\Scripts\python -m codex_bridge list-opencode
```

先干跑，不写入 Codex：

```powershell
.venv\Scripts\python -m codex_bridge import-opencode <session-id>
```

确认后正式导入：

```powershell
.venv\Scripts\python -m codex_bridge import-opencode <session-id> --write
```

验证已经安装的会话：

```powershell
.venv\Scripts\python -m codex_bridge verify <codex-session-id>
```

所有命令都支持指定路径：

```powershell
.venv\Scripts\python -m codex_bridge `
  --codex-home $HOME\.codex `
  import-opencode <session-id> `
  --opencode-db C:\path\to\opencode.db `
  --write
```

使用 `--json` 可以获得适合脚本处理的输出。

### Claude Code（预览）

列出 Claude Code 会话：

```powershell
.venv\Scripts\python -m codex_bridge list-claude
```

先干跑，再正式导入：

```powershell
.venv\Scripts\python -m codex_bridge import-claude <session-id>
.venv\Scripts\python -m codex_bridge import-claude <session-id> --write
```

默认从 `~/.claude/projects` 读取。也可以使用 `--claude-home` 或
`--claude-session-dir` 指定其他位置。当前已通过合成 Claude Code JSONL 测试；在真实会话完成 Codex
桌面端续聊验证前，该适配器保持预览状态。

### 独立脚本：四方向转换（scripts/）

[`scripts/session_convert.py`](scripts/session_convert.py) 是一个无依赖的单文件脚本，
与 `codex_bridge` 互补：

- 覆盖四个方向：Codex <-> Claude 双向、OpenCode -> Codex（直读活库）、
  Codex -> OpenCode（导出文件 + `opencode import`）；
- 保留工具调用、工具结果和推理摘要（转成 thinking 块），不只是纯文本回合；
- 双向清理注入消息：Codex 侧过滤 AGENTS.md、`<environment_context>`、
  `<in-app-browser-context>` 等隐藏上下文并对恢复会话产生的重复输入去重，
  Claude 侧过滤斜杠命令、本地命令输出、`<system-reminder>` 等记录；
- 图片消息双向转换（base64 直通，转进 Codex 时同时落盘供界面显示）；
- 两端桌面应用自动注册：claude2codex 写 Codex 的 `state_5.sqlite` threads 表，
  codex2claude 写 Claude 桌面端的会话注册目录（重启应用后可见）；
- 转换结果已在 Claude Code 真实续聊场景验证。

**交互模式**：不带参数运行进入菜单——选方向、从最近会话列表（带标题和时间）
选一个，自动转换并安装。可用 PyInstaller 打成免安装的单文件 exe，双击即用：

```powershell
pip install pyinstaller
pyinstaller --onefile --console --name session-convert scripts/session_convert.py
```

命令行用法：

```powershell
python scripts/session_convert.py codex2claude --latest
python scripts/session_convert.py claude2codex --latest
python scripts/session_convert.py opencode2codex --latest
python scripts/session_convert.py codex2opencode --latest
```

详细用法、两种格式的逆向说明和已知限制见 [scripts/README.md](scripts/README.md)。

## 安全设计

正式导入采用以下顺序：

1. 从 OpenCode 数据库只读提取会话。
2. 在内存中生成 Codex rollout。
3. 备份 Codex 状态数据库、会话索引和同名 rollout。
4. 原子写入 rollout。
5. 在 SQLite 事务中注册 Codex 会话元数据。
6. 原子更新会话索引。
7. 重新读取并验证所有关键状态。
8. 任一步失败则恢复备份。

备份默认位于：

```text
~/.codex/backups/codex-session-bridge/
```

## 数据保真度

当前目标是保留可见对话历史并支持继续聊天：

- 保留用户和助手文本、时间顺序、标题和工作目录。
- 将一个用户回合后的多个助手文本片段合并成一个 Codex 助手回复。
- 不导入模型内部 reasoning。
- 暂不导入原始工具调用、审批状态、沙箱状态和二进制附件。

工具调用结果中有价值的信息如果已经出现在助手最终文本里，会随最终文本保留。

## 开发

运行测试：

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
```

运行编译检查：

```powershell
.venv\Scripts\python -m compileall -q codex_bridge session_sdk unisessions tests
```

## 上游与许可证

本项目基于 [vibheksoni/session-export](https://github.com/vibheksoni/session-export) 开发，保留原始 Git 历史和 MIT `LICENSE`。详细改动边界见 [UPSTREAM.md](UPSTREAM.md)。
