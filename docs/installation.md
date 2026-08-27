# Installation

## Windows: standalone exe (no Python needed)

Download `session-convert.exe` from the
[latest release](https://github.com/leNing-sy/codex-session-bridge/releases/latest).
It covers the four Codex bridge directions through an interactive menu or the
command line.

The exe is built from the tagged source by GitHub Actions and attached to the
release. It is not code-signed, so Windows SmartScreen may warn on first run.

!!! warning "Back up before the first conversion"
    The exe writes into the session directories and desktop state databases of
    live applications. It is not backup software. Keep the source session files,
    and back up the target session directory and state database before your first
    run of a direction.

## From source

```bash
git clone https://github.com/leNing-sy/codex-session-bridge.git
cd codex-session-bridge
pip install -e .
```

With optional extras:

```bash
pip install -e ".[mcp]"        # MCP server (FastMCP)
pip install -e ".[fast]"       # orjson + google-re2 for faster JSON and regex
pip install -e ".[mcp,fast]"   # everything
```

This installs the `session_sdk` and `unisessions` packages, the `codex-bridge`
console script, and the `unisessions-mcp` console script.

!!! note "Not published to PyPI"
    `pip install codex-session-bridge` does not work — the package is not on
    PyPI. Install from source or use the Windows exe. Do not
    `pip install unisessions` expecting this fork; that name belongs to the
    [upstream project](https://github.com/vibheksoni/session-export) and does
    not include this fork's Codex bridge fixes.

## Prerequisites

- **Python 3.11 or later** (uses `match` statements, `type` aliases, and modern typing).
- pip or any PEP 517-compatible installer.
- Git for cloning (if installing from source).

## Dependencies

| Package | Required | Purpose |
|---|---|---|
| `tiktoken>=0.7.0` | Yes | Token estimation for Pi assistant message usage fields. |
| `cryptography>=42.0.0` | Yes | AES-256-GCM decryption for Windsurf Cascade sessions. |
| `orjson>=3.10.0` | No (auto-detected) | 2x faster JSON parsing. Falls back to stdlib `json` if absent. |
| `google-re2>=1.1` | No (auto-detected) | 4x faster regex search. Falls back to stdlib `re` if absent. |
| `fastmcp>=2.0.0` | No | MCP server. Install with `pip install -e ".[mcp]"`. |

Neither the standalone exe nor the four Codex bridge directions require the
optional packages.

## Verify installation

The Codex-focused importer:

```bash
codex-bridge --help
python -m codex_bridge list-claude
python -m codex_bridge list-opencode
```

The inherited multi-provider CLI (module invocation only — there is no
`unisessions` console script):

```bash
python -m unisessions list codex
python -m unisessions list claude
python -m unisessions list opencode
python -m unisessions list pi
python -m unisessions list devin
python -m unisessions list factory
python -m unisessions list windsurf
```

Each command should print a tab-separated list of sessions or "no sessions found."

## Running the tests

```bash
python -m unittest discover -s tests -v
```

74 tests, 3 of which skip when `cryptography` is absent (they cover Windsurf
Cascade decryption).

## Optional: orjson for speed

`orjson` is auto-detected at runtime. No configuration needed. When installed, all JSON parsing uses `orjson` (measured at ~244 MB/s vs ~120 MB/s for stdlib `json` on a 21 MB Codex rollout file). When absent, the SDK falls back to stdlib `json` transparently.

## Next steps

- [Quick Start](quickstart.md) -- convert your first session.
- [CLI Reference](cli.md) -- all commands and flags.
