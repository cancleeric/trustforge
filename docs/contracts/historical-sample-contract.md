# Historical Sample Contract — TrustForge 共用 Evidence/Outcome 格式

> Milestone 2 交付。本 contract 同時服務 #195（source-reliability trainer）與
> #197（conformal backtest），確保兩者使用同一套 PIT 安全的樣本格式。
>
> 版本：v1｜日期：2026-07-27｜設計：Eric Wang（颶風軟體 CEO）

---

## 一、Sample Schema（JSONL，每行一個 sample）

```json
{
  "sample_id": "sha256(coin + as_of + source + direction + horizon)",
  "coin": "BTC",
  "as_of": "2026-01-01T00:00:00Z",
  "source": "alternative-me-fng",
  "provider": "Alternative.me",
  "source_family": "sentiment",
  "scope": "market-wide",
  "claim_direction": "bearish",
  "evidence_strength": 0.61,
  "outcome_horizon": "T+7",
  "outcome_direction": "bearish",
  "outcome_observed_at": "2026-01-08T00:00:00Z",
  "lineage_hash": "sha256-of-all-input-artifacts",
  "training_cutoff": "2026-07-27"
}
```

| 欄位 | 必填 | 型別 | 說明 |
|------|------|------|------|
| `sample_id` | ✅ | string | `sha256(f"{coin}:{as_of}:{source}:{claim_direction}:{outcome_horizon}")`[:16] |
| `coin` | ✅ | string | BTC/ETH/SOL/BNB/XRP |
| `as_of` | ✅ | ISO 8601 | 分析時間點；之後的資料不進入 evidence |
| `source` | ✅ | string | 來源名稱（如 `alternative-me-fng`、`blockchain-com-charts`、`ohlcv-csv`） |
| `provider` | ✅ | string | Provider 名稱（如 `Alternative.me`、`Blockchain.com`） |
| `source_family` | ✅ | enum | `sentiment` / `onchain` / `price` / `regulatory` |
| `scope` | ✅ | enum | `market-wide` / `per-coin` |
| `claim_direction` | ✅ | enum | `bullish` / `bearish` / `neutral` |
| `evidence_strength` | ✅ | float 0..1 | 證據強度；從 replay 的 `calibrated_confidence` 或原始 evidence 計算 |
| `outcome_horizon` | ✅ | string | `T+1` / `T+7` / `T+14` |
| `outcome_direction` | ✅ | enum | `bullish` / `bearish` / `neutral`；從 OHLCV 計算 |
| `outcome_observed_at` | ✅ | ISO 8601 | outcome 實際能觀測到的日期（T+N） |
| `lineage_hash` | ✅ | string | 所有輸入 artifact 的 composite SHA-256（見第六節） |
| `training_cutoff` | ✅ | UTC date | 執行時指定的 `YYYY-MM-DD` inclusive cutoff；晚於此日期的 evidence 排除 |

Additional（選填，供下游 #197 使用）：

| 欄位 | 型別 | 說明 |
|------|------|------|
| `source_count` | int | 該 sample 參與的異質來源數（同 source_family 只算一次） |
| `abstain` | bool | 該 sample 是否為系統 abstain（方向=neutral/不明） |
| `confidence_raw` | float 0..1 | 未校準的原始 confidence（若與 evidence_strength 不同） |
| `market_regime` | string | `bull` / `bear` / `sideways`（依 as_of 前 30 日趨勢判定） |

---

## 二、三個 Input 格式 → Contract Mapping

### 2.1 FNG JSONL → Contract

| JSONL 欄位 | Contract 欄位 | 轉換規則 |
|---|---|---|
| `published_at` | `as_of` | 直接對應 |
| `coin` | `coin` | 直接對應（FNG 是 market-wide，**展開到多幣只增加 scope="market-wide" 標記，不虛增為獨立 source**） |
| `source` | `source` | `alternative-me-fng` |
| `provider` | `provider` | `Alternative.me` |
| `value` | — | FNG 值域 0-100，用於計算 claim_direction |
| `classification` | — | Extreme Fear/Fear → bearish；Extreme Greed/Greed → bullish；Neutral → neutral |
| — | `source_family` | 固定 `sentiment` |
| — | `scope` | 固定 `market-wide` |
| — | `evidence_strength` | `clamp(0.5 + abs(value - 50) / 100, 0.5, 0.85)` |
| — | `outcome_direction` | 從 OHLCV 計算（見 2.3） |

**FNG 展開約束**：JSONL 中同一個 `published_at` 有 6 筆（BTC/ETH/SOL/BNB/XRP/ARB），
**只產出一個 sample**（`coin=BTC`，因 BTC 為全市場代表性資產），
`scope=market-wide`。不為每個幣各產一個 sample——那會虛增樣本數 6 倍。

### 2.2 Replay Output → Contract

| Replay 欄位 | Contract 欄位 | 轉換規則 |
|---|---|---|
| `snapshot_at` | `as_of` | 直接對應 |
| `coin` | `coin` | 直接對應 |
| `report.direction` | `claim_direction` | bullish/bearish/neutral（若為「不明」→ `claim_direction="neutral"` + `abstain=true`） |
| `report.calibrated_confidence` | `evidence_strength` | 直接使用 |
| `evidence[].source` | `source` | 取 evidence 清單中第一個 source 名稱 |
| `evidence[].visible_at` / `published_at` / `fetched_at` | — | 依此前後順序選取第一個存在的欄位；必須是帶時區的 ISO 8601，且不能晚於 `as_of` |
| — | `source_family` | 從 `evidence[].kind` 對映：`sentiment`/`onchain`/`price`/`regulatory` |
| — | `scope` | 從 `evidence[].meta.get("scope", "per-coin")` |
| — | `source_count` | 從 `evidence[]` 長度計算（去重 source_family 後） |

### 2.3 OHLCV → Outcome Direction

```
ret = close[T+N] / close[T] - 1
|ret| ≤ 3%  → neutral
ret > +3%  → bullish
ret < -3%  → bearish
```

`outcome_observed_at` = as_of + N days（以 UTC 日期表示）。

T+N 超出資料範圍 → 該 sample 不產出 outcome（outcome_direction = null，由下游決定是否排除）。

---

## 三、Sample 建構 Pipeline

```
                               ┌──────────────┐
  out/history/*.jsonl ────────→│              │
  out/replay/five-year-* ─────→│  build_samples.py  │──→ out/samples/historical_samples.jsonl
  data/data/*_daily_ohlcv.csv ─→│              │
                               └──────────────┘
```

### Pipeline 步驟

1. **Load FNG data**：從 JSONL 建 `{published_at: {value, classification}}` index。
   只保留 BTC 行（market-wide 約束）。

2. **Load replay data**：從 `out/replay/five-year-{coin}/*.json` 讀 daily snapshots。
   對每個 day，提取 report direction 與 calibrated_confidence。

3. **Load OHLCV**：用 `outcome_labeler.py` 的 `label_n_day_direction()` 計算 outcome（N=1/7/14）。

4. **Join & validate**：
   - 每個 `(coin, as_of, source, source_family)` 各自保留一列；同日
     sentiment/onchain/price 不互相覆寫
   - PIT gate：evidence visibility timestamp 必須存在、格式有效且 `≤ as_of`
   - Replay 採 snapshot-level fail-closed：任何一筆 evidence 是 malformed、
     missing/invalid/future timestamp，整個 snapshot（包括原本有效 evidence 與
     report direction/confidence）全部拒收，並在 CLI summary 分類計數
   - `snapshot.coin` 必填、須為支援幣別且等於本次 requested coin；否則整個
     snapshot 拒收並計入 `snapshot_coin_mismatch`
   - 單一輸入檔上限 32 MiB、JSONL 單行上限 1 MiB、replay 最多 10,000 個
     JSON 檔；超限、Unicode/JSON/recursion parse failure 均 fail-closed 並計數
   - Scope gate：`scope=market-wide` 的 FNG sample 不因 coin-expanded rows
     重複產生；Blockchain.com 僅接受 BTC
   - cutoff gate：`as_of` 的 UTC 日期必須 `≤ --cutoff YYYY-MM-DD`（inclusive）

5. **Output**：以 `as_of/coin/source_family/source/sample_id` 固定排序，每行一個
   JSON object。相同輸入、cutoff 與 horizon 必須 byte-for-byte deterministic。

輸入中的 `report` 與 `evidence` 必須本身就是 JSON object/array。JSON string、
Python literal 或其他序列化形式一律視為 malformed；pipeline 不使用
`eval()`、`exec()` 或 literal evaluation。Outcome 僅在 feature 建構完成後依
T+N OHLCV 加入輸出，`outcome_direction` 與 `outcome_observed_at` 不得回流至
claim、strength、source 或其他 feature。

---

## 四、對 #195 與 #197 的分別影響

| Contract 欄位 | #195（source-reliability） | #197（conformal backtest） |
|---|---|---|
| `sample_id` | ✅ | ✅ |
| `coin` | ✅ | ✅ |
| `as_of` | ✅（PIT gate） | ✅（split point） |
| `source` / `provider` / `source_family` | ✅（逐 source 分組算 reliability） | ✅（異質 source count） |
| `claim_direction` | ✅（對齊 outcome 算 accuracy） | ✅（supporting/contrarian 判斷） |
| `evidence_strength` | ✅（用於 Brier 校準） | ✅（用於 conformal threshold τ） |
| `outcome_direction` | ✅（ground truth） | ✅（判定 correct/wrong） |
| `outcome_horizon` | ✅（T+7 primary） | ✅（T+3 primary） |
| `source_count` | — | ✅（異質度計量） |
| `abstain` | ✅（排除） | ✅（不計入 wrong，但計入 abstain rate） |
| `market_regime` | — | ✅（分 regime 評估） |

---

## 五、⛔ 不做的事

1. **不修改 scoring.py** — 本 contract 是資料層，不影響 production trust score
2. **不新增 DB schema** — 輸出為 JSONL files，用檔案系統管理
3. **不因本 contract 就 wire #197** — 仍需通過 promotion threshold
4. **不把舊 replay output 格式刪除** — contract 是新增層，不改 existing schema
5. **FNG 不展開到六幣** — scope=market-wide 的 sample 只產一份（對齊 BTC）

---

## 六、Lineage Hash 生成規則

```python
def lineage_hash(files: list[str]) -> str:
    composite = b""
    for f in sorted(files):
        composite += hashlib.sha256(Path(f).read_bytes()).digest()
    return hashlib.sha256(composite).hexdigest()
```

參與 lineage 的 artifact：

| Input | File | 說明 |
|------|------|------|
| FNG raw | `out/history/alternative-me-fng-*.jsonl` | 原始 API 回應 |
| Replay snapshots | `out/replay/five-year-{coin}/index.json` | 全幣 replay index |
| OHLCV | `data/data/{coin}_daily_ohlcv.csv` | 價格資料 |
| Contract version | `docs/contracts/historical-sample-contract.md` | 本文件 SHA-256（固化格式版本） |

實作必須對不存在的 lineage artifact fail closed，不得以 `MISSING` placeholder
產生看似有效的 digest。Replay directory 中所有 JSON artifacts 都參與 digest。

---

## 七、驗收條件

- [x] pipeline 可從明確 JSON/schema 與 OHLCV 產生 contract JSONL
- [x] 同日 2+ source families 以不同 rows 保留
- [x] PIT gate 排除並計數 missing/invalid/future evidence
- [x] 任一 evidence 違反 PIT/schema 時整個 replay snapshot fail-closed
- [x] snapshot coin identity 與 requested coin 一致
- [x] FNG market-wide 同日期只產生一個 sample，不因多幣展開
- [x] Blockchain 僅接受 BTC
- [x] abstain samples 標記但不排除
- [x] lineage/sample ID/ordering 可重現
- [x] UTC `YYYY-MM-DD` cutoff 為 inclusive
