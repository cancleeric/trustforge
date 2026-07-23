# T+1 / T+7 / T+14 outcome 語意與資料規則

> Issue: #501
>
> Status: **PENDING CEO DISPOSITION — NOT APPROVED FOR IMPLEMENTATION**
>
> Owner: CEO / product owner
>
> Draft date: 2026-07-23
>
> Scope: 決策文件與可轉 fixture 的期望值；不實作 labeler、不動 DB、不回填資料。

## 1. 決策摘要

本文件定義 outcome labeler 所需的候選契約。下列推薦均為工程分析，**不是產品決策**；CEO 必須在第 10 節逐項留下書面 disposition，所有項目通過前不得把推薦值當成 production default。

| ID | 待決問題 | 選項 | 工程推薦 | 主要風險 | 狀態 |
|---|---|---|---|---|---|
| D1 | 市場日曆 | A. 資產／venue 的正式交易日曆；B. UTC 日曆日；C. OHLCV 第 N 根 bar | A；24/7 資產使用 UTC 日曆 | C 會把缺 bar、停牌誤當不存在的時間；B 不適合有休市的市場 | pending CEO disposition |
| D2 | T+N 終點 | A. 第 N 個合格交易 session 的 official close；B. elapsed N×24h；C. 第 N 根現有 bar | A | A 需要可靠 calendar/venue；B/C 跨假日語意不穩 | pending CEO disposition |
| D3 | 安全起點價格 | A. event 所屬 session close；B. availability-cutoff 後第一個安全 close；C. event 時即時價 | B；只能由 `prediction_available_at` 與 cutoff 判定 | A 對晚到 prediction 造成 leakage；C 難重現 | pending CEO disposition |
| D4 | 中性 outcome | A. 不評分；B. 固定 ±2%；C. horizon/volatility-aware band | A（首版）；另報 realized move | B/C 會新增未驗證產品閾值；A 無法校準中性預測 | pending CEO disposition |
| D5 | hit tie | A. directional return `> 0` 才 hit；B. `>= 0`；C. flat 為 neutral | A | B 把零報酬算成功；C 需要先決定 neutral 契約 | pending CEO disposition |
| D6 | 公司行動／股息 | A. split-adjusted price return；B. dividend-adjusted total return；C. raw price return + action ledger；D. unavailable | 首版 A：同 provider、同 methodology version、以 outcome `as_of` 可得的 split-adjusted close；股息不計 | A 不代表投資人總報酬；B 需要可靠 dividend/reinvestment 契約；回溯調整會改寫 latest | pending CEO disposition |
| D7 | 行情修訂 | A. immutable first-known；B. latest truth；C. 雙版本 | C | 單用 A 犧牲正確性；單用 B 破壞重現性 | pending CEO disposition |
| D8 | 晚到資料 | A. cutoff 後永久 unavailable；B. cutoff 後產生新 revision；C. 無限期等待 | B，current 統計按 `as_of` 選 canonical revision | A 浪費可恢復資料；B 需版本化；C 造成無限漂移 | pending CEO disposition |

## 2. 與現有程式契約的差異

此文件不改變現況，只記錄必須在後續 implementation issue 解決的差異：

- `src/trustforge/calibration.py::outcomes_for_horizon`：T+N 是排序後 OHLCV 的 index distance；只接受 `偏多`、`偏空`、`bullish`、`bearish`；缺起點／終點 bar 或起點 close=0 時直接略過；`directional_return > 0` 才命中。
- `src/trustforge/calibration_runner.py::compare_predictions`：同樣以 bar index 計算；接受 `中性`、`偏多`、`偏空`；中性使用固定 `abs(return) < 2%`，方向 hit 使用嚴格正／負。
- 兩者目前都沒有正式 calendar、event/available time、maturity state、停牌、公司行動、晚到或 revision lineage 欄位。

在 D1–D8 disposition 完成前，既有行為只能視為 diagnostic legacy behavior，不得被描述成已核准的 outcome product contract。

## 3. 候選時間模型

### 3.1 時間欄位

所有 timestamp 使用 RFC 3339、帶 offset 儲存；比較前正規化為 UTC。session label 仍使用 calendar 的本地日期，禁止用 UTC date 代替有休市市場的 session date。

24/7 UTC daily calendar 的 session label `D` 精確定義為半開區間 `[D 00:00:00Z, D+1 00:00:00Z)`，scheduled close 是 `D+1 00:00:00Z`。因此 label `2026-01-01` 的 close bar event time 是 `2026-01-02T00:00:00Z`；所有 start/target session 欄位存 label，不存 close 的 UTC date。非 24/7 venue 由具版本的官方 calendar 提供 open/close instants。

PIT invariant：`prediction_event_at <= prediction_available_at`。等號合法，表示事件在產生時即完整可用；反序是 `unavailable(INVALID_PREDICTION_TIMELINE)`，不得自動交換、截斷或推測。所有 cutoff 比較先轉 UTC；DST 只影響 calendar 提供的本地 close 對應 UTC instant。

| 欄位 | 公式／定義 | 來源 | event time | available time | null / pending / unavailable |
|---|---|---|---|---|---|
| `prediction_event_at` | 產生預測所代表的分析邊界 | immutable prediction envelope | 即該 timestamp | 同筆 envelope 的 `prediction_available_at` | null=不合法；不可進 label queue |
| `prediction_available_at` | 系統首次可讀到完整預測的時間 | append-only ingest/audit log | 不適用（本身是 availability） | 即該 timestamp | null=不合法；不可證明 PIT |
| `calendar_id` | `{asset_class}:{venue}:{calendar_version}` | instrument master + calendar registry | instrument mapping 生效時間 | registry version 發布時間 | null=pending mapping；找不到版本=unavailable |
| `timezone` | calendar 的 IANA timezone | versioned calendar registry | calendar 生效時間 | registry version 發布時間 | null=unavailable；不得猜 offset |
| `start_session` | D3=B 時，`prediction_available_at <= scheduled_close_at - prediction_cutoff_buffer` 的最早 scheduled session | calendar + prediction availability | session scheduled close | calendar 與 prediction 皆可用之較晚者 | 尚未收盤=pending；calendar 缺口=unavailable；bar 缺失不改 session |
| `start_close` | start session 的 official close | versioned OHLCV provider | `start_session_close_at` | provider `retrieved_at` | 尚未發布=pending；缺失過 cutoff=unavailable |
| `target_session` | `advance(start_session, N, eligible_session)` | versioned calendar | target session close time | calendar version 發布時間 | 未到 close=pending；calendar gap=unavailable |
| `target_close` | target session 的 official adjusted close（若 D6=A） | versioned OHLCV + action lineage | target session close time | provider `retrieved_at` | 未到／未發布=pending；過 cutoff 仍缺=unavailable |
| `matures_at` | `target_session.scheduled_close_at + publication_lag_sla` | calendar + approved SLA | target scheduled close | 規則於部署時可用 | target 未知時 null/pending；規則缺失=unavailable |
| `labeled_at` | labeler 實際產生此版本 outcome 的時間 | label audit log | label computation time | 同值 | 未算=pending；不可事後偽造 |

### 3.2 三個獨立 cutoff（數值仍待 CEO disposition）

| Rule | 作用 | 可簽選項 | 首版推薦（未核准） | 等號／cutoff 後規則 |
|---|---|---|---|---|
| `prediction_cutoff_buffer` | prediction 必須早於 start close 的安全距離 | 24/7: 0m/5m/15m；session venue: 0m/15m/30m | 24/7=5m；session venue=15m | `available_at == close-buffer` 可用該 session；大於則 start 移至下一 scheduled session |
| `publication_lag_sla` | close event 後等待 official bar 的正常發布期 | 15m/1h/4h | 24/7=1h；session venue=4h | `available_at <= close+SLA` 是 on-time；超過仍可在 late cutoff 前標記 late/pending |
| `late_data_cutoff` | 超過 maturity 後等待缺資料的期限 | 24h/3 calendar days/7 calendar days | 3 calendar days | `as_of == matures_at+cutoff` 仍可 label；大於時該 revision unavailable；D8=B 到貨後新增 outcome revision，不覆寫 |

三個參數按 `calendar_id` 版本化且不可互相代替。未配置任何一個即 `unavailable(RULE_NOT_APPROVED)`；不能默認為零。

### 3.3 T+N 候選算法（仍待 D1/D2 disposition）

```text
target_session = start_session
repeat N times:
    target_session = calendar.next_eligible_session(target_session)
```

`eligible_session` **只**由版本化 venue calendar 宣告的 scheduled open/close 決定，與 instrument 是否交易、停牌、是否有 bar/close 無關。週末、法定假日與 calendar 已宣告的臨時休市不計數；縮短交易日計數並使用 scheduled early close。若 calendar registry 對日期無法回答「open 或 closed」，是 `CALENDAR_GAP`，不可猜測。24/7 資產使用版本化 UTC daily calendar，每個 UTC day 都是 session。instrument 停牌或缺 bar 不得改變 start/target session，也不得把下一根 bar 當 T+N。

## 4. 候選 outcome 欄位契約

| 欄位 | 公式／定義 | 來源 | event time | available time | 狀態語意 |
|---|---|---|---|---|---|
| `outcome_id` | hash(`prediction_id`, `horizon`, `contract_version`, `market_data_variant`, `market_data_revision`, `outcome_version`) | labeler deterministic function | prediction event | identity inputs 全部可用時 | null=不合法；同 prediction/horizon 的 revisions 絕不可 collision |
| `outcome_version` | 從 1 開始、同 logical outcome 每次 immutable recomputation 單調遞增 | label audit log | recomputation event | 同值 | null=不合法；不可重用 |
| `market_data_variant` | enum `as_first_known` / `latest_official` | D7 policy | bar event | variant policy deployment | null=不合法 |
| `horizon` | enum `T+1`,`T+7`,`T+14` | prediction/outcome request | prediction event | request 建立時間 | null=不合法；其他值 unavailable |
| `return_pct` | `100 × (target_close/start_close - 1)` | approved closes | target close | 兩 close 可用之較晚者 | pending=未成熟/等資料；unavailable=任一必要 close 永久缺；不以 0 代替 |
| `direction_sign` | bullish=`+1`; bearish=`-1`; neutral=`0`; abstain/unknown=`null` | immutable prediction | prediction event | prediction availability | 未知 mapping=unavailable；abstain 不可假裝 neutral |
| `directional_return_pct` | directional prediction: `return_pct × direction_sign` | derived | target close | 同 `return_pct` | neutral/abstain=null；pending/unavailable 繼承 return |
| `risk_abs_move_pct` | `abs(return_pct)` | derived | target close | 同 `return_pct` | pending/unavailable 繼承 return |
| `risk_downside_pct` | `min(return_pct, 0)` | derived | target close | 同 `return_pct` | pending/unavailable 繼承 return |
| `hit` | D5 推薦：bullish/bearish 時 `directional_return_pct > 0` | derived | target close | 同 `return_pct` | neutral/abstain=null；flat=false；pending/unavailable 不得為 false |
| `maturity` | enum，見第 5 節 state machine | derived from time/data | 隨狀態事件變更 | 狀態事件可用時 | 永不為 null |
| `reason_code` | state 的機器可讀原因 | controlled vocabulary | 狀態事件 | 同事件 availability | `labeled` 可為 null；其他 state 必填 |
| `market_data_revision` | provider/version/retrieved_at/content hash | market-data lineage | bar event | provider retrieval | 缺 lineage=unavailable |
| `canonical_as_of` | 此 revision 被選為 canonical 所依據的 report `as_of` | report query | report event | query execution | 非 current materialization 可為 null |
| `supersedes_outcome_id` | 前一 immutable revision identity | label audit log | revision event | revision availability | 首版 null；後續版必填 |
| `contract_version` | 核准後 immutable semantic version | outcome registry | contract effective time | deployment time | pending CEO 時不可發 production version |

報酬是百分點（例如 100→110 為 `10.0`），不是小數 `0.10`。計算使用未四捨五入的 decimal/float；顯示層才 round。風險欄位是 realized diagnostics，不得描述為預測風險機率。

### 4.1 Numeric contract（待 CEO 與 D5 disposition）

- 所有 price 與計算使用 base-10 `Decimal`，禁止 binary float；輸入最多 18 位有效數、8 位小數，超出即 unavailable。
- 中間運算使用 precision 34、`ROUND_HALF_EVEN`；除法不先 quantize。
- persisted percentage quantize 到小數點後 8 位；顯示可另行 round，但不得回寫 outcome。
- fixture 比較 tolerance 固定 `0.00000001` percentage point。`after_cutoff` 的 exact value 是 `200/51 = 3.921568627450980392...`，persisted expected `3.92156863`。
- D5=A 的 hit 使用**未 quantize** directional return `> 0`；exact zero 才是 miss。任何非零 Decimal（即使 persisted 顯示接近 0）仍依符號判定。若產品要 dead-band，必須選 D5=C 並另簽明確 threshold，不得偷用 tolerance 當商業規則。

### 4.2 Revision、current、canonical 與 as-of

- logical outcome key 是 (`prediction_id`, `horizon`, `contract_version`, `market_data_variant`)；實體 identity 再加 `market_data_revision` 與 `outcome_version`。
- `as_first_known` 固定使用 late cutoff 內首次完整可得的 provider revisions；一旦 labeled 不因後續 provider revision 改值。
- `latest_official` 可新增 immutable outcome version。`current(as_of)` 是 `available_at <= as_of` 中最高 `outcome_version`；未來才到的 revision 不可見。
- `canonical(as_of, variant)` 是該 logical key 在指定 variant 的 `current(as_of)`。沒有跨 variant 的隱含 canonical；呼叫者必須明選 variant。
- superseded 是版本關係，不是刪除。歷史 as-of 查詢仍回傳當時 canonical；current 報表只計 canonical revision 一次，audit 報表可列所有 versions 但不得混入 denominator。
- `market_data_revision` 必須涵蓋 start/target bar 各自的 provider、dataset/version、methodology version、event_at、available_at、content hash；只寫「latest」不合法。

## 5. maturity 與缺值 state machine

| `maturity` | 定義 | 可否進 eligible denominator | 欄位要求 |
|---|---|---:|---|
| `pending` | target close 尚未發生，或仍在核准 publication/late-data cutoff 內 | 否 | outcome metrics 全為 null；`reason_code` 必填 |
| `labeled` | 核准版本的所有必要輸入已可用且計算成功 | 是（僅 directional prediction；neutral 取決 D4） | metrics 依契約填值；lineage 必填 |
| `unavailable` | cutoff 已過且必要 calendar/price/lineage 永久缺失，或規則不存在 | 否 | metrics 全為 null；`reason_code` 必填 |
| `superseded` | 在某個較晚 as-of 已有同 variant successor；早期 as-of 仍可能是 canonical | 否；current 只計 canonical successor | 保留舊值與 `superseded_by`，不可覆寫 |

`null` 是欄位值；`pending` / `unavailable` 是 outcome 狀態，兩者不可互換。禁止把 pending/unavailable 的數值填 0，也禁止從 eligible denominator 靜默丟棄而不計 state count。

建議 reason codes：`NOT_MATURE`、`WAITING_OFFICIAL_CLOSE`、`WAITING_LATE_DATA_CUTOFF`、`INVALID_PREDICTION_TIMELINE`、`CALENDAR_MAPPING_MISSING`、`CALENDAR_GAP`、`START_CLOSE_MISSING`、`TARGET_CLOSE_MISSING`、`ZERO_START_CLOSE`、`PRICE_LINEAGE_MISSING`、`RULE_NOT_APPROVED`、`PREDICTION_NOT_DIRECTIONAL`、`MARKET_DATA_REVISED`、`LATE_AFTER_CUTOFF`。

## 6. 邊界規則候選

- **週末／假日**：不計入非 24/7 venue 的 N；24/7 UTC calendar 照常計入。
- **停牌**：venue calendar scheduled session 仍照常計數；instrument 無 official close 不改 T+N。固定 target session，late cutoff 前 pending，之後該 revision unavailable；若 D8=B 核准且 bar 後到，新增 revision。
- **縮短交易日**：是合格 session；使用正式 early close。
- **公司行動**：D6=A 是 split-adjusted **price return**，排除 cash dividend；D6=B 是含 dividend 且依明定 reinvestment rule 的 **total return**。兩端必須用同 provider、同 methodology version、且在 report `as_of` 可得的同一 adjustment basis；不可混用 raw/adjusted 或偷偷用未來回溯調整。首版精確推薦是 A + `as_first_known`，provider/methodology 由資產 onboarding disposition 指定。
- **缺 bar**：不可 forward-fill、backfill、跨日 substitute，亦不可因缺 bar 而把後一根當 T+N。
- **晚到**：cutoff 內由 pending 轉 labeled；cutoff 後依 D8 disposition 決定 unavailable 或新增版本，禁止原地覆寫。
- **行情修訂**：D7 推薦保留 `as_first_known` 與 `latest_official`；identity、as-of、canonical 與計數遵循 4.1，報表必須明標 variant。
- **重複 prediction**：以 immutable `prediction_id` 分開標記；不得只用 coin/date 去重。
- **時區 DST**：只由 IANA timezone + calendar version 解決；不得硬編 UTC offset。

## 7. 人工演算與 fixture 決策表

以下採推薦值作 deterministic review vectors（24/7 buffer=5m/SLA=1h；XNYS buffer=15m/SLA=4h；late cutoff=3d；D6=A；variant=`as_first_known`），**不是 CEO disposition**。`calendar_sessions` 的 session label 是 ISO date，`O@HHZ` 是該日 scheduled close、`C` 是 calendar 宣告 closed、`?` 是 registry gap。bars 格式是 `session:event_at:available_at:close:methodology`；其中 `event_at` / `available_at` 必須是完整 RFC 3339 timestamp。每列 expected 都依序完整列出 `maturity/reason/start/target/return/directional/abs_risk/downside/hit/outcome_version`。

| fixture_id | calendar_id | calendar_sessions | prediction_id | prediction_event_at | prediction_available_at | direction | horizon | bars | as_of | market_data_variant | expected |
|---|---|---|---|---|---|---|---|---|---|---|---|
| daily_bull_up | crypto:UTC:v1 | 01-01O@00Z,01-02O@00Z | p01 | 2026-01-01T23:00:00Z | 2026-01-01T23:01:00Z | bullish | T+1 | 01-01:00Z:00:10Z:100:split-v1,01-02:00Z:00:10Z:110:split-v1 | 2026-01-03T00:00:00Z | as_first_known | labeled/null/01-01/01-02/10/10/10/0/true/1 |
| daily_bear_down | crypto:UTC:v1 | 01-01O@00Z,01-02O@00Z | p02 | 2026-01-01T22:00:00Z | 2026-01-01T22:01:00Z | bearish | T+1 | 01-01:00Z:00:10Z:100:split-v1,01-02:00Z:00:10Z:90:split-v1 | 2026-01-03T00:00:00Z | as_first_known | labeled/null/01-01/01-02/-10/10/10/-10/true/1 |
| bearish_miss | crypto:UTC:v1 | 01-01O@00Z,01-02O@00Z | p03 | 2026-01-01T22:00:00Z | 2026-01-01T22:01:00Z | bearish | T+1 | 01-01:00Z:00:10Z:100:split-v1,01-02:00Z:00:10Z:110:split-v1 | 2026-01-03T00:00:00Z | as_first_known | labeled/null/01-01/01-02/10/-10/10/0/false/1 |
| cutoff_equal | crypto:UTC:v1 | 2026-01-01O@00Z,2026-01-02O@00Z | p04 | 2026-01-01T23:54:00Z | 2026-01-01T23:55:00Z | bullish | T+1 | 2026-01-01:2026-01-02T00:00:00Z:2026-01-02T00:10:00Z:100:split-v1,2026-01-02:2026-01-03T00:00:00Z:2026-01-03T00:10:00Z:105:split-v1 | 2026-01-03T01:00:00Z | as_first_known | labeled/null/2026-01-01/2026-01-02/5/5/5/0/true/1 |
| after_cutoff | crypto:UTC:v1 | 2026-01-01O@2026-01-02T00:00:00Z,2026-01-02O@2026-01-03T00:00:00Z,2026-01-03O@2026-01-04T00:00:00Z | p05 | 2026-01-01T23:55:00Z | 2026-01-01T23:55:00.001Z | bullish | T+1 | 2026-01-02:2026-01-03T00:00:00Z:2026-01-03T00:10:00Z:102:split-v1,2026-01-03:2026-01-04T00:00:00Z:2026-01-04T00:10:00Z:106:split-v1 | 2026-01-04T01:00:00Z | as_first_known | labeled/null/2026-01-02/2026-01-03/3.92156863/3.92156863/3.92156863/0/true/1 |
| invalid_timeline | crypto:UTC:v1 | 2026-01-01O@00Z,2026-01-02O@00Z | p06 | 2026-01-01T12:00:01Z | 2026-01-01T12:00:00Z | bullish | T+1 | 2026-01-01:2026-01-02T00:00:00Z:2026-01-02T00:10:00Z:100:split-v1,2026-01-02:2026-01-03T00:00:00Z:2026-01-03T00:10:00Z:110:split-v1 | 2026-01-03T01:00:00Z | as_first_known | unavailable/INVALID_PREDICTION_TIMELINE/null/null/null/null/null/null/null/1 |
| weekend_skip | XNYS:v2026a | 01-02O@21Z,01-03C,01-04C,01-05O@21Z | p07 | 2026-01-02T20:00:00Z | 2026-01-02T20:01:00Z | bullish | T+1 | 01-02:21Z:21:10Z:100:split-v1,01-05:21Z:21:10Z:105:split-v1 | 2026-01-06T02:00:00Z | as_first_known | labeled/null/01-02/01-05/5/5/5/0/true/1 |
| early_close | XNYS:official-2026 | 2026-11-27O@18Z,2026-11-30O@21Z | p08 | 2026-11-27T17:44:00Z | 2026-11-27T17:45:00Z | bullish | T+1 | 2026-11-27:2026-11-27T18:00:00Z:2026-11-27T18:10:00Z:100:UNAVAILABLE,2026-11-30:2026-11-30T21:00:00Z:2026-11-30T21:10:00Z:101:UNAVAILABLE | 2026-12-01T02:00:00Z | as_first_known | labeled/null/2026-11-27/2026-11-30/1/1/1/0/true/1 |
| dst_calendar | XNYS:v2026a | 2026-03-06O@21Z,2026-03-09O@20Z | p09 | 2026-03-06T20:00:00Z | 2026-03-06T20:01:00Z | bullish | T+1 | 2026-03-06:2026-03-06T21:00:00Z:2026-03-06T21:10:00Z:100:split-v1,2026-03-09:2026-03-09T20:00:00Z:2026-03-09T20:10:00Z:102:split-v1 | 2026-03-10T01:00:00Z | as_first_known | labeled/null/2026-03-06/2026-03-09/2/2/2/0/true/1 |
| emergency_closed | XNYS:v2026a | 01-07O@21Z,01-08C,01-09O@21Z | p10 | 2026-01-07T20:00:00Z | 2026-01-07T20:01:00Z | bullish | T+1 | 01-07:21Z:21:10Z:100:split-v1,01-09:21Z:21:10Z:103:split-v1 | 2026-01-10T02:00:00Z | as_first_known | labeled/null/01-07/01-09/3/3/3/0/true/1 |
| calendar_gap | XNYS:v2026a | 2026-01-07O@21Z,2026-01-08? | p11 | 2026-01-07T20:00:00Z | 2026-01-07T20:01:00Z | bullish | T+1 | 2026-01-07:2026-01-07T21:00:00Z:2026-01-07T21:10:00Z:100:split-v1 | 2026-01-12T00:00:00Z | as_first_known | unavailable/CALENDAR_GAP/2026-01-07/null/null/null/null/null/null/1 |
| suspension_no_slide | XNYS:v2026a | 01-12O@21Z,01-13O@21Z,01-14O@21Z | p12 | 2026-01-12T20:00:00Z | 2026-01-12T20:01:00Z | bullish | T+1 | 01-12:21Z:21:10Z:100:split-v1,01-14:21Z:21:10Z:110:split-v1 | 2026-01-17T01:00:00Z | as_first_known | pending/WAITING_LATE_DATA_CUTOFF/01-12/01-13/null/null/null/null/null/1 |
| target_missing | XNYS:v2026a | 01-12O@21Z,01-13O@21Z | p13 | 2026-01-12T20:00:00Z | 2026-01-12T20:01:00Z | bullish | T+1 | 01-12:21Z:21:10Z:100:split-v1 | 2026-01-17T01:00:00.001Z | as_first_known | unavailable/TARGET_CLOSE_MISSING/01-12/01-13/null/null/null/null/null/1 |
| late_after_cutoff | XNYS:v2026a | 01-12O@21Z,01-13O@21Z | p14 | 2026-01-12T20:00:00Z | 2026-01-12T20:01:00Z | bullish | T+1 | 01-12:21Z:21:10Z:100:split-v1,01-13:21Z:01-18T00Z:110:split-v1 | 2026-01-18T01:00:00Z | latest_official | labeled/LATE_AFTER_CUTOFF/01-12/01-13/10/10/10/0/true/2 |
| split_adjusted_asof | XNYS:v2026a | 01-20O@21Z,01-21O@21Z | p15 | 2026-01-20T20:00:00Z | 2026-01-20T20:01:00Z | bullish | T+1 | 01-20:21Z:21:10Z:50:split-v1,01-21:21Z:21:10Z:55:split-v1 | 2026-01-22T02:00:00Z | as_first_known | labeled/null/01-20/01-21/10/10/10/0/true/1 |
| dividend_price_only | XNYS:v2026a | 02-02O@21Z,02-03O@21Z | p16 | 2026-02-02T20:00:00Z | 2026-02-02T20:01:00Z | bullish | T+1 | 02-02:21Z:21:10Z:100:split-v1,02-03:21Z:21:10Z:99:split-v1 | 2026-02-04T02:00:00Z | as_first_known | labeled/null/02-02/02-03/-1/-1/1/-1/false/1 |
| adjustment_future_hidden | XNYS:v2026a | 02-02O@21Z,02-03O@21Z | p17 | 2026-02-02T20:00:00Z | 2026-02-02T20:01:00Z | bullish | T+1 | 02-02:21Z:02-05T00Z:50:split-v2,02-03:21Z:21:10Z:55:split-v2 | 2026-02-04T02:00:00Z | latest_official | pending/WAITING_OFFICIAL_CLOSE/02-02/02-03/null/null/null/null/null/1 |
| zero_start | crypto:UTC:v1 | 2026-01-01O@00Z,2026-01-02O@00Z | p18 | 2026-01-01T22:00:00Z | 2026-01-01T22:01:00Z | bullish | T+1 | 2026-01-01:2026-01-02T00:00:00Z:2026-01-02T00:10:00Z:0:split-v1,2026-01-02:2026-01-03T00:00:00Z:2026-01-03T00:10:00Z:10:split-v1 | 2026-01-03T01:00:00Z | as_first_known | unavailable/ZERO_START_CLOSE/2026-01-01/2026-01-02/null/null/null/null/null/1 |
| neutral_unscored | crypto:UTC:v1 | 01-01O@00Z,01-02O@00Z | p19 | 2026-01-01T22:00:00Z | 2026-01-01T22:01:00Z | neutral | T+1 | 01-01:00Z:00:10Z:100:split-v1,01-02:00Z:00:10Z:101:split-v1 | 2026-01-03T00:00:00Z | as_first_known | labeled/PREDICTION_NOT_DIRECTIONAL/01-01/01-02/1/null/1/0/null/1 |
| revision_v1 | crypto:UTC:v1 | 2026-01-01O@2026-01-02T00:00:00Z,2026-01-02O@2026-01-03T00:00:00Z | p20 | 2026-01-01T22:00:00Z | 2026-01-01T22:01:00Z | bullish | T+1 | 2026-01-01:2026-01-02T00:00:00Z:2026-01-02T00:10:00Z:100:split-v1,2026-01-02:2026-01-03T00:00:00Z:2026-01-03T00:10:00Z:110:split-v1 | 2026-01-03T12:00:00Z | latest_official | labeled/null/2026-01-01/2026-01-02/10/10/10/0/true/1; revision=r1; outcome_id=o1; supersedes=null; canonical=o1 |
| revision_v2 | crypto:UTC:v1 | 2026-01-01O@2026-01-02T00:00:00Z,2026-01-02O@2026-01-03T00:00:00Z | p20 | 2026-01-01T22:00:00Z | 2026-01-01T22:01:00Z | bullish | T+1 | 2026-01-01:2026-01-02T00:00:00Z:2026-01-02T00:10:00Z:100:split-v1,2026-01-02:2026-01-03T00:00:00Z:2026-01-04T00:00:00Z:108:split-v2 | 2026-01-04T00:00:00Z | latest_official | labeled/MARKET_DATA_REVISED/2026-01-01/2026-01-02/8/8/8/0/true/2; revision=r2; outcome_id=o2; supersedes=o1; canonical=o2 |

`revision_v1` 與 `revision_v2` 共用 logical key `(p20,T+1,contract,latest_official)`：`as_of=2026-01-03T12:00:00Z` 時 r2 尚不可見，canonical=o1；`as_of=2026-01-04T00:00:00Z` 時 canonical=o2 且 o1 superseded。兩個 outcome identity 必須不同。

NYSE 官方 2026 calendar 指定 2026-11-27（Thanksgiving 次日）於 13:00 America/New_York，即 18:00Z early close；2026-07-03 是全日休市，禁止作 early-close fixture。來源：https://www.nyse.com/markets/hours-calendars 。

## 8. 可觀測性與報表要求

每個 horizon 使用二維互斥報表，禁止把 maturity 與 direction eligibility 混成同一組 buckets：

- maturity 軸（每個 logical prediction/horizon 的 canonical revision 恰落一格）：`pending`、`labeled`、`unavailable`；三者之和必須等於 `total_logical_outcomes`。
- eligibility 軸只切 `labeled`：`eligible_directional`、`neutral_or_abstain`；兩者之和必須等於 `labeled`。

`superseded_versions` 是另列 audit count，不進上述任何 current count。報表必須帶 `contract_version`、`calendar_version`、`market_data_variant`、`as_of` 與產生時間。只報 eligible 而隱藏缺失會造成 survivorship bias。

## 9. 實作前置門檻

### 9.1 首版 asset scope 候選（全部 PENDING）

| scope_id | instruments | asset class | venue / calendar_id | timezone | provider / dataset / methodology | 可否簽 |
|---|---|---|---|---|---|---|
| S1 | BTC, ETH, SOL, BNB, XRP | crypto spot reference | 24/7 UTC / `crypto:UTC:v1` | UTC | **UNAVAILABLE/PENDING**：repo 有 OHLCV loader 與 coin symbols，但未提供可證實的 production provider、dataset ID 與 adjustment methodology contract | **不可簽**，直到具名 provider/dataset/methodology 與授權 lineage 補齊 |
| S2 | 無 | listed equity | XNYS / `XNYS:official-2026` | America/New_York | **UNAVAILABLE/PENDING**：本 issue 未證實 instrument master、production price provider/dataset 或 corporate-action methodology | **不可簽**；XNYS 僅供 calendar fixture，不代表首版支援股票 |

不允許把 fixture 的 `split-v1` 當真實 provider methodology。首版 scope 必須逐 instrument 指定 provider、dataset/version、price type、corporate-action methodology、license/lineage 與 calendar version；任何一格 unavailable 時 CEO 不可批准該 scope。

1. CEO 完成 D1–D8 書面 disposition，包含 cutoff SLA 與首版適用 asset/venue。
2. 將核准結果轉 immutable contract version 與 machine-readable fixtures。
3. 另開 implementation issue；不得在 #501 偷帶 labeler、DB 或回填。
4. 對現有兩套 calibration 路徑提出 migration/compatibility 計畫，禁止靜默改歷史數字。
5. 資料污染風險（PIT、revision、adjustment lineage）須完成 harper（CISO）審查與 `/codex-review`。

## 10. CEO / product owner disposition（必填）

> **目前 disposition：PENDING。下表未簽署，因此本文件不得作為 production default。**

| Decision | CEO disposition（approve option / reject / revise） | 理由 | 日期 | commit SHA |
|---|---|---|---|---|
| D1 calendar | PENDING |  |  |  |
| D2 T+N endpoint | PENDING |  |  |  |
| D3 start price | PENDING |  |  |  |
| D4 neutral | PENDING |  |  |  |
| D5 tie | PENDING |  |  |  |
| D6 corporate actions | PENDING |  |  |  |
| D7 revisions | PENDING |  |  |  |
| D8 late data | PENDING |  |  |  |
| cutoff SLA / asset scope | PENDING |  |  |  |

書面核准必須綁定包含本表的 exact commit SHA。PR review、author 自述或口頭同意都不能取代 CEO / product owner disposition。
