"""模組 runtime 遙測（Module Runtime Telemetry）。

為 TrustForge 的 21+ 模組提供真實 runtime evidence：
- last_invoked_at：最後一次呼叫的 ISO timestamp
- invocation_count：累計呼叫次數
- last_result：最後一次呼叫結果（success / failure / degraded）
- avg_latency_ms：平均延遲（毫秒）
- state：模組生命週期狀態（registered → configured → resolved → invoked → verified）

持久化用 SQLite（`out/module-telemetry.sqlite3`），寫入為背景
threading，失敗不影響核心 pipeline。
"""
from __future__ import annotations

import enum
import logging
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .telemetry_store import (
    SQLiteTelemetryStore,
    TelemetryStore,
    TelemetryStoreEvent,
    TelemetryStoreRecord,
    default_telemetry_db_path,
)


class ModuleState(str, enum.Enum):
    """模組生命週期狀態。

    正常流程：registered → configured → resolved → invoked → verified
    異常狀態：disabled / blocked / degraded / failed / stale
    """
    registered = "registered"
    configured = "configured"
    resolved = "resolved"
    invoked = "invoked"
    verified = "verified"
    disabled = "disabled"
    blocked = "blocked"
    degraded = "degraded"
    failed = "failed"
    stale = "stale"

logger = logging.getLogger(__name__)

# 預設 SQLite 位置
_DEFAULT_DB_PATH = default_telemetry_db_path()

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
    state: str = ModuleState.registered.value  # 模組生命週期狀態
    evidence_ref: str = ""  # verified 狀態的佐證參考
    metadata: dict[str, Any] = field(default_factory=dict)


class _WriteEvent:
    """Background writer 消費的事件。"""
    __slots__ = (
        "module_id", "latency_ms", "result", "ts", "metadata", "state",
        "evidence_ref", "count_invocation",
    )

    def __init__(self, module_id: str, latency_ms: float, result: str,
                 ts: float, metadata: dict[str, Any] | None = None,
                 state: str = ModuleState.invoked.value,
                 evidence_ref: str = "",
                 count_invocation: bool = True):
        self.module_id = module_id
        self.latency_ms = latency_ms
        self.result = result
        self.ts = ts
        self.metadata = metadata or {}
        self.state = state
        self.evidence_ref = evidence_ref
        self.count_invocation = count_invocation


class ModuleTelemetry:
    """Thread-safe 模組遙測 singleton。

    寫入走 background thread（async write），讀取直接走 SQLite（immutable snapshot ok）。
    """

    _instance: "ModuleTelemetry | None" = None
    _lock = threading.Lock()

    def __init__(self, db_path: str | None = None, store: TelemetryStore | None = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._store = store or SQLiteTelemetryStore(self._db_path)
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
            self._store.initialize()
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
            self._store.write_batch([_to_store_event(ev) for ev in batch])
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
                state=ModuleState.invoked.value,
            )
            self._queue.put_nowait(ev)
        except queue.Full:
            logger.debug("module_telemetry: queue full, dropping event for %s", module_id)
        except Exception:
            pass  # fail-silent

    def record_verified(
        self,
        module_id: str,
        evidence_ref: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """記錄模組已通過驗證（state → verified）。

        Parameters
        ----------
        module_id : 模組識別
        evidence_ref : 驗證佐證（如測試名稱、CI run URL、程式碼位置）
        metadata : 選填額外資訊
        """
        try:
            ev = _WriteEvent(
                module_id=module_id,
                latency_ms=0.0,
                result="verified",
                ts=time.time(),
                metadata=metadata,
                state=ModuleState.verified.value,
                evidence_ref=evidence_ref,
                count_invocation=False,
            )
            self._queue.put_nowait(ev)
        except queue.Full:
            logger.debug("module_telemetry: queue full, dropping verified event for %s", module_id)
        except Exception:
            pass  # fail-silent

    def record_state(
        self,
        module_id: str,
        state: ModuleState | str,
        evidence_ref: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a module lifecycle state without counting it as an invocation."""
        try:
            normalized_state = ModuleState(state).value
            ev = _WriteEvent(
                module_id=module_id,
                latency_ms=0.0,
                result=normalized_state,
                ts=time.time(),
                metadata=metadata,
                state=normalized_state,
                evidence_ref=evidence_ref,
                count_invocation=False,
            )
            self._queue.put_nowait(ev)
        except queue.Full:
            logger.debug("module_telemetry: queue full, dropping state event %s", module_id)
        except Exception:
            pass  # fail-silent

    def get_telemetry(self, module_id: str) -> TelemetryRecord | None:
        """查詢單一模組的遙測快照。"""
        try:
            rec = self._store.get(module_id)
            return _from_store_record(rec) if rec is not None else None
        except Exception:
            logger.warning("module_telemetry: get_telemetry failed for %s", module_id, exc_info=True)
            return None

    def get_all_telemetry(self) -> list[TelemetryRecord]:
        """查詢所有模組的遙測快照。"""
        try:
            return [_from_store_record(rec) for rec in self._store.list_all()]
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


def _to_store_event(ev: _WriteEvent) -> TelemetryStoreEvent:
    return TelemetryStoreEvent(
        subject_id=ev.module_id,
        latency_ms=ev.latency_ms,
        result=ev.result,
        ts=ev.ts,
        metadata=ev.metadata,
        state=ev.state,
        evidence_ref=ev.evidence_ref,
        count_invocation=ev.count_invocation,
    )


def _from_store_record(rec: TelemetryStoreRecord) -> TelemetryRecord:
    return TelemetryRecord(
        module_id=rec.subject_id,
        last_invoked_at=rec.last_invoked_at,
        invocation_count=rec.invocation_count,
        last_result=rec.last_result,
        avg_latency_ms=rec.avg_latency_ms,
        last_latency_ms=rec.last_latency_ms,
        state=rec.state,
        evidence_ref=rec.evidence_ref,
        metadata=rec.metadata,
    )


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


def record_state(
    module_id: str,
    state: ModuleState | str,
    evidence_ref: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """記錄模組生命週期狀態（module-level 便捷函式）。"""
    try:
        ModuleTelemetry.get_instance().record_state(
            module_id=module_id,
            state=state,
            evidence_ref=evidence_ref,
            metadata=metadata,
        )
    except Exception:
        pass  # telemetry 失敗不崩


def record_verified(
    module_id: str,
    evidence_ref: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """記錄模組已通過驗證（module-level 便捷函式）。"""
    try:
        ModuleTelemetry.get_instance().record_verified(
            module_id=module_id, evidence_ref=evidence_ref,
            metadata=metadata,
        )
    except Exception:
        pass  # telemetry 失敗不崩
