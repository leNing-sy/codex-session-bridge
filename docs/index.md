---
hide:
  - navigation
  - toc
---

# Codex Session Bridge

Migrate local session history between **Codex**, **Claude Code**, and
**OpenCode** so a converted session appears in the target tool's history list
and keeps enough visible context to continue the conversation.

[![Release](https://img.shields.io/github/v/release/leNing-sy/codex-session-bridge?label=release)](https://github.com/leNing-sy/codex-session-bridge/releases/latest)
[![Windows Build](https://github.com/leNing-sy/codex-session-bridge/actions/workflows/build-exe.yml/badge.svg)](https://github.com/leNing-sy/codex-session-bridge/actions/workflows/build-exe.yml)
[![License](https://img.shields.io/github/license/leNing-sy/codex-session-bridge)](https://github.com/leNing-sy/codex-session-bridge/blob/main/LICENSE)

[:material-download: Install](installation.md){ .md-button .md-button--primary }
[:material-rocket-launch: Quick Start](quickstart.md){ .md-button }
[:material-github: GitHub](https://github.com/leNing-sy/codex-session-bridge){ .md-button }

---

!!! danger "Back up important sessions before converting"
    These tools write into the session directories and desktop state databases
    of live applications. Keep the source session files at minimum, and back up
    the target session directory and state database when you first use a
    direction, right after a client upgrade, or before using `overwrite`.

    Local storage formats change between client versions. Conversions use
    conservative conflict handling and atomic writes, but no guarantee covers
    every client version, non-standard session, or interrupted run. Validate
    irreplaceable sessions on a copy first.

## Two layers, different maturity

This project is a fork of [vibheksoni/session-export](https://github.com/vibheksoni/session-export)
(published upstream as UniSessions). It ships two distinct layers, and they are
verified to different degrees.

### 1. The Codex bridge (this fork's focus)

Four directions, exercised against real sessions and shipped as a Windows
single-file exe plus a Python importer:

| Direction | Result | Verification status |
|---|---|---|
| OpenCode -> Codex | Installed and registered in the Codex history list | :white_check_mark: Import and resume verified with real sessions |
| Claude Code -> Codex | Installed and registered in the Codex history list | :white_check_mark: Visible after restart and resume verified with real history |
| Codex -> Claude Code | Written to the Claude Code session directory; desktop list registered when a template exists | :test_tube: Automated tests only; resume not yet verified in a real Claude client |
| Codex -> OpenCode | Generates an OpenCode export JSON | :test_tube: Automated tests only; requires a manual `opencode import` |

Start here: [Installation](installation.md) and [Quick Start](quickstart.md).

### 2. The inherited UniSessions SDK

The upstream SDK remains in the repository and still covers seven providers
(Codex, Pi, OpenCode, Claude Code, Devin, Factory, Windsurf Cascade) across all
42 direction combinations, plus trace export and MCP chat recall.

!!! note "Inherited surface, lighter verification"
    The four directions in the table above are what this fork actively tests
    against real client data. The remaining SDK directions carry upstream's
    coverage and are documented here as a library reference. Treat them as
    unverified against current client versions, and always dry-run first.

The architecture pages describe this layer:
[Stores](stores.md), [Converters](converters.md), [Models](models.md),
[Paths](paths.md), [Data Fidelity](data-fidelity.md).

## What is preserved

Migration moves **visible conversation context**. It does not move accounts,
subscriptions, model state, permission grants, or cloud session identity.

Preserved: user-visible messages and final assistant replies, conversation
order, title, original working directory, base timestamps, base64 images
between Claude Code and Codex, and optional reasoning summaries for
Codex -> Claude Code.

Not preserved by default: hidden reasoning and signatures, internal tool-call
chains, tool results, subagent records, approval and sandbox state, and unsupported
media such as audio. This is deliberate — replaying another runtime's execution
records as native history causes role mismatches and can break resumability.

## Other capabilities

| Capability | Description |
|---|---|
| [Trace export](traces.md) | Export sessions as HuggingFace STS, OpenAI fine-tuning JSONL, or ShareGPT training data |
| [MCP chat recall](mcp-server.md) | A FastMCP server exposing SQLite FTS5 search over parsed session history so an agent can recall past conversations |
| [Search](search.md) | The search engine and index behind chat recall |
| [CLI reference](cli.md) | Full command surface for the inherited multi-provider CLI |

## Quick install

Windows users can download the standalone exe and skip Python entirely:

[:material-download: Download the latest `session-convert.exe`](https://github.com/leNing-sy/codex-session-bridge/releases/latest)

From source:

```bash
git clone https://github.com/leNing-sy/codex-session-bridge.git
cd codex-session-bridge
pip install -e .
```

See [Installation](installation.md) for extras and requirements.

---

[:material-download: Get started](installation.md){ .md-button .md-button--primary }
[:material-book-open: Read the docs](quickstart.md){ .md-button }
