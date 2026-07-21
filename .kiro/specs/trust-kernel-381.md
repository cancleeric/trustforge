# Spec：抽出純 Trust Kernel，禁止 provider/LLM/IO 依賴滲入核心 (#381)

> Issue: #381
> Priority: P1-core
> Size: L
> Depends: #380（架構盤點）
> Aligns: #195, #196, #197
> Downstream: #386（PolicyProvider contract）

---

## Requirements

### R1: 版本化 Kernel input/output contract

- 定義 `KernelInput` dataclass：接受標準化、已綁定來源與 PIT（point-in-time）時間的 Evidence/Claim 集合
  - 每筆 Claim 必帶：`id`, `text`, `direction`, `source_kind`, `source_canonical`, `ts`, `meta`
  - 全域參數：`now_ts`（PIT 時間錨點）、`weights`（可選覆寫，預設 DEFAULT_WEIGHTS）
- 定義 `KernelOutput` dataclass：
  - 每筆結果含：`claim_id`, `trust_score`, `components`（reputation/corroboration/recency/manipulation 各分項）, `confidence_band`（abstain/low_confidence/normal）, `reason_codes: list[str]`
  - 聚合結果：`supporting`, `contrarian`, `confidence`, `calibrated_confidence`, `decision_state`
- contract schema 版本化（`KERNEL_SCHEMA_VERSION = "1.0.0"`），放入 `data_contracts.py`
- KernelInput/KernelOutput 序列化為 JSON 後可跨進程傳遞（dataclasses-json 或手動 `to_dict`/`from_dict`）

### R2: 純計算、零外部依賴

- Trust Kernel 的程式碼**禁止** import 以下模組/符號：
  - `trustforge.bedrock`（LLM）
  - `trustforge.ingestion.*`（IO / 連接器）
  - `trustforge.web`（UI）
  - `trustforge.skills`（Skill registry）
  - `trustforge.budget_guard`（部署控制）
  - `trustforge.ledger`（成本帳本）
  - `trustforge.agent.*`（Agent 編排）
  - `boto3` / `botocore`（AWS SDK）
  - `os.environ` / `os.getenv`（環境變數）
  - `urllib` / `http` / `socket`（網路）
  - `open()` / `pathlib.Path().read_*`（檔案 IO）
- 允許的依賴：`math`, `re`, `dataclasses`, `typing`, `logging`, `collections`, `functools`, 及 Trust Kernel 內部子模組
- `stance_fn` 作為 **inject 的 callable**（型別 `Callable[[str, str], str] | None`）從外層注入，Kernel 只呼叫、不自行建構

### R3: 純記憶體 fixture 可執行

- Trust Kernel 可用純 Python in-memory fixture 測試，無需 AWS/network/filesystem/env
- 提供 `tests/fixtures/kernel_fixtures.py`：至少 3 組 frozen input → expected output 的 fixture
- fixture 涵蓋：正常多源（≥3 獨立來源）、abstain（單源/稀少證據）、操縱懲罰（含 manip pattern 命中）

### R4: import-boundary test

- 新增 `tests/test_kernel_boundary.py`
- 使用 AST 掃描 `src/trustforge/trust/kernel/` 下所有 `.py` 的 import 語句
- 任何 import 命中 R2 禁止清單 → 測試 fail
- CI 執行此測試作為合併閘門

### R5: 既有測試全數通過（回歸鎖）

- `tests/test_trust_scoring.py` 全數通過，分數行為 byte-identical
- `tests/test_cross_source_signal.py`、`tests/test_tier2_divergence.py`、`tests/test_stance_budget_sharing.py` 全數通過
- 不改動任何既有公開 API 簽名（`score()`, `aggregate()`, `extract_claims()`, `build_stance_fn()`）

### R6: 可重現、byte-stable 核心結果

- 相同 frozen `KernelInput`（含 `now_ts`）→ 產出 bit-identical `KernelOutput`
- 測試用 `json.dumps(output.to_dict(), sort_keys=True)` 比對 SHA-256
- 不允許依賴 `PYTHONHASHSEED`、`time.time()`、random、外部 API 回傳值

### R7: 文件列出 immutable core controls 與 outer policy 邊界

- 新增 `docs/architecture/TRUST-KERNEL-BOUNDARY.md`
- 明確列出：
  - **Immutable Core**（禁止 outer skill/policy 修改）：trust weights、PIT time boundary、evidence binding、scoring formula、manipulation patterns、recency half-life
  - **Outer Policy**（可由 PolicyExecutor 調整）：stance_fn 實作切換（快取/線上）、stance 預算上限、迭代輪數上限、source alias 映射擴充
- 列出呼叫端（`agent/orchestrator.py`、`pipeline.py`）如何透過 adapter 呼叫 Kernel

### R8: PR 完成 /codex-review 與 commit-bound reviewer attestation

- PR 描述含 adversarial review section（injection / boundary bypass / 回歸面）
- 通過 `/codex-review`，所有 HIGH/MEDIUM finding 已解決
- commit-bound reviewer attestation 記錄在 PR

---

## Design

### 目錄結構

```
src/trustforge/trust/
├── kernel/                       ★ 新增 pure-compute sub-package
│   ├── __init__.py              # 公開 API: compute(), aggregate_brief()
│   ├── contract.py              # KernelInput, KernelOutput, KernelClaimInput, KernelClaimResult
│   ├── reputation.py            # _source_reputation (pure, 只吃 kind + canonical_source + dynamic_map)
│   ├── corroboration.py         # _corroboration, _corroboration_detail (pure, stance_fn 注入)
│   ├── recency.py               # _recency_decay (pure, now 注入)
│   ├── manipulation.py          # _manipulation_penalty, _manipulation_flags, _MANIP_PATTERNS
│   ├── direction.py             # _infer_direction, _directional_word_polarities (regex-only)
│   ├── calibration.py           # _evidence_strength, _calibrate_confidence, _CALIBRATION_TABLE
│   ├── aggregation.py           # aggregate() → TrustedBrief
│   ├── coordination.py          # _coordination_template_flags, _coordination_signals (info-only)
│   ├── dawid_skene.py           # em_source_reliability (move from trust/dawid_skene.py)
│   └── constants.py             # DEFAULT_WEIGHTS, KIND_REPUTATION, DOMAIN_STOP, _SOURCE_ALIASES...
├── scoring.py                   # 瘦殼 — 組裝 kernel 子模組 + 注入 stance_fn/IO 依賴
├── stance_cache.py              # 不動 — 屬 outer layer（IO/cache）
├── insights.py                  # 不動 — 純計算但依賴 kernel 輸出，留原位
├── conformal.py                 # 不動
└── __init__.py                  # 不動公開 API
```

### KernelInput / KernelOutput 合約

```python
# src/trustforge/trust/kernel/contract.py
from __future__ import annotations
from dataclasses import dataclass, field

KERNEL_SCHEMA_VERSION = "1.0.0"

@dataclass(frozen=True)
class KernelClaimInput:
    """Kernel 接受的單一主張輸入。已由外層完成 claim 抽取與來源綁定。"""
    id: str
    text: str
    direction: str                    # bullish | bearish | neutral
    source_kind: str                  # price | news | social | ...
    source_canonical: str             # _canonical_source() 已正規化
    ts: float                         # epoch seconds (PIT-bound)
    meta: dict = field(default_factory=dict)  # 來源覆寫信譽等

@dataclass(frozen=True)
class KernelConfig:
    """Kernel 執行組態。Immutable — 不得從環境變數讀取。"""
    now_ts: float
    weights: dict = field(default_factory=lambda: {
        "src": 0.50, "corr": 0.25, "rec": 0.15, "manip": 0.40
    })
    dynamic_reputation: bool = True
    reputation_iterations: int = 3
    offline: bool = False

@dataclass
class KernelClaimResult:
    """單一主張的評分結果。"""
    claim_id: str
    trust_score: float                # 0–1
    components: dict                  # reputation/corroboration/recency/manipulation
    decision_band: str               # abstain | low_confidence | normal
    reason_codes: list[str] = field(default_factory=list)
    reputation_trace: dict | None = None
    manip_flags: list[str] = field(default_factory=list)
    info_flags: list[str] = field(default_factory=list)

@dataclass
class KernelOutput:
    """Kernel 完整輸出。"""
    schema_version: str = KERNEL_SCHEMA_VERSION
    results: list[KernelClaimResult] = field(default_factory=list)
    # 聚合摘要
    supporting_ids: list[str] = field(default_factory=list)
    contrarian_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    calibrated_confidence: float = 0.0
    decision_state: str = "normal"    # abstain | low_confidence | normal
```

### 分離策略（Move + Thin Wrapper）

1. **scoring.py 現行函式拆分到 kernel/ 子模組**：每個分項（reputation / corroboration / recency / manipulation / direction / calibration / aggregation）獨立成一個 `.py`，只依賴 kernel 內部常數與 stdlib。
2. **scoring.py 變成 thin orchestration wrapper**：
   - import kernel 子模組
   - 注入 `stance_fn`（從 `stance_cache.py` 建構，需 IO/BedrockClient）
   - 注入 `now_ts`（從外層取得，可能是 `time.time()` 或 fixture）
   - 包裝回 `ScoredClaim` / `TrustedBrief`（既有公開 dataclass 不動，但內部委託 kernel 計算）
3. **既有公開 API 完全不變**：`score()`, `aggregate()`, `extract_claims()`, `build_stance_fn()` 簽名與回傳型別不動；呼叫端（`agent/orchestrator.py`, `pipeline.py`）零修改。

### 邊界圖

```
┌─────────────────────────────────────────────────────────────────────┐
│ Outer Layer（允許 IO/LLM/env/network）                               │
│  pipeline.py │ agent/orchestrator.py │ bedrock.py │ ingestion/*      │
│  stance_cache.py │ budget_guard.py │ ledger.py │ skills.py           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ inject: stance_fn, now_ts, config
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Trust Kernel（純計算，零外部依賴）                                     │
│  kernel/reputation.py │ kernel/corroboration.py │ kernel/recency.py  │
│  kernel/manipulation.py │ kernel/calibration.py │ kernel/aggregation │
│  kernel/coordination.py │ kernel/dawid_skene.py │ kernel/constants   │
│  kernel/contract.py │ kernel/direction.py                            │
└─────────────────────────────────────────────────────────────────────┘
       ▲ 禁止：boto3, bedrock, ingestion, web, skills, os.environ,
         urllib, open(), pathlib.read, budget_guard, ledger, agent
```

### 遷移步驟（建議 PR 拆分）

| PR | 內容 | 回歸風險 |
|----|------|----------|
| PR-A | 建 `kernel/` 骨架 + `contract.py` + `constants.py`；import-boundary test | 零（新增檔案） |
| PR-B | 移 `_source_reputation`, `_recency_decay`, `_manipulation_*`, `_infer_direction` 到 kernel/ | 中（函式位置變動） |
| PR-C | 移 `_corroboration*`, `_coordination_*`, `_evidence_strength`, `_calibrate_confidence`, `aggregate` 到 kernel/ | 高（核心邏輯） |
| PR-D | scoring.py 瘦殼化 + `KernelInput`/`KernelOutput` adapter + fixture 測試 + boundary doc | 中 |

每個 PR 獨立可合併、測試全綠後才進下一個。

---

## Tasks

### T1: 建立 kernel/ sub-package 骨架與 contract [PR-A]

- [ ] 建立 `src/trustforge/trust/kernel/__init__.py`（暴露 `compute`, `aggregate_brief`）
- [ ] 建立 `kernel/contract.py`：`KernelClaimInput`, `KernelConfig`, `KernelClaimResult`, `KernelOutput`
- [ ] 建立 `kernel/constants.py`：搬入 `DEFAULT_WEIGHTS`, `KIND_REPUTATION`, `KIND_HALFLIFE_HOURS`, `DOMAIN_STOP`, `_SOURCE_ALIASES`, `_MANIP_PATTERNS`, `_BULLISH_WORDS`, `_BEARISH_WORDS`
- [ ] 在 `data_contracts.py` 註冊 `KERNEL_SCHEMA_VERSION`
- [ ] 驗證：`pytest tests/` 全綠（純新增，不動既有）

### T2: import-boundary test [PR-A]

- [ ] 建立 `tests/test_kernel_boundary.py`
- [ ] AST 掃描 `trust/kernel/` 所有 `.py`，assert 無禁止 import
- [ ] CI 加入此測試

### T3: 移純計算函式到 kernel/ [PR-B + PR-C]

- [ ] `kernel/reputation.py`：`_source_reputation`, `_canonical_source`, `_reputation_floor`, `KIND_REPUTATION` 使用
- [ ] `kernel/recency.py`：`_recency_decay`（接受 `ts`, `now`, `half_life_h`）
- [ ] `kernel/manipulation.py`：`_manipulation_penalty`, `_manipulation_flags`, `_manip_hits`
- [ ] `kernel/direction.py`：`_infer_direction`, `_directional_word_polarities`, `_NEG_RX`
- [ ] `kernel/corroboration.py`：`_corroboration`, `_corroboration_detail`, `_normalize`, `_direction_compatible`, `_jaccard`
- [ ] `kernel/coordination.py`：`_coordination_template_flags`, `_coordination_signals`, `_coordination_burst_flags`, `_StanceBudget`
- [ ] `kernel/calibration.py`：`_evidence_strength`, `_calibrate_confidence`, `_CALIBRATION_TABLE`, `_STRENGTH_WEIGHTS`
- [ ] `kernel/aggregation.py`：`aggregate()` 核心邏輯（接受 `list[KernelClaimResult]`）
- [ ] `kernel/dawid_skene.py`：移入（已是純函式，零外部依賴）
- [ ] 每次移動後跑 `pytest -q`，確認 byte-identical 回歸鎖

### T4: scoring.py 瘦殼化 [PR-D]

- [ ] `scoring.py` 的 `score()` 改為：建構 `KernelConfig` → 呼叫 `kernel.compute()` → 包裝回 `ScoredClaim`
- [ ] `scoring.py` 的 `aggregate()` 改為：委託 `kernel.aggregation` → 包裝回 `TrustedBrief`
- [ ] 保留 `build_stance_fn()`, `extract_claims()`, `cached_stance_fn` 在 scoring.py（屬 outer layer，需 IO）
- [ ] 公開 API 簽名不動 — `trust/__init__.py` 維持既有 `__all__`

### T5: frozen fixture + byte-stability 測試 [PR-D]

- [ ] `tests/fixtures/kernel_fixtures.py`：3+ 組 frozen input → expected output
- [ ] `tests/test_kernel_determinism.py`：SHA-256 比對 `KernelOutput.to_dict()` 序列化結果
- [ ] 涵蓋：正常多源、abstain、manip 命中、動態信譽（DS EM fallback）

### T6: 邊界文件 [PR-D]

- [ ] `docs/architecture/TRUST-KERNEL-BOUNDARY.md`：immutable core vs outer policy 清單
- [ ] 列出 adapter 模式（scoring.py wrapper 如何注入 stance_fn / now_ts）
- [ ] 列出測試矩陣：kernel 純記憶體 + scoring.py 整合 + pipeline end-to-end

### T7: 最終回歸驗證

- [ ] `pytest -q` 全綠（含既有 13+ 測試 + 新增 boundary/determinism 測試）
- [ ] `python -m trustforge.cli analyze --coin BTC --type multi_source --query "..." --offline --out out/btc` 成功產出 4 交付件
- [ ] PR 通過 `/codex-review`，commit-bound reviewer attestation

---

## 明確不做（引用 Issue #381）

- 不改 production deployment
- 不允許 outer skill 修改 trust weights / time boundary / evidence binding / security / cost gates
- 不在本單更換模型或調整評分效度
- 不重構 `agent/orchestrator.py` 或 `pipeline.py` 的簽名
