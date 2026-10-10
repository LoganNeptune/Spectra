#!/usr/bin/env python3
"""Prepare a Linux Spectra checkout; --check only checks local prerequisites."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.coordinator import audit
from app import backends
import shlex


def run(label, command):
    audit("setup " + label + " attempted")
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    if result.returncode:
        audit("setup " + label + " failed; command output suppressed")
        raise RuntimeError(label)
    audit("setup " + label + " completed")


def copy_if_missing(source, target):
    try:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(source.read_text(encoding="utf-8"))
        audit("setup created " + target.name)
        print("Created " + target.name)
    except FileExistsError:
        audit("setup preserved existing " + target.name)
        print("Preserved " + target.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="check prerequisites without installing or configuring (audit log only)")
    args = parser.parse_args()
    os.umask(0o077)
    audit("setup prerequisite check attempted")
    if not sys.platform.startswith("linux") or sys.version_info < (3, 10):
        print("Spectra setup requires Linux and Python 3.10 or newer.")
        audit("setup prerequisite check failed; unsupported platform or Python")
        return 1
    try:
        import venv
        from zoneinfo import ZoneInfo
        ZoneInfo("America/New_York")
    except (ImportError, KeyError):
        print("Install your distribution's Python venv support and timezone data, then retry.")
        audit("setup prerequisite check failed; venv or timezone data unavailable")
        return 1
    print("PASS: Linux, Python, venv module and New York timezone data.")
    # Check the selected backend without sourcing .env or displaying its contents.
    env = dict(os.environ)
    if (ROOT / ".env").is_file():
        for line in (ROOT / ".env").read_text().splitlines():
            line = line.strip().removeprefix("export ")
            if line and not line.startswith("#") and "=" in line:
                key, raw = line.split("=", 1)
                if key.strip().startswith("SPECTRA_"):
                    parts = shlex.split(raw, comments=True)
                    env[key.strip()] = parts[0] if len(parts) == 1 else ""
    selected = backends.backend(env)
    try:
        worker = backends.executable(env, selected)
    except ValueError:
        audit("setup prerequisite check failed; Cursor command resolves to Grok")
        print("Cursor command resolves to Grok. Set SPECTRA_CURSOR_COMMAND to the actual Cursor executable.")
        return 1
    print("Selected backend: " + backends.LABELS[selected])
    for command in (worker, "systemctl", "crontab"):
        print(("FOUND: " if shutil.which(command) else "MISSING: ") + command)
    print("The selected agent CLI must be installed and signed in; scheduled check-ins need a running cron service.")
    audit("setup prerequisite check completed")
    if args.check:
        return 0
    python = ROOT / ".venv/bin/python"
    if not (ROOT / ".venv").exists():
        run("virtual environment creation", [sys.executable, "-m", "venv", str(ROOT / ".venv")])
    if not python.is_file():
        print("Existing .venv has no Python executable; inspect it before retrying.")
        audit("setup blocked; existing virtual environment incomplete")
        return 1
    run("dependency installation", [str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
    for name in ("runtime/logs", "runtime/state", "runtime/google-auth"):
        (ROOT / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    copy_if_missing(ROOT / "docs/examples/coordinator.env.example", ROOT / ".env")
    copy_if_missing(ROOT / "docs/examples/spectra.conf.example", ROOT / "runtime/spectra.conf")
    run("coordinator dry run", [str(python), "-B", str(ROOT / "coordinator.py"), "--dry-run"])
    audit("setup completed")
    print("Setup complete. Configure .env and Google authorization using docs/COORDINATOR.md.")
    print("Start the listener and install the scheduled job when ready; setup does not start either.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        audit("setup failed; private details suppressed")
        print("Setup failed. Check Python venv/pip support, network access and directory permissions.")
        raise SystemExit(1)
