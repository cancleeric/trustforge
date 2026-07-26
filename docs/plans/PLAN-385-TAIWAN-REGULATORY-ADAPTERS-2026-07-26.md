# PLAN #385 — 台灣監管來源 adapters 開發計劃

- 建立日期：2026-07-26
- 對應 issue：[#385](https://github.com/cancleeric/trustforge/issues/385)
- 前置 Discovery：`docs/audit/TAIWAN-REGULATORY-SOURCE-DISCOVERY-385.md`
- 起始分支：`develop`（現行 HEAD `7b069cb`）

---

## 一、範圍

### 做

以標準 Source / Document contract 接入 FSC 與 MOPS 兩類台灣官方監管來源，滿足 #385 全部驗收條件，並修正現行 stub 的 8 項缺口與 3 個地雷。

### 不做（沿用 #385「明確不做」）

- 不繞過 robots、登入或付費牆
- 不把 BlockTempo 等媒體當官方監管真值
- 不在本單調整 Trust Kernel 權重
- 不在 coverage 驗證前把 references 頁翻 ✅
- 不在 coverage 驗證前加 Radar 台灣監管維度

---

## 二、目標架構

現行 `TaiwanRegulatorySource` 只有單一 `_build_document()`，無法同時承載 XML 與 JSON 兩類來源。拆為兩層：

```
TaiwanRegulatorySource(Source)          共用層
  ├─ host allowlist 驗證（含 openapi.twse.com.tw）
  ├─ content_hash / fetched_at / live_source 標記
  ├─ _record_source_event() 降級紀錄
  ├─ 關鍵字閘門 _CRYPTO_TERMS
  └─ PIT 閘門（台北時區日界）
      │
      ├─ _RssFeedSource            FSC 三個 feed
      │     ├─ XML parse、guid dataserno 當 canonical id
      │     ├─ _MAX_BYTES = 8 MB + </rss> 完整性 sentinel
      │     └─ description HTML 去標籤
      │
      └─ _OpenApiSource            MOPS / TWSE / TPEx
            ├─ JSON parse、key .strip() 正規化
            ├─ 顯式欄位映射表（TWSE 與 TPEx 兩套欄位名）
            ├─ 民國年 + 發言時間 轉換
            └─ 無 URL → 組 query-page reference URL
```

### Source 命名與登記

| `name` | 類型 | 端點 | 歷史 |
|---|---|---|---|
| `fsc-news` | RSS | `RSS/Messages?serno=201202290009` | 800 筆 |
| `fsc-penalty` | RSS | `RSS/Messages?serno=201202290003` | 498 筆 |
| `fsc-notice` | RSS | `RSS/Messages?serno=201202290001` | 800 筆 |
| `mops-twse` | JSON | `openapi.twse.com.tw/v1/opendata/t187ap04_L` | 當日 |
| `mops-tpex` | JSON | `www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O` | 當日 |
| `twse-punish` | JSON | `openapi.twse.com.tw/v1/opendata/t187ap22_L` | 年度 |

全部 `kind = "regulatory"`，全部登記進 `cache.COIN_AGNOSTIC_SOURCES`。

---

## 三、分階段交付

### PR-1：基礎設施與 fixture（純新增，零行為變動）

**交付**

1. `tests/fixtures/taiwan/` 存本輪實測的真實 response：
   - `fsc_penalty.xml`（裁剪為前 20 items，保留完整 XML 結構與 `</rss>`）
   - `fsc_penalty_truncated.xml`（刻意截斷，測 sentinel）
   - `mops_twse.json`（含 `'主旨 '` 結尾空白原樣）
   - `mops_tpex.json`
   - `twse_punish.json`
2. `src/trustforge/ingestion/tw_datetime.py` 新模組：
   - `roc_date_to_date(s)` — `"1150725"` → `date(2026,7,25)`
   - `roc_datetime_to_epoch(date_s, time_s)` — `("1150725","70003")` → epoch（注意 `發言時間` 無前導零）
   - `rfc822_gmt_to_taipei_date(s)` — `"Tue, 21 Jul 2026 00:00:00 GMT"` → 台北日界
   - `pit_visible_at(published, listed)` — 取較晚者
3. `tests/test_tw_datetime.py` — 含 PIT 邊界測試、無前導零測試、GMT 跨日測試

**驗收**：`pytest tests/test_tw_datetime.py -v` 全綠；既有測試不受影響。

---

### PR-2：FSC RSS adapter

**交付**

1. `_RssFeedSource` 實作，`fsc-news` / `fsc-penalty` / `fsc-notice` 三個 instance
2. `_MAX_BYTES = 8 * 1024 * 1024` + `</rss>` 完整性 sentinel；不完整 → 回空 + `_record_source_event(outcome="truncated")`
3. canonical id：`f"tw-reg:fsc:{dataserno}"`（來源自身唯一鍵，非內容 hash）
4. `content_hash`：`hashlib.sha256(raw_body)` 前 16 碼
5. 關鍵字閘門 `_CRYPTO_TERMS = ("虛擬資產","VASP","加密","穩定幣","洗錢防制","比特幣","以太","數位資產")`
6. `description` HTML 去標籤與 entity 還原
7. fail-closed：timeout / 非 200 / XML ParseError / 結構不符 → 回空 + 記錄事件，不拋

**測試**（`tests/test_taiwan_regulatory.py` 擴充）

- fixture 驅動 parse，驗 Document 全欄位（`schema_version` / `source` / `url` / `published_at` / `fetched_at` / `content_hash`）
- 截斷 fixture → 回空且 outcome 為 `truncated`
- 同一 `dataserno` 出現兩次 → 只產一個 Document（鏡像不重複計票）
- 非白名單 host → 拒絕
- 關鍵字閘門：無關銀行裁罰不入池
- PIT：分析時間設為 7/21，7/22 上架的資料不得出現

---

### PR-3：MOPS / TWSE OpenAPI adapter

**交付**

1. `_OpenApiSource` 實作，`mops-twse` / `mops-tpex` / `twse-punish` 三個 instance
2. key `.strip()` 正規化 + 顯式欄位映射表：

```
MOPS_FIELD_MAP = {
  "mops-twse": {"code": "公司代號", "name": "公司名稱", "subject": "主旨",
                "date": "發言日期", "time": "發言時間", "body": "說明"},
  "mops-tpex": {"code": "SecuritiesCompanyCode", "name": "CompanyName", "subject": "主旨",
                "date": "發言日期", "time": "發言時間", "body": "說明"},
}
```
（`subject` 統一寫 `"主旨"`，靠 key `.strip()` 吸收 TWSE 的結尾空白）

3. 無 URL → 組 MOPS 查詢頁 reference URL，`meta["url_kind"] = "query-page"`
4. `meta["history_backfillable"] = False`（重大訊息）／`True`（裁罰專區）
5. canonical id：`f"tw-reg:{name}:{sha256(code+date+time+subject)[:12]}"`
6. 缺鍵 / 型別不符 / 民國日期無法解析 → 跳過該筆並記錄，不整批炸（比照 `regulatory.py` 單詞失敗隔離精神）

**測試**

- `'主旨 '` 結尾空白 fixture 必須正常取值（回歸鎖 KeyError）
- TWSE 與 TPEx 兩套欄位名各自解析正確
- 民國日期 + 無前導零時間轉換正確
- 缺鍵 fixture → 該筆跳過、其餘正常
- 全部端點失敗 → 回空 + 記錄，不拋

**至此滿足驗收條件「至少兩個官方來源有 adapter 與 fixture/contract tests」。**

---

### PR-4：管線接線

**交付**

1. `taiwan_regulatory.py` 新增 `build_taiwan_regulatory_sources() -> list[Source]`
2. `base.py:338-347` `build_sources()` 串接
3. `scripts/fetch_scheduler.py:112-117` 加 import 與呼叫，端點間加禮貌延遲
4. `cache.py:206` `COIN_AGNOSTIC_SOURCES` 加入六個 source name
5. `base.py:70` `_DEFAULT_DISABLED_SOURCES` **先加入全部六個**，預設不啟用

**預設 disabled 的理由**：比照 `hoyabit-ticker` 的謹慎作法。台灣監管來源對每個幣都是「全市場通用」，一旦誤放無關公告進池會污染所有幣的證據。先手動用 `set_source_enabled_override` 開啟觀察，coverage 與雜訊率驗過再翻預設。

**測試**：`build_sources()` 含台灣來源但預設 disabled；override 開啟後可正常取得。

---

### PR-5：coverage 驗證與 references 翻牌（獨立輪次，不與前四 PR 併）

**前置**：PR-4 上線後排程實跑一段時間，累積真實 coverage 數據。

**交付**

1. coverage 報告：各 source 實際入池筆數、關鍵字命中率、雜訊率
2. coverage 足夠才做：
   - `docs/audit/REFERENCES-TRUTH-AUDIT.md:143-145, 213, 242-244` 更新
   - `~/kiro/trustforge-devlog/references.html:125-129, 208, 285-291` 更新
   - `scripts/check_references_truth_audit.py:127-129` 守門條件同步調整
3. Radar 台灣監管維度：**僅在 coverage 足夠時才評估**，coverage 不足則留白，不補 0、不用 LLM 猜測

---

## 四、驗收條件對照

| #385 驗收條件 | 落點 |
|---|---|
| 提交官方資料契約與法遵/使用條款評估 | ✅ `docs/audit/TAIWAN-REGULATORY-SOURCE-DISCOVERY-385.md` |
| 至少兩個官方來源有 adapter 與 fixture/contract tests | PR-2（FSC）+ PR-3（MOPS） |
| Document 含 schema_version、source、url、published_at/fetched_at、content hash | PR-2 / PR-3 |
| timeout/403/schema drift/空結果 fail-closed 且有降級紀錄 | PR-2 / PR-3 的 `_record_source_event` |
| 同一官方公告的鏡像不能算多票 | PR-2 canonical `dataserno` id；PR-3 穩定 sha256 id |
| PIT 測試排除分析時間後發布資料 | PR-1 `pit_visible_at` + PR-2/3 PIT 測試 |
| 無資料時雷達留白，不補 0、不用 LLM 猜測 | PR-5 gate |
| references 僅在 verified 後改為 ✅ | PR-5 gate（含 checker 同步） |

---

## 五、風險與對策

| 風險 | 對策 |
|---|---|
| FSC feed 3 MB 被 512 KB 靜默截斷 | 自帶 8 MB 上限 + `</rss>` sentinel，非猜測而是主動偵測 |
| TWSE `'主旨 '` 結尾空白造成 KeyError | key `.strip()` 正規化 + fixture 保留原樣鎖回歸 |
| 數百筆無關政府公告淹沒每幣證據池 | 關鍵字閘門 + 預設 disabled 觀察期 |
| MOPS 重大訊息無歷史，PIT replay 受限 | meta 標 `history_backfillable: False`，references 寫明 coverage 起始日 |
| 政府站點 schema drift | fixture/contract test + 單筆失敗隔離 + 降級紀錄 |
| 對政府站請求過頻 | `COIN_AGNOSTIC_SOURCES` 廣播 + 禮貌延遲 + 只在排程發真請求 |

---

## 六、執行順序

```
PR-1 → PR-2 → PR-3 → PR-4 →（排程觀察期）→ PR-5
```

PR-1〜PR-4 皆不動 Trust Kernel 權重、不動 DB schema、不需 migration token。
