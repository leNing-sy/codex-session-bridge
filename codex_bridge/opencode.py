from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from codex_bridge.models import Conversation, ConversationMessage, group_messages
from session_sdk.models import SessionSummary


class OpenCodeDatabaseStore:
    """Read OpenCode's live SQLite database without modifying it."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()

    @classmethod
    def discover(cls, explicit_path: Path | None = None) -> OpenCodeDatabaseStore:
        candidates = []
        if explicit_path is not None:
            candidates.append(explicit_path)
        home = Path.home()
        candidates.extend(
            [
                home / ".local" / "share" / "opencode" / "opencode.db",
                home / "AppData" / "Roaming" / "opencode" / "opencode.db",
                home / "AppData" / "Local" / "opencode" / "opencode.db",
            ]
        )
        for candidate in candidates:
            if candidate.is_file():
                return cls(candidate)
        searched = "\n".join(f"- {candidate}" for candidate in candidates)
        raise FileNotFoundError(f"OpenCode database not found. Searched:\n{searched}")

    def list(self, *, limit: int | None = None) -> list[SessionSummary]:
        sql = """
            SELECT s.id, s.directory, s.title, s.time_created,
                   (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id) AS message_count
            FROM session s
            WHERE s.time_archived IS NULL
            ORDER BY s.time_updated DESC
        """
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        connection = self._connect()
        try:
            rows = connection.execute(sql, params).fetchall()
        finally:
            connection.close()
        return [
            SessionSummary(
                provider="opencode",
                session_id=row["id"],
                cwd=row["directory"],
                timestamp=_iso_from_ms(row["time_created"]),
                path=self.database_path,
                message_count=row["message_count"],
            )
            for row in rows
        ]

    def load_conversation(self, session_id: str) -> Conversation:
        connection = self._connect()
        try:
            session = connection.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise FileNotFoundError(f"OpenCode session not found: {session_id}")
            message_rows = connection.execute(
                "SELECT * FROM message WHERE session_id = ? ORDER BY time_created, id",
                (session_id,),
            ).fetchall()
            part_rows = connection.execute(
                "SELECT * FROM part WHERE session_id = ? ORDER BY time_created, id",
                (session_id,),
            ).fetchall()
        finally:
            connection.close()

        text_by_message: dict[str, list[str]] = defaultdict(list)
        for row in part_rows:
            part = _json_object(row["data"])
            if part.get("type") != "text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_by_message[row["message_id"]].append(text.strip())

        messages: list[ConversationMessage] = []
        for row in message_rows:
            data = _json_object(row["data"])
            role = data.get("role")
            if role not in {"user", "assistant"}:
                continue
            text = "\n\n".join(text_by_message.get(row["id"], [])).strip()
            if not text:
                continue
            timestamp_ms = _message_timestamp_ms(data, row["time_created"], row["time_updated"])
            model, provider = _model_metadata(data)
            messages.append(
                ConversationMessage(
                    source_id=row["id"],
                    role=role,
                    text=text,
                    timestamp_ms=timestamp_ms,
                    model=model,
                    provider=provider,
                )
            )

        turns, unpaired = group_messages(messages)
        if not turns:
            raise ValueError(f"OpenCode session contains no user text messages: {session_id}")
        title = (session["title"] or turns[0].user.text).strip()
        return Conversation(
            source="opencode",
            source_id=session_id,
            title=title,
            cwd=session["directory"] or str(Path.home()),
            created_at_ms=session["time_created"],
            turns=tuple(turns),
            unpaired_assistants=tuple(unpaired),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection


def _json_object(value: str) -> dict[str, object]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("OpenCode database JSON value is not an object")
    return parsed


def _message_timestamp_ms(data: dict[str, object], created: int, updated: int) -> int:
    time = data.get("time")
    if isinstance(time, dict):
        completed = time.get("completed")
        source_created = time.get("created")
        if isinstance(completed, int):
            return completed
        if isinstance(source_created, int):
            return source_created
    return updated if updated >= created else created


def _model_metadata(data: dict[str, object]) -> tuple[str | None, str | None]:
    model = data.get("model")
    if isinstance(model, dict):
        model_id = model.get("modelID")
        provider_id = model.get("providerID")
    else:
        model_id = data.get("modelID")
        provider_id = data.get("providerID")
    return (
        model_id if isinstance(model_id, str) else None,
        provider_id if isinstance(provider_id, str) else None,
    )


def _iso_from_ms(timestamp_ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
