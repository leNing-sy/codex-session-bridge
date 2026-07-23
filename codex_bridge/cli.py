from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from codex_bridge.claude import ClaudeCodeConversationStore
from codex_bridge.codex import CodexImporter, CodexSessionVerifier
from codex_bridge.models import Conversation
from codex_bridge.opencode import OpenCodeDatabaseStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-bridge",
        description="Import AI coding conversations into Codex as visible, resumable desktop threads.",
    )
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_opencode = subparsers.add_parser("list-opencode", help="List sessions in the live OpenCode database.")
    list_opencode.add_argument("--opencode-db", type=Path)
    list_opencode.add_argument("--limit", type=int, default=30)
    list_opencode.add_argument("--json", action="store_true")

    import_opencode = subparsers.add_parser("import-opencode", help="Import one OpenCode session into Codex.")
    import_opencode.add_argument("session_id")
    import_opencode.add_argument("--opencode-db", type=Path)
    import_opencode.add_argument("--target-id")
    import_opencode.add_argument("--write", action="store_true", help="Apply the import. The default is a dry run.")
    import_opencode.add_argument("--overwrite", action="store_true")
    import_opencode.add_argument("--model")
    import_opencode.add_argument("--reasoning-effort")
    import_opencode.add_argument("--json", action="store_true")

    list_claude = subparsers.add_parser("list-claude", help="List Claude Code sessions.")
    list_claude.add_argument("--claude-home", type=Path)
    list_claude.add_argument("--claude-session-dir", type=Path)
    list_claude.add_argument("--limit", type=int, default=30)
    list_claude.add_argument("--json", action="store_true")

    import_claude = subparsers.add_parser("import-claude", help="Import one Claude Code session into Codex.")
    import_claude.add_argument("session_id")
    import_claude.add_argument("--claude-home", type=Path)
    import_claude.add_argument("--claude-session-dir", type=Path)
    import_claude.add_argument("--target-id")
    import_claude.add_argument("--write", action="store_true", help="Apply the import. The default is a dry run.")
    import_claude.add_argument("--overwrite", action="store_true")
    import_claude.add_argument("--model")
    import_claude.add_argument("--reasoning-effort")
    import_claude.add_argument("--json", action="store_true")

    verify = subparsers.add_parser("verify", help="Verify an installed Codex session.")
    verify.add_argument("session_id")
    verify.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list-opencode":
            return _list_opencode(args)
        if args.command == "import-opencode":
            return _import_opencode(args)
        if args.command == "list-claude":
            return _list_claude(args)
        if args.command == "import-claude":
            return _import_claude(args)
        if args.command == "verify":
            return _verify(args)
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"Unhandled command: {args.command}")


def _list_opencode(args: argparse.Namespace) -> int:
    store = OpenCodeDatabaseStore.discover(args.opencode_db)
    sessions = store.list(limit=args.limit)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": session.session_id,
                        "cwd": session.cwd,
                        "timestamp": session.timestamp,
                        "message_count": session.message_count,
                    }
                    for session in sessions
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"OpenCode database: {store.database_path}")
    for session in sessions:
        print(f"{session.session_id}  {session.timestamp}  {session.message_count:>4} messages  {session.cwd}")
    return 0


def _import_opencode(args: argparse.Namespace) -> int:
    store = OpenCodeDatabaseStore.discover(args.opencode_db)
    conversation = store.load_conversation(args.session_id)
    return _import_conversation(args, conversation)


def _list_claude(args: argparse.Namespace) -> int:
    store = ClaudeCodeConversationStore.discover(args.claude_home, args.claude_session_dir)
    sessions = store.list(limit=args.limit)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": session.session_id,
                        "cwd": session.cwd,
                        "timestamp": session.timestamp,
                        "message_count": session.message_count,
                        "path": str(session.path),
                    }
                    for session in sessions
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"Claude Code home: {store.claude_home}")
    for session in sessions:
        print(f"{session.session_id}  {session.timestamp}  {session.message_count:>4} messages  {session.cwd}")
    return 0


def _import_claude(args: argparse.Namespace) -> int:
    store = ClaudeCodeConversationStore.discover(args.claude_home, args.claude_session_dir)
    conversation = store.load_conversation(args.session_id)
    return _import_conversation(args, conversation)


def _import_conversation(args: argparse.Namespace, conversation: Conversation) -> int:
    importer = CodexImporter(args.codex_home)
    target_id, destination, _records = importer.plan(conversation, session_id=args.target_id)
    assistant_turns = sum(bool(turn.assistant_text) for turn in conversation.turns)

    if not args.write:
        payload = {
            "dry_run": True,
            "source": conversation.source,
            "source_id": conversation.source_id,
            "target_id": target_id,
            "title": conversation.title,
            "cwd": conversation.cwd,
            "turns": len(conversation.turns),
            "assistant_turns": assistant_turns,
            "unpaired_assistants": len(conversation.unpaired_assistants),
            "destination": str(destination),
        }
        _print_payload(payload, args.json)
        if not args.json:
            print("Dry run only. Re-run with --write to install this session into Codex.")
        return 0

    result = importer.install(
        conversation,
        session_id=args.target_id,
        overwrite=args.overwrite,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    payload = {
        "dry_run": False,
        "source_id": conversation.source_id,
        "target_id": result.session_id,
        "title": result.title,
        "turns": result.turn_count,
        "assistant_turns": result.assistant_turn_count,
        "destination": str(result.rollout_path),
        "backup": str(result.backup_dir) if result.backup_dir else None,
        "valid": result.validation.valid,
        "warnings": [*result.warnings, *result.validation.warnings],
    }
    _print_payload(payload, args.json)
    return 0


def _verify(args: argparse.Namespace) -> int:
    result = CodexSessionVerifier(args.codex_home).verify(args.session_id)
    payload = {
        "session_id": args.session_id,
        "valid": result.valid,
        "turns": result.turn_count,
        "user_messages": result.user_message_count,
        "assistant_messages": result.assistant_message_count,
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }
    _print_payload(payload, args.json)
    return 0 if result.valid else 1


def _print_payload(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        if isinstance(value, list):
            if value:
                print(f"{key}:")
                for item in value:
                    print(f"  - {item}")
        else:
            print(f"{key}: {value}")
