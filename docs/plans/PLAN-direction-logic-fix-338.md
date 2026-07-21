# PLAN: 方向判定邏輯修復 (#338)

> 作者：gray（CPO）
> 日期：2026-07-21
> Issue: #338 (P0-critical)
> Spec: `.kiro/specs/direction-logic-338.md`

---

## A. 問題根因分析

### A1. 缺陷本質

`src/trustforge/agent/orchestrator.py` 第 189–210 行的 `_direction()` 函式：

```python
def _direction(supporting: list[ScoredClaim]) -> str:
    for sc in supporting:
        if sc.claim.doc.kind == "price":
            t = sc.claim.text
            if "上漲" in t:
                return "偏多"
            if "下跌" in t:
                return "偏空"
            if "盤整" in t:
                return "中性"
    return "中性"  # ← 99%+ 的情境走到這裡
```

問題：這段邏輯假設 `price` kind 的 claim 文字中一定包含「上漲」「下跌」「盤整」這三個精確字詞。但實際 OHLCV 連接器產生的 claim 文字是「BTC 今日收盤 46637.08 美元」「14 天報酬率 -7.4%」這類**數值敘述**，根本不會出現那三個關鍵字。結果：所有分析一律走到 `return "中性"`。

### A2. 為什麼做了幾個月才發現

| 原因 | 說明 |
|------|------|
| 預設回傳值是合法輸出 | `"中性"` 是三種方向之一，不會觸發異常/錯誤、不會讓 pipeline 崩潰，從外表看「系統正常運作」 |
| 缺乏方向分佈監控 | 沒有 assertion / CI gate 檢查「5 幣種 × 多次分析的方向分佈不可以全是中性」 |
| 信任層是核心焦點 | 團隊注意力集中在 TrustScore（信譽/佐證/時效/操縱）、W1.5 stance、W2 Dawid-Skene、W3 協同偵測、W4 校準——方向判定被視為「簡單的下游消費者」而忽略 |
| 測試用硬編資料 | `test_comparison.py::test_report_direction_field_set` 只斷言 `direction in ("偏多", "偏空", "中性")`，因為 "中性" 本就合法，測試永遠通過 |
| 離線樣本走不同路徑 | `demo/sample_data/` 中的合成 claim 有些恰好含「上漲」字串，demo 跑出方向看起來正確，掩蓋了真實 OHLCV 路徑的問題 |

### A3. 哪些審查環節漏了

1. **Code Review 時未檢查硬編碼字串與上游資料格式的對齊** — `_direction()` 被寫的時候沒人問「price kind 的 claim 文字長什麼樣？」
2. **Integration Test 覆蓋不足** — 測試只用合成資料 + 合法值斷言，沒有用真實 OHLCV 走完整管線再驗方向
3. **無 QA 分佈斷言** — 缺少「跑 N 次分析、方向分佈至少要有 2 種以上不同值」的端到端測試
4. **回歸測試只驗型別不驗語意** — `assert report.direction in ("偏多", "偏空", "中性")` 是形式正確性，非語意正確性

### A4. 系統測試為什麼沒抓到

- `test_trust_scoring.py` 測的是 `_infer_direction()`（scoring.py 的純函式），不是 orchestrator 的 `_direction()`
- `test_comparison.py::test_report_direction_field_set` 用 monkeypatch 餵合成資料，claim 文字手動包含「上漲」
- **沒有任何測試使用真實 OHLCV CSV → ingestion → scoring → orchestrator 的完整路徑**
- **沒有任何測試斷言「給定明確漲幅/跌幅的 OHLCV，方向必須是偏多/偏空」**

---

## B. 審查標準（永久門檻，以後不再發生）

### B1. 核心演算法修改的 Acceptance Criteria

任何對 `_direction()` 或方向判定邏輯的修改，PR 描述必須包含：

| # | 標準 | 量化門檻 |
|---|------|----------|
| 1 | 輸入來源覆蓋 | 必須列出「此函式的輸入實際長什麼樣」的 3+ 個真實範例（不可只用合成/理想化文字） |
| 2 | 三態分佈驗證 | 用 5 幣種的真實 OHLCV 跑 ≥20 個不同日期窗口，輸出必須涵蓋「偏多」「偏空」「中性」三種，且任一種佔比 ≤ 80% |
| 3 | 對齊文件 | Spec (`direction-logic-338.md`) 的每個 R（R1/R2/R3）都有對應的通過測試案例 |
| 4 | 回歸不破壞 | 既有 `tests/` 全通過、覆蓋率不降 |

### B2. 方向判定的正確性定義（可量化）

**Ground Truth 定義：**

| 方向 | 定義（14 天 close-to-close 報酬率） |
|------|------|
| 偏多 | 14 天報酬率 > +3% |
| 偏空 | 14 天報酬率 < -3% |
| 中性 | -3% ≤ 報酬率 ≤ +3% |

**正確性指標（Layer 1 price_trend_direction）：**

- **準確率 ≥ 95%** — 給定一段 OHLCV 序列，函式輸出與上述 ground truth 一致的比例
- **三態覆蓋** — 100 天隨機抽樣中，三種方向各至少出現 5 次（否則說明閾值不合理）
- **確定性** — 同一輸入必同一輸出（無隨機性）

### B3. 回歸測試門檻

| 測試檔 | 最低案例數 | 內容 |
|--------|-----------|------|
| `tests/test_direction_logic.py`（新建） | 12 | Layer 1: 漲 >3% → 偏多、跌 >3% → 偏空、盤整 → 中性、邊界 ±3%、無價格資料 → None |
| 同上 | 8 | Layer 2: 多源 stance 加權（≥2 獨立源 bullish → 偏多、bearish → 偏空、不足 2 源 fallback） |
| 同上 | 4 | 整合: Layer 2 有效時優先、fallback 到 Layer 1、兩者皆失敗 → 不明 |
| `tests/test_direction_distribution.py`（新建） | 1 | 端到端：讀真實 `data/BTC_daily_ohlcv.csv`，抽 50 天窗口，斷言三態分佈至少各 ≥3 |

### B4. Code Review 必檢查清單（方向判定相關 PR）

- [ ] `_direction()` 的每個分支是否有對應的測試案例？
- [ ] 硬編碼字串（如中文關鍵字）是否與上游來源的實際輸出格式對齊？附上至少 3 個真實來源文字範例
- [ ] 是否有「全部走進預設分支」的失敗模式？如何偵測？
- [ ] 使用真實 OHLCV 執行一次完整管線（不是只用 monkeypatch 的合成資料），截圖/日誌附在 PR
- [ ] 方向分佈 CI 檢查是否仍通過（`test_direction_distribution.py`）？
- [ ] 修改是否影響 `calibrated_confidence`、`decision_state`、`abstain` 判斷？若是，附 diff 說明

---

## C. 修復方案（分層）

### Layer 1（必做）：OHLCV 報酬率方向

**修改位置：** `src/trustforge/agent/orchestrator.py`

**新增函式：**

```python
def _price_trend_direction(supporting: list[ScoredClaim]) -> str | None:
    """從 OHLCV meta 計算 14 天報酬率方向。

    搜尋 supporting 中 kind='price' 的 claims，提取 doc.meta 的 close 值序列，
    按日期排序，算 (最近 close - 14天前 close) / 14天前 close。
    > +3% → "偏多"
    < -3% → "偏空"
    中間 → "中性"
    價格資料不足 14 天 → None（不判定）
    """
```

**邏輯：**
1. 從 `supporting` 過濾 `sc.claim.doc.kind == "price"` 的所有 scored claims
2. 收集 `sc.claim.doc.meta` 中的 `{"date": ..., "close": ...}` 資料點
3. 按日期排序，取最近值與 14 天前值
4. 算報酬率 `(close_recent - close_14d_ago) / close_14d_ago`
5. 套閾值回傳方向或 None

**不依賴中文關鍵字。** 純數學運算，確定性，可測試。

**驗收標準：**
- 給定 BTC 2024-01-01 到 2024-01-14 的真實 OHLCV（close 從 42000 漲到 46000，報酬率 +9.5%），回傳「偏多」
- 給定 2022-05-01 到 2022-05-14 的真實 OHLCV（Luna 崩盤期間），回傳「偏空」
- 給定橫盤區間（報酬率 -1.5%），回傳「中性」
- 價格資料只有 3 天 → 回傳 None
- 100 天隨機抽樣的三態分佈：偏多 ≥ 15%、偏空 ≥ 15%、中性 ≥ 10%

### Layer 2（應做）：多源 stance 加權方向

**修改位置：** 同 `orchestrator.py`

**新增函式：**

```python
def _stance_consensus_direction(supporting: list[ScoredClaim]) -> str | None:
    """多源 stance 加權方向共識。

    收集 supporting 中 claim.direction != "neutral" 的 claims，
    用 trust_score 做加權：
    - bullish_weight = Σ(trust_score for direction=="bullish")
    - bearish_weight = Σ(trust_score for direction=="bearish")

    判定：
    - 獨立來源 ≥ 2 且 bullish_weight > bearish_weight × 1.3 → "偏多"
    - 獨立來源 ≥ 2 且 bearish_weight > bullish_weight × 1.3 → "偏空"
    - 否則 → None（不判定，交由 fallback）
    """
```

**獨立來源計數：** 用 `_canonical_source()` 去重，與 `_corroboration` 口徑一致。

**驗收標準：**
- 3 個獨立來源（CoinGecko sentiment=bullish + Cointelegraph news=bullish + 社群=neutral），bullish 加權和 > bearish × 1.3 → 回傳「偏多」
- 2 個獨立來源 bullish + 1 個 bearish，但 bearish × 1.3 > bullish → 回傳「偏空」
- 只有 1 個獨立來源有方向 → 回傳 None（不足 2 源）
- 勢均力敵（bullish 1.0 vs bearish 0.9，0.9 × 1.3 = 1.17 > 1.0）→ 回傳 None

### Layer 3（後續 / #339）：Dawid-Skene 信譽加權

**概念：** 用 `trust/dawid_skene.py` 的 `em_source_reliability` 對方向標籤做加權，取代 Layer 2 的等權 trust_score 加權。

**先決條件：**
- Layer 1 + Layer 2 上線且穩定
- DS EM 離線 fallback 的行為在此情境下經驗證（目前 DS 用於信任校準，不確定方向判定是否需要不同的 `min_raters_per_item`）
- 回歸確認 Layer 1/2 的測試不受影響

**驗收標準（定義於此，實作在 #339）：**
- 對「3 個獨立來源中有 1 個歷史上頻繁出錯」的情境，DS 動態降低其權重，使共識方向偏向其餘 2 個可靠來源
- 5 年回測的方向準確率（相對 Layer 2 無 DS）提升 ≥ 2 個百分點
- 迭代輪數硬上限 ≤ 5（已有 `MAX_REPUTATION_ITERATIONS`）

### 重寫後的 `_direction()`

```python
def _direction(supporting: list[ScoredClaim]) -> str:
    """多層方向判定：多源共識 > 價格趨勢 > 不明。"""

    # Layer 2: 多源 stance 共識（需 ≥3 獨立來源有方向才用）
    stance_dir = _stance_consensus_direction(supporting)
    if stance_dir and _count_directional_sources(supporting) >= 3:
        return stance_dir

    # Layer 1: OHLCV 價格趨勢（確定性，不依賴 claim 文字）
    price_dir = _price_trend_direction(supporting)
    if price_dir:
        return price_dir

    # 兩層都算不出 → 回傳「不明」（非「中性」！「不明」= 資料不足以判定）
    return "不明"
```

**「不明」vs「中性」語意差異：**
- 「中性」= 有足夠資料，判定結果為中性（如 14 天報酬率 ±2%）
- 「不明」= 資料不足以做出任何判定

---

## D. 驗證計劃

### D1. 修改前：記錄 baseline

**執行指令：**
```bash
python -m trustforge.cli analyze --coin BTC --type multi_source \
    --query "分析 BTC 過去兩週市場狀況" --offline --out out/baseline/btc
python -m trustforge.cli analyze --coin ETH --type multi_source \
    --query "分析 ETH 過去兩週市場狀況" --offline --out out/baseline/eth
python -m trustforge.cli analyze --coin SOL --type multi_source \
    --query "分析 SOL 過去兩週市場狀況" --offline --out out/baseline/sol
```

**記錄：**
- 每個幣種的 `report.direction` 值
- 預期結果：全部是「中性」（確認 bug 復現）
- 儲存到 `out/baseline/direction_matrix.json`

### D2. 修改後：跑 QA matrix 確認三種方向分佈

**執行同上三條指令（指向不同 out 目錄）：**
```bash
# 跑 5 幣種
for coin in BTC ETH SOL BNB XRP; do
    python -m trustforge.cli analyze --coin $coin --type multi_source \
        --query "分析 ${coin} 過去兩週市場狀況" --offline --out out/post-fix/$coin
done
```

**驗收門檻：**
- 5 幣種輸出中，至少 2 種不同方向值出現
- 如果 5 個全是中性 → 修復失敗，不可合併

### D3. 回測：5 年 OHLCV 抽樣 100 天

**測試腳本（新增 `scripts/backtest_direction.py`）：**

```python
"""方向判定回測：從 5 年 OHLCV 中抽樣 100 個 14 天窗口，驗證方向判定正確性。"""
import random
import csv
from pathlib import Path

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP"]
DATA_DIR = Path("data")
THRESHOLD = 0.03  # ±3%
SAMPLE_SIZE = 100  # 總抽樣數（5 幣各 20）

def load_ohlcv(coin: str) -> list[dict]:
    path = DATA_DIR / f"{coin}_daily_ohlcv.csv"
    with open(path) as f:
        return list(csv.DictReader(f))

def ground_truth_direction(rows: list[dict], end_idx: int) -> str:
    """回傳 14 天窗口的 ground truth 方向。"""
    start_idx = end_idx - 14
    if start_idx < 0:
        return "insufficient"
    close_start = float(rows[start_idx]["close"])
    close_end = float(rows[end_idx]["close"])
    ret = (close_end - close_start) / close_start
    if ret > THRESHOLD:
        return "偏多"
    elif ret < -THRESHOLD:
        return "偏空"
    else:
        return "中性"

# 每幣抽 20 個隨機窗口，驗證 _price_trend_direction 輸出與 ground truth 一致
```

**驗收門檻：**

| 指標 | 門檻 |
|------|------|
| Layer 1 準確率 | ≥ 95%（100 樣本中 ≥ 95 個與 ground truth 一致） |
| 偏多出現次數 | ≥ 10 / 100 |
| 偏空出現次數 | ≥ 10 / 100 |
| 中性出現次數 | ≥ 5 / 100 |
| 不明（None）出現次數 | 0（回測時保證有 14 天資料） |

### D4. 持續 CI：方向分佈檢查

**新增 `tests/test_direction_distribution.py`：**

```python
"""CI 門檻：方向判定對真實 OHLCV 必須產生三種方向分佈。"""
import pytest
from pathlib import Path

@pytest.mark.skipif(not Path("data/BTC_daily_ohlcv.csv").exists(),
                    reason="需要官方 OHLCV 資料")
def test_direction_distribution_not_all_neutral():
    """從真實 BTC OHLCV 抽 50 個 14 天窗口，方向不可以全部相同。"""
    # 實作：讀 CSV → 構造 50 個 price Document → 呼叫 _price_trend_direction
    # 斷言：set(directions) 至少有 2 個不同值
    ...

def test_direction_three_states_achievable():
    """合成 3 組資料（明確漲/跌/盤），確認三態都可達。"""
    # 合成 close 序列：[100→110]、[100→90]、[100→101]
    # 呼叫 _price_trend_direction，分別斷言偏多/偏空/中性
    ...
```

**CI 配置：** 在 `pytest.ini` 或 CI workflow 中確保 `test_direction_distribution.py` 在有 `data/` 的環境下執行（App Runner 部署前的 smoke test）。

---

## E. 時程

| 日期 | 里程碑 | 負責 | 產出 |
|------|--------|------|------|
| D+0（今天） | 本計劃通過 CEO 審批 | gray | 本文件 |
| D+1 | baseline 記錄 + Task 1 實作 (`_price_trend_direction`) | 開發 | 函式 + 6 個單元測試通過 |
| D+1 | Task 3 重寫 `_direction()` (先只串 Layer 1) | 開發 | 用真實 OHLCV 執行確認不全是中性 |
| D+2 | Task 2 實作 (`_stance_consensus_direction`) | 開發 | 函式 + 8 個單元測試通過 |
| D+2 | Task 3 完整串接 Layer 1 + 2 | 開發 | `_direction()` 最終版 |
| D+2 | Task 4：回測腳本 + CI 分佈測試 | 開發 | `scripts/backtest_direction.py` + `tests/test_direction_distribution.py` 通過 |
| D+3 | Task 5：QA matrix 驗證 + PR 提交 | 開發 | 5 幣種跑完、方向分佈 ≥ 2 種值 |
| D+3 | Code Review（用 B4 清單逐項檢查） | 審查者 | PR 核可 |
| D+4 | 合併 main + 部署驗證 | 開發 | production health check 通過 |
| 後續 | Layer 3 (DS 信譽加權) 開 #339 | gray 規劃 | 獨立 spec + plan |

**總工時預估：** 3–4 個工作天（Layer 1+2+測試+回測+驗證）。

---

## F. 風險與緩解

| 風險 | 嚴重性 | 緩解 |
|------|--------|------|
| 改方向判定影響所有下游（calibration、decision_state、abstain） | HIGH | 先跑 baseline、改後逐項驗 diff；閾值 ±3% 保守，大多數弱趨勢仍是中性 |
| OHLCV meta 格式不統一（不同連接器產出的 meta 結構不同） | MEDIUM | 寫 adapter：支援 `meta["close"]`（單值）和 `meta["ohlcv"]`（序列）兩種格式 |
| 多源 stance 資料太稀缺，Layer 2 幾乎不觸發 | LOW | 設計上 Layer 2 fallback 到 Layer 1 即可，不會比修復前差 |
| 「不明」新增輸出值影響前端/報表渲染 | MEDIUM | 前端 mapping：「不明」顯示為灰色/虛線，不影響既有三態圖表 |
| 回測用固定種子，但 CI 每次跑結果一致嗎 | LOW | `random.seed(42)` 固定種子，確定性 |

---

## G. 不做的事（scope 排除）

- **不動 TrustScore 公式**（信譽/佐證/時效/操縱四維不變）
- **不動 `_infer_direction()`**（scoring.py 的純函式，用於 claim 抽取時的方向標註，那個是對的）
- **不新增 Bedrock 呼叫**（方向判定是確定性演算法，不用 LLM）
- **不動 abstain 邏輯**（但會驗證修改後 abstain 行為不退化）
- **不做 Layer 3 DS 加權**（獨立 issue #339）

---

## H. 計劃審批

- [ ] CEO 審批
- [ ] 開發人員確認時程可行
- [ ] 確認 `data/` 目錄有 5 幣種 OHLCV（回測/CI 需要）
