"""HTTP server that receives TradingView webhook alerts and appends each one
as a line to a JSON-lines log file. Meant to run continuously
(python -m tv_bridge.receiver) on this machine; reachable from the internet
only through a tunnel (e.g. Cloudflare Tunnel) pointed at
TV_WEBHOOK_HOST:TV_WEBHOOK_PORT -- this process itself only binds locally.

TradingView alerts can't send custom headers, so the shared secret travels
inside the JSON body instead; requests with a missing/wrong secret are
rejected before anything is written.

The Pine scripts build their own JSON payload inline and fire it via
alert(), rather than relying on TradingView's {{placeholder}} substitution
in the alert dialog's Message box -- that only supports one scalar value per
alert, not the full zone/trail state these scripts track. See
ob_detector.pine / atr_trail.pine (or the versions of your own scripts with
the alert() calls added) for the exact shape. Every payload carries "secret",
"type", and "symbol"; required fields beyond that depend on "type":
  atr_trail          -- timeframe, trail_stop, trend, event_time, bar_time
  ob_zone_formed     -- timeframe, direction, start_time, top, btm,
                         detected_time, detected_price
  ob_zone_mitigated  -- timeframe, direction, start_time, mitigated_time,
                         mitigated_price
"""
from __future__ import annotations

import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tv_bridge.config import BridgeConfig, load_bridge_config

_EVENT_SCHEMAS = {
    "atr_trail": ("timeframe", "trail_stop", "trend", "event_time", "bar_time"),
    "ob_zone_formed": ("timeframe", "direction", "start_time", "top", "btm",
                        "detected_time", "detected_price"),
    "ob_zone_mitigated": ("timeframe", "direction", "start_time",
                           "mitigated_time", "mitigated_price"),
}
_write_lock = threading.Lock()


def _append_signal(log_path: Path, record: dict) -> None:
    with _write_lock:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def _validate(payload: dict, secret: str) -> tuple[bool, str]:
    if not hmac.compare_digest(str(payload.get("secret", "")), secret):
        return False, "bad secret"
    if not payload.get("symbol"):
        return False, "missing symbol"
    required = _EVENT_SCHEMAS.get(payload.get("type"))
    if required is None:
        return False, f"type must be one of {sorted(_EVENT_SCHEMAS)}"
    missing = [f for f in required if f not in payload]
    if missing:
        return False, f"missing fields: {missing}"
    return True, ""


def make_handler(cfg: BridgeConfig):
    log_path = Path(cfg.signal_log_file)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"[TV_BRIDGE] {self.address_string()} {fmt % args}")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._respond(400, {"error": "invalid json"})
                return

            ok, reason = _validate(payload, cfg.secret)
            if not ok:
                print(f"[TV_BRIDGE] rejected: {reason}")
                self._respond(403, {"error": reason})
                return

            record = {k: v for k, v in payload.items() if k != "secret"}
            record["received_at"] = time.time()
            _append_signal(log_path, record)
            print(f"[TV_BRIDGE] saved: {record}")
            self._respond(200, {"status": "ok"})

        def _respond(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> None:
    cfg = load_bridge_config()
    server = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(cfg))
    print(f"TV bridge listening on {cfg.host}:{cfg.port} -> {cfg.signal_log_file}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
