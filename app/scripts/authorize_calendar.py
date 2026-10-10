#!/usr/bin/env python3
"""Authorize the only calendar Spectra uses: Google Calendar on the account that syncs to iOS."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.coordinator import ROOT, audit

SCOPE = "https://www.googleapis.com/auth/calendar.events"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize", action="store_true")
    parser.add_argument("--no-browser", action="store_true", help="use an SSH tunnel and open the URL locally")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    os.umask(0o077)
    audit("calendar read/write authorization helper attempted")
    if not args.authorize:
        audit("calendar read/write authorization dry run completed")
        print("DRY RUN: use --authorize to request calendar.events access in your browser.")
        return 0
    target = ROOT / "runtime/google-auth/token-rw.json"
    if target.exists():
        audit("calendar authorization blocked; existing write token preserved")
        print("runtime/google-auth/token-rw.json already exists. Existing credentials were preserved.")
        return 1
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(ROOT / "runtime/google-auth/credentials.json"), [SCOPE])
        audit("Google OAuth browser consent attempted")
        credentials = flow.run_local_server(host="127.0.0.1", port=args.port,
                                           open_browser=not args.no_browser, access_type="offline", prompt="consent")
        if not credentials.refresh_token:
            raise ValueError("refresh token missing")
        granted = credentials.granted_scopes or credentials.scopes
        if SCOPE not in granted:
            raise ValueError("write scope not granted")
        audit("Google OAuth browser consent completed; token save attempted")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(credentials.to_json())
        audit("calendar read/write token saved; no calendar events changed")
        print("Saved runtime/google-auth/token-rw.json. No calendar events were changed.")
        return 0
    except Exception:
        audit("calendar read/write authorization failed; private details suppressed")
        print("Authorization failed. Check the OAuth desktop credentials, dependencies, port and consent settings.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
