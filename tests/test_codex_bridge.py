from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from codex_bridge.claude import ClaudeCodeConversationStore
from codex_bridge.codex import CodexImporter, CodexRolloutBuilder, CodexSessionVerifier
from codex_bridge.opencode import OpenCodeDatabaseStore


class CodexBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="codex-session-bridge-")
        self.root = Path(self.temporary.name)
        self.opencode_database = self.root / "opencode.db"
        self.codex_home = self.root / ".codex"
        self.claude_home = self.root / ".claude"
        self._create_opencode_database()
        self._create_codex_database()
        self._create_claude_session()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reads_live_opencode_database_and_groups_turns(self) -> None:
        store = OpenCodeDatabaseStore(self.opencode_database)

        conversation = store.load_conversation("ses_demo")

        self.assertEqual(conversation.title, "中文测试会话")
        self.assertEqual(len(conversation.turns), 2)
        self.assertEqual(conversation.turns[0].user.text, "第一个问题")
        self.assertEqual(conversation.turns[0].assistant_text, "先检查一下\n\n最终回答")
        self.assertEqual(conversation.turns[1].user.text, "第二个问题")
        self.assertEqual(conversation.turns[1].assistant_text, "")
        self.assertEqual(conversation.assistant_count, 2)

    def test_builder_emits_resumable_codex_turn_events(self) -> None:
        conversation = OpenCodeDatabaseStore(self.opencode_database).load_conversation("ses_demo")

        session_id, records = CodexRolloutBuilder().build(conversation)

        self.assertEqual(UUID(session_id).version, 7)
        event_types = [
            record["payload"]["type"]
            for record in records
            if record["type"] == "event_msg"
        ]
        self.assertEqual(event_types.count("task_started"), 2)
        self.assertEqual(event_types.count("user_message"), 2)
        self.assertEqual(event_types.count("agent_message"), 1)
        self.assertEqual(event_types.count("task_complete"), 2)
        started = {
            record["payload"]["turn_id"]
            for record in records
            if record["type"] == "event_msg" and record["payload"]["type"] == "task_started"
        }
        completed = {
            record["payload"]["turn_id"]
            for record in records
            if record["type"] == "event_msg" and record["payload"]["type"] == "task_complete"
        }
        self.assertEqual(started, completed)

    def test_installs_registers_indexes_and_validates(self) -> None:
        conversation = OpenCodeDatabaseStore(self.opencode_database).load_conversation("ses_demo")
        importer = CodexImporter(self.codex_home)

        result = importer.install(conversation)

        self.assertTrue(result.validation.valid, result.validation.errors)
        self.assertTrue(result.rollout_path.is_file())
        self.assertTrue((result.backup_dir / "state_5.sqlite").is_file())
        self.assertEqual(UUID(result.session_id).version, 7)
        connection = sqlite3.connect(self.codex_home / "state_5.sqlite")
        try:
            row = connection.execute(
                "SELECT title, preview, has_user_event, model, reasoning_effort FROM threads WHERE id = ?",
                (result.session_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("中文测试会话", "第一个问题", 1, "gpt-5.6-sol", "high"))
        index_entries = [
            json.loads(line)
            for line in (self.codex_home / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertTrue(any(entry["id"] == result.session_id for entry in index_entries))

        validation = CodexSessionVerifier(self.codex_home).verify(result.session_id)
        self.assertTrue(validation.valid, validation.errors)
        self.assertEqual(validation.turn_count, 2)
        self.assertEqual(validation.assistant_message_count, 1)

    def test_failed_registration_rolls_back_new_rollout(self) -> None:
        bad_home = self.root / "bad-codex"
        bad_home.mkdir()
        sqlite3.connect(bad_home / "state_5.sqlite").close()
        conversation = OpenCodeDatabaseStore(self.opencode_database).load_conversation("ses_demo")
        importer = CodexImporter(bad_home)
        _session_id, destination, _records = importer.plan(conversation)

        with self.assertRaisesRegex(RuntimeError, "threads table is missing"):
            importer.install(conversation)

        self.assertFalse(destination.exists())

    def test_reads_claude_code_and_reuses_codex_importer(self) -> None:
        store = ClaudeCodeConversationStore(self.claude_home)

        conversation = store.load_conversation("01234567-89ab-4def-8123-456789abcdef")
        session_id, records = CodexRolloutBuilder().build(conversation)

        self.assertEqual(conversation.source, "claude")
        self.assertEqual(conversation.cwd, r"C:\projects\demo")
        self.assertEqual(len(conversation.turns), 1)
        self.assertEqual(conversation.turns[0].user.text, "Claude question")
        self.assertEqual(conversation.turns[0].assistant_text, "Claude answer")
        self.assertEqual(UUID(session_id).version, 7)
        event_types = [
            record["payload"]["type"]
            for record in records
            if record["type"] == "event_msg"
        ]
        self.assertEqual(event_types, ["task_started", "user_message", "agent_message", "task_complete"])

    def _create_opencode_database(self) -> None:
        connection = sqlite3.connect(self.opencode_database)
        try:
            connection.executescript(
                """
                CREATE TABLE session (
                    id TEXT PRIMARY KEY,
                    directory TEXT NOT NULL,
                    title TEXT NOT NULL,
                    time_created INTEGER NOT NULL,
                    time_updated INTEGER NOT NULL,
                    time_archived INTEGER
                );
                CREATE TABLE message (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    time_created INTEGER NOT NULL,
                    time_updated INTEGER NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE part (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    time_created INTEGER NOT NULL,
                    time_updated INTEGER NOT NULL,
                    data TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO session VALUES (?, ?, ?, ?, ?, NULL)",
                ("ses_demo", r"C:\projects\demo", "中文测试会话", 1783912646000, 1783913000000),
            )
            messages = [
                ("msg_user_1", "user", 1783912646722, {"model": {"providerID": "demo", "modelID": "model-a"}}),
                ("msg_assistant_1", "assistant", 1783912650000, {"providerID": "demo", "modelID": "model-a"}),
                ("msg_assistant_2", "assistant", 1783912656000, {"providerID": "demo", "modelID": "model-a"}),
                ("msg_user_2", "user", 1783912700000, {"model": {"providerID": "demo", "modelID": "model-a"}}),
            ]
            for message_id, role, timestamp, extra in messages:
                data = {"role": role, "time": {"created": timestamp}, **extra}
                connection.execute(
                    "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
                    (message_id, "ses_demo", timestamp, timestamp, json.dumps(data, ensure_ascii=False)),
                )
            parts = [
                ("part_1", "msg_user_1", "text", "第一个问题", 1783912646722),
                ("part_2", "msg_assistant_1", "text", "先检查一下", 1783912650000),
                ("part_3", "msg_assistant_1", "tool", "ignored", 1783912650100),
                ("part_4", "msg_assistant_2", "text", "最终回答", 1783912656000),
                ("part_5", "msg_user_2", "text", "第二个问题", 1783912700000),
            ]
            for part_id, message_id, part_type, text, timestamp in parts:
                connection.execute(
                    "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        part_id,
                        message_id,
                        "ses_demo",
                        timestamp,
                        timestamp,
                        json.dumps({"type": part_type, "text": text}, ensure_ascii=False),
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    def _create_codex_database(self) -> None:
        self.codex_home.mkdir(parents=True)
        database = self.codex_home / "state_5.sqlite"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY,
                    rollout_path TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    model_provider TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    title TEXT NOT NULL,
                    sandbox_policy TEXT NOT NULL,
                    approval_mode TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    has_user_event INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    archived_at INTEGER,
                    cli_version TEXT NOT NULL DEFAULT '',
                    first_user_message TEXT NOT NULL DEFAULT '',
                    agent_nickname TEXT,
                    agent_role TEXT,
                    memory_mode TEXT NOT NULL DEFAULT 'enabled',
                    model TEXT,
                    reasoning_effort TEXT,
                    agent_path TEXT,
                    created_at_ms INTEGER,
                    updated_at_ms INTEGER,
                    thread_source TEXT,
                    preview TEXT NOT NULL DEFAULT '',
                    recency_at INTEGER NOT NULL DEFAULT 0,
                    recency_at_ms INTEGER NOT NULL DEFAULT 0,
                    history_mode TEXT NOT NULL DEFAULT 'legacy'
                );
                """
            )
            connection.execute(
                """
                INSERT INTO threads (
                    id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                    sandbox_policy, approval_mode, cli_version, model, reasoning_effort,
                    updated_at_ms, preview, recency_at, recency_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "019f0000-0000-7000-8000-000000000000",
                    r"\\?\C:\fake\rollout.jsonl",
                    1,
                    2,
                    "vscode",
                    "custom",
                    r"\\?\C:\projects\demo",
                    "template",
                    '{"type":"read-only"}',
                    "on-request",
                    "0.145.0",
                    "gpt-5.6-sol",
                    "high",
                    2,
                    "template",
                    2,
                    2,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        (self.codex_home / "session_index.jsonl").write_text(
            '{"id":"019f0000-0000-7000-8000-000000000000","thread_name":"template","updated_at":"2026-01-01T00:00:00Z"}\n',
            encoding="utf-8",
        )

    def _create_claude_session(self) -> None:
        session_id = "01234567-89ab-4def-8123-456789abcdef"
        path = self.claude_home / "projects" / "C--projects-demo" / f"{session_id}.jsonl"
        path.parent.mkdir(parents=True)
        records = [
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": r"C:\projects\demo",
                "timestamp": "2026-07-20T10:00:00.000Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Claude question"}],
                },
            },
            {
                "type": "assistant",
                "sessionId": session_id,
                "cwd": r"C:\projects\demo",
                "timestamp": "2026-07-20T10:00:02.000Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-test",
                    "content": [
                        {"type": "text", "text": "Claude answer"},
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "README.md"}},
                    ],
                },
            },
        ]
        path.write_text(
            "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
