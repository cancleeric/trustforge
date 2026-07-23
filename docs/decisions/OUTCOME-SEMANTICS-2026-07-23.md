# T+1 / T+7 / T+14 outcome 語意與資料規則

> Issue: #501  
> Status: **PENDING CEO DISPOSITION — NOT APPROVED FOR IMPLEMENTATION**  
> Owner: CEO / product owner  
> Draft date: 2026-07-23  
> Scope: 決策文件與可轉 fixture 的期望值；不實作 labeler、不動 DB、不回填資料。

## 1. 決策摘要

本文件定義 outcome labeler 所需的候選契約。下列推薦均為工程分析，**不是產品決策**；CEO 必須在第 10 節逐項留下書面 disposition，所有項目通過前不得把推薦值當成 production default。

| ID | 待決問題 | 選項 | 工程推薦 | 主要風險 | 狀態 |
|---|---|---|---|---|---|
| D1 | 市場日曆 | A. 資產／venue 的正式交易日曆；B. UTC 日曆日；C. OHLCV 第 N 根 bar | A；24/7 資產使用 UTC 日曆 | C 會把缺 bar、停牌誤當不存在的時間；B 不適合有休市的市場 | pending CEO disposition |
| D2 | T+N 終點 | A. 第 N 個合格交易 session 的 official close；B. elapsed N×24h；C. 第 N 根現有 bar | A | A 需要可靠 calendar/venue；B/C 跨假日語意不穩 | pending CEO disposition |
| D3 | 起點價格 | A. event 所屬 session official close；B. event 後第一個 close；C. event 時即時價 | B（用 availability cutoff 判定） | A 對收盤後事件造成 leakage；C 難保證可重現 | pending CEO disposition |
| D4 | 中性 outcome | A. 不評分；B. 固定 ±2%；C. horizon/volatility-aware band | A（首版）；另報 realized move | B/C 會新增未驗證產品閾值；A 無法校準中性預測 | pending CEO disposition |
| D5 | hit tie | A. directional return `> 0` 才 hit；B. `>= 0`；C. flat 為 neutral | A | B 把零報酬算成功；C 需要先決定 neutral 契約 | pending CEO disposition |
| D6 | 公司行動 | A. adjusted official close；B. raw close + action ledger；C. unavailable | A，且保留 adjustment lineage | provider 回溯調整會改寫歷史結果 | pending CEO disposition |
| D7 | 行情修訂 | A. immutable first-known；B. latest truth；C. 雙版本 | C | 單用 A 犧牲正確性；單用 B 破壞重現性 | pending CEO disposition |
| D8 | 晚到資料 | A. maturity 後補算；B. 永久 unavailable；C. cutoff 內補算 | C（明確 SLA/cutoff） | A 造成無限漂移；B 浪費可恢復資料 | pending CEO disposition |

## 2. 與現有程式契約的差異

此文件不改變現況，只記錄必須在後續 implementation issue 解決的差異：

- `src/trustforge/calibration.py::outcomes_for_horizon`：T+N 是排序後 OHLCV 的 index distance；只接受 `偏多`、`偏空`、`bullish`、`bearish`；缺起點／終點 bar 或起點 close=0 時直接略過；`directional_return > 0` 才命中。
- `src/trustforge/calibration_runner.py::compare_predictions`：同樣以 bar index 計算；接受 `中性`、`偏多`、`偏空`；中性使用固定 `abs(return) < 2%`，方向 hit 使用嚴格正／負。
- 兩者目前都沒有正式 calendar、event/available time、maturity state、停牌、公司行動、晚到或 revision lineage 欄位。

在 D1–D8 disposition 完成前，既有行為只能視為 diagnostic legacy behavior，不得被描述成已核准的 outcome product contract。

## 3. 候選時間模型

### 3.1 時間欄位

所有 timestamp 使用 RFC 3339、帶 offset 儲存；比較前正規化為 UTC。session label 仍使用 calendar 的本地日期，禁止用 UTC date 代替有休市市場的 session date。

| 欄位 | 公式／定義 | 來源 | event time | available time | null / pending / unavailable |
|---|---|---|---|---|---|
| `prediction_event_at` | 產生預測所代表的分析邊界 | immutable prediction envelope | 即該 timestamp | 同筆 envelope 的 `prediction_available_at` | null=不合法；不可進 label queue |
| `prediction_available_at` | 系統首次可讀到完整預測的時間 | append-only ingest/audit log | 不適用（本身是 availability） | 即該 timestamp | null=不合法；不可證明 PIT |
| `calendar_id` | `{asset_class}:{venue}:{calendar_version}` | instrument master + calendar registry | instrument mapping 生效時間 | registry version 發布時間 | null=pending mapping；找不到版本=unavailable |
| `timezone` | calendar 的 IANA timezone | versioned calendar registry | calendar 生效時間 | registry version 發布時間 | null=unavailable；不得猜 offset |
| `start_session` | D3 所選規則得到的第一個可用 session | calendar + prediction availability | session close time | calendar 與 prediction 皆可用之較晚者 | 尚未收盤=pending；永久無 session=unavailable |
| `start_close` | start session 的 official close | versioned OHLCV provider | `start_session_close_at` | provider `retrieved_at` | 尚未發布=pending；缺失過 cutoff=unavailable |
| `target_session` | `advance(start_session, N, eligible_session)` | versioned calendar | target session close time | calendar version 發布時間 | 未到 close=pending；calendar gap=unavailable |
| `target_close` | target session 的 official adjusted close（若 D6=A） | versioned OHLCV + action lineage | target session close time | provider `retrieved_at` | 未到／未發布=pending；過 cutoff 仍缺=unavailable |
| `matures_at` | `target_session.close_at + publication_lag_sla` | calendar + approved SLA | target close time | 規則於部署時可用 | target 未知時 null/pending；規則缺失=unavailable |
| `labeled_at` | labeler 實際產生此版本 outcome 的時間 | label audit log | label computation time | 同值 | 未算=pending；不可事後偽造 |

### 3.2 推薦的 cutoff 規則（仍待 D3 disposition）

推薦：若 `prediction_available_at <= session.close_at - cutoff_buffer`，`start_session` 為該 session；否則為下一個合格 session。這避免收盤後才可得的預測用已知收盤價作起點。`cutoff_buffer` 必須按 venue 配置並由 CEO 批准；未配置時 outcome 是 `unavailable(rule_missing)`，不能默認 0 秒。

### 3.3 T+N 候選算法（仍待 D1/D2 disposition）

```text
target_session = start_session
repeat N times:
    target_session = calendar.next_eligible_session(target_session)
```

`eligible_session` 是 calendar 宣告開市且具有正式 close 的 session。週末、法定假日不計數；臨時休市不計數；縮短交易日計數且使用其正式 close。24/7 資產使用版本化 UTC daily calendar，每個 UTC day 都是 session。**不可用「目前存在的第 N 根 bar」代替 calendar**，否則資料缺口會改變 horizon。

## 4. 候選 outcome 欄位契約

| 欄位 | 公式／定義 | 來源 | event time | available time | 狀態語意 |
|---|---|---|---|---|---|
| `outcome_id` | hash(`prediction_id`, `horizon`, `contract_version`) | labeler deterministic function | prediction event | inputs 全部可用時 | null=不合法 |
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
| `contract_version` | 核准後 immutable semantic version | outcome registry | contract effective time | deployment time | pending CEO 時不可發 production version |

報酬是百分點（例如 100→110 為 `10.0`），不是小數 `0.10`。計算使用未四捨五入的 decimal/float；顯示層才 round。風險欄位是 realized diagnostics，不得描述為預測風險機率。

## 5. maturity 與缺值 state machine

| `maturity` | 定義 | 可否進 eligible denominator | 欄位要求 |
|---|---|---:|---|
| `pending` | target close 尚未發生，或仍在核准 publication/late-data cutoff 內 | 否 | outcome metrics 全為 null；`reason_code` 必填 |
| `labeled` | 核准版本的所有必要輸入已可用且計算成功 | 是（僅 directional prediction；neutral 取決 D4） | metrics 依契約填值；lineage 必填 |
| `unavailable` | cutoff 已過且必要 calendar/price/lineage 永久缺失，或規則不存在 | 否 | metrics 全為 null；`reason_code` 必填 |
| `superseded` | 因核准行情修訂或 contract migration 產生新版本 | 否；只計 successor | 保留舊值與 `superseded_by`，不可覆寫 |

`null` 是欄位值；`pending` / `unavailable` 是 outcome 狀態，兩者不可互換。禁止把 pending/unavailable 的數值填 0，也禁止從 eligible denominator 靜默丟棄而不計 state count。

建議 reason codes：`NOT_MATURE`、`WAITING_OFFICIAL_CLOSE`、`WAITING_LATE_DATA_CUTOFF`、`CALENDAR_MAPPING_MISSING`、`CALENDAR_GAP`、`START_CLOSE_MISSING`、`TARGET_CLOSE_MISSING`、`PRICE_LINEAGE_MISSING`、`RULE_NOT_APPROVED`、`PREDICTION_NOT_DIRECTIONAL`、`MARKET_DATA_REVISED`。

## 6. 邊界規則候選

- **週末／假日**：不計入非 24/7 venue 的 N；24/7 UTC calendar 照常計入。
- **停牌**：calendar session 仍存在但 instrument 無 official close。推薦不向後滑到下一根 bar；保持 target session 身分，cutoff 前 pending、之後 unavailable。向後滑會把 N 改成不固定 horizon。
- **縮短交易日**：是合格 session；使用正式 early close。
- **公司行動**：D6 推薦以同一 provider、同一 adjustment methodology 的 adjusted closes 計算，保留 action/version lineage；不可混用 raw start 與 adjusted target。
- **缺 bar**：不可 forward-fill、backfill、跨日 substitute，亦不可因缺 bar 而把後一根當 T+N。
- **晚到**：cutoff 內由 pending 轉 labeled；cutoff 後依 D8 disposition 決定 unavailable 或新增版本，禁止原地覆寫。
- **行情修訂**：D7 推薦保留 `as_first_known` 與 `latest_official` 兩版本；研究重現用前者，真值修正報表可用後者，報表必須標 variant。
- **重複 prediction**：以 immutable `prediction_id` 分開標記；不得只用 coin/date 去重。
- **時區 DST**：只由 IANA timezone + calendar version 解決；不得硬編 UTC offset。

## 7. 人工演算與 fixture 決策表

以下 expected values 假設暫採 D1=A、D2=A、D3=B、D5=A、D6=A；其目的只供審查與未來轉 fixture，**不是 CEO disposition**。

| fixture_id | calendar / facts | prediction | horizon | 人工演算 | expected |
|---|---|---|---|---|---|
| `daily_bull_up` | 24/7 UTC；Jan-01 close=100，Jan-02=110 | Jan-01 cutoff 前 bullish | T+1 | `100×(110/100-1)=10`; `10×1=10` | labeled; return=10; directional=10; abs_risk=10; downside=0; hit=true |
| `daily_bear_down` | 24/7 UTC；100→90 | bearish | T+1 | return=-10; `-10×-1=10` | labeled; directional=10; downside=-10; hit=true |
| `flat_is_miss` | 24/7 UTC；100→100 | bullish | T+1 | return=0; strict `>0` false | labeled; hit=false |
| `weekend_skip` | weekday venue；Fri close=100，Mon=105 | Fri cutoff 前 bullish | T+1 | Sat/Sun 不計；target=Mon；return=5 | labeled; return=5; hit=true |
| `holiday_skip_t7` | weekday venue；start Mon；下一週 Mon 是 holiday | bullish | T+7 | Tue(1),Wed(2),Thu(3),Fri(4),Tue(5),Wed(6),Thu(7) | target=次週 Thu，不是 calendar +7d |
| `after_cutoff` | weekday venue；Mon close=100，Tue=102，Wed=106 | Mon close 後才 available；bullish | T+1 | start=Tue；target=Wed；`100×(106/102-1)=3.921568...` | labeled; return≈3.9215686; event/available lineage retained |
| `suspension_no_slide` | target Tue session exists但 instrument 停牌、無 close；Wed close=110 | bullish | T+1 | target 固定 Tue；不得用 Wed | cutoff 前 pending，後 unavailable; return=null |
| `split_adjusted` | 2:1 split；adjusted start=50,target=55（raw 100→55） | bullish | T+1 | adjusted return=`100×(55/50-1)=10` | labeled; return=10；action lineage required，不得算 -45 |
| `missing_start` | prediction session close 永久缺 | bullish | T+1 | 無分母 | unavailable; reason=START_CLOSE_MISSING; metrics=null |
| `not_mature_t14` | target session 尚未 close | bearish | T+14 | 不計算 | pending; reason=NOT_MATURE; metrics=null |
| `neutral_unscored` | 24/7 UTC；100→101 | neutral | T+1 | D4=A | labeled truth return=1; direction fields/hit=null; excluded directional denominator |
| `late_before_cutoff` | target close event 已發生，official bar 尚未到；cutoff 後前抵達 | bullish | T+1 | arrival 前不可猜 | pending→labeled；labeled_at=實際補算時間 |
| `revision_dual` | first-known target=110；修訂 target=108；start=100 | bullish | T+1 | first=10；latest=8 | 兩 immutable versions；舊版 superseded/仍可重現 |

建議未來 fixture 的最小輸入鍵：`fixture_id`, `calendar_id`, `calendar_sessions`, `prediction_id`, `prediction_event_at`, `prediction_available_at`, `direction`, `horizon`, `bars[{session,event_at,available_at,close,adjustment_version}]`, `as_of`, `expected{maturity,reason_code,start_session,target_session,return_pct,directional_return_pct,risk_abs_move_pct,risk_downside_pct,hit}`。

## 8. 可觀測性與報表要求

每個 horizon 必須分別呈現 `total_predictions`、`pending`、`unavailable`、`neutral_or_abstain`、`eligible_directional`，且總和可 reconciliation。報表必須帶 `contract_version`、`calendar_version`、market-data variant 與產生時間。只報 eligible 而隱藏缺失會造成 survivorship bias。

## 9. 實作前置門檻

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
