"""Persisted subscriber list for the profit-alerts bot
(SecretTrader_Critical_Bot) -- approval-gated per the user's explicit
requirement, 2026-08-28: "i want to choose and approve each person."

Three states a chat_id can be in:
- Owner (PROFIT_ALERTS_TELEGRAM_CHAT_ID) -- seeded as approved on first
  load, always. This is you; never needs manual approval.
- Pending -- messaged the bot for the first time, not yet approved.
  Recorded automatically by profit_alerts_listener.py, invisible to
  profit_alerts_watcher.py (never receives alerts).
- Approved -- explicitly approved by the owner (via /approve in
  Telegram). profit_alerts_watcher.py sends to every approved chat_id.

Telegram itself only allows a bot to message someone who has messaged
it first -- this store's own "pending" state can't exist for someone
who has never done that, so there's nothing to pre-seed here beyond the
owner.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class SubscriberStore:
    def __init__(self, path: str, owner_chat_id: str):
        self._path = Path(path)
        self._owner_chat_id = str(owner_chat_id)
        self._approved: Dict[str, dict] = {}   # chat_id -> {name, approved_at}
        self._pending: Dict[str, dict] = {}    # chat_id -> {name, requested_at}
        self._load()
        if self._owner_chat_id and self._owner_chat_id not in self._approved:
            self._approved[self._owner_chat_id] = {"name": "owner", "approved_at": time.time()}
            self._save()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._approved = raw.get("approved", {})
        self._pending = raw.get("pending", {})

    def _save(self) -> None:
        self._path.write_text(json.dumps({"approved": self._approved, "pending": self._pending}, indent=2))

    def is_approved(self, chat_id) -> bool:
        return str(chat_id) in self._approved

    def is_pending(self, chat_id) -> bool:
        return str(chat_id) in self._pending

    def is_owner(self, chat_id) -> bool:
        return str(chat_id) == self._owner_chat_id

    @property
    def owner_chat_id(self) -> str:
        return self._owner_chat_id

    def record_pending(self, chat_id, name: str) -> bool:
        """No-op (returns False) if already approved or already pending
        -- only a genuinely NEW sender gets recorded (and only that
        first message should trigger the "pending approval" reply)."""
        chat_id = str(chat_id)
        if chat_id in self._approved or chat_id in self._pending:
            return False
        self._pending[chat_id] = {"name": name, "requested_at": time.time()}
        self._save()
        return True

    def list_pending(self) -> List[Tuple[str, str]]:
        return [(chat_id, info["name"]) for chat_id, info in self._pending.items()]

    def list_approved(self) -> List[Tuple[str, str]]:
        return [(chat_id, info["name"]) for chat_id, info in self._approved.items()]

    def approved_chat_ids(self) -> List[str]:
        return list(self._approved.keys())

    def approve_by_identifier(self, identifier: str) -> Optional[Tuple[str, str]]:
        """identifier can be an exact chat_id or a case-insensitive
        substring of a pending requester's name. Returns (chat_id, name)
        of whoever got approved, or None if nothing matched. Exact
        chat_id match takes priority over a name substring match."""
        identifier = identifier.strip()
        if identifier in self._pending:
            return self._move_to_approved(identifier)

        lowered = identifier.lower()
        matches = [chat_id for chat_id, info in self._pending.items() if lowered in info["name"].lower()]
        if len(matches) == 1:
            return self._move_to_approved(matches[0])
        return None  # no match, or ambiguous (more than one name matched) -- caller should ask for the exact chat_id

    def _move_to_approved(self, chat_id: str) -> Tuple[str, str]:
        info = self._pending.pop(chat_id)
        self._approved[chat_id] = {"name": info["name"], "approved_at": time.time()}
        self._save()
        return chat_id, info["name"]
