"""Terminlogik für den Telefonassistenten.

Speichert Termine in einer JSON-Datei (Demo-Modus). Für echte Kunden wird
`JsonStore` später gegen Google Calendar / Cal.com ausgetauscht; die
Funktionen hier bleiben gleich.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


class BookingError(Exception):
    """Fehler, der dem Anrufer in verständlicher Form erklärt werden kann."""


@dataclass
class Service:
    id: str
    name: str
    duration: timedelta
    price: str
    aliases: list[str]


class JsonStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[]", encoding="utf-8")

    def load(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, bookings: list[dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(bookings, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


class Calendar:
    def __init__(self, config: dict, store: JsonStore, now=None):
        self.cfg = config
        self.store = store
        self.tz = ZoneInfo(config["timezone"])
        self.step = timedelta(minutes=config.get("slot_step_minutes", 15))
        self.lead = timedelta(minutes=config.get("min_lead_minutes", 60))
        self.horizon = config.get("booking_horizon_days", 30)
        self.staff = config["staff"]
        self.services = [
            Service(s["id"], s["name"], timedelta(minutes=s["duration_min"]), s["price_eur"], s.get("aliases", []))
            for s in config["services"]
        ]
        self._now = now  # für Tests überschreibbar
        self._lock = threading.Lock()

    # ---------- Hilfsfunktionen ----------

    def now(self) -> datetime:
        return self._now() if self._now else datetime.now(self.tz)

    def today_info(self) -> str:
        n = self.now()
        return f"{WEEKDAYS_DE[n.weekday()]}, {n.strftime('%d.%m.%Y')}, {n.strftime('%H:%M')} Uhr"

    def find_service(self, text: str) -> Service:
        t = (text or "").strip().lower()
        for s in self.services:
            if t in (s.id, s.name.lower()):
                return s
        for s in self.services:
            if any(a in t or t in a for a in s.aliases) or t in s.name.lower():
                return s
        names = ", ".join(s.name for s in self.services)
        raise BookingError(f"Leistung '{text}' gibt es nicht. Angeboten werden: {names}.")

    def _parse_date(self, value: str) -> date:
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            raise BookingError(f"Datum '{value}' nicht verstanden, bitte im Format JJJJ-MM-TT übergeben.")

    def _parse_time(self, value: str) -> time:
        try:
            return time.fromisoformat(value)
        except (TypeError, ValueError):
            raise BookingError(f"Uhrzeit '{value}' nicht verstanden, bitte im Format HH:MM übergeben.")

    def _opening_windows(self, d: date) -> list[tuple[datetime, datetime]]:
        if d.isoformat() in self.cfg.get("closed_dates", []):
            return []
        windows = []
        for start, end in self.cfg["opening_hours"][WEEKDAYS[d.weekday()]]:
            windows.append((
                datetime.combine(d, time.fromisoformat(start), self.tz),
                datetime.combine(d, time.fromisoformat(end), self.tz),
            ))
        return windows

    def _busy(self, bookings: list[dict], staff: str, start: datetime, end: datetime) -> bool:
        for b in bookings:
            if b["status"] != "confirmed" or b["staff"] != staff:
                continue
            b_start = datetime.fromisoformat(b["start"])
            b_end = datetime.fromisoformat(b["end"])
            if start < b_end and b_start < end:
                return True
        return False

    def _free_staff(self, bookings, start, end, wanted: str | None) -> list[str]:
        candidates = [wanted] if wanted else self.staff
        return [s for s in candidates if not self._busy(bookings, s, start, end)]

    def _check_staff(self, staff: str | None) -> str | None:
        if not staff:
            return None
        for s in self.staff:
            if s.lower() == staff.strip().lower():
                return s
        raise BookingError(f"'{staff}' arbeitet hier nicht. Mitarbeiter: {', '.join(self.staff)}.")

    def _slots_on(self, bookings, d: date, service: Service, staff: str | None,
                  after: time | None, before: time | None) -> list[datetime]:
        earliest = self.now() + self.lead
        slots = []
        for w_start, w_end in self._opening_windows(d):
            t = w_start
            while t + service.duration <= w_end:
                ok = t >= earliest
                ok = ok and (after is None or t.time() >= after)
                ok = ok and (before is None or t.time() <= before)
                if ok and self._free_staff(bookings, t, t + service.duration, staff):
                    slots.append(t)
                t += self.step
        return slots

    @staticmethod
    def _spread(slots: list[datetime], n: int) -> list[datetime]:
        """Wählt n über den Tag verteilte Zeiten statt nur der ersten n."""
        if len(slots) <= n:
            return slots
        idx = sorted({round(i * (len(slots) - 1) / (n - 1)) for i in range(n)})
        return [slots[i] for i in idx]

    def _fmt(self, dt: datetime) -> str:
        return f"{WEEKDAYS_DE[dt.weekday()]}, {dt.strftime('%d.%m.')} um {dt.strftime('%H:%M')} Uhr"

    # ---------- Werkzeuge für den Agenten ----------

    def check_availability(self, service: str, date_str: str | None = None, time_from: str | None = None,
                           time_to: str | None = None, staff: str | None = None) -> dict:
        svc = self.find_service(service)
        staff = self._check_staff(staff)
        after = self._parse_time(time_from) if time_from else None
        before = self._parse_time(time_to) if time_to else None
        bookings = self.store.load()
        today = self.now().date()

        if date_str:
            d = self._parse_date(date_str)
            if d < today:
                raise BookingError("Dieses Datum liegt in der Vergangenheit.")
            if d > today + timedelta(days=self.horizon):
                raise BookingError(f"Termine können nur bis zu {self.horizon} Tage im Voraus gebucht werden.")
            slots = self._slots_on(bookings, d, svc, staff, after, before)
            if slots:
                picked = self._spread(slots, 5)
                return {
                    "heute": self.today_info(),
                    "leistung": svc.name, "dauer_min": int(svc.duration.total_seconds() // 60),
                    "datum": d.isoformat(),
                    "freie_zeiten": [s.strftime("%H:%M") for s in picked],
                    "hinweis": f"Am {WEEKDAYS_DE[d.weekday()]} sind {len(slots)} Startzeiten frei; nenne höchstens 2-3 davon.",
                }
            # Nichts frei -> nächste Alternativen suchen
            start_search = d + timedelta(days=1)
        else:
            start_search = today

        for i in range(self.horizon + 1):
            d2 = start_search + timedelta(days=i)
            if d2 > today + timedelta(days=self.horizon):
                break
            slots = self._slots_on(bookings, d2, svc, staff, after, before)
            if slots:
                return {
                    "heute": self.today_info(),
                    "leistung": svc.name, "dauer_min": int(svc.duration.total_seconds() // 60),
                    "gewuenschter_tag_frei": False if date_str else None,
                    "naechster_freier_tag": d2.isoformat(),
                    "wochentag": WEEKDAYS_DE[d2.weekday()],
                    "freie_zeiten": [s.strftime("%H:%M") for s in self._spread(slots, 5)],
                }
        return {"heute": self.today_info(), "leistung": svc.name, "freie_zeiten": [],
                "hinweis": "In den nächsten Wochen ist nichts frei. Biete einen Rückruf an."}

    def book_appointment(self, service: str, date_str: str, time_str: str, customer_name: str,
                         customer_phone: str, staff: str | None = None, notes: str | None = None) -> dict:
        svc = self.find_service(service)
        staff = self._check_staff(staff)
        if not customer_name or not customer_name.strip():
            raise BookingError("Für die Buchung fehlt der Name des Kunden.")
        d = self._parse_date(date_str)
        t = self._parse_time(time_str)
        start = datetime.combine(d, t, self.tz)
        end = start + svc.duration

        with self._lock:
            bookings = self.store.load()
            # Idempotent: gleicher Kunde, gleicher Termin -> bestehende Buchung zurückgeben
            for b in bookings:
                if (b["status"] == "confirmed" and b["customer_phone"] == customer_phone
                        and b["start"] == start.isoformat() and b["service_id"] == svc.id):
                    return {"erfolg": True, "buchungsnummer": b["id"], "termin": self._fmt(start),
                            "mitarbeiter": b["staff"], "hinweis": "Dieser Termin war bereits gebucht."}

            if start not in self._slots_on(bookings, d, svc, staff, None, None):
                alternatives = self.check_availability(svc.id, d.isoformat(), staff=staff)
                return {"erfolg": False, "grund": "Diese Zeit ist nicht (mehr) frei.", "alternativen": alternatives}

            assigned = self._free_staff(bookings, start, end, staff)[0]
            booking = {
                "id": uuid.uuid4().hex[:8].upper(),
                "status": "confirmed",
                "service_id": svc.id, "service": svc.name,
                "start": start.isoformat(), "end": end.isoformat(),
                "staff": assigned,
                "customer_name": customer_name.strip(),
                "customer_phone": customer_phone,
                "notes": notes or "",
                "created_at": self.now().isoformat(),
            }
            bookings.append(booking)
            self.store.save(bookings)

        return {"erfolg": True, "buchungsnummer": booking["id"], "termin": self._fmt(start),
                "leistung": svc.name, "mitarbeiter": assigned, "preis_eur": svc.price}

    def find_appointments(self, customer_phone: str) -> dict:
        now = self.now()
        upcoming = [
            {"buchungsnummer": b["id"], "termin": self._fmt(datetime.fromisoformat(b["start"])),
             "datum": b["start"][:10], "uhrzeit": b["start"][11:16], "leistung": b["service"],
             "mitarbeiter": b["staff"]}
            for b in self.store.load()
            if b["status"] == "confirmed" and b["customer_phone"] == customer_phone
            and datetime.fromisoformat(b["start"]) > now
        ]
        return {"termine": upcoming, "anzahl": len(upcoming)}

    def cancel_appointment(self, customer_phone: str, booking_id: str | None = None,
                           date_str: str | None = None) -> dict:
        with self._lock:
            bookings = self.store.load()
            now = self.now()
            matches = [
                b for b in bookings
                if b["status"] == "confirmed" and b["customer_phone"] == customer_phone
                and datetime.fromisoformat(b["start"]) > now
                and (not booking_id or b["id"] == booking_id.strip().upper())
                and (not date_str or b["start"][:10] == date_str)
            ]
            if not matches:
                return {"erfolg": False, "grund": "Kein passender zukünftiger Termin unter dieser Nummer gefunden."}
            if len(matches) > 1:
                return {"erfolg": False, "grund": "Mehrere Termine gefunden, bitte nachfragen welcher.",
                        "termine": [{"buchungsnummer": b["id"],
                                     "termin": self._fmt(datetime.fromisoformat(b["start"]))} for b in matches]}
            matches[0]["status"] = "cancelled"
            self.store.save(bookings)
        return {"erfolg": True, "storniert": self._fmt(datetime.fromisoformat(matches[0]["start"]))}

    def business_info(self) -> dict:
        days = []
        for i, wd in enumerate(WEEKDAYS):
            hours = self.cfg["opening_hours"][wd]
            days.append(f"{WEEKDAYS_DE[i]}: " + (", ".join(f"{a}-{b}" for a, b in hours) if hours else "geschlossen"))
        return {
            "heute": self.today_info(),
            "name": self.cfg["business_name"],
            "adresse": self.cfg["address"],
            "oeffnungszeiten": days,
            "leistungen": [{"name": s.name, "dauer_min": int(s.duration.total_seconds() // 60), "preis_eur": s.price}
                           for s in self.services],
            "parken": self.cfg.get("parking"),
            "zahlung": self.cfg.get("payment"),
            "faq": self.cfg.get("faq", []),
        }
