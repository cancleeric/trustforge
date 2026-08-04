#!/usr/bin/env bash
# Install bounded Hermes prefetch/replay diagnostics on an EC2 host.
set -euo pipefail

APP_DIR="${TRUSTFORGE_APP_DIR:-/opt/trustforge}"
REGION="${REGION:-ap-southeast-2}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
PRIMARY_UNIT="$UNIT_DIR/trustforge.service"
SKILL_LOG_PATH="${TRUSTFORGE_SKILL_CHANGE_LOG:-/var/lib/trustforge/skill_changes.jsonl}"
BEDROCK_RPS_BACKEND="${TRUSTFORGE_BEDROCK_RPS_BACKEND:-dynamodb}"
BEDROCK_RPS_REGION="${TRUSTFORGE_BEDROCK_RPS_REGION:-us-east-1}"
BEDROCK_RPS_TABLE="${TRUSTFORGE_BEDROCK_RPS_TABLE:-competition-trustforge-team11-budget}"
FORMAL_RUN_TABLE="${TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE:-trustforge-formal-run}"
TOKEN_SSM_PREFIX="${TRUSTFORGE_TOKEN_SSM_PREFIX:-/trustforge/runtime}"
SHARED_ANALYSIS_DB_PATH="${TRUSTFORGE_SHARED_ANALYSIS_DB_PATH:-/var/lib/trustforge/analysis.sqlite3}"

if [[ "$BEDROCK_RPS_BACKEND" != "dynamodb" ]] ||
   ! [[ "$BEDROCK_RPS_REGION" =~ ^[a-z]{2}(-gov)?-[a-z]+-[0-9]+$ ]] ||
   ! [[ "$BEDROCK_RPS_TABLE" =~ ^[A-Za-z0-9_.-]{3,255}$ ]]; then
  echo "invalid canonical Bedrock RPS gate configuration" >&2
  exit 2
fi
if ! [[ "$FORMAL_RUN_TABLE" =~ ^[A-Za-z0-9_.-]{3,255}$ ]] ||
   ! [[ "$TOKEN_SSM_PREFIX" =~ ^/[A-Za-z0-9_./-]+$ ]] ||
   ! [[ "$SHARED_ANALYSIS_DB_PATH" =~ ^/[A-Za-z0-9_./-]+$ ]]; then
  echo "invalid formal-run production configuration" >&2
  exit 2
fi

MODEL="${BEDROCK_MODEL_ID:-}"
if [[ -z "$MODEL" && -f "$PRIMARY_UNIT" ]]; then
  MODEL="$(sed -n 's/^Environment=BEDROCK_MODEL_ID=//p' \
    "$PRIMARY_UNIT" | tail -n 1)"
fi
if [[ -n "$MODEL" && ! "$MODEL" =~ ^[A-Za-z0-9._:-]+$ ]]; then
  echo "invalid BEDROCK_MODEL_ID in primary service contract" >&2
  exit 2
fi

if ! [[ "$SKILL_LOG_PATH" =~ ^/[A-Za-z0-9._/-]+$ ]] || [[ "$SKILL_LOG_PATH" == *"/../"* ]] || [[ "$SKILL_LOG_PATH" == */.. ]]; then
  echo "TRUSTFORGE_SKILL_CHANGE_LOG must be an absolute safe path" >&2
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
Environment=TRUSTFORGE_BEDROCK_RPS_BACKEND=$BEDROCK_RPS_BACKEND
Environment=TRUSTFORGE_BEDROCK_RPS_REGION=$BEDROCK_RPS_REGION
Environment=TRUSTFORGE_BEDROCK_RPS_TABLE=$BEDROCK_RPS_TABLE
Environment=TRUSTFORGE_ENV=production
Environment=TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE=$FORMAL_RUN_TABLE
Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=$TOKEN_SSM_PREFIX
Environment=TRUSTFORGE_SHARED_ANALYSIS_DB_PATH=$SHARED_ANALYSIS_DB_PATH
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
Environment=TRUSTFORGE_BEDROCK_RPS_BACKEND=$BEDROCK_RPS_BACKEND
Environment=TRUSTFORGE_BEDROCK_RPS_REGION=$BEDROCK_RPS_REGION
Environment=TRUSTFORGE_BEDROCK_RPS_TABLE=$BEDROCK_RPS_TABLE
Environment=TRUSTFORGE_ENV=production
Environment=TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE=$FORMAL_RUN_TABLE
Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=$TOKEN_SSM_PREFIX
Environment=TRUSTFORGE_SHARED_ANALYSIS_DB_PATH=$SHARED_ANALYSIS_DB_PATH
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

TRUSTFORGE_SKILL_CHANGE_LOG="$SKILL_LOG_PATH" bash "$APP_DIR/deploy/reconcile_skill_change_log.sh"
systemctl daemon-reload
systemctl enable --now hermes-cycle.timer
systemctl enable --now trustforge-analysis-flow.service
bash "$APP_DIR/deploy/install_fetch_scheduler.sh"
bash "$APP_DIR/deploy/prepare_backend_deploy_backup.sh"
echo "Hermes timer installed. Autonomy is disabled by default in production; enable via admin config or TRUSTFORGE_HERMES_AUTONOMY_ENABLED=1. Inspect with: systemctl list-timers hermes-cycle.timer"
