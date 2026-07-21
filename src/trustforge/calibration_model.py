"""純 Python isotonic regression 校準模型（Issue #343）。

取代硬編碼 `_CALIBRATION_TABLE`，用 backfill training data 學出
confidence → hit_rate 的單調遞增映射。不引入 sklearn，全用 PAV 演算法。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def train_isotonic(confidences: list[float], hit_flags: list[bool]) -> list[dict]:
    """純 Python isotonic regression（PAV 演算法）。

    輸入：信心值列表 + 對應的 hit/miss boolean
    輸出：校準映射點 [{"confidence": x, "calibrated": y}, ...]

    PAV (Pool Adjacent Violators)：
    1. 按 confidence 排序
    2. 計算每個點的 hit_rate（hit=1, miss=0）
    3. 從左到右，如果後一個點的 hit_rate < 前一個，合併取平均
    4. 結果是單調遞增的映射

    若輸入為空回傳空列表。
    """
    if not confidences or not hit_flags:
        return []
    if len(confidences) != len(hit_flags):
        raise ValueError("confidences 和 hit_flags 長度不一致")

    # Step 1: 按 confidence 排序，建立 (confidence, value) 對
    pairs = sorted(zip(confidences, hit_flags), key=lambda p: p[0])

    # Step 2: 建立 blocks：每個 block 是 {sum, count, confidence_sum}
    # value: hit=1.0, miss=0.0
    blocks: list[dict] = []
    for conf, hit in pairs:
        blocks.append({
            "value_sum": 1.0 if hit else 0.0,
            "count": 1,
            "conf_sum": conf,
        })

    # Step 3: PAV — 從左到右合併違反單調性的相鄰 blocks
    merged: list[dict] = []
    for block in blocks:
        merged.append(block)
        # 持續合併直到單調遞增
        while len(merged) >= 2:
            curr = merged[-1]
            prev = merged[-2]
            curr_rate = curr["value_sum"] / curr["count"]
            prev_rate = prev["value_sum"] / prev["count"]
            if curr_rate < prev_rate:
                # 合併：prev 吸收 curr
                prev["value_sum"] += curr["value_sum"]
                prev["count"] += curr["count"]
                prev["conf_sum"] += curr["conf_sum"]
                merged.pop()
            else:
                break

    # Step 4: 輸出校準點（每個 block 的平均 confidence → 平均 hit_rate）
    # 去重：同一個 confidence 值只保留一個點（PAV 可能產出多個相同 confidence 的 block）
    points: list[dict] = []
    for block in merged:
        avg_conf = block["conf_sum"] / block["count"]
        avg_rate = block["value_sum"] / block["count"]
        point = {
            "confidence": round(avg_conf, 6),
            "calibrated": round(avg_rate, 6),
        }
        # 去重：如果與前一個點 confidence 相同，合併（取最後的 calibrated）
        if points and points[-1]["confidence"] == point["confidence"]:
            points[-1] = point
        else:
            points.append(point)

    return points


def save_calibration_model(
    points: list[dict], path: Path, sample_count: int
) -> None:
    """存模型到 JSON。

    格式：
    {
      "points": [{"confidence": x, "calibrated": y}, ...],
      "trained_at": ISO timestamp,
      "sample_count": int
    }
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "points": points,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": sample_count,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_calibration_model(path: Path) -> list[dict] | None:
    """讀取模型，不存在或格式錯誤回 None。"""
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        points = data.get("points")
        if not isinstance(points, list) or len(points) < 2:
            return None
        # 驗證格式
        for p in points:
            if "confidence" not in p or "calibrated" not in p:
                return None
        return points
    except (json.JSONDecodeError, OSError):
        return None


def apply_calibration(raw_confidence: float, model: list[dict]) -> float:
    """用模型映射 confidence（線性插值）。

    - raw_confidence clamp 到 [0, 1]
    - model 必須按 confidence 遞增排序（train_isotonic 保證）
    - 超出模型邊界則用邊界值
    - 兩個最近點之間用線性插值
    """
    if not model:
        return raw_confidence

    x = max(0.0, min(1.0, raw_confidence))

    # 邊界處理
    if x <= model[0]["confidence"]:
        return model[0]["calibrated"]
    if x >= model[-1]["confidence"]:
        return model[-1]["calibrated"]

    # 線性插值：找到 x 落在哪兩個點之間
    for i in range(len(model) - 1):
        x0 = model[i]["confidence"]
        x1 = model[i + 1]["confidence"]
        if x0 <= x <= x1:
            if x1 == x0:
                return model[i]["calibrated"]
            ratio = (x - x0) / (x1 - x0)
            y0 = model[i]["calibrated"]
            y1 = model[i + 1]["calibrated"]
            return round(y0 + ratio * (y1 - y0), 4)

    # 防禦性：理論上不會到這
    return round(x, 4)
