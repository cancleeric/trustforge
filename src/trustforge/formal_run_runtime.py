"""Runtime composition for the formal-run shared authority.

Production is deliberately DynamoDB-only.  SQLite exists solely as an explicit
single-host development/test adapter and is never a production fallback.
"""

from __future__ import annotations

import os
import hashlib
from decimal import Decimal
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from botocore.exceptions import BotoCoreError

from .formal_run_idempotency import FormalRunIdempotencyStore, IdempotencyUnavailable
from .formal_run_idempotency_dynamodb import DynamoDbFormalRunIdempotencyStore
from .formal_run_idempotency_sqlite import SqliteFormalRunIdempotencyStore
from .runtime_control import is_production_environment


def formal_run_store(
    *,
    environment: str | None = None,
    sqlite_path: str | Path | None = None,
) -> FormalRunIdempotencyStore:
    """Build the authority selected by the trusted runtime environment."""
    resolved_environment = (
        environment if environment is not None else os.getenv("TRUSTFORGE_ENV", "")
    ).strip().lower()
    production = is_production_environment() or resolved_environment in {
        "prod",
        "production",
    }
    if production:
        table = os.getenv("TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE", "").strip()
        region = (
            os.getenv("AWS_REGION", "").strip()
            or os.getenv("AWS_DEFAULT_REGION", "").strip()
        )
        if not table or not region:
            raise IdempotencyUnavailable(
                "production formal-run DynamoDB authority is not configured"
            )
        try:
            import boto3

            client = boto3.client("dynamodb", region_name=region)
            return DynamoDbFormalRunIdempotencyStore(client, table_name=table)
        except (BotoCoreError, ImportError, RuntimeError, ValueError) as exc:
            raise IdempotencyUnavailable(
                "production formal-run DynamoDB authority is unavailable"
            ) from exc

    if resolved_environment not in {"test", "development"}:
        raise IdempotencyUnavailable(
            "formal-run environment must be explicitly test or development"
        )
    configured_path = (
        str(sqlite_path)
        if sqlite_path is not None
        else os.getenv("TRUSTFORGE_FORMAL_RUN_SQLITE_PATH", "").strip()
    )
    if not configured_path:
        raise IdempotencyUnavailable(
            "development formal-run SQLite authority path is not configured"
        )
    if configured_path != ":memory:" and not Path(configured_path).is_absolute():
        raise IdempotencyUnavailable(
            "development formal-run SQLite authority path must be absolute"
        )
    return SqliteFormalRunIdempotencyStore(
        configured_path, environment=resolved_environment
    )


def _secret(name: str) -> bytes:
    value = os.getenv(name, "").encode("utf-8")
    if len(value) < 32:
        raise IdempotencyUnavailable(f"{name} must contain at least 32 bytes")
    return value


def _formal_budget_authority(environment: str):
    from .formal_budget_reservation import (
        DynamoDbFormalBudgetAuthority,
        SqliteFormalBudgetAuthority,
    )

    if is_production_environment() or environment in {"prod", "production"}:
        table = os.getenv("TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE", "").strip()
        region = (
            os.getenv("AWS_REGION", "").strip()
            or os.getenv("AWS_DEFAULT_REGION", "").strip()
        )
        if not table or not region:
            raise IdempotencyUnavailable("formal budget authority unavailable")
        try:
            import boto3

            return DynamoDbFormalBudgetAuthority(
                boto3.client("dynamodb", region_name=region), table_name=table
            )
        except Exception as exc:
            raise IdempotencyUnavailable("formal budget authority unavailable") from exc
    budget_path = os.getenv("TRUSTFORGE_FORMAL_BUDGET_SQLITE_PATH", "").strip()
    if not budget_path or not Path(budget_path).is_absolute():
        raise IdempotencyUnavailable("formal budget authority path is unavailable")
    return SqliteFormalBudgetAuthority(budget_path, environment=environment)


@lru_cache(maxsize=1)
def formal_run_coordinator():
    """Compose the HTTP coordinator with a tokenized budget authority."""
    from . import budget_guard
    from .analysis_flow import AnalysisFlow
    from .formal_run_coordinator import FormalRunCoordinator, FormalRunSecrets

    environment = os.getenv("TRUSTFORGE_ENV", "").strip().lower()
    store = formal_run_store(environment=environment)
    flow_path = os.getenv("TRUSTFORGE_SHARED_ANALYSIS_DB_PATH", "").strip()
    if not flow_path or not Path(flow_path).is_absolute():
        raise IdempotencyUnavailable("shared analysis projection path is unavailable")
    flow = AnalysisFlow(flow_path)
    budget = _formal_budget_authority(environment)

    def reserve(operation_id: str):
        return budget.reserve(
            reservation_id="br_" + hashlib.sha256(operation_id.encode()).hexdigest()[:32],
            # Formal settled spend lives inside the token authority. Feed only
            # legacy/non-formal ledger spend so both paths share one cap
            # without counting formal receipts twice.
            spent=Decimal(str(budget_guard.daily_nonformal_cost_usd())),
            cost=Decimal(str(budget_guard.request_max_cost_usd())),
            cap=Decimal(str(budget_guard.daily_cap_usd())),
            now=datetime.now(timezone.utc),
        )

    return FormalRunCoordinator(
        store=store,
        flow=flow,
        secrets=FormalRunSecrets(
            caller_secret=_secret("TRUSTFORGE_FORMAL_CALLER_SECRET"),
            caller_key_id=os.getenv("TRUSTFORGE_FORMAL_CALLER_KEY_ID", "caller-v1"),
            idempotency_secret=_secret("TRUSTFORGE_FORMAL_IDEMPOTENCY_SECRET"),
            idempotency_key_id=os.getenv("TRUSTFORGE_FORMAL_IDEMPOTENCY_KEY_ID", "key-v1"),
            retention_locator_secret=_secret("TRUSTFORGE_FORMAL_RETENTION_SECRET"),
            fingerprint_secret=_secret("TRUSTFORGE_FORMAL_FINGERPRINT_SECRET"),
            fingerprint_key_id=os.getenv(
                "TRUSTFORGE_FORMAL_FINGERPRINT_KEY_ID", "fingerprint-v1"
            ),
            content_secret=_secret("TRUSTFORGE_FORMAL_CONTENT_SECRET"),
            content_key_id=os.getenv("TRUSTFORGE_FORMAL_CONTENT_KEY_ID", "content-v1"),
        ),
        reserve_budget=reserve,
        release_budget=lambda token: budget.release(token),
    )


def formal_run_worker(flow):
    """Compose the daemon worker against the same durable authorities as HTTP."""
    from .formal_run_worker import FormalRunWorker

    environment = os.getenv("TRUSTFORGE_ENV", "").strip().lower()
    store = formal_run_store(environment=environment)
    budget = _formal_budget_authority(environment)
    return FormalRunWorker(
        store=store,
        queue=flow,
        execute=flow.dispatch_formal_projection,
        reconcile=flow.reconcile_formal_projection,
        settle_budget=budget.settle,
        budget_authority=budget,
    )
