# TrustForge 決賽 Live Demo 錄影腳本（繁體中文）

> 目的：提供可直接照表錄製的決賽示範流程。建議同時保留「從送出題目到產出完成」的未剪輯母帶，以及約 7 分鐘的評審版；若正式分析等待超過腳本時間，只能壓縮等待片段，不可剪接出不存在的成功狀態。

## 錄影前置條件

錄影負責人開始前逐項確認，未通過就停止錄影並排除問題：

- [ ] 使用已授權的正式站 URL；首頁、`/analyze`、`/history` 均可開啟。
- [ ] 桌面解析度 1920×1080，瀏覽器縮放 100%，通知、書籤列與密碼管理器提示已關閉。
- [ ] BTC／ETH／SOL／BNB／XRP 可選，官方題型「多源整合／假設驗證／比較分析」與題目輸入可用。
- [ ] 已用一筆非正式測試確認分析 job 能從 queued 進入 running，再到 completed；記下備援完成 run，但主錄影仍須展示本次新送出的 run。
- [ ] Final Report、Evidence、Log 三個下載動作均成功；下載資料夾先清空，方便在畫面上核對檔名。
- [ ] Source / Config 交件包已綁定欲展示的 commit SHA，README、`pyproject.toml`、`uv.lock`、部署／執行說明可開啟。
- [ ] 畫面、網址列、開發者工具與下載檔均沒有 token、API key、password、cookie、私人金鑰或內部主機名稱。
- [ ] 麥克風音量與游標高亮已測試；正式錄影全程保留系統時間或 OBS 時間碼。

## 建議示範題目

幣種選 `BTC`，題型選「多源整合」，輸入：

> 分析 BTC 過去兩週的市場表現，整合價格、鏈上、新聞、社群與監管訊號，說明各類資料是否一致，並列出可能推翻結論的條件。

這段題目只要求系統整合與判讀可取得的證據，不預設漲跌、不要求投資建議，也不保證每一類來源都有資料。

## 7 分鐘評審版分鏡

| 時間 | 畫面操作 | 旁白 | 字幕重點 |
|---|---|---|---|
| 00:00–00:20 | 開啟正式站首頁，停留在產品名稱與主要入口；網址列只短暫露出公開網域。 | 「這是 TrustForge，一個把多源加密市場資訊轉成可追溯判斷的 AI Agent。今天會從新題目開始，完整展示分析、報告、證據與執行紀錄。」 | `多源整合 · 信任提煉 · 可追溯` |
| 00:20–00:50 | 點「新增分析」進入分析頁。依序指向幣種、官方題型與題目欄位。 | 「決賽題目由幣種、題型和自然語言問題組成。這次選 BTC 的多源整合，要求系統說明來源一致程度與可能推翻結論的條件。」 | `BTC｜多源整合｜不預設答案` |
| 00:50–01:10 | 貼上建議題目，慢速捲動讓全文可讀，再按「開始分析」。 | 「送出後建立的是一個新的分析 job。TrustForge 不會先放一份固定報告，也不會在證據不足時硬湊高信心結論。」 | `建立新 run｜不足則 abstain` |
| 01:10–02:10 | 保持進度區可見，依序指向五個節點；若等待較久，母帶不停止，評審版可用時間碼跳切並標示實際等待時間。 | 「流程有五個可觀察階段：來源蒐集、主張抽取、信任推理、證據組裝、報告交付。每一階段都綁定同一個 run 與資料快照；失敗會留下狀態，不會假裝完成。」 | `1 來源蒐集 → 2 主張抽取 → 3 信任推理 → 4 證據組裝 → 5 報告交付` |
| 02:10–03:05 | job 完成後停在 Final Report 頂部，依序框選結論／市場判斷、方向、信心或 decision state、關鍵依據與限制。 | 「Final Report 是主要評分件。先看結論，再看支撐它的事實與推論。信任分數描述證據完整度與可信程度，不是價格上漲機率；限制和 could-flip 條件會明確保留。」 | `Final Report｜Facts ≠ Inferences｜Trust Score ≠ 漲跌預測` |
| 03:05–03:45 | 展示正反方或跨源訊號區，指向 supporting、contrarian、divergence／insufficient coverage。 | 「系統不只挑支持答案的資料。反方、低信任與跨源分歧會分開呈現；若某一來源類型缺席，報告應說明覆蓋不足，而不是宣稱沒有風險。」 | `正反證據並列｜保留分歧與缺口` |
| 03:45–04:35 | 開啟 Evidence List／Evidence 表格，展開一筆支持證據與一筆反方或受限證據；指向 source、fetched_at、content_reference、related_claim／claim_id 與 trust。 | 「每個關鍵判斷可回到 Evidence List。評審可以抽查來源、取得時間、引用內容與對應主張；低信任證據不會被偽裝成同等權重。」 | `source｜fetched_at｜content_reference｜related_claim` |
| 04:35–05:20 | 回到 Hermes Execution Panel，展開五階段摘要與來源執行明細；顯示 run id、各節點狀態、事件數與耗時。 | 「Execution Log 是執行佐證，不是主報告。它保留時間戳、流程摘要與公開允許的工具／資料取得事件，讓這次 run 可以被稽核，同時不公開原始秘密或敏感參數。」 | `Execution Log｜同一 run｜公開欄位經過限制` |
| 05:20–05:55 | 依序按「報告」、「Evidence」、「Log」下載；打開瀏覽器下載清單，只顯示檔名與成功狀態，不展開可能含內部資料的本機路徑。 | 「三個按鈕分別輸出 Markdown 報告、Evidence JSON 與 Execution Log。它們是分工清楚、可以交叉核對的交付件。」 | `report.md｜evidence.json｜execution log` |
| 05:55–06:30 | 切到已準備好的公開 GitHub repository／commit 頁籤，顯示 commit SHA、README、依賴 lock 與部署說明；不要開啟任何 `.env` 或 secrets 頁面。 | 「第四件 Source / Config 是 commit 綁定的 repository 交件包，不是假裝成伺服器下載檔。它包含原始碼、依賴鎖定與執行說明，只記錄環境變數名稱和秘密注入方式，不包含秘密值。」 | `Source / Config｜commit-bound｜zero secrets` |
| 06:30–07:00 | 回到 Final Report，停在結論、信心與 Evidence 引用同框的位置，最後顯示本次 run id。 | 「TrustForge 的價值不是替評審下投資結論，而是把多源資料、推理限制與證據鏈放在同一份可追溯結果裡。這次展示由新題目開始，四件交付物都能回到同一個 run 與 source commit。」 | `一個 run｜四件交付｜可追溯、可抽查` |

## 等待與失敗時的錄影規則

- 新 run 若仍在執行，評審版可剪去中間等待，但跳切前後都要顯示相同 run id，並加字幕「實際等待 X 分 Y 秒」；未剪輯母帶需完整保存。
- 超時、失敗或證據不足時，不切換到另一個 run 冒充本次結果。旁白應說明終態，再以明確標示的「備援既有 run」展示介面能力。
- 某來源種類沒有有效資料時，照實展示 coverage／limits；不可口頭宣稱系統已抓到畫面上不存在的來源。
- 正式站若沒有 Source / Config runtime 頁面，使用 commit 綁定的公開 repository 頁籤；不可把本機資料夾或私有設定頁說成正式交付介面。
- 不開開發者工具的 Network、Application、Storage 或環境設定；這些畫面容易暴露 cookie、headers 或秘密值。

## 收音與字幕校對詞表

- `Final Report`：分析報告，主要評分件。
- `Evidence List`：證據清單，支撐或挑戰報告中的主張。
- `Execution Log`：執行紀錄，保留時間戳與流程摘要，不等於 Final Report。
- `Source / Config`：commit 綁定的程式碼、依賴與執行說明，不包含秘密值。
- `Trust Score`：資訊與證據可信／完整程度，不是價格方向機率，也不是投資建議。
- `Decision State / abstain`：證據不足或規則要求時，系統保留判斷或拒絕下結論。

## 錄完立即驗收

- [ ] 畫面完整包含題目輸入、五階段、Final Report、Evidence List、Execution Log 與 Source / Config。
- [ ] 新 run 的 run id 在進度、報告與下載片段一致；若有跳切，已標示實際等待時間。
- [ ] 至少抽查一筆 Evidence 與對應 claim，且清楚呈現來源與取得時間。
- [ ] 三個下載檔均由 UI 實際產出，沒有使用事先放好的檔案冒充。
- [ ] Source / Config 顯示正確 commit SHA，依賴 lock 與操作說明可見。
- [ ] 全片沒有秘密值、cookie、內部 IP、本機路徑、私人通知或個資。
- [ ] 旁白沒有聲稱不存在的來源、production 能力、模型結果或預測準確率。
- [ ] 未剪輯母帶與評審版均保存；檔名包含 UTC 時間、run id 與 source commit。

## 參考文件

- `docs/competition/DEMO-EVIDENCE-CHECKLIST.md`
- `docs/technical-docs/16-competition-submission.md`
- `docs/competition/COMPETITION-OFFICIAL.md`
- `docs/competition/TRUST-EXPLAINABILITY.md`
