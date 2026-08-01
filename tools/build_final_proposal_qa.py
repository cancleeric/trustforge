from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)

INK = "172033"; BLUE = "3157C8"; SKY = "12A8C7"; MINT = "21B894"
PALE = "EEF6FF"; LIGHT = "F6F8FC"; AMBER = "F59E0B"; RED = "B42318"

def shade(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd"); shd.set(qn("w:fill"), fill); tcpr.append(shd)

def cell_text(cell, value, bold=False, color=INK, size=9):
    cell.text = ""
    p = cell.paragraphs[0]; p.paragraph_format.space_after = Pt(0)
    r = p.add_run(str(value)); r.bold = bold; r.font.name = "Microsoft JhengHei"
    r.font.size = Pt(size); r.font.color.rgb = RGBColor.from_string(color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

def base(title, subtitle, label):
    d = Document(); sec = d.sections[0]
    sec.page_width = Inches(8.5); sec.page_height = Inches(11)
    sec.left_margin = sec.right_margin = Inches(.82)
    sec.top_margin = Inches(.72); sec.bottom_margin = Inches(.68)
    normal = d.styles["Normal"]
    normal.font.name = "Microsoft JhengHei"; normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor.from_string(INK)
    normal.paragraph_format.space_after = Pt(6); normal.paragraph_format.line_spacing = 1.16
    for name, size, color in [("Title", 29, BLUE), ("Heading 1", 18, BLUE), ("Heading 2", 13, SKY), ("Heading 3", 11, INK)]:
        s = d.styles[name]; s.font.name = "Microsoft JhengHei"; s.font.size = Pt(size)
        s.font.bold = True; s.font.color.rgb = RGBColor.from_string(color)
        s.paragraph_format.keep_with_next = True
    h = sec.header.paragraphs[0]; h.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r = h.add_run("TRUSTFORGE  /  TEAM 11"); r.bold = True; r.font.size = Pt(8); r.font.color.rgb = RGBColor.from_string(BLUE)
    f = sec.footer.paragraphs[0]; f.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = f.add_run("2026 雲湧智生生成式 AI 黑客松｜智慧交易・HOYA BIT"); r.font.size = Pt(8); r.font.color.rgb = RGBColor(100,110,125)
    p = d.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(label); r.bold = True; r.font.size = Pt(10); r.font.color.rgb = RGBColor.from_string(MINT)
    p = d.add_paragraph(style="Title"); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.add_run(title)
    p = d.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(subtitle); r.bold = True; r.font.size = Pt(14); r.font.color.rgb = RGBColor.from_string(SKY)
    p = d.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("中再參與｜HurricaneSoft｜2026-07-31").font.size = Pt(9)
    return d

def callout(d, text, color=BLUE):
    t = d.add_table(rows=1, cols=1); t.alignment = WD_TABLE_ALIGNMENT.CENTER
    c = t.cell(0,0); shade(c, PALE); cell_text(c, text, True, color, 11)
    c.paragraphs[0].paragraph_format.space_before = Pt(8); c.paragraphs[0].paragraph_format.space_after = Pt(8)
    d.add_paragraph()

def bullets(d, values):
    for value in values:
        p = d.add_paragraph(style="List Bullet"); p.paragraph_format.space_after = Pt(4); p.add_run(value)

def table(d, headers, rows, widths):
    t = d.add_table(rows=1, cols=len(headers)); t.style = "Table Grid"; t.alignment = WD_TABLE_ALIGNMENT.CENTER; t.autofit = False
    for i, header in enumerate(headers): shade(t.rows[0].cells[i], BLUE); cell_text(t.rows[0].cells[i], header, True, "FFFFFF", 9)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for i, value in enumerate(row):
            if ri % 2: shade(cells[i], LIGHT)
            cell_text(cells[i], value)
    for row in t.rows:
        for i, width in enumerate(widths): row.cells[i].width = Inches(width)
    d.add_paragraph()

def page(d): d.add_section(WD_SECTION.NEW_PAGE)

def proposal():
    d = base("TrustForge Hermes 賽前提案報告", "加密市場多源資訊的信任提煉 AI Agent", "正式繳交版｜提案大綱＋技術架構＋Demo 與連結")
    callout(d, "一句話提案：在生成式 AI 寫出市場分析之前，先把每一條主張做來源評估、交叉佐證、時效檢查與反方保留，讓結論可回到原始證據。")
    d.add_heading("團隊基本資料", 1)
    table(d, ["欄位", "內容"], [("開發環境組別", "Team 11"), ("團隊名稱", "中再參與"), ("團隊／品牌", "HurricaneSoft"), ("隊長", "王英豪"), ("成員", "嵋婕、林子彤、王榆翔（Nicholas）"), ("命題類別", "智慧交易｜HOYA BIT"), ("作品名稱", "TrustForge Hermes（信源熔爐）")], [1.55, 5.25])
    d.add_heading("提案大綱（可直接貼到繳交平台）", 1)
    d.add_paragraph("TrustForge Hermes 是面向加密市場研究的多源資訊信任提煉 AI Agent。系統整合行情、新聞、社群與可回溯資料，先將內容拆成可驗證主張，再由 Trust Layer 計算來源聲譽、獨立佐證、時效與風險訊號，保留支持及反方證據，最後交由 Amazon Bedrock 產生附引用的分析報告。產品不預測價格、不提供投資建議；核心價值是讓研究者知道每個結論『憑什麼相信、何時應改變判斷』。現場可在 15 分鐘內由題目與指定幣種產出 Final Report、Evidence List、Execution Log、Source／Config 四項交付物。")

    page(d); d.add_heading("一、問題與命題連結", 1)
    d.add_paragraph("加密市場資訊高速、碎片化且易受轉載、同溫層與情緒操作影響。一般生成式 AI 能整理文字，卻可能把重複來源當成多方共識，或生成無法查證的引用。HOYA BIT 命題要求多源資訊分析、證據可回溯及完整執行紀錄；TrustForge 將『信任判斷』做成獨立、可稽核的中介層。")
    table(d, ["痛點", "TrustForge 回應", "可驗證輸出"], [("來源多但不等於獨立", "辨識來源與主張，計算獨立佐證", "支持／矛盾證據計數"), ("LLM 引用可能失真", "先建立 Evidence contract，再限制生成", "URL、時間、引用片段、claim_id"), ("結論缺少限制", "保留反方證據與 could_flip 條件", "信心、限制、翻轉條件"), ("Demo 時間短", "固定執行預算與四件式輸出", "Report／Evidence／Log／Config")], [1.55, 2.8, 2.45])
    d.add_heading("使用情境", 2)
    bullets(d, ["交易所研究與客服：快速生成可追溯的市場事件摘要。", "風險與合規：抽查引用是否存在、來源是否一致、結論是否過度延伸。", "一般使用者：在做決策前看見支持證據、反方證據與資訊缺口。"])
    d.add_heading("以官方評分標準設計產品", 2)
    table(d, ["評分項", "權重", "TrustForge 的可驗證特色"], [("主題切合度", "30%", "多源整合、證據回溯、矛盾訊號、雙軸信心與限制說明"), ("商業應用性", "25%", "降低研究與查核成本；可嵌入 HOYA BIT 市場資訊頁"), ("技術可行性", "20%", "Trust Kernel、Bedrock 責任分離、獨立降級與執行紀錄"), ("創意度", "15%", "來源獨立性、矛盾帳本、雙軸信心、負空間情報"), ("完成度", "10%", "15 分鐘內產出 Report、Evidence、Log 與 Source/Config"), ("AWS Kiro", "+10%", "Spec 驅動需求、驗收、設計與實作追溯")], [1.45, .7, 4.65])

    page(d); d.add_heading("二、解決方案與資料流程", 1)
    callout(d, "輸入題目＋指定幣種 → 蒐集資料 → 主張抽取 → TrustScore／交叉佐證 → Bedrock 敘事化 → 四份可稽核交付物")
    table(d, ["層級", "主要工作", "輸出"], [("1. Ingestion", "取得 OHLCV、新聞、社群與文件；記錄來源、取得時間、查詢條件", "標準化 Document"), ("2. Trust Kernel", "拆解 Claim；計算來源聲譽、獨立佐證、時效、風險標記；保存反方", "ScoredClaim／TrustedBrief"), ("3. Agent & Delivery", "Amazon Bedrock 依 TrustedBrief 組織文字，強制以 claim_id 對應證據", "Final Report 等四件")], [1.25, 3.65, 1.9])
    d.add_heading("TrustScore 的定位", 2)
    d.add_paragraph("TrustScore 是『資訊完整度與可溯源性』指標，不是幣價上漲機率。內部預測驗證 AUC 約 0.49，接近隨機，因此本團隊不宣稱預測能力；這項限制反而確立產品聚焦：提升判斷品質，而非製造報酬保證。")
    d.add_heading("Evidence 最小契約", 2)
    bullets(d, ["source：來源名稱或資料提供者。", "fetched_at：取得時間。", "content_reference：引用片段、檔名、查詢或資料範圍。", "related_claim：該證據支持或反駁的主張。", "網頁附 source_url；API／CSV 附 endpoint、參數、交易對、時間範圍或檔名。"])

    page(d); d.add_heading("三、AWS 與生成式 AI 技術架構", 1)
    table(d, ["服務", "用途", "控制措施"], [("Amazon Bedrock", "唯一基礎模型入口；抽取、立場分類與報告敘事", "模型 ID／用量留痕、成本護欄、離線降級"), ("Amazon EC2＋nginx", "React Live Demo 與 Python API", "HTTPS、服務程序管理、健康檢查"), ("Amazon DynamoDB", "執行狀態、冪等及協調資料", "租約與重試控制"), ("Amazon S3", "交付物與版本 manifest", "SHA-256、candidate／active／previous"), ("IAM＋SSM", "最小權限及維運存取", "不在 Repo 儲存長效憑證"), ("CloudWatch", "日誌、健康及錯誤觀測", "run_id 串接執行紀錄")], [1.5, 3.15, 2.15])
    d.add_heading("生成式 AI 的責任邊界", 2)
    bullets(d, ["Bedrock 負責語意抽取、有限立場分類與可讀敘事；不可捏造證據。", "信任分數、證據關聯、限制與輸出契約由 TrustForge pipeline 管理。", "任一模型呼叫失敗時，以可說明的離線結果降級，不以假資料冒充成功。", "競賽執行環境只使用主辦允許的 AWS 服務與基礎模型。"])
    d.add_heading("現況揭露", 2)
    d.add_paragraph("HOYA BIT 特定即時 API 只有在現場取得正式契約並完成連線驗證後才宣稱已串接；Three-track learning 與 AGOS 已有設計／實作痕跡，但未啟用的能力不列為正式 Demo 成果。歷史訓練 JSONL 為 2,005 筆，並非持續線上學習。")

    page(d); d.add_heading("四、Live Demo 與競賽交付", 1)
    table(d, ["時間", "操作", "評審可見證據"], [("0:00–1:00", "輸入題目與指定幣種，建立 run_id", "任務與資料模式"), ("1:00–4:00", "蒐集與標準化多源資料", "來源、時間、查詢條件"), ("4:00–7:00", "主張抽取、TrustScore、支持／矛盾歸類", "Evidence Trail"), ("7:00–10:00", "Bedrock 生成附 claim_id 的報告", "結論、信心、限制、could_flip"), ("10:00–13:00", "檢查四份輸出及引用", "Report／Evidence／Log／Config"), ("13:00–15:00", "打包與上傳、保留緩衝", "檔名、hash、完成狀態")], [1.2, 2.8, 2.8])
    d.add_heading("四份正式交付物", 2)
    table(d, ["交付物", "內容"], [("① Final Report", "結論／市場判斷、關鍵依據、信心、已知限制、可能推翻條件"), ("② Evidence List", "每筆證據含 source、fetched_at、content_reference、related_claim"), ("③ Execution Log", "run_id、階段時間、狀態、模型／成本資訊、錯誤與降級"), ("④ Source／Config", "可重現程式碼、設定與版本；排除所有機密憑證")], [1.6, 5.2])

    page(d); d.add_heading("五、價值、差異化與成功指標", 1)
    callout(d, "別人給 HOYA BIT 一個答案；TrustForge 讓 HOYA BIT 知道，這個答案值不值得相信。")
    table(d, ["一般分析 Agent", "TrustForge"], [("直接生成答案", "先建構證據與主張關係，再生成答案"), ("重複轉載可能被視為共識", "計算來源獨立性與交叉佐證"), ("只顯示信心分數", "同時顯示支持、矛盾、限制與翻轉條件"), ("錯誤難追查", "每個 claim 可回到來源、時間與引用片段"), ("成功只看文字流暢", "成功看可回溯率、完整率、執行成功率與耗時")], [3.05, 3.75])
    d.add_heading("預期效益", 2)
    bullets(d, ["降低研究者逐條查核與整理多源資料的時間。", "讓 HOYA BIT 的 AI 服務從『會回答』升級為『可採信、可抽查、可交代』。", "將 Trust Layer 封裝為可整合 API，延伸至研究、客服、風控與內容治理。"])
    d.add_heading("建議 KPI", 2)
    bullets(d, ["Evidence 必填欄位完整率與來源可回溯率。", "報告 claim 與 evidence 對應率。", "15 分鐘內完成四項輸出的成功率與 P95 時間。", "失敗時的降級成功率、錯誤可診斷率與單次 Bedrock 成本。"])

    page(d); d.add_heading("六、提案繳交資訊", 1)
    table(d, ["主辦要求", "提交內容／狀態"], [("團隊基本資料", "已收錄於本文件首頁"), ("提案大綱", "已收錄於本文件首頁，可直接貼入平台"), ("完整提案簡報", "outputs/TrustForge_決賽6分鐘簡報.pptx（提交前再開檔檢查）"), ("Live Demo 部署網址", "https://trustforge.hurricanesoft.com.tw/"), ("Live Demo 錄製影片", "【提交前填入影片網址】"), ("GitHub（完整原始碼）", "https://github.com/cancleeric/trustforge")], [2.0, 4.8])
    d.add_heading("提交前資安與完整性檢查", 2)
    bullets(d, ["不得提交 AWS Access Key、API Token、資料庫密碼、Workshop Access Code 或任何私密連結。", "機密一律由環境變數、IAM Instance Role 或 SSM 管理；提交前執行 secret scan。", "保留專案根目錄 .kiro 與其 specs、hooks、steering；不得整包加入 .gitignore。", "確認 README 含環境設定、執行範例、benchmark 重現及資料／模型／索引版本。", "確認依賴鎖定檔、架構與資料流程文件、Demo 網址、影片網址及 Repo 權限。"])
    d.add_heading("風險與應變", 2)
    table(d, ["風險", "應變"], [("Bedrock 延遲或權限問題", "先做連線煙霧測試；提供離線降級並在 Log 明示"), ("外部資料源失效", "使用有時間戳的快取／fixture，標示 data mode"), ("15 分鐘逾時", "設定分階段時間預算，13 分鐘開始打包"), ("引用不可回溯", "提交前抽查 URL、引用片段與 claim_id")], [2.15, 4.65])
    path = OUT / "TrustForge_賽前提案報告.docx"; d.save(path); return path

def qa():
    d = base("TrustForge 決賽 4 分鐘備詢手冊", "統問統答｜短答、證據、界線", "決賽口袋版｜每題先結論，再證據")
    callout(d, "答題公式：先用 10 秒講結論，再用 15 秒指出畫面／檔案證據，最後用 5 秒說限制或下一步。不要把未驗證能力講成已上線。")
    d.add_heading("四分鐘節奏", 1)
    table(d, ["時間", "任務", "原則"], [("0:00–0:20", "主答者重述問題並直接回答", "先結論，不重講整份簡報"), ("0:20–3:30", "依序回答所有問題", "每題 25–35 秒；指向實證"), ("3:30–4:00", "補充關鍵限制與價值", "收斂到可回溯、可抽查")], [1.3, 2.35, 3.15])
    d.add_heading("開場備用句", 2)
    d.add_paragraph("謝謝評審。TrustForge 的核心不是預測漲跌，而是在生成內容前建立可稽核的信任層；以下我們會直接回答，並以 Live Demo、Evidence List 或 Execution Log 作證。")

    qas = [
      ("TrustScore 準嗎？", "它衡量資訊完整度與可溯源性，不是漲跌機率。內部預測 AUC 約 0.49，因此我們不把它包裝成投資預測；評審可在 Evidence Trail 查看分項來源。", "Evidence Trail／限制欄"),
      ("和一般 RAG 有何不同？", "一般 RAG 找到文字就交給模型；TrustForge 先拆 Claim、辨識獨立來源、保留支持與矛盾，再限制 Bedrock 只能依 claim_id 敘事。", "claim_id → Evidence"),
      ("如何避免 AI 幻覺？", "我們不只靠提示詞。報告中的重要主張必須對應 Evidence contract；沒有來源就降級或標示資料不足，不以假引用補洞。", "source_url／引用片段"),
      ("如果來源互相矛盾？", "不強行平均或刪除反方。系統保留 contrarian evidence，降低信心，並列出 could_flip，讓使用者知道哪個新證據會改變判斷。", "支持／矛盾計數"),
      ("為什麼一定要 AWS？", "競賽合規且架構可治理：Bedrock 是唯一模型入口；EC2、DynamoDB、S3、IAM／SSM、CloudWatch 分別負責服務、狀態、交付、權限與觀測。", "AWS 架構頁"),
      ("Bedrock 失敗怎麼辦？", "執行會留下錯誤與模型使用狀態，並以明確標示的離線結果降級。系統不會把 fallback 冒充真實 Bedrock 成功。", "Execution Log／ledger"),
      ("15 分鐘怎麼保證完成？", "流程有階段預算：前 10 分鐘完成分析，10–13 分鐘驗證四份輸出，13 分鐘開始打包；外部來源逾時可切換有時間戳的快取。", "階段時間戳"),
      ("資料真的來自 HOYA BIT 嗎？", "只有在取得正式 API 契約並成功連線後才會這樣宣稱；否則畫面明確顯示資料模式與來源，不以 stub 冒充企業資料。", "data mode／source"),
      ("你們的創新是什麼？", "不是多一個聊天框，而是把『信任』做成可計算、可追溯、可反駁的中介層，讓每一段生成內容都有證據路徑。", "Trust Layer 流程"),
      ("商業價值在哪裡？", "HOYA BIT 可把它整合到研究、客服與風控：縮短人工查核時間，也降低不可解釋內容帶來的品牌與合規風險。", "KPI／整合路線"),
      ("系統會自我學習嗎？", "歷史 JSONL 有 2,005 筆；Three-track 與 AGOS 尚未啟用的部分不列為正式成果。所有升級需經測試、審查與版本控制，不允許自行上線。", "版本／審查紀錄"),
      ("目前最大的限制？", "預測能力不是產品主張、即時外部 API 受現場契約與網路影響。優先確保四份交付可回溯、Demo 可降級、未驗證能力不誇大。", "limitations／could_flip"),
    ]
    page(d); d.add_heading("高機率題庫", 1)
    for idx, (q, a, proof) in enumerate(qas, 1):
        d.add_heading(f"{idx}. {q}", 2)
        p = d.add_paragraph(); r = p.add_run("回答｜"); r.bold = True; r.font.color.rgb = RGBColor.from_string(BLUE); p.add_run(a)
        p = d.add_paragraph(); r = p.add_run("指證｜"); r.bold = True; r.font.color.rgb = RGBColor.from_string(MINT); p.add_run(proof)
        if idx in (4, 8): page(d)

    page(d); d.add_heading("備詢作戰表", 1)
    table(d, ["情境", "處理方式"], [("不知道確切答案", "明說目前未觀測／未驗證，再說可如何用 Log 或測試確認"), ("評審質疑準確率", "重申 TrustScore 非預測分數；展示可回溯與限制"), ("Demo 當下失敗", "打開 Execution Log，說明失敗節點並展示降級輸出"), ("追問資料真實性", "直接開 Evidence List，抽查 URL、時間與引用片段"), ("追問資安", "說明無憑證入庫、IAM／SSM、secret scan 與 .kiro 保留政策")], [2.0, 4.8])
    d.add_heading("禁止誇大的說法", 1)
    table(d, ["不要說", "改成"], [("TrustScore 能預測漲跌", "TrustScore 衡量完整度、可溯源性與佐證品質"), ("每次都使用 Bedrock", "該次是否使用由 run ledger 證明"), ("HOYA BIT API 已串好", "僅在正式契約與連線驗證完成後宣稱"), ("系統持續自我進化", "升級候選需測試、審查、核准才可啟用"), ("所有資料都是真實即時", "畫面明示 live／cache／fixture data mode")], [2.7, 4.1])
    d.add_heading("最後 20 秒收尾", 1)
    callout(d, "TrustForge 不替使用者做投資決定；它讓使用者看得見每個結論的證據、矛盾與限制。對 HOYA BIT 而言，這是把生成式 AI 從『會回答』推進到『值得採信、可以稽核』。", MINT)
    path = OUT / "TrustForge_決賽4分鐘備詢.docx"; d.save(path); return path

if __name__ == "__main__":
    print(proposal())
    print(qa())
