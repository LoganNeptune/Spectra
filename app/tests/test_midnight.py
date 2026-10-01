import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import coordinator as c


class MidnightTests(unittest.TestCase):
    def run_slot(self, when, unavailable=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "runtime/state"
            state.mkdir(parents=True)
            (root / "runtime/spectra.conf").write_text("Shared routine prompt.")
            with patch.object(c, "ROOT", root), patch.object(c, "audit"), \
                    patch.object(c.dt, "datetime", wraps=dt.datetime) as clock, \
                    patch.object(c, "Calendar") as calendar, \
                    patch.object(c, "think", return_value={"action": "none", "reply": "Day overview"}) as think, \
                    patch.object(c.Coordinator, "respond") as respond, \
                    patch.dict(c.os.environ, {"SPECTRA_CONFIG": "runtime/spectra.conf"}):
                clock.now.return_value = when
                events = [{"summary": "Appointment", "start": {"date": when.date().isoformat()}}]
                calendar.return_value.events.return_value = events
                if unavailable:
                    calendar.return_value.events.side_effect = c.SafeError("Unavailable")
                self.assertEqual(c.scheduled_checkin({"SPECTRA_TIMEZONE": "America/New_York"}, state), 0)
                calls = calendar.return_value.events.call_args
                if when.hour == 0:
                    start, end = calls.kwargs["start"], calls.kwargs["end"]
                    self.assertEqual(start.date(), when.date())
                    self.assertEqual(start.hour, 0)
                    self.assertEqual(end.date(), when.date() + dt.timedelta(days=1))
                    self.assertEqual(end.hour, 0)
                else:
                    self.assertEqual(calls.kwargs, {})
                if unavailable:
                    think.assert_not_called()
                    self.assertIn("couldn't read your calendar", respond.call_args.args[1])
                else:
                    prompt = think.call_args.args[2]
                    self.assertIn("Shared routine prompt.", prompt)
                    self.assertEqual("Midnight day orientation" in prompt, when.hour == 0)
                    self.assertEqual(think.call_args.args[3], events)
                return calls.kwargs

    def test_midnight_uses_full_local_day_across_dst(self):
        for date, hours in (("2026-03-08", 23), ("2026-11-01", 25), ("2026-09-30", 24)):
            with self.subTest(date=date):
                when = dt.datetime.fromisoformat(date).replace(tzinfo=ZoneInfo("America/New_York"))
                bounds = self.run_slot(when)
                elapsed = bounds["end"].astimezone(dt.timezone.utc) - bounds["start"].astimezone(dt.timezone.utc)
                self.assertEqual(elapsed.total_seconds(), hours * 3600)

    def test_calendar_failure_has_explicit_notice(self):
        self.run_slot(dt.datetime(2026, 9, 30, tzinfo=ZoneInfo("America/New_York")), unavailable=True)

    def test_other_even_hours_keep_upcoming_window(self):
        self.run_slot(dt.datetime(2026, 9, 30, 2, tzinfo=ZoneInfo("America/New_York")))

    def test_calendar_query_preserves_local_boundaries_and_all_day_events(self):
        start = dt.datetime(2026, 11, 1, tzinfo=ZoneInfo("America/New_York"))
        end = start + dt.timedelta(days=1)
        event = {"id": "all-day", "start": {"date": "2026-11-01"}, "end": {"date": "2026-11-02"}}
        calendar = c.Calendar({})
        with patch.object(calendar, "authenticate"), patch.object(calendar, "request", return_value={"items": [event]}) as request:
            self.assertEqual(calendar.events(start=start, end=end), [event])
            query = request.call_args.kwargs["query"]
            self.assertEqual(query["timeMin"], "2026-11-01T00:00:00-04:00")
            self.assertEqual(query["timeMax"], "2026-11-02T00:00:00-05:00")
            self.assertEqual(query["singleEvents"], "true")
