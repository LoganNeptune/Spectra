import copy
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from app import coordinator as c


ENV = {"TELEGRAM_CHAT_ID": "123", "TELEGRAM_USER_ID": "123", "TELEGRAM_BOT_TOKEN": "secret",
       "SPECTRA_TIMEZONE": "America/New_York", "SPECTRA_CALENDAR_WRITE": "1",
       "SPECTRA_MODEL": "configured-model"}
CREATE = {"reply": "Here's the proposed time.", "action": "create", "event_id": "", "summary": "Focus",
          "start": "2026-10-01T09:00:00-04:00", "end": "2026-10-01T10:00:00-04:00"}
EVENT = {"id": "abc", "etag": '"v1"', "summary": "Focus", "start": {"dateTime": CREATE["start"]},
         "end": {"dateTime": CREATE["end"]}, "organizer": {"self": True}}


def update(uid, text, sender=123, chat=123, kind="private"):
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": kind}, "from": {"id": sender}, "text": text}}


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.audit = patch.object(c, "audit").start()
        self.addCleanup(patch.stopall)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.store = c.Store(self.path)
        self.addCleanup(self.store.db.close)
        self.calendar = Mock()
        self.calendar.events.return_value = [EVENT]
        self.calendar.snapshot.return_value = EVENT
        self.model = Mock(return_value=copy.deepcopy(CREATE))
        self.transcript = c.Transcript(Path(self.tmp.name) / "telegram.txt", ENV)
        self.bot = c.Coordinator(dict(ENV), self.store, self.calendar, self.model, self.transcript)
        self.bot.send = Mock()

    def proposal(self):
        self.bot.process(update(1, "Schedule focus tomorrow"))
        self.calendar.apply.assert_not_called()
        return self.store.data["pending"]["code"]

    def test_private_owner_only(self):
        for uid, kwargs in enumerate(({"sender": 999}, {"chat": 999}, {"kind": "group"}), 1):
            self.bot.process(update(uid, "Hi", **kwargs))
        self.bot.send.assert_not_called()
        self.model.assert_not_called()
        self.calendar.events.assert_not_called()
        self.assertFalse(self.transcript.path.exists())

    def test_forwarded_confirmation_ignored(self):
        code = self.proposal()
        message = update(2, "/confirm " + code)
        message["message"]["forward_origin"] = {"type": "user"}
        self.bot.process(message)
        self.calendar.apply.assert_not_called()

    def test_confirm_once_and_persist(self):
        code = self.proposal()
        self.bot.process(update(2, "/confirm wrong"))
        self.calendar.apply.assert_not_called()
        self.bot.process(update(3, "/confirm " + code))
        self.calendar.apply.assert_called_once()
        self.bot.process(update(3, "/confirm " + code))
        self.bot.process(update(4, "/confirm " + code))
        self.calendar.apply.assert_called_once()
        reopened = c.Store(self.path)
        self.addCleanup(reopened.db.close)
        self.assertEqual(reopened.data["pending"]["status"], "completed")
        self.assertNotIn("history", reopened.data)
        self.assertIn("Calendar change completed", self.transcript.read())

    def test_expired_proposal(self):
        code = self.proposal()
        self.store.data["pending"]["expires"] = time.time() - 1
        self.bot.process(update(2, "/confirm " + code))
        self.calendar.apply.assert_not_called()

    def test_write_disabled(self):
        code = self.proposal()
        self.bot.env["SPECTRA_CALENDAR_WRITE"] = "0"
        self.bot.process(update(2, "/confirm " + code))
        self.calendar.apply.assert_not_called()

    def test_paused_prevents_confirmation_and_model(self):
        code = self.proposal()
        self.bot.process(update(2, "/pause"))
        self.bot.process(update(3, "/confirm " + code))
        self.calendar.apply.assert_not_called()
        self.assertEqual(self.model.call_count, 1)

    def test_failure_never_replayed(self):
        code = self.proposal()
        self.calendar.apply.side_effect = TimeoutError("secret")
        self.bot.process(update(2, "/confirm " + code))
        self.assertEqual(self.store.data["pending"]["status"], "uncertain")
        self.bot.process(update(3, "/confirm " + code))
        self.calendar.apply.assert_called_once()
        self.assertNotIn("secret", self.bot.send.call_args.args[0])
        self.bot.process(update(4, "/resolve " + code))
        self.assertIsNone(self.store.data["pending"])

    def test_mutation_intent_saved_before_request(self):
        code = self.proposal()
        def apply(pending):
            other = c.Store(self.path)
            try:
                self.assertEqual(other.data["pending"]["status"], "attempting")
                self.assertEqual(other.data["offset"], 3)
            finally:
                other.db.close()
        self.calendar.apply.side_effect = apply
        self.bot.process(update(2, "/confirm " + code))

    def test_send_failure_cannot_repeat_write(self):
        code = self.proposal()
        self.bot.send.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.bot.process(update(2, "/confirm " + code))
        self.bot.process(update(2, "/confirm " + code))
        self.calendar.apply.assert_called_once()

    def test_cancel(self):
        code = self.proposal()
        self.bot.process(update(2, "/cancel"))
        self.bot.process(update(3, "/confirm " + code))
        self.calendar.apply.assert_not_called()

    def test_unavailable_calendar_blocks_proposal(self):
        self.calendar.events.side_effect = TimeoutError()
        self.bot.process(update(1, "Plan my morning"))
        self.assertIsNone(self.store.data["pending"])
        self.assertFalse(self.model.call_args.args[-1])

    def test_unknown_event_id_rejected(self):
        self.model.return_value = {**CREATE, "action": "delete", "event_id": "invented"}
        self.bot.process(update(1, "Delete focus"))
        self.assertIsNone(self.store.data["pending"])
        self.calendar.snapshot.assert_not_called()

    def test_delete_preview_uses_real_event(self):
        self.model.return_value = {**CREATE, "action": "delete", "event_id": "abc", "summary": "invented"}
        code = self.proposal()
        pending = self.store.data["pending"]
        self.assertIn("Focus", pending["preview"])
        self.assertNotIn("invented", pending["preview"])
        self.bot.process(update(2, "/confirm " + code))
        self.calendar.apply.assert_called_once()

    def test_timezone_required(self):
        self.bot.env.pop("SPECTRA_TIMEZONE")
        self.bot.process(update(1, "Schedule focus"))
        self.assertIsNone(self.store.data["pending"])

    def test_transcript_retains_full_history_and_rereads(self):
        self.model.return_value = {**CREATE, "action": "none"}
        for i in range(15):
            self.bot.process(update(i, f"Help plan {i}"))
        self.assertNotIn("history", self.store.data)
        context = self.model.call_args.args[1]
        self.assertIn("Help plan 0", context)
        self.assertIn("Help plan 14", context)
        self.assertIn("spectra", context)
        original = self.transcript.path.read_bytes()
        self.bot.process(update(16, "/forget"))
        self.assertTrue(self.transcript.path.read_bytes().startswith(original))

    def test_input_durable_before_codex(self):
        def model(*args):
            self.assertIn("Latest message", self.transcript.read())
            return {**CREATE, "action": "none"}
        self.bot.model = model
        self.bot.process(update(1, "Latest message"))

    def test_transcript_deduplicates_restart_before_checkpoint(self):
        self.transcript.append("user-1", "user", "hello")
        self.bot.process(update(1, "hello"))
        records = [json.loads(line) for line in self.transcript.read().splitlines()]
        self.assertEqual(sum(r["id"] == "user-1" for r in records), 1)

    def test_transcript_failure_does_not_consume_input(self):
        with patch.object(self.transcript, "append", side_effect=OSError()):
            with self.assertRaises(OSError):
                self.bot.process(update(1, "hello"))
        self.assertEqual(self.store.data["offset"], 0)
        self.model.assert_not_called()

    def test_legacy_history_migrates_without_duplicates(self):
        self.store.data["history"] = [{"role": "user", "content": "old message"}]
        c.Coordinator(ENV, self.store, self.calendar, self.model, self.transcript)
        self.assertNotIn("history", self.store.data)
        self.store.data["history"] = [{"role": "user", "content": "old message"}]
        c.Coordinator(ENV, self.store, self.calendar, self.model, self.transcript)
        self.assertEqual(self.transcript.read().count("old message"), 1)

    def test_transcript_redaction_and_permissions(self):
        self.transcript.append("test", "user", "My token is secret")
        self.assertNotIn("secret", self.transcript.read())
        self.assertEqual(self.transcript.path.stat().st_mode & 0o777, 0o600)

    def test_oversized_transcript_never_silently_truncated(self):
        self.transcript.append("test", "user", "text")
        self.transcript.env = {"SPECTRA_TRANSCRIPT_MAX_BYTES": "10"}
        with self.assertRaises(c.SafeError):
            self.transcript.read()

    def test_uncertain_delivery_recorded(self):
        self.bot.send.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.bot.process(update(1, "hello"))
        self.assertIn("delivery incomplete or uncertain", self.transcript.read())


class CalendarTests(unittest.TestCase):
    def setUp(self):
        patch.object(c, "audit").start()
        self.addCleanup(patch.stopall)

    def test_time_validation(self):
        c.event_body(CREATE, ENV["SPECTRA_TIMEZONE"])
        for changes in ({"end": CREATE["start"]}, {"start": "2026-10-01T09:00:00"},
                        {"start": "2026-10-01T09:00:00-05:00"}):
            with self.assertRaises(c.SafeError):
                c.event_body({**CREATE, **changes}, ENV["SPECTRA_TIMEZONE"])
        self.assertEqual(c.event_body({**CREATE, "start": "2026-10-01", "end": "2026-10-02"}, "UTC")["start"],
                         {"date": "2026-10-01"})

    def test_invites_and_series_blocked(self):
        calendar = c.Calendar(ENV)
        for extra in ({"attendees": [{"email": "private@example.com"}]}, {"recurrence": ["RRULE:FREQ=DAILY"]},
                      {"organizer": {"self": False}}, {"eventType": "birthday"}):
            with patch.object(calendar, "request", return_value={**EVENT, **extra}):
                with self.assertRaises(c.SafeError):
                    calendar.snapshot("abc")

    def test_stale_event_blocked_and_if_match(self):
        calendar = c.Calendar(ENV)
        pending = {"action": "update", "event_id": "abc", "before": EVENT, "body": c.event_body(CREATE, ENV["SPECTRA_TIMEZONE"])}
        with patch.object(calendar, "authenticate"), patch.object(calendar, "snapshot", return_value={**EVENT, "etag": '"v2"'}), patch.object(calendar, "request") as request:
            with self.assertRaises(c.SafeError):
                calendar.apply(pending)
            request.assert_not_called()
        with patch.object(calendar, "authenticate"), patch.object(calendar, "snapshot", return_value=EVENT), patch.object(calendar, "request") as request:
            calendar.apply(pending)
            self.assertEqual(request.call_args.args[3], '"v1"')

    def test_create_uses_stable_id(self):
        calendar = c.Calendar(ENV)
        pending = {"action": "create", "event_id": "abc123", "body": c.event_body(CREATE, ENV["SPECTRA_TIMEZONE"])}
        with patch.object(calendar, "authenticate"), patch.object(calendar, "request") as request:
            calendar.apply(pending)
            self.assertEqual(request.call_args.kwargs["body"]["id"], "abc123")

    def test_pagination(self):
        calendar = c.Calendar(ENV)
        with patch.object(calendar, "authenticate"), patch.object(calendar, "request", side_effect=[
            {"items": [EVENT], "nextPageToken": "next"}, {"items": [{"id": "gone", "status": "cancelled"}]}]) as request:
            events = calendar.events()
            self.assertEqual(len(events), 1)
            self.assertEqual(request.call_args.kwargs["query"]["pageToken"], "next")

    def test_codex_stdin_schema_auth_and_secret_isolation(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(c, "ROOT", Path(folder)):
            (Path(folder) / "runtime/state").mkdir(parents=True)
            def run(command, **kwargs):
                self.assertEqual(command[:2], ["codex", "exec"])
                self.assertIn("--ephemeral", command)
                self.assertIn("--ignore-user-config", command)
                self.assertIn('forced_login_method="chatgpt"', command)
                self.assertIn("features.shell_tool=false", command)
                self.assertEqual(command[-1], "-")
                self.assertNotIn("TELEGRAM_BOT_TOKEN", kwargs["env"])
                self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
                self.assertEqual(kwargs["env"]["CODEX_HOME"], "/configured/auth")
                self.assertNotIn("hi", command)
                self.assertIn("full durable transcript", kwargs["input"])
                self.assertIn("hi", kwargs["input"])
                schema = Path(command[command.index("--output-schema") + 1])
                self.assertEqual(json.loads(schema.read_text()), c.SCHEMA)
                output = Path(command[command.index("--output-last-message") + 1])
                output.write_text(json.dumps(CREATE))
                return subprocess.CompletedProcess(command, 0)
            with patch.object(c.subprocess, "run", side_effect=run), patch.object(c, "http") as http:
                self.assertEqual(c.think({**ENV, "CODEX_HOME": "/configured/auth", "OPENAI_API_KEY": "unused"},
                                         "full durable transcript", "hi", [], True), CREATE)
                http.assert_not_called()

    def test_codex_failure_and_invalid_output_blocked(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(c, "ROOT", Path(folder)):
            (Path(folder) / "runtime/state").mkdir(parents=True)
            for failure in (FileNotFoundError(), subprocess.TimeoutExpired("codex", 180)):
                with patch.object(c.subprocess, "run", side_effect=failure):
                    with self.assertRaises(c.SafeError):
                        c.think(ENV, "", "hi", [], True)
            def invalid(command, **kwargs):
                Path(command[command.index("--output-last-message") + 1]).write_text('{"action":"shell"}')
                return subprocess.CompletedProcess(command, 0)
            with patch.object(c.subprocess, "run", side_effect=invalid):
                with self.assertRaises(c.SafeError):
                    c.think(ENV, "", "hi", [], True)

    def test_full_access_and_reasoning_apply_to_each_codex_run(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(c, "ROOT", Path(folder)):
            (Path(folder) / "runtime/state").mkdir(parents=True)
            def run(command, **kwargs):
                self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
                self.assertNotIn("--sandbox", command)
                self.assertNotIn("features.shell_tool=false", command)
                self.assertIn("features.shell_tool=true", command)
                self.assertIn('model_reasoning_effort="low"', command)
                self.assertEqual(command[command.index("--model") + 1], "gpt-6-luna")
                self.assertEqual(kwargs["cwd"], Path(folder))
                self.assertIn("preview-and-confirm", kwargs["input"])
                self.assertIn("authenticated, direct request", kwargs["input"])
                self.assertIn("verify the resulting file", kwargs["input"])
                self.assertIn("File writes do not use /confirm codes", kwargs["input"])
                self.assertIn("runtime/profiles/profile.txt", kwargs["input"])
                Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(CREATE))
                return subprocess.CompletedProcess(command, 0)
            env = {**ENV, "SPECTRA_CODEX_FULL_ACCESS": "1", "SPECTRA_REASONING_EFFORT": "low", "SPECTRA_MODEL": "gpt-6-luna"}
            with patch.object(c.subprocess, "run", side_effect=run) as process:
                c.think(env, "transcript", "hi", [], True)
                c.think(env, "transcript", "hi again", [], True)
                self.assertEqual(process.call_count, 2)

    def test_invalid_reasoning_rejected_before_codex(self):
        with patch.object(c.subprocess, "run") as process:
            with self.assertRaises(c.SafeError):
                c.think({**ENV, "SPECTRA_REASONING_EFFORT": "arbitrary"}, "", "hi", [], True)
            process.assert_not_called()

    def test_default_dry_run_no_network_or_state(self):
        with patch("sys.argv", ["coordinator.py"]), patch.object(c, "http") as http, patch.object(c, "Store") as store:
            self.assertEqual(c.main(), 0)
            http.assert_not_called()
            store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
