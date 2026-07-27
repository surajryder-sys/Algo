"""Manual-intervention blocking: if the user manually cancels a pending
order or closes a position (as opposed to the bot doing it, or an SL/TP/
stop-out exit), the exact OB zone that setup came from gets blocked from
re-entry -- one blocked zone per source timeframe (M5/M15/M30).

Released three ways:
  - automatically, once a NEW OB forms in the same direction on that same
    timeframe (the blocked zone is no longer current, so the block is moot)
  - manually, via `python -m btc_smc.reset_block <M5|M15|M30|all>`
  - manually, via the RESET M5/M15/M30 buttons on the BTCUSD bridge
    indicator's chart (see check_reset_requests() below)

The RESET flag files and status file are namespaced by symbol
(RESET_BTCUSD_<tf>.flag, BLOCK_STATUS_BTCUSD.json) because the MT5 Common
Files bridge folder is shared across every terminal install for this
Windows user -- without the symbol in the filename, this bot's resets would
collide with the XAUUSD bot's (unscoped RESET_<tf>.flag) or the ETHUSD
bot's (RESET_ETHUSD_<tf>.flag).

Block state is Python-side only, so it's also published to a small JSON
status file for a future indicator-side display (see publish_status_file())
-- the reverse direction of the reset flags.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from btc_smc.bridge_reader import bridge_root

RESET_FLAG_TIMEFRAMES = ("M5", "M15", "M30")


class BlockedZoneStore:
    def __init__(self, path: str, symbol: str):
        self._path = Path(path)
        self.symbol = symbol
        self._blocked: dict = {}   # source_tf -> zone_key
        self._reasons: dict = {}  # source_tf -> reason string
        self._load()
        self.publish_status_file()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return

        if "blocked" in data:
            self._blocked = data.get("blocked", {})
            self._reasons = data.get("reasons", {})
        else:
            # Older format: a flat {source_tf: zone_key} dict, no reasons.
            self._blocked = data
            self._reasons = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({"blocked": self._blocked, "reasons": self._reasons}))
        self.publish_status_file()

    def blocked_zone_key(self, source_tf: str) -> Optional[str]:
        return self._blocked.get(source_tf)

    def is_blocked(self, source_tf: str, zone_key: str) -> bool:
        return self._blocked.get(source_tf) == zone_key

    def block(self, source_tf: str, zone_key: str, reason: str = "unknown") -> None:
        self._blocked[source_tf] = zone_key
        self._reasons[source_tf] = reason
        self._save()

    def release(self, source_tf: str) -> Optional[str]:
        removed = self._blocked.pop(source_tf, None)
        self._reasons.pop(source_tf, None)
        if removed is not None:
            self._save()
        return removed

    def release_if_stale(self, source_tf: str, direction: int,
                         current_latest_zone_key: Optional[str]) -> None:
        """Auto-release only if the block belongs to the same direction and
        a genuinely different zone is now the latest for that direction."""
        blocked = self._blocked.get(source_tf)
        if blocked is None:
            return

        blocked_direction = int(blocked.split("|")[1])
        if blocked_direction != direction:
            return

        if current_latest_zone_key is not None and current_latest_zone_key != blocked:
            print(f"[BLOCK] auto-released {source_tf} block ({blocked}): new same-direction zone superseded it")
            self.release(source_tf)

    def publish_status_file(self) -> None:
        """Writes a small status file for the indicator to eventually poll
        and show BLOCKED/CLEAR (and why) next to its RESET buttons."""
        status = {
            tf: {
                "blocked": tf in self._blocked,
                "reason": self._reasons.get(tf),
                "zone_key": self._blocked.get(tf),
            }
            for tf in RESET_FLAG_TIMEFRAMES
        }

        final_path = bridge_root() / f"BLOCK_STATUS_{self.symbol}.json"
        tmp_path = final_path.with_suffix(".json.tmp")
        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            # Compact (no spaces) -- matches how the OB bridge JSON is
            # authored on the MQL5 side.
            tmp_path.write_text(json.dumps(status, separators=(",", ":")))
            tmp_path.replace(final_path)
        except OSError:
            pass


def check_reset_requests(blocked: BlockedZoneStore) -> None:
    """Polls for RESET_<symbol>_<tf>.flag files the indicator's chart buttons
    write, releases the corresponding block, and clears the flag. Deliberately
    not gated behind enable_trading -- a manual reset should always take
    effect immediately, dry-run or not."""
    for tf in RESET_FLAG_TIMEFRAMES:
        flag_path = bridge_root() / f"RESET_{blocked.symbol}_{tf}.flag"
        if not flag_path.exists():
            continue

        released = blocked.release(tf)
        if released is not None:
            print(f"[BLOCK] chart reset button released {tf} block on {released}")
        else:
            print(f"[BLOCK] chart reset button pressed for {tf} (no active block)")

        try:
            flag_path.unlink()
        except OSError:
            pass
