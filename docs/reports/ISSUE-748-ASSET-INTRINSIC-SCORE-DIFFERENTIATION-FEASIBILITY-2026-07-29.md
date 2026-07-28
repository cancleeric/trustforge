# #748 資產本質與信任分區分度可行性分析

- 日期：2026-07-29
- Parent issue：[#748](https://github.com/cancleeric/trustforge/issues/748)
- 狀態：可行，但正式分數接入尚未具備 promotion 條件
- 評估基準：`origin/develop@944bc6b7`

## 一、結論

#748 的技術方向可行，而且資料契約、PIT repository、shadow contribution、
Analyze／Compare API 與解釋 UI 已有約 70–80% 的基礎。真正尚未完成的是：

1. BTC 與 BNB 的可驗證資料覆蓋不足，現行 coverage gate 不允許產生非零 delta。
2. 現行結果刻意是 shadow，不能改動正式 confidence、calibrated confidence、
   decision state 或 market judgment。
3. 尚未建立跨資產 observation dataset、promotion thresholds、A/B release 與
   release-level rollback 證據。
4. GitHub #757、#758 曾因 PR 先合入 `develop`、後續整批升版至 `main`，未觸發
   closing keyword 而殘留 OPEN。2026-07-29 已完成「工單—程式—測試」一致性稽核，
   以 73 個後端與 7 個前端針對測試重新驗證後，兩張 scoped issue 均已關閉。

因此，不應直接在 `trust/scoring.py` 加上 BTC baseline 或 BNB penalty。正確路徑是先
補足可重現的資產本質事實，觀察 shadow 分布，再經 promotion gate 接入一個語意獨立、
可分解且可回滾的 Asset Structure 分數。

## 二、使用者回饋中的有效需求與不可直接採用的假設

### 有效需求

- 不同資產的結構性風險需要有可理解的區分。
- 一般使用者應能看懂發行、控制、供給、治理與集中度差異。
- 分數差異必須能追溯到具體事實與來源。

### 不可直接當作評分事實

- 「價格高，所以信任較高」不是可接受的因果關係。
- 「一千多萬枚 BTC 已鎖住或遺失」缺少可驗證的實益所有權與遺失金鑰資料。
- 「華爾街持有大部分 BTC」需要時間點一致、去重且 entity-resolved 的持有資料。
- 「交易所幣可能額外產幣」必須由可重現的 consensus、upgrade authority、治理與
  鏈上資料證明，不能由資產名稱或發行方類型推定。

驗收條件必須是「同一方法套用所有資產」，不可設定「BTC 必須高於 BNB」。如果
可驗證事實不足，正確輸出是 unknown／0 contribution，而不是補值或人工排序。

## 三、現有實作盤點

### 1. 五維 PIT 資料契約已存在

`src/trustforge/asset_intrinsic.py` 已定義：

- issuance predictability
- control dispersion
- supply verifiability
- governance capture resistance
- holder concentration

每維具有 known／unknown／stale／conflicted 狀態、`as_of`、`valid_from`、
`valid_until`、`fetched_at`、來源 URL、方法、content hash、coverage、來源 revision
與 evidence coordinates。Repository 只暴露指定時間點可見的資料，future、stale 與
conflicted facts 不具 contribution eligibility。

### 2. Shadow contribution 已存在

`src/trustforge/asset_intrinsic_shadow.py` 已實作：

- 每維固定權重 `0.032`
- 至少 3/5 known dimensions
- 至少 2 個 source families
- 總調整上限 ±0.08
- unknown／stale／conflicted 精確貢獻 0
- 非有限值與不合法來源 fail-closed
- 輸入順序、symbol 與 asset identity 不影響相同 facts 的輸出

此模組明確宣告 `mode=shadow`、`affects_official_score=false`。

### 3. API 與 UI 已接入 shadow

- Analyze 與 Compare response 可附加獨立 `asset_intrinsic_assessment`。
- `AssetIntrinsicShadowPanel` 已顯示五維、coverage gate、delta、PIT 時間與證據溯源。
- 前端會嚴格驗證 payload；不相容資料不會污染正式分數。
- Compare 會分別綁定兩個資產的 assessment，避免交叉引用。

### 4. 現行真實資料為 honest zero

`data/asset_intrinsic_records.json` 目前：

- BTC：issuance predictability 與 supply verifiability 為 known；控制、治理、
  持有集中度為 unknown。
- BNB：五維目前都因缺少 pinned、可獨立重現或 entity-resolved 證據而為 unknown。

因此兩者都未通過 coverage gate，`total_delta=0`。現有測試明確保證 shadow 不改動
正式 confidence、calibrated confidence、decision state 與 market judgment。

## 四、方法論風險

### 1. Trust 與 Asset Structure 語意混淆

現行 trust score 主要表達證據與推論的可靠程度；去中心化、供給與治理則描述資產
結構。若直接混入 calibrated confidence，使用者可能誤解為「某資產的新聞證據因
資產較去中心化而更可靠」。

建議保留：

- Evidence Trust：資料、來源與推論可信度。
- Asset Structure：發行、控制、供給、治理與集中度。
- Composite view：只在 UI 或明確版本化的 policy layer 組合，且回傳逐維貢獻。

### 2. 資料不對稱

開源、文件完整的資產較容易取得證據；資料不足不代表較差。unknown 必須保持中性，
並與負分清楚區分。

### 3. 時間漂移與治理變更

validator set、upgrade authority、timelock、token unlock 與持有集中度會變動。正式
使用前必須有 freshness／validity policy，不能把一次性 fixture 當永久基線。

### 4. 權重與 cap 尚未實證校準

`0.032` 與 ±0.08 是安全的 shadow 起點，不是已證明的 production calibration。
必須用 observation dataset 檢查分布、敏感度、缺資料偏差與排名穩定性。

## 五、可行性分級

| 項目 | 可行性 | 現況 |
|---|---:|---|
| 五維契約與 PIT 安全 | 高 | 已實作，需 acceptance audit |
| Shadow deterministic contribution | 高 | 已實作，真實 BTC／BNB 仍為 0 |
| 一般使用者解釋 UI | 高 | 已有 shadow panel，需簡化與實際 Eye 驗證 |
| BTC／BNB 可驗證資料補齊 | 中 | 取決於 pinned upstream 與獨立資料來源 |
| 正式分數校準 | 中 | 缺 observation dataset 與 promotion evidence |
| 寫死 BTC > BNB | 不接受 | 違反方法論與現有 invariant |

## 六、建議完成定義

#748 只有在以下條件全部成立後才能關閉：

1. #757／#758 的 issue 狀態與實際程式、測試一致（已於 2026-07-29 完成）。
2. 至少 BTC、BNB 及兩個額外資產完成同方法 PIT evidence pack。
3. 每個有非零 contribution 的資產通過 3/5 known、2 source-family gate。
4. observation report 證明沒有 symbol hardcode、unknown penalty 或單來源支配。
5. Evidence Trust 與 Asset Structure 在 API/UI 語意上分離。
6. promotion policy、feature flag、A/B、release-level rollback drill 完成。
7. 完整 pre-push、Eye、`/codex-review` 與 reviewer attestation 全綠。
