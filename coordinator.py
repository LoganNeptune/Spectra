#!/usr/bin/env python3
"""Stable entry point for tmux and scheduled check-ins."""
from app.coordinator import audit, main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        audit("coordinator startup failed; private details suppressed")
        print("Coordinator could not start. Check configuration, timezone, file permissions and bot webhook settings.")
        raise SystemExit(1)
