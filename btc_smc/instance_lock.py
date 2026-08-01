"""Single-instance lock: refuses to start a second copy of this bot's
main.py while one is already running.

This is the real fix for the duplicate-order incidents on 2026-08-01 (both
eth_smc and btc_smc): the broker-side duplicate guard in main.py only
narrows the race between two concurrent processes down to however long an
order_send() round-trip takes -- it can't eliminate it, because both
processes can pass "no live order exists for this zone" before either
one's order has actually registered. Two fills landed 560ms apart on
BTCUSD, 1.5s apart on ETHUSD, both same zone, both from a second process
that started and died without ever showing up in a routine check.

Uses an OS-level byte-range lock (msvcrt.locking) on a dedicated lock file,
held open for the entire process lifetime. Windows releases all locks a
process holds the instant it exits for ANY reason -- normal exit, crash,
or being killed -- so there's no stale-lock cleanup to get wrong; the next
launch just acquires it fresh.
"""
from __future__ import annotations

import msvcrt
import os
from pathlib import Path

# Anchored to this module's own directory, NOT the process's working
# directory. A relative path here would resolve differently depending on
# where "python -m btc_smc.main" happens to be launched from -- two
# processes started from different working directories would each create
# their own lock file in a different location and never conflict, which is
# exactly how a duplicate slipped through despite this lock being in place
# (confirmed live on BTCUSD, 2026-08-01 23:35 -- two fills 522ms apart, six
# minutes after this lock was deployed and the bot restarted).
_MODULE_DIR = Path(__file__).resolve().parent


class SingleInstanceLock:
    def __init__(self, lock_file: str):
        self._path = _MODULE_DIR / lock_file
        self._handle = None

    def acquire(self) -> None:
        """Raises RuntimeError immediately if another instance already
        holds the lock -- fails loud and fast instead of silently racing."""
        handle = open(self._path, "a+")
        # Always lock byte 0 explicitly -- in append mode the file position
        # starts at EOF once the file has content from a previous acquire,
        # and msvcrt.locking() locks the range starting at the CURRENT
        # position. Without this seek, two processes on a non-empty lock
        # file would lock different byte offsets and never conflict.
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            raise RuntimeError(
                f"another instance is already running (lock held on {self._path}) "
                f"-- refusing to start a second copy"
            )
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._handle.close()
        self._handle = None
