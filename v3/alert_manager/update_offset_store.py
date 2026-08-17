"""Persists the last processed Telegram update_id for the /bias command
listener, so a restart doesn't re-process (and re-reply to) old
messages that arrived while the process was down."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class UpdateOffsetStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._offset: Optional[int] = None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._offset = json.loads(self._path.read_text()).get("offset")
        except (json.JSONDecodeError, OSError):
            self._offset = None

    def get(self) -> int:
        """0 means "no offset yet" -- Telegram's own convention, fetches
        from the oldest still-pending update."""
        return self._offset if self._offset is not None else 0

    def set(self, offset: int) -> None:
        self._offset = offset
        self._path.write_text(json.dumps({"offset": offset}))
