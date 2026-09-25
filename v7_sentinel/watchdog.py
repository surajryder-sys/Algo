"""V7-Sentinel Watchdog -- lightweight, standalone process monitor.

Originally built 2026-09-24 for V6-Sentinel after discovering TM-STR,
Scalper, Exit Manager, and the NLB/NSB Watcher had all been silently
failing on the MT5 Python API's own IPC channel ("No tick for XAUUSD:
(-10001, 'IPC send failed')") for hours, completely undetected, while
RM-STR/RM-ICT kept trading normally the entire time -- see this
conversation's own live investigation (2026-09-24) for the full trail.
Rebuilt the same day for V7S's own 2-PROCESS shape (Data Manager, Trade
Manager -- see data_manager.py/trade_manager_main.py) once that
consolidation replaced V6S's 5 independent MT5-touching processes.
Nothing here opens an MT5 connection of its own -- deliberately: pure
log-tail + heartbeat-file checks over each process's already-redirected
v7s_*_run.log/heartbeat files, so the watchdog itself can never add to
the exact IPC contention it exists to catch.

DETECTION -- two independent signals per monitored process, either one
alone is enough to flag it:

  1. FAILURE SIGNATURE IN RECENT LOG OUTPUT. Tails each process's ONE
     combined run.log (byte offset persisted between cycles in this
     module's own state file, so it never re-reads a multi-hundred-
     thousand-line file from scratch) and checks whatever's new since
     the last poll against _FAILURE_SIGNATURES. A cycle counts as
     unhealthy only if there ARE new lines and EVERY one of them matches
     a failure signature -- mixed output (anything genuinely new
     alongside the noise) counts as healthy, same as no new output at
     all counts as neutral (carried forward, see signal 2).
     _CONSECUTIVE_TO_ALERT unhealthy cycles in a row trips the alert, so
     one transient blip never pages anyone. This check is per PROCESS
     (one shared run.log per process, since all of a process's own sub-
     components print into the same redirected output) -- it can say
     "Trade Manager is failing" but not which of its sub-components,
     that's what signal 2 is for.

  2. DEAD SUB-COMPONENT. Each process now monitors SEVERAL heartbeat
     files, one per internal sub-component (V7S's own choice, finer-
     grained than V6S's 5-file scheme -- Data Manager: tv_scraper +
     NLB/NSB Watcher + ICT Block Watcher; Trade Manager: TM-STR, RM-ICT
     (RM-STR removed 2026-09-25, see reversal_main.py's own docstring),
     and EM's own ICT Exit component (its only one left -- Scalper's two
     dedicated EM components, LTF Exit, and Bias Exit were all removed
     2026-09-25, see exit_manager.py's own docstring)). ANY one
     of a process's own heartbeats going stale (no update in
     _HEARTBEAT_STALE_SECONDS) marks that WHOLE process
     unhealthy, naming exactly which sub-component(s) went quiet.
     Confirmed live in the V6S incident this was built for: heartbeats
     update every loop regardless of whether that loop errored, so a
     truly DEAD process (not just one raising exceptions) is what this
     signal catches that signal 1 alone can't tell apart from "just
     quiet."

ALERT-ONLY (2026-09-24, user's own scope for v1): flags problems on
Telegram, restarts nothing. This project's own standing rule (CLAUDE.md)
is that a live-trading process is never (re)started without the user's
explicit go-ahead each time -- auto-restart is a deliberate separate
decision, not bundled in here.

Only STATE TRANSITIONS (healthy->unhealthy, unhealthy->healthy) send a
message, not every poll cycle -- one alert when something breaks, one
when it recovers, silence in between. Uses V7S's OWN separate Telegram
bot (V7S_ALERTS_TELEGRAM_BOT_TOKEN/CHAT_ID) -- deliberately NOT the same
bot V6S uses, per the user's own choice for the V7S consolidation, so
alerts from the two systems never interleave in one chat during the
transition.

Run with: python -m v7_sentinel.watchdog
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv

from v7_sentinel import telegram_alerts

load_dotenv()

_STATE_FILE = "v7s_watchdog_state.json"

_POLL_SECONDS = float(os.getenv("V7S_WATCHDOG_POLL_SECONDS", "30"))
_CONSECUTIVE_TO_ALERT = int(os.getenv("V7S_WATCHDOG_CONSECUTIVE_TO_ALERT", "3"))
_HEARTBEAT_STALE_SECONDS = float(os.getenv("V7S_WATCHDOG_HEARTBEAT_STALE_SECONDS", "120"))

_ALERTS_BOT_TOKEN = os.getenv("V7S_ALERTS_TELEGRAM_BOT_TOKEN") or None
_ALERTS_CHAT_ID = os.getenv("V7S_ALERTS_TELEGRAM_CHAT_ID") or None

# Every string seen live in an actual failure episode today. A new-lines
# batch only counts as an unhealthy cycle if EVERY new line matches one
# of these -- add more here if a future failure mode turns out to print
# something else, rather than trying to guess every possible message.
_FAILURE_SIGNATURES = (
    "IPC send failed",
    "No tick for",
    "no live tick available",
)


@dataclass
class _Monitored:
    name: str
    run_log: str
    heartbeats: tuple[tuple[str, str], ...]   # (sub_component_label, heartbeat_file) pairs


_PROCESSES: list[_Monitored] = [
    _Monitored("Data Manager", "v7s_data_manager_run.log", (
        ("tv_scraper", "v7s_tv_scraper_heartbeat.json"),
        ("NLB/NSB Watcher", "v7s_nlb_nsb_watcher_heartbeat_XAUUSD.json"),
        # ICT Block Watcher (2026-09-24) -- see ict_ob_watcher.py's own docstring.
        ("ICT Block Watcher", "v7s_ict_ob_watcher_heartbeat_XAUUSD.json"),
    )),
    _Monitored("Trade Manager", "v7s_trade_manager_run.log", (
        ("TM-STR", "v7s_trend_manager_heartbeat_XAUUSD.json"),
        ("RM-ICT", "v7s_reversal_manager_heartbeat_XAUUSD.json"),
        # ICT Exit (2026-09-24) -- see exit_manager_ict.py's own docstring. Exit Manager's
        # only remaining component (Bias removed 2026-09-25 on request, "cisd is only for
        # entry, we are taking out of exit"; LTF removed 2026-09-25 too, see exit_manager.py's
        # own docstring; Scalper, EM-Candle, EM-Scalper-M3M5 removed 2026-09-25 along with
        # Scalper itself).
        ("EM-ICT", "v7s_exit_manager_heartbeat_ict_XAUUSD.json"),
    )),
]


@dataclass
class _ProcState:
    offset: int = 0
    unhealthy_streak: int = 0
    alerted: bool = False
    last_reason: str = ""


def _load_state() -> dict[str, _ProcState]:
    try:
        raw = json.loads(open(_STATE_FILE, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return {}
    return {name: _ProcState(**fields) for name, fields in raw.items()}


def _save_state(state: dict[str, _ProcState]) -> None:
    raw = {name: vars(s) for name, s in state.items()}
    tmp = _STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    os.replace(tmp, _STATE_FILE)


def _read_new_lines(path: str, offset: int) -> tuple[list[str], int]:
    """Everything appended to the file since `offset`. A shrunk file
    (log rotated/truncated under us) resets to 0 rather than raising, so
    a rotation just looks like "no new lines this cycle" instead of
    crashing the watchdog."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if size < offset:
        offset = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        text = f.read()
        new_offset = f.tell()
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return lines, new_offset


def _is_failure_line(line: str) -> bool:
    return any(sig in line for sig in _FAILURE_SIGNATURES)


def _heartbeat_age(path: str | None) -> float | None:
    if path is None:
        return None
    try:
        raw = json.loads(open(path, encoding="utf-8").read())
        return time.time() - float(raw["updated"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def _check_one(proc: _Monitored, st: _ProcState) -> None:
    lines, new_offset = _read_new_lines(proc.run_log, st.offset)
    st.offset = new_offset

    # ANY one of this process's own sub-component heartbeats going stale marks the whole
    # process unhealthy -- name exactly which one(s), since a process can have several.
    stale = []
    for label, path in proc.heartbeats:
        age = _heartbeat_age(path)
        if age is not None and age > _HEARTBEAT_STALE_SECONDS:
            stale.append((label, age))
    dead = bool(stale)

    if dead:
        unhealthy_this_cycle = True
        stale_desc = ", ".join(f"{label} ({age:.0f}s)" for label, age in stale)
        reason = (f"heartbeat stale, no update in over {_HEARTBEAT_STALE_SECONDS:.0f}s -- "
                 f"likely dead: {stale_desc}")
    elif lines and all(_is_failure_line(ln) for ln in lines):
        unhealthy_this_cycle = True
        reason = f"last {len(lines)} new log line(s) all failure signatures, e.g. {lines[-1][:200]!r}"
    elif lines:
        # Genuine new output that isn't just failure spam -- healthy,
        # resets the streak immediately rather than waiting it out.
        unhealthy_this_cycle = False
        reason = ""
    elif st.alerted:
        # No new log output this cycle, but every heartbeat is confirmed fresh (dead is
        # False here) AND we were previously alerted -- that's a definite recovery signal
        # for the heartbeat check specifically, not ambiguous silence. Found via testing
        # 2026-09-24: without this branch, a process that recovers from heartbeat-staleness
        # but happens to have a quiet log cycle right after could never clear its alert,
        # since "no new lines" alone always fell through to the ambiguous branch below.
        unhealthy_this_cycle = False
        reason = ""
    else:
        # Never alerted, no new output this cycle, heartbeat fine -- genuinely ambiguous
        # (could be legitimately quiet before any real problem was ever confirmed), carry
        # the streak forward unchanged rather than guessing either way.
        unhealthy_this_cycle = None
        reason = st.last_reason

    if unhealthy_this_cycle is True:
        st.unhealthy_streak += 1
        st.last_reason = reason
    elif unhealthy_this_cycle is False:
        if st.alerted:
            telegram_alerts.send_if_configured(
                _ALERTS_BOT_TOKEN, _ALERTS_CHAT_ID,
                f"[V7S-WATCHDOG] RECOVERED: {proc.name} is producing normal output again.",
            )
            print(f"[V7S-WATCHDOG] {proc.name}: recovered")
        st.unhealthy_streak = 0
        st.alerted = False
        st.last_reason = ""
    # unhealthy_this_cycle is None (ambiguous/quiet) -- streak untouched.

    if st.unhealthy_streak >= _CONSECUTIVE_TO_ALERT and not st.alerted:
        telegram_alerts.send_if_configured(
            _ALERTS_BOT_TOKEN, _ALERTS_CHAT_ID,
            f"[V7S-WATCHDOG] BROKEN: {proc.name} -- {st.last_reason}",
        )
        print(f"[V7S-WATCHDOG] {proc.name}: ALERTED -- {st.last_reason}")
        st.alerted = True


def run_once(state: dict[str, _ProcState]) -> None:
    for proc in _PROCESSES:
        st = state.setdefault(proc.name, _ProcState())
        _check_one(proc, st)
    _save_state(state)


def main() -> None:
    if not _ALERTS_BOT_TOKEN or not _ALERTS_CHAT_ID:
        print("[V7S-WATCHDOG] WARNING: V7S_ALERTS_TELEGRAM_BOT_TOKEN/CHAT_ID not set -- "
              "problems will be printed here but no Telegram alert will be sent.")
    print(f"[V7S-WATCHDOG] watching {len(_PROCESSES)} process(es), "
          f"poll={_POLL_SECONDS}s consecutive_to_alert={_CONSECUTIVE_TO_ALERT} "
          f"heartbeat_stale={_HEARTBEAT_STALE_SECONDS}s")
    state = _load_state()
    # Fresh state starts every offset at the CURRENT end of each log file
    # rather than 0 -- otherwise a first run against an already-enormous
    # run.log would read the whole thing as "new" in one shot and could
    # misjudge health off ancient lines, or just take a long time for no
    # benefit (nothing before "now" is actionable).
    if not state:
        for proc in _PROCESSES:
            try:
                size = os.path.getsize(proc.run_log)
            except OSError:
                size = 0
            state[proc.name] = _ProcState(offset=size)
        _save_state(state)

    while True:
        run_once(state)
        time.sleep(_POLL_SECONDS)


if __name__ == "__main__":
    main()
