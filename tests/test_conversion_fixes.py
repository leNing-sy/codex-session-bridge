"""Tests for extraction filtering, dedupe, and the standalone convert script.

Covers the fixes for:
- Codex contextual markers that leaked into conversions (<in-app-browser-context>,
  AGENTS.md headers without the "for" suffix).
- Codex re-appending pending user inputs on every session resume.
- Claude sidechain / isMeta / slash-command noise leaking into conversions.
- CodexToClaudeConverter.has_changes computing the destination with an empty cwd.
- scripts/session_convert.py noise filtering and Codex state registration.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from session_sdk.converters import (
    ClaudeToCodexConverter,
    CodexToClaudeConverter,
    MessageExtractor,
)
from session_sdk.jsonl import JsonlFile
from session_sdk.models import NativeSession
from session_sdk.paths import SessionIdFactory, sanitize_claude_cwd
from session_sdk.stores import ClaudeStore, CodexStore

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "session_convert.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("session_convert", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _codex_session(records: list[dict]) -> NativeSession:
    return NativeSession("codex", "sess-1", r"C:\projects\demo", "2026-07-01T10:00:00.000Z", Path("x.jsonl"), records)


def _codex_user(text: str, ts: str = "2026-07-01T10:00:01.000Z") -> dict:
    return {
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
    }


def _codex_assistant(text: str, ts: str = "2026-07-01T10:00:02.000Z") -> dict:
    return {
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]},
    }


class CodexExtractionTests(unittest.TestCase):
    def test_new_contextual_markers_are_filtered(self) -> None:
        session = _codex_session(
            [
                _codex_user('<in-app-browser-context source="ambient-ui-state">tab state</in-app-browser-context>'),
                _codex_user("# AGENTS.md instructions\n\n<INSTRUCTIONS>be nice</INSTRUCTIONS>"),
                _codex_user("<turn-state>abc</turn-state>"),
                _codex_user("<current-datetime>2026-07-01</current-datetime>"),
                _codex_user("real question"),
            ]
        )
        messages = MessageExtractor().from_codex(session)
        conversational = [m for m in messages if not m.is_contextual]
        self.assertEqual([m.text for m in conversational], ["real question"])

    def test_resume_duplicated_pending_input_is_deduped(self) -> None:
        session = _codex_session(
            [
                _codex_user("first question"),
                _codex_assistant("answer"),
                _codex_user("pending question"),
                _codex_user("pending question"),
                _codex_user("pending question"),
            ]
        )
        messages = MessageExtractor().from_codex(session)
        self.assertEqual(
            [m.text for m in messages],
            ["first question", "answer", "pending question"],
        )

    def test_resume_duplicate_with_whitespace_variant_is_deduped(self) -> None:
        # The original input often carries a trailing newline that the
        # re-appended copies drop.
        session = _codex_session(
            [
                _codex_user("pending question\n"),
                _codex_user("<environment_context>refresh</environment_context>"),
                _codex_user("pending question"),
                _codex_user("pending question"),
            ]
        )
        messages = MessageExtractor().from_codex(session)
        conversational = [m for m in messages if not m.is_contextual]
        self.assertEqual([m.text for m in conversational], ["pending question\n"])

    def test_genuine_repeat_after_answer_is_kept(self) -> None:
        session = _codex_session(
            [
                _codex_user("try again"),
                _codex_assistant("done"),
                _codex_user("try again"),
            ]
        )
        messages = MessageExtractor().from_codex(session)
        self.assertEqual([m.text for m in messages], ["try again", "done", "try again"])


def _claude_record(rtype: str, content, *, sidechain=False, meta=False, ts="2026-07-01T10:00:01.000Z") -> dict:
    record = {
        "type": rtype,
        "message": {"role": rtype, "content": content},
        "uuid": "u-%d" % id(content),
        "parentUuid": None,
        "isSidechain": sidechain,
        "cwd": r"C:\projects\demo",
        "sessionId": "c1a9e2d4-0000-0000-0000-000000000001",
        "timestamp": ts,
    }
    if meta:
        record["isMeta"] = True
    return record


class ClaudeExtractionTests(unittest.TestCase):
    def test_sidechain_and_meta_records_are_skipped(self) -> None:
        session = NativeSession(
            "claude",
            "c1",
            r"C:\projects\demo",
            "2026-07-01T10:00:00.000Z",
            Path("c1.jsonl"),
            [
                _claude_record("user", "main question"),
                _claude_record("user", "subagent prompt", sidechain=True),
                _claude_record("assistant", [{"type": "text", "text": "subagent answer"}], sidechain=True),
                _claude_record("user", "meta note", meta=True),
                _claude_record("assistant", [{"type": "text", "text": "main answer"}]),
            ],
        )
        messages = MessageExtractor().from_claude(session)
        self.assertEqual([m.text for m in messages], ["main question", "main answer"])

    def test_command_noise_is_contextual(self) -> None:
        noise = [
            "<command-name>/model</command-name>",
            "<local-command-stdout>Set model</local-command-stdout>",
            "Caveat: The messages below were generated by the user while running local commands. DO NOT respond to these messages.",
            "<system-reminder>reminder</system-reminder>",
            "[Request interrupted by user]",
        ]
        records = [_claude_record("user", text) for text in noise]
        records.append(_claude_record("user", "real question"))
        session = NativeSession(
            "claude", "c1", r"C:\projects\demo", "2026-07-01T10:00:00.000Z", Path("c1.jsonl"), records
        )
        messages = MessageExtractor().from_claude(session)
        conversational = [m for m in messages if not m.is_contextual]
        self.assertEqual([m.text for m in conversational], ["real question"])

    def test_tool_result_only_records_produce_no_messages(self) -> None:
        session = NativeSession(
            "claude",
            "c1",
            r"C:\projects\demo",
            "2026-07-01T10:00:00.000Z",
            Path("c1.jsonl"),
            [
                _claude_record("user", "run it"),
                _claude_record("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]),
                _claude_record("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
                _claude_record("assistant", [{"type": "text", "text": "ran fine"}]),
            ],
        )
        messages = MessageExtractor().from_claude(session)
        self.assertEqual([m.text for m in messages], ["run it", "ran fine"])


class ConverterRoundTripTests(unittest.TestCase):
    def _codex_home(self, root: Path, session_id: str, records: list[dict]) -> Path:
        path = root / "sessions" / "2026" / "07" / "01" / f"rollout-2026-07-01T10-00-00-{session_id}.jsonl"
        JsonlFile(path).write(records)
        return path

    def test_codex_to_claude_filters_and_links_records(self) -> None:
        session_id = "0197e001-0000-7000-8000-000000000001"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = {
                "timestamp": "2026-07-01T10:00:00.000Z",
                "type": "session_meta",
                "payload": {"id": session_id, "timestamp": "2026-07-01T10:00:00.000Z", "cwd": r"C:\projects\demo"},
            }
            self._codex_home(
                root / "codex",
                session_id,
                [
                    meta,
                    _codex_user("<in-app-browser-context>leak</in-app-browser-context>"),
                    _codex_user("question"),
                    _codex_assistant("answer"),
                    _codex_user("pending"),
                    _codex_user("pending"),
                ],
            )
            codex_store = CodexStore(root / "codex")
            claude_store = ClaudeStore(root / "claude")
            converter = CodexToClaudeConverter(codex_store, claude_store, SessionIdFactory())
            plan = converter.plan(session_id)
            texts = []
            previous_uuid = None
            for record in plan.records:
                self.assertEqual(record["parentUuid"], previous_uuid)
                previous_uuid = record["uuid"]
                content = record["message"]["content"]
                texts.append(content if isinstance(content, str) else content[0]["text"])
            self.assertEqual(texts, ["question", "answer", "pending"])
            self.assertIn(sanitize_claude_cwd(r"C:\projects\demo"), str(plan.destination))

    def test_codex_to_claude_has_changes_uses_source_cwd(self) -> None:
        session_id = "0197e001-0000-7000-8000-000000000002"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = {
                "timestamp": "2026-07-01T10:00:00.000Z",
                "type": "session_meta",
                "payload": {"id": session_id, "timestamp": "2026-07-01T10:00:00.000Z", "cwd": r"C:\projects\demo"},
            }
            self._codex_home(root / "codex", session_id, [meta, _codex_user("question"), _codex_assistant("answer")])
            codex_store = CodexStore(root / "codex")
            claude_store = ClaudeStore(root / "claude")
            converter = CodexToClaudeConverter(codex_store, claude_store, SessionIdFactory())
            plan = converter.plan(session_id)
            # Write a destination at the cwd-derived path with exactly as many
            # records as the source file; has_changes must find it there.
            padded = plan.records + [{"type": "user", "message": {"role": "user", "content": "pad"}, "uuid": "pad", "parentUuid": None}]
            JsonlFile(plan.destination).write(padded, overwrite=True)
            self.assertFalse(converter.has_changes(session_id))

    def test_claude_to_codex_drops_command_noise(self) -> None:
        session_id = "c1a9e2d4-0000-4000-8000-000000000003"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "claude" / "projects" / sanitize_claude_cwd(r"C:\projects\demo")
            records = [
                _claude_record("user", "<command-name>/model</command-name>"),
                _claude_record("user", "real question"),
                _claude_record("assistant", [{"type": "text", "text": "real answer"}]),
            ]
            for record in records:
                record["sessionId"] = session_id
            JsonlFile(project / f"{session_id}.jsonl").write(records)
            converter = ClaudeToCodexConverter(
                ClaudeStore(root / "claude"), CodexStore(root / "codex"), SessionIdFactory()
            )
            plan = converter.plan(session_id)
            payloads = [r["payload"] for r in plan.records if r.get("type") == "response_item"]
            self.assertEqual(
                [(p["role"], p["content"][0]["text"]) for p in payloads],
                [("user", "real question"), ("assistant", "real answer")],
            )


class ConvertScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = _load_script()

    def test_codex2claude_filters_dedupes_and_chains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "rollout.jsonl"
            out = Path(tmp) / "out.jsonl"
            records = [
                {
                    "timestamp": "2026-07-01T10:00:00.000Z",
                    "type": "session_meta",
                    "payload": {"id": "s1", "timestamp": "2026-07-01T10:00:00.000Z", "cwd": r"C:\projects\demo"},
                },
                _codex_user("<in-app-browser-context>leak</in-app-browser-context>"),
                _codex_user("question"),
                {
                    "timestamp": "2026-07-01T10:00:02.000Z",
                    "type": "response_item",
                    "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]},
                },
                _codex_assistant("answer"),
                _codex_user("pending"),
                _codex_user("pending"),
            ]
            src.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            self.script.codex_to_claude(str(src), out_path=str(out), install=False)
            converted = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            human = [r["message"]["content"] for r in converted if isinstance(r["message"]["content"], str)]
            self.assertEqual(human, ["question", "pending"])
            previous = None
            for record in converted:
                self.assertEqual(record["parentUuid"], previous)
                previous = record["uuid"]
            kinds = [r["message"]["content"][0]["type"] for r in converted if r["type"] == "assistant"]
            self.assertEqual(kinds, ["thinking", "text"])

    def test_claude2codex_pairs_tools_and_drops_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "claude.jsonl"
            out = Path(tmp) / "out.jsonl"
            records = [
                _claude_record("user", "<command-name>/model</command-name>"),
                _claude_record("user", "run the tests"),
                _claude_record("assistant", [{"type": "thinking", "thinking": "plan"}]),
                _claude_record("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}}]),
                _claude_record("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "3 passed"}]),
                _claude_record("assistant", [{"type": "text", "text": "all green"}]),
            ]
            src.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            self.script.claude_to_codex(str(src), out_path=str(out), install=False)
            converted = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            items = [r["payload"] for r in converted if r["type"] == "response_item"]
            user_texts = [p["content"][0]["text"] for p in items if p["type"] == "message" and p["role"] == "user"]
            self.assertEqual(user_texts, ["run the tests"])
            calls = [p for p in items if p["type"] == "function_call"]
            outputs = [p for p in items if p["type"] == "function_call_output"]
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(outputs), 1)
            self.assertEqual(calls[0]["call_id"], outputs[0]["call_id"])
            self.assertEqual([p["type"] for p in items if p["type"] == "reasoning"], ["reasoning"])

    def test_register_codex_thread_inserts_and_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex").mkdir()
            db = home / ".codex" / "state_5.sqlite"
            connection = sqlite3.connect(db)
            connection.execute(
                """
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY, rollout_path TEXT, created_at INTEGER,
                    updated_at INTEGER, source TEXT, model_provider TEXT, cwd TEXT,
                    title TEXT, tokens_used INTEGER, has_user_event INTEGER,
                    archived INTEGER, cli_version TEXT, first_user_message TEXT,
                    model TEXT, created_at_ms INTEGER, updated_at_ms INTEGER,
                    thread_source TEXT, preview TEXT, recency_at INTEGER,
                    recency_at_ms INTEGER, history_mode TEXT
                )
                """
            )
            connection.commit()
            connection.close()
            original_home = self.script.HOME
            self.script.HOME = str(home)
            try:
                ok = self.script.register_codex_thread(
                    "sess-1", "My title", r"C:\projects\demo", r"C:\rollout.jsonl",
                    "first message", "2026-07-01T10:00:00.000Z",
                )
                self.assertTrue(ok)
                ok = self.script.register_codex_thread(
                    "sess-1", "New title", r"C:\projects\demo", r"C:\rollout.jsonl",
                    "first message", "2026-07-01T10:00:00.000Z",
                )
                self.assertTrue(ok)
            finally:
                self.script.HOME = original_home
            connection = sqlite3.connect(db)
            try:
                rows = connection.execute("SELECT id, title, has_user_event FROM threads").fetchall()
            finally:
                connection.close()
            self.assertEqual(rows, [("sess-1", "New title", 1)])

    def test_relative_output_path_works(self) -> None:
        # write_jsonl used to call makedirs('') for a bare -o filename.
        import os
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "rollout.jsonl"
            records = [
                {
                    "timestamp": "2026-07-01T10:00:00.000Z",
                    "type": "session_meta",
                    "payload": {"id": "s1", "timestamp": "2026-07-01T10:00:00.000Z", "cwd": r"C:\projects\demo"},
                },
                _codex_user("question"),
            ]
            src.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                self.script.codex_to_claude(str(src), out_path="relative-out.jsonl", install=False)
                self.assertTrue((Path(tmp) / "relative-out.jsonl").is_file())
            finally:
                os.chdir(cwd)

    def test_register_codex_thread_missing_database_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_home = self.script.HOME
            self.script.HOME = tmp
            try:
                ok = self.script.register_codex_thread(
                    "sess-1", "t", "c", "r", "p", "2026-07-01T10:00:00.000Z"
                )
            finally:
                self.script.HOME = original_home
            self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
