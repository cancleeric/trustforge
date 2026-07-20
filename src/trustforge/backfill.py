"""Hermes 歷史回填系統（Backfill Worker）。

可控、可佈署、daemon 整合的 backfill worker。用 5 年官方 OHLCV + 可用歷史來源
逐日產 snapshot → 跑 replay，快速累積 point-in-time 校準資料（≥100 個）。

設計原則：
- 不偽造歷史：每個 backfill snapshot 明確標記 archive_type=backfilled_archive，
  與 forward-captured 隔離。
- 斷點續跑：進度持久化到 SQLite，中斷後從最後完成日期繼續。
- 啟停控制：三層（env → admin config → state file），預設關閉。
- 不阻擋正常分析：backfill 有自己的 thread/timer，daemon 主迴圈不被卡住。
- 速率可調：batch_size 控制一輪跑幾天，interval 控制每輪間隔。

Issue: #291
Spec: .kiro/specs/backfill-worker.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .historical_replay import replay_snapshot
from .ingestion.cache import (
    cache_set_if_newer,
    get_cache_backend,
    trust_snapshot_history_key,
)
from .ingestion.prices import Bar, load_ohlcv
from .replay import source_snapshot_backfill_key, SOURCE_SNAPSHOT_HISTORY_TTL_SECONDS
from .schema import COIN_POOL, iso_utc

logger = logging.getLogger(__name__)

# ─── 啟停控制 ────────────────────────────────────────────────────────────────

_TRUTHY = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSY = frozenset({"0", "false", "no", "off", "disabled"})


def _parse_bool(raw: str | None) -> bool | None:
    if raw is None or raw.strip() == "":
        return None
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return None


@dataclass(frozen=True)
class BackfillControl:
    enabled: bool
    source: str
    reason: str = ""


def backfill_enabled() -> BackfillControl:
    """三層控制：env > admin config > state file > default (off)。

    預設關閉（需明確啟動）。backfill 是重資源操作，不應自動跑。
    """
    # Layer 1: 環境變數最高優先
    env = _parse_bool(os.getenv("TRUSTFORGE_BACKFILL_ENABLED"))
    if env is not None:
        return BackfillControl(env, "env")

    # Layer 2: admin config（DynamoDB / 動態設定）
    try:
        from .admin_config import get_config_cached
        configured = getattr(get_config_cached(), "backfill_enabled", None)
        if configured is not None:
            return BackfillControl(bool(configured), "config")
    except Exception:
        pass

    # Layer 3: state file
    state_path = _state_file_path()
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        value = raw.get("enabled")
        if isinstance(value, bool):
            return BackfillControl(value, "state_file", str(raw.get("reason", "")))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Default: off（需明確啟動）
    return BackfillControl(False, "default", "backfill requires explicit activation")


def set_backfill_enabled(
    enabled: bool, *, reason: str = "cli", actor: str = "local",
) -> BackfillControl:
    """寫入 state file 控制 backfill 啟停。"""
    path = _state_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": bool(enabled),
        "reason": reason,
        "actor": actor,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return backfill_enabled()


def _state_file_path() -> Path:
    configured = os.getenv("TRUSTFORGE_BACKFILL_STATE_PATH", "").strip()
    if configured:
        return Path(configured)
    return _root() / "out" / "trustforge-backfill-control.json"


def _root() -> Path:
    return Path(
        os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])),
    )


# ─── 進度追蹤（SQLite 持久化）───────────────────────────────────────────────

def _db_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return _root() / "out" / "trustforge-backfill.sqlite3"


@dataclass
class BackfillProgress:
    coin: str
    start_date: str
    end_date: str
    last_completed_date: str | None
    total_days: int
    completed_days: int
    failed_days: int
    skipped_days: int
    state: str  # idle / running / paused / completed


@dataclass
class BackfillDayResult:
    coin: str
    date_str: str
    state: str  # completed / failed / skipped
    snapshot_id: str | None = None
    document_count: int = 0
    error: str | None = None
    duration_sec: float = 0.0


class BackfillWorker:
    """逐日歷史回填 worker。

    支援：
    - 多幣種並行或序列
    - 斷點續跑（進度持久化）
    - 啟停控制（每 batch 檢查一次 backfill_enabled）
    - batch_size 控制一輪跑幾天
    """

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        data_dir: str | Path | None = None,
        coins: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        batch_size: int = 30,
        interval_sec: float = 5.0,
    ):
        self.db_path = _db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir) if data_dir else _root() / "data" / "data"
        self.coins = [c.upper() for c in (coins or list(COIN_POOL))]
        self.start_date = start_date or "2021-07-01"
        self.end_date = end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.batch_size = max(1, batch_size)
        self.interval_sec = max(0.5, interval_sec)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=10,
                isolation_level=None,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS backfill_tasks (
          coin TEXT NOT NULL, date_str TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending',
          snapshot_id TEXT, document_count INTEGER DEFAULT 0,
          error TEXT, duration_sec REAL DEFAULT 0,
          created_at REAL NOT NULL, updated_at REAL NOT NULL,
          PRIMARY KEY(coin, date_str)
        );
        CREATE INDEX IF NOT EXISTS idx_backfill_tasks_state
          ON backfill_tasks(state, date_str);
        """)

    # ─── 任務排程 ─────────────────────────────────────────────────────────

    def plan(self) -> dict[str, int]:
        """計算每個幣種需要回填的天數，不寫入 DB。"""
        result: dict[str, int] = {}
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        for coin in self.coins:
            bars = load_ohlcv(coin, self.data_dir)
            bar_dates = {b.date for b in bars}
            count = 0
            day = start
            while day <= end:
                if day.isoformat() in bar_dates:
                    count += 1
                day += timedelta(days=1)
            result[coin] = count
        return result

    def seed_tasks(self) -> int:
        """將所有待回填日期寫入 SQLite（跳過已存在的記錄）。"""
        conn = self._get_conn()
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        now = time.time()
        seeded = 0
        for coin in self.coins:
            bars = load_ohlcv(coin, self.data_dir)
            bar_dates = {b.date for b in bars}
            day = start
            while day <= end:
                ds = day.isoformat()
                if ds in bar_dates:
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO backfill_tasks"
                        "(coin,date_str,state,created_at,updated_at)"
                        " VALUES(?,?,?,?,?)",
                        (coin, ds, "pending", now, now),
                    )
                    if cursor.rowcount:
                        seeded += 1
                day += timedelta(days=1)
        return seeded

    # ─── 核心執行 ─────────────────────────────────────────────────────────

    def run_batch(self) -> list[BackfillDayResult]:
        """跑一個 batch（最多 batch_size 天），回傳結果。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT coin, date_str FROM backfill_tasks"
            " WHERE state='pending'"
            " ORDER BY date_str ASC, coin ASC LIMIT ?",
            (self.batch_size,),
        ).fetchall()

        results: list[BackfillDayResult] = []
        for row in rows:
            if self._stop.is_set():
                break
            # 每天檢查啟停控制
            control = backfill_enabled()
            if not control.enabled:
                logger.info(
                    "Backfill paused by %s: %s", control.source, control.reason,
                )
                break
            result = self._process_day(row["coin"], row["date_str"])
            results.append(result)
        return results

    def _process_day(self, coin: str, date_str: str) -> BackfillDayResult:
        """處理單日回填：產 snapshot → replay。"""
        conn = self._get_conn()
        start_time = time.time()
        conn.execute(
            "UPDATE backfill_tasks SET state='running', updated_at=?"
            " WHERE coin=? AND date_str=?",
            (start_time, coin, date_str),
        )

        try:
            snapshot = self._build_day_snapshot(coin, date_str)
            if snapshot is None:
                result = BackfillDayResult(
                    coin, date_str, "skipped", error="no_data_for_date",
                )
                conn.execute(
                    "UPDATE backfill_tasks SET state='skipped',"
                    " error=?, updated_at=? WHERE coin=? AND date_str=?",
                    (result.error, time.time(), coin, date_str),
                )
                return result

            # 跑 replay（offline，不用 Bedrock）
            replay_result = replay_snapshot(
                snapshot,
                query=f"回填分析 {coin} {date_str} 市場信任狀態",
            )

            # 將 replay 結果寫入 trust_snapshot_history_key，讓
            # get_trust_history() 能讀到（格式對齊 fetch_scheduler.py
            # _snapshot_dict()：cache_get() → entry['docs'][0]）。
            self._persist_to_trust_history(coin, date_str, replay_result, snapshot)

            duration = time.time() - start_time
            doc_count = sum(
                len(s.get("documents", []))
                for s in snapshot.get("sources", [])
            )

            snapshot_id = f"backfill-{coin.lower()}-{date_str}"
            result = BackfillDayResult(
                coin,
                date_str,
                "completed",
                snapshot_id=snapshot_id,
                document_count=doc_count,
                duration_sec=duration,
            )
            conn.execute(
                "UPDATE backfill_tasks SET state='completed',"
                " snapshot_id=?, document_count=?, duration_sec=?,"
                " updated_at=? WHERE coin=? AND date_str=?",
                (snapshot_id, doc_count, duration, time.time(), coin, date_str),
            )
            return result

        except Exception as exc:
            duration = time.time() - start_time
            error_msg = f"{type(exc).__name__}: {exc}"
            result = BackfillDayResult(
                coin, date_str, "failed", error=error_msg, duration_sec=duration,
            )
            conn.execute(
                "UPDATE backfill_tasks SET state='failed',"
                " error=?, duration_sec=?, updated_at=?"
                " WHERE coin=? AND date_str=?",
                (error_msg, duration, time.time(), coin, date_str),
            )
            logger.warning(
                "Backfill failed for %s %s: %s", coin, date_str, error_msg,
            )
            return result

    def _build_day_snapshot(
        self, coin: str, date_str: str,
    ) -> dict[str, Any] | None:
        """用 OHLCV 截取到該日為止的資料，組成 point-in-time snapshot。"""
        bars = load_ohlcv(coin, self.data_dir)
        if not bars:
            return None

        # 截取到目標日期（含）的 OHLCV
        target = date.fromisoformat(date_str)
        eligible_bars = [b for b in bars if date.fromisoformat(b.date) <= target]
        if not eligible_bars:
            return None

        # 取最近 90 天作為 snapshot 資料（模擬當天可見的價格窗口）
        window = eligible_bars[-90:]

        # snapshot_epoch = 當天 UTC 23:59:59
        snapshot_epoch = datetime(
            target.year, target.month, target.day, 23, 59, 59,
            tzinfo=timezone.utc,
        ).timestamp()

        # 構建 OHLCV documents
        ohlcv_documents: list[dict[str, Any]] = []
        for bar in window:
            bar_date = date.fromisoformat(bar.date)
            published_at = datetime(
                bar_date.year, bar_date.month, bar_date.day,
                tzinfo=timezone.utc,
            ).timestamp()
            ohlcv_documents.append({
                "id": f"ohlcv-{coin}-{bar.date}",
                "kind": "price",
                "source": "ohlcv-official",
                "text": (
                    f"{coin} Daily OHLCV {bar.date}: "
                    f"O={bar.open:.2f} H={bar.high:.2f} L={bar.low:.2f} "
                    f"C={bar.close:.2f} V={bar.volume:.0f}"
                ),
                "url": "",
                "ts": published_at,
                "published_at": iso_utc(published_at),
                "meta": {
                    "coin": coin,
                    "date": bar.date,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                },
            })

        sources = [{
            "source": "ohlcv-official",
            "fetched_at": snapshot_epoch,
            "documents": ohlcv_documents,
        }]

        return {
            "coin": coin,
            "snapshot_at": iso_utc(snapshot_epoch),
            "snapshot_epoch": snapshot_epoch,
            "archive_type": "backfilled_archive",
            "sources": sources,
        }

    def _persist_to_trust_history(
        self,
        coin: str,
        date_str: str,
        replay_result: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> None:
        """將 replay 結果寫入 trust_snapshot_history_key。

        格式對齊 fetch_scheduler.py::_snapshot_dict()，確保
        get_trust_history() 能正確讀取（它讀 entry['docs'][0]）。

        明確標註 archive_type=backfilled_archive，與 forward-captured 區分。
        TTL 用 5 年（對齊 SOURCE_SNAPSHOT_HISTORY_TTL_SECONDS）——回填歷史
        需要長期保留供校準使用。
        """
        report = replay_result.get("report", {})

        # 組裝與 _snapshot_dict() 一致的格式
        snap: dict[str, Any] = {
            "coin": coin,
            "trust_score": round(float(report.get("confidence", 0)), 4),
            "direction": report.get("direction", "neutral"),
            "calibrated_confidence": round(
                float(report.get("calibrated_confidence", 0)), 4,
            ),
            "decision_state": report.get("decision_state", ""),
            "generated_at": report.get("generated_at", iso_utc(time.time())),
            "archive_type": "backfilled_archive",
            "snapshot_epoch": snapshot.get("snapshot_epoch", 0),
            "snapshot_at": snapshot.get("snapshot_at", ""),
        }

        backend = get_cache_backend()
        key = trust_snapshot_history_key(coin, date_str)
        fetched_at = float(snapshot.get("snapshot_epoch", 0))

        write_result = cache_set_if_newer(
            backend, key, [snap], fetched_at=fetched_at,
            ttl_seconds=SOURCE_SNAPSHOT_HISTORY_TTL_SECONDS,
        )
        if not write_result.ok:
            logger.warning(
                "Backfill trust history write failed for %s %s: %s",
                coin, date_str, write_result.error,
            )
        elif write_result.skipped:
            logger.debug(
                "Backfill trust history skipped (newer exists) for %s %s",
                coin, date_str,
            )

    # ─── Daemon 模式 ──────────────────────────────────────────────────────

    def start_daemon(self) -> None:
        """啟動背景 thread，持續執行 backfill 直到完成或被停止。"""
        if self._thread and self._thread.is_alive():
            logger.warning("Backfill daemon already running")
            return
        self._stop.clear()
        self.seed_tasks()
        self._thread = threading.Thread(
            target=self._daemon_loop, name="hermes-backfill", daemon=True,
        )
        self._thread.start()
        logger.info(
            "Backfill daemon started: coins=%s range=%s~%s",
            self.coins, self.start_date, self.end_date,
        )

    def stop_daemon(self) -> None:
        """停止背景 thread。"""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30)
        logger.info("Backfill daemon stopped")

    def _daemon_loop(self) -> None:
        """背景迴圈：每輪跑 batch_size 天，間隔 interval_sec。"""
        while not self._stop.is_set():
            control = backfill_enabled()
            if not control.enabled:
                logger.debug(
                    "Backfill disabled (%s); sleeping", control.source,
                )
                self._stop.wait(self.interval_sec * 4)
                continue

            results = self.run_batch()
            if not results:
                # 沒有 pending tasks → 完成
                logger.info("Backfill completed: no more pending tasks")
                break

            completed = sum(1 for r in results if r.state == "completed")
            failed = sum(1 for r in results if r.state == "failed")
            logger.info(
                "Backfill batch done: %d completed, %d failed, %d processed",
                completed, failed, len(results),
            )

            self._stop.wait(self.interval_sec)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ─── 狀態查詢 ─────────────────────────────────────────────────────────

    def progress(self) -> dict[str, BackfillProgress]:
        """查詢每個幣種的回填進度。"""
        conn = self._get_conn()
        result: dict[str, BackfillProgress] = {}
        for coin in self.coins:
            rows = conn.execute(
                "SELECT state, count(*) as cnt FROM backfill_tasks"
                " WHERE coin=? GROUP BY state",
                (coin,),
            ).fetchall()
            state_counts = {row["state"]: row["cnt"] for row in rows}
            total = sum(state_counts.values())
            completed = state_counts.get("completed", 0)
            failed = state_counts.get("failed", 0)
            skipped = state_counts.get("skipped", 0)
            pending = state_counts.get("pending", 0)
            running = state_counts.get("running", 0)

            last = conn.execute(
                "SELECT date_str FROM backfill_tasks"
                " WHERE coin=? AND state='completed'"
                " ORDER BY date_str DESC LIMIT 1",
                (coin,),
            ).fetchone()

            if total == 0:
                state = "idle"
            elif pending == 0 and running == 0:
                state = "completed"
            elif running > 0 or self.is_running:
                state = "running"
            else:
                state = "paused"

            result[coin] = BackfillProgress(
                coin=coin,
                start_date=self.start_date,
                end_date=self.end_date,
                last_completed_date=last["date_str"] if last else None,
                total_days=total,
                completed_days=completed,
                failed_days=failed,
                skipped_days=skipped,
                state=state,
            )
        return result

    def status(self) -> dict[str, Any]:
        """API 友善的狀態摘要。"""
        control = backfill_enabled()
        progress = self.progress()
        total_completed = sum(p.completed_days for p in progress.values())
        total_days = sum(p.total_days for p in progress.values())
        total_skipped = sum(p.skipped_days for p in progress.values())
        return {
            "enabled": control.enabled,
            "source": control.source,
            "reason": control.reason,
            "is_running": self.is_running,
            "coins": self.coins,
            "date_range": {"start": self.start_date, "end": self.end_date},
            "total_days": total_days,
            "total_completed": total_completed,
            "total_remaining": total_days - total_completed - total_skipped,
            "progress_pct": (
                round(total_completed / total_days * 100, 1) if total_days else 0
            ),
            "per_coin": {coin: asdict(p) for coin, p in progress.items()},
        }

    def reset_failed(self) -> int:
        """將 failed 狀態的 tasks 重設為 pending，以便重試。"""
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE backfill_tasks SET state='pending', error=NULL,"
            " updated_at=? WHERE state='failed'",
            (time.time(),),
        )
        return cursor.rowcount

    def close(self) -> None:
        """停止 daemon 並關閉連線。"""
        self.stop_daemon()
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None
