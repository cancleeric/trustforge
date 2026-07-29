# #872 Holder Concentration Entity-Resolution Feasibility Gate

- 日期：2026-07-29
- Parent issue：[#748](https://github.com/cancleeric/trustforge/issues/748)（子工單 D）
- 前置分析：`docs/reports/ISSUE-748-ASSET-INTRINSIC-SCORE-DIFFERENTIATION-FEASIBILITY-2026-07-29.md`
- 母計劃：`docs/plans/PLAN-ISSUE-748-ASSET-STRUCTURE-SCORE-PROMOTION-2026-07-29.md`
- 估時：6h（僅 feasibility report，無 data pipeline 產出）
- 原則：禁止 code；產出為 research/doc deliverables

## 一、Scope

本單只回答一個問題：在 TrustForge 現有 PIT 資料契約（`asset_intrinsic.py` 五維，
source_families ≥ 2、known ≥ 3/5 gate、coverage/freshness/content-hash 溯源）
的約束下，**holder concentration 維度能否取得 entity-resolved、可重現、
跨資產套用的可驗證事實**；若不能，誠實結案為 unknown disposition，不阻擋
其他四個 shadow dimensions。

### 1.1 範圍內

| 項目 | 說明 |
|---|---|
| 跨鏈去重 | 同一位址在主鏈、側鏈、L2、wrapped token 合約的多筆持有 —— 識別為同一實體 |
| Custodian 去重 | 交易所熱錢包、冷錢包、機構託管地址 —— 不可計為交易所／託管商「持有」 |
| Bridge 去重 | 跨鏈橋合約持有的原生資產 vs 橋另一端對應的 wrapped token |
| Burn / locked 去重 | 可證明銷毀地址（OP_RETURN、zero address、genesis）與鎖倉合約（timelock、staking）—— 排除出分母 |
| Lost-key 去重 | 已知遺失金鑰、早期無值挖礦、Satoshi-era 未移動 UTXO 的處理策略 —— 可區分「可證明遺失」vs「長期未動但無金鑰遺失證據」 |
| 資料授權與成本 | 盤點現有公開免費來源、需付費的提供商，標註契約限制 |
| Freshness 與可重現性 | 逐來源評估算為 PIT-eligible 的有效期限、revision pinning、byte-stable 重建能力 |

### 1.2 範圍外

| 項目 | 原因 |
|---|---|
| 產出 working data pipeline | 本單為 feasibility gate，非實作 |
| 任何付費資料採購 | AC 明定：需付費另開 cost-sensitive issue |
| 數值產出 | 若 entity-resolved 不可行，disposition = unknown，不允許 0.5、同業平均或 LLM 猜測補值 |
| 直接修改 `asset_intrinsic_records.json` | 本單只產出可行性結論，不變更 repository state |
| 實作 entity clustering 演算法 | 本單只評估既有公開方法與資料集的適用性，不實作 |

## 二、Deliverables（均為 research / doc，無 code）

### D1：市場與資料來源 landscape doc（~2h）

產出路徑：`docs/reports/ISSUE-872-HOLDER-DATA-LANDSCAPE-{DATE}.md`

- 盤點提供 entity-labeled / cluster-labeled 鏈上地址資料的服務商，逐一標註：
  - **服務商名稱**（Arkham、Chainalysis、TRM、Elliptic、Nansen、Dune、Glassnode、
    IntoTheBlock、CoinMetrics、Messari 等）
  - **標註方法論**：自陳 cluster heuristic（co-spend、multi-input、deposit-address
    reuse、withdrawal pattern）或人工標註
  - **覆蓋資產範圍**：限 BTC／限 ETH／限 EVM / multi-chain / 不支援 BSC
  - **授權類型**：公開免費層、付費 API、付費歷史 archive、禁止 redistribution
  - **API 能否取得 revision-pinned PIT snapshot**：能否指定 as_of timestamp 取回
    `{entity_id: balance, cluster_id, label}` 且 revision 可 pin
  - **Content hash 可重現性**：相同 as_of + revision 產出是否 byte-stable
  - **Freshness 限制**：資料更新頻率（daily/weekly）、最長歷史回溯（90d／1yr／full）、
    stale 判定建議
  - **Cross-chain coverage**：BTC + BSC 是否同一 provider 涵蓋，或需合併多來源
  - **合規限制**：禁止 redistribution、禁止 embed 至第三方產品、需 attribution
  - **成本結構**：free tier 限制（日請求數、資產數、歷史深度）、enterprise licensing
    是否必經合約審查

- 對所有 provider 做 final disposition 標記：
  - **eligible**：滿足 2+ source_families、PIT-pinnable、content-hash-verifiable、非零 coverage
  - **eligible_with_gaps**：滿足資格但有 freshness／asset scope／pinning 缺口需記錄
  - **ineligible**：授權禁止、無法 pin、無法 hash-verify、closed-source 黑箱
  - **unknown**：文件不足無法判定，需 provider contact

- 若所有公開免費來源均為 ineligible 或 unknown，在結論段明確陳述；
  這是 honest-zero disposition 的客觀基礎。

### D2：Entity-resolution 可行性模型與去重規範（~1.5h）

產出路徑：`docs/reports/ISSUE-872-ENTITY-DEDUP-MODEL-{DATE}.md`

**核心論述軸**：為何 top-N address concentration 不等同 holder concentration，
以及從 address → entity → holder 映射路徑所需的每一層推理。

#### 2.1 Address → Cluster

- 盤點學術與工業界公認的 clustering heuristics：
  - **BTC UTXO model**：multi-input co-spend（Meiklejohn 2013）、one-time change
    detection（Androulaki 2013）、address-reuse 保守假設
  - **Account model（ETH/BSC）**：deposit-address reuse、gas-funding patterns、
    DEX pool routing、NFT marketplace 交互特徵
  - 上述 heuristic 的**已知 false-positive 來源**：CoinJoin、PayJoin、mixer、
    privacy pool、batched exchange withdrawal、shared multisig、CEX 內轉
- 明確定義 cluster ≠ entity 的邊界：
  - 一個 entity 可控制多個 cluster（operational separation）
  - 一個 cluster 可能含多個 entity（shared wallet infrastructure、dust attack、
    airdrop farming pool）
- 結論：cluster 是必要中間層，不可直接等同 holder

#### 2.2 Cluster → Entity

- 盤點 label propagation 來源：
  - **Exchange deposit address tagging**（deposit → withdrawal 鏈路追蹤）
  - **On-chain identity protocol**（ENS、Space ID、Lens）
  - **Explorer 公開標籤**（Etherscan、BscScan —— 社群標註，非官方審計）
  - **Self-attestation**（機構公開錢包地址清單：Grayscale、MicroStrategy、
    Tesla、El Salvador 政府錢包等）
  - **OpSec 洩漏**（doxxed address、known hacks、seized funds）
- 不可接受的 entity inference：
  - 「exchange 錢包」= exchange 持有（exchange 是 custodian，非 beneficial owner）
  - 「長期未動 UTXO」= lost keys（無金鑰遺失密碼學證據不可假定遺失）
  - 「大戶地址」= institutional investor（無 KYC 資料不可假定身分）
  - 「Satashi-era address」= Satoshi（多個早期礦工同時存在）
  - 「Wall Street 持有大部分」= 華爾街機構持有（需時間點一致、去重、具名 entity map）

#### 2.3 Entity → Holder concentration

- 定義 normalization 邏輯：
  - 分子 = Σ(beneficial holder entity 持有的 supply)
  - 分母 = circulating supply − provably unspendable（burn 地址、genesis、zero
    address）− timelocked（若合約不可提前解鎖且為過渡性持有，歸屬發行方）
  - 除非有 entity label，custodian 地址的持有量歸屬 unknown，不計入任何 holder
  - bridge 合約鎖倉量：歸屬 wrapped token 持有者（若可驗證跨鏈鎖倉對應關係），
    或歸屬 unknown
- 集中度指標選擇：
  - Gini coefficient、HHI（Herfindahl-Hirschman）、top-N %（N ∈ {1, 5, 10, 50, 100}）、
    Nakamoto coefficient
  - 指標必須對 entity map coverage 敏感 —— 低 coverage 時 HHI 會低估集中度
  - 定義 **minimum entity-label coverage** 閾值以宣告 known vs unknown

#### 2.4 Lost-key 可驗證性

- 可證明類：
  - Provably unspendable：OP_RETURN、genesis block coinbase、null data outputs
  - Known lost keys：公開發布遺失金鑰事件（如早期硬碟損壞新聞、資安事件
    技術分析報告中有 signature 證明擁有權後確認金鑰銷毀）
  - Documented seized funds：美國 DOJ、FBI 公告扣押地址
- 不可證明類（必須 stay unknown）：
  - X 年未移動 UTXO（可能只是 HODL）
  - 推估 Satoshi 持有量（多個早期礦工，無身分證據）
  - 「業界共識」或「社群普遍認為」—— 無可驗證來源即不合格

### D3：授權、freshness 與可重現性限制文件（~1h）

產出路徑：`docs/reports/ISSUE-872-LICENSING-FRESHNESS-LIMITS-{DATE}.md`

- 逐資料來源評估五個維度的達標狀態：可 pin revision、可 content-hash verify、
  可獨立重建、有明定 freshness SLA、授權允許嵌入分析產品
- 定義本維度的 **stale policy**：
  - 提議值（如 30 天），理由（鏈上地址 clustering 變化速率 vs 治理變更）
  - 引用既有鏈上資料分析方法論文或 provider SLA 佐證
- 定義 **conflict resolution** 策略：
  - 若 provider A 與 provider B 的 entity label 不一致如何判定 conflicted
  - 若同一 provider 的 successive revisions 對同一地址有不同 label（如
    exchange 錢包被標記為 sanctioned entity 後由 provider 移除標籤），
    如何判定 PIT-consistent
- 明列所有需要付費才能滿足 source_families ≥ 2 的情境；若免費來源不足兩個
  eligible families，開立獨立 cost-sensitive issue（見 §四）

### D4：最終 feasibility 結論與 disposition report（~1.5h）

產出路徑：`docs/reports/ISSUE-872-FEASIBILITY-DISPOSITION-{DATE}.md`

- 摘要 D1–D3 發現
- 逐資產（BTC、BNB、ETH 等 parent plan M1 指定資產）給出 disposition：
  - **ready_for_evidence**：存在 ≥ 2 eligible source families 可開始產製
    known dimension records
  - **need_paid_source**：需付費來源；開立 cost-sensitive issue
  - **need_provider_contact**：文件不足無法判定；需向 provider 確認授權/pinning
  - **unknown_permanent**：可預見未來（6–12m）無可驗證路徑，建議本維度長期
    unknown，不阻擋其他四維 promotion
- 若 disposition 為 ready_for_evidence：提供下階段 72h data build plan outline
  （不含本單，由 issue C 或後續 issue 承接）
- 若 disposition 非 ready_for_evidence：提供明確的「為何未知」因果鏈（從 D1
  來源盤點 → D2 去重需求 → D3 授權/freshness gap → 結論），確保未來審計
  可追溯

## 三、Test Plan

本單無 code deliverables，test plan 指接受／拒絕標準的評估 checklists。

### 3.1 來源資格 checklist（D1）

- [ ] 每個 nominee provider 的授權文件已擷取（ToS、API docs、data license page）
- [ ] 能為至少一個 provider 產出 byte-stable content hash（同一 as_of + revision
  請求兩次得到相同 response body）
- [ ] 能為至少一個 provider 取得 revision 標記（API response header、chain height、
  文件版本號）
- [ ] 至少盤點 8 個 provider 以確保 landscape 不被單一來源認知偏差蒙蔽
- [ ] 免費層限制已逐項記錄（rate limit、asset scope、history depth、redistribution）

### 3.2 Address≠holder proof checklist（D2）

- [ ] 文件明確定義 address → cluster → entity → holder 四層映射
- [ ] 每一層的 false-positive 來源有實例
- [ ] 文檔引用至少兩篇學術或工業界文獻佐證 clustering 限制（Meiklejohn 2013、
  Androulaki 2013、Ermilov 2017 級）
- [ ] 明確禁止的 inference pattern（lost-key by inactivity、exchange=beneficial owner、
  top-address=holder）以表格逐項列出，含反例
- [ ] lost-key 可證明 vs 不可證明的判定樹有邏輯閘（signature proof → provable；
  無密碼學證據 + X 年未動 → unknown）

### 3.3 Freshness/reproducibility checklist（D3）

- [ ] 每個 eligible provider 有明確 freshness SLA 或建議 stale 天數
- [ ] content hash 重建步驟已記錄且可被獨立第三人重複
- [ ] conflict resolution 規則已案例化（至少兩個假設案例：同 provider revision
  drift、跨 provider label 衝突）
- [ ] 付費來源的 cost-sensitive issue 已草擬（issue 編號、預估範圍、審批對象）

### 3.4 Final disposition checklist（D4）

- [ ] disposition 對每個評估資產有明確分類（四選一）
- [ ] 「unknown」結論有 D1→D2→D3 可追溯因果鏈，不等於「我們沒查」
- [ ] 若結論為不可行，不帶任何暗示性數值（「某資產集中度顯然較高」→ 不可）
- [ ] 與 Parent plan §二不可妥協條件逐條對照無違反

## 四、Risks

| 風險 | 嚴重度 | 緩解 |
|---|---|---|
| 所有公開免費 entity-label 來源均禁止 redistribution 或無 PIT pinning | 高 —— 使 holder concentration 變為 permanent unknown | 誠實結案；不明確阻擋其他四維；若付費來源存在，開 cost-sensitive issue |
| 現有 provider API 不支援 `as_of` point-in-time 查詢 | 中 —— 只能採 periodic snapshot + 自記 revision，無法 ad-hoc PIT replay | 記錄無法 PIT-verify 即 ineligible，不降標 |
| Provider label 品質差異極大，無法驗證（closed-source ML model） | 中 —— 兩個 provider 的 entity map 可能完全不同，判定 conflicted | conflict 即 delta=0，不強制選邊 |
| BTC UTXO clustering 文獻成熟，但 BSC account model 聚類研究相對有限 | 中 —— BTC 可能有 eligible，BNB 仍是 unknown，造成資產不對稱 | 不對稱是事實；不補值不對齊；記錄真因 |
| 需付費來源才能達到 source_families ≥ 2 | 中 —— 進入 cost-sensitive review | 單獨開 issue，明確需要哪個 provider、最小 contract scope、預估年成本 |
| 第三方 API 不保證 revision-pin 無限保留 | 低 —— snapshot 留存成本可控 | 確認 API 至少保留 90d 歷史，不足則 self-archive 符合 license |
| Regulatory risk：特定管轄區要求不揭露機構持有部位的細粒度 identity | 低 —— TrustForge 不輸出原始 identity，只輸出 aggregated Gini/HHI/top-N 匿名指標 | 記錄合規限制；法律審查納入正式接入前 gate |

## 五、完成定義

本單 #872 在以下全部達成後關閉：

1. D1–D4 四個 deliverables 全部產出並簽入 `docs/reports/`
2. 最終 disposition 對 BTC、BNB、ETH 三個核心資產各有明確結論
3. 若結論包含 ready_for_evidence：下階段 72h data build plan outline 已交付
4. 若結論包含 need_paid_source：cost-sensitive issue 已草擬並標註 parent #872
5. Top-address concentration 不等同 holder concentration 的論證完整且有引用來源
6. 所有「未知」結論均可追溯到具體的 license／pinning／freshness／conflict gap
7. CEO 審查通過（本報告屬判斷完整性敏感，需 gray CPO + harper CISO review）
8. 本單不可阻擋 issue B、C、E 的 shadow research 進展

## 六、時程與相依

- 本單可與 issue B（issuance/supply）、C（control/governance）、E（shadow observation）平行執行
- 本單的 disposition 會影響後續是否開立 holder concentration data build issue，
  但不影響 issue F（多資產 benchmark）—— issue F 可用其他四維的已知事實進行
- 若最終 disposition 為 unknown_permanent，issue F 的 5-asset benchmark 以
  holder_concentration = unknown 參與 shadow scoring，這是已知且合法的路徑
