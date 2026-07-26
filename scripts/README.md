# session-convert

Claude Code 与 Codex 会话记录互相转换的脚本。把一边的历史会话转换并"安装"到另一边的会话目录，使其出现在对方的历史列表里，可以直接恢复并继续对话。

单文件、无第三方依赖，Python 3.8+。

## 用法

### 直接下载 exe（最简单）

去仓库的 [Releases 页面](../../../releases/latest)下载 `session-convert.exe`
（Windows 64 位，约 9 MB，免安装免 Python），双击即用。

- exe 由 GitHub Actions 在打 tag 时从源码自动构建（先跑完整测试套件，
  构建日志公开可查），不是手工上传的二进制。
- 未做代码签名：首次运行遇到 Windows SmartScreen 提示时，
  点"更多信息 → 仍要运行"即可。

### 交互模式（推荐，双击即用）

不带参数运行（或双击 exe）会进入交互模式：选方向 → 从最近 15 个
会话列表里选一个（带标题和时间）→ 自动转换并安装，全程不用记参数。

### 从源码自行打包 exe

```powershell
pip install pyinstaller
pyinstaller --onefile --console --name session-convert scripts/session_convert.py
```

生成 `dist\session-convert.exe`，与 Release 下载的等价。

### 发布新版本（维护者）

```bash
git tag v0.x.0 && git push origin v0.x.0
```

GitHub Actions 自动完成：全量测试 → 构建 exe → 冒烟测试 → 创建 Release 并挂附件。
发版前记得同步 `assets/version_info.txt` 里的两处版本号（exe 属性里显示的版本）。
图标由 `assets/make_icon.py` 生成（纯标准库），改图案后重跑一次提交新的 `icon.ico` 即可。

### 命令行

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
- **Claude 桌面应用**：转换时会自动注册到桌面应用的会话目录
  （`%APPDATA%\Claude\claude-code-sessions\<org>\<user>\local_*.json`，
  参照已有条目做模板；没装桌面版时自动跳过）。**重启桌面应用**后
  出现在历史列表。桌面应用的列表不扫描 CLI 会话目录，注册条目是必须的。

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
- 图片消息双向转换（base64 直通；转进 Codex 时同时落盘到
  `~/.codex/bridge-images/<会话id>/` 供界面显示）；音频等其他多媒体不转换。
