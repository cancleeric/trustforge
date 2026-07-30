#!/usr/bin/env bash
# Install bounded Hermes prefetch/replay diagnostics on an EC2 host.
set -euo pipefail

APP_DIR="${TRUSTFORGE_APP_DIR:-/opt/trustforge}"
REGION="${REGION:-ap-southeast-2}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
PRIMARY_UNIT="$UNIT_DIR/trustforge.service"

MODEL="${BEDROCK_MODEL_ID:-}"
if [[ -z "$MODEL" && -f "$PRIMARY_UNIT" ]]; then
  MODEL="$(sed -n 's/^Environment=BEDROCK_MODEL_ID=//p' \
    "$PRIMARY_UNIT" | tail -n 1)"
fi
if [[ -n "$MODEL" && ! "$MODEL" =~ ^[A-Za-z0-9._:-]+$ ]]; then
  echo "invalid BEDROCK_MODEL_ID in primary service contract" >&2
  exit 2
fi

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
Environment=BEDROCK_MODEL_ID=$MODEL
Environment=PYTHONPATH=$APP_DIR
Environment=CACHE_BACKEND=dynamodb
Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache
Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger
Environment=COST_LEDGER_BACKEND=dynamodb
Environment=SCHEDULER_RUN_LOG_BACKEND=dynamodb
Environment=TRUSTFORGE_SCHEDULER_RUN_TABLE=trustforge-scheduler-runs
Environment=TRUSTFORGE_SKILL_ROOT=$APP_DIR/skills/hermes
Environment=TRUSTFORGE_HERMES_AUTONOMY_ENABLED=0
ExecStart=/usr/bin/python3.11 scripts/hermes_cycle.py --max-budget-sec 900
UNIT

install -m 0644 /dev/stdin "$UNIT_DIR/hermes-cycle.timer" <<'UNIT'
[Unit]
Description=Run TrustForge Hermes preflight cycle every 30 minutes when enabled

[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
Persistent=true
RandomizedDelaySec=20s

[Install]
WantedBy=timers.target
UNIT

# The bounded 30-minute Hermes cycle creates only low-priority scheduled work.
# Manual analysis uses the durable analysis-flow queue and therefore needs a
# separate, always-on consumer.  Without this unit, production can accept a
# manual job but leave it queued forever until an unrelated local process runs.
install -m 0644 /dev/stdin "$UNIT_DIR/trustforge-analysis-flow.service" <<UNIT
[Unit]
Description=TrustForge durable manual analysis-flow worker
After=network-online.target trustforge.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment=TRUSTFORGE_HOME=$APP_DIR
Environment=AWS_REGION=$REGION
Environment=BEDROCK_MODEL_ID=$MODEL
Environment=PYTHONPATH=$APP_DIR
Environment=CACHE_BACKEND=dynamodb
Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache
Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger
Environment=COST_LEDGER_BACKEND=dynamodb
Environment=SCHEDULER_RUN_LOG_BACKEND=dynamodb
Environment=TRUSTFORGE_SCHEDULER_RUN_TABLE=trustforge-scheduler-runs
Environment=TRUSTFORGE_SKILL_ROOT=$APP_DIR/skills/hermes
ExecStart=/usr/bin/python3.11 scripts/run_analysis_flow.py --daemon --workers-per-stage 2 --poll-seconds 2 --schedule-seconds 1800
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now hermes-cycle.timer
systemctl enable --now trustforge-analysis-flow.service
bash "$APP_DIR/deploy/install_fetch_scheduler.sh"
bash "$APP_DIR/deploy/prepare_backend_deploy_backup.sh"
echo "Hermes timer installed. Autonomy is disabled by default in production; enable via admin config or TRUSTFORGE_HERMES_AUTONOMY_ENABLED=1. Inspect with: systemctl list-timers hermes-cycle.timer"
