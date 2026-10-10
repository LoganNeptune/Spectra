#!/usr/bin/env python3
"""Install the user-approved New York even-hour Spectra job, preserving other cron entries."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.coordinator import audit

MARKER = "# SPECTRA-EVEN-HOUR-CHECK-IN"
JOB = ("0 0,2,4,6,8,10,12,14,16,18,20,22 * * * "
       "cd /root/Desktop/.Spectra && /bin/bash -c "
       "'set -a; source .env; set +a; exec env SPECTRA_LIVE=1 "
       "SPECTRA_SCHEDULED_CHECKIN=1 python3 -B coordinator.py --scheduled-check-in' "
       ">> runtime/logs/scheduled-checkin.log 2>&1")
BLOCK = "\n".join((MARKER, "CRON_TZ=America/New_York", JOB))


def main():
    audit("even-hour scheduled cron installation attempted")
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if existing.returncode:
        # A genuinely empty crontab is a valid starting point; preserve other failures.
        if "no crontab for" not in existing.stderr.lower():
            audit("even-hour cron installation failed; existing crontab left unchanged")
            print("Could not read the current user's crontab; nothing was changed.")
            return 1
        current = ""
    else:
        current = existing.stdout.rstrip()
    if MARKER in current or "coordinator.py --scheduled-check-in" in current:
        audit("even-hour cron installation skipped; matching job already exists")
        print("A scheduled coordinator entry already exists; crontab was left unchanged.")
        return 0
    updated = current + ("\n\n" if current else "") + BLOCK + "\n"
    result = subprocess.run(["crontab", "-"], input=updated, text=True, capture_output=True)
    if result.returncode:
        audit("even-hour cron installation failed; crontab command rejected update")
        print("Cron rejected the new entry; no successful installation was reported.")
        return 1
    audit("even-hour scheduled cron installation completed; existing entries preserved")
    print("Installed Spectra's dedicated 00:00–22:00 even-hour New York check-in job; existing cron entries preserved.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        audit("even-hour cron installation failed; details suppressed")
        print("Could not install the scheduled check-in; existing cron entries were not intentionally removed.")
        raise SystemExit(1)
