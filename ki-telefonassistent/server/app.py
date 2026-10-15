"""HTTP-Server, den der Voice-Agent während des Anrufs aufruft.

Start:  python3 server/app.py            (Port 8080, oder PORT setzen)
Endpunkte:
  GET  /health
  POST /tools/<name>   name = check_availability | book_appointment |
                              find_appointments | cancel_appointment | business_info

Akzeptiert sowohl das Retell-Format {"name", "call", "args"} als auch
direkt die Argumente als JSON. Die Nummer des Anrufers wird aus
call.from_number übernommen, wenn der Agent keine customer_phone mitschickt.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from booking import BookingError, Calendar, JsonStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("BUSINESS_CONFIG", ROOT / "config" / "salon_lena.json"))
DATA_PATH = Path(os.environ.get("BOOKINGS_FILE", ROOT / "data" / "bookings.json"))
TOOL_TOKEN = os.environ.get("TOOL_TOKEN", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tools")

calendar = Calendar(json.loads(CONFIG_PATH.read_text(encoding="utf-8")), JsonStore(DATA_PATH))


def run_tool(name: str, args: dict, caller: str | None) -> dict:
    phone = args.get("customer_phone") or caller or ""
    if name == "check_availability":
        return calendar.check_availability(args.get("service", ""), args.get("date"), args.get("time_from"),
                                           args.get("time_to"), args.get("staff"))
    if name == "book_appointment":
        if not phone:
            raise BookingError("Telefonnummer des Kunden fehlt, bitte erfragen.")
        return calendar.book_appointment(args.get("service", ""), args.get("date", ""), args.get("time", ""),
                                         args.get("customer_name", ""), phone, args.get("staff"), args.get("notes"))
    if name == "find_appointments":
        return calendar.find_appointments(phone)
    if name == "cancel_appointment":
        return calendar.cancel_appointment(phone, args.get("booking_id"), args.get("date"))
    if name == "business_info":
        return calendar.business_info()
    raise KeyError(name)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not TOOL_TOKEN:
            return True
        query = parse_qs(urlparse(self.path).query)
        given = self.headers.get("X-Tool-Token") or (query.get("token") or [""])[0]
        return hmac.compare_digest(given, TOOL_TOKEN)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            return self._send(200, {"ok": True, "business": calendar.cfg["business_name"]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/tools/"):
            return self._send(404, {"error": "not found"})
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "invalid json"})

        name = path.removeprefix("/tools/")
        args = body.get("args", body) if isinstance(body, dict) else {}
        caller = (body.get("call") or {}).get("from_number") if isinstance(body, dict) else None
        try:
            result = run_tool(name, args, caller)
        except KeyError:
            return self._send(404, {"error": f"unknown tool {name}"})
        except BookingError as e:
            result = {"erfolg": False, "fehler": str(e)}
        except Exception:  # Agent soll nie hängen bleiben
            log.exception("tool %s failed", name)
            result = {"erfolg": False, "fehler": "Technischer Fehler. Biete einen Rückruf an und buche nichts."}
        log.info("tool=%s args=%s -> %s", name, json.dumps(args, ensure_ascii=False), json.dumps(result, ensure_ascii=False)[:300])
        self._send(200, result)

    def log_message(self, *_):  # eigenes Logging oben
        pass


def main():
    port = int(os.environ.get("PORT", "8080"))
    if not TOOL_TOKEN:
        log.warning("TOOL_TOKEN ist nicht gesetzt - Server ist ungeschützt (nur für lokale Tests).")
    log.info("Server läuft auf Port %s für %s", port, calendar.cfg["business_name"])
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
