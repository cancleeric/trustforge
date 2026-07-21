"""純 Trust Kernel facade（Phase 1 — PR-A）。

封裝信任評分的純計算邏輯，不依賴 IO/LLM/cache/boto3。
外層透過此介面呼叫，以後可漸進抽出為獨立 sub-package（見 spec T3/T4）。

本模組作為 immutable core 的穩定介面層：
- 公開的計算函式（evaluate / aggregate / em_source_reliability）
- 核心常數（DEFAULT_WEIGHTS / KIND_REPUTATION / KIND_HALFLIFE_HOURS）
- 資料型別 re-export（Claim / ScoredClaim / TrustedBrief）

呼叫端（agent/orchestrator.py、pipeline.py）可透過本模組存取所有
信任評分的純計算功能，而不需直接依賴 scoring.py 的內部結構。

禁止事項（R2 — 零外部依賴邊界）：
  本模組本身只做 re-export，不引入任何 IO/LLM/cache/boto3/env 依賴。
  日後演進為 kernel/ sub-package 時，所有子模組也須遵守同一邊界。
"""
from __future__ import annotations

# === 核心計算函式 ===
from .scoring import (
    extract_claims,
    score,
    aggregate,
)

# === Dawid-Skene EM 動態信譽 ===
from .dawid_skene import em_source_reliability

# === 核心常數 ===
from .scoring import (
    DEFAULT_WEIGHTS,
    KIND_REPUTATION,
    KIND_HALFLIFE_HOURS,
)

# === 資料型別 ===
from .scoring import (
    Claim,
    ScoredClaim,
    TrustedBrief,
)

# === Schema 版本 ===
KERNEL_SCHEMA_VERSION = "1.0.0"

__all__ = [
    # 計算函式
    "extract_claims",
    "score",
    "aggregate",
    "em_source_reliability",
    # 常數
    "DEFAULT_WEIGHTS",
    "KIND_REPUTATION",
    "KIND_HALFLIFE_HOURS",
    "KERNEL_SCHEMA_VERSION",
    # 資料型別
    "Claim",
    "ScoredClaim",
    "TrustedBrief",
]
