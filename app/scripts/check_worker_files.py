#!/usr/bin/env python3
"""Verify real agent file creation and appending, without sending Telegram messages."""
import argparse
import os
from pathlib import Path
import shlex
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.coordinator import ROOT, audit, think
from app import backends


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--backend", choices=tuple(backends.LABELS))
    args = parser.parse_args()
    if not args.live:
        audit("agent file-write verification dry run completed")
        print("DRY RUN: use --live [--backend NAME] to make two agent calls with synthetic data and temporary project files.")
        return 0
    audit("agent file-write verification attempted")
    env = dict(os.environ)
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        parts = shlex.split(raw, comments=True)
        if len(parts) > 1:
            raise ValueError("unsupported environment value")
        env[key.strip()] = parts[0] if parts else ""
    if args.backend:
        env["SPECTRA_BACKEND"] = args.backend
    if not backends.full_access(env):
        raise ValueError("Full Access disabled")
    env["SPECTRA_SCHEDULED_CHECKIN"] = "0"
    parent = ROOT / "runtime/profiles"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="file-check-", dir=parent) as folder:
        path = Path(folder) / "profile.txt"
        relative = str(path.relative_to(ROOT))
        requests = (
            f"Create the new project file {relative} containing exactly alpha followed by a newline. "
            "Use your tools to write and verify it now. This is an authorized file-write test, not a calendar request.",
            f"Append exactly beta followed by a newline to {relative}, preserving the existing content. "
            "Use your tools and verify the result. This is an authorized file-write test, not a calendar request.",
        )
        for request, expected in zip(requests, ("alpha\n", "alpha\nbeta\n")):
            result = think(env, "", request, [], True)
            if result["action"] != "none" or not path.is_file() or path.read_text() != expected:
                audit("agent file-write verification failed; requested file contents not verified")
                print("FAIL: worker did not produce the requested file. No Telegram message was sent.")
                print(f"Calendar action: {result['action']}; file present: {path.is_file()}; "
                      f"expected content verified: {path.is_file() and path.read_text() == expected}")
                return 1
            audit("agent file-write verification step completed; file contents verified")
    audit("agent file-write verification completed; temporary files removed; no Telegram messages sent")
    print("PASS: " + backends.LABELS[backends.backend(env)] + " created, verified and appended the file across fresh invocations.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        audit("agent file-write verification failed; private details suppressed")
        print("FAIL: worker verification could not complete; private details suppressed.")
        raise SystemExit(1)
