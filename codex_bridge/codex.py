from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from codex_bridge.models import Conversation, ImportResult, ValidationResult
from session_sdk.json_types import JsonObject
from session_sdk.jsonl import JsonlFile


BRIDGE_VERSION = "0.4.3"


def iso_from_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def uuid7_from_ms(timestamp_ms: int, entropy: str) -> str:
    timestamp_bits = timestamp_ms & ((1 << 48) - 1)
    random_bits = int.from_bytes(hashlib.sha256(entropy.encode("utf-8")).digest()[:10], "big")
    random_a = (random_bits >> 62) & 0xFFF
    random_b = random_bits & ((1 << 62) - 1)
    value = timestamp_bits << 80
    value |= 0x7 << 76
    value |= random_a << 64
    value |= 0b10 << 62
    value |= random_b
    return str(UUID(int=value))


class CodexRolloutBuilder:
    def build(self, conversation: Conversation, *, session_id: str | None = None) -> tuple[str, list[JsonObject]]:
        resolved_id = session_id or uuid7_from_ms(
            conversation.created_at_ms,
            f"{conversation.source}:{conversation.source_id}",
        )
        created_at = iso_from_ms(conversation.created_at_ms)
        records: list[JsonObject] = [
            {
                "timestamp": created_at,
                "type": "session_meta",
                "payload": {
                    "session_id": resolved_id,
                    "id": resolved_id,
                    "timestamp": created_at,
                    "cwd": conversation.cwd,
                    "originator": "codex_session_bridge",
                    "cli_version": f"codex-session-bridge/{BRIDGE_VERSION}",
                    "source": "vscode",
                    "thread_source": "user",
                    "model_provider": "custom",
                    "history_mode": "legacy",
                },
            }
        ]

        for position, turn in enumerate(conversation.turns):
            user_at = iso_from_ms(turn.user.timestamp_ms)
            assistant_at = iso_from_ms(turn.completed_at_ms)
            turn_id = uuid7_from_ms(
                turn.user.timestamp_ms,
                f"{resolved_id}:turn:{position}:{turn.user.source_id}",
            )
            records.extend(
                [
                    {
                        "timestamp": user_at,
                        "type": "event_msg",
                        "payload": {
                            "type": "task_started",
                            "turn_id": turn_id,
                            "started_at": user_at,
                            "model_context_window": 243200,
                            "collaboration_mode_kind": "default",
                        },
                    },
                    {
                        "timestamp": user_at,
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": turn.user.text}],
                        },
                    },
                    {
                        "timestamp": user_at,
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": turn.user.text,
                            "images": [],
                            "local_images": [],
                            "text_elements": [],
                        },
                    },
                ]
            )

            assistant_text = turn.assistant_text
            if assistant_text:
                response_id = "msg_" + hashlib.sha256(
                    f"{resolved_id}:assistant:{position}".encode("utf-8")
                ).hexdigest()[:24]
                records.extend(
                    [
                        {
                            "timestamp": assistant_at,
                            "type": "event_msg",
                            "payload": {
                                "type": "agent_message",
                                "message": assistant_text,
                                "phase": "final_answer",
                            },
                        },
                        {
                            "timestamp": assistant_at,
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "id": response_id,
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": assistant_text}],
                                "phase": "final_answer",
                            },
                        },
                    ]
                )

            records.append(
                {
                    "timestamp": assistant_at,
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": turn_id,
                        "last_agent_message": assistant_text or None,
                        "completed_at": assistant_at,
                        "duration_ms": max(0, turn.completed_at_ms - turn.user.timestamp_ms),
                    },
                }
            )
        return resolved_id, records


@dataclass(frozen=True, slots=True)
class _Backup:
    root: Path
    rollout: Path | None
    state_database: Path | None
    session_index: Path | None


class CodexImporter:
    def __init__(self, codex_home: Path) -> None:
        self.codex_home = codex_home.expanduser().resolve()
        self.builder = CodexRolloutBuilder()

    def plan(self, conversation: Conversation, *, session_id: str | None = None) -> tuple[str, Path, list[JsonObject]]:
        resolved_id, records = self.builder.build(conversation, session_id=session_id)
        created = datetime.fromtimestamp(conversation.created_at_ms / 1000, UTC)
        stamp = created.strftime("%Y-%m-%dT%H-%M-%S-%f")[:23]
        destination = (
            self.codex_home
            / "sessions"
            / f"{created.year:04d}"
            / f"{created.month:02d}"
            / f"{created.day:02d}"
            / f"rollout-{stamp}-{resolved_id}.jsonl"
        )
        return resolved_id, destination, records

    def install(
        self,
        conversation: Conversation,
        *,
        session_id: str | None = None,
        overwrite: bool = False,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> ImportResult:
        resolved_id, destination, records = self.plan(conversation, session_id=session_id)
        if destination.exists() and not overwrite:
            raise FileExistsError(f"Codex session already exists: {destination}")

        backup = self._backup(resolved_id, destination)
        warnings: list[str] = []
        if conversation.unpaired_assistants:
            warnings.append(
                f"Skipped {len(conversation.unpaired_assistants)} assistant message(s) that appeared before the first user turn."
            )
        try:
            self._write_rollout(destination, records, overwrite=overwrite)
            registrar = _CodexStateRegistrar(self.codex_home)
            registrar.register(
                conversation,
                session_id=resolved_id,
                rollout_path=destination,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            _SessionIndex(self.codex_home / "session_index.jsonl").upsert(
                resolved_id,
                conversation.title,
            )
            validation = CodexSessionVerifier(self.codex_home).verify(
                resolved_id,
                expected_turns=len(conversation.turns),
                expected_assistant_turns=sum(bool(turn.assistant_text) for turn in conversation.turns),
            )
            if not validation.valid:
                raise RuntimeError("Codex validation failed: " + "; ".join(validation.errors))
        except Exception:
            self._restore(backup, destination)
            raise

        return ImportResult(
            session_id=resolved_id,
            rollout_path=destination,
            title=conversation.title,
            turn_count=len(conversation.turns),
            assistant_turn_count=sum(bool(turn.assistant_text) for turn in conversation.turns),
            validation=validation,
            backup_dir=backup.root,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _write_rollout(destination: Path, records: list[JsonObject], *, overwrite: bool) -> None:
        if destination.exists() and not overwrite:
            raise FileExistsError(f"Codex session already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        stale_temp = destination.with_suffix(destination.suffix + ".tmp")
        if stale_temp.exists():
            stale_temp.unlink()
        bridge_temp = destination.with_suffix(destination.suffix + ".bridge-tmp")
        JsonlFile(bridge_temp).write(records, overwrite=True)
        try:
            os.replace(bridge_temp, destination)
        except PermissionError:
            # Windows can keep an idle Codex rollout open with sharing that permits
            # writes but rejects rename replacement. The caller already made a backup.
            try:
                with bridge_temp.open("rb") as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
            finally:
                bridge_temp.unlink(missing_ok=True)

    def _backup(self, session_id: str, destination: Path) -> _Backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        root = self.codex_home / "backups" / "codex-session-bridge" / f"{stamp}-{session_id[:8]}"
        root.mkdir(parents=True, exist_ok=False)
        rollout_backup = None
        if destination.exists():
            rollout_backup = root / destination.name
            shutil.copy2(destination, rollout_backup)

        index_path = self.codex_home / "session_index.jsonl"
        index_backup = None
        if index_path.exists():
            index_backup = root / index_path.name
            shutil.copy2(index_path, index_backup)

        state_path = self.codex_home / "state_5.sqlite"
        state_backup = None
        if state_path.exists():
            state_backup = root / state_path.name
            source = sqlite3.connect(state_path)
            target = sqlite3.connect(state_backup)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()

        manifest = {
            "session_id": session_id,
            "destination": str(destination),
            "had_rollout": rollout_backup is not None,
            "had_state_database": state_backup is not None,
            "had_session_index": index_backup is not None,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return _Backup(root, rollout_backup, state_backup, index_backup)

    def _restore(self, backup: _Backup, destination: Path) -> None:
        if backup.rollout is not None:
            with backup.rollout.open("rb") as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
        elif destination.exists():
            destination.unlink()

        state_path = self.codex_home / "state_5.sqlite"
        if backup.state_database is not None:
            source = sqlite3.connect(backup.state_database)
            target = sqlite3.connect(state_path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()

        index_path = self.codex_home / "session_index.jsonl"
        if backup.session_index is not None:
            shutil.copy2(backup.session_index, index_path)


class _CodexStateRegistrar:
    def __init__(self, codex_home: Path) -> None:
        self.database_path = codex_home / "state_5.sqlite"

    def register(
        self,
        conversation: Conversation,
        *,
        session_id: str,
        rollout_path: Path,
        model: str | None,
        reasoning_effort: str | None,
    ) -> None:
        if not self.database_path.exists():
            raise FileNotFoundError(f"Codex state database not found: {self.database_path}")
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            schema = connection.execute("PRAGMA table_info(threads)").fetchall()
            if not schema:
                raise RuntimeError("Unsupported Codex state database: threads table is missing")
            columns = {row["name"]: row for row in schema}
            template = self._template(connection, conversation.cwd, session_id)
            values = self._values(
                conversation,
                session_id=session_id,
                rollout_path=rollout_path,
                template=template,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            missing = [
                name
                for name, row in columns.items()
                if row["notnull"] and row["dflt_value"] is None and not row["pk"] and name not in values
            ]
            if missing:
                raise RuntimeError(
                    "Unsupported Codex state database; required columns are not handled: " + ", ".join(missing)
                )
            usable = {name: value for name, value in values.items() if name in columns}
            exists = connection.execute("SELECT 1 FROM threads WHERE id = ?", (session_id,)).fetchone() is not None
            connection.execute("BEGIN IMMEDIATE")
            if exists:
                assignments = ", ".join(f'"{name}" = ?' for name in usable if name != "id")
                parameters = [usable[name] for name in usable if name != "id"] + [session_id]
                connection.execute(f"UPDATE threads SET {assignments} WHERE id = ?", parameters)
            else:
                names = list(usable)
                placeholders = ", ".join("?" for _ in names)
                quoted = ", ".join(f'"{name}"' for name in names)
                connection.execute(
                    f"INSERT INTO threads ({quoted}) VALUES ({placeholders})",
                    [usable[name] for name in names],
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _template(connection: sqlite3.Connection, cwd: str, session_id: str) -> dict[str, object]:
        candidates = (cwd, _windows_extended_path(cwd))
        row = connection.execute(
            """
            SELECT * FROM threads
            WHERE id <> ? AND source = 'vscode' AND cwd IN (?, ?)
            ORDER BY updated_at_ms DESC LIMIT 1
            """,
            (session_id, *candidates),
        ).fetchone()
        if row is None:
            row = connection.execute(
                "SELECT * FROM threads WHERE id <> ? AND source = 'vscode' ORDER BY updated_at_ms DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return dict(row) if row is not None else {}

    @staticmethod
    def _values(
        conversation: Conversation,
        *,
        session_id: str,
        rollout_path: Path,
        template: dict[str, object],
        model: str | None,
        reasoning_effort: str | None,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        now_s = int(now.timestamp())
        now_ms = int(now.timestamp() * 1000)
        created_s = conversation.created_at_ms // 1000
        title = _clean_title(conversation.title or conversation.first_user_message)
        preview = conversation.first_user_message[:1000]
        return {
            "id": session_id,
            "rollout_path": _windows_extended_path(str(rollout_path)),
            "created_at": created_s,
            "updated_at": now_s,
            "source": template.get("source") or "vscode",
            "model_provider": template.get("model_provider") or "custom",
            "cwd": _windows_extended_path(conversation.cwd),
            "title": title,
            "sandbox_policy": template.get("sandbox_policy") or '{"type":"read-only"}',
            "approval_mode": template.get("approval_mode") or "on-request",
            "tokens_used": 0,
            "has_user_event": 1,
            "archived": 0,
            "archived_at": None,
            "cli_version": template.get("cli_version") or f"codex-session-bridge/{BRIDGE_VERSION}",
            "first_user_message": conversation.first_user_message,
            "agent_nickname": None,
            "agent_role": None,
            "memory_mode": template.get("memory_mode") or "enabled",
            "model": model or template.get("model"),
            "reasoning_effort": reasoning_effort or template.get("reasoning_effort"),
            "agent_path": None,
            "created_at_ms": conversation.created_at_ms,
            "updated_at_ms": now_ms,
            "thread_source": "user",
            "preview": preview,
            "recency_at": now_s,
            "recency_at_ms": now_ms,
            "history_mode": "legacy",
        }


class _SessionIndex:
    def __init__(self, path: Path) -> None:
        self.path = path

    def upsert(self, session_id: str, title: str) -> None:
        entries: list[JsonObject] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                value = json.loads(line)
                if isinstance(value, dict):
                    entries.append(value)
        updated_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        replacement: JsonObject = {
            "id": session_id,
            "thread_name": _clean_title(title),
            "updated_at": updated_at,
        }
        for index, entry in enumerate(entries):
            if entry.get("id") == session_id:
                entries[index] = replacement
                break
        else:
            entries.append(replacement)
        content = "".join(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n" for entry in entries)
        _atomic_write_text(self.path, content)


class CodexSessionVerifier:
    def __init__(self, codex_home: Path) -> None:
        self.codex_home = codex_home.expanduser().resolve()

    def verify(
        self,
        session_id: str,
        *,
        expected_turns: int | None = None,
        expected_assistant_turns: int | None = None,
    ) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        rollout = self._find_rollout(session_id)
        if rollout is None:
            return ValidationResult(False, (f"Rollout file not found for {session_id}",))
        records = JsonlFile(rollout).read()
        meta = next((record.get("payload") for record in records if record.get("type") == "session_meta"), None)
        if not isinstance(meta, dict) or meta.get("id") != session_id:
            errors.append("session_meta id does not match the requested session")

        events = [record.get("payload") for record in records if record.get("type") == "event_msg"]
        task_started = [event for event in events if isinstance(event, dict) and event.get("type") == "task_started"]
        task_complete = [event for event in events if isinstance(event, dict) and event.get("type") == "task_complete"]
        user_events = [event for event in events if isinstance(event, dict) and event.get("type") == "user_message"]
        agent_events = [event for event in events if isinstance(event, dict) and event.get("type") == "agent_message"]
        started_ids = {event.get("turn_id") for event in task_started}
        completed_ids = {event.get("turn_id") for event in task_complete}
        if started_ids != completed_ids:
            errors.append("task_started and task_complete turn IDs do not match")
        if len(user_events) != len(task_started):
            errors.append("user_message count does not match task_started count")
        if expected_turns is not None and len(task_started) != expected_turns:
            errors.append(f"expected {expected_turns} turns, found {len(task_started)}")
        if expected_assistant_turns is not None and len(agent_events) != expected_assistant_turns:
            errors.append(f"expected {expected_assistant_turns} assistant turns, found {len(agent_events)}")

        state_path = self.codex_home / "state_5.sqlite"
        if state_path.exists():
            connection = sqlite3.connect(f"file:{state_path.as_posix()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                row = connection.execute(
                    "SELECT title, preview, has_user_event, rollout_path FROM threads WHERE id = ?",
                    (session_id,),
                ).fetchone()
            finally:
                connection.close()
            if row is None:
                errors.append("session is missing from the Codex threads table")
            else:
                if not row["title"]:
                    errors.append("Codex thread title is empty")
                if not row["preview"]:
                    errors.append("Codex thread preview is empty")
                if row["has_user_event"] != 1:
                    errors.append("Codex thread is marked as having no user event")
        else:
            warnings.append("Codex state database is absent; list visibility could not be verified")

        index_path = self.codex_home / "session_index.jsonl"
        if index_path.exists():
            indexed = any(
                isinstance(value, dict) and value.get("id") == session_id
                for value in (
                    json.loads(line)
                    for line in index_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            )
            if not indexed:
                errors.append("session is missing from session_index.jsonl")
        else:
            warnings.append("session_index.jsonl is absent")

        return ValidationResult(
            valid=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            turn_count=len(task_started),
            user_message_count=len(user_events),
            assistant_message_count=len(agent_events),
        )

    def _find_rollout(self, session_id: str) -> Path | None:
        roots = (self.codex_home / "sessions", self.codex_home / "archived_sessions")
        for root in roots:
            if not root.exists():
                continue
            matches = list(root.rglob(f"*{session_id}.jsonl"))
            if matches:
                return matches[0]
        return None


def _clean_title(value: str) -> str:
    cleaned = " ".join(value.split())
    return cleaned[:120] or "Imported conversation"


def _windows_extended_path(value: str) -> str:
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\" + value


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
