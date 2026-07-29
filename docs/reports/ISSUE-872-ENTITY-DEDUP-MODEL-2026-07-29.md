# D2: Entity-Resolution Dedup Model & Forbidden Inference List

- 日期：2026-07-29
- Issue：[#872](https://github.com/cancleeric/trustforge/issues/872)
- 目標：定義 address → cluster → entity → holder 四層嚴格區分及禁推清單

## 一、Core Thesis

> **Top-N address concentration ≠ holder concentration。**

單一 holder 可控制數千個地址（operational wallet segmentation、exchange hot wallets、
institutional custody structure），單一地址可代表多個 beneficial owner（exchange
omnibus wallet、pooled staking contract、bridge escrow）。任一層級的跳級推論
（address → holder）均會產出系統性錯誤。

本文件定義四層映射路徑、每層的 false-positive 來源、最小 coverage 閾值，以及
明確禁止的 inference pattern。Address 就是 address，直到有可驗證的 entity label
將其升格為 cluster → entity → holder。

## 二、Address → Cluster

### 2.1 Definition

**Cluster** 是一組被 heuristic 判定由同一 controlling entity 控制的鏈上地址集合。
Cluster 是 technical-level grouping，不帶有 legal identity 或 beneficial ownership
的 inference。

### 2.2 Clustering Heuristics（文獻基礎）

#### BTC UTXO Model

| Heuristic | 機制 | 文獻來源 |
|---|---|---|
| Multi-input co-spend | 同一 TX 的多個 input 地址由同一 entity 控制（需共同簽署） | Meiklejohn et al. (2013), "A Fistful of Bitcoins" |
| One-time change detection | 識別交易中的找零地址（新地址、不同 address type pattern） | Androulaki et al. (2013), "Evaluating User Privacy in Bitcoin" |
| Address reuse (conservative) | 同一地址多次出現在 input side 表明同一 entity 持續控制 | Ron & Shamir (2013), "Quantitative Analysis of the Full Bitcoin Transaction Graph" |
| Peel chain detection | 大額 UTXO 被逐步拆分到不同地址的鏈式 pattern | Meiklejohn et al. (2013) |

#### Account Model (ETH/BSC)

| Heuristic | 機制 | 文獻來源 |
|---|---|---|
| Deposit-address reuse | 多用戶向同一 exchange deposit address 入金，該地址為 exchange cluster | Victor & Lüders (2019), "Measuring Ethereum-based ERC20 Token Networks" |
| Gas-funding pattern | 同一 funder address 為多個新地址發送 gas ETH/BNB 用於部署或首次交易 | Ermilov et al. (2017), "Automatic Bitcoin Address Clustering" (adapted for account model) |
| DEX pool routing | 透過 DEX liquidity pool 的交易對手模式識別關聯地址 | Chen et al. (2021), "Understanding Ethereum via Graph Analysis" |
| Contract creation factory | 同一 factory contract 部署的多個合約地址可 cluster 為同一 developer entity | Pinna et al. (2019), "A Petri Nets Model for Blockchain Analysis" |

### 2.3 Known False-Positive Sources for Clustering

| False-Positive 情境 | 影響 | 機制 |
|---|---|---|
| **CoinJoin / PayJoin** | 將不相關的多個用戶 cluster 為同一 entity | 多個 independent 用戶的 input 合併於單一 TX，co-spend heuristic 失效 |
| **Mixer / Privacy pool** | 破壞 cluster boundary，無法 trace deposit→withdrawal | Tornado Cash、Wasabi Wallet |
| **Batched exchange withdrawal** | 將多個 independent 用戶的 withdrawal 地址 cluster 為同一 entity | Exchange 批量出金 TX 將不相關的接收地址置於同一 TX output set |
| **Shared multisig wallet** | 單一 multisig 合約可能代表多個 independent signer entity | Gnosis Safe 合約地址的 signer set 各為獨立 entity |
| **CEX internal transfer** | Exchange 內轉不產生鏈上 TX，cluster heuristic 無法觀測 | 用戶間的 exchange 內轉在鏈上不可見 |
| **Lightning Network channel** | Channel open/close TX 的地址歸屬取決於 channel funding，無法從單向 TX 判定 | LN channel 的 funding output 地址不代表任何單一 entity 的持有量 |
| **Airdrop farming pool** | 多個 independent 用戶的地址透過 farming contract 交互而被 cluster | 合約交互地址不代表共同控制 |

### 2.4 Cluster ≠ Entity Boundary

- 一個 entity 可控多個 cluster（operational separation：hot wallet cluster vs cold storage cluster、exchange deposit cluster vs withdrawal cluster）
- 一個 cluster 可能含多個 entity（shared exchange wallet infrastructure、dust attack victim set、airdrop farming pool）
- **Cluster 是必要的技術中間層，不可直接等同 holder**

## 三、Cluster → Entity

### 3.1 Definition

**Entity** 是具有可驗證 legal 或 organizational identity 的持有主體。Entity label
來源必須能追溯到可審計的 attribution method，不可僅依靠 heuristic。

### 3.2 Valid Label Propagation Sources

| Source | 方法 | 限制 |
|---|---|---|
| **Exchange deposit address tagging** | 透過 deposit→withdrawal 鏈路追蹤識別 exchange-controlled 地址 | 僅標註 "exchange" label，不區分 exchange 作為 custodian vs beneficial owner |
| **On-chain identity protocol** | ENS (.eth)、Space ID (.bnb)、Lens Protocol handle 直接綁定地址 | 域名持有者 ≠ 唯一 beneficial owner；域名可能被出售或轉移 |
| **Explorer public tags** | Etherscan/BscScan 社群標註（經 explorer team moderation） | 社群標註非審計級；標籤可被 moderator 移除或變更 |
| **Self-attestation** | 機構公開錢包地址清單（Grayscale holdings address、MicroStrategy BTC address、Tesla BTC address、El Salvador government wallet、Tether treasury） | 需定期驗證地址是否仍由宣稱 entity 控制；self-attestation 可能過時或誤導 |
| **OpSec leakage / forensic** | Doxxed address（blockchain forensics 確認）、known hack address、DOJ/FBI seized funds announcement | 需要可驗證的 forensic 報告或 official 公告來源 |
| **Compliance/AML provider label** | Chainalysis、TRM、Elliptic 的 KYC-linked exchange data + risk scoring | Proprietary data；label quality 取決於 provider data partnerships；無法獨立驗證 |

### 3.3 Invalid / Forbidden Entity Inference

以下 inference patterns **嚴禁**用於 entity attribution，均會產出不可驗證的假 entity：

| 禁止的 Inference | 為何錯誤 | 反例 |
|---|---|---|
| **"exchange 錢包" = exchange 持有** | Exchange 是 custodian，非 beneficial owner。Exchange 錢包的資產屬於其用戶 | Binance cold wallet 的 BTC 不屬於 Binance 公司資產負債表；FTX 案證明 exchange 持有與 custodian 持有的區別在法律上至關重要 |
| **"長期未動 UTXO" = lost keys** | 無金鑰遺失的密碼學證據不可假定遺失。可能只是 HODL | ~3.7M BTC 超過 5 年未移動（Chainalysis 2023），但大部分歸屬於 early adopters 的 long-term HODL，非遺失 |
| **"大戶地址" = institutional investor** | 無 KYC 或 self-attestation 資料不可假定身分 | Top-100 BTC address 包含多個 exchange cold wallet，非機構投資人 |
| **"Satoshi-era address" = Satoshi** | 多個早期礦工同時存在；Patoshi pattern 僅標記可能的早期礦工之一，且仍有學術爭議 | 2009–2010 期間有數十個活躍礦工（Lerner 2013 Patoshi pattern）；無法區分 |
| **"Wall Street 持有大部分 BTC"** | 需要時間點一致、去重且具名的 entity map，不可從新聞標題推論 | 2024 Q1 spot ETF 持有 ~4% of circulating supply（BitcoinTreasuries.net），遠非 "大部分" |
| **"Top-N address 持有 X% supply → 集中度高"** | Address concentration 未做 entity dedup（exchange wallet aggregation、custodian aggregation） | Top-100 BTC addresses ~15% of supply（2024），但 entity-resolved 後 top-100 entities 可能僅 ~8%（Arkham estimate） |
| **"Whale address accumulating → smart money"** | Accumulation pattern 僅說明該地址在買入，不代表 entity 的投資決策品質或身分 | 單一 exchange deposit address 的累積可能反映數萬個用戶的 collective buying |

## 四、Entity → Holder Concentration

### 4.1 Definition

**Holder concentration** 衡量有益所有權（beneficial ownership）在可識別 entity 之間的
分布集中度。所有 custodian-held 資產除非能追溯到 identifiable beneficial owner，
否則歸屬 unknown。

### 4.2 Normalization Formula

```
分子 = Σ(beneficial_holder_entity_i持有的supply)
分母 = circulating_supply − provably_unspendable − timelocked_归属发行方
```

其中：
- `circulating_supply` = total supply − provably unspendable
- `provably_unspendable` 的判定見 §五
- `timelocked_归属发行方` = 合約鎖倉且不可提前解鎖，且歸屬發行方的過渡性持有
  （如 foundation vesting schedule 中尚未解鎖的 token），非 third-party staking
- Custodian 地址的持有量若無 beneficial owner label，歸屬 unknown，不計入任何
  holder 的 numerator
- Bridge 合約鎖倉量：若可驗證跨鏈鎖倉對應關係（wrapped token 發行量 = bridge
  locked native supply），歸屬 wrapped token 持有者；否則歸屬 unknown

### 4.3 Concentration Metrics

| Metric | 定義 | 對 Entity Coverage 敏感度 | 使用建議 |
|---|---|---|---|
| **Gini coefficient** | 0 (完全均等) ~ 1 (極度集中) | 中：低 coverage 時可能高估集中度（unknown 被忽略） | Primary metric；需搭配 coverage ratio |
| **HHI (Herfindahl-Hirschman Index)** | Σ(market_share_i²)，0 ~ 10,000 | 高：低 coverage 明顯低估 HHI | Secondary metric；提供 top-entity granular view |
| **Top-N % (N ∈ {1, 5, 10, 50, 100})** | Top-N entity 持有量占總 supply 比例 | 中：低 coverage 時 top-N 可能包含 unknown entity cluster | 輔助解釋；與 Gini/HHI 一起呈現 |
| **Nakamoto coefficient** | 需多少 entity 聯合才可控制 ≥ 51% supply | 高：低 coverage 顯著高估分散度 | 治理相關輔助指標 |

### 4.4 Minimum Entity-Label Coverage Threshold

| 條件 | 閾值 | 理由 |
|---|---|---|
| Entity-labeled supply 占 circulating supply 比例 | ≥ 60% | 低於 60% 時 Gini/HHI/top-N 均不可靠；unknown tail 可能包含 whale entities |
| Source families with entity labels | ≥ 2 | 符合現有 PIT 契約 gate；防止單一 provider 支配 |
| Entity label agreement rate (cross-provider) | ≥ 80% on overlapping labeled addresses | 低 agreement rate 表示 entity attribution 品質不足；conflicted = 0 contribution |

**若未達最小 coverage 閾值：disposition = unknown，不允許 partial estimate。**

## 五、Lost-Key / Unspendable Verifiability

### 5.1 Provably Unspendable（可計入分母扣除）

| 類別 | 證明方法 | 示例 |
|---|---|---|
| **OP_RETURN outputs** | 鏈上可驗證：OP_RETURN script 鎖定 output 為不可花費 | 任何 `OP_RETURN` output |
| **Genesis block coinbase** | UTXO set 驗證：genesis coinbase TX 被 hardcoded 排除於 validation | BTC genesis block 50 BTC coinbase |
| **Null data outputs** | Script validation：provably unspendable script pattern | `OP_RETURN <data>` |
| **Zero / burn address** | 發送到無已知私鑰的地址（0x0000...0000、0x0000...dead） | ETH burn address 0x0000000000000000000000000000000000000000 |
| **Documented key destruction with cryptographic proof** | 公開的 signature 證明擁有權後確認私鑰銷毀 + 該地址之後無 TX | James Howells 硬碟 case：有 public key 證明 mining reward，但私鑰遺失非"銷毀"，歸屬 unknown |

### 5.2 Known Lost Keys（可計入分母扣除——需密碼學證據）

| 條件 | 證明門檻 | 示例 |
|---|---|---|
| 公開發布遺失金鑰事件 + 可驗證 signature of known address | 1) 有 public statement of key loss；2) 有 historical signature from the address 證明 previous control；3) 地址之後無任何 outgoing TX | 需 forensic 分析報告級證據 |
| Documented seized funds (DOJ/FBI) | Official government forfeiture notice + on-chain freeze/seizure TX | DOJ 2022 Bitfinex hack seizure (94,000 BTC)；地址現由 USG 控制，非 "lost" |
| 資安事件 technical report with cryptographic proof | 第三方 security firm 的 forensic report 含 signature proof of previous control + key compromise evidence | 需具名 security firm 報告 |

### 5.3 NOT Provable Lost（不可計入分母扣除）

| 情境 | 為何不可證明 | 應歸類為 |
|---|---|---|
| X 年未移動的 UTXO | 可能只是 HODL，無金鑰狀態的密碼學證據 | **unknown** — 不可從 inactivity 推論 lost |
| "Satoshi coins" | 多個早期礦工同時存在；無身分證據；無法區分單一 vs 多個 entity | **unknown** — Patoshi pattern 是 heuristic 非 identity |
| "業界共識"或"社群普遍認為" | 無可驗證來源即不合格 | **unknown** — consensus ≠ evidence |
| 早期無值挖礦時代的 mining reward | 當時 BTC/ETH 無市場價值，礦工可能未備份私鑰 —— 但無密碼學證據證明私鑰已遺失 | **unknown** — probabilistic ≠ provable |
| 單一 forensic firm 的 estimate（無 signature proof） | Estimate 非 fact；需要可驗證的密碼學證據鏈 | **unknown** — estimate ≠ evidence |

### 5.4 Lost-Key Decision Tree

```
Does address have on-chain outgoing TX after the alleged loss date?
├── YES → NOT lost (someone controls it) → unknown for holder calc
└── NO
    └── Is there cryptographic proof of prior control (signed message from address)?
        ├── YES
        │   └── Is there documented evidence of key destruction (published key deletion, forensics)?
        │       ├── YES → **provably lost** → exclude from denominator
        │       └── NO → **unknown** (prior control proven, but loss not provable)
        └── NO → **unknown** (no proof of ever being controlled)

Is the address provably unspendable by script validation?
├── YES → **provably unspendable** → exclude from denominator
└── NO → apply above lost-key decision tree
```

## 六、Cross-Chain Dedup

### 6.1 Same Entity Across Chains

同一 entity 的主鏈 + L2 + wrapped token 持有需 cross-chain 去重：
- BTC + WBTC (Ethereum) + BTCB (BSC)：同一 BTC 在兩條鏈上的 wrapped representation，
  歸屬同一 beneficial holder（需透過 bridge lock/burn event mapping 驗證）
- ETH + WETH + stETH：需區分 native staking（屬於 ETH holder）vs liquid staking
  derivative（LSD 持有者為 beneficial owner）
- BNB + wrapped BNB (BSC)：BNB 即 BSC native token，不存在 wrapping
- 跨鏈橋合約持有的原生資產歸屬 wrapped token 持有者（若 1:1 pegged + verifiable）

### 6.2 Cross-Chain Dedup Rules

| 情境 | Dedup Rule |
|---|---|
| 同一 BTC 被 bridge lock + mint WBTC | 計入 WBTC holder 的 BTC exposure；bridge lock address 不計入任何 BTC holder |
| Entity A 持有 BTC + WBTC（不同 address）| 若可驗證同一 entity label → aggregate as single holder |
| Entity A 持有 ETH + stETH via Lido | stETH 為 liquid staking derivative，計為 ETH exposure；需確認不 double-count ETH + stETH |
| CEX 在多條鏈有 deposit address | 若 service-provider label 一致（"Binance" on ETH + BSC）→ aggregate custodian holdings；但仍歸屬 unknown（custodian not beneficial owner） |

## 七、Forbidden Inference Pattern Master List

| # | 禁止的 Inference | 類別 | 替代處理 |
|---|---|---|---|
| 1 | Address balance → holder balance | Address ≠ Entity | 無 entity label 的地址歸屬 unknown |
| 2 | Top-N address % → holder concentration | Address ≠ Entity | 需 entity-resolved + cross-chain dedup |
| 3 | Exchange wallet balance = exchange owns it | Custodian ≠ Beneficial Owner | 歸屬 unknown（除非 beneficial owner identity 可驗證） |
| 4 | X years inactive UTXO = lost keys | Inactivity ≠ Loss | 歸屬 unknown |
| 5 | "Satoshi coins" = Satoshi's holdings | Heuristic ≠ Identity | 歸屬 unknown |
| 6 | Whale address = institutional investor | Behavior ≠ Identity | 歸屬 unknown（除非有 self-attestation 或 KYC-grade entity label） |
| 7 | Cluster = Entity | Cluster ≠ Entity | Cluster 是技術中間層；entity 需 identity attribution |
| 8 | Explorer community tag = verified entity | Crowd-sourced ≠ Verified | 不可用作 PIT fact source |
| 9 | "Wall Street / institutions own most of BTC" | News headline ≠ Data | 需 time-consistent、deduped、named entity map |
| 10 | Any numeric estimate without entity-resolved source | Estimate ≠ Fact | Disposition = unknown |

## 八、Literature References

1. Meiklejohn, S., Pomarole, M., Jordan, G., Levchenko, K., McCoy, D., Voelker, G.M., & Savage, S. (2013). "A Fistful of Bitcoins: Characterizing Payments Among Men with No Names." *IMC '13*.
2. Androulaki, E., Karame, G.O., Roeschlin, M., Scherer, T., & Capkun, S. (2013). "Evaluating User Privacy in Bitcoin." *FC '13*.
3. Ron, D., & Shamir, A. (2013). "Quantitative Analysis of the Full Bitcoin Transaction Graph." *FC '13*.
4. Ermilov, D., Panov, M., & Yanovich, Y. (2017). "Automatic Bitcoin Address Clustering." *IEEE ICMLA 2017*.
5. Victor, F., & Lüders, B.K. (2019). "Measuring Ethereum-based ERC20 Token Networks." *FC '19*.
6. Chen, T., Li, Z., Zhu, Y., Chen, J., Luo, X., Lui, J.C.S., Lin, X., & Zhang, X. (2021). "Understanding Ethereum via Graph Analysis." *ACM TOIT*.
7. Pinna, A., Ibba, S., Baralla, G., Tonelli, R., & Marchesi, M. (2019). "A Petri Nets Model for Blockchain Analysis." *The Computer Journal*.
8. Lerner, S.D. (2013). "The Well Deserved Fortune of Satoshi Nakamoto, Bitcoin Creator, Visionary and Genius." *Bitslog*.
