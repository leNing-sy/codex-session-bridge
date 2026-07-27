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
            JsonlFile(plan.destination).write(plan.records, overwrite=True)
            self.assertFalse(converter.has_changes(session_id))

    def test_codex_to_claude_update_refuses_target_side_continuation(self) -> None:
        session_id = "0197e001-0000-7000-8000-000000000004"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = {
                "timestamp": "2026-07-01T10:00:00.000Z",
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "timestamp": "2026-07-01T10:00:00.000Z",
                    "cwd": r"C:\projects\demo",
                },
            }
            self._codex_home(
                root / "codex", session_id,
                [meta, _codex_user("question"), _codex_assistant("answer")],
            )
            converter = CodexToClaudeConverter(
                CodexStore(root / "codex"), ClaudeStore(root / "claude"),
                SessionIdFactory(),
            )
            plan = converter.plan(session_id)
            continuation = {
                "type": "user",
                "message": {"role": "user", "content": "target-only follow-up"},
                "uuid": "target-only",
                "parentUuid": plan.records[-1]["uuid"],
                "sessionId": session_id,
                "cwd": r"C:\projects\demo",
                "timestamp": "2026-07-01T10:10:00.000Z",
            }
            JsonlFile(plan.destination).write(
                plan.records + [continuation], overwrite=True)
            self.assertFalse(converter.has_changes(session_id))

    def test_codex_to_claude_update_accepts_source_extension(self) -> None:
        session_id = "0197e001-0000-7000-8000-000000000005"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = {
                "timestamp": "2026-07-01T10:00:00.000Z",
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "timestamp": "2026-07-01T10:00:00.000Z",
                    "cwd": r"C:\projects\demo",
                },
            }
            source_path = self._codex_home(
                root / "codex", session_id,
                [meta, _codex_user("question"), _codex_assistant("answer")],
            )
            converter = CodexToClaudeConverter(
                CodexStore(root / "codex"), ClaudeStore(root / "claude"),
                SessionIdFactory(),
            )
            initial = converter.plan(session_id)
            JsonlFile(initial.destination).write(initial.records, overwrite=True)
            extended_records = JsonlFile(source_path).read() + [
                _codex_user("follow-up", "2026-07-01T10:10:00.000Z")]
            JsonlFile(source_path).write(extended_records, overwrite=True)
            converter = CodexToClaudeConverter(
                CodexStore(root / "codex"), ClaudeStore(root / "claude"),
                SessionIdFactory(),
            )
            self.assertTrue(converter.has_changes(session_id))

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

    # 1x1 红色 PNG (无第三方依赖)
    _PNG_B64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4"
        "z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
    )

    def test_codex2claude_converts_input_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "rollout.jsonl"
            out = Path(tmp) / "out.jsonl"
            records = [
                {
                    "timestamp": "2026-07-01T10:00:00.000Z",
                    "type": "session_meta",
                    "payload": {"id": "s1", "timestamp": "2026-07-01T10:00:00.000Z", "cwd": r"C:\projects\demo"},
                },
                {
                    "timestamp": "2026-07-01T10:00:01.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message", "role": "user",
                        "content": [
                            {"type": "input_image", "image_url": "data:image/png;base64," + self._PNG_B64},
                            {"type": "input_text", "text": "看这张图"},
                        ],
                    },
                },
            ]
            src.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            self.script.codex_to_claude(str(src), out_path=str(out), install=False)
            converted = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            users = [r for r in converted if r["type"] == "user"]
            self.assertEqual(len(users), 1)
            blocks = users[0]["message"]["content"]
            self.assertEqual(blocks[0]["type"], "image")
            self.assertEqual(blocks[0]["source"]["media_type"], "image/png")
            self.assertEqual(blocks[0]["source"]["data"], self._PNG_B64)
            self.assertEqual(blocks[1], {"type": "text", "text": "看这张图"})

    def test_claude2codex_converts_image_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "claude.jsonl"
            out = Path(tmp) / "out.jsonl"
            records = [
                _claude_record("user", [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": self._PNG_B64}},
                    {"type": "text", "text": "这是截图"},
                ]),
                _claude_record("assistant", [{"type": "text", "text": "看到了"}]),
            ]
            src.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            self.script.claude_to_codex(str(src), out_path=str(out), install=False)
            converted = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            items = [r["payload"] for r in converted if r["type"] == "response_item"]
            user_msgs = [p for p in items if p["type"] == "message" and p["role"] == "user"]
            self.assertEqual(len(user_msgs), 1)
            content = user_msgs[0]["content"]
            self.assertEqual(content[0]["type"], "input_image")
            self.assertEqual(content[0]["image_url"], "data:image/png;base64," + self._PNG_B64)
            self.assertEqual(content[1], {"type": "input_text", "text": "这是截图"})
            events = [r["payload"] for r in converted if r["type"] == "event_msg"
                      and r["payload"].get("type") == "user_message"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["message"], "这是截图")
            self.assertEqual(events[0]["local_images"], [])  # install=False 不落盘

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

    def test_all_four_directions_skip_existing_output_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex = root / "rollout.jsonl"
            claude = root / "claude.jsonl"
            opencode_db = root / "opencode.db"
            outputs = [
                root / "codex-to-claude.jsonl",
                root / "claude-to-codex.jsonl",
                root / "opencode-to-codex.jsonl",
                root / "codex-to-opencode.json",
            ]
            codex.write_text("".join(json.dumps(r) + "\n" for r in [
                {
                    "timestamp": "2026-07-01T10:00:00.000Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "01234567-89ab-cdef-0123-456789abcdef",
                        "timestamp": "2026-07-01T10:00:00.000Z",
                        "cwd": r"C:\projects\demo",
                    },
                },
                _codex_user("question"),
                _codex_assistant("answer"),
            ]), encoding="utf-8")
            claude.write_text("".join(json.dumps(r) + "\n" for r in [
                _claude_record("user", "question"),
                _claude_record("assistant", [{"type": "text", "text": "answer"}]),
            ]), encoding="utf-8")
            self._make_opencode_db(opencode_db)

            self.script.codex_to_claude(str(codex), str(outputs[0]), install=False)
            self.script.claude_to_codex(str(claude), str(outputs[1]), install=False)
            self.script.opencode_to_codex(
                "ses_1", str(outputs[2]), install=False, db=str(opencode_db))
            self.script.codex_to_opencode(str(codex), str(outputs[3]))
            first = [path.read_bytes() for path in outputs]

            self.script.codex_to_claude(str(codex), str(outputs[0]), install=False)
            self.script.claude_to_codex(str(claude), str(outputs[1]), install=False)
            self.script.opencode_to_codex(
                "ses_1", str(outputs[2]), install=False, db=str(opencode_db))
            self.script.codex_to_opencode(str(codex), str(outputs[3]))

            self.assertEqual([path.read_bytes() for path in outputs], first)

    def test_opencode_to_codex_uses_stable_target_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "opencode.db"
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            self._make_opencode_db(db)
            self.script.opencode_to_codex(
                "ses_1", str(first), install=False, db=str(db))
            self.script.opencode_to_codex(
                "ses_1", str(second), install=False, db=str(db))
            first_id = json.loads(first.read_text(encoding="utf-8").splitlines()[0])[
                "payload"]["id"]
            second_id = json.loads(second.read_text(encoding="utf-8").splitlines()[0])[
                "payload"]["id"]
            self.assertEqual(first_id, second_id)

    def test_codex_index_upsert_does_not_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "session_index.jsonl"
            original = self.script.CODEX_INDEX_FILE
            self.script.CODEX_INDEX_FILE = str(index)
            try:
                self.script.upsert_codex_index("session-1", "Title one")
                self.script.upsert_codex_index("session-1", "Title two")
            finally:
                self.script.CODEX_INDEX_FILE = original
            entries = [json.loads(line) for line in index.read_text(
                encoding="utf-8-sig").splitlines()]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["thread_name"], "Title two")

    def test_codex_index_upsert_preserves_unparseable_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "session_index.jsonl"
            index.write_text("not-json\n", encoding="utf-8")
            original = self.script.CODEX_INDEX_FILE
            self.script.CODEX_INDEX_FILE = str(index)
            try:
                self.script.upsert_codex_index("session-1", "Title")
            finally:
                self.script.CODEX_INDEX_FILE = original
            lines = index.read_text(encoding="utf-8-sig").splitlines()
            self.assertEqual(lines[0], "not-json")
            self.assertEqual(json.loads(lines[1])["id"], "session-1")

    def test_fork_updates_embedded_ids_for_custom_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex = root / "rollout.jsonl"
            claude = root / "claude.jsonl"
            db = root / "opencode.db"
            codex.write_text("".join(json.dumps(r) + "\n" for r in [
                {
                    "timestamp": "2026-07-01T10:00:00.000Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "01234567-89ab-cdef-0123-456789abcdef",
                        "timestamp": "2026-07-01T10:00:00.000Z",
                        "cwd": r"C:\projects\demo",
                    },
                },
                _codex_user("question"),
            ]), encoding="utf-8")
            claude.write_text("".join(json.dumps(r) + "\n" for r in [
                _claude_record("user", "question"),
            ]), encoding="utf-8")
            self._make_opencode_db(db)

            claude_out = root / "claude-target.jsonl"
            codex_out = root / "codex-target.jsonl"
            opencode_codex_out = root / "oc-codex-target.jsonl"
            opencode_out = root / "opencode-target.json"
            for path in (claude_out, codex_out, opencode_codex_out, opencode_out):
                path.write_text("occupied", encoding="utf-8")

            claude_fork = Path(self.script.codex_to_claude(
                str(codex), str(claude_out), install=False, on_conflict="fork"))
            codex_fork = Path(self.script.claude_to_codex(
                str(claude), str(codex_out), install=False, on_conflict="fork"))
            oc_codex_fork = Path(self.script.opencode_to_codex(
                "ses_1", str(opencode_codex_out), install=False, db=str(db),
                on_conflict="fork"))
            opencode_fork = Path(self.script.codex_to_opencode(
                str(codex), str(opencode_out), on_conflict="fork"))

            claude_record = json.loads(claude_fork.read_text(
                encoding="utf-8").splitlines()[0])
            codex_record = json.loads(codex_fork.read_text(
                encoding="utf-8").splitlines()[0])
            oc_codex_record = json.loads(oc_codex_fork.read_text(
                encoding="utf-8").splitlines()[0])
            opencode_export = json.loads(opencode_fork.read_text(encoding="utf-8"))
            self.assertIn(claude_record["sessionId"][:8], claude_fork.name)
            self.assertIn(codex_record["payload"]["id"][:8], codex_fork.name)
            self.assertIn(oc_codex_record["payload"]["id"][:8], oc_codex_fork.name)
            self.assertIn(opencode_export["info"]["id"][4:12], opencode_fork.name)

    def test_isolated_installs_are_idempotent_and_cleaned_up(self) -> None:
        with tempfile.TemporaryDirectory(prefix="session-bridge-isolation-") as tmp:
            root = Path(tmp)
            codex_source = root / "source-rollout.jsonl"
            claude_source = root / "source-claude.jsonl"
            db = root / "opencode.db"
            opencode_export = root / "codex.opencode.json"
            codex_source.write_text("".join(json.dumps(r) + "\n" for r in [
                {
                    "timestamp": "2026-07-01T10:00:00.000Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "01234567-89ab-cdef-0123-456789abcdef",
                        "timestamp": "2026-07-01T10:00:00.000Z",
                        "cwd": r"C:\projects\demo",
                    },
                },
                _codex_user("question"),
                _codex_assistant("answer"),
            ]), encoding="utf-8")
            claude_source.write_text("".join(json.dumps(r) + "\n" for r in [
                _claude_record("user", "question"),
                _claude_record("assistant", [{"type": "text", "text": "answer"}]),
            ]), encoding="utf-8")
            self._make_opencode_db(db)

            originals = {
                "HOME": self.script.HOME,
                "CODEX_SESSIONS_DIR": self.script.CODEX_SESSIONS_DIR,
                "CODEX_INDEX_FILE": self.script.CODEX_INDEX_FILE,
                "CLAUDE_PROJECTS_DIR": self.script.CLAUDE_PROJECTS_DIR,
                "CLAUDE_DESKTOP_DIR": self.script.CLAUDE_DESKTOP_DIR,
            }
            self.script.HOME = str(root)
            self.script.CODEX_SESSIONS_DIR = str(root / ".codex" / "sessions")
            self.script.CODEX_INDEX_FILE = str(root / ".codex" / "session_index.jsonl")
            self.script.CLAUDE_PROJECTS_DIR = str(root / ".claude" / "projects")
            self.script.CLAUDE_DESKTOP_DIR = str(root / "claude-desktop")
            try:
                for _ in range(2):
                    self.script.codex_to_claude(str(codex_source))
                    self.script.claude_to_codex(str(claude_source))
                    self.script.opencode_to_codex("ses_1", db=str(db))
                    self.script.codex_to_opencode(
                        str(codex_source), str(opencode_export))

                claude_files = list((root / ".claude" / "projects").rglob("*.jsonl"))
                codex_files = list((root / ".codex" / "sessions").rglob("*.jsonl"))
                index_lines = (root / ".codex" / "session_index.jsonl").read_text(
                    encoding="utf-8-sig").splitlines()
                self.assertEqual(len(claude_files), 1)
                self.assertEqual(len(codex_files), 2)
                self.assertEqual(len(index_lines), 2)
                self.assertTrue(opencode_export.is_file())
            finally:
                for name, value in originals.items():
                    setattr(self.script, name, value)

    def _make_desktop_registry(self, root: Path) -> Path:
        target = root / "org-1" / "user-1"
        target.mkdir(parents=True)
        template = {
            "sessionId": "local_00000000-0000-4000-8000-000000000000",
            "cliSessionId": "some-other-session",
            "cwd": r"C:\old\path", "originCwd": r"C:\old\path",
            "title": "old title", "titleSource": "auto",
            "createdAt": 1, "lastActivityAt": 1, "lastFocusedAt": 1,
            "model": "claude-fable-5", "isArchived": False,
            "permissionMode": "auto", "completedTurns": 2,
            "sessionPermissionUpdates": [{"type": "addRules"}],
        }
        (target / (template["sessionId"] + ".json")).write_text(
            json.dumps(template), encoding="utf-8")
        return target

    def test_register_claude_desktop_creates_entry_from_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self._make_desktop_registry(root)
            original = self.script.CLAUDE_DESKTOP_DIR
            self.script.CLAUDE_DESKTOP_DIR = str(root)
            try:
                ok = self.script.register_claude_desktop(
                    "cli-sess-1", "我的标题", r"C:\projects\demo", 5)
            finally:
                self.script.CLAUDE_DESKTOP_DIR = original
            self.assertTrue(ok)
            files = sorted(target.glob("local_*.json"))
            self.assertEqual(len(files), 2)
            entries = [json.loads(f.read_text(encoding="utf-8")) for f in files]
            new = next(e for e in entries if e["cliSessionId"] == "cli-sess-1")
            self.assertEqual(new["title"], "我的标题")
            self.assertEqual(new["cwd"], r"C:\projects\demo")
            self.assertEqual(new["completedTurns"], 5)
            self.assertEqual(new["sessionPermissionUpdates"], [])
            self.assertNotEqual(new["sessionId"], "local_00000000-0000-4000-8000-000000000000")

    def test_register_claude_desktop_updates_existing_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self._make_desktop_registry(root)
            original = self.script.CLAUDE_DESKTOP_DIR
            self.script.CLAUDE_DESKTOP_DIR = str(root)
            try:
                self.script.register_claude_desktop("cli-sess-1", "标题一", r"C:\p", 3)
                self.script.register_claude_desktop("cli-sess-1", "标题二", r"C:\p", 4)
            finally:
                self.script.CLAUDE_DESKTOP_DIR = original
            files = sorted(target.glob("local_*.json"))
            self.assertEqual(len(files), 2)  # 模板 + 注册条目, 没有重复
            entries = [json.loads(f.read_text(encoding="utf-8")) for f in files]
            mine = [e for e in entries if e["cliSessionId"] == "cli-sess-1"]
            self.assertEqual(len(mine), 1)
            self.assertEqual(mine[0]["title"], "标题二")
            self.assertEqual(mine[0]["completedTurns"], 4)

    def test_register_claude_desktop_missing_dir_is_noop(self) -> None:
        original = self.script.CLAUDE_DESKTOP_DIR
        self.script.CLAUDE_DESKTOP_DIR = r"C:\nonexistent-desktop-dir-xyz"
        try:
            ok = self.script.register_claude_desktop("cli-1", "t", "c", 1)
        finally:
            self.script.CLAUDE_DESKTOP_DIR = original
        self.assertFalse(ok)

    def test_register_claude_desktop_no_template_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "org-1" / "user-1").mkdir(parents=True)  # 空目录, 无模板
            original = self.script.CLAUDE_DESKTOP_DIR
            self.script.CLAUDE_DESKTOP_DIR = tmp
            try:
                ok = self.script.register_claude_desktop("cli-1", "t", "c", 1)
            finally:
                self.script.CLAUDE_DESKTOP_DIR = original
            self.assertFalse(ok)

    def _make_opencode_db(self, path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT,
                time_created INTEGER, time_updated INTEGER, time_archived INTEGER);
            CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
                time_created INTEGER, time_updated INTEGER, data TEXT);
            CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
                time_created INTEGER, time_updated INTEGER, data TEXT);
            """
        )
        connection.execute(
            "INSERT INTO session VALUES ('ses_1', '测试会话', 'C:/projects/demo', 1751364000000, 1751364100000, NULL)")
        connection.execute(
            "INSERT INTO message VALUES ('m1', 'ses_1', 1751364001000, 0, ?)",
            (json.dumps({"role": "user"}),))
        connection.execute(
            "INSERT INTO part VALUES ('p1', 'm1', 'ses_1', 1751364001000, 0, ?)",
            (json.dumps({"type": "text", "text": "帮我看看"}),))
        connection.execute(
            "INSERT INTO message VALUES ('m2', 'ses_1', 1751364002000, 0, ?)",
            (json.dumps({"role": "assistant"}),))
        connection.execute(
            "INSERT INTO part VALUES ('p2', 'm2', 'ses_1', 1751364002000, 0, ?)",
            (json.dumps({"type": "text", "text": "看好了"}),))
        connection.execute(  # 非文本 part 应被忽略
            "INSERT INTO part VALUES ('p3', 'm2', 'ses_1', 1751364003000, 0, ?)",
            (json.dumps({"type": "tool", "name": "bash"}),))
        connection.commit()
        connection.close()

    def test_opencode2codex_converts_live_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "opencode.db"
            out = Path(tmp) / "out.jsonl"
            self._make_opencode_db(db)
            self.script.opencode_to_codex("ses_1", out_path=str(out),
                                          install=False, db=str(db))
            converted = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            meta = converted[0]
            self.assertEqual(meta["type"], "session_meta")
            self.assertEqual(meta["payload"]["cwd"], "C:/projects/demo")
            items = [r["payload"] for r in converted if r["type"] == "response_item"]
            self.assertEqual(
                [(p["role"], p["content"][0]["text"]) for p in items],
                [("user", "帮我看看"), ("assistant", "看好了")],
            )
            events = [r["payload"]["type"] for r in converted if r["type"] == "event_msg"]
            self.assertEqual(events, ["user_message", "agent_message"])

    def test_codex2opencode_exports_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "rollout.jsonl"
            out = Path(tmp) / "export.json"
            records = [
                {
                    "timestamp": "2026-07-01T10:00:00.000Z",
                    "type": "session_meta",
                    "payload": {"id": "s1", "timestamp": "2026-07-01T10:00:00.000Z", "cwd": r"C:\projects\demo"},
                },
                _codex_user("<environment_context>ctx</environment_context>"),
                _codex_user("问题"),
                _codex_assistant("答案"),
                _codex_user("悬空重复"),
                _codex_user("悬空重复"),
            ]
            src.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            self.script.codex_to_opencode(str(src), out_path=str(out))
            export = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(export["info"]["title"], "问题")
            self.assertEqual(export["info"]["directory"], r"C:\projects\demo")
            texts = [(m["info"]["role"], m["parts"][0]["text"]) for m in export["messages"]]
            self.assertEqual(texts, [("user", "问题"), ("assistant", "答案"), ("user", "悬空重复")])
            assistant = export["messages"][1]["info"]
            self.assertEqual(assistant["parentID"], export["messages"][0]["info"]["id"])

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
