from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codex_bridge.models import Conversation, ConversationMessage, group_messages
from session_sdk.converters import MessageExtractor
from session_sdk.models import SessionSummary, TextMessage
from session_sdk.stores import ClaudeStore


class ClaudeCodeConversationStore:
    """Adapt Claude Code JSONL transcripts to the bridge conversation model."""

    def __init__(self, claude_home: Path, session_dir: Path | None = None) -> None:
        self.claude_home = claude_home.expanduser().resolve()
        self.session_dir = session_dir.expanduser().resolve() if session_dir is not None else None
        self._store = ClaudeStore(self.claude_home, self.session_dir)
        self._extractor = MessageExtractor()

    @classmethod
    def discover(
        cls,
        explicit_home: Path | None = None,
        session_dir: Path | None = None,
    ) -> ClaudeCodeConversationStore:
        home = (explicit_home or (Path.home() / ".claude")).expanduser()
        root = session_dir or (home / "projects")
        if not root.is_dir():
            raise FileNotFoundError(f"Claude Code session directory not found: {root}")
        return cls(home, session_dir)

    def list(self, *, limit: int | None = None) -> list[SessionSummary]:
        sessions = self._store.list()
        sessions.sort(key=lambda session: session.timestamp, reverse=True)
        return sessions[:limit] if limit is not None else sessions

    def load_conversation(self, session_id: str) -> Conversation:
        native = self._store.load(session_id)
        extracted = self._extractor.from_claude(native)
        messages = [
            self._convert_message(message, position)
            for position, message in enumerate(extracted)
            if not message.is_contextual and not message.is_compaction and message.text.strip()
        ]
        turns, unpaired = group_messages(messages)
        if not turns:
            raise ValueError(f"Claude Code session contains no user text messages: {session_id}")
        created_at_ms = _epoch_ms(native.timestamp)
        if created_at_ms <= 0:
            created_at_ms = turns[0].user.timestamp_ms
        title = _title_from_text(turns[0].user.text)
        return Conversation(
            source="claude",
            source_id=native.session_id,
            title=title,
            cwd=native.cwd or str(Path.home()),
            created_at_ms=created_at_ms,
            turns=tuple(turns),
            unpaired_assistants=tuple(unpaired),
        )

    @staticmethod
    def _convert_message(message: TextMessage, position: int) -> ConversationMessage:
        timestamp_ms = _epoch_ms(message.timestamp)
        if timestamp_ms <= 0:
            timestamp_ms = int(datetime.now(UTC).timestamp() * 1000) + position
        return ConversationMessage(
            source_id=f"claude-{position}",
            role=message.role,
            text=message.text.strip(),
            timestamp_ms=timestamp_ms,
            model=message.model,
            provider="anthropic" if message.role == "assistant" else None,
        )


def _epoch_ms(value: str) -> int:
    if not value:
        return 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int(parsed.timestamp() * 1000)


def _title_from_text(value: str) -> str:
    cleaned = " ".join(value.split())
    return cleaned[:80] or "Imported Claude Code conversation"
