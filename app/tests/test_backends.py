import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app import backends as b, coordinator as c

REPLY = {"reply": "Ready.", "action": "none", "event_id": "", "summary": "", "start": "", "end": ""}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "runtime/state").mkdir(parents=True)
        (self.root / "AGENTS.md").write_text("Project-specific governance fixture.")
        self.env = {"HOME": str(self.root), "PATH": os.environ["PATH"], "SPECTRA_TIMEZONE": "America/New_York",
                    "SPECTRA_MODEL": "codex-only-model", "SPECTRA_REASONING_EFFORT": "low",
                    "TELEGRAM_BOT_TOKEN": "private-bot-token", "SPECTRA_GOOGLE_REFRESH_TOKEN": "private-google-token",
                    "ANTHROPIC_API_KEY": "anthropic-key", "XAI_API_KEY": "xai-key", "CURSOR_API_KEY": "cursor-key"}

    def call(self, name, envelope, access=True, status=0):
        env = {**self.env, "SPECTRA_BACKEND": name, "SPECTRA_FULL_ACCESS": "1" if access else "0",
               f"SPECTRA_{name.upper()}_COMMAND": "fixture-cli"}
        captured = {}

        def run(command, **kwargs):
            captured.update(command=command, **kwargs)
            if name == "claude":
                prompt = kwargs["input"]
            elif name == "grok":
                prompt = Path(command[command.index("--prompt-file") + 1]).read_text()
            else:
                context = next(arg for arg in command if arg.startswith("Read @"))
                prompt = Path(context.split("@", 1)[1].split(" for the complete", 1)[0]).read_text()
            self.assertIn("private conversation fixture", prompt)
            self.assertIn("Project-specific governance fixture.", prompt)
            if access:
                self.assertIn(b.LABELS[name], prompt)
            self.assertNotIn("private conversation fixture", " ".join(command))
            self.assertNotIn("--resume", command)
            self.assertNotIn("--continue", command)
            self.assertNotIn("codex-only-model", command)
            self.assertNotIn("TELEGRAM_BOT_TOKEN", kwargs["env"])
            self.assertNotIn("SPECTRA_GOOGLE_REFRESH_TOKEN", kwargs["env"])
            key = {"claude": "ANTHROPIC_API_KEY", "grok": "XAI_API_KEY", "cursor": "CURSOR_API_KEY"}[name]
            self.assertIn(key, kwargs["env"])
            for other in {"ANTHROPIC_API_KEY", "XAI_API_KEY", "CURSOR_API_KEY"} - {key}:
                self.assertNotIn(other, kwargs["env"])
            kwargs["stdout"].write(json.dumps(envelope))
            return subprocess.CompletedProcess(command, status)

        with patch.object(c, "ROOT", self.root), patch.object(c, "audit"), \
                patch.object(b.subprocess, "run", side_effect=run):
            value = c.think(env, "private conversation fixture", "Help organize today", [], True)
        self.assertEqual(value, REPLY)
        self.assertFalse(list((self.root / "runtime/state").glob("spectra-*")))
        return captured

    def test_all_cli_envelopes_share_response_validation(self):
        for name, envelope in (("claude", {"structured_output": REPLY}),
                               ("grok", {"text": json.dumps(REPLY)}),
                               ("cursor", {"result": "```json\n" + json.dumps(REPLY) + "\n```"})):
            with self.subTest(backend=name):
                call = self.call(name, envelope)
                self.assertEqual(call["cwd"], self.root)
                if name == "claude":
                    self.assertIn("--allowedTools", call["command"])
                    self.assertIn("Bash,Read,Write,Edit,Glob,Grep", call["command"])
                    self.assertIn("--no-session-persistence", call["command"])
                elif name == "grok":
                    self.assertIn("--always-approve", call["command"])
                    self.assertIn("--no-subagents", call["command"])
                else:
                    self.assertIn("--force", call["command"])
                    self.assertIn("disabled", call["command"])

    def test_tools_disabled_modes_are_selected(self):
        for name, envelope in (("claude", {"structured_output": REPLY}),
                               ("grok", {"text": json.dumps(REPLY)}),
                               ("cursor", {"result": json.dumps(REPLY)})):
            with self.subTest(backend=name):
                call = self.call(name, envelope, access=False)
                self.assertNotEqual(call["cwd"], self.root)
                if name in ("claude", "grok"):
                    command = call["command"]
                    self.assertEqual(command[command.index("--tools") + 1], "")
                else:
                    self.assertIn("ask", call["command"])
                    self.assertNotIn("--force", call["command"])

    def test_failures_and_malformed_results_are_sanitized(self):
        for name in ("claude", "grok", "cursor"):
            for envelope, status in (({"error": "secret-response-body"}, 0),
                                     ({"structured_output": {**REPLY, "action": "shell"}}, 0),
                                     ({"structured_output": REPLY}, 1),
                                     ({"structured_output": {**REPLY, "reply": "x" * 6001}}, 0)):
                with self.subTest(backend=name, status=status):
                    with self.assertRaises(c.SafeError) as error:
                        self.call(name, envelope, status=status)
                    self.assertNotIn("secret-response-body", str(error.exception))

    def test_cursor_rejects_grok_agent_alias(self):
        with patch.object(b.shutil, "which", return_value="/root/.grok/bin/agent"):
            with self.assertRaises(ValueError):
                b.executable({}, "cursor")

    def test_backend_defaults_and_model_isolation(self):
        self.assertEqual(b.backend({}), "codex")
        self.assertEqual(b.model(self.env), "codex-only-model")
        for name in ("claude", "grok", "cursor"):
            env = {**self.env, "SPECTRA_BACKEND": name}
            self.assertEqual(b.model(env), "")
            self.assertEqual(b.effort(env), "")
            env[f"SPECTRA_{name.upper()}_MODEL"] = "specific-model"
            self.assertEqual(b.model(env), "specific-model")
        with self.assertRaises(ValueError):
            b.backend({"SPECTRA_BACKEND": "unknown"})
        self.assertFalse(b.full_access({"SPECTRA_CODEX_FULL_ACCESS": "1", "SPECTRA_FULL_ACCESS": "0"}))

    def test_all_new_auth_values_are_redacted_in_transcripts(self):
        transcript = c.Transcript(self.root / "notes.txt", self.env)
        for key in ("ANTHROPIC_API_KEY", "XAI_API_KEY", "CURSOR_API_KEY"):
            self.assertEqual(transcript.redact(self.env[key]), "[REDACTED]")

    def test_selected_backend_is_reported_in_status(self):
        with patch.object(c, "audit"):
            store = c.Store(self.root / "state.sqlite3")
            try:
                env = {**self.env, "SPECTRA_BACKEND": "grok", "SPECTRA_GROK_MODEL": "account-model",
                       "SPECTRA_FULL_ACCESS": "1"}
                bot = c.Coordinator(env, store, transcript=c.Transcript(self.root / "notes.txt", env))
                status = bot.handle("/status")
                self.assertIn("Backend: Grok Build, account-model", status)
                self.assertIn("Full Access", status)
                self.assertNotIn("codex-only-model", status)
            finally:
                store.db.close()
