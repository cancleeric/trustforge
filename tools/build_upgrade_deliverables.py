from pathlib import Path
import argparse
import html
import re
import shutil
import subprocess
from docx import Document
from docx.shared import Pt
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT=Path(__file__).resolve().parents[1]
SLIDE=ROOT/'docs/competition/slide-deck'
OUT=ROOT/'outputs'

CHROMIUM_VERSION = "151.0.7922.71"

TERMS={
 'Trust Kernel':'可重現的信任計算核心：依來源、時效、交叉佐證與異常訊號計算信任與完整度。',
 'Evidence List':'證據清單：列出結論對應的來源、Claim、網址與時間戳。',
 'Execution Log':'執行紀錄：記錄工具、步驟、時間、成本、錯誤與結果。',
 'ModelHub':'模型治理中心：管理版本、指標、artifact、候選狀態與人工批准。',
 'SageMaker':'AWS 機器學習執行平台：建立訓練任務並產生模型 artifact。',
 'Calibration':'信心校準：把原始 confidence 調整成較可靠的機率。',
 'Claim':'可驗證的具體主張。',
 'Evidence contract':'證據契約：規定每筆資料必須帶來源、時間與可回查資訊。',
 'run_id':'每次分析的唯一識別碼，用來串起報告、證據與執行紀錄。',
 'PoC':'Proof of Concept，概念驗證階段。',
 'Pilot':'試點導入階段。',
 'Trust Layer':'位於資料與生成模型之間的信任治理層。',
}

TERM_PATTERN = re.compile(
    "|".join(re.escape(term) for term in sorted(TERMS, key=len, reverse=True))
)

TOOLTIP_CSS='<style>.term{border-bottom:1px dotted #2e74b5;cursor:help;position:relative}.term:hover:after{content:attr(data-tip);position:absolute;z-index:50;left:0;top:1.6em;width:280px;padding:9px 11px;border-radius:8px;background:#10294a;color:#fff;font-size:13px;line-height:1.45;box-shadow:0 8px 24px rgba(0,0,0,.25);white-space:normal;text-align:left}.glossary-note{background:#eef8fb;border-left:4px solid #2e74b5;padding:10px 14px;margin:12px 0}</style>'

def tooltip_html(text):
    # One substitution pass is essential: sequential ``str.replace`` calls can
    # find a shorter term inside markup inserted for an earlier term and corrupt
    # its ``data-tip`` attribute with nested ``<span>`` HTML.
    return TERM_PATTERN.sub(
        lambda match: (
            '<span class="term" data-tip="'
            + html.escape(TERMS[match.group(0)], quote=True)
            + '">'
            + match.group(0)
            + "</span>"
        ),
        text,
    )

def copy_html(src,dst,inject=''):
    text=src.read_text(encoding='utf-8')
    if '</head>' in text: text=text.replace('</head>',TOOLTIP_CSS+'</head>',1)
    if inject: text=text.replace('<body>', '<body>'+inject,1)
    # Keep markup intact and wrap visible text nodes only (never attributes/tags).
    def visible(match):
        chunk=match.group(1)
        if not chunk.strip() or 'data-tip=' in chunk: return match.group(0)
        return '>'+tooltip_html(chunk)+'<'
    text=re.sub(r'>([^<>]+)<',visible,text)
    dst.write_text(text,encoding='utf-8')

def upgrade_deck():
    src=SLIDE/'TrustForge_正式提案簡報_6分鐘.html'; dst=SLIDE/'TrustForge_正式提案簡報_6分鐘_升級版.html'
    text=src.read_text(encoding='utf-8')
    css='<style>.commercial-mini{display:grid;grid-template-columns:repeat(3,1fr);gap:1vw;margin-top:1.8vh}.commercial-mini div{padding:1vw;border:1px solid rgba(65,217,232,.55);border-radius:12px;background:rgba(65,217,232,.09)}.commercial-mini b{display:block;color:#41d9e8;font-size:clamp(12px,1vw,16px);margin-bottom:5px}.commercial-mini span{color:#b7c9dc;font-size:clamp(11px,.85vw,14px);line-height:1.35}</style>'
    text=text.replace('</head>',css+'</head>',1)
    marker='<p class="quote"'
    insert='<div class="commercial-mini"><div><b>首個買方</b><span>交易所研究、風控與合規團隊</span></div><div><b>4 週 PoC</b><span>5 幣＋4 類外部來源＋Evidence 抽查</span></div><div><b>成功 KPI</b><span>查核時間、可溯源率、報告完成時間</span></div></div>'
    text=text.replace(marker,insert+marker,1)
    dst.write_text(text,encoding='utf-8')

def upgrade_script():
    src=SLIDE/'TrustForge_正式提案講稿_6分鐘.html'; dst=SLIDE/'TrustForge_正式提案講稿_6分鐘_升級版.html'
    inject='<div class="glossary-note"><b>講者使用方式：</b>藍色虛線底線的專有名詞，滑鼠移上去會顯示一句解釋；正式上台時只需先講中文，評審追問再補英文。</div>'
    copy_html(src,dst,inject)

def upgrade_qa():
    src=SLIDE/'TrustForge_正式提案_4分鐘備詢.html'; dst=SLIDE/'TrustForge_正式提案_4分鐘備詢_升級版.html'
    inject='<section class="qa key"><h2>現場 Top 6 速答</h2><p><b>自行訓練：</b>我們訓練的是 task-specific confidence calibrator，不是另訓大型語言模型。</p><p><b>為何不用現成模型：</b>Bedrock 負責語言理解；信任分數與校準需要可重現、可稽核的 deterministic pipeline。</p><p><b>ModelHub／SageMaker：</b>ModelHub 管治理，SageMaker 執行 AWS 訓練。</p><p><b>AWS 限制：</b>生成式 foundation model 只走 Bedrock；校準器是任務模型，正式路徑可走 SageMaker。</p><p><b>來源驗證：</b>台灣監管 connector 已建立，但各來源 coverage 依 live、cache、fixture 狀態揭露；BlockTempo 仍是 planned。</p><p><b>15 分鐘失敗：</b>以 budget、cache、平行來源與 fail-closed 控制；資料不足就 abstain，不硬湊答案。</p></section>'
    copy_html(src,dst,inject)

def upgrade_report():
    src=OUT/'TrustForge_完整商業化提案報告.docx'; dst=OUT/'TrustForge_完整商業化提案報告_升級版.docx'
    doc=Document(src)
    target=next((p for p in doc.paragraphs if p.text.strip()=='摘要與產品定位'),doc.paragraphs[0])
    entries=[
      ('Heading 1','Executive Summary｜商業化摘要'),
      ('Normal','目標客戶：交易所、券商研究部、虛擬資產資訊平台，以及需要可稽核市場資料的風控與合規團隊。第一個產品切入點是 HOYA BIT 市場資訊頁的 Trust Layer 外掛。'),
      ('Normal','導入方案：第一階段以 4 週 PoC 接入 5 幣與 4 類外部來源，交付 Evidence Trail 與人工抽查報告；第二階段以 8–12 週 Pilot 接入研究流程與角色權限；第三階段再進入 3–6 個月 Production，導入 SLA、audit、budget、tenant 與 dashboard。'),
      ('Normal','KPI：查核時間下降、Evidence 可溯源率、報告完成時間、人工改稿率、失敗後恢復時間與每次 run 成本。收費採 API usage、seat 與 enterprise integration 的組合，實際價格於 PoC 後依資料量與部署模式報價。'),
      ('Normal','商業風險邊界：不提供投資建議、不承諾價格預測；live、cache、fixture 明確標示；第三方 API 依授權使用；Evidence、Log 與 artifact 不輸出秘密。'),
      ('Heading 2','競品與替代方案'),
      ('Normal','一般 RAG 找得到資料，但通常不先評估來源可信度；一般 Crypto AI 能給答案，但難以追溯；BI Dashboard 有數字卻沒有推理鏈；人工研究可信但慢且難重現。TrustForge 的差異是先建立 Claim → Evidence → Trust → Report 的可治理鏈。'),
    ]
    for style,text in reversed(entries):
        p=target.insert_paragraph_before(text); p.style=style
    # metadata
    props=doc.core_properties; props.title='TrustForge Hermes｜完整商業化提案報告（升級版）'; props.author='HurricaneSoft'; props.company='HurricaneSoft'; props.subject='Evidence-native Decision Intelligence Agent 商業化提案'
    doc.save(dst)
    src_html=OUT/'TrustForge_完整商業化提案報告.html'; dst_html=OUT/'TrustForge_完整商業化提案報告_升級版.html'
    summary='<section class="commercial-summary"><h1>Executive Summary｜商業化摘要</h1><p><b>目標客戶：</b>交易所、券商研究部、虛擬資產資訊平台、風控與合規團隊。第一個切入點是 HOYA BIT 市場資訊頁的 Trust Layer 外掛。</p><p><b>導入方案：</b>4 週 PoC（5 幣＋4 類外部來源＋Evidence 抽查）→ 8–12 週 Pilot（研究流程與角色權限）→ 3–6 個月 Production（SLA、audit、budget、tenant、dashboard）。</p><p><b>KPI／收費：</b>查核時間、可溯源率、報告完成時間、人工改稿率、恢復時間與每次 run 成本；收費採 API usage、seat、enterprise integration 組合。</p><p><b>風險邊界：</b>不提供投資建議、不承諾價格預測；live/cache/fixture 明示；第三方 API 依授權使用；Evidence、Log、artifact 不輸出秘密。</p><h2>競品與替代方案</h2><p>一般 RAG 找得到資料但不先評估可信度；一般 Crypto AI 難稽核；BI Dashboard 沒有推理鏈；人工研究慢且難重現。TrustForge 建立 Claim → Evidence → Trust → Report 的可治理鏈。</p></section>'
    copy_html(src_html,dst_html,summary)

def build_pdfs():
    """Rebuild every committed upgraded PDF with a pinned Chromium version."""
    chromium = shutil.which("chromium")
    if chromium is None:
        raise RuntimeError(
            f"Chromium {CHROMIUM_VERSION} is required to rebuild upgraded PDFs"
        )
    version = subprocess.check_output(
        [chromium, "--version"], text=True, encoding="utf-8"
    ).strip()
    if CHROMIUM_VERSION not in version:
        raise RuntimeError(
            f"expected Chromium {CHROMIUM_VERSION}, found {version!r}"
        )

    html_outputs = (
        SLIDE/'TrustForge_正式提案簡報_6分鐘_升級版.html',
        SLIDE/'TrustForge_正式提案講稿_6分鐘_升級版.html',
        SLIDE/'TrustForge_正式提案_4分鐘備詢_升級版.html',
        OUT/'TrustForge_完整商業化提案報告_升級版.html',
    )
    for source in html_outputs:
        destination = source.with_suffix(".pdf")
        subprocess.run(
            [
                chromium,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-pdf-header-footer",
                f"--print-to-pdf={destination.resolve()}",
                source.resolve().as_uri(),
            ],
            check=True,
        )

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf",
        action="store_true",
        help=f"also rebuild PDFs with Chromium {CHROMIUM_VERSION}",
    )
    args = parser.parse_args()
    upgrade_deck(); upgrade_script(); upgrade_qa(); upgrade_report()
    if args.pdf:
        build_pdfs()
    print('upgrade deliverables generated')
