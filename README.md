# Spectra

A private life coordinator.


## Setup

Requires Linux, Python 3.10+, Python venv/pip support, and timezone data.
Install your selected agent CLI and sign in. systemd and a running cron service support
unattended operation. Run these commands from the project directory:

```sh
python3 -B app/scripts/setup.py --check
python3 -B app/scripts/setup.py
```

Setup creates or reuses `.venv`, installs `requirements.txt`, and creates missing
`.env` and `runtime/spectra.conf` files from the examples. Existing settings are
preserved. It also works when invoked from outside the project directory.
`--check` checks local prerequisites and writes an audit entry only.
Setup does not start the listener, authorize Google, or install cron jobs.

Configure Telegram and Google using the [operations guide](docs/COORDINATOR.md).
Codex is the default; [backend configuration](docs/BACKENDS.md) adds Claude Code,
Grok Build, and Cursor Agent through their logged-in CLIs.
For unattended listening, install the systemd service (as root):

```sh
install -m 0644 app/spectra.service /etc/systemd/system/spectra.service
systemctl daemon-reload
systemctl enable --now spectra.service
```

The supplied unit targets this server's `/root/Desktop/.Spectra` checkout and root
Codex login. Adjust its user, paths, HOME and PATH when deploying elsewhere.
Stop any manually launched listener first. The service starts at boot and restarts
after exits; SSH and tmux can close safely. Scheduled check-ins remain in cron.

```sh
systemctl status spectra.service
systemctl restart spectra.service  # Reload .env after editing
journalctl -u spectra.service -f
systemctl stop spectra.service     # Stop polling until started again or reboot
```

Use `systemctl disable --now spectra.service` to also disable startup at boot.
Stopping the service does not stop cron check-ins.

## Layout

```text
.Spectra/
├── AGENTS.md          # Assistant governance
├── README.md
├── coordinator.py     # Stable launcher for tmux and cron
├── requirements.txt   # Google authorization dependency
├── app/               # Implementation, scripts, and tests
├── docs/              # Operations guide and configuration examples
└── runtime/           # Private credentials, transcript, prompt, state, and logs
```

Hidden `.env` holds private runtime settings; `.venv/` contains Python dependencies.
`runtime/messages.txt` is the append-only conversation history;
`runtime/spectra.conf` is the scheduled check-in prompt. Runtime data is excluded
from version control. The launcher and `.env` stay at the project root.

## Checks

```sh
python3 -B coordinator.py --dry-run
python3 -B -m unittest discover -s app/tests -v
```
