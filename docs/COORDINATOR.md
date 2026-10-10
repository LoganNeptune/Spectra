# Talk to Spectra in Telegram

Google Calendar is optional and off by default (`SPECTRA_CALENDAR_ENABLED=0`).
Leave it off to use conversations and scheduled check-ins without Google credentials.
No Calendar reads, proposals, writes, or midnight Calendar agenda are performed
while disabled. To opt in, set `SPECTRA_CALENDAR_ENABLED=1`, complete step 4 below,
and restart the coordinator. `SPECTRA_CALENDAR_WRITE=1` separately permits confirmed
writes. Disabling the integration also blocks confirmation of existing proposals.

Codex is the default agent backend. See [backend configuration](BACKENDS.md) for
Claude Code, Grok Build, Cursor Agent, model settings and CLI authentication.

`coordinator.py` provides a private, two-way conversation loop and cron-triggered
proactive check-ins. Both use the same `runtime/spectra.conf` task prompt and append-only
`runtime/messages.txt` transcript. It reads Google Calendar, discusses plans, and proposes
creating, moving, renaming or deleting personal events. Each write requires the
exact `/confirm CODE` command shown with its preview. Proposals expire after
15 minutes. This is the current application confirmation workflow.

Every accepted message is appended to `runtime/messages.txt` before processing. Replies
and delivery outcomes are appended too. Each conversational turn rereads the full
file and supplies it to a fresh `codex exec` invocation through standard input.
There is no rolling conversation history in SQLite and no resumed Codex session.

Example conversation:

> You: I'm overwhelmed tomorrow. Help me find time for the report.
>
> Spectra: Discusses priorities and available time, with buffers and a fallback.
>
> You: Put a 45-minute focus block at 9 tomorrow.
>
> Spectra: Shows the event title, full date, times and timezone, and a confirmation code.
>
> You: /confirm CODE
>
> Spectra: Applies that exact change and reports the result.

Run all commands below from the project root.

## Configure once

Use Python 3.10+ on Linux with timezone data. The coordinator uses only the standard
library. The interactive authorization helper additionally needs
`google-auth-oauthlib`, installed in a project-local virtual environment if needed:

```sh
python3 -B app/scripts/setup.py
```

1. Configure your Telegram bot token and your **numeric private chat ID and user
   ID**. Both must match incoming messages. Group chats, forwarded messages,
   edited messages and messages from other users cannot trigger actions.
2. Install a current Codex CLI and sign in with ChatGPT as the user running Spectra
   (`codex login`; for supported headless setups, `codex login --device-auth`).
   No OpenAI API key is needed. `SPECTRA_CODEX_COMMAND` defaults to `codex`; it must
   be a single executable, not a shell command string. `SPECTRA_MODEL` is optional.
   The worker requires support for `--ignore-user-config`, `--ignore-rules`,
   `--ephemeral`, `--output-schema`, and `--output-last-message`.
   It uses the existing Codex authentication location, forces ChatGPT auth, and
   ignores user configuration for these runs. `SPECTRA_CODEX_FULL_ACCESS=1` selects
   Full Access on every coordinator invocation: shell tools are enabled and Codex
   runs in the project directory without sandbox or command approval prompts.
   These commands have the permissions of the server user running the service
   (root on this server). Calendar confirmation remains an
   application workflow, not a security boundary for unrestricted commands.
   Set this variable to `0` or unset it for the read-only, tools-disabled worker.
   Apps and configured MCP servers remain disabled. Telegram and Google secrets
   are not inherited as environment variables; Full Access can still read files
   accessible to the server user. These flags do not change your regular Codex configuration.

   With Full Access enabled, direct Telegram requests can read, create, edit and
   append project files through Codex tools. Spectra verifies the file before
   reporting success. For example, "Save these preferences to my profile" uses
   `runtime/profiles/profile.txt` unless you specify another project path. Later
   conversations can read that saved profile. File operations do not require a
   calendar `/confirm` code. The conversation transcript remains separate.
3. Spectra defaults to your selected timezone, `America/New_York`, following EST
   and EDT automatically. Set `SPECTRA_TIMEZONE` only if you want to override it.
4. Optional: enable `SPECTRA_CALENDAR_ENABLED=1` to connect Google Calendar. Put Google OAuth
   **desktop-client** credentials in `runtime/google-auth/credentials.json`, with the
   Calendar API enabled, then authorize the `calendar.events` scope:

```sh
.venv/bin/python app/scripts/authorize_calendar.py --authorize
```

For a headless server, forward localhost port 8080 from your own computer:

```sh
ssh -L 8080:127.0.0.1:8080 your-server
# In the project on that server:
.venv/bin/python app/scripts/authorize_calendar.py --authorize --no-browser --port 8080
```

Open the printed Google consent URL on your computer. The helper binds only to
server loopback. It creates `runtime/google-auth/token-rw.json` with mode 0600 and never
replaces an existing token. That file is the only credential source. Client id,
client secret, and refresh token in the environment are not accepted.

5. Set `SPECTRA_GOOGLE_CALENDAR_ID` to one calendar on that Google account
   (`primary` by default). Only that calendar is accessed. The iPhone Calendar
   app shows these events when the same Google account is added under
   Settings > Calendar > Accounts. Set `SPECTRA_CALENDAR_WRITE=1` to enable
   confirmed writes. Without it, conversations and previews work but
   confirmations cannot write.

See [`docs/examples/coordinator.env.example`](examples/coordinator.env.example) for all variables. Supply actual values through your
existing private environment configuration; the program does not automatically
load dotenv files. Never put credentials in chat or commit them. OAuth tokens for
Google apps in Testing can expire after seven days; configure your OAuth project
for the intended ongoing use and reauthorize when needed.

### Model and allowance

The selected starting configuration is `SPECTRA_MODEL=gpt-6-luna` and
`SPECTRA_REASONING_EFFORT=low`, aimed at routine planning and conversation. Model
access depends on your Codex account. Leave either blank to use the Codex default,
or increase reasoning when a task needs more analysis. Low reasoning is a workload
choice here; evaluate the quality of your actual scheduling requests.

There is no guaranteed 24-calls-per-day setting. Usage depends on your plan,
context, model, reasoning, tool use, and other activity sharing the allowance.
Review the Codex usage dashboard. Spectra resends the full growing transcript,
which can increase per-turn usage. Full Access does not increase the allowance.

These settings apply to the listener and scheduled check-ins.

## Scheduled check-ins

`runtime/spectra.conf` is the shared task prompt. The cron entry below calls
`--scheduled-check-in` at 00:00, 02:00, ..., 22:00 using New York local time,
including overnight. Each run reviews upcoming events and all of `runtime/messages.txt`,
then appends its Telegram reply. Calendar writes are suppressed for scheduled
check-ins; direct requests still use the usual confirmation preview. `/pause`
also pauses scheduled check-ins and `/resume` restarts them.

At midnight, the check-in specifically reads the new local day's calendar, from
midnight to the following midnight in `America/New_York`, including DST changes.
It uses the shared prompt plus a day-orientation instruction to summarize the
agenda, first timed commitment, all-day events, overlaps and preparation needs,
then suggest a manageable first step after waking. If Calendar cannot be read,
it sends an explicit unavailable notice instead of inferring an empty agenda.
The other even-hour check-ins retain their usual upcoming-calendar window.

The request for this exact schedule is recorded in `AGENTS.md`. The cron command
uses this project only and sets explicit live opt-ins on that entry. It appends no
secrets to the crontab; credentials come from the private `.env` file.

```cron
CRON_TZ=America/New_York
0 0,2,4,6,8,10,12,14,16,18,20,22 * * * cd /root/Desktop/.Spectra && /bin/bash -c 'set -a; source .env; set +a; exec env SPECTRA_LIVE=1 SPECTRA_SCHEDULED_CHECKIN=1 python3 -B coordinator.py --scheduled-check-in' >> runtime/logs/scheduled-checkin.log 2>&1
```

Inspect the installed entry with `crontab -l`. Remove only the dedicated line
containing `coordinator.py --scheduled-check-in` to stop check-ins; leave unrelated
crontab jobs alone. The cron runner is independent of the systemd conversation
listener, but both share pause state and `runtime/messages.txt`.

The persistent listener runs as `spectra.service`, independently of SSH and tmux.
The service uses the existing `.env`, virtual environment and root Codex login.
It starts at boot and automatically restarts five seconds after an exit. After
changing `.env`, run `systemctl restart spectra.service`.

```sh
systemctl status spectra.service
journalctl -u spectra.service -f
systemctl stop spectra.service
systemctl start spectra.service
```

`systemctl disable --now spectra.service` stops it and disables startup at boot.
The separate cron check-ins retain their own controls. Installation and deployment
paths are documented in [README.md](../README.md).

Use `/status` in Telegram to verify the running model, reasoning and permissions.

## Run and stop

```sh
python3 coordinator.py --dry-run
python3 -B -m unittest discover -s app/tests -v
# Start the installed persistent listener:
systemctl start spectra.service
```

For manual debugging, stop the service before running `python3 coordinator.py --live`
with `.env` loaded. Ctrl-C stops that manual process. A project-local lock prevents
two copies from running against the same state. Use only one polling consumer per Telegram bot.
If the bot has an existing webhook, startup stops without removing it.

`--dry-run` wins over `--live` and makes no network requests. The conversation listener uses `--live`; the scheduled command also requires
`SPECTRA_LIVE=1` and `SPECTRA_SCHEDULED_CHECKIN=1`.

Telegram commands:

| Command | Effect |
| --- | --- |
| `/help` | Show usage |
| `/status` | Show pause/write/timezone/proposal state |
| `/confirm CODE` | Apply the displayed, unexpired change once |
| `/cancel` | Cancel an unattempted proposal |
| `/pause`, `/resume` | Pause/resume conversations and writes |
| `/resolve CODE` | After manually checking an uncertain write, clear it without retrying |

Pausing also suppresses scheduled check-ins. `runtime/messages.txt` is append-only: `/forget` reports that no
messages were erased. Explicitly archive or edit the transcript on the server to
reset context; preserve its JSON-lines format if editing. `/cancel` handles pending
proposals independently of the transcript.

## Boundaries and recovery

The first version supports text conversations and ordinary personal events with
no guests. It does not invite or email people, change a whole recurring series,
or handle voice notes. Recurring instances can be changed individually when they
meet the personal-event checks. Calendar context covers the next 14 days by
default; `SPECTRA_CALENDAR_DAYS` accepts 1–31. It does not infer availability outside
that window. Creating a block on another date still requires your explicit review.

Updates and deletes use Google's event version (ETag) to reject a change if the
event has changed since its preview. Writes are durably marked as attempted before
the request. An interrupted or uncertain write is never automatically retried:
check Google Calendar, then follow `/status` and `/resolve CODE`. A confirmed new
event also uses a stable event ID to prevent duplicate insertions.

Authorized text is durably appended before checkpointing the update. Transcript
entry IDs prevent duplicate input records if interrupted before that checkpoint.
Updates are checkpointed before Codex or calendar actions. A crash or delivery
failure can therefore lose a reply; ask again or check `/status`. This favors avoiding
replayed writes over guaranteed delivery. Incoming Telegram messages already
queued when the process starts may be processed, but cannot write without a fresh
matching confirmation. Expired proposals cannot execute.

`runtime/messages.txt` is a UTF-8 text file with one JSON record per line: entry ID,
timestamp, speaker and text. Newlines inside messages are escaped to keep record
boundaries unambiguous. It is created with owner-only permissions and excluded
from Git. Configured credential values are redacted; do not send new secrets as
chat messages. Prepared replies and delivery results are recorded separately so
Codex can distinguish an attempted reply from a confirmed delivery. Old SQLite
rolling history, if any, is appended once on startup and removed from active state.
An older `telegram.txt`, if present, is copied once to `runtime/messages.txt` without
replacing an existing transcript.


`runtime/state/coordinator.sqlite3` retains operational state such as the Telegram cursor,
pause flag, last scheduled slot and calendar proposal. These files are not encrypted. The full
transcript and calendar context are sent through Codex to its model on each turn.
`--ephemeral` avoids persisting Codex session files; service retention still applies.
To avoid silently forgetting older messages, a transcript larger than
`SPECTRA_TRANSCRIPT_MAX_BYTES` (default 200,000 bytes) blocks conversational turns
with an explanation. The file is never automatically truncated or summarized.
Review/archive it explicitly or increase the limit within your model's context
capacity. Confirmation and control commands remain usable. Audit entries in
`runtime/logs/coordinator.log` contain action names and outcomes, not conversation bodies,
tokens, HTTP URLs or raw error responses.

The legacy Rust dispatcher and read-only adapter have been removed. There is no
second calendar protocol. The coordinator has transcript context, but no separate
long-term task database yet.

Official references:
- https://core.telegram.org/bots/api#getupdates
- https://developers.google.com/workspace/calendar/api/v3/reference/events/insert
- https://developers.google.com/workspace/calendar/api/guides/version-resources
- https://developers.openai.com/codex/noninteractive
- https://developers.openai.com/codex/cli/reference
- https://learn.chatgpt.com/docs/models
- https://learn.chatgpt.com/docs/pricing
- https://learn.chatgpt.com/docs/agent-approvals-security
