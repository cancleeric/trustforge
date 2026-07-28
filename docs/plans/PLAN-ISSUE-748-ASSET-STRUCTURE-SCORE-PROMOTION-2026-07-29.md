# #748 Asset Structure Score 開發與 Promotion 計劃

- 日期：2026-07-29
- Parent issue：[#748](https://github.com/cancleeric/trustforge/issues/748)
- 前置分析：`docs/reports/ISSUE-748-ASSET-INTRINSIC-SCORE-DIFFERENTIATION-FEASIBILITY-2026-07-29.md`
- 原則：每張執行 issue 預估不超過 12 小時；不得 hardcode BTC／BNB 排名

## 一、目標

把現有 asset-intrinsic shadow 基礎推進為：

1. 工單、程式、測試狀態一致。
2. 可重現且 PIT-safe 的多資產 evidence packs。
3. 可量測的 shadow observation 與 promotion decision。
4. Evidence Trust 與 Asset Structure 語意分離的 API／UI。
5. 有 feature flag、A/B、release-level rollback 的受控上線能力。

本計劃不預設任何資產的最終排序。分數只能由相同契約、相同 gate 與可驗證資料產生。

## 二、不可妥協條件

- 不得以 symbol、asset name、issuer category 寫死加減分。
- unknown／stale／conflicted／future facts 貢獻必須為 0。
- 不得以 0.5、同業平均或 LLM 猜測補缺資料。
- 所有 scoring input 必須有 PIT、來源版本、hash、coverage 與 freshness。
- 正式接入前必須通過 observation promotion gate。
- feature flag 關閉時，正式輸出必須 byte-compatible 或 contract-compatible 回到舊行為。
- release-level A/B rollback 為 P0；rollback 不依賴重新建置。

## 三、里程碑

### M0：現況與工單一致性

完成 #757、#758 的 acceptance audit，區分「已完成」「證據缺漏」「尚未完成」，禁止只因
程式存在就直接關閉。

狀態：2026-07-29 已完成。PR #760、#761、#762 的 scoped acceptance、review 與 gate
證據已核對；現行 develop 再跑 73 個後端與 7 個前端針對測試全綠，#757、#758 已關閉。

### M1：資料方法與 evidence packs

建立可重複套用到所有資產的 rubric；BTC、BNB 分別補齊，不共用人工特例。至少再選
兩個資產驗證方法可移植性。

### M2：Observation 與語意分離

產生跨資產、跨時間 shadow observation dataset，量測 coverage、delta 分布、敏感度、
排名穩定性與 missingness bias。同時版本化 Asset Structure API，不污染 calibrated
confidence。

### M3：Promotion、A/B 與 rollback

只有 promotion checks 全通過才允許 feature flag 開啟。完成 A→B→regression→A
release-level rollback drill，留下 commit、artifact、config 與健康驗證證據。

## 四、執行 issues（每張 ≤12 小時）

| 順序 | 暫定標題 | 預估 | Depends on | Blocks |
|---:|---|---:|---|---|
| A | 五維方法論、來源資格與 asset-identity-blind 評分規格 | 8h | #757 | B、C、D、E |
| B | issuance／supply 通用來源擴充與版本化擷取 | 10h | A、#757 | F |
| C | control dispersion／governance capture 方法與 facts | 12h | A、#757 | F |
| D | holder concentration entity-resolution feasibility gate | 6h | A、#757 | 後續可選資料接入 |
| E | shadow observation event、provenance 與 dashboard | 10h | A、#758 | G、H |
| F | 多資產 PIT replay、區分度與 symbol-blind benchmark | 10h | B、C | G、H |
| G | promotion／non-inferiority gate 與停止條件 | 8h | E、F | H、I、J |
| H | canonical scorer candidate path 與 flag-off byte parity | 12h | G | I、J、K |
| I | shadow→official 解釋狀態與一般使用者文案 | 8h | H | K |
| J | release A/B artifact、rollback receipt 與非 production drill | 10h | G、H | K |
| K | limited canary、人工 promotion 與 post-promotion monitoring | 10h | H、I、J，且 G=PASS | Parent closure |

B、C、D、E 可平行；H 完成後 I、J 可平行。不可越級：E/F 未完成不得寫 promotion
結論；G 未 PASS 不得產生 production-capable promotion；J drill 未 PASS 不得執行 K。

## 五、各 issue 驗收摘要

### A. 通用方法論（8h）

- 逐維定義 measurement、`[0,1]` 正規化、stale 期限、conflict 與來源資格。
- 公式不得讀 symbol、名稱或 issuer label；以 metamorphic tests 證明 identity rename 不變。
- lost keys、地址等同實體、Wall Street ownership 等不可驗證推論列為禁止事項。
- 方法屬判斷完整性敏感範圍，需 gray（CPO）、harper（CISO）與 `/codex-review`。

### B. Issuance／Supply 通用資料（10h）

- 使用通用 adapter／record builder 產出 pinned revision、content hash 與 PIT timestamps。
- 至少兩種不同協議類型具有可離線重建、byte-stable 的 fixtures。
- future／stale／conflicted fail-closed；相同 facts 換 asset ID 輸出相同。
- 是否形成 BTC＞BNB 不得列為 acceptance criterion。

### C. Control／Governance 通用資料（12h）

- 分開衡量 validator／miner／node／governance 控制面。
- 不得把官方文件敘述直接等同 entity control。
- 至少 2 source families 才 eligible；缺資料保持 unknown。
- 衝突來源必須輸出 conflicted／delta 0，並具有 PIT replay 與來源撤回測試。

### D. Holder concentration 可行性（6h）

- 盤點跨鏈、custodian、bridge、burn／locked／lost-key 去重需求。
- 無 entity-resolved 歷史資料時結論必須為 unknown／no numeric value。
- 不得使用 top-address concentration 冒充 holder concentration。
- 若需要付費資料，另開成本敏感 issue；本單不採購。

### E. Shadow observation（10h）

- 每筆記錄 baseline／candidate trust、delta、facts hash、schema、PIT、gate reason、
  known/source-family counts 與 release identity。
- shadow 絕不改正式 Report；重試 idempotent，敏感 URL/query 不進 event。
- malformed/nonfinite fail-closed；flag off 時無寫入與行為差異。
- dashboard 可查 coverage、missing、stale、conflict 與 delta 分布。

### F. 多資產 benchmark（10h）

- 至少 5 個資產或 5 組匿名 profiles；涵蓋 known／unknown／stale／conflicted。
- identity rename、輸入 permutation 與同 facts 跨 symbol 結果必須一致。
- 量測 factual distance 對 score spread、coverage bias、極端值與單來源操縱敏感度。
- baseline、candidate、資料版本與 seed 可重建；不得斷言 BTC 必須高於 BNB。

### G. Promotion gate（8h）

- 至少 200 筆 PIT observations、5 個資產、30 日 observation。
- 每筆 promotion-eligible assessment 至少 3/5 known、2 source families；不足即 BLOCK。
- abs delta ≤0.08、nonfinite 零容忍、facts 相同跨 symbol 必須一致。
- 若有成熟 outcome labels，Brier/ECE 各不得惡化超過 0.01；否則不得宣稱校準改善。
- 產出機器可判讀 policy／receipt；只提供 recommendation，不自動切 production。

### H. Canonical scorer candidate（12h）

- candidate 只接唯一 canonical core scoring 入口，禁止 web/orchestrator post-process。
- 只讀 eligible PIT view；`candidate_trust=clamp(base_trust+delta)`。
- 不直接改 direction；任何 calibrated confidence／decision state 變化必須進 shadow diff。
- flag OFF 時 Report／kernel output byte-for-byte 與現版一致。
- flag ON 初期仍為 shadow-only，不對使用者冒充 official。

### I. 一般使用者 UI（8h）

- 明確區分「資產本質」與「市場方向／預測信心」。
- official 狀態由 release capability／receipt 驅動，不使用前端常數。
- unknown、來源與限制完整；不得顯示結論先行的 BTC＞BNB 文案。
- zh/en、desktop/mobile、200% zoom、long provenance 執行實際 branch Eye。

### J. Release A/B rollback（10h）

- A=現行 scorer；B=intrinsic candidate，兩者皆為 immutable release artifact/digest。
- 沿用既有 release router，不另造第二套 hot-swap。
- 非 production 執行 A→B→注入 regression→A，留下 actor/reason/digest/時間 receipt。
- 無不可逆 migration；A artifact 保留且 rollback 後健康。

### K. Limited canary（10h）

- G=PASS、H/I/J 完成後才可開始；先限定非 production／allowlisted canary。
- 任一 stop condition 自動停止擴量，但不自動 promotion。
- 只有 CEO 人工授權能升級；promotion 後驗 health 與實際 Analyze／Compare。
- 持續監測 spread、flip、coverage、source concentration；觸發門檻立即 route 回 A。
- pre-push、Eye、reviewer、harper 與 `/codex-review` 全綠。

## 六、相依圖

```text
#757 ─> A ─┬─> B ─┐
           ├─> C ─┼─> F ─┐
           ├─> D  │      ├─> G ─> H ─┬─> I ─┐
#758 ──────└─> E ─┘      │           └─> J ─┼─> K ─> close #748
                         └──────────────(gate)┘
```

## 七、CEO 審查決議

gray（CPO）計劃經 CEO 審查，以下決議全數通過：

1. #748 不以 BTC＞BNB 為驗收，而以 symbol-blind factual differentiation 與
   non-inferiority 為驗收。
2. production promotion 最低觀察量為 200 筆、5 個資產、30 日；不足就維持 shadow。
3. 正式改分視為 security／judgment-integrity sensitive，強制 harper + CPO +
   `/codex-review`。
4. holder concentration 可長期 unknown，不阻擋其他維度的 shadow research。
5. release-level A/B rollback 為 P0，且不得以 module hot-swap 代替完整 artifact rollback。

## 八、PR 與驗證策略

- 每張 issue 使用獨立 scoped branch；一張 issue 原則上一個 PR。
- PR 必須指定 reviewer，附 commit-bound pre-push evidence。
- UI issue 必須跑實際 branch Eye。
- 每個 PR merge 前完成 `/codex-review`；所有 findings 修正後重跑受影響 gate。
- 安全或 production policy 變更需要 harper（CISO）與 `/codex-review` 雙審。
- 每完成一個里程碑或超過三個 PR 主動回報。

## 九、Parent #748 關閉條件

- 子 issues 全部完成或有明確 research-only disposition。
- 正式接入只能在 promotion gate PASS 且 rollback drill PASS 後關閉。
- 若資料仍不足，允許以「shadow 方法完成、production promotion 未獲准」誠實結案，
  但不得宣稱已解決正式分數區分度。
