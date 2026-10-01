# Agent backends

Spectra supports four logged-in CLI backends: Codex, Claude Code, Grok Build,
and Cursor Agent. Telegram, the append-only transcript, calendar previews and
confirmations, midnight orientation, and file-write requests use the same
coordinator regardless of backend. Codex is the default.

## Select a backend

Set `SPECTRA_BACKEND` in the project's private `.env`:

```sh
SPECTRA_BACKEND=claude
SPECTRA_FULL_ACCESS=1
SPECTRA_CLAUDE_COMMAND=claude
SPECTRA_CLAUDE_MODEL=
```

Then run `systemctl restart spectra.service` and send `/status` in Telegram.
The cron jobs load `.env` on each invocation, so they use the same backend.

| Backend | CLI setting | Sign-in command | Model setting |
| --- | --- | --- | --- |
| Codex | `SPECTRA_CODEX_COMMAND=codex` | `codex login` | `SPECTRA_CODEX_MODEL` or legacy `SPECTRA_MODEL` |
| Claude | `SPECTRA_CLAUDE_COMMAND=claude` | `claude auth login` | `SPECTRA_CLAUDE_MODEL` |
| Grok | `SPECTRA_GROK_COMMAND=grok` | `grok login` | `SPECTRA_GROK_MODEL` |
| Cursor | `SPECTRA_CURSOR_COMMAND=/absolute/path/to/cursor-agent` | `/absolute/path/to/cursor-agent login` | `SPECTRA_CURSOR_MODEL` |

Install and sign in to the selected CLI as the user running the service. The
supplied service runs as root and uses `HOME=/root`. Use an absolute executable
path if the CLI is not on the service's PATH. Leave each model blank to use that
CLI's account default, or choose a model available to that account. The existing
`SPECTRA_MODEL` and `SPECTRA_REASONING_EFFORT` apply only to Codex; they are never
sent to another backend.

Optional reasoning settings are `SPECTRA_CLAUDE_REASONING_EFFORT` (`low`, `medium`,
`high`, `max`) and `SPECTRA_GROK_REASONING_EFFORT` (`none`, `minimal`, `low`,
`medium`, `high`, `xhigh`, `max`), subject to the chosen model's support.
Cursor reasoning is controlled through its model selection.

**Cursor executable collision:** Grok Build can install its own `agent` alias.
On this server `/root/.local/bin/agent` resolves to Grok. Spectra rejects that
executable for the Cursor backend. Set `SPECTRA_CURSOR_COMMAND` to the actual
Cursor binary. If no command is configured, Spectra looks for `cursor-agent`,
then a non-Grok `agent` executable.

## Permissions and authentication

`SPECTRA_FULL_ACCESS=1` enables the selected CLI's file-writing and shell tools.
If the new setting is absent, `SPECTRA_CODEX_FULL_ACCESS` remains its legacy
fallback for all backends. Set `SPECTRA_FULL_ACCESS=0` for conversation-only use;
Claude and Grok tools are denied, and Cursor runs in ask mode with its sandbox.
Calendar changes still use the application's existing preview/confirmation
workflow. Backend errors never trigger a fallback call or repeat a write.

Claude uses non-interactive `dontAsk` mode with an explicit allowlist of
`Bash,Read,Write,Edit,Glob,Grep` when Full Access is enabled. This avoids relying
on bypass mode in root deployments. Grok uses `--always-approve --sandbox off`,
and Cursor uses `--force --sandbox disabled`. Codex retains its existing flags.
Full Access runs with the service user's operating-system permissions.

Existing CLI logins can be used without adding API keys to Spectra. If your
chosen CLI/account needs an explicit credential, only its own configured auth
variables are forwarded: `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` for
Claude, `XAI_API_KEY` or `GROK_API_KEY` for Grok, and `CURSOR_API_KEY` for Cursor.
Keep these in `.env`, never in Telegram. They are redacted from the transcript.
Telegram and Google credentials are never passed to agent subprocesses as
environment variables. A Full Access agent can still read project files.

Every turn starts a fresh invocation with `runtime/messages.txt` supplied as
context. No adapter uses continue/resume flags. Codex and Claude request ephemeral
or nonpersistent sessions; Grok and Cursor may retain local session records
according to their CLI settings. All responses pass the same strict JSON
validation before calendar proposals or Telegram delivery. Claude and Grok
use native JSON-schema output; Cursor reads the request and schema from a
temporary context file and its response is validated locally.

## Verify configuration

```sh
python3 -B app/scripts/setup.py --check
python3 -B -m unittest discover -s app/tests -v

# Two real calls using synthetic data and temporary files, with no Telegram sends:
.venv/bin/python -B app/scripts/check_worker_files.py --live --backend grok
```

The live check does not change the selected backend in `.env`. It requires the
requested CLI to be installed, signed in, and able to use the configured model.
The temporary file is verified after creation and appending, then removed.

Official references:

- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Grok Build headless scripting](https://docs.x.ai/build/cli/headless-scripting)
- [Grok Build CLI reference](https://docs.x.ai/build/cli/reference)
- [Cursor headless CLI](https://cursor.com/docs/cli/headless)
- [Cursor CLI parameters](https://cursor.com/docs/cli/reference/parameters)
