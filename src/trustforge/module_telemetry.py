"""模組 runtime 遙測（Module Runtime Telemetry）。

為 TrustForge 的 21+ 模組提供真實 runtime evidence：
- last_invoked_at：最後一次呼叫的 ISO timestamp
- invocation_count：累計呼叫次數
- last_result：最後一次呼叫結果（success / failure / degraded）
- avg_latency_ms：平均延遲（毫秒）

持久化用 SQLite（`out/module-telemetry.sqlite3`），寫入為背景
threading，失敗不影響核心 pipeline。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 預設 SQLite 位置
_DEFAULT_DB_PATH = os.getenv(
    "TRUSTFORGE_TELEMETRY_DB",
    str(Path(__file__).resolve().parents[2] / "out" / "module-telemetry.sqlite3"),
)

# Background write queue size limit
_QUEUE_MAX = 2048


@dataclass
class TelemetryRecord:
    """單一模組的遙測快照。"""
    module_id: str
    last_invoked_at: str  # ISO 8601
    invocation_count: int
    last_result: str  # success / failure / degraded
    avg_latency_ms: float
    last_latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class _WriteEvent:
    """Background writer 消費的事件。"""
    __slots__ = ("module_id", "latency_ms", "result", "ts", "metadata")

    def __init__(self, module_id: str, latency_ms: float, result: str,
                 ts: float, metadata: dict[str, Any] | None = None):
        self.module_id = module_id
        self.latency_ms = latency_ms
        self.result = result
        self.ts = ts
        self.metadata = metadata or {}


class ModuleTelemetry:
    """Thread-safe 模組遙測 singleton。

    寫入走 background thread（async write），讀取直接走 SQLite（immutable snapshot ok）。
    """

    _instance: "ModuleTelemetry | None" = None
    _lock = threading.Lock()

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._queue: queue.Queue[_WriteEvent | None] = queue.Queue(maxsize=_QUEUE_MAX)
        self._writer_thread: threading.Thread | None = None
        self._started = False
        self._init_db()
        self._start_writer()

    @classmethod
    def get_instance(cls, db_path: str | None = None) -> "ModuleTelemetry":
        """取得 singleton instance（thread-safe）。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path=db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """測試用：重設 singleton。"""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.shutdown()
                cls._instance = None

    def _init_db(self) -> None:
        """建立 SQLite schema（如果不存在）。"""
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            conn = sqlite3.connect(self._db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS module_telemetry (
                    module_id TEXT PRIMARY KEY,
                    last_invoked_at TEXT NOT NULL,
                    invocation_count INTEGER NOT NULL DEFAULT 0,
                    last_result TEXT NOT NULL DEFAULT 'unknown',
                    total_latency_ms REAL NOT NULL DEFAULT 0.0,
                    avg_latency_ms REAL NOT NULL DEFAULT 0.0,
                    last_latency_ms REAL NOT NULL DEFAULT 0.0,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
            """)
            conn.commit()
            conn.close()
        except Exception:
            logger.warning("module_telemetry: failed to init DB at %s", self._db_path, exc_info=True)

    def _start_writer(self) -> None:
        """啟動 background writer thread。"""
        if self._started:
            return
        self._started = True
        self._writer_thread = threading.Thread(
            target=self._writer_loop, daemon=True, name="module-telemetry-writer"
        )
        self._writer_thread.start()

    def _writer_loop(self) -> None:
        """Background loop：從 queue 消費並 batch 寫入 SQLite。"""
        batch: list[_WriteEvent] = []
        while True:
            try:
                # Block-wait for first event
                event = self._queue.get(timeout=2.0)
                if event is None:
                    # Shutdown signal
                    break
                batch.append(event)
                # Drain remaining events (non-blocking)
                while len(batch) < 64:
                    try:
                        ev = self._queue.get_nowait()
                        if ev is None:
                            self._flush_batch(batch)
                            return
                        batch.append(ev)
                    except queue.Empty:
                        break
                self._flush_batch(batch)
                batch = []
            except queue.Empty:
                if batch:
                    self._flush_batch(batch)
                    batch = []
            except Exception:
                logger.warning("module_telemetry: writer loop error", exc_info=True)
                batch = []

    def _flush_batch(self, batch: list[_WriteEvent]) -> None:
        """Batch 寫入 SQLite。"""
        if not batch:
            return
        try:
            conn = sqlite3.connect(self._db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            for ev in batch:
                ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ev.ts))
                meta_json = json.dumps(ev.metadata, ensure_ascii=False) if ev.metadata else "{}"
                conn.execute("""
                    INSERT INTO module_telemetry
                        (module_id, last_invoked_at, invocation_count, last_result,
                         total_latency_ms, avg_latency_ms, last_latency_ms, metadata)
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(module_id) DO UPDATE SET
                        last_invoked_at = excluded.last_invoked_at,
                        invocation_count = invocation_count + 1,
                        last_result = excluded.last_result,
                        total_latency_ms = total_latency_ms + excluded.total_latency_ms,
                        avg_latency_ms = (total_latency_ms + excluded.total_latency_ms)
                                         / (invocation_count + 1),
                        last_latency_ms = excluded.last_latency_ms,
                        metadata = excluded.metadata
                """, (ev.module_id, ts_iso, ev.result,
                      ev.latency_ms, ev.latency_ms, ev.latency_ms, meta_json))
            conn.commit()
            conn.close()
        except Exception:
            logger.warning("module_telemetry: flush_batch failed", exc_info=True)

    def record_invocation(
        self,
        module_id: str,
        latency_ms: float,
        result: str = "success",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """記錄一次模組呼叫（non-blocking, fire-and-forget）。

        Parameters
        ----------
        module_id : 模組識別（如 "trust.scoring", "agent.build_report"）
        latency_ms : 這次呼叫的延遲（ms）
        result : "success" / "failure" / "degraded"
        metadata : 選填額外資訊
        """
        try:
            ev = _WriteEvent(
                module_id=module_id,
                latency_ms=latency_ms,
                result=result,
                ts=time.time(),
                metadata=metadata,
            )
            self._queue.put_nowait(ev)
        except queue.Full:
            logger.debug("module_telemetry: queue full, dropping event for %s", module_id)
        except Exception:
            pass  # fail-silent

    def get_telemetry(self, module_id: str) -> TelemetryRecord | None:
        """查詢單一模組的遙測快照。"""
        try:
            conn = sqlite3.connect(self._db_path, timeout=3)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM module_telemetry WHERE module_id = ?", (module_id,)
            ).fetchone()
            conn.close()
            if row is None:
                return None
            return TelemetryRecord(
                module_id=row["module_id"],
                last_invoked_at=row["last_invoked_at"],
                invocation_count=row["invocation_count"],
                last_result=row["last_result"],
                avg_latency_ms=row["avg_latency_ms"],
                last_latency_ms=row["last_latency_ms"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            )
        except Exception:
            logger.warning("module_telemetry: get_telemetry failed for %s", module_id, exc_info=True)
            return None

    def get_all_telemetry(self) -> list[TelemetryRecord]:
        """查詢所有模組的遙測快照。"""
        try:
            conn = sqlite3.connect(self._db_path, timeout=3)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM module_telemetry ORDER BY last_invoked_at DESC"
            ).fetchall()
            conn.close()
            return [
                TelemetryRecord(
                    module_id=r["module_id"],
                    last_invoked_at=r["last_invoked_at"],
                    invocation_count=r["invocation_count"],
                    last_result=r["last_result"],
                    avg_latency_ms=r["avg_latency_ms"],
                    last_latency_ms=r["last_latency_ms"],
                    metadata=json.loads(r["metadata"]) if r["metadata"] else {},
                )
                for r in rows
            ]
        except Exception:
            logger.warning("module_telemetry: get_all_telemetry failed", exc_info=True)
            return []

    def shutdown(self) -> None:
        """關閉 writer thread（graceful）。"""
        if self._started:
            try:
                self._queue.put(None, timeout=2)
            except Exception:
                pass
            if self._writer_thread and self._writer_thread.is_alive():
                self._writer_thread.join(timeout=3)
            self._started = False


# --- 便捷函式（module-level，用 singleton）---------------------------------

def record_invocation(
    module_id: str,
    latency_ms: float,
    result: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    """記錄一次模組呼叫（module-level 便捷函式）。"""
    try:
        ModuleTelemetry.get_instance().record_invocation(
            module_id=module_id, latency_ms=latency_ms,
            result=result, metadata=metadata,
        )
    except Exception:
        pass  # telemetry 失敗不崩


def get_telemetry(module_id: str) -> TelemetryRecord | None:
    """查詢單一模組的遙測快照。"""
    try:
        return ModuleTelemetry.get_instance().get_telemetry(module_id)
    except Exception:
        return None


def get_all_telemetry() -> list[TelemetryRecord]:
    """查詢所有模組遙測。"""
    try:
        return ModuleTelemetry.get_instance().get_all_telemetry()
    except Exception:
        return []
