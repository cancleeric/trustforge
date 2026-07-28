"""Durable, snapshot-isolated Hermes pre-analysis pipeline.

The browser never starts work here.  A data refresh creates one immutable document
snapshot, enqueues the complete coin/mode matrix, and five independent workers move
packages through the real TrustForge functions.  SQLite contains the observable
queue/checkpoint/result state; in-memory objects only exist while a package is in a
worker.  Interrupted packages are safely restarted from their immutable snapshot.
"""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import itertools
import json
import logging
import math
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import budget_guard
from .agent.narrative_locale import DEFAULT_LOCALE as DEFAULT_NARRATIVE_LOCALE
from .agent.narrative_locale import normalize_locale
from .agent.orchestrator import build_report
from .bedrock import BedrockClient
from .execlog import ExecutionLog
from .ingestion.base import collect
from .ingestion.cache import doc_from_dict, doc_to_dict
from .ledger import append_run
from .schema import COIN_POOL, QuestionType, iso_utc
from .trust.scoring import build_stance_fn
from .feature_store import TrustFeatureStore

STAGES = ("source_ingestion", "claim_extraction", "trust_reasoning", "evidence_assembly", "report_delivery")

# Issue N16: a `state='running'` job whose checkpoint stops advancing for this
# long is presumed orphaned (owning process crashed, or its worker thread is
# hung on a blocking call — `Thread.is_alive()` in `reconcile_runtime()` can't
# see a hang, only a dead thread). 600s (10 minutes) is chosen because it is
# comfortably longer than any observed single-stage duration (Bedrock calls in
# `_stage_claim_extraction`/`_stage_trust_reasoning` are the slowest steps and
# normally complete in well under a minute) while still being short enough
# that an operator sees recovery within one incident window rather than a job
# sitting stuck indefinitely. It is also well above the 30s max retry backoff
# used elsewhere in this module, so it never races an in-flight retry.
STALE_RUNNING_JOB_THRESHOLD_SECONDS = 600
MODES: dict[str, tuple[QuestionType, str]] = {
    "risk": (QuestionType.MULTI_SOURCE, "評估{coin}整體信任狀態，並標記任何正在形成的操縱風險。"),
    "sentiment": (QuestionType.MULTI_SOURCE, "分析{coin}市場情緒、分歧與反方訊號。"),
    "fundamentals": (QuestionType.HYPOTHESIS, "檢驗{coin}基本面與目前市場敘事是否獲得證據支持。"),
    "news": (QuestionType.MULTI_SOURCE, "整理{coin}最新事件，區分事實、推論與未證實主張。"),
    "catalyst": (QuestionType.HYPOTHESIS, "檢驗{coin}近期催化因素是否足以改變現有判斷。"),
}
QUESTION_TYPES = {**{mode: item[0] for mode, item in MODES.items()}, "comparison": QuestionType.COMPARISON}
QUEUE_CAPACITY = 500


class MultiAngleCapacityError(RuntimeError):
    """五角度工作無法原子入列。"""


class MultiAngleBudgetError(RuntimeError):
    """五角度執行的保守成本預檢未通過。"""
MANUAL_PRIORITY = 0
SCHEDULED_PRIORITY = 100
MANUAL_DEDUP_WINDOW_SEC = 300
MANUAL_LOCK_TIMEOUT_SEC = 90


@contextmanager
def _manual_dedup_lock(db_path: Path, canonical_key: str):
    lock_dir = db_path.parent / ".manual-locks"
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    digest = hashlib.sha256(canonical_key.encode()).hexdigest()
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    dir_fd = os.open(lock_dir, dir_flags)
    fd = -1
    try:
        directory = os.fstat(dir_fd)
        if directory.st_uid != os.getuid():
            raise PermissionError("manual analysis lock directory has an unexpected owner")
        os.fchmod(dir_fd, 0o700)
        file_flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(f"{digest}.lock", file_flags, 0o600, dir_fd=dir_fd)
        lock_file = os.fstat(fd)
        if lock_file.st_uid != os.getuid():
            raise PermissionError("manual analysis lock file has an unexpected owner")
        os.fchmod(fd, 0o600)
        deadline = time.monotonic() + MANUAL_LOCK_TIMEOUT_SEC
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("manual analysis deduplication lock timed out")
                time.sleep(0.05)
        yield
    finally:
        if fd >= 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        os.close(dir_fd)


def _question_terms(value: str) -> set[str]:
    """Language-agnostic retrieval features for mixed Chinese/English questions."""
    normalized = re.sub(r"\s+", "", value.casefold())
    words = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    han = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    return words | {han[index:index + 2] for index in range(max(0, len(han) - 1))}


def _question_similarity(left: str, right: str) -> float:
    a, b = _question_terms(left), _question_terms(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _db_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else Path(__file__).resolve().parents[2] / "out" / "trustforge.sqlite3"


@contextmanager
def _bedrock_live_attempt(log: ExecutionLog, *, prepaid: bool = False):
    """Yield 是否本次真的允許呼叫 Bedrock（fail-closed；任何判定例外一律離線）。

    複用既有閘邏輯，不另造繞過 `budget_guard`／每日 cap 的新路徑（`STAGES`
    daemon 管線跟公開 `/api/analyze` 共用同一套安全護欄）：
    - `web._bedrock_allowed()`：live 總閘（env `BEDROCK_MODEL_ID` AND admin
      config `bedrock_enabled`，皆 fail-closed，見 `web.py` 該函式 docstring）。
      Lazy import（同檔案 `_stage_report_delivery` 既有慣例）避免頂層循環匯入。
    - `budget_guard.daily_cap_exceeded()` / `narrative_model_priced()` /
      `try_reserve_request_budget()`：每日 $ cap、unpriced model 保護、
      並行 TOCTOU 原子預留，跟 `pipeline.run()` 放行真 Bedrock 前的檢查
      （`pipeline.py` 220-299 行）邏輯一致。

    離開時（無論本次呼叫成功/失敗/未真的呼叫），順序固定「先記帳、後放預留」
    （codex + harper CISO 雙審 HIGH，比照 `orchestrator.py:1633` 先記帳、
    `pipeline.py:369` 外層 finally 後才釋放的既有安全序；先前版本順序反了，
    `release_request_budget()` 先跑會讓並發 daemon job 在這筆花費「還沒進
    帳本」的空窗期呼叫 `try_reserve_request_budget()`，讀到偏低的
    `daily_cost_usd()` 誤判還有額度，繞過 $/天 cap）：
    - `AnalysisFlow` 走 `agent.orchestrator.build_report()` 直接組報告，不經過
      `run_agent_pipeline()` 收尾既有的 `ledger.append_run()` 記帳——若不在
      這裡補記，daemon 這條管線的真花費永遠進不了帳本，`daily_cost_usd()`
      看不到它，每日 cap 對這條管線形同虛設。這裡把本次呼叫期間新增的
      `llm.cost` log 事件彙總，比照 `run_agent_pipeline()` 收尾同一套格式
      寫回帳本；持久化失敗（含 `append_run` 本身丟例外）同樣落
      `budget_guard.record_unledgered_spend()`（fail-closed，避免帳本故障
      期間被重複呼叫無限繞過 cap）。`total_cost_usd` 在 `append_run` 的
      try 之外先算好，確保即使 `append_run` 呼叫本身丟例外，仍握有這個值
      可以落 unledgered fallback，不會發生「兩頭都沒記」。
    - 記帳完成（成功或已落 fallback）後，才釋放本次預留（若有）——包在
      巢狀 `finally` 保證即使記帳邏輯本身出非預期例外，release 仍必定
      執行，不讓預留卡死漏放。
    """
    reservation: float | None = None
    live = False
    try:
        from .web import _bedrock_allowed  # noqa: PLC0415 — 避免頂層循環匯入

        if (
            _bedrock_allowed()
            and not budget_guard.daily_cap_exceeded()
            and budget_guard.narrative_model_priced()
        ):
            if prepaid:
                live = True
            else:
                reservation = budget_guard.try_reserve_request_budget()
                live = reservation is not None
    except Exception:
        logging.getLogger(__name__).warning(
            "analysis_flow: bedrock live 閘判定失敗，fail-closed 強制本次離線",
            exc_info=True,
        )
        live, reservation = False, None

    start_idx = len(log.events)
    try:
        yield live
    finally:
        try:
            # `total_cost_usd` 故意算在 `append_run` 的 try 之外/更前
            # （CISO 對抗審 LOW）：即使下面 `append_run` 呼叫本身丟例外，
            # 這裡仍握有算好的金額可以落 `record_unledgered_spend` 的保守
            # 估計，不會發生「記帳跟 unledgered fallback 兩頭都沒記」。
            new_calls = [
                {
                    "model": event["params"].get("model"),
                    "tokens_in": event["params"].get("tokens_in", 0),
                    "tokens_out": event["params"].get("tokens_out", 0),
                    "cost_usd": event["params"].get("cost_usd", 0.0),
                }
                for event in log.events[start_idx:]
                if event.get("tool") == "llm.cost"
            ]
            total_cost_usd = round(sum(float(c["cost_usd"] or 0.0) for c in new_calls), 6)
            if total_cost_usd > 0:
                try:
                    persisted = append_run({
                        "ts": iso_utc(time.time()),
                        "question_type": "analysis_flow",
                        "coin": None,
                        "offline": not live,
                        "calls": new_calls,
                        "total_cost_usd": total_cost_usd,
                    })
                except Exception:
                    persisted = False
                    logging.getLogger(__name__).warning(
                        "analysis_flow: append_run 記帳例外，落 record_unledgered_spend 保守估計",
                        exc_info=True,
                    )
                if not persisted:
                    budget_guard.record_unledgered_spend(total_cost_usd)
        except Exception:
            logging.getLogger(__name__).warning(
                "analysis_flow: bedrock 花費記帳失敗（cap 帳本可能少計這筆）", exc_info=True,
            )
        finally:
            # codex + harper CISO 雙審 HIGH：記帳必須先於釋放預留完成——見
            # docstring。巢狀 finally 保證無論上面記帳成功/丟例外，release
            # 一定執行（不因記帳例外漏放預留），但文字順序＋巢狀結構確保
            # release 永遠在記帳嘗試「之後」才生效，關掉並發 job 讀到偏低
            # `daily_cost_usd()`、誤判有額度繞過 cap 的 TOCTOU 空窗。
            if reservation is not None:
                try:
                    budget_guard.release_request_budget(reservation)
                except Exception:
                    logging.getLogger(__name__).warning(
                        "analysis_flow: release_request_budget 失敗", exc_info=True,
                    )


# Issue #570: three-track learning emission hooks. These wrappers live at
# module scope so the ``AnalysisFlow._worker`` call sites stay narrow and the
# import of :mod:`trustforge.three_track_wiring` only happens when emission
# is actually enabled (lazy). Each wrapper is structurally fail-soft: even if
# the underlying helper raises unexpectedly, the analysis path is unaffected.
def _emit_three_track_learning_on_success(flow: "AnalysisFlow", job_id: str) -> None:
    try:
        from .three_track_wiring import emit_for_completed_job
        emit_for_completed_job(flow, job_id)
    except Exception:
        logging.getLogger(__name__).exception(
            "three-track learning SUCCESS emission failed (fail-soft) "
            "for job_id=%s", job_id,
        )


def _emit_three_track_learning_on_failure(
    flow: "AnalysisFlow", job_id: str, error: BaseException,
) -> None:
    try:
        from .three_track_wiring import emit_for_failed_job
        emit_for_failed_job(flow, job_id, error=error)
    except Exception:
        logging.getLogger(__name__).exception(
            "three-track learning FAILURE emission failed (fail-soft) "
            "for job_id=%s", job_id,
        )


class AnalysisFlow:
    def __init__(self, path: str | Path | None = None, *, workers_per_stage: int = 1,
                 readonly: bool = False):
        self.path = _db_path(path)
        self.readonly = readonly
        if not readonly:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.workers_per_stage = max(1, workers_per_stage)
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        # Every stage boundary is a safe handoff point. Priority queues preserve
        # manual priority through the complete flow without interrupting work
        # that is already executing.
        self._queues = {stage: queue.PriorityQueue() for stage in STAGES}
        self._queue_sequence = itertools.count()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._timers: list[threading.Timer] = []
        self._adopted: set[str] = set()
        if not readonly:
            self._init_schema()

    def _readonly_store_missing(self) -> bool:
        return self.readonly and not self.path.exists()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            target: str | Path = self.path
            kwargs: dict[str, Any] = {}
            if self.readonly:
                target = f"file:{self.path}?mode=ro"
                kwargs["uri"] = True
            conn = sqlite3.connect(
                target, timeout=2 if self.readonly else 10,
                isolation_level=None, check_same_thread=False, **kwargs,
            )
            conn.row_factory = sqlite3.Row
            if not self.readonly:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={2000 if self.readonly else 10000}")
            self._local.conn = conn
            with self._connections_lock: self._connections.append(conn)
        return conn

    def _init_schema(self) -> None:
        conn = self._conn()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS analysis_snapshots (
          snapshot_id TEXT PRIMARY KEY, coin TEXT NOT NULL, created_at REAL NOT NULL,
          source_revision TEXT NOT NULL, docs_json TEXT NOT NULL, document_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS analysis_jobs (
          job_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, coin TEXT NOT NULL,
          mode TEXT NOT NULL, question TEXT NOT NULL, question_type TEXT NOT NULL,
          state TEXT NOT NULL, current_stage TEXT NOT NULL, retry_count INTEGER NOT NULL DEFAULT 0,
          error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
          UNIQUE(snapshot_id, coin, mode, question)
        );
        CREATE TABLE IF NOT EXISTS analysis_stage_runs (
          job_id TEXT NOT NULL, stage TEXT NOT NULL, state TEXT NOT NULL,
          queue_entered_at REAL NOT NULL, started_at REAL, finished_at REAL,
          duration_sec REAL, event_count INTEGER NOT NULL DEFAULT 0,
          retry_count INTEGER NOT NULL DEFAULT 0, error TEXT,
          PRIMARY KEY(job_id, stage)
        );
        CREATE TABLE IF NOT EXISTS analysis_results (
          result_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, snapshot_id TEXT NOT NULL,
          coin TEXT NOT NULL, mode TEXT NOT NULL, question TEXT NOT NULL,
          payload_json TEXT NOT NULL, published_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS analysis_synthesis_claims (
          snapshot_id TEXT NOT NULL, coin TEXT NOT NULL, claimed_at REAL NOT NULL,
          PRIMARY KEY(snapshot_id, coin)
        );
        CREATE TABLE IF NOT EXISTS analysis_multi_angle_runs (
          snapshot_id TEXT NOT NULL, coin TEXT NOT NULL, submitted_at REAL NOT NULL,
          reserved_usd REAL NOT NULL DEFAULT 0,
          PRIMARY KEY(snapshot_id, coin)
        );
        CREATE INDEX IF NOT EXISTS idx_analysis_results_lookup
          ON analysis_results(coin, mode, question, published_at DESC);
        CREATE TABLE IF NOT EXISTS analysis_questions (
          question_id TEXT PRIMARY KEY, coin TEXT NOT NULL, mode TEXT NOT NULL,
          question TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
          created_at REAL NOT NULL, updated_at REAL NOT NULL,
          UNIQUE(coin, mode, question)
        );
        CREATE TABLE IF NOT EXISTS analysis_stage_attempts (
          attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage TEXT NOT NULL,
          attempt INTEGER NOT NULL, state TEXT NOT NULL, started_at REAL NOT NULL,
          finished_at REAL NOT NULL, duration_sec REAL NOT NULL,
          retryable INTEGER NOT NULL, error TEXT
        );
        CREATE TABLE IF NOT EXISTS analysis_dead_letters (
          job_id TEXT PRIMARY KEY, stage TEXT NOT NULL, coin TEXT NOT NULL,
          mode TEXT NOT NULL, question TEXT NOT NULL, snapshot_id TEXT NOT NULL,
          attempts INTEGER NOT NULL, error TEXT NOT NULL, failed_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS analysis_retry_queue (
          job_id TEXT NOT NULL, stage TEXT NOT NULL, next_retry_at REAL NOT NULL,
          attempt INTEGER NOT NULL, error TEXT NOT NULL,
          PRIMARY KEY(job_id, stage)
        );
        CREATE TABLE IF NOT EXISTS analysis_conversation (
          message_id TEXT PRIMARY KEY, coin TEXT NOT NULL, mode TEXT NOT NULL,
          role TEXT NOT NULL, content TEXT NOT NULL, question_id TEXT,
          job_id TEXT, snapshot_id TEXT, created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_analysis_conversation_lookup
          ON analysis_conversation(coin, mode, created_at DESC);
        CREATE TABLE IF NOT EXISTS analysis_lineage_events (
          event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
          snapshot_id TEXT, job_id TEXT, stage TEXT,
          entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
          parent_type TEXT, parent_id TEXT, metadata_json TEXT NOT NULL,
          created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_analysis_lineage_job
          ON analysis_lineage_events(job_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_analysis_lineage_snapshot
          ON analysis_lineage_events(snapshot_id, created_at);
        CREATE TRIGGER IF NOT EXISTS analysis_lineage_no_update
          BEFORE UPDATE ON analysis_lineage_events BEGIN
            SELECT RAISE(ABORT, 'analysis_lineage_events is append-only');
          END;
        CREATE TRIGGER IF NOT EXISTS analysis_lineage_no_delete
          BEFORE DELETE ON analysis_lineage_events BEGIN
            SELECT RAISE(ABORT, 'analysis_lineage_events is append-only');
          END;
        """)
        # SQLite deployments created before manual priority support need an
        # additive migration.  Defaults preserve the previous scheduled-job
        # semantics for every existing row.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(analysis_jobs)").fetchall()}
        if "origin" not in columns:
            conn.execute("ALTER TABLE analysis_jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'scheduled'")
        if "priority" not in columns:
            conn.execute(f"ALTER TABLE analysis_jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT {SCHEDULED_PRIORITY}")
        run_columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(analysis_multi_angle_runs)"
        ).fetchall()}
        if "reserved_usd" not in run_columns:
            conn.execute(
                "ALTER TABLE analysis_multi_angle_runs "
                "ADD COLUMN reserved_usd REAL NOT NULL DEFAULT 0"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analysis_jobs_priority ON analysis_jobs(state,priority,created_at)")
        TrustFeatureStore.ensure_schema(conn)
        # Backfill the dialogue surface for databases created before conversation
        # memory existed. Deterministic IDs make this migration restart-safe.
        conn.execute("""
          INSERT OR IGNORE INTO analysis_conversation
          SELECT 'message-seed-' || question_id,coin,mode,'user',question,question_id,NULL,NULL,created_at
          FROM analysis_questions
        """)
        for row in conn.execute(
            "SELECT job_id,coin,mode,snapshot_id,payload_json,published_at FROM analysis_results",
        ).fetchall():
            try:
                content = json.loads(row["payload_json"]).get("report", {}).get("market_judgment") or "分析完成"
            except (TypeError, json.JSONDecodeError):
                content = "分析完成"
            conn.execute(
                "INSERT OR IGNORE INTO analysis_conversation VALUES(?,?,?,?,?,?,?,?,?)",
                (f"message-{row['job_id']}", row["coin"], row["mode"], "hermes", content,
                 None, row["job_id"], row["snapshot_id"], row["published_at"]),
            )

    def _append_lineage(
        self, event_type: str, *, entity_type: str, entity_id: str,
        snapshot_id: str | None = None, job_id: str | None = None,
        stage: str | None = None, parent_type: str | None = None,
        parent_id: str | None = None, metadata: dict[str, Any] | None = None,
    ) -> str:
        event_id = f"lineage-{uuid.uuid4().hex[:20]}"
        self._conn().execute(
            "INSERT INTO analysis_lineage_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, event_type, snapshot_id, job_id, stage, entity_type, entity_id,
             parent_type, parent_id,
             json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), time.time()),
        )
        return event_id

    def lineage(self, *, job_id: str | None = None, snapshot_id: str | None = None,
                limit: int = 500) -> list[dict[str, Any]]:
        if not job_id and not snapshot_id:
            raise ValueError("job_id or snapshot_id is required")
        clauses, params = [], []
        if job_id:
            clauses.append("job_id=?"); params.append(job_id)
        if snapshot_id:
            clauses.append("snapshot_id=?"); params.append(snapshot_id)
        params.append(max(1, min(limit, 2000)))
        rows = self._conn().execute(
            f"SELECT * FROM analysis_lineage_events WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at,event_id LIMIT ?", params,
        ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            output.append(item)
        return output

    def register_question(self, coin: str, mode: str, question: str, *, enqueue: bool = True,
                          origin: str = "scheduled") -> tuple[str, str | None]:
        """Persist an active question and enqueue it against the latest committed snapshot."""
        coin, mode, question = coin.strip().upper(), mode.strip(), question.strip()
        if coin not in COIN_POOL or mode not in QUESTION_TYPES:
            raise ValueError("unsupported coin or mode")
        if not question or len(question) > 1000:
            raise ValueError("question must contain 1..1000 characters")
        existing = self._conn().execute(
            "SELECT count(*) FROM analysis_questions WHERE coin=? AND mode=? AND active=1", (coin, mode),
        ).fetchone()[0]
        known = self._conn().execute(
            "SELECT 1 FROM analysis_questions WHERE coin=? AND mode=? AND question=?", (coin, mode, question),
        ).fetchone()
        if existing >= 20 and known is None:
            raise ValueError("active question limit reached")
        now = time.time()
        question_id = "question-" + hashlib.sha256(f"{coin}\0{mode}\0{question}".encode()).hexdigest()[:20]
        self._conn().execute("""
          INSERT INTO analysis_questions VALUES(?,?,?,?,1,?,?)
          ON CONFLICT(coin,mode,question) DO UPDATE SET active=1,updated_at=excluded.updated_at
        """, (question_id, coin, mode, question, now, now))
        message_id = "message-" + hashlib.sha256(
            f"user\0{question_id}\0{int(now)}".encode(),
        ).hexdigest()[:20]
        self._conn().execute(
            "INSERT OR IGNORE INTO analysis_conversation VALUES(?,?,?,?,?,?,?,?,?)",
            (message_id, coin, mode, "user", question, question_id, None, None, now),
        )
        snap = self._conn().execute(
            "SELECT snapshot_id FROM analysis_snapshots WHERE coin=? ORDER BY created_at DESC LIMIT 1", (coin,),
        ).fetchone()
        job_id = self.enqueue_job(snap[0], mode, question, origin=origin) if snap and enqueue else None
        return question_id, job_id

    def submit_manual(self, coin: str, mode: str, question: str,
                      *, locale: str = DEFAULT_NARRATIVE_LOCALE) -> tuple[str, str | None]:
        """Create a high-priority, snapshot-isolated job for an explicit user run.

        This deliberately does not consult the Hermes autonomy toggle: that
        toggle controls scheduled refresh creation only.  The normal snapshot
        ingestion and downstream pipeline remain subject to their existing
        source, Bedrock and cost controls.

        `locale` (N11) selects the narrative output language and rides on the
        in-memory stage package only — see `enqueue_job`.
        """
        # Validate and persist the intent before collecting live sources.  Bad
        # input must never trigger a chargeable/network ingestion attempt.
        coin, mode, question = coin.strip().upper(), mode.strip(), question.strip()
        question_id, _ = self.register_question(coin, mode, question, enqueue=False)
        locale = normalize_locale(locale)
        canonical_key = f"{coin}\0{mode}\0{question}"
        with _manual_dedup_lock(self.path, canonical_key):
            existing = self._conn().execute("""
              SELECT job_id FROM analysis_jobs
              WHERE coin=? AND mode=? AND question=? AND origin='manual'
              AND state IN ('queued','running','completed') AND created_at>=?
              ORDER BY created_at DESC LIMIT 1
            """, (coin, mode, question, time.time() - MANUAL_DEDUP_WINDOW_SEC)).fetchone()
            if existing:
                existing_job_id = existing["job_id"]
                # N11: `analysis_jobs` has UNIQUE(snapshot_id, coin, mode,
                # question) with no `locale` column (schema change is CDO
                # scope, not touched here). Reusing this row is correct for
                # dedup purposes, but if the caller asked for a *different*
                # locale than whatever this job last published, blindly
                # returning it silently serves a stale-language report — the
                # exact N11 production bug. Re-drive the same job through the
                # pipeline instead so the reused row picks up the requested
                # locale. Locale is tracked durably via `analysis_lineage_events`
                # (see `_locale_for_job`), not in-process state, because the
                # daemon that actually executes the stages runs in a
                # *different OS process* (`run_analysis_flow.py --daemon`)
                # from whichever process called `submit_manual`.
                if self._locale_for_job(existing_job_id) == locale:
                    return question_id, existing_job_id
                self._append_lineage(
                    "job_relocalized", entity_type="analysis_job", entity_id=existing_job_id,
                    job_id=existing_job_id, metadata={"locale": locale},
                )
                self._conn().execute(
                    "UPDATE analysis_jobs SET state='queued',current_stage=?,error=NULL,updated_at=? WHERE job_id=?",
                    (STAGES[0], time.time(), existing_job_id),
                )
                self._checkpoint(existing_job_id, STAGES[0], "queued")
                self._put_package(STAGES[0], {"job_id": existing_job_id, "locale": locale})
                self._adopted.add(existing_job_id)
                return question_id, existing_job_id
            snapshot_id = self.create_snapshot(coin, query=question)
            job_id = self.enqueue_job(snapshot_id, mode, question, origin="manual",
                                      locale=locale)
            if job_id is None:
                existing = self._conn().execute(
                    "SELECT job_id FROM analysis_jobs WHERE snapshot_id=? AND coin=? AND mode=? AND question=?",
                    (snapshot_id, coin, mode, question),
                ).fetchone()
                job_id = existing["job_id"] if existing else None
            return question_id, job_id

    def submit_multi_angle(self, coin: str, question: str, *,
                           locale: str = DEFAULT_NARRATIVE_LOCALE) -> dict[str, Any]:
        """建立同一個 snapshot，同時跑五角度（#809）。

        回傳 {snapshot_id, job_ids: {mode: job_id}, coin}。
        五角度共用同一份 immutable source snapshot（解決 G-MA-2）。
        """
        coin = coin.strip().upper()
        if coin not in COIN_POOL:
            raise ValueError(f"unsupported coin: {coin}")
        locale = normalize_locale(locale)
        # 原子預留五個 fan-out pipeline 並計入其他 in-flight reservations。
        # 選填 narration 是第六次呼叫，合成時另走 `_bedrock_live_attempt`
        # 單獨預留；第六份額度不足時只降級 deterministic narration。
        reservation = budget_guard.try_reserve_request_budget_batch(len(MODES))
        if reservation is None:
            raise MultiAngleBudgetError("無法原子預留五角度分析預算")

        conn = self._conn()
        # 快速容量預檢放在資料收集/建立 snapshot 之前，避免明知不可能原子
        # 入列時留下孤兒 snapshot。真正防 race 的檢查仍在下方 IMMEDIATE tx。
        pending = conn.execute(
            "SELECT count(*) FROM analysis_jobs WHERE state IN ('queued','running')"
        ).fetchone()[0]
        if pending + len(MODES) > QUEUE_CAPACITY:
            raise MultiAngleCapacityError(
                f"佇列剩餘容量不足，五角度需同時保留 {len(MODES)} 個位置"
            )

        snapshot_id = self.create_snapshot(coin, query=question)
        planned: list[tuple[str, str, str]] = []
        for mode, (_qtype, template) in MODES.items():
            mode_question = template.format(coin=coin)
            planned.append((mode, mode_question, f"flow-{uuid.uuid4().hex[:16]}"))

        now = time.time()
        try:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                "SELECT count(*) FROM analysis_jobs WHERE state IN ('queued','running')"
            ).fetchone()[0]
            if pending + len(planned) > QUEUE_CAPACITY:
                raise MultiAngleCapacityError(
                    f"佇列剩餘容量不足，五角度需同時保留 {len(planned)} 個位置"
                )
            conn.execute(
                "INSERT INTO analysis_multi_angle_runs VALUES(?,?,?,?)",
                (snapshot_id, coin, now, reservation),
            )
            for mode, mode_question, job_id in planned:
                qtype = QUESTION_TYPES[mode]
                cur = conn.execute(
                    """INSERT OR IGNORE INTO analysis_jobs(
                         job_id,snapshot_id,coin,mode,question,question_type,state,current_stage,
                         retry_count,error,created_at,updated_at,origin,priority
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, snapshot_id, coin, mode, mode_question, qtype.value,
                     "queued", STAGES[0], 0, None, now, now, "manual", MANUAL_PRIORITY),
                )
                if not cur.rowcount:
                    raise MultiAngleCapacityError("五角度工作已存在，未建立部分重複工作")
                self._checkpoint(job_id, STAGES[0], "queued")
                self._append_lineage(
                    "job_enqueued", entity_type="analysis_job", entity_id=job_id,
                    snapshot_id=snapshot_id, job_id=job_id,
                    parent_type="snapshot", parent_id=snapshot_id,
                    metadata={"coin": coin, "mode": mode,
                              "question_type": qtype.value, "origin": "manual",
                              "priority": MANUAL_PRIORITY, "locale": locale},
                )
            job_ids = {mode: job_id for mode, _question, job_id in planned}
            self._append_lineage(
                "multi_angle_submitted", entity_type="multi_angle_run",
                entity_id=f"ma-{snapshot_id}", snapshot_id=snapshot_id,
                metadata={"coin": coin, "job_ids": job_ids, "locale": locale},
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.execute(
                "DELETE FROM analysis_snapshots WHERE snapshot_id=? "
                "AND NOT EXISTS(SELECT 1 FROM analysis_jobs WHERE snapshot_id=?)",
                (snapshot_id, snapshot_id),
            )
            budget_guard.release_request_budget(reservation)
            raise

        for mode, mode_question, job_id in planned:
            try:
                self.register_question(coin, mode, mode_question, enqueue=False)
            except Exception:
                logging.getLogger(__name__).warning(
                    "multi-angle question registry failed for durable job %s",
                    job_id,
                    exc_info=True,
                )
            # SQLite queue/checkpoint 是跨 process source of truth；in-memory
            # package 只是同 process 快路徑。快路徑失敗時保留 durable queued
            # state，daemon 的 reconcile/restart 會接手，不可回滾成部分 DB jobs。
            try:
                self._put_package(
                    STAGES[0],
                    {"job_id": job_id, "priority": MANUAL_PRIORITY, "locale": locale},
                )
                self._adopted.add(job_id)
            except Exception:
                logging.getLogger(__name__).warning(
                    "multi-angle in-memory dispatch failed; durable daemon will recover %s",
                    job_id,
                    exc_info=True,
                )
        return {"snapshot_id": snapshot_id, "job_ids": job_ids, "coin": coin}

    def multi_angle_status(self, coin: str, snapshot_id: str | None = None) -> dict[str, Any] | None:
        """回傳指定幣種的最新 multi-angle synthesis 結果。

        支援指定 snapshot_id 或取最新。readonly safe。
        """
        if self._readonly_store_missing():
            return None
        coin = coin.strip().upper()
        if snapshot_id:
            row = self._conn().execute(
                "SELECT payload_json FROM analysis_results "
                "WHERE snapshot_id=? AND coin=? AND mode='multi_angle' "
                "ORDER BY published_at DESC LIMIT 1",
                (snapshot_id, coin),
            ).fetchone()
        else:
            row = self._conn().execute(
                "SELECT payload_json FROM analysis_results "
                "WHERE coin=? AND mode='multi_angle' "
                "ORDER BY published_at DESC LIMIT 1",
                (coin,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def _maybe_trigger_synthesis(self, snapshot_id: str, coin: str) -> bool:
        """檢查同 snapshot 五角度是否全部完成，觸發 synthesis（#809）。

        由 _stage_report_delivery 在 COMMIT 後 fail-soft 呼叫。
        最後完成的 job 觸發此函式。Synthesis 是確定性演算法，不呼叫 LLM。
        """
        conn = self._conn()
        submitted = conn.execute(
            "SELECT 1 FROM analysis_multi_angle_runs WHERE snapshot_id=? AND coin=?",
            (snapshot_id, coin),
        ).fetchone()
        if not submitted:
            return False
        completed = conn.execute(
            "SELECT DISTINCT mode FROM analysis_results WHERE snapshot_id=? AND coin=?",
            (snapshot_id, coin),
        ).fetchall()
        completed_modes = {row["mode"] for row in completed}
        if not set(MODES.keys()) <= completed_modes:
            return False
        # 避免重複觸發
        existing = conn.execute(
            "SELECT 1 FROM analysis_results WHERE snapshot_id=? AND coin=? AND mode='multi_angle'",
            (snapshot_id, coin),
        ).fetchone()
        if existing:
            return False
        # 原子取得 synthesis claim，避免兩個最後完成的 worker 都呼叫 LLM、
        # 重複寫 lineage。未完成或失敗時會釋放 claim，允許後續重試。
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM analysis_synthesis_claims "
                "WHERE snapshot_id=? AND coin=? AND claimed_at<?",
                (snapshot_id, coin, time.time() - STALE_RUNNING_JOB_THRESHOLD_SECONDS),
            )
            claim = conn.execute(
                "INSERT OR IGNORE INTO analysis_synthesis_claims VALUES(?,?,?)",
                (snapshot_id, coin, time.time()),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        if not claim.rowcount:
            return False
        try:
            return self._complete_claimed_synthesis(snapshot_id, coin)
        except Exception:
            conn.execute(
                "DELETE FROM analysis_synthesis_claims WHERE snapshot_id=? AND coin=?",
                (snapshot_id, coin),
            )
            raise

    def _complete_claimed_synthesis(self, snapshot_id: str, coin: str) -> bool:
        """完成已取得唯一 claim 的合成；任何例外由 caller 釋放 claim。"""
        conn = self._conn()
        from .multi_angle import angle_result_from_payload, synthesize_angles
        angles = []
        for mode in MODES:
            row = conn.execute(
                "SELECT payload_json, job_id, question FROM analysis_results "
                "WHERE snapshot_id=? AND coin=? AND mode=? "
                "ORDER BY published_at DESC LIMIT 1",
                (snapshot_id, coin, mode),
            ).fetchone()
            if row:
                angles.append(angle_result_from_payload(
                    mode,
                    row["payload_json"],
                    job_id=row["job_id"],
                    question=row["question"],
                ))
        if len(angles) < len(MODES):
            raise RuntimeError("multi-angle inputs disappeared after synthesis claim")
        report = synthesize_angles(angles, coin, snapshot_id)
        narration = report.synthesis_summary
        # Flag 關閉時完全不進 live gate、不預留成本、不建立 client。
        if os.environ.get("TRUSTFORGE_MULTI_ANGLE_NARRATION") == "1":
            try:
                from .multi_angle import narrate_synthesis
                narration_log = ExecutionLog(run_id=f"ma-{snapshot_id}")
                with _bedrock_live_attempt(narration_log) as live:
                    client = BedrockClient(offline=not live)
                    narration = narrate_synthesis(report, client, narration_log)
            except Exception:
                narration = report.synthesis_summary
        now = time.time()
        result_id = f"result-ma-{snapshot_id}"
        payload = report.to_dict()
        if narration and narration != report.synthesis_summary:
            payload["narration"] = narration
        try:
            conn.execute(
                "INSERT INTO analysis_results VALUES(?,?,?,?,?,?,?,?)",
                (result_id, f"ma-{snapshot_id}", snapshot_id, coin, "multi_angle",
                 "五角度綜合評估", json.dumps(payload, ensure_ascii=False), now),
            )
        except sqlite3.IntegrityError:
            return False
        self._append_lineage(
            "multi_angle_synthesized", entity_type="multi_angle_result",
            entity_id=result_id, snapshot_id=snapshot_id,
            metadata={"consensus": report.consensus, "conflicts_count": len(report.conflicts),
                      "evidence_independence": report.evidence_independence},
        )
        reserved_row = conn.execute(
            "SELECT reserved_usd FROM analysis_multi_angle_runs "
            "WHERE snapshot_id=? AND coin=?",
            (snapshot_id, coin),
        ).fetchone()
        reserved = float(reserved_row["reserved_usd"] or 0.0) if reserved_row else 0.0
        if reserved:
            conn.execute(
                "UPDATE analysis_multi_angle_runs SET reserved_usd=0 "
                "WHERE snapshot_id=? AND coin=?",
                (snapshot_id, coin),
            )
            budget_guard.release_request_budget(reserved)
        return True

    def _multi_angle_prepaid(self, snapshot_id: str) -> bool:
        row = self._conn().execute(
            "SELECT reserved_usd FROM analysis_multi_angle_runs WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        return bool(row and float(row["reserved_usd"] or 0.0) > 0)

    def question_context(self, coin: str, mode: str, question: str, *, limit: int = 5) -> dict[str, Any]:
        """Retrieve semantically similar prior questions and their published answers.

        Character bigrams deliberately support Chinese without an opaque embedding
        service. Results are real SQLite memories and retain snapshot/run lineage.
        """
        coin, mode, question = coin.upper(), mode.strip(), question.strip()
        if coin not in COIN_POOL or mode not in QUESTION_TYPES or not question:
            raise ValueError("valid coin, mode and question required")
        if self._readonly_store_missing():
            return {"query": question, "matches": [], "conversation": [], "retrieval": "sqlite_char_bigram_v1"}
        rows = self._conn().execute("""
          SELECT q.question_id,q.coin,q.mode,q.question,q.updated_at,
                 r.snapshot_id,r.job_id,r.payload_json,r.published_at
          FROM analysis_questions q
          LEFT JOIN analysis_results r ON r.result_id=(
            SELECT r2.result_id FROM analysis_results r2
            WHERE r2.coin=q.coin AND r2.mode=q.mode AND r2.question=q.question
            ORDER BY r2.published_at DESC LIMIT 1
          )
          WHERE q.active=1
          ORDER BY q.updated_at DESC LIMIT 300
        """).fetchall()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            similarity = _question_similarity(question, row["question"])
            if row["coin"] == coin:
                similarity += 0.12
            if row["mode"] == mode:
                similarity += 0.08
            if similarity <= 0.08:
                continue
            judgment = None
            if row["payload_json"]:
                try:
                    judgment = json.loads(row["payload_json"]).get("report", {}).get("market_judgment")
                except (TypeError, json.JSONDecodeError):
                    pass
            candidates.append({
                "question_id": row["question_id"], "coin": row["coin"], "mode": row["mode"],
                "question": row["question"], "similarity": round(min(similarity, 1.0), 4),
                "answer": judgment, "snapshot_id": row["snapshot_id"], "job_id": row["job_id"],
                "published_at": row["published_at"],
                "source_tier": "historical_non_evidentiary",
            })
        candidates.sort(key=lambda item: (item["similarity"], item["published_at"] or 0), reverse=True)
        conversation = [dict(row) for row in self._conn().execute("""
          SELECT message_id,role,content,question_id,job_id,snapshot_id,created_at
          FROM analysis_conversation WHERE coin=? AND mode=?
          ORDER BY created_at DESC LIMIT 12
        """, (coin, mode)).fetchall()]
        conversation.reverse()
        return {"query": question, "matches": candidates[:max(1, min(limit, 10))],
                "conversation": conversation, "retrieval": "sqlite_char_bigram_v1"}

    def enqueue_job(self, snapshot_id: str, mode: str, question: str, *, origin: str = "scheduled",
                    locale: str = DEFAULT_NARRATIVE_LOCALE) -> str | None:
        """`locale`（N11）：敘事輸出語系，只掛在**行程內的 stage package** 上，
        不進 `analysis_jobs` 資料表（schema 異動歸 CDO，本次不碰）。因此
        daemon 重啟後由 `_restart_from_snapshot()` 重跑的工作會回到預設中文
        ——這是已知且刻意的降級，不是靜默錯誤。"""
        row = self._conn().execute("SELECT coin FROM analysis_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if row is None or mode not in QUESTION_TYPES:
            raise ValueError("unknown snapshot or mode")
        if origin not in {"manual", "scheduled"}:
            raise ValueError("unsupported analysis job origin")
        qtype = QUESTION_TYPES[mode]
        priority = MANUAL_PRIORITY if origin == "manual" else SCHEDULED_PRIORITY
        # Idempotency must be checked before capacity.  Otherwise a full queue
        # prevents refresh_once() from revisiting the same immutable snapshot
        # and filling matrix entries that did not fit during the previous pass.
        if self._conn().execute(
            "SELECT 1 FROM analysis_jobs WHERE snapshot_id=? AND coin=? AND mode=? AND question=?",
            (snapshot_id, row["coin"], mode, question),
        ).fetchone():
            return None
        pending = self._conn().execute(
            "SELECT count(*) FROM analysis_jobs WHERE state IN ('queued','running')",
        ).fetchone()[0]
        if pending >= QUEUE_CAPACITY:
            return None
        job_id, now = f"flow-{uuid.uuid4().hex[:16]}", time.time()
        cur = self._conn().execute(
            """INSERT OR IGNORE INTO analysis_jobs(
                 job_id,snapshot_id,coin,mode,question,question_type,state,current_stage,retry_count,error,
                 created_at,updated_at,origin,priority
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, snapshot_id, row["coin"], mode, question, qtype.value, "queued", STAGES[0], 0, None,
             now, now, origin, priority),
        )
        if not cur.rowcount:
            return None
        self._checkpoint(job_id, STAGES[0], "queued")
        self._append_lineage(
            "job_enqueued", entity_type="analysis_job", entity_id=job_id,
            snapshot_id=snapshot_id, job_id=job_id,
            parent_type="snapshot", parent_id=snapshot_id,
            metadata={"coin": row["coin"], "mode": mode, "question_type": qtype.value,
                      "origin": origin, "priority": priority, "locale": normalize_locale(locale)},
        )
        self._put_package(STAGES[0], {"job_id": job_id, "priority": priority,
                                      "locale": normalize_locale(locale)})
        self._adopted.add(job_id)
        return job_id

    def create_snapshot(self, coin: str, *, query: str = "市場信任分析") -> str:
        coin = coin.upper()
        if coin not in COIN_POOL:
            raise ValueError(f"unsupported coin: {coin}")
        docs = collect(query, coin=coin, offline=False)
        raw = [doc_to_dict(doc) for doc in docs]
        encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        revision = hashlib.sha256(encoded.encode()).hexdigest()
        snapshot_id = f"snap-{coin.lower()}-{revision[:16]}"
        cursor = self._conn().execute(
            "INSERT OR IGNORE INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
            (snapshot_id, coin, time.time(), revision, encoded, len(raw)),
        )
        if cursor.rowcount:
            self._append_lineage(
                "snapshot_created", entity_type="snapshot", entity_id=snapshot_id,
                snapshot_id=snapshot_id,
                metadata={"coin": coin, "source_revision": revision, "document_count": len(raw)},
            )
        return snapshot_id

    def enqueue_matrix(self, snapshot_id: str, *, questions: dict[str, str] | None = None) -> list[str]:
        row = self._conn().execute("SELECT coin FROM analysis_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if row is None:
            raise ValueError("unknown snapshot")
        coin, jobs = row["coin"], []
        for mode, (qtype, template) in MODES.items():
            del qtype
            default = (questions or {}).get(mode) or template.format(coin=coin)
            self.register_question(coin, mode, default, enqueue=False)
            active = self._conn().execute(
                "SELECT question FROM analysis_questions WHERE coin=? AND mode=? AND active=1 ORDER BY created_at",
                (coin, mode),
            ).fetchall()
            for item in active:
                job_id = self.enqueue_job(snapshot_id, mode, item["question"], origin="scheduled")
                if job_id: jobs.append(job_id)
        return jobs

    def _checkpoint(self, job_id: str, stage: str, state: str, *, started: float | None = None,
                    duration: float | None = None, events: int = 0, error: str | None = None,
                    retry: int = 0) -> None:
        now = time.time()
        self._conn().execute("""
          INSERT INTO analysis_stage_runs(job_id,stage,state,queue_entered_at,started_at,finished_at,duration_sec,event_count,retry_count,error)
          VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id,stage) DO UPDATE SET
          state=excluded.state, started_at=COALESCE(excluded.started_at,analysis_stage_runs.started_at),
          finished_at=excluded.finished_at, duration_sec=excluded.duration_sec,
          event_count=excluded.event_count, retry_count=excluded.retry_count, error=excluded.error
        """, (job_id, stage, state, now, started, now if state in {"completed", "failed"} else None, duration, events, retry, error))
        job_state = "failed" if state == "failed" else "queued" if state == "queued" else "running"
        self._conn().execute("UPDATE analysis_jobs SET state=?,current_stage=?,error=?,updated_at=? WHERE job_id=?",
                             (job_state, stage, error, now, job_id))

    def _put_package(self, stage: str, package: dict[str, Any]) -> None:
        priority = int(package.get("priority", self._job(package["job_id"])["priority"]))
        package["priority"] = priority
        self._queues[stage].put((priority, next(self._queue_sequence), package))

    def start(self) -> None:
        self.recover()
        for stage in STAGES:
            for index in range(self.workers_per_stage):
                self._spawn_worker(stage, index)
        self.adopt_due_retries()

    def _spawn_worker(self, stage: str, index: int) -> None:
        thread = threading.Thread(
            target=self._worker, args=(stage,),
            name=f"hermes-{stage}-{index}", daemon=True,
        )
        thread.start()
        self._threads.append(thread)

    def _restart_from_snapshot(self, job_ids: list[str]) -> int:
        restarted = 0
        for job_id in job_ids:
            self._conn().execute("DELETE FROM analysis_retry_queue WHERE job_id=?", (job_id,))
            self._conn().execute("DELETE FROM analysis_stage_runs WHERE job_id=?", (job_id,))
            self._checkpoint(job_id, STAGES[0], "queued")
            self._put_package(STAGES[0], {"job_id": job_id, "locale": self._locale_for_job(job_id)})
            self._adopted.add(job_id)
            restarted += 1
        return restarted

    def reconcile_runtime(self) -> dict[str, int]:
        """Repair dead workers and durable rows whose in-memory package vanished.

        Intermediate Python objects are intentionally not persisted.  A lost
        package therefore restarts from its immutable snapshot rather than
        pretending the recorded stage can resume with missing state.
        """
        repaired = {"workers": 0, "jobs": 0}
        for stage in STAGES:
            prefix = f"hermes-{stage}-"
            live = [thread for thread in self._threads if thread.name.startswith(prefix) and thread.is_alive()]
            stage_was_unstaffed = not live
            if len(live) < self.workers_per_stage:
                # A running row owned by a dead stage worker has no recoverable
                # in-memory package. Only reset it when no worker for that stage
                # survived, avoiding duplicate work with multi-worker stages.
                if not live:
                    lost = self._conn().execute(
                        "SELECT job_id FROM analysis_stage_runs WHERE stage=? AND state='running'",
                        (stage,),
                    ).fetchall()
                    repaired["jobs"] += self._restart_from_snapshot([row["job_id"] for row in lost])
                for index in range(len(live), self.workers_per_stage):
                    self._spawn_worker(stage, index)
                    repaired["workers"] += 1

            # A durable queued row with an empty process queue is orphaned.  For
            # non-first stages its package cannot be reconstructed in place.
            if stage_was_unstaffed and stage != STAGES[0] and self._queues[stage].empty():
                running = self._conn().execute(
                    "SELECT count(*) FROM analysis_stage_runs WHERE stage=? AND state='running'",
                    (stage,),
                ).fetchone()[0]
                if not running:
                    orphaned = self._conn().execute(
                        "SELECT job_id FROM analysis_stage_runs WHERE stage=? AND state='queued'",
                        (stage,),
                    ).fetchall()
                    repaired["jobs"] += self._restart_from_snapshot([row["job_id"] for row in orphaned])
        if repaired["workers"] or repaired["jobs"]:
            logging.warning("Hermes runtime reconciled: %s", repaired)
        repaired["syntheses"] = self._recover_multi_angle_syntheses()
        return repaired

    def _recover_multi_angle_syntheses(self) -> int:
        """重啟/巡檢時補做五角度已完成但 crash 遺失的 synthesis。"""
        self._release_failed_multi_angle_reservations()
        cutoff = time.time() - STALE_RUNNING_JOB_THRESHOLD_SECONDS
        rows = self._conn().execute(
            """SELECT r.snapshot_id,r.coin
               FROM analysis_multi_angle_runs r
               WHERE NOT EXISTS(
                 SELECT 1 FROM analysis_results x
                 WHERE x.snapshot_id=r.snapshot_id AND x.coin=r.coin AND x.mode='multi_angle'
               )
               AND (SELECT count(DISTINCT x.mode) FROM analysis_results x
                    WHERE x.snapshot_id=r.snapshot_id AND x.coin=r.coin
                    AND x.mode IN ('risk','sentiment','fundamentals','news','catalyst'))=5
               AND NOT EXISTS(
                 SELECT 1 FROM analysis_synthesis_claims c
                 WHERE c.snapshot_id=r.snapshot_id AND c.coin=r.coin AND c.claimed_at>=?
               )""",
            (cutoff,),
        ).fetchall()
        recovered = 0
        for row in rows:
            self._conn().execute(
                "DELETE FROM analysis_synthesis_claims "
                "WHERE snapshot_id=? AND coin=? AND claimed_at<?",
                (row["snapshot_id"], row["coin"], cutoff),
            )
            if self._maybe_trigger_synthesis(row["snapshot_id"], row["coin"]):
                recovered += 1
        return recovered

    def _release_failed_multi_angle_reservations(self) -> int:
        """釋放已進 failed/dead-letter 終態、無法再完成合成的批次預留。"""
        rows = self._conn().execute(
            """SELECT r.snapshot_id,r.coin,r.reserved_usd
               FROM analysis_multi_angle_runs r
               WHERE r.reserved_usd>0 AND EXISTS(
                 SELECT 1 FROM analysis_jobs j
                 JOIN analysis_dead_letters d ON d.job_id=j.job_id
                 WHERE j.snapshot_id=r.snapshot_id
               )"""
        ).fetchall()
        for row in rows:
            self._conn().execute(
                "UPDATE analysis_multi_angle_runs SET reserved_usd=0 "
                "WHERE snapshot_id=? AND coin=? AND reserved_usd=?",
                (row["snapshot_id"], row["coin"], row["reserved_usd"]),
            )
            budget_guard.release_request_budget(float(row["reserved_usd"]))
        return len(rows)

    def recover(self) -> None:
        # Runtime payloads are deliberately not pickled. Restart from immutable snapshot.
        rows = self._conn().execute("""
          SELECT job_id FROM analysis_jobs WHERE state IN ('queued','running','failed')
          AND job_id NOT IN (SELECT job_id FROM analysis_retry_queue)
          AND job_id NOT IN (SELECT job_id FROM analysis_dead_letters)
          ORDER BY priority ASC, created_at ASC
        """).fetchall()
        for row in rows:
            if row["job_id"] in self._adopted:
                continue
            self._conn().execute("DELETE FROM analysis_stage_runs WHERE job_id=?", (row["job_id"],))
            self._checkpoint(row["job_id"], STAGES[0], "queued")
            self._put_package(STAGES[0], {"job_id": row["job_id"], "locale": self._locale_for_job(row["job_id"])})
            self._adopted.add(row["job_id"])
        self._recover_multi_angle_syntheses()

    def reap_stale_running(self, threshold_seconds: float | None = None) -> int:
        """Recover `state='running'` jobs whose checkpoint stopped advancing.

        `reconcile_runtime()` only detects a *dead* worker thread; it cannot
        see a worker that is still alive but hung (e.g. a blocked network
        call), nor a job left behind by a daemon process that crashed and was
        never restarted. This scans for running jobs whose `updated_at` is
        older than the threshold and routes them through the same
        retry-queue / dead-letter decision `_worker`'s exception handler
        already uses (three attempts, then dead-letter) — the actual
        re-enqueue happens later via the existing `adopt_due_retries()` /
        `_release_retry()` path, not a new mechanism here.
        """
        threshold = STALE_RUNNING_JOB_THRESHOLD_SECONDS if threshold_seconds is None else threshold_seconds
        cutoff = time.time() - threshold
        rows = self._conn().execute("""
          SELECT job_id, current_stage, retry_count FROM analysis_jobs
          WHERE state='running' AND updated_at < ?
          AND job_id NOT IN (SELECT job_id FROM analysis_retry_queue)
          AND job_id NOT IN (SELECT job_id FROM analysis_dead_letters)
        """, (cutoff,)).fetchall()
        reaped = 0
        for row in rows:
            job_id, stage, retry = row["job_id"], row["current_stage"], row["retry_count"] + 1
            error = f"stale running job reaped after {threshold:.0f}s without progress"
            self._conn().execute(
                "INSERT INTO analysis_stage_attempts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f"attempt-{uuid.uuid4().hex[:16]}", job_id, stage, retry, "failed",
                 time.time(), time.time(), 0.0, 1, error),
            )
            if retry < 3:
                self._conn().execute("UPDATE analysis_jobs SET retry_count=? WHERE job_id=?", (retry, job_id))
                self._conn().execute(
                    "INSERT OR REPLACE INTO analysis_retry_queue VALUES(?,?,?,?,?)",
                    (job_id, stage, time.time(), retry, error),
                )
                self._checkpoint(job_id, stage, "queued", error=error, retry=retry)
            else:
                job = self._job(job_id)
                self._conn().execute(
                    "INSERT OR REPLACE INTO analysis_dead_letters VALUES(?,?,?,?,?,?,?,?,?)",
                    (job_id, stage, job["coin"], job["mode"], job["question"], job["snapshot_id"],
                     retry, error, time.time()),
                )
                self._checkpoint(job_id, stage, "failed", error=error, retry=retry)
                self._adopted.discard(job_id)
            reaped += 1
        if reaped:
            logging.warning("Hermes reaped %d stale running job(s) after %.0fs idle", reaped, threshold)
        return reaped

    def adopt_pending(self) -> int:
        """Adopt jobs inserted by the web process without restarting the daemon."""
        rows = self._conn().execute("""
          SELECT s.job_id FROM analysis_stage_runs s
          JOIN analysis_jobs j USING(job_id)
          WHERE s.stage=? AND s.state='queued' AND j.current_stage=?
          AND s.job_id NOT IN (SELECT job_id FROM analysis_retry_queue)
          ORDER BY j.priority ASC, s.queue_entered_at ASC
        """, (STAGES[0], STAGES[0])).fetchall()
        adopted = 0
        for row in rows:
            job_id = row["job_id"]
            if job_id in self._adopted: continue
            self._adopted.add(job_id)
            self._put_package(STAGES[0], {"job_id": job_id, "locale": self._locale_for_job(job_id)})
            adopted += 1
        return adopted

    def _release_retry(self, job_id: str, stage: str) -> bool:
        cursor = self._conn().execute(
            "DELETE FROM analysis_retry_queue WHERE job_id=? AND stage=? AND next_retry_at<=?",
            (job_id, stage, time.time()),
        )
        if not cursor.rowcount: return False
        self._put_package(STAGES[0], {"job_id": job_id, "locale": self._locale_for_job(job_id)})
        self._adopted.add(job_id)
        return True

    def adopt_due_retries(self) -> int:
        rows = self._conn().execute(
            "SELECT job_id,stage FROM analysis_retry_queue WHERE next_retry_at<=? ORDER BY next_retry_at", (time.time(),),
        ).fetchall()
        return sum(1 for row in rows if self._release_retry(row["job_id"], row["stage"]))

    def _worker(self, stage: str) -> None:
        while not self._stop.is_set():
            try: package = self._queues[stage].get(timeout=.2)
            except queue.Empty: continue
            _, _, package = package
            started = time.time(); job_id = package["job_id"]
            self._checkpoint(job_id, stage, "running", started=started)
            job = self._job(job_id)
            self._append_lineage(
                "stage_started", entity_type="stage_run", entity_id=f"{job_id}:{stage}",
                snapshot_id=job["snapshot_id"], job_id=job_id, stage=stage,
                parent_type="analysis_job", parent_id=job_id,
            )
            try:
                package = getattr(self, f"_stage_{stage}")(package)
                events = len(package.get("log").events) if package.get("log") else 0
                duration = time.time() - started
                self._checkpoint(job_id, stage, "completed", started=started, duration=duration, events=events)
                self._append_lineage(
                    "stage_completed", entity_type="stage_run", entity_id=f"{job_id}:{stage}",
                    snapshot_id=job["snapshot_id"], job_id=job_id, stage=stage,
                    parent_type="analysis_job", parent_id=job_id,
                    metadata={"duration_sec": duration, "event_count": events},
                )
                pos = STAGES.index(stage)
                if pos + 1 < len(STAGES):
                    next_stage = STAGES[pos + 1]; self._checkpoint(job_id, next_stage, "queued")
                    self._put_package(next_stage, package)
                else:
                    self._conn().execute(
                        "UPDATE analysis_jobs SET state='completed',error=NULL,updated_at=? WHERE job_id=?",
                        (time.time(), job_id),
                    )
                    self._adopted.discard(job_id)
                    # Issue #570: three-track learning SUCCESS hook.
                    # Structurally fail-soft — runs strictly after durable
                    # state has landed; the helper never raises into us.
                    _emit_three_track_learning_on_success(self, job_id)
            except Exception as exc:
                retry = int(package.get("retries", {}).get(stage, 0)) + 1
                package.setdefault("retries", {})[stage] = retry
                retryable = not isinstance(exc, (ValueError, TypeError, KeyError))
                duration = time.time() - started
                self._append_lineage(
                    "stage_failed", entity_type="stage_attempt",
                    entity_id=f"{job_id}:{stage}:{retry}", snapshot_id=job["snapshot_id"],
                    job_id=job_id, stage=stage, parent_type="analysis_job", parent_id=job_id,
                    metadata={"duration_sec": duration, "retry": retry,
                              "retryable": retryable, "error_type": type(exc).__name__},
                )
                self._conn().execute(
                    "INSERT INTO analysis_stage_attempts VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (f"attempt-{uuid.uuid4().hex[:16]}", job_id, stage, retry, "failed", started,
                     time.time(), duration, 1 if retryable else 0, str(exc)[:1000]),
                )
                if retryable and retry < 3:
                    self._checkpoint(job_id, stage, "queued", started=started, duration=time.time()-started,
                                     error=str(exc)[:1000], retry=retry)
                    self._conn().execute("UPDATE analysis_jobs SET retry_count=retry_count+1 WHERE job_id=?", (job_id,))
                    delay = min(30.0, float(2 ** (retry - 1)))
                    next_retry_at = time.time() + delay
                    self._conn().execute(
                        "INSERT OR REPLACE INTO analysis_retry_queue VALUES(?,?,?,?,?)",
                        (job_id, stage, next_retry_at, retry, str(exc)[:1000]),
                    )
                    # In-process retries retain intermediate objects. After a daemon
                    # restart adopt_due_retries safely reconstructs from stage 1.
                    def release_in_process() -> None:
                        cursor = self._conn().execute(
                            "DELETE FROM analysis_retry_queue WHERE job_id=? AND stage=? AND next_retry_at<=?",
                            (job_id, stage, time.time()),
                        )
                        if cursor.rowcount: self._put_package(stage, package)
                    timer = threading.Timer(delay, release_in_process)
                    timer.daemon = True; timer.start()
                    self._timers.append(timer)
                else:
                    self._checkpoint(job_id, stage, "failed", started=started, duration=time.time()-started,
                                     error=str(exc)[:1000], retry=retry)
                    job = self._job(job_id)
                    self._conn().execute(
                        "INSERT OR REPLACE INTO analysis_dead_letters VALUES(?,?,?,?,?,?,?,?,?)",
                        (job_id, stage, job["coin"], job["mode"], job["question"], job["snapshot_id"],
                         retry, str(exc)[:1000], time.time()),
                    )
                    self._adopted.discard(job_id)
                    # Issue #570: three-track learning FAILURE hook.
                    # Structurally Fail-soft — runs strictly after the
                    # dead-letter row has landed; the helper never raises
                    # into us.
                    _emit_three_track_learning_on_failure(self, job_id, exc)
            finally: self._queues[stage].task_done()

    def _job(self, job_id: str) -> sqlite3.Row:
        return self._conn().execute("SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone()

    def _locale_for_job(self, job_id: str) -> str:
        """N11: `locale` was originally only ever carried on the in-process
        stage `package` dict, which is invisible across process boundaries —
        `run_analysis_flow.py --daemon` is a *separate OS process* from the
        web process that calls `submit_manual`/`enqueue_job`, so any package
        reconstructed purely from `job_id` (recover()/adopt_pending()/
        _release_retry(), all triggered by DB polling, not shared memory)
        silently fell back to DEFAULT_NARRATIVE_LOCALE. This is the actual
        production root cause, not just the manual-dedup window.

        Fix without an `analysis_jobs.locale` column (schema change is CDO
        scope): `analysis_lineage_events.metadata_json` is an existing
        free-form JSON column already written per job (`job_enqueued`,
        `job_relocalized`); we record locale there and read it back here.
        """
        row = self._conn().execute(
            """SELECT metadata_json FROM analysis_lineage_events
               WHERE job_id=? AND event_type IN ('job_enqueued','job_relocalized')
               ORDER BY created_at DESC LIMIT 1""",
            (job_id,),
        ).fetchone()
        if row is None:
            return DEFAULT_NARRATIVE_LOCALE
        try:
            locale = json.loads(row["metadata_json"]).get("locale")
        except (TypeError, json.JSONDecodeError):
            return DEFAULT_NARRATIVE_LOCALE
        return normalize_locale(locale) if locale else DEFAULT_NARRATIVE_LOCALE

    def _stage_source_ingestion(self, package: dict) -> dict:
        job = self._job(package["job_id"])
        snap = self._conn().execute("SELECT * FROM analysis_snapshots WHERE snapshot_id=?", (job["snapshot_id"],)).fetchone()
        package.update(job=job, docs=[doc_from_dict(x) for x in json.loads(snap["docs_json"])], log=ExecutionLog(run_id=job["job_id"]))
        package["log"].record("ingestion.collect", params={"coin": job["coin"], "snapshot_id": job["snapshot_id"]}, summary=f"locked {snap['document_count']} documents")
        package["retrieval_context"] = self.question_context(job["coin"], job["mode"], job["question"], limit=5)["matches"]
        package["log"].record(
            "retrieval.question_memory",
            params={"engine": "sqlite_char_bigram_v1", "snapshot_id": job["snapshot_id"]},
            summary=f"retrieved {len(package['retrieval_context'])} historical question contexts; non-evidentiary",
        )
        return package

    def _stage_claim_extraction(self, package: dict) -> dict:
        log = package["log"]
        with _bedrock_live_attempt(
            log, prepaid=self._multi_angle_prepaid(package["job"]["snapshot_id"])
        ) as live:
            client = BedrockClient(offline=not live)
            package["client"] = client
            package["claims"] = client.extract_claims_with_llm(package["docs"], log=log)
            llm_active = not client.offline and bool(client.config.model_id)
        log.record("bedrock.complete", params={"step": 1, "task": "claim_extraction", "llm_active": llm_active}, summary=f"{len(package['claims'])} claims")
        return package

    def _stage_trust_reasoning(self, package: dict) -> dict:
        from .agent.authoritative_kernel_mapper import run_authoritative_judgment
        from .agent.kernel_mapper import to_kernel_claim
        from .direction_resolution import resolve_direction

        finite = [d.ts for d in package["docs"] if math.isfinite(d.ts)]
        now = min(max(finite, default=time.time()), time.time())
        stance = build_stance_fn(stance_client=None, stance_remaining_time_fn=package["log"].remaining)
        # `offline` 反映 Step1（`_stage_claim_extraction`）決定的真實 live 狀態，
        # 不再寫死 True——`stance_client=None` 本來就不會真的打 Bedrock 分類，
        # 這裡只影響 DS EM 離線 fallback 是否觸發（見 `score()` docstring）。
        direction = resolve_direction(
            tuple(to_kernel_claim(claim) for claim in package["claims"]),
            coin=package["job"]["coin"],
            pit_epoch=now,
        )
        (
            package["kernel_output"],
            package["scored"],
            package["brief"],
            package["kernel_judgment"],
        ) = run_authoritative_judgment(
            package["claims"],
            pit_epoch=now,
            coin=package["job"]["coin"],
            query=package["job"]["question"],
            direction=direction,
            stance_fn=stance,
            offline=package["client"].offline,
        )
        package["stance"] = stance
        package["log"].record(
            "judgment.derive",
            params={
                "judgment_source": "trustforge_core.run_kernel",
                "contract_version": package["kernel_output"].contract_version,
            },
            summary=f"kernel scored {len(package['scored'])} claims",
        )
        return package

    def _stage_evidence_assembly(self, package: dict) -> dict:
        job = package["job"]
        client = package["client"]
        log = package["log"]

        def _build_report():
            return build_report(
                job["question"], job["coin"], QuestionType(job["question_type"]), package["brief"],
                client=client, log=log, stance_fn=package["stance"], scored=package["scored"],
                kernel_judgment=package["kernel_judgment"],
                locale=package.get("locale", DEFAULT_NARRATIVE_LOCALE),
            )

        if client.offline:
            # Step1 已判離線（未 live 資格 / 預留失敗）：Step3 narrative 不會
            # 憑空變成 live，維持離線、不需要再走一次閘。
            package["report"], package["evidence"] = _build_report()
        else:
            # Step3（`build_report` 內的 narrative 呼叫，Bedrock #2）跟 Step1
            # 是兩次獨立真呼叫，中間隔著佇列等待——重新走一次獨立的 live 閘 +
            # 預留，反映呼叫當下最新的 cap 狀態，不沿用 Step1 當時已過期的判定
            # （避免這段等待期間才發生的「已達每日上限」被繞過）。
            with _bedrock_live_attempt(
                log, prepaid=self._multi_angle_prepaid(package["job"]["snapshot_id"])
            ) as live:
                client.offline = not live
                package["report"], package["evidence"] = _build_report()
        return package

    def _stage_report_delivery(self, package: dict) -> dict:
        job, log = package["job"], package["log"]
        # Reuse the public API's established presentation aggregates so a stored
        # pre-analysis snapshot has exactly the same contract as /api/analyze.
        from .agent.orchestrator import aggregate_trust_by_kind
        from .web import VERSION, _aggregate_trust_components, _price_provenance_data, _public_evidence_dict
        evidence = package["evidence"]
        payload = {"version": VERSION, "report": dataclasses.asdict(package["report"]),
                   "evidence": [_public_evidence_dict(e) for e in evidence],
                   "trust_radar": aggregate_trust_by_kind(evidence),
                   "trust_components_aggregate": _aggregate_trust_components(evidence),
                   "price_provenance": _price_provenance_data(evidence),
                   "retrieval_context": package.get("retrieval_context", []),
                   "execution": log.manifest(), "execution_log": log.events, "snapshot_id": job["snapshot_id"], "mode": job["mode"]}
        now = time.time()
        self._conn().execute("BEGIN IMMEDIATE")
        try:
            self._conn().execute("INSERT OR REPLACE INTO analysis_results VALUES(?,?,?,?,?,?,?,?)",
                                 (f"result-{job['job_id']}", job["job_id"], job["snapshot_id"], job["coin"], job["mode"], job["question"], json.dumps(payload, ensure_ascii=False), now))
            self._append_lineage(
                "result_published", entity_type="analysis_result",
                entity_id=f"result-{job['job_id']}", snapshot_id=job["snapshot_id"],
                job_id=job["job_id"], stage="report_delivery",
                parent_type="analysis_job", parent_id=job["job_id"],
                metadata={"report_schema_version": payload["report"].get("schema_version"),
                          "evidence_count": len(evidence)},
            )
            trusts = [float(item.trust) for item in evidence]
            TrustFeatureStore(connection=self._conn(), initialize=False).put_many(
                feature_set="analysis_trust.v1", entity_key=job["coin"],
                features={
                    "calibrated_confidence": payload["report"].get("calibrated_confidence", 0.0),
                    "raw_confidence": payload["report"].get("confidence", 0.0),
                    "evidence_count": len(evidence),
                    "average_evidence_trust": sum(trusts) / len(trusts) if trusts else 0.0,
                    "independent_source_count": len({item.source for item in evidence}),
                },
                event_time=now, available_at=now, snapshot_id=job["snapshot_id"],
                run_id=job["job_id"], source_reference=f"result-{job['job_id']}",
            )
            answer = payload["report"].get("market_judgment") or "分析完成"
            self._conn().execute(
                "INSERT OR REPLACE INTO analysis_conversation VALUES(?,?,?,?,?,?,?,?,?)",
                (f"message-{job['job_id']}", job["coin"], job["mode"], "hermes", answer,
                 None, job["job_id"], job["snapshot_id"], now),
            )
            self._conn().execute("UPDATE analysis_jobs SET state='completed',updated_at=? WHERE job_id=?", (now, job["job_id"]))
            self._conn().execute("COMMIT")
        except Exception:
            self._conn().execute("ROLLBACK"); raise
        # #809: Multi-angle synthesis 觸發（fail-soft，不影響 report_delivery）
        try:
            self._maybe_trigger_synthesis(job["snapshot_id"], job["coin"])
        except Exception:
            logging.getLogger(__name__).warning(
                "multi-angle synthesis trigger failed (fail-soft) for snapshot=%s coin=%s",
                job["snapshot_id"], job["coin"], exc_info=True,
            )
        return package

    def status(self) -> dict[str, Any]:
        if self._readonly_store_missing():
            return {"agent": "hermes", "state": "continuous",
                    "stages": [{"id": stage, "queued": 0, "current": None, "next_retry_at": None} for stage in STAGES],
                    "queue": {"pending": 0, "capacity": QUEUE_CAPACITY, "backpressure": False},
                    "dead_letter_count": 0, "updated_at": iso_utc(time.time())}
        stages = []
        for stage in STAGES:
            running = self._conn().execute("""SELECT j.coin,j.mode,j.question,j.snapshot_id,j.origin,j.priority,s.started_at,s.retry_count,s.error
              FROM analysis_stage_runs s JOIN analysis_jobs j USING(job_id) WHERE s.stage=? AND s.state='running' ORDER BY s.started_at LIMIT 1""", (stage,)).fetchone()
            queued = self._conn().execute("SELECT count(*) FROM analysis_stage_runs WHERE stage=? AND state='queued'", (stage,)).fetchone()[0]
            retry = self._conn().execute(
                "SELECT min(next_retry_at) FROM analysis_retry_queue WHERE stage=?", (stage,),
            ).fetchone()[0]
            stages.append({"id": stage, "queued": queued, "current": dict(running) if running else None,
                           "next_retry_at": retry})
        dead = self._conn().execute("SELECT count(*) FROM analysis_dead_letters").fetchone()[0]
        pending = self._conn().execute(
            "SELECT count(*) FROM analysis_jobs WHERE state IN ('queued','running')",
        ).fetchone()[0]
        queued_manual = self._conn().execute(
            "SELECT count(*) FROM analysis_jobs WHERE state IN ('queued','running') AND origin='manual'",
        ).fetchone()[0]
        return {"agent": "hermes", "state": "continuous", "stages": stages,
                "queue": {"pending": pending, "capacity": QUEUE_CAPACITY,
                          "backpressure": pending >= QUEUE_CAPACITY,
                          "manual_pending": queued_manual},
                "dead_letter_count": dead, "updated_at": iso_utc(time.time())}

    def job_status(self, job_id: str) -> dict[str, Any] | None:
        """Return one durable job and its atomically published report, if ready."""
        if self._readonly_store_missing():
            return None
        job = self._conn().execute("SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone()
        if job is None:
            return None
        item = dict(job)
        result = self._conn().execute(
            "SELECT payload_json FROM analysis_results WHERE job_id=?", (job_id,),
        ).fetchone()
        item["result"] = json.loads(result["payload_json"]) if result else None
        ahead = self._conn().execute("""
          SELECT count(*) FROM analysis_jobs
          WHERE state='queued' AND current_stage=?
          AND (priority < ? OR (priority=? AND created_at < ?))
        """, (item["current_stage"], item["priority"], item["priority"], item["created_at"])).fetchone()[0]
        item["queue_position"] = ahead + 1 if item["state"] == "queued" else None
        return item

    def journey(self, *, limit: int = 50) -> dict[str, Any]:
        if self._readonly_store_missing():
            return {"jobs": [], "dead_letters": [], "updated_at": iso_utc(time.time())}
        jobs = [dict(row) for row in self._conn().execute(
            "SELECT * FROM analysis_jobs ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 200)),),
        ).fetchall()]
        # Avoid the former 2N+2 query pattern (202 queries for the dashboard's
        # limit=100). Under polling, several callers could hold hundreds of
        # SQLite statements/connections long enough to trigger a retry storm.
        # Fetch both child collections in two bounded set queries instead.
        stages_by_job: dict[str, list[dict[str, Any]]] = {job["job_id"]: [] for job in jobs}
        attempts_by_job: dict[str, list[dict[str, Any]]] = {job["job_id"]: [] for job in jobs}
        if jobs:
            ids = list(stages_by_job)
            placeholders = ",".join("?" for _ in ids)
            for row in self._conn().execute(
                f"SELECT * FROM analysis_stage_runs WHERE job_id IN ({placeholders}) "
                "ORDER BY job_id,queue_entered_at", ids,
            ).fetchall():
                stages_by_job[row["job_id"]].append(dict(row))
            for row in self._conn().execute(
                f"SELECT * FROM analysis_stage_attempts WHERE job_id IN ({placeholders}) "
                "ORDER BY job_id,started_at", ids,
            ).fetchall():
                attempts_by_job[row["job_id"]].append(dict(row))
        for job in jobs:
            job["stages"] = stages_by_job[job["job_id"]]
            job["attempts"] = attempts_by_job[job["job_id"]]
        dead = [dict(row) for row in self._conn().execute(
            "SELECT * FROM analysis_dead_letters ORDER BY failed_at DESC LIMIT ?", (max(1, min(limit, 200)),),
        ).fetchall()]
        return {"jobs": jobs, "dead_letters": dead, "updated_at": iso_utc(time.time())}

    def improvement_history(self, *, limit: int = 500) -> dict[str, Any]:
        """Bounded durable measurements for the outer-framework proposal loop."""
        jobs = self._conn().execute(
            "SELECT coin,mode,question,state,retry_count,created_at,updated_at FROM analysis_jobs ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(limit, 2000)),),
        ).fetchall()
        stages = self._conn().execute("""
          SELECT stage,count(*) runs,
                 avg(CASE WHEN duration_sec IS NOT NULL THEN duration_sec END) avg_duration_sec,
                 max(CASE WHEN duration_sec IS NOT NULL THEN duration_sec END) max_duration_sec,
                 sum(CASE WHEN state='failed' THEN 1 ELSE 0 END) failures,
                 sum(retry_count) retries
          FROM analysis_stage_runs GROUP BY stage ORDER BY stage
        """).fetchall()
        questions = [row[0] for row in self._conn().execute(
            "SELECT question FROM analysis_questions WHERE active=1 ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(limit, 2000)),),
        ).fetchall()]
        similar_pairs = 0
        compared_pairs = 0
        for index, question in enumerate(questions):
            for prior in questions[max(0, index - 20):index]:
                compared_pairs += 1
                if _question_similarity(question, prior) >= 0.72:
                    similar_pairs += 1
        return {
            "job_count": len(jobs),
            "completed_jobs": sum(1 for row in jobs if row["state"] == "completed"),
            "failed_jobs": sum(1 for row in jobs if row["state"] == "failed"),
            "retried_jobs": sum(1 for row in jobs if row["retry_count"] > 0),
            "active_question_count": len(questions),
            "compared_question_pairs": compared_pairs,
            "similar_question_pairs": similar_pairs,
            "similar_question_rate": round(similar_pairs / compared_pairs, 4) if compared_pairs else 0.0,
            "stages": [dict(row) for row in stages],
        }

    def requeue_dead_letter(self, job_id: str) -> bool:
        row = self._conn().execute("SELECT 1 FROM analysis_dead_letters WHERE job_id=?", (job_id,)).fetchone()
        if row is None: return False
        self._conn().execute("BEGIN IMMEDIATE")
        try:
            self._conn().execute("DELETE FROM analysis_dead_letters WHERE job_id=?", (job_id,))
            self._conn().execute("DELETE FROM analysis_retry_queue WHERE job_id=?", (job_id,))
            self._conn().execute("DELETE FROM analysis_stage_runs WHERE job_id=?", (job_id,))
            self._conn().execute("UPDATE analysis_jobs SET state='queued',current_stage=?,retry_count=0,error=NULL,updated_at=? WHERE job_id=?",
                                 (STAGES[0], time.time(), job_id))
            self._conn().execute("COMMIT")
        except Exception:
            self._conn().execute("ROLLBACK"); raise
        self._checkpoint(job_id, STAGES[0], "queued")
        return True

    def prune(self, *, snapshot_days: int = 30, job_days: int = 30, result_days: int = 90) -> dict[str, int]:
        now = time.time(); counts: dict[str, int] = {}
        # Never delete a snapshot referenced by an active job or retained result.
        result_cutoff, job_cutoff, snapshot_cutoff = now-result_days*86400, now-job_days*86400, now-snapshot_days*86400
        counts["attempts"] = self._conn().execute(
            "DELETE FROM analysis_stage_attempts WHERE finished_at < ?", (job_cutoff,),
        ).rowcount
        counts["results"] = self._conn().execute("DELETE FROM analysis_results WHERE published_at < ?", (result_cutoff,)).rowcount
        old_jobs = [row[0] for row in self._conn().execute(
            "SELECT job_id FROM analysis_jobs WHERE updated_at < ? AND state IN ('completed','failed')", (job_cutoff,),
        ).fetchall()]
        for job_id in old_jobs:
            self._conn().execute("DELETE FROM analysis_stage_runs WHERE job_id=?", (job_id,))
            self._conn().execute("DELETE FROM analysis_dead_letters WHERE job_id=?", (job_id,))
            self._conn().execute("DELETE FROM analysis_retry_queue WHERE job_id=?", (job_id,))
            self._conn().execute("DELETE FROM analysis_jobs WHERE job_id=?", (job_id,))
        counts["jobs"] = len(old_jobs)
        counts["snapshots"] = self._conn().execute("""
          DELETE FROM analysis_snapshots WHERE created_at < ?
          AND snapshot_id NOT IN (SELECT snapshot_id FROM analysis_jobs)
          AND snapshot_id NOT IN (SELECT snapshot_id FROM analysis_results)
        """, (snapshot_cutoff,)).rowcount
        self._conn().execute("PRAGMA wal_checkpoint(PASSIVE)")
        return counts

    def latest(self, coin: str, mode: str, question: str | None = None) -> dict | None:
        if self._readonly_store_missing():
            return None
        sql = "SELECT payload_json FROM analysis_results WHERE coin=? AND mode=?"
        params: list[Any] = [coin.upper(), mode]
        if question:
            sql += " AND question=?"; params.append(question.strip())
        row = self._conn().execute(sql + " ORDER BY published_at DESC LIMIT 1", params).fetchone()
        if row is None and question:
            # Fallback: 精確 question 沒匹配到時，退回只用 coin+mode 取最新結果
            row = self._conn().execute(
                "SELECT payload_json FROM analysis_results WHERE coin=? AND mode=? ORDER BY published_at DESC LIMIT 1",
                [coin.upper(), mode],
            ).fetchone()
        return json.loads(row[0]) if row else None

    def refresh_once(self) -> list[str]:
        """Snapshot every coin and incrementally fill its idempotent analysis matrix.

        Revisiting an unchanged snapshot is intentional: when the durable queue is
        full, later refreshes enqueue the matrix entries that previously could not
        fit.  Existing jobs are protected by the database uniqueness constraint.
        """
        jobs: list[str] = []
        for coin in COIN_POOL:
            try:
                snapshot_id = self.create_snapshot(coin)
            except Exception:
                logging.exception("Hermes snapshot refresh failed for %s; other coins continue", coin)
                continue
            jobs.extend(self.enqueue_matrix(snapshot_id))
        return jobs

    def join(self) -> None:
        for stage in STAGES: self._queues[stage].join()

    def stop(self) -> None:
        self._stop.set()
        for timer in self._timers: timer.cancel()
        self._timers.clear()
        for thread in self._threads: thread.join(timeout=1)
        self.close()

    def close(self) -> None:
        with self._connections_lock:
            connections, self._connections = self._connections, []
        for conn in connections:
            try: conn.close()
            except sqlite3.Error: pass
        self._local.conn = None

    def __enter__(self) -> "AnalysisFlow":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __del__(self):
        try: self.close()
        except Exception: pass
