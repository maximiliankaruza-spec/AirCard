import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
from booking import BookingError, Calendar, JsonStore  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")
CONFIG = json.loads((ROOT / "config" / "salon_lena.json").read_text(encoding="utf-8"))
# Dienstag, 20.10.2026, 08:00
NOW = datetime(2026, 10, 20, 8, 0, tzinfo=TZ)


class CalendarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cal = Calendar(CONFIG, JsonStore(Path(self.tmp.name) / "b.json"), now=lambda: NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def book(self, time_str, phone="+491701111111", service="herren", day="2026-10-20", staff=None):
        return self.cal.book_appointment(service, day, time_str, "Max Muster", phone, staff)

    def test_service_aliases(self):
        self.assertEqual(self.cal.find_service("Herrenschnitt").id, "herren_schnitt")
        self.assertEqual(self.cal.find_service("Balayage").id, "straehnen")
        with self.assertRaises(BookingError):
            self.cal.find_service("Massage")

    def test_closed_monday_jumps_to_next_open_day(self):
        r = self.cal.check_availability("herren", "2026-10-26")  # Montag
        self.assertEqual(r["naechster_freier_tag"], "2026-10-27")
        self.assertTrue(r["freie_zeiten"])

    def test_no_slots_in_lead_time_or_past(self):
        r = self.cal.check_availability("herren", "2026-10-20")
        self.assertTrue(all(t >= "09:00" for t in r["freie_zeiten"]))
        with self.assertRaises(BookingError):
            self.cal.check_availability("herren", "2026-10-19")

    def test_long_service_must_fit_before_closing(self):
        # Samstag 09-14, Strähnen dauern 150 min -> spätester Start 11:30
        r = self.cal.check_availability("straehnen", "2026-10-24")
        self.assertLessEqual(max(r["freie_zeiten"]), "11:30")

    def test_booking_and_double_booking(self):
        self.assertTrue(self.book("10:00", staff="Lena")["erfolg"])
        # Lena ist belegt, Tom noch frei
        r = self.book("10:00", phone="+491702222222")
        self.assertTrue(r["erfolg"])
        self.assertEqual(r["mitarbeiter"], "Tom")
        # beide belegt
        r = self.book("10:15", phone="+491703333333")
        self.assertFalse(r["erfolg"])
        self.assertIn("alternativen", r)

    def test_booking_is_idempotent(self):
        a = self.book("11:00")
        b = self.book("11:00")
        self.assertEqual(a["buchungsnummer"], b["buchungsnummer"])
        self.assertEqual(len(self.cal.store.load()), 1)

    def test_find_and_cancel(self):
        self.book("12:00")
        found = self.cal.find_appointments("+491701111111")
        self.assertEqual(found["anzahl"], 1)
        self.assertFalse(self.cal.cancel_appointment("+499999")["erfolg"])
        self.assertTrue(self.cal.cancel_appointment("+491701111111")["erfolg"])
        self.assertEqual(self.cal.find_appointments("+491701111111")["anzahl"], 0)
        # Platz ist wieder frei
        self.assertTrue(self.book("12:00", phone="+491704444444", staff="Lena")["erfolg"])

    def test_cancel_ambiguous(self):
        self.book("12:00")
        self.book("15:00")
        r = self.cal.cancel_appointment("+491701111111")
        self.assertFalse(r["erfolg"])
        self.assertEqual(len(r["termine"]), 2)

    def test_closed_dates(self):
        r = self.cal.check_availability("herren", "2026-11-13")
        self.assertEqual(r["datum"], "2026-11-13")
        cfg = dict(CONFIG, closed_dates=["2026-11-13"])
        cal = Calendar(cfg, self.cal.store, now=lambda: NOW)
        self.assertEqual(cal.check_availability("herren", "2026-11-13")["naechster_freier_tag"], "2026-11-14")


if __name__ == "__main__":
    unittest.main()
