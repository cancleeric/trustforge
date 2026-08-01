from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/competition/slide-deck/TrustForge_正式提案講稿_6分鐘.html'
TARGET = ROOT / 'docs/competition/slide-deck/TrustForge_正式提案講稿_6分鐘_升級版.html'

TERMS = {
    'AI': 'Artificial Intelligence（人工智慧）：以資料與演算法執行理解、分類、推理或生成工作的軟體能力。',
    'Agent': 'AI Agent（人工智慧代理人）：能依受控流程自主蒐集資料、呼叫工具、分析、產出報告並留下紀錄的軟體。',
    'Runtime': '執行時環境：程式實際運作的地方；負責接收任務、呼叫 API、執行 Trust Kernel、保存紀錄並回傳結果。',
    'Bedrock': 'Amazon Bedrock：AWS 的生成式 AI 服務平台；在 TrustForge 中負責語言理解與文字生成，不直接決定 Trust Score。',
    'Trust Kernel': '信任計算核心：以來源品質、時效、交叉佐證、重複與操縱風險等可重現規則計算信任狀態。',
    'Trust Score': '信任分數：證據可靠程度的量化結果，不是投資報酬率、價格勝率或投資建議。',
    'Claim': '可驗證主張：從來源內容抽出的具體說法，可以被其他來源支持、反駁或回源查證。',
    'Claims': '可驗證主張集合：從來源內容抽出的多個具體說法，每一項都能追溯到原文。',
    'Claim Extraction': '主張抽取：從新聞、公告或社群內容找出可驗證的具體說法，先結構化再進入信任計算。',
    'Bullish': '看多／偏樂觀：資料語意傾向價格或市場可能上升，但不是系統的價格預測。',
    'Bearish': '看空／偏悲觀：資料語意傾向價格或市場可能下跌，但不是系統的價格預測。',
    'Neutral': '中立：來源沒有明確支持上漲或下跌方向。',
    'Stance': '語意立場：把 Claim 分成 bullish、bearish 或 neutral，用來描述來源觀點而非做交易決策。',
    'Deduplication': '去重：合併同一事件的轉載或重複訊息，避免把一篇新聞誤算成多個獨立來源。',
    'Corroboration': '交叉佐證：確認同一 Claim 是否被不同且獨立的來源支持。',
    'Uncertainty': '不確定性：揭露資料不足、來源衝突、時效限制與可能改變結論的因素。',
    'Evidence': '證據：能回到原始來源、帶有時間與內容參照，並用來支持或反駁 Claim 的資料。',
    'Evidence-native': '證據原生：系統從設計起就要求結論與來源、時間、Claim 及溯源鏈一起產生。',
    'Decision Intelligence': '決策智能：把資料、證據、風險、信心與限制整理成可供人員判斷的資訊，不代替投資決策。',
    'Decision Record': '決策紀錄：把結論、依據、限制、版本、時間與執行步驟一起保存的可追溯產物。',
    'audit trail': '稽核軌跡：記錄資料、步驟、模型版本、分數、批准與錯誤，使結果可以重播、查證與追責。',
    'Ingestion': '資料匯入：抓取外部來源、檢查格式與時間、標準化後送入分析管線。',
    'Reasoning Pipeline': '推理管線：依序完成 Claim、立場、去重、交叉佐證、Trust Kernel 與 uncertainty 的處理流程。',
    'Tool Registry': '工具註冊表：Agent 可使用的工具清單，包含用途、輸入輸出、權限、版本與安全限制。',
    'Policy plane': '政策層：記錄來源、分析、報告、評估與改善各階段允許的行為與消費者。',
    'Memory plane': '記憶層：保存 run、artifact、evidence、learning event 與 replay history，供追蹤和重播。',
    'learning event': '學習事件：一次分析留下的完整可追蹤紀錄；先沉澱資料，等 outcome 成熟後才評估訓練。',
    'run_id': '執行識別碼：每次分析的唯一 ID，用來串起報告、證據、log、模型版本與 outcome。',
    'Execution Log': '執行紀錄：記錄實際使用的工具、步驟、時間、耗時、錯誤、token 與成本。',
    'Evidence List': '證據清單：列出結論對應的來源、URL、抓取時間、內容參照與 Claim。',
    'Final Report': '最終報告：整理決策狀態、證據、信心、不確定性、限制與非投資建議聲明。',
    'Snapshot': '分析快照：保存當下的輸入資料、模型、規則與結果，之後可以一致重現。',
    'API Response': 'API 回應：以 JSON 等結構化格式把結果提供給前端或其他企業系統。',
    'training backend': '訓練後端：真正讀取資料、送出 Training Job、產生模型 artifact 的執行模組。',
    'ModelHub': '模型治理中心：管理模型版本、指標、artifact provenance、候選狀態、審核與啟用生命週期。',
    'SageMaker': 'Amazon SageMaker：AWS 的機器學習訓練平台；可從 S3 讀取資料、執行訓練並產生 artifact。',
    'S3': 'Amazon S3：AWS 物件儲存服務，用來保存訓練資料、模型 artifact 與完整性 manifest。',
    'Isotonic Regression': '等序迴歸校準器：把原始 confidence 調整成較接近實際命中率的校準信心。',
    'candidate': '候選版本：已訓練或提議、但尚未通過人工審核的模型、規則或升級版本。',
    'T+1': 'T+1 outcome：分析後第 1 天觀察到的市場結果，用來做事後校準。',
    'T+7': 'T+7 outcome：分析後第 7 天觀察到的市場結果，用來檢查中期穩定性。',
    'T+14': 'T+14 outcome：分析後第 14 天觀察到的市場結果，用來檢查較長時間的校準效果。',
    'ground truth': '事後真值：分析完成後實際觀察到、可用來驗證當時信心是否合理的結果資料。',
    'Training Status': '訓練狀態：顯示資料批次、訓練任務、指標、候選版本與人工審核進度。',
    'Upgrade Queue': '升級候選佇列：集中管理待測試、待審核、可啟用或需回滾的模型與規則升級。',
    'rollback': '回滾：新版本出問題時回到上一個已驗證的穩定版本，並留下 decision record。',
    'EC2': 'Amazon EC2：AWS 虛擬伺服器；目前 TrustForge 的後端與 Web 服務部署在此。',
    'nginx': 'Web Server／Reverse Proxy：接收 HTTPS、提供前端頁面，並將 API 請求轉交 Python backend。',
    'App Runner': 'AWS App Runner：另一種託管部署服務；目前不是 TrustForge 的正式生產入口。',
    'IAM': 'AWS Identity and Access Management：以最小權限控制服務、使用者與資源存取。',
    'CloudWatch': 'AWS 監控服務：收集 log、指標與告警，觀測模組健康、耗時與成本。',
    'Budget': '成本預算護欄：限制 Bedrock token、Agent 執行、訓練與 AWS 資源支出，避免用量失控。',
    'Carbon': '算力與推理用量遙測：記錄 token、呼叫次數與可估算的運算消耗，不宣稱已完成正式碳盤查。',
    'Module Telemetry': '模組遙測：記錄每個 connector、Trust Kernel 或模型呼叫的執行時間、成功率、錯誤與用量。',
    'Fail-closed': '安全失敗：遇到資料、權限或模型異常時停止或拒絕下結論，不硬湊答案。',
    'degraded': '降級模式：資料源或服務不可用時，以較少功能或較保守方式繼續並揭露狀態。',
    'abstain': '棄權：證據不足或衝突過高時明確表示目前無法判斷，而不是猜測。',
    'API': 'Application Programming Interface：讓前端或其他系統以結構化方式呼叫 TrustForge 功能。',
    'Live Demo': '現場可執行展示：使用指定題目與幣種，在限制時間內完成分析並交付四份產物。',
    'PoC': 'Proof of Concept（概念驗證）：用限定範圍與時間驗證產品能否解決真實問題。',
    'KPI': 'Key Performance Indicator：用來衡量導入成效的指標，例如查核時間、可溯源率與人工改稿率。',
}

TERMS.update({
    'Product thesis': '產品主張：用一句話說明產品解決什麼高價值問題，以及為何值得企業採用。',
    'Product surface': '產品表面：使用者實際接觸的 workspace、console、API 與交付產物集合。',
    'Runtime architecture': '執行架構：描述 Agent 如何接收任務、呼叫資料與工具、執行規則並交付結果。',
    'architecture': '架構：系統元件、資料流、權限與部署邊界的整體設計。',
    'research': '研究：從資料蒐集、比較、證據查核到報告產出的分析工作。',
    'operations': '營運：監控訓練、預算、模組健康、升級與事件處理的日常工作。',
    'workflow': '工作流程：企業原本執行任務的步驟；TrustForge 將報告、證據與紀錄接入其中。',
    'bounded': '有界：Agent 只能在固定工具、來源、權限、預算與時間範圍內行動，不允許無限探索。',
    'observable': '可觀測：每次執行的狀態、耗時、錯誤、成本與產物都能被監控與查詢。',
    'reversible': '可回復：模型、規則或部署異常時，可以依紀錄安全回到上一個穩定版本。',
    'decision state': '決策狀態：包含方向、Trust Score、calibrated confidence、reason codes、abstain 與 could_flip 的結構化結果。',
    'Evidence contract': '證據契約：規定每筆資料必須帶 source、fetched_at、content_reference、related_claim 與可回源資訊。',
    'fetched_at': '抓取時間欄位：記錄系統取得或驗證資料的時間，避免把過時資訊當成即時資訊。',
    'content_reference': '內容參照：指出原始文件中支持 Claim 的段落、欄位、行號或引用片段。',
    'related_claim': '關聯主張：把一筆證據連回它支持、反駁或影響的 Claim。',
    'URL': 'Uniform Resource Locator：可直接回到網頁或 API 資源的地址。',
    'endpoint': '端點：API 提供特定功能的請求地址與介面契約。',
    'query': '查詢：送給資料來源的條件、參數或搜尋語句。',
    'deterministic logic': '確定性邏輯：在相同輸入、規則與版本下得到相同結果，可測試與重播。',
    'source reputation': '來源信譽：依來源類型、歷史可靠度與驗證狀態給出的基礎權重。',
    'freshness half-life': '新鮮度半衰期：描述證據隨時間經過而降低權重的衰減參數。',
    'manipulation risk': '操縱風險：衡量集中轉帳、異常訊號或不自然傳播可能影響判斷的風險。',
    'direction': '方向：目前證據支持的市場語意方向，例如 bullish、bearish 或 neutral。',
    'reason codes': '原因代碼：把分數形成原因以結構化代碼保存，方便 UI、API 與稽核使用。',
    'independent source count': '獨立來源數：扣除轉載與同源內容後，真正相互獨立的佐證來源數量。',
    'canonical source identity': '規範化來源身份：將同一出版者、網域或資料提供者統一識別，避免重複計票。',
    'claim grouping': '主張分組：把描述同一事件的不同文字歸到同一組 Claim。',
    'union-find': 'Union-Find：用來合併相同事件群組的資料結構，能有效處理來源去重與連通關係。',
    'contrarian evidence': '反方證據：與目前主結論方向相反、可能推翻或降低信心的資料。',
    'Conflict Ledger': '衝突帳本：保存來源之間的矛盾、分歧與解決狀態，不用平均值掩蓋風險。',
    'could_flip': '可能翻轉條件：明確列出什麼新證據出現時，現在的決策狀態可能改變。',
    'stance pair': '立場對：把支持與反對同一 Claim 的 evidence 成對保存，呈現完整分歧。',
    'Asset Intrinsic': '資產內在面：資產本身的供應、用途、治理或協議基本資料角度。',
    'Peer Metrics': '同類資產指標：與同類資產比較的市值、流動性、波動與其他可量化指標。',
    'multi-angle': '多角度分析：以基本面、風險、情緒、新聞、鏈上與市場資料等角度交叉檢查。',
    'Whale history': '巨鯨歷史：大型錢包或大額資金活動的時間序列紀錄，用於風險脈絡分析。',
    'report': '報告：把結論、證據、信心、限制與反方資訊整理成可讀的交付物。',
    'response': '回應：API 或 Agent 對請求產生的結構化結果與狀態資訊。',
    'source': '來源：提供原始資料的網站、API、檔案或企業系統。',
    'analysis': '分析：將資料經過驗證、推理、評分與交付的完整執行流程。',
    'evaluation': '評估：用固定資料、指標與 holdout 檢查模型或規則是否改善。',
    'improvement': '改善：根據評估與 outcome 提出的模型、規則、來源或流程優化。',
    'policy': '政策：定義系統哪些行為被允許、拒絕或需要人工批准的規則。',
    'token': 'Token：模型處理文字時使用的基本單位；input/output token 可用於成本與用量統計。',
    'label leakage': '標籤洩漏：訓練資料不小心使用了分析當下不可能知道的未來結果，導致評估失真。',
})

def wrap_terms(text: str) -> str:
    placeholders = {}
    for i, (term, tip) in enumerate(sorted(TERMS.items(), key=lambda item: -len(item[0]))):
        token = f'\ue000{i}\ue001'
        text = text.replace(term, token)
        placeholders[token] = f'<span class="term" data-tip="{tip}">{term}</span>'

    # The script is intended for live speaking practice: every remaining
    # English token also gets a hover explanation, including lower-case words
    # such as "runtime" and "agent" that are not in the curated glossary.
    def generic(match):
        word = match.group(0)
        token = f'\ue010{len(placeholders)}\ue011'
        placeholders[token] = (
            f'<span class="term" data-tip="英文單字「{word}」：在本段 TrustForge 講稿中，'
            f'它描述產品、資料、流程或軟體工程概念；請依前後文理解其具體角色。">{word}</span>'
        )
        return token

    text = re.sub(r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9_./+:-]*(?![A-Za-z0-9])', generic, text)
    for token, html in placeholders.items():
        text = text.replace(token, html)
    return text

html = SOURCE.read_text(encoding='utf-8')
tooltip_css = '<style>.term{border-bottom:1px dotted #2e74b5;cursor:help;position:relative}.term:hover:after{content:attr(data-tip);position:absolute;z-index:50;left:0;top:1.6em;width:300px;padding:10px 12px;border-radius:8px;background:#10294a;color:#fff;font-size:13px;line-height:1.5;box-shadow:0 8px 24px rgba(0,0,0,.25);white-space:normal;text-align:left}.glossary-note{background:#eef8fb;border-left:4px solid #2e74b5;padding:10px 14px;margin:12px 0}</style>'
html = html.replace('</head>', tooltip_css + '</head>', 1)
html = html.replace('<body>', '<body><div class="glossary-note"><b>講者使用方式：</b>藍色虛線底線的英文術語，滑鼠移上去會顯示詳細解釋；上台時先講中文，評審追問再補英文。</div>', 1)
body_start = html.find('<body>')
head = html[:body_start]
body = html[body_start:] if body_start >= 0 else html

def visible(match):
    chunk = match.group(1)
    if not chunk.strip() or 'DENSE_GLOSSARY' in chunk:
        return match.group(0)
    return '>' + wrap_terms(chunk) + '<'

body = re.sub(r'>([^<>]+)<', visible, body)
TARGET.write_text(head + body, encoding='utf-8')
print(f'enriched {TARGET}')
