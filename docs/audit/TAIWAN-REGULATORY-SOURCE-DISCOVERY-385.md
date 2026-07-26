# 台灣監管來源 Discovery 與法遵評估（issue #385）

- 建立日期：2026-07-26
- 對應 issue：[#385](https://github.com/cancleeric/trustforge/issues/385) feat(data) 台灣監管來源 adapters
- 上游依賴：#380（已 CLOSED，不再阻擋）
- 狀態：Discovery 完成，`blocked-external` label 已移除
- 本文對應 #385 驗收條件第 1 項「提交官方資料契約與法遵/使用條款評估」

---

## 一、結論

**四個目標來源全部有可用的官方介面，全部免申請帳號、免 API key。**
`blocked-external` 的前提（官方 API endpoint 未確認）已不成立。

本輪以 curl 逐一實測，所有端點回應與大小如下記錄，可重跑驗證。

---

## 二、端點實測結果

實測時間 2026-07-26，User-Agent 帶聯絡信箱 `TrustForge/1.0 (eric.wang@hurricanesoft.com.tw)`。

| 來源 | 端點 | HTTP | Content-Type | 大小 | 筆數 |
|---|---|---|---|---|---|
| MOPS 上市重大訊息 | `https://openapi.twse.com.tw/v1/opendata/t187ap04_L` | 200 | `application/json` | 12,020 B | 8 |
| MOPS 上櫃重大訊息 | `https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O` | 200 | `application/json` | 4,570 B | 3 |
| TWSE 裁罰專區 | `https://openapi.twse.com.tw/v1/opendata/t187ap22_L` | 200 | `application/json` | — | 21 |
| FSC 新聞稿 | `https://www.fsc.gov.tw/RSS/Messages?serno=201202290009&language=chinese` | 200 | `text/xml` | 2,448,269 B | 800 |
| FSC 裁罰案件 | `https://www.fsc.gov.tw/RSS/Messages?serno=201202290003&language=chinese` | 200 | `text/xml` | 3,071,165 B | 498 |
| FSC 重要公告 | `https://www.fsc.gov.tw/RSS/Messages?serno=201202290001&language=chinese` | 200 | `text/xml` | 1,348,932 B | 800 |
| TWSE OpenAPI 索引 | `https://openapi.twse.com.tw/v1/swagger.json` | 200 | `application/json` | 306,043 B | 143 endpoints |
| TPEx OpenAPI 索引 | `https://www.tpex.org.tw/openapi/swagger.json` | 200 | `application/json` | 476,923 B | — |

FSC RSS feed 索引頁：`https://www.fsc.gov.tw/ch/main.jsp?websitelink=rss.jsp&mtitle=RSS`

TWSE swagger 中其他監管相關 dataset（本輪盤點，未全接）：

| 路徑 | 說明 |
|---|---|
| `/opendata/t187ap04_L` | 上市公司每日重大訊息 |
| `/opendata/t187ap22_L` | 上市公司金管會證券期貨局裁罰案件專區 |
| `/opendata/t187ap23_L` | 上市公司違反資訊申報、重大訊息及說明記者會規定專區 |
| `/opendata/t187ap25_L` `/t187ap26_L` `/t187ap27_L` | 經營權及營業範圍異動專區 |
| `/announcement/punish` | 集中市場公布處置股票 |
| `/announcement/notice` | 集中市場當日公布注意股票 |
| `/announcement/notetrans` | 集中市場公布注意累計次數異常資訊 |

---

## 三、法遵與使用條款評估

### robots.txt

| 主機 | robots.txt | 內容 |
|---|---|---|
| `www.fsc.gov.tw` | 200 | 僅 `User-agent: Googlebot` / `Disallow: /uploaddowndoc` |
| `openapi.twse.com.tw` | 404 | 無 |
| `mops.twse.com.tw` | 404 | 無 |
| `www.tpex.org.tw` | 302 | 無實體 robots |

`fsc.gov.tw` 唯一的 Disallow 路徑 `/uploaddowndoc` 與本單使用的 `/RSS/` 路徑無關，且該規則僅針對 Googlebot。

### 合規判定

- 全部為政府公開資料介面：**無登入、無付費牆、無需繞過任何 robots 規則**，符合 #385「明確不做」第一項。
- 不使用 BlockTempo 等媒體作為監管真值，符合「明確不做」第二項。
- 本單不動 Trust Kernel 權重，符合「明確不做」第三項。

### 授權標示義務

TWSE / TPEx OpenAPI 與 FSC 網站資料屬政府資料開放範疇，散布時須標示來源。實作上以 Document `meta` 保留 `source`、`url`、`published_at`、`fetched_at`，UI 引用時顯示來源機關全名：

- `mops-twse` → 臺灣證券交易所公開資訊觀測站
- `mops-tpex` → 財團法人中華民國證券櫃檯買賣中心
- `twse-punish` → 臺灣證券交易所
- `fsc-*` → 金融監督管理委員會

### 禮貌性措施（沿用 `regulatory.py` 既有作法）

- 固定 User-Agent 含聯絡信箱
- 多端點間加請求延遲
- 真實請求只發生在 `scripts/fetch_scheduler.py` 排程；產品路徑一律讀 cache

---

## 四、資料契約：兩類來源本質不同

| 面向 | FSC RSS | MOPS / TWSE OpenAPI |
|---|---|---|
| 格式 | XML（RSS 2.0） | JSON |
| 永久連結 | ✅ `<guid isPermaLink="true">` 帶 `dataserno` + `dtable` | ❌ **資料集內無任何 URL 欄位** |
| 歷史深度 | 498〜800 筆，回溯數年 | 重大訊息＝**當日 snapshot**；裁罰專區＝年度累積 |
| 全文 | ✅ `<description>` 含整份裁處書 | 主旨 + 說明 |
| 時間格式 | RFC822，**日精度、GMT** | 民國年 `1150725` + `發言時間` `"70003"` |
| 單次回應 | 1.3〜3.0 MB | 4〜12 KB |

歷史深度實測依據：

- `t187ap04_L` 8 筆，`發言日期` 全為 `1150725`（單一日期）
- `mopsfin_t187ap04_O` 3 筆，`發言日期` 全為 `1150725`（單一日期）
- `t187ap22_L` 21 筆，`發函日期` 橫跨 `1150105`〜`1150518`（**有歷史**）

**含意**：MOPS 重大訊息無法回填歷史，只能靠排程逐日累積 cache 當檔案庫。接線上線日之前的重大訊息永久取不到。裁罰專區則有年度歷史，PIT replay 價值較高。

### FSC RSS item 結構（實測一筆）

```xml
<title><![CDATA[台新綜合證券股份有限公司違反證券管理法令處分案(金管證券罰字第1150383460號)]]></title>
<pubDate>Tue, 21 Jul 2026 00:00:00 GMT</pubDate>
<link><![CDATA[https://www.fsc.gov.tw/ch/home.jsp?id=131&parentpath=0,2&mcustomize=multimessage_view.jsp&dataserno=202607220001&toolsflag=Y&dtable=Penalty]]></link>
<guid isPermaLink="true"><![CDATA[...同 link...]]></guid>
<description><![CDATA[<div class="zbox">...整份裁處書 HTML...]]></description>
```

可用 tag 僅 `title` / `pubDate` / `link` / `guid` / `description`。

### MOPS 欄位（實測）

```
TWSE t187ap04_L keys:
  ['出表日期', '發言日期', '發言時間', '公司代號', '公司名稱', '主旨 ', '符合條款', '事實發生日', '說明']
                                                              ^^^^^^ 結尾有空白字元

TPEx mopsfin_t187ap04_O keys:
  ['Date', '發言日期', '發言時間', 'SecuritiesCompanyCode', 'CompanyName', '主旨', '符合條款', '事實發生日', '說明']

TWSE t187ap22_L keys:
  ['出表日期', '發函日期', '股票代號', '公司名稱', '違規事由', '違反法規', '裁處情形']
```

同一份 MOPS 重大訊息資料，TWSE 與 TPEx 使用**兩套不同欄位名**（`公司代號`/`SecuritiesCompanyCode`、`公司名稱`/`CompanyName`、`出表日期`/`Date`），且 TWSE 的 `'主旨 '` 帶結尾空白。

---

## 五、內容相關性驗證

> **訂正（2026-07-26 第二輪量測）**：本節初版用的關鍵字集含「加密」「詐騙」
> 「洗錢」等寬鬆詞，得出 FSC 新聞稿 83 筆、裁罰 60 筆命中。逐筆檢視後發現
> **絕大多數為誤報**，該組數字不可用。以下為嚴格詞集重新量測的結果。

### 關鍵字集的誤報量測

對 FSC 裁罰 feed（498 筆）分別套用兩組詞：

| 詞集 | 命中 | 逐筆檢視 |
|---|---|---|
| 寬（含「加密」「洗錢防制」） | 42 | **38 筆**只因「洗錢防制」命中（銀行 AML 裁罰）、**4 筆**只因「加密」命中（資安「資料加密」缺失）→ 全數誤報 |
| 嚴（「加密貨幣」「加密資產」等複合詞） | 1 | 僅 1 筆，且是臺灣銀行 AML 案順帶提及「虛擬資產」 |

結論：**「加密」不可單獨成詞**（會收到資安加密），**「洗錢防制」「詐騙」不可入列**（與加密無必然關係）。最終詞集見 `taiwan_regulatory._CRYPTO_TERMS`。

### 嚴格詞集下的真實 coverage

| 來源 | 總筆數 | 通過閘門 | 內容評估 |
|---|---|---|---|
| FSC 新聞稿 | 800 | **23** | **唯一高價值來源**，含 2026-06-30「立法院院會三讀通過『虛擬資產服務法』」、VASP 防詐辦法預告 |
| FSC 重要公告 | 800 | 8 | 全為「修正各業別金融檢查手冊」例行公告，順帶提及虛擬資產，價值低 |
| FSC 裁罰案件 | 498 | 1 | 目前近乎無加密內容；保留以承接未來 VASP 開罰 |
| MOPS 上市重大訊息 | 8（當日） | 0 | — |
| MOPS 上櫃重大訊息 | 3（當日） | 0 | — |
| TWSE 裁罰專區 | 21 | 0 | — |
| TPEx 裁罰專區 | 18 | 0 | — |

**誠實結論**：7 個來源中只有 `fsc-news` 目前有實質加密監管內容。其餘 6 個 coverage 為 0 或近 0。這符合 #385「資料不足時誠實留白」的要求——adapter 仍應建立（契約正確、fail-closed、未來事件發生時能承接），但 **radar 台灣監管維度必須留白，references 頁不得翻 ✅**。

---

## 六、現行程式碼缺口盤點

現況：`src/trustforge/ingestion/taiwan_regulatory.py`（106 行，commit `7b069cb`）四個 `fetch()` 全為 `return []`。全 repo grep 只有 `tests/test_taiwan_regulatory.py` 引用，**尚未接入任何管線**。

| # | 缺口 | 位置 | 影響 |
|---|---|---|---|
| 1 | 無 `build_taiwan_regulatory_sources()`，未進 `build_sources()`、未進排程 | `base.py:338-347`、`scripts/fetch_scheduler.py:112-117` | 即使 fetch 寫好也不會有資料進管線 |
| 2 | doc id 用內建 `hash(text)` | `taiwan_regulatory.py:47` | 受 `PYTHONHASHSEED` 隨機化，換 process 就換 id，違反「鏡像不能算多票」與 cache key 穩定性 |
| 3 | meta 缺 `fetched_at` / `content_hash` / `live_source` | `taiwan_regulatory.py:53-57` | 未滿足驗收條件第 3 項 |
| 4 | 未呼叫 `_record_source_event()` | 全檔 | 「timeout/403/schema drift 要有降級紀錄」無落點 |
| 5 | 未登記 `COIN_AGNOSTIC_SOURCES` | `cache.py:206` | 台灣監管內容對每幣相同，不登記則每幣各打一次真 API |
| 6 | 無關鍵字閘門 | 全檔 | `base.py:185-200` 分支 3「無幣別提及→全市場通用，納入每一幣」會讓數百筆無關政府公告淹沒每個幣的證據池 |
| 7 | `ALLOWED_TW_HOSTS` 漏 `openapi.twse.com.tw` | `taiwan_regulatory.py:19-24` | 實際要打的主機不在白名單內 |
| 8 | `_validate_host()` 定義了但無人呼叫 | `taiwan_regulatory.py:39` | 白名單形同虛設 |

---

## 七、三個實作地雷

### 地雷 1：TWSE 欄位名帶結尾空白

`r["主旨"]` 打在 TWSE `t187ap04_L` 上會 **KeyError**（實際鍵為 `'主旨 '`）。TPEx 同欄位則無空白。

對策：讀取前先對所有 key 做 `.strip()` 正規化，並寫顯式欄位映射表，不直接 index 原始鍵。

### 地雷 2：`safe_fetch` 512 KB 上限會靜默截斷 FSC feed

- `safe_fetch.py:212` 預設 `max_bytes=512 * 1024`
- `safe_fetch.py:288` 為 `body = resp.read(max_bytes)`，**超過直接截斷且不回報**
- FSC 裁罰 feed 實測 3,071,165 B，為上限的 6 倍

若比照 `regulatory.py:49` 的 512 KB 設定，會拿到截斷 XML → parse 失敗 → 永遠回空清單，且無人知道原因。

對策：FSC adapter 自帶 `_MAX_BYTES = 8 * 1024 * 1024`，**並額外驗 body 結尾含 `</rss>`** 作為完整性 sentinel（因 `safe_fetch` 不會回報是否截斷），不符則視為降級並記錄 `outcome="truncated"`。

### 地雷 3：MOPS 無 URL、重大訊息無歷史

驗收條件要求 Document 含 `url`，但 MOPS 資料集無任何 per-announcement 連結。

對策：以 `公司代號` 組 MOPS 查詢頁的穩定 reference URL，meta 標 `url_kind: "query-page"`，**不假裝是永久連結**；另標 `history_backfillable: False`，references 頁須寫明 coverage 起始日，避免 radar 誤判有長期資料。

FSC 則有 `dataserno` 永久連結，`url_kind: "permalink"`。

### 地雷 4：Python ≥ 3.13 的 TLS 嚴格檢查會擋掉台灣政府憑證

實測（Python 3.14.6 / OpenSSL 3.6.2）：

```
www.fsc.gov.tw   → [SSL: CERTIFICATE_VERIFY_FAILED] Missing Subject Key Identifier
www.tpex.org.tw  → 同上
openapi.twse.com.tw → 正常
```

原因：Python 3.13 起 `ssl.create_default_context()` 預設開啟 `VERIFY_X509_STRICT`，
強制 RFC 5280 檢查；`fsc.gov.tw` 與 `tpex.org.tw` 的憑證缺 Subject Key Identifier
擴充欄位而被拒。`curl` 走不同的 TLS 實作故不受影響。

實測各版本：

| Python | `VERIFY_X509_STRICT` 預設 | 這兩個來源 |
|---|---|---|
| 3.11.14 | False | ✅ 正常 |
| 3.12（`Dockerfile` 生產基底 `python:3.12-slim`） | False | ✅ 正常 |
| 3.13.12 / 3.14.6 | **True** | ❌ 全數失敗 |

**目前生產不受影響**（Dockerfile 為 `python:3.12-slim`）。但**若日後升到 3.13+，
FSC 與 TPEx 兩來源會整批失效**。adapter 對此已 fail-closed：失敗會拋
`TaiwanRegulatoryUnavailable` 並記 WARNING，不會偽裝成「沒有相關公告」。
升版時須先驗這兩個來源。

---

## 八、PIT 邊界定義

實測樣本：某裁罰案 `pubDate = Tue, 21 Jul 2026`（發文日期），但 `dataserno = 202607220001`（7/22 上架），兩者相差一日。

**採用定義：`published_at = max(發文日期, 上架日期)`**，即資料真正對外可見的時間。

理由：PIT fail-closed 要防的是「宣稱 7/21 就看得到、實際 7/22 才上網」這類未來資訊洩漏。取較晚者是保守側。

補充約束：

- FSC `pubDate` 的 `00:00:00 GMT` 是**日期標籤而非真實時刻**。換算後為台北同日 08:00（`2026-07-21 00:00 GMT` → `2026-07-21 08:00+08:00`），日期標籤與台北日曆日一致。
- 真正的問題是**只有日精度**：無從得知該日幾點上線。fail-closed 的作法是把可見時間視為**台北該日結束**（`23:59:59+08:00`），否則會宣稱比實際更早就看得到，形成未來資訊洩漏。
- MOPS 為民國年 `1150725` + `發言時間` `"70003"`（＝07:00:03，注意無前導零），有到秒的精度，依實際時刻處理即可，不需套用日結束規則。

---

## 八之二、VASP 登記業者名單（issue #721）

七個文字公告來源之外，唯一能給出**結構化實體清單**的通道。

| 項目 | 位址 |
|---|---|
| 證期局 VASP 專區 | `https://www.sfb.gov.tw/ch/home.jsp?id=1053&parentpath=0,8` |
| 名單本體 | `https://www.fsc.gov.tw/userfiles/file/<民國日期>更新完成洗錢防制登記之VASP業者名單.pdf` |

- 檔名嵌民國日期（`1150722` ＝ 2026-07-22），**不可寫死 URL**，每次更新都會變
- 專區頁另掛「疑似洗錢態樣例示.pdf」等無關附件，須以檔名同時含「登記」與
  「VASP」篩選
- 實測 8 家登記業者，**第一筆即 HOYA BIT**（禾亞數位科技，統編 90615871）

### 地雷 5：PDF 版面規則本質上不可靠 → 改由模型整理

初版用規則切版面，連撞三種例外：

1. 統一編號與電話號碼都是 8 碼數字（`電話：02-77566286`）——純數字錨點在
   整份 PDF 的 14 個 8 碼數字中會多抓 **6 筆假業者**
2. 「有限公司」會被硬斷行拆開，實測有 `有 限公司` 與 `有限公 司` 兩種
3. 部分業者**沒有電話欄**（末欄是網站 URL），靠「前一筆以電話結尾」切界失效

每修一種就冒出下一種。最終改為：**pypdf 抽文字 → 模型結構化 → 規則回源比對**。

規則的職責從「切」變成「驗」：

- `temperature=0` + 強制 tool use，輸出必須符合固定 schema
- `_verify_against_source()` 把每個欄位值去空白後回原文比對，比不到就丟棄
  該筆並記 WARNING（選填欄位則清空不丟）
- 這是 #385「不用 LLM 猜測」的守門——模型只能整理，產出的每個字都必須在
  官方原文裡找得到

交叉驗證：規則版與模型版對同一份 PDF 產出**完全一致的 8 家業者**，且模型版
零驗證 WARNING。

### 地雷 6：URL 裡的半形冒號

備註欄是 `標籤：值` 格式（全形冒號），但網站欄的值本身含半形冒號
（`https://…`）。以冒號當終止符會把 URL 砍成空字串。

---

## 九、後續

開發計劃見 `docs/plans/PLAN-385-TAIWAN-REGULATORY-ADAPTERS-2026-07-26.md`。

references 頁狀態翻 ✅ 的守門：`scripts/check_references_truth_audit.py:127-129` 會在台灣那行出現 ✅ 時主動 raise。此為刻意設計，**須待 coverage 實測通過後，連同 checker 一併調整**，不可單獨改文件繞過。
