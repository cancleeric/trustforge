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

def wrap_terms(text: str) -> str:
    placeholders = {}
    for i, (term, tip) in enumerate(sorted(TERMS.items(), key=lambda item: -len(item[0]))):
        token = f'__DENSE_GLOSSARY_{i}__'
        text = text.replace(term, token)
        placeholders[token] = f'<span class="term" data-tip="{tip}">{term}</span>'
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
