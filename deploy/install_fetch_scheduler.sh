#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${TRUSTFORGE_APP_DIR:-/opt/trustforge}"
REGION="${REGION:-ap-southeast-2}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"

install -m 0644 /dev/stdin "$UNIT_DIR/fetch-scheduler.service" <<UNIT
[Unit]
Description=TrustForge connector cache fetch scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$APP_DIR
Environment=AWS_REGION=$REGION
Environment=PYTHONPATH=$APP_DIR
Environment=CACHE_BACKEND=dynamodb
Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache
Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger
Environment=COST_LEDGER_BACKEND=dynamodb
Environment=SCHEDULER_RUN_LOG_BACKEND=dynamodb
Environment=TRUSTFORGE_SCHEDULER_RUN_TABLE=trustforge-scheduler-runs
ExecStartPre=/usr/bin/python3.11 scripts/fetch_scheduler.py --probe
ExecStart=/usr/bin/python3.11 scripts/fetch_scheduler.py --allow-partial
UNIT

install -m 0644 /dev/stdin "$UNIT_DIR/fetch-scheduler.timer" <<'UNIT'
[Unit]
Description=Run TrustForge fetch scheduler periodically

[Timer]
OnBootSec=1min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now fetch-scheduler.timer
