# session-convert

Claude Code 与 Codex 会话记录互相转换的脚本。把一边的历史会话转换并"安装"到另一边的会话目录，使其出现在对方的历史列表里，可以直接恢复并继续对话。

单文件、无第三方依赖，Python 3.8+。

## 用法

```bash
# Codex -> Claude
python session_convert.py codex2claude <rollout-*.jsonl>
python session_convert.py codex2claude --latest              # 最新的 Codex 会话

# Claude -> Codex
python session_convert.py claude2codex <session.jsonl>
python session_convert.py claude2codex --latest              # 最新的 Claude 会话

# 列出两边可转换的会话文件
python session_convert.py list [codex|claude|all]
```

默认直接安装到目标工具的会话目录；加 `-o <文件>` 则只输出到指定文件、不安装。

### codex2claude 选项

| 选项 | 说明 |
|---|---|
| `--title <标题>` | 设置 Claude 会话标题 |
| `--claude-cwd <路径>` | 覆盖会话 cwd，决定装到哪个 Claude 项目目录（默认用 Codex 会话原本的 cwd） |
| `--reasoning thinking\|text\|skip` | Codex 推理摘要的转换方式，默认转成 thinking 块 |
| `--include-context` | 保留 AGENTS.md、环境上下文等注入消息（默认跳过，只留真实对话） |

两个方向默认都会清理注入消息：

- codex2claude：跳过 Codex 注入的隐藏上下文消息（`<environment_context>`、
  `<in-app-browser-context>`、AGENTS.md 等），这些在 Codex 界面里不显示，
  转过去会被当成正文渲染；并对恢复会话导致的重复用户输入去重（比较时忽略首尾空白）。
- claude2codex：跳过 Claude 记录的斜杠命令（`<command-name>` 等）、本地命令输出
  （`<local-command-stdout>`）、`<system-reminder>` 和"[Request interrupted]"等标记。

## 转换后如何打开

- **Claude Code CLI**：在会话对应的 cwd 目录下运行 `claude --resume <会话id>`，或 `claude -r` 从历史列表选择。
- **Codex**：转换时会写入 `~/.codex/session_index.jsonl` 索引，并尽力注册到桌面端
  实际读取的 `state_5.sqlite` threads 表（缺库/缺表时自动跳过），出现在历史会话列表。
- **Claude 桌面应用**：桌面应用的历史列表不扫描 CLI 会话目录，而是读自己的注册表
  `%APPDATA%\Claude\claude-code-sessions\<org>\<user>\local_*.json`。
  想在桌面应用里看到转换的会话，需要参照已有条目手动补一个注册 JSON
  （`cliSessionId` 填转换出的会话 id，`cwd` 填项目路径），然后重启应用。

## 格式说明（基于实际文件逆向）

**Codex rollout**（`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`）：
每行 `{timestamp, type, payload}`；`type` 有 `session_meta` / `response_item` /
`event_msg` / `turn_context` 等。对话内容在 `response_item` 里：
`message`（user/assistant/developer）、`reasoning`（推理摘要）、
`function_call` / `custom_tool_call` 及对应的 `*_output`。

**Claude 会话**（`~/.claude/projects/<项目目录名>/<uuid>.jsonl`）：
每行一条记录，`type` 有 `user` / `assistant` 等；记录间用 `parentUuid -> uuid`
链接成串。`user.message.content` 是字符串（人类输入）或 `tool_result` 块列表；
`assistant.message.content` 是 `thinking` / `text` / `tool_use` 块列表。
项目目录名由 cwd 中非字母数字字符替换为 `-` 得到。

## 已知限制

- Codex 的推理摘要转成 Claude thinking 块时没有签名，继续对话时模型可能忽略这部分（不影响正文和工具记录的理解）。
- Claude 的 sidechain（子代理）记录不转换。
- 图片、音频等多媒体内容不转换，只保留文本。
