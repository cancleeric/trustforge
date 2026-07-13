# TrustForge 獨立稽核落差修復計劃（2026-07-13）

> 背景：老闆 Eric Wang 要求對稍早一輪獨立唯讀稽核發現的 3 個確認屬實落差，
> 撰寫分析與修改計劃並開對應 GitHub issue。**本輪不改任何程式碼**，僅新增本文件。
> 對應 issue：#176（docstring 矛盾）、#177（anti-manipulation 命名落差）、
> #178（動態信譽收斂條件依賴話術）。

---

## 落差 1：`hoyabit.py` docstring 與實際 social/Reddit 接線自相矛盾（最優先，可快速修）

### 問題描述
`src/trustforge/ingestion/hoyabit.py` 第 19-21 行 docstring 寫：

> 「social（Reddit）部分：**已確認不接**（Reddit 2025-11 終止 self-service，見
> milestone 收斂指示）。本模組與整條管線都不含任何 social/Reddit 真實或 stub
> 連接器——`hoyabit` 是唯一的『待接真實 API』佔位，專指交易所一手行情。」

但事實是：

- `src/trustforge/ingestion/social.py` 第 179 行起完整實作 `RedditCryptoSource`，
  會對 `https://www.reddit.com/r/CryptoCurrency/search.rss`、
  `https://www.reddit.com/r/Bitcoin/search.rss` 發真實 HTTP 請求（第 4-5、43、55-56
  行）。
- `src/trustforge/ingestion/base.py` 第 244、252 行的 `build_social_sources()` 已將
  `RedditCryptoSource` 接入生產 `collect()` 流程，非停用/移除狀態。
- 既有 issue #8（原始追蹤）、#153（`[#8 blocked] D2.1 Reddit 社群真實 OAuth —
  待雲端 IP 憑證/7-13 工作坊`）都已記載「Reddit 連接器存在但受阻」的事實，
  與 hoyabit.py docstring 的敘述直接矛盾。

### 根因
推測是先前某輪 milestone 收斂時，Reddit self-service API 終止的決策被寫進
`hoyabit.py`（不相關檔案）的 docstring 作為情境註記，但後續 `social.py` 又
以 RSS/`.rss` 端點（非 self-service OAuth API）方式重新接上 Reddit，docstring
未同步更新，造成「文件說沒接、程式碼說有接」的自相矛盾。此類矛盾若被審查者
（含決賽評審）讀到程式碼，會直接對專案誠實性/文件品質產生負面觀感。

### 修改方案
1. 修正 `hoyabit.py` 第 19-21 行 docstring，移除「本模組與整條管線都不含任何
   social/Reddit 真實或 stub 連接器」的錯誤陳述，改為準確描述：
   - social/Reddit 連接器**存在**於 `ingestion/social.py`（`RedditCryptoSource`），
     且**已接入**生產 `collect()` 流程（`base.py::build_social_sources()`）。
   - 其現況限制：因缺乏 Reddit OAuth 憑證，雲端環境 IP 常態被 Reddit 判定
     `403`，導致實務上「有接但常空手」，對應 issue #8、#153 的既定追蹤。
   - 明確區分：`hoyabit.py` 本檔案是「交易所一手行情」的待接真實 API 佔位；
     social/Reddit 是另一條「已接但受阻」的管線，兩者不可混為一談。
2. 修正後應在 docstring 中加入 cross-reference（`見 ingestion/social.py`、
   `見 issue #8 #153`），避免未來再度漂移。
3. 純文件修正，不涉及程式邏輯或測試行為變更，不需要新增/修改測試案例。

### 驗收標準
- [ ] `hoyabit.py` docstring 不再出現「本模組與整條管線都不含任何 social/Reddit
      真實或 stub 連接器」此類與事實矛盾的陳述。
- [ ] docstring 準確描述 Reddit 連接器現況：存在、已接生產流程、受 OAuth 缺失
      導致 cloud IP 常態 403。
- [ ] docstring 明確區分 hoyabit（交易所行情佔位）與 social（Reddit，已接但
      受阻）為兩條不同管線。
- [ ] 修正後跑一次既有測試（`pytest -k hoyabit or social`）確認未破壞任何既有
      行為（本輪修正應為零行為變更，純註解）。

---

## 落差 2：「anti-manipulation detection」命名與實質判別力落差

### 問題描述
`src/trustforge/trust/scoring.py` 第 104-107 行的 `_MANIP_PATTERNS` 是純正則
關鍵詞黑名單（`to the moon`、`暴漲`、`翻倍`、`shill`、`喊單`、`穩賺`、
`financial advice`、`pump`、`快上車`、`百倍`），由 `_manipulation_penalty`/
`_manipulation_flags`（約 330-370 行）使用，僅比對文字表面關鍵詞是否命中，
並非行為模式或統計異常偵測。

`insights.py` 第 384 行另有 `detect_manipulation_burst`（協同發文密集度訊號），
但第 360 行明確定案為「本指標為資訊型警示、不併入 trust 扣分（informational-only，
見 `ScoredClaim.info_flags`）」，僅供人工判讀，不影響最終信任分數。

實質落差：真正的協同操縱團伙可以完全不使用任何敏感詞，純靠時間點洗量
（timing-based coordination）操縱資訊環境，目前機制對此類手法**無偵測能力**。
若對外簡報/文件使用「anti-manipulation detection」這種措辭，容易讓評審誤以為
系統具備行為/統計層級的操縱偵測能力，構成誇大陳述風險。

### 根因
功能命名（`_manipulation_penalty`、`detect_manipulation_burst`）在早期實作時
即以「操縱偵測」為概念命名，但實作手段（關鍵詞黑名單 + informational-only 的
密集度訊號）與命名所暗示的能力範疇（行為/統計異常偵測）之間存在落差，且此
落差未在對外文件/簡報材料中明確揭露、澄清邊界。

### 修改方案
**程式碼命名/文件（本輪不動程式碼，留待後續 PR）：**
1. 評估是否將 `_manipulation_penalty` 等內部函式命名調整為更準確反映實作方式
   的名稱（如 `_keyword_manipulation_penalty`），或至少在 docstring 中明確標注
   「基於關鍵詞黑名單，非行為/統計異常偵測」。
2. `detect_manipulation_burst` 的 docstring 已有 informational-only 定調，需確認
   對外文件（README/pitch deck）是否同步引用此邊界說明。

**決賽簡報話術修改建議（優先，成本低、影響對外溝通誠實性）：**
1. **避免**單獨使用「anti-manipulation detection」這種暗示行為/統計層級偵測的
   措辭。
2. **建議改用**分層描述：
   - 「關鍵詞層級的可疑用語標記（keyword-based flagging）」— 描述
     `_manipulation_penalty` 的實際能力。
   - 「協同發文密集度資訊型警示（informational burst signal，不影響信任分）」—
     描述 `detect_manipulation_burst` 的實際能力與定位。
3. 若被評審追問「能否偵測純靠時間點洗量、不用敏感詞的協同操縱」，誠實話術：
   「目前的密集度訊號（burst detection）是朝這個方向的第一步，但目前是
   informational-only、尚未納入評分，且不含統計顯著性檢定；純行為模式的
   操縱偵測是我們的後續路線圖項目，不是今天已完成的能力。」
4. Demo 講稿中若展示 `_manipulation_penalty`/flags 相關 UI，建議加註「基於
   關鍵詞比對」字樣，避免視覺呈現與口頭描述之間產生能力誇大的落差。

### 驗收標準
- [ ] 對外簡報/pitch 文件（若本輪或後續輪次有更新）不再使用未加限定詞的
      「anti-manipulation detection」；改用能準確反映「關鍵詞標記」與
      「informational-only 密集度警示」的措辭。
- [ ] Demo 講稿備妥上述誠實話術，可應對評審追問「是否偵測非關鍵詞式協同操縱」。
- [ ] （後續 PR，非本輪）評估 `_manipulation_penalty` 相關函式/docstring 是否
      需要更精確命名或補充邊界說明；不影響現有測試與評分邏輯。

---

## 落差 3：動態信譽收斂機制（`_dynamic_reputation`）的有條件依賴需誠實話術

### 問題描述
`src/trustforge/trust/scoring.py` 約第 1090-1247 行的迭代信譽傳播算法
（TruthFinder/CRH 類機制，`_dynamic_reputation`）**依賴 Bedrock stance
client** 才會實際啟動迭代收斂：

- `stance_client=None` 時（第 1292、1323 行附近註解），機制對信譽是
  **no-op**——`agree_n`/`contradict_n` 皆為 0，`final == prior`，即離線/無
  LLM 情境下該機制實質不執行任何迭代信譽更新。
- 此行為已由測試
  `tests/test_w2_enable.py::test_run_agent_pipeline_dynamic_reputation_offline_is_honest_noop`
  與 `tests/test_trust_scoring.py`（第 644 行引用）明確驗證並定性為
  「誠實的 no-op」（即離線時不假裝有跑，而是老實不動分數）。
- 文件面已將此列為已知風險/限制，但決賽對外簡報若被問「這個動態信譽收斂功能
  平常有在跑嗎」，需要一致且誠實的話術，避免臨場語塞或無意誤導評審以為
  該機制在任何情況下都全程運作。

### 根因
`_dynamic_reputation` 的設計本質上是「用 LLM stance classification（Bedrock）
判斷兩則 claim 是否同向/反向，據此做跨來源信譽的迭代傳播」。這個依賴是
設計上必要的（沒有 stance 判斷就無法建立 agree/contradict 邊，迭代收斂
無從談起），並非 bug，而是「有條件啟動的進階功能」，但此條件性尚未系統化
整理進決賽簡報的問答準備材料。

### 修改方案
**本輪（今天）：**
1. 在本文件中明確整理話術腳本（見下方「決賽簡報話術」），供 CPO/CEO/展示者
   在決賽現場統一口徑使用。
2. 確認 `docs/plans/TRUSTFORGE-STATUS-2026-07-13.md`、
   `docs/qa/CONFORMAL-FINDING.md` 等既有文件中是否已收錄此限制（本輪僅讀取
   確認，不修改既有文件；若未收錄，於 issue 中標記由後續輪次補上 cross-link）。

**決賽簡報話術修改建議：**
1. 若被問「動態信譽收斂機制平常有在跑嗎」，誠實回答模板：
   「這個機制在有 Bedrock LLM 可用時（線上、有 stance client）才會啟動迭代
   信譽收斂；離線或沒有 LLM 資源時，我們刻意讓它是**誠實的 no-op**——
   不會假裝算了信譽分數，最終信譽值就是初始信譽（prior），不會偷偷退化成
   一個看起來有跑但其實是假數據的狀態。這個 no-op 行為本身有測試覆蓋
   （`test_run_agent_pipeline_dynamic_reputation_offline_is_honest_noop`）。」
2. **避免**在 demo 中展示「動態信譽收斂」功能時，若當下環境沒有配置 Bedrock
   stance client，卻讓畫面呈現看似正在迭代收斂的效果（需 demo 前確認展示
   環境確實有 stance client 配置，或明確口頭標注目前是 no-op 狀態）。
3. 若評審追問「離線的可靠性」，可強調這正是系統誠實性設計的一部分：
   「寧可清楚告知功能未啟動，也不用假數據填補」——把這個限制轉化為
   誠實性/工程紀律的加分敘事，而非防禦性辯解。

### 驗收標準
- [ ] 決賽簡報問答準備材料中納入上述話術模板，展示者/CEO 熟悉此問答。
- [ ] Demo 展示動態信譽收斂功能前，確認當下環境的 Bedrock stance client
      配置狀態，避免無意中呈現與實際運作狀態不符的畫面。
- [ ] 若既有文件（STATUS/CONFORMAL-FINDING）未收錄此限制，於後續輪次補上
      明確段落與 cross-link 至本文件、對應測試。

---

## 附錄：對照既有 issue，避免重複開票

稽核前已存在的相關 issue：#8、#153、#167、#168、#169、#170、#171、#172。
逐一比對後確認：

- #153（`[#8 blocked] D2.1 Reddit 社群真實 OAuth`）記載的是「Reddit OAuth
  憑證/雲端 IP 被擋」的**功能性**問題，與本輪落差 1（docstring 文字矛盾）
  是不同層次的問題（一個是程式碼會不會動，一個是文件寫得對不對），
  不重複，本輪另開票並在新 issue 中交叉引用 #8、#153。
- #167（`[HOYA BIT] 真實資料接線`）聚焦交易所行情連接器規格對接，與 social/
  Reddit docstring 矛盾無直接關聯。
- #171（決賽敘事強化：分層評分 UI、Evidence Trail Cards 等）與本輪落差 2、3
  的「決賽簡報話術對齊」主題相關但範疇不同（#171 是 UI/呈現層強化，本輪落差
  2、3 是「措辭準確性/誠實話術」），故仍另開新票，並在新 issue 中註記與
  #171 的關聯，避免未來被誤判為完全重複。
- 未發現既有 issue 涵蓋落差 2（`_manipulation_penalty` 命名落差）與落差 3
  （`_dynamic_reputation` 條件依賴話術）的具體內容，故新開票。

新開 issue：
- #176 — `[快速修正] hoyabit.py docstring 與 social/Reddit 真實接線自相矛盾`
- #177 — `[決賽話術對齊] anti-manipulation detection 命名與實質判別力落差（關鍵詞黑名單 vs 行為/統計偵測）`
- #178 — `[決賽話術對齊] 動態信譽收斂機制依賴 Bedrock stance client，離線為誠實 no-op`
