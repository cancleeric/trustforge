#!/usr/bin/env bash
# Install bounded Hermes prefetch/replay diagnostics on an EC2 host.
set -euo pipefail

APP_DIR="${TRUSTFORGE_APP_DIR:-/opt/trustforge}"
REGION="${REGION:-ap-southeast-2}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"

install -m 0644 /dev/stdin "$UNIT_DIR/hermes-cycle.service" <<UNIT
[Unit]
Description=TrustForge Hermes bounded autonomous cycle
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$APP_DIR
Environment=TRUSTFORGE_HOME=$APP_DIR
Environment=AWS_REGION=$REGION
Environment=PYTHONPATH=$APP_DIR
Environment=CACHE_BACKEND=dynamodb
Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache
Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger
Environment=COST_LEDGER_BACKEND=dynamodb
Environment=SCHEDULER_RUN_LOG_BACKEND=dynamodb
Environment=TRUSTFORGE_SCHEDULER_RUN_TABLE=trustforge-scheduler-runs
Environment=TRUSTFORGE_SKILL_ROOT=$APP_DIR/skills/hermes
ExecStart=/usr/bin/python3 scripts/hermes_cycle.py --max-budget-sec 900
UNIT

install -m 0644 /dev/stdin "$UNIT_DIR/hermes-cycle.timer" <<'UNIT'
[Unit]
Description=Run TrustForge Hermes prefetch cycle every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true
RandomizedDelaySec=20s

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now hermes-cycle.timer
bash "$APP_DIR/deploy/install_fetch_scheduler.sh"
bash "$APP_DIR/deploy/prepare_backend_deploy_backup.sh"
echo "Hermes timer installed and active. Inspect with: systemctl list-timers hermes-cycle.timer"
