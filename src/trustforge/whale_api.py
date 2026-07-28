"""大額轉帳（Whale Alert）API 聚合邏輯。

提供兩支函式供 server.py 路由呼叫：
  - `whale_summary(coin, backend)` → 從 cache 讀最新一批 whale Document，聚合統計
  - `whale_history(coin, days, archive_path)` → 從 SourceEventArchive 讀歷史
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ingestion.cache import CacheBackend, cache_get, cache_key


def whale_summary(coin: str, backend: CacheBackend) -> dict[str, Any]:
    """從 cache 讀 whale-alert 最新一批 Document，聚合為即時摘要。

    回傳結構見 spec design.md `/api/whale-summary` 段落。
    無資料時回傳空結構（total_count=0），不拋例外。
    """
    coin = coin.upper() if coin else "BTC"
    key = cache_key("whale-alert", coin)
    entry = cache_get(backend, key)

    empty: dict[str, Any] = {
        "coin": coin,
        "period_hours": 1,
        "total_count": 0,
        "total_usd": 0,
        "net_exchange_flow_usd": 0,
        "exchange_inflow_usd": 0,
        "exchange_outflow_usd": 0,
        "max_single_usd": 0,
        "whale_transfer_count": 0,
        "exchange_inflow_count": 0,
        "exchange_outflow_count": 0,
        "recent_transfers": [],
        "updated_at": None,
        "signal": "no_data",
        "signal_label": "暫無大額轉帳紀錄",
    }

    if entry is None:
        return empty

    docs: list[dict] = entry.get("docs", [])
    fetched_at = entry.get("fetched_at", 0.0)

    if not docs:
        empty["updated_at"] = _format_ts(fetched_at)
        return empty

    total_usd = 0.0
    max_single = 0.0
    exchange_inflow = 0.0
    exchange_outflow = 0.0
    inflow_count = 0
    outflow_count = 0
    whale_count = 0

    for doc in docs:
        meta = doc.get("meta", {})
        amount_usd = meta.get("amount_usd", 0) or 0
        direction = meta.get("direction", "")

        total_usd += amount_usd
        if amount_usd > max_single:
            max_single = amount_usd

        if direction == "exchange_inflow":
            exchange_inflow += amount_usd
            inflow_count += 1
        elif direction == "exchange_outflow":
            exchange_outflow += amount_usd
            outflow_count += 1
        else:
            whale_count += 1

    net_flow = exchange_inflow - exchange_outflow

    # 取最近 5 筆（按 ts 降序）
    sorted_docs = sorted(docs, key=lambda d: d.get("ts", 0), reverse=True)
    recent = []
    for doc in sorted_docs[:5]:
        meta = doc.get("meta", {})
        recent.append({
            "amount_usd": meta.get("amount_usd", 0),
            "coin": meta.get("coin", coin),
            "from": meta.get("from", "unknown"),
            "to": meta.get("to", "unknown"),
            "direction": meta.get("direction", "unknown"),
            "ts": doc.get("ts", 0),
        })

    # 推導信號
    if net_flow < -1_000_000:
        signal = "exchange_net_outflow"
        signal_label = "淨流出交易所（囤積訊號）"
    elif net_flow > 1_000_000:
        signal = "exchange_net_inflow"
        signal_label = "淨流入交易所（賣壓訊號）"
    else:
        signal = "neutral"
        signal_label = "交易所流動中性"

    return {
        "coin": coin,
        "period_hours": 1,
        "total_count": len(docs),
        "total_usd": round(total_usd, 2),
        "net_exchange_flow_usd": round(net_flow, 2),
        "exchange_inflow_usd": round(exchange_inflow, 2),
        "exchange_outflow_usd": round(exchange_outflow, 2),
        "max_single_usd": round(max_single, 2),
        "whale_transfer_count": whale_count,
        "exchange_inflow_count": inflow_count,
        "exchange_outflow_count": outflow_count,
        "recent_transfers": recent,
        "updated_at": _format_ts(fetched_at),
        "signal": signal,
        "signal_label": signal_label,
    }


def whale_history(coin: str, days: int, archive_path: Path | str | None = None) -> dict[str, Any]:
    """從 SourceEventArchive（SQLite）讀歷史 whale-alert Document，聚合為時序+明細。

    `days` 限制 1/7/30，其他值強制為 7。
    回傳結構見 spec design.md `/api/whale-history` 段落。
    """
    import sqlite3

    coin = coin.upper() if coin else "BTC"
    if days not in (1, 7, 30):
        days = 7

    if archive_path is None:
        from .source_archive import _default_path
        archive_path = _default_path()
    archive_path = Path(archive_path)

    empty: dict[str, Any] = {
        "coin": coin,
        "days": days,
        "available_since": None,
        "summary": {
            "total_count": 0,
            "total_usd": 0,
            "net_exchange_flow_usd": 0,
            "max_single_usd": 0,
        },
        "timeline": [],
        "transfers": [],
    }

    if not archive_path.exists():
        return empty

    cutoff = time.time() - days * 86400

    try:
        conn = sqlite3.connect(str(archive_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT fetched_at, raw_payload_json
            FROM source_events
            WHERE source_id = 'whale-alert'
              AND coin = ?
              AND fetched_at >= ?
              AND quality_state = 'accepted'
            ORDER BY fetched_at DESC
            """,
            (coin, cutoff),
        ).fetchall()
        conn.close()
    except Exception:
        return empty

    if not rows:
        return empty

    # 解析所有 Document
    all_transfers: list[dict] = []
    earliest_ts = time.time()

    for row in rows:
        fetched_at = row["fetched_at"]
        if fetched_at < earliest_ts:
            earliest_ts = fetched_at
        try:
            payload = json.loads(row["raw_payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        # payload 可能是單個 doc dict 或 list
        doc_list = payload if isinstance(payload, list) else [payload]
        for doc in doc_list:
            if not isinstance(doc, dict):
                continue
            meta = doc.get("meta", {})
            all_transfers.append({
                "amount_usd": meta.get("amount_usd", 0) or 0,
                "amount": meta.get("amount", 0) or 0,
                "coin": meta.get("coin", coin),
                "from": meta.get("from", "unknown"),
                "to": meta.get("to", "unknown"),
                "direction": meta.get("direction", "unknown"),
                "ts": doc.get("ts", 0) or fetched_at,
                "tx_url": doc.get("url", ""),
            })

    # 去重（同一筆轉帳可能在多次 fetch 中出現）
    seen_keys: set[str] = set()
    unique_transfers: list[dict] = []
    for t in all_transfers:
        key = f"{t['ts']}-{t['amount_usd']}-{t['from']}-{t['to']}"
        if key not in seen_keys:
            seen_keys.add(key)
            unique_transfers.append(t)

    unique_transfers.sort(key=lambda x: x["ts"], reverse=True)

    # Summary
    total_usd = sum(t["amount_usd"] for t in unique_transfers)
    max_single = max((t["amount_usd"] for t in unique_transfers), default=0)
    net_flow = sum(
        t["amount_usd"] if t["direction"] == "exchange_inflow" else
        -t["amount_usd"] if t["direction"] == "exchange_outflow" else 0
        for t in unique_transfers
    )

    # Timeline buckets
    timeline: list[dict] = []
    if days <= 1:
        # 按小時聚合
        buckets: dict[str, dict] = {}
        for t in unique_transfers:
            bucket_key = datetime.fromtimestamp(t["ts"], tz=timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
            if bucket_key not in buckets:
                buckets[bucket_key] = {"bucket": bucket_key, "count": 0, "total_usd": 0, "net_flow_usd": 0}
            buckets[bucket_key]["count"] += 1
            buckets[bucket_key]["total_usd"] += t["amount_usd"]
            if t["direction"] == "exchange_inflow":
                buckets[bucket_key]["net_flow_usd"] += t["amount_usd"]
            elif t["direction"] == "exchange_outflow":
                buckets[bucket_key]["net_flow_usd"] -= t["amount_usd"]
        timeline = sorted(buckets.values(), key=lambda b: b["bucket"], reverse=True)
    else:
        # 按天聚合
        buckets = {}
        for t in unique_transfers:
            bucket_key = datetime.fromtimestamp(t["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
            if bucket_key not in buckets:
                buckets[bucket_key] = {"bucket": bucket_key, "count": 0, "total_usd": 0, "net_flow_usd": 0}
            buckets[bucket_key]["count"] += 1
            buckets[bucket_key]["total_usd"] += t["amount_usd"]
            if t["direction"] == "exchange_inflow":
                buckets[bucket_key]["net_flow_usd"] += t["amount_usd"]
            elif t["direction"] == "exchange_outflow":
                buckets[bucket_key]["net_flow_usd"] -= t["amount_usd"]
        timeline = sorted(buckets.values(), key=lambda b: b["bucket"], reverse=True)

    return {
        "coin": coin,
        "days": days,
        "available_since": _format_ts(earliest_ts),
        "summary": {
            "total_count": len(unique_transfers),
            "total_usd": round(total_usd, 2),
            "net_exchange_flow_usd": round(net_flow, 2),
            "max_single_usd": round(max_single, 2),
        },
        "timeline": timeline,
        "transfers": unique_transfers[:100],  # 最多 100 筆明細
    }


def _format_ts(ts: float) -> str | None:
    """epoch → ISO 8601 UTC 字串。"""
    if not ts or ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
