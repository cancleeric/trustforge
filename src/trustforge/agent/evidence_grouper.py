"""事實聚合引擎：將同源同指標的時序 Evidence 群組化，供呈現層使用。

Issue #862 — 非破壞式事實聚合與介面呈現優化。

設計原則：
  - 只讀 Evidence list，不修改任何既有資料
  - 輸出群組結構，保留所有原始索引（溯源）
  - 確定性規則，不呼叫 Bedrock
  - 不影響 evidence.json 輸出（完整保留）
  - 聚合規則可透過參數調整（time_window_days、similarity_threshold）
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from ..schema import Evidence

# ---------------------------------------------------------------------------
# 指標名稱與數值提取正則
# ---------------------------------------------------------------------------

# 匹配 "指標名: 數值 單位" 或 "指標名 = 數值 單位" 格式
# 支援中英文指標名稱、冒號/等號分隔
_METRIC_PATTERN = re.compile(
    r"([\w\u4e00-\u9fff\s/]+?)\s*[:：=]\s*([\d,.]+)\s*(\S*)"
)

# 獨立數值提取（fallback：句中任何 "數值 單位" 模式）
_NUMERIC_PATTERN = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*([A-Za-z%/\u4e00-\u9fff]+)"
)

# 時間戳解析格式（ISO8601 UTC）
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


# ---------------------------------------------------------------------------
# 資料結構
# ---------------------------------------------------------------------------

@dataclass
class EvidenceGroup:
    """一個聚合群組。

    representative_idx: 群組中 trust 最高者在原始 evidence list 中的索引
    member_indices: 所有原始 Evidence 索引（含代表自己）
    trend: "rising" / "falling" / "stable" / None
    value_range: "828–891 TH/s" 格式（數值型才填）
    latest_value: 最近一筆的數值摘要
    """
    representative_idx: int
    member_indices: list[int] = field(default_factory=list)
    trend: str | None = None
    value_range: str | None = None
    latest_value: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 內部工具函式
# ---------------------------------------------------------------------------

def _normalize_source(source: str) -> str:
    """來源正規化（strip + casefold），與 orchestrator._normalize_source_key 同口徑。"""
    return source.strip().casefold()


def _parse_fetched_at(fetched_at: str) -> float:
    """ISO8601 UTC → epoch 秒。解析失敗回 0.0。"""
    if not fetched_at:
        return 0.0
    try:
        dt = datetime.strptime(fetched_at, _ISO_FMT).replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, OverflowError):
        return 0.0


def _tokenize(text: str) -> set[str]:
    """文字 token 化（中英文 ≥2 字元的 token），複用 scoring._normalize 邏輯。"""
    return {t for t in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()) if len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度。任一邊為空集合視為不相似（0.0）。"""
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def extract_metric_key(content_reference: str) -> str | None:
    """嘗試從 content_reference 提取指標名稱。

    例如：
      "算力: 891 TH/s" → "算力"
      "Gas Fee = 12.5 Gwei" → "gas fee"
      "price: 67500 USD" → "price"

    回傳正規化後的指標名（lowercase, stripped）。無法提取回 None。
    """
    m = _METRIC_PATTERN.search(content_reference)
    if m:
        key = m.group(1).strip().lower()
        # 過濾過短或純數字的誤匹配
        if len(key) >= 2 and not key.isdigit():
            return key
    return None


def extract_numeric_value(content_reference: str) -> tuple[float, str] | None:
    """嘗試從 content_reference 提取數值與單位。

    回傳 (value, unit)，例如 (891.0, "TH/s")。
    無法提取回 None。
    """
    # 優先用 metric pattern（更精準）
    m = _METRIC_PATTERN.search(content_reference)
    if m:
        raw_val = m.group(2).replace(",", "")
        unit = m.group(3) or ""
        try:
            return (float(raw_val), unit)
        except ValueError:
            pass

    # Fallback: 任何 "數值 單位" 模式
    m = _NUMERIC_PATTERN.search(content_reference)
    if m:
        raw_val = m.group(1).replace(",", "")
        unit = m.group(2)
        try:
            return (float(raw_val), unit)
        except ValueError:
            pass
    return None


def compute_trend(values: list[tuple[float, float]]) -> str | None:
    """從 (timestamp, numeric_value) 序列計算趨勢方向。

    - len < 2 → None（無法判定）
    - 最新值 > 首值 × 1.02 → "rising"
    - 最新值 < 首值 × 0.98 → "falling"
    - 否則 → "stable"

    values 須按 timestamp 升序排列。
    """
    if len(values) < 2:
        return None
    first_val = values[0][1]
    last_val = values[-1][1]
    if first_val == 0:
        # 避免除以零：首值為零時若末值非零視為 rising
        return "rising" if last_val > 0 else ("falling" if last_val < 0 else None)
    ratio = last_val / first_val
    if ratio > 1.02:
        return "rising"
    elif ratio < 0.98:
        return "falling"
    else:
        return "stable"


def format_value_range(values: list[float], unit: str) -> str:
    """格式化值域字串。例如 [828.0, 855.0, 891.0] + "TH/s" → "828–891 TH/s"。"""
    if not values:
        return ""
    min_v = min(values)
    max_v = max(values)
    # 整數顯示不帶小數點，否則保留一位
    def _fmt(v: float) -> str:
        if v == int(v) and abs(v) < 1e12:
            return f"{int(v):,}"
        return f"{v:,.1f}"
    if min_v == max_v:
        return f"{_fmt(min_v)} {unit}".strip()
    return f"{_fmt(min_v)}–{_fmt(max_v)} {unit}".strip()


# ---------------------------------------------------------------------------
# 主聚合函式
# ---------------------------------------------------------------------------

def group_evidence(
    evidence: Sequence[Evidence],
    *,
    time_window_days: int = 7,
    similarity_threshold: float = 0.70,
) -> list[EvidenceGroup]:
    """將 Evidence list 聚合為群組。

    演算法：
      1. 按 (normalized_source, kind) 分桶
      2. 桶內按指標名稱匹配分子群
      3. 子群內用 Jaccard 相似度做 fallback 聚合
      4. 過濾例外（direction 相關條目由 related_claim 辨別、flagged 條目）
      5. 排除跨時間窗口的配對
      6. 每群組選 trust 最高者為 representative
      7. 計算 trend + value_range + latest_value
      8. 剩餘未被聚合的 evidence 各自獨立成一組

    保證：union(g.member_indices for g in groups) == set(range(len(evidence)))
    """
    if not evidence:
        return []

    n = len(evidence)
    time_window_sec = time_window_days * 86400

    # 標記哪些 evidence 應獨立（不參與聚合）
    # flagged = manipulation > 0 的條目
    independent: set[int] = set()
    for i, ev in enumerate(evidence):
        manip = ev.trust_components.get("manipulation", 0)
        if isinstance(manip, (int, float)) and manip > 0:
            independent.add(i)

    # Step 1: 按 (normalized_source, kind) 分桶
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, ev in enumerate(evidence):
        if i in independent:
            continue
        key = (_normalize_source(ev.source), ev.kind)
        buckets[key].append(i)

    # Step 2–5: 桶內聚合
    grouped_indices: set[int] = set()
    groups: list[EvidenceGroup] = []

    for _bucket_key, indices in buckets.items():
        if len(indices) < 2:
            # 單筆桶，跳過（後面統一處理未聚合的）
            continue

        # 按指標名稱再分子群
        metric_subgroups: dict[str, list[int]] = defaultdict(list)
        no_metric: list[int] = []

        for idx in indices:
            metric = extract_metric_key(evidence[idx].content_reference)
            if metric:
                metric_subgroups[metric].append(idx)
            else:
                no_metric.append(idx)

        # 處理有指標名稱的子群
        for _metric, sub_indices in metric_subgroups.items():
            _build_group_from_candidates(
                evidence, sub_indices, time_window_sec, groups, grouped_indices
            )

        # 處理無指標名稱的：用 Jaccard 相似度做 fallback 聚合
        if len(no_metric) >= 2:
            _jaccard_group(
                evidence, no_metric, similarity_threshold,
                time_window_sec, groups, grouped_indices
            )

    # Step 8: 未被聚合的 evidence 各自獨立成一組
    for i in range(n):
        if i not in grouped_indices:
            groups.append(EvidenceGroup(
                representative_idx=i,
                member_indices=[i],
                trend=None,
                value_range=None,
                latest_value=None,
            ))

    # 按 representative trust 降序排列
    groups.sort(key=lambda g: evidence[g.representative_idx].trust, reverse=True)

    return groups


def _build_group_from_candidates(
    evidence: Sequence[Evidence],
    candidates: list[int],
    time_window_sec: float,
    groups: list[EvidenceGroup],
    grouped_indices: set[int],
) -> None:
    """從候選 indices 中按時間窗口建立群組。"""
    if len(candidates) < 2:
        return

    # 按 fetched_at 排序
    sorted_cands = sorted(candidates, key=lambda i: _parse_fetched_at(evidence[i].fetched_at))

    # 檢查時間窗口：最早到最晚是否在窗口內
    ts_first = _parse_fetched_at(evidence[sorted_cands[0]].fetched_at)
    ts_last = _parse_fetched_at(evidence[sorted_cands[-1]].fetched_at)

    if ts_first > 0 and ts_last > 0 and (ts_last - ts_first) > time_window_sec:
        # 超出時間窗口：嘗試用滑動窗口切分
        _sliding_window_group(evidence, sorted_cands, time_window_sec, groups, grouped_indices)
        return

    # 全部在窗口內，建立一組
    _finalize_group(evidence, sorted_cands, groups, grouped_indices)


def _sliding_window_group(
    evidence: Sequence[Evidence],
    sorted_indices: list[int],
    time_window_sec: float,
    groups: list[EvidenceGroup],
    grouped_indices: set[int],
) -> None:
    """對超出單一時間窗口的候選集，用滑動窗口切分為多個群組。"""
    used: set[int] = set()
    for start_pos, start_idx in enumerate(sorted_indices):
        if start_idx in used:
            continue
        ts_start = _parse_fetched_at(evidence[start_idx].fetched_at)
        window: list[int] = [start_idx]
        for end_pos in range(start_pos + 1, len(sorted_indices)):
            end_idx = sorted_indices[end_pos]
            if end_idx in used:
                continue
            ts_end = _parse_fetched_at(evidence[end_idx].fetched_at)
            if ts_start > 0 and ts_end > 0 and (ts_end - ts_start) <= time_window_sec:
                window.append(end_idx)
            else:
                break
        if len(window) >= 2:
            _finalize_group(evidence, window, groups, grouped_indices)
            used.update(window)
        else:
            used.add(start_idx)


def _jaccard_group(
    evidence: Sequence[Evidence],
    candidates: list[int],
    threshold: float,
    time_window_sec: float,
    groups: list[EvidenceGroup],
    grouped_indices: set[int],
) -> None:
    """用 Jaccard 相似度對無指標名稱的候選做聚合。

    貪心：按 trust 降序掃描，每筆嘗試與已建群組的代表配對；
    配對成功（相似度 >= threshold 且時間窗口內）加入該群組；
    否則自起新群組。最後只保留 ≥ 2 筆的群組。
    """
    if len(candidates) < 2:
        return

    # 按 trust 降序
    sorted_cands = sorted(candidates, key=lambda i: evidence[i].trust, reverse=True)

    # token 快取
    token_cache: dict[int, set[str]] = {}
    for idx in sorted_cands:
        token_cache[idx] = _tokenize(evidence[idx].content_reference)

    local_groups: list[list[int]] = []

    for idx in sorted_cands:
        if idx in grouped_indices:
            continue
        placed = False
        ts_idx = _parse_fetched_at(evidence[idx].fetched_at)
        for grp in local_groups:
            rep_idx = grp[0]  # 群組代表（trust 最高，因為按 trust 降序掃描）
            ts_rep = _parse_fetched_at(evidence[rep_idx].fetched_at)
            # 時間窗口檢查
            if ts_idx > 0 and ts_rep > 0 and abs(ts_idx - ts_rep) > time_window_sec:
                continue
            # 相似度檢查
            sim = _jaccard(token_cache[idx], token_cache[rep_idx])
            if sim >= threshold:
                grp.append(idx)
                placed = True
                break
        if not placed:
            local_groups.append([idx])

    # 只保留 ≥ 2 筆的群組
    for grp in local_groups:
        if len(grp) >= 2:
            # 按 fetched_at 排序以計算趨勢
            grp_sorted = sorted(grp, key=lambda i: _parse_fetched_at(evidence[i].fetched_at))
            _finalize_group(evidence, grp_sorted, groups, grouped_indices)


def _finalize_group(
    evidence: Sequence[Evidence],
    member_indices: list[int],
    groups: list[EvidenceGroup],
    grouped_indices: set[int],
) -> None:
    """建立一個 EvidenceGroup 並更新 grouped_indices。"""
    if len(member_indices) < 2:
        return

    # 選 trust 最高者為代表
    rep_idx = max(member_indices, key=lambda i: evidence[i].trust)

    # 計算趨勢與值域
    time_values: list[tuple[float, float]] = []
    raw_values: list[float] = []
    unit = ""

    for idx in member_indices:
        ev = evidence[idx]
        extracted = extract_numeric_value(ev.content_reference)
        if extracted:
            val, u = extracted
            ts = _parse_fetched_at(ev.fetched_at)
            if ts > 0:
                time_values.append((ts, val))
            raw_values.append(val)
            if not unit and u:
                unit = u

    # 按時間排序
    time_values.sort(key=lambda x: x[0])

    trend = compute_trend(time_values)
    value_range = format_value_range(raw_values, unit) if raw_values else None
    latest_value: str | None = None
    if time_values:
        last_val = time_values[-1][1]
        latest_value = f"{last_val:,.1f} {unit}".strip() if unit else f"{last_val:,.1f}"

    group = EvidenceGroup(
        representative_idx=rep_idx,
        member_indices=list(member_indices),
        trend=trend,
        value_range=value_range,
        latest_value=latest_value,
    )
    groups.append(group)
    grouped_indices.update(member_indices)
