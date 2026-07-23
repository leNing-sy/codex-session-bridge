# Upstream Attribution

Codex Session Bridge started from [vibheksoni/session-export](https://github.com/vibheksoni/session-export),
also published as UniSessions. The upstream project is licensed under the MIT License.

The original Git history and `LICENSE` file are retained. The main changes in this fork are:

- a Codex desktop-compatible rollout writer with explicit turn lifecycle events;
- transactional Codex registration, backup, validation, and rollback;
- direct, read-only OpenCode SQLite support;
- UUID v7 generation for imported Codex sessions;
- real target-format tests that verify list visibility and resumable turn structure.

The legacy UniSessions converters remain available while the Codex-focused bridge is developed.
