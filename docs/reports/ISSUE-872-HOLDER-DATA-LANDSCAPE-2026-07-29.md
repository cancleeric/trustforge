# D1: Holder Concentration Data Source Landscape

- 日期：2026-07-29
- Issue：[#872](https://github.com/cancleeric/trustforge/issues/872)
- 前置：`docs/reports/ISSUE-748-ASSET-INTRINSIC-SCORE-DIFFERENTIATION-FEASIBILITY-2026-07-29.md`
- 目標：盤點跨鏈 holder concentration 可用的 entity-labeled 資料源

## 一、Executive Summary

盤點 12 個提供 entity-labeled / cluster-labeled 鏈上地址資料的服務商。
**所有公開免費 tier 均不滿足 TrustForge PIT 契約**（無法 pin revision、無法
content-hash verify、或授權禁止 redistribution）。付費 tier 中 Arkham 與
Chainalysis 為最接近 eligible 的選項，但均需 enterprise contract review 確認
PIT pinning 與 redistribution 條款。

結論：**免費來源 zero eligible；付費來源 2–3 個 potentially eligible with contract clarification。**

## 二、Provider Inventory

### 評等標記

- **eligible**：滿足 source_families ≥ 2、PIT-pinnable、content-hash-verifiable
- **eligible_with_gaps**：基本資格滿足但存在 freshness/scope/pinning gaps
- **ineligible**：license 禁止、無法 pin、無法 hash-verify、closed-source 黑箱
- **unknown**：文件不足，需 provider contact

---

### 2.1 Arkham Intelligence
- **URL**：https://platform.arkhamintelligence.com/
- **Cluster 方法論**：自陳 co-spend heuristic、deposit-address reuse、withdrawal pattern
  matching、人工標註 + ML-assisted entity labeling
- **覆蓋**：BTC (UTXO)、ETH (account)、BSC、Polygon、Avalanche、Arbitrum、Optimism、
  Base、Solana、Tron（multi-chain，含 BTC + BSC）
- **授權**：Proprietary。Free tier：limited dashboard access（~10 entities/day）；
  API access：enterprise plan only ($15K+/yr estimated)。Redistribution clause：
  TOS §4.2 禁止未經授權重散布 raw entity data；需合約審查確認是否允許
  aggregated Gini/HHI/top-N output
- **PIT snapshot**：API 文件未明確記載 `as_of` 參數；history replay 需確認。採用
  "current + archival snapshots" model，但 revision pinning 語意不明
- **Content hash**：無文件承諾 byte-stable response；closed-source backend
- **Freshness**：Real-time balance updates；historical ledger data claimed full chain
  history；無 stale SLA
- **成本**：Free tier ≤ 10 entity lookups/day；Enterprise API $15K–$50K/yr（est.）
- **合規**：TOS 禁止未經書面授權的 automated scraping；attribution required；
  aggregated derivative works clause ambiguous
- **Final disposition**：**eligible_with_gaps**（付費 only；PIT pinning / redistribution 需 contract clarification）

---

### 2.2 Chainalysis (Reactor / Address Screening)
- **URL**：https://www.chainalysis.com/
- **Cluster 方法論**：Proprietary co-spend clustering、counterparty risk scoring、
  real-world entity attribution（KYC-linked exchange data partnerships）；業界
  gold standard for AML/KYT 標註
- **覆蓋**：BTC、ETH、ERC-20、BSC、Polygon、Solana（含 BTC + BSC）
- **授權**：Proprietary。No free tier。API via Chainalysis KYT / Reactor API
  enterprise license（estimated $25K–$100K+/yr）。Redistribution strictly
  prohibited in standard terms — 需合約 negotiation for derivative analytics
- **PIT snapshot**：Reactor 支援 historical investigation view；API 未明確支援
  `as_of` PIT query；block-height-based state replay 可行但非官方 API feature
- **Content hash**：No byte-stable reproducibility guarantee；audit trail via
  internal case management only
- **Freshness**：Real-time cluster updates；historical full chain coverage；
  無明定 stale SLA（depends on licensing tier）
- **成本**：$25K+/yr minimum；需合約審查與 compliance onboarding
- **合規**：Extremely restrictive redistribution terms；attribution required；
  end-user licensing 可能不允許 embedding in public-facing products
- **Final disposition**：**eligible_with_gaps**（付費 only；redistribution terms 是主要 blocker）

---

### 2.3 Nansen
- **URL**：https://www.nansen.ai/
- **Cluster 方法論**：Proprietary wallet labeling（"Smart Money"、"Fund"、
  "Exchange"、"Miner" etc.）；heuristic-based + on-chain behavior classification。
  不揭露底層 clustering algorithm
- **覆蓋**：ETH、Polygon、BSC、Arbitrum、Optimism、Fantom、Avalanche、Solana
  （BSC covered；**BTC NOT covered**）
- **授權**：Proprietary。Free tier：limited dashboard。API via Nansen Query
  （enterprise plan, ~$3K–$10K+/yr）。TOS 禁止 raw data redistribution
- **PIT snapshot**：API 不支援 `as_of` historical replay；current-state only
- **Content hash**：無 byte-stable 保證
- **Freshness**：Daily label updates；historical data available via Query but no
  revision pinning
- **成本**：$3K–$10K+/yr（Query API）
- **合規**：Redistribution prohibited；attribution required
- **Final disposition**：**ineligible**（無 BTC coverage；無 PIT pinning）

---

### 2.4 Glassnode
- **URL**：https://glassnode.com/
- **Cluster 方法論**：On-chain metrics + entity-adjusted supply metrics（entity-adjusted
  SOPR、exchange balances、miner balances）。Entity clustering via proprietary
  heuristics（co-spend for BTC；deposit-address labeling for account-model chains）
- **覆蓋**：BTC、ETH、ERC-20（limited alt-L1）。**BSC not covered**
- **授權**：Proprietary。Free tier：dashboard metrics only。API via
  Glassnode Studio API（Advanced/Professional tier, ~$800–$2K+/mo）。TOS §3
  prohibits redistribution of raw data；aggregated metrics use allowed
- **PIT snapshot**：API 不直接支援 `as_of` historical query；time-series endpoints
  回傳 pre-computed historical metrics（但無 revision pinning）
- **Content hash**：無 byte-stable guarantee
- **Freshness**：Daily metric updates；historical depth varies by metric
- **成本**：$800–$2K/mo（Advanced/Professional tier）
- **合規**：Attribution required；no raw redistribution
- **Final disposition**：**ineligible**（無 BSC coverage；無 PIT pinning；無 explicit entity-label API）

---

### 2.5 CoinMetrics
- **URL**：https://coinmetrics.io/
- **Cluster 方法論**：Network data API + reference rates。Entity-adjusted metrics
  via CM Entity Data（proprietary clustering）。Methodology partially documented
  in research papers（co-spend for UTXO；account-model heuristics not fully disclosed）
- **覆蓋**：BTC、ETH、ERC-20、multiple L1s。**BSC limited support**（not full entity data）
- **授權**：Proprietary。Community tier：limited API access。Entity Data：
  enterprise only。Redistribution restricted in TOS
- **PIT snapshot**：API 支援 `start_time`/`end_time` time-series queries but not
  revision-pinned point-in-time entity snapshots
- **Content hash**：無 byte-stable 保證
- **Freshness**：Daily updates；historical depth varies（BTC full；others limited）
- **成本**：Community tier free（no entity data）；Entity Data enterprise pricing
  undisclosed（est. $10K+/yr）
- **合規**：Attribution required；redistribution restricted
- **Final disposition**：**ineligible**（BSC entity coverage insufficient；無 PIT pinning）

---

### 2.6 IntoTheBlock
- **URL**：https://www.intotheblock.com/
- **Cluster 方法論**：Proprietary ML-based address classification（"Whales"、
  "Investors"、"Traders" etc.）。Concentration metrics（HHI、Gini）via
  Ownership by Concentration indicators。底層 clustering algorithm closed-source
- **覆蓋**：BTC、ETH、BSC、Polygon、multiple chains（含 BTC + BSC）
- **授權**：Proprietary。Free tier：limited dashboard indicators。API via
  ITB API（custom pricing）。Redistribution terms unclear in public docs
- **PIT snapshot**：Historical indicators available but no revision pinning mechanism
- **Content hash**：無 byte-stable 保證；closed-source ML model
- **Freshness**：Daily indicator updates
- **成本**：Custom API pricing；no public free API tier with entity data
- **合規**：Attribution required
- **Final disposition**：**ineligible**（closed-source black-box ML；無 PIT pinning；無 content-hash 保證）

---

### 2.7 Dune Analytics
- **URL**：https://dune.com/
- **Cluster 方法論**：No native entity labeling engine。Rely on community-built
  dashboards with user-defined wallet labels（e.g. @hildobby's BTC entity-adjusted
  dashboards, @21Shares' exchange labeling）。Labels are crowd-sourced, not
  audited or versioned
- **覆蓋**：BTC（via Dune BTC dataset）、ETH、BSC、multiple EVM chains
- **授權**：Free tier：public dashboards & queries。API via Dune API（paid）。
  Query results may be used with attribution
- **PIT snapshot**：No native `as_of` query；data is current-state；can simulate
  via block-number filter but no upstream entity-label revision pinning
- **Content hash**：No；query results depend on mutable community spellbook tables
- **Freshness**：Near-real-time
- **成本**：Free tier sufficient for dashboard queries；API $349+/mo
- **合規**：Attribution required；community labels lack audit trail
- **Final disposition**：**ineligible**（無 native entity resolution；community labels
  無 audit trail 且 non-reproducible；不可作為 PIT fact source）

---

### 2.8 Messari
- **URL**：https://messari.io/
- **Cluster 方法論**：Research reports + governance data。No entity-resolution
  API product。Concentration analysis in quarterly reports only（not automated data feed）
- **覆蓋**：Research covers BTC、ETH、BSC but not as automated entity-labeled data
- **授權**：Free tier：report summaries。Pro API：full reports & data feeds
- **PIT snapshot**：Not applicable（no entity-data API）
- **Final disposition**：**ineligible**（無 entity-resolution data product）

---

### 2.9 TRM Labs
- **URL**：https://www.trmlabs.com/
- **Cluster 方法論**：Proprietary blockchain intelligence for compliance/AML。
  Wallet screening + entity attribution via proprietary risk-scoring models
- **覆蓋**：BTC、ETH、BSC、multi-chain（含 BTC + BSC）
- **授權**：Enterprise only；no free tier。Redistribution strictly prohibited
- **PIT snapshot**：Unknown；API documentation not publicly accessible
- **Final disposition**：**unknown**（文件不公開；需 enterprise contact）

---

### 2.10 Elliptic
- **URL**：https://www.elliptic.co/
- **Cluster 方法論**：Proprietary wallet screening + entity attribution（Holistic
  screening）。Clustering methodology not publicly disclosed
- **覆蓋**：BTC、ETH、ERC-20、BSC（含 BTC + BSC）
- **授權**：Enterprise only；no free tier
- **PIT snapshot**：Unknown；API docs not publicly accessible
- **Final disposition**：**unknown**（文件不公開；需 enterprise contact）

---

### 2.11 Blockchain.com Explorer Tags
- **URL**：https://www.blockchain.com/explorer
- **Cluster 方法論**：Explorer-based address tagging（exchange、mining pool、
  known entity labels）。人工標註，無自動化 clustering engine
- **覆蓋**：BTC only
- **授權**：Free explorer access。No API for tag export。TOS limits automated
  data extraction
- **Final disposition**：**ineligible**（BTC only；無 API；無 audit trail；無 PIT pinning）

---

### 2.12 Opensea / Etherscan / BscScan Labels
- **Cluster 方法論**：Community-submitted address tags（Etherscan "Name Tags"、
  BscScan "Address Labels"）。Crowd-sourced，非審計級
- **覆蓋**：Etherscan：ETH/ERC-20 only；BscScan：BSC only。**No single provider covers both**
- **授權**：Free web access。API rate-limited（5 calls/sec Etherscan free tier）。
  Redistribution requires attribution
- **PIT snapshot**：No；tags are mutable and non-versioned
- **Content hash**：No；community moderation changes tags without audit trail
- **Final disposition**：**ineligible**（non-versioned crowd-sourced labels；無 PIT pinning；無 cross-chain coverage）

---

## 三、Provider Disposition Summary

| Provider | BTC | BSC | PIT-pinnable | Content-hash | Redistribution OK | Free Tier | Disposition |
|---|---|---|---|---|---|---|---|
| Arkham | ✅ | ✅ | ❓ | ❌ | ❓(contract) | ❌ | eligible_with_gaps |
| Chainalysis | ✅ | ✅ | ❓ | ❌ | ❌(strict) | ❌ | eligible_with_gaps |
| Nansen | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ineligible |
| Glassnode | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ineligible |
| CoinMetrics | ✅ | ⚠️ | ❌ | ❌ | ❌ | ❌(no entity) | ineligible |
| IntoTheBlock | ✅ | ✅ | ❌ | ❌ | ❓ | ❌(no entity API) | ineligible |
| Dune | ✅ | ✅ | ❌ | ❌ | ❓ | ✅(no entity) | ineligible |
| Messari | N/A | N/A | N/A | N/A | N/A | ❌(no product) | ineligible |
| TRM Labs | ✅ | ✅ | ❓ | ❓ | ❌ | ❌ | unknown |
| Elliptic | ✅ | ✅ | ❓ | ❓ | ❌ | ❌ | unknown |
| Blockchain.com | ✅ | ❌ | ❌ | ❌ | ❌ | ✅(no API) | ineligible |
| Explorer labels | ❌ | ❌ | ❌ | ❌ | ❓ | ✅(rate-limited) | ineligible |

## 四、Key Findings

### 4.1 Zero Free-Tier Eligible Providers

沒有任何公開免費來源滿足 TrustForge PIT 契約的三個必要條件：
1. Revision-pinnable PIT snapshot（可指定 `as_of` + revision pin 取值）
2. Content-hash-verifiable byte-stable output（相同參數產出相同 hash）
3. Redistribution-compatible license（允許嵌入分析產品輸出 aggregated indicators）

所有 inspectable 來源的授權均禁止 raw data redistribution，且無一提供
revision-pinned API 或 content-hash 保證。這是 honest-zero disposition 的客觀基礎。

### 4.2 Arkham + Chainalysis 為最接近 Eligible 的選項

兩者均覆蓋 BTC + BSC，均採用文獻公認的 clustering heuristics（co-spend、
deposit-address reuse），且均為業界公認的 entity-labeling authority。但：

- PIT pinning 語意需合約確認（API 文件未明載 `as_of` 參數）
- Redistribution 條款需合約 negotiation（現行 TOS 均禁止或嚴格限制）
- Content-hash reproducibility 無文件承諾（closed-source backend）
- 成本均為 enterprise tier（$15K–$100K+/yr estimated）

### 4.3 BSC Entity Coverage Is the Bottleneck

BTC 的 UTXO clustering 研究成熟（Glassnode、CoinMetrics、Arkham 均支援）；BSC
account-model entity clustering research 相對有限。跨 BTC + BSC 雙覆蓋的免費來源
為零，付費來源中僅 Arkham、Chainalysis、TRM、Elliptic、IntoTheBlock 宣稱支援
BSC entity data，但後三者的 API 文件不公開或無 PIT pinning。

### 4.4 Content-Hash Reproducibility 是跨 Provider 的系統性 Gap

所有被評估 provider 均以 closed-source backend 或 live API 方式提供 entity data。
無一 provider 公開承諾 byte-stable response 或提供 revision-pinned content hash。
這意味著 TrustForge 無法獨立驗證同一 `as_of` + revision 的兩次查詢產出相同
entity map — 這是 PIT 契約的核心要求。

### 4.5 Community-Labeled Sources Are Not Acceptable

Dune 社群 dashboard、Etherscan/BscScan Name Tags、Blockchain.com explorer tags
均為非審計級、非版本化的 crowd-sourced labels，不可作為 TrustForge 的 PIT fact
source。無 audit trail、無 revision history、無方法論文檔，且同一地址的 label
可能隨社群 moderation 任意變更。

## 五、Conclusion for D2/D3/D4 Feed-in

- **免費 source_families = 0**
- **付費 potentially eligible families ≤ 2**（Arkham + Chainalysis，需 contract clarification）
- **所有來源均缺乏 content-hash reproducibility 保證**
- **BSC entity data 是跨 provider 的稀缺資源**

若 Arkham 或 Chainalysis 的 contract negotiation 成功確認 PIT pinning 與
redistribution，則 source_families 可達 2（滿足 gate），但 content-hash
reproducibility gap 仍為未解問題。詳見 D3 授權/freshness 限制文件。
