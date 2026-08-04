"""Generic execution event log primitives.

The public application can map these records to product-specific workflow
graphs outside this module.  This layer only owns run/event/step structure,
JSONL compatibility, and defensive secret redaction.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


REDACTED = "[REDACTED]"
_SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


@dataclass(frozen=True)
class ExecutionStepRecord:
    """Generic execution step metadata."""

    step_id: str
    label: str = ""
    order: int = 0
    status: str = "observed"


@dataclass(frozen=True)
class ExecutionEventRecord:
    """One provider-neutral execution event."""

    ts: str
    elapsed_sec: float
    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    step: ExecutionStepRecord | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return the existing execution_log.jsonl event shape."""

        return {
            "ts": self.ts,
            "elapsed_sec": self.elapsed_sec,
            "tool": self.tool,
            "params": self.params,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class ExecutionRunRecord:
    """Generic execution run envelope."""

    run_id: str
    started_at: str
    elapsed_sec: float
    budget_sec: int
    steps: list[ExecutionStepRecord] = field(default_factory=list)


class ExecutionEventLog:
    """In-memory generic event log with JSONL compatibility serializer."""

    def __init__(self, run_id: str, started_at: str, budget_sec: int):
        self.run_id = run_id
        self.started_at = started_at
        self.budget_sec = budget_sec
        self.events: list[ExecutionEventRecord] = []

    def append(
        self,
        *,
        ts: str,
        elapsed_sec: float,
        tool: str,
        params: dict[str, Any] | None = None,
        summary: str = "",
        step: ExecutionStepRecord | None = None,
    ) -> ExecutionEventRecord:
        event = ExecutionEventRecord(
            ts=ts,
            elapsed_sec=round(float(elapsed_sec), 2),
            tool=tool,
            params=redact_secrets(params or {}),
            summary=summary,
            step=step,
        )
        self.events.append(event)
        return event

    def to_jsonl(self) -> str:
        return serialize_legacy_jsonl(self.events)

    def manifest(self, *, elapsed_sec: float | None = None) -> ExecutionRunRecord:
        elapsed = self.events[-1].elapsed_sec if elapsed_sec is None and self.events else 0.0
        if elapsed_sec is not None:
            elapsed = round(float(elapsed_sec), 2)
        return ExecutionRunRecord(
            run_id=self.run_id,
            started_at=self.started_at,
            elapsed_sec=elapsed,
            budget_sec=self.budget_sec,
            steps=[event.step for event in self.events if event.step is not None],
        )


def serialize_legacy_jsonl(events: list[ExecutionEventRecord]) -> str:
    """Serialize events using the existing JSONL contract."""

    return "\n".join(json.dumps(event.to_legacy_dict(), ensure_ascii=False) for event in events)


def record_to_dict(record: ExecutionRunRecord) -> dict[str, Any]:
    """Convert run envelope to a JSON-compatible dict."""

    return asdict(record)


def redact_secrets(value: Any) -> Any:
    """Recursively redact obvious secrets from JSON-like structures."""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    if lowered in _SECRET_KEY_MARKERS:
        return True
    parts = set(filter(None, re.split(r"[_\W]+", lowered)))
    return any(marker in parts for marker in _SECRET_KEY_MARKERS)


# ── Public execution-log allowlist (deny-by-default) ───────────────────────
# #943：對外公開的 payload 一律只投影下列欄位。``params`` 整包預設不公開——只
# 透過 :func:`_public_params` 投影**列舉的公開子鍵**（hermes workflow context +
# ingestion.source 來源摘要）；其餘 params 子鍵（含 url ``?token=``、api_key、
# wallet、未列舉欄位、free-text summary 內誤帶的 secret）一律丟棄。**allowlist
# 才是主防線**；下方 :func:`_scrub_summary` 與既有 :func:`redact_secrets` 僅作
# 第二層 defense-in-depth，絕不能用來取代 allowlist。
PUBLIC_EVENT_FIELDS = frozenset(
    {"ts", "elapsed_sec", "tool", "summary", "step", "node_id", "cost"}
)

# #943 curated params sub-allowlist（deny-by-default 投影）。前端 HermesExecutionPanel /
# AnalyzePage 讀取的公開欄位才列舉；其餘 params 子鍵一律不公開。
_PUBLIC_HERMES_KEYS = frozenset(
    {"node_id", "node_label", "node_order", "status", "run_id", "agent"}
)
_PUBLIC_INGESTION_SOURCE_KEYS = frozenset(
    {"source", "kind", "coin", "duration_ms", "document_count", "outcome", "data_mode"}
)


def _public_params(event: dict) -> dict:
    """把 event 的 ``params`` 投影成**只含列舉公開子鍵**的 dict（deny-by-default）。

    - ``params.hermes``：**每個 event 都投影**——前端 HermesExecutionPanel 無條件讀
      ``event.params.hermes``，缺鍵會 throw（legacy 快照 reproject 路徑特別易踩），
      故 hermes 缺/非 dict 時投影成 ``{}`` 而非省略。只保留 :data:`_PUBLIC_HERMES_KEYS`
      中的鍵。
    - ``tool == "ingestion.source"``：額外投影 :data:`_PUBLIC_INGESTION_SOURCE_KEYS`
      中存在於 params 的鍵（前端來源摘要表讀取）。
    - 其餘 params 子鍵（api_key/url/wallet/未列舉鍵…）一律不公開。
    恆回傳含 ``hermes`` 的 dict（呼叫端不再判空），確保公開 payload 的
    ``event.params.hermes`` 永遠安全可讀。
    """
    params = event.get("params")
    hermes = params.get("hermes") if isinstance(params, dict) else None
    out: dict[str, Any] = {
        "hermes": {
            k: hermes[k]
            for k in _PUBLIC_HERMES_KEYS
            if isinstance(hermes, dict) and k in hermes
        }
    }
    if event.get("tool") == "ingestion.source" and isinstance(params, dict):
        for k in _PUBLIC_INGESTION_SOURCE_KEYS:
            if k in params:
                out[k] = params[k]
    return out

# Defense-in-depth：對投影後的 ``summary`` 字串做 value scrub，把 token-like 明顯
# secret 形樣換成 [REDACTED]。**主防線是上方 allowlist（直接丟棄 params）；這組
# pattern 只堵 ``summary`` 本身誤帶 secret 的邊角。**
# colon-form ``key: value`` scrub 的 key 名集合：對齊 :data:`_SECRET_KEY_MARKERS`
# （api_key/apikey/auth/authorization/credential/password/secret/token 全數納入），
# 並補 passwd/access_token/key 等常見短別名以與上方 URL query-param scrub 覆蓋面
# 一致——避免 ``token: SECRET`` / ``auth: SECRET`` 等 colon 形樣原樣外露。
_COLON_SECRET_KEY_ALT = "|".join(
    _SECRET_KEY_MARKERS + ("passwd", "pw", "access_token", "key")
)
# Match the same marker semantics inside composite names that ``_is_secret_key``
# recognises (for example ``client_secret`` and ``refresh-token``).  Components
# must be separator-delimited so innocent names such as ``monkey`` do not match
# the short ``key`` marker.
_STRUCTURED_SECRET_KEY = (
    rf"(?:[A-Za-z0-9]+[._-])*(?:{_COLON_SECRET_KEY_ALT})"
    r"(?:[._-][A-Za-z0-9]+)*"
)
# OAuth ``code`` is sensitive only as the exact URL parameter name.  Letting it
# participate in composite expansion would incorrectly redact ordinary
# ``status_code`` / ``country_code`` diagnostics.
_URL_STRUCTURED_SECRET_KEY = rf"(?:code|{_STRUCTURED_SECRET_KEY})"
_TOKEN_LIKE_PATTERNS = (
    # URL query param：?token=... / &api_key=... / ?access_token=... —— 保留前綴只遮值。
    re.compile(
        rf"([?&]{_URL_STRUCTURED_SECRET_KEY}=)[^&#\s]+",
        re.IGNORECASE,
    ),
    # ``bearer <token>``
    re.compile(r"(bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    # header / ``key: value``：authorization: ... / password: ... / token: ... ——
    # key 名集合對齊 _SECRET_KEY_MARKERS（經 _COLON_SECRET_KEY_ALT 展開）。
    re.compile(
        rf"({_STRUCTURED_SECRET_KEY}:\s*)[^\s,;]+",
        re.IGNORECASE,
    ),
    # Assignment form outside URL query strings: ``key=value`` / ``pw='value'``.
    # Requiring a recognised secret marker avoids redacting ordinary ``name=value``
    # diagnostics.  URL query forms are handled by the first pattern above.
    re.compile(
        rf"((?<![?&A-Za-z0-9_]){_STRUCTURED_SECRET_KEY}\s*=\s*)"
        r'(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'
        r"'(?:\\[^\r\n]|[^'\\\r\n])*'|[^\s,;]+)",
        re.IGNORECASE,
    ),
    # Standard padded base64 blobs (minimum 16 encoded characters).  Padding is
    # required deliberately so ordinary identifiers/prose are not over-redacted.
    re.compile(
        r"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{4}){3,}"
        r"(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)"
        r"(?![A-Za-z0-9+/=])"
    ),
    # 長 hex（≥32）：JWT segment / SHA256 / API key hex。
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
)


def _scrub_summary(summary: Any) -> Any:
    """Defense-in-depth：把 ``summary`` 字串中的 token-like 值換成 [REDACTED]。

    **主防線是 :data:`PUBLIC_EVENT_FIELDS` allowlist**（透過 :func:`_public_params`
    直接丟棄未列舉的 ``params`` 子鍵，包含 api_key/url/wallet 等敏感值）；本函式僅是
    第二層 defense-in-depth，處理 ``summary`` 本身誤帶 secret 的邊角，**絕不能取代
    allowlist**。

    覆蓋的形樣見 :data:`_TOKEN_LIKE_PATTERNS`：URL ``?token=``、``bearer``、
    ``key: value`` colon-form、secret-marker ``key=value`` assignment、標準 padded
    base64，以及 ≥32 long-hex。短 hex 若位於 secret-marker assignment 中，也會由
    assignment 規則遮蔽；無 marker 的任意 free text 仍不可能由 regex 完整辨識。

    非 str 輸入原樣回傳（投影層不對型別做強轉）。
    """
    if not isinstance(summary, str):
        return summary
    for pattern in _TOKEN_LIKE_PATTERNS:
        summary = pattern.sub(
            lambda m: (m.group(1) if m.lastindex else "") + REDACTED, summary
        )
    return summary


def to_public_event_dict(event: dict) -> dict:
    """把單一原始 event 投影成**只含公開欄位**的 dict（deny-by-default）。

    只投影 :data:`PUBLIC_EVENT_FIELDS` 中**存在**的鍵（缺的不補）；``params``
    只透過 :func:`_public_params` 投影列舉的公開子鍵（hermes + ingestion.source
    摘要），其餘 params 子鍵與任何未列舉欄位一律丟棄。``params`` 恆存在（至少含
    ``hermes`` 子鍵，確保前端 ``event.params.hermes`` 永不 throw）。投影後對
    ``summary`` 跑 :func:`_scrub_summary`，再對整份 dict 跑一次 :func:`redact_secrets`
    作雙保險。
    """
    public = {key: event[key] for key in PUBLIC_EVENT_FIELDS if key in event}
    if "summary" in public:
        public["summary"] = _scrub_summary(public["summary"])
    public["params"] = _public_params(event)
    return redact_secrets(public)


def to_public_events(events: list[dict]) -> list[dict]:
    """批量 :func:`to_public_event_dict`，供公開 payload 統一套用。"""
    return [to_public_event_dict(event) for event in events]
