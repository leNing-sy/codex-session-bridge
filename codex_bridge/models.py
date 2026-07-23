from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    source_id: str
    role: str
    text: str
    timestamp_ms: int
    model: str | None = None
    provider: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    user: ConversationMessage
    assistants: tuple[ConversationMessage, ...] = ()

    @property
    def assistant_text(self) -> str:
        return "\n\n".join(message.text for message in self.assistants if message.text).strip()

    @property
    def completed_at_ms(self) -> int:
        if self.assistants:
            return max(message.timestamp_ms for message in self.assistants)
        return self.user.timestamp_ms


@dataclass(frozen=True, slots=True)
class Conversation:
    source: str
    source_id: str
    title: str
    cwd: str
    created_at_ms: int
    turns: tuple[ConversationTurn, ...]
    unpaired_assistants: tuple[ConversationMessage, ...] = ()

    @property
    def first_user_message(self) -> str:
        return self.turns[0].user.text if self.turns else ""

    @property
    def last_user_message(self) -> str:
        return self.turns[-1].user.text if self.turns else ""

    @property
    def assistant_count(self) -> int:
        return sum(len(turn.assistants) for turn in self.turns)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    turn_count: int = 0
    user_message_count: int = 0
    assistant_message_count: int = 0


@dataclass(frozen=True, slots=True)
class ImportResult:
    session_id: str
    rollout_path: Path
    title: str
    turn_count: int
    assistant_turn_count: int
    validation: ValidationResult
    backup_dir: Path | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def group_messages(
    messages: list[ConversationMessage],
) -> tuple[list[ConversationTurn], list[ConversationMessage]]:
    turns: list[ConversationTurn] = []
    unpaired: list[ConversationMessage] = []
    current_user: ConversationMessage | None = None
    assistants: list[ConversationMessage] = []
    for message in messages:
        if message.role == "user":
            if current_user is not None:
                turns.append(ConversationTurn(current_user, tuple(assistants)))
            current_user = message
            assistants = []
        elif current_user is None:
            unpaired.append(message)
        else:
            assistants.append(message)
    if current_user is not None:
        turns.append(ConversationTurn(current_user, tuple(assistants)))
    return turns, unpaired
