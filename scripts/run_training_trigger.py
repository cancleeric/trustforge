#!/usr/bin/env python3
"""Cron-friendly entrypoint for TrustForge scheduled training triggers."""

from trustforge.training_trigger import main


if __name__ == "__main__":
    raise SystemExit(main())
