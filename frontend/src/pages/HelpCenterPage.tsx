// 說明中心（/help，R2 深色艦橋設計稿新頁）：彙整名詞解釋、5 階段管線原理、
// 信任分數色帶與常見問題。內容取自設計稿逐字稿（見 docs/design/hermes-r2-darkbridge/
// HERMES Help Center.dc.html），純靜態頁、無需後端資料，故不比照其他頁面
// 走 API loading/error 狀態機。

type GlossaryRow = { term: string; en: string; desc: string; where: string }
type Stage = { num: string; label: string; desc: string; highlight?: boolean }
type Band = { range: string; label: string; color: string; bg: string; border: string; desc: string }
type Faq = { q: string; a: string }

const GLOSSARY: GlossaryRow[] = [
  { term: '信任分數', en: 'Trust Score', desc: '0–100 的綜合評分，衡量「這個幣的市場資料有多可信」，不是價格預測或投資建議。分數越高，資料越一致、越難被操縱。', where: '分析結果 · 執行台 · 歷史趨勢' },
  { term: 'multi_source 模式', en: 'Multi-source', desc: '同時比對多個交易所與資料源（報價、成交量、情緒），交叉驗證後才給分，比單一來源更難被造假。', where: '分析結果 · 執行台 · 成本' },
  { term: '交叉佐證', en: 'Cross-corroboration', desc: '用多個獨立來源互相印證同一項數據。越多來源一致，該數據越可信。', where: '分析結果 · 拆解抽屜' },
  { term: '跨來源分歧', en: 'Cross-source divergence', desc: '同一項數據在不同來源之間的差異。分歧越大，代表資料越不可靠或可能有人為操縱。', where: '分析結果 · 比較 · 拆解抽屜' },
  { term: '抗操縱能力', en: 'Manipulation resistance', desc: '評估資料被人為操縱（假量、掛單誘導）影響的難易度。分數越高越不容易被操縱。', where: '分析結果 · 比較 · 拆解抽屜' },
  { term: 'wash-trading 疑慮', en: 'Wash trading', desc: '自買自賣（對敲）製造假成交量的嫌疑，會讓成交量看起來比實際熱絡。', where: '分析結果 · 執行台 · 通知' },
  { term: 'spoofing 訊號', en: 'Spoofing', desc: '掛出大量假委託、再快速撤單，誘導市場方向的操縱手法訊號。', where: '分析結果 · 設定' },
  { term: '資料充足度', en: 'Sufficiency', desc: '衡量當下可用的可信來源夠不夠多、夠不夠新，足以支撐一個可靠的信任分數。', where: '歷史趨勢' },
  { term: '完整度區間', en: 'Completeness band', desc: '資訊完整度的合理波動範圍。區間越窄代表估計越穩定；越寬代表資料較不足、需保守看待。', where: '歷史趨勢' },
  { term: '新鮮度', en: 'Freshness', desc: '資料的即時程度。FRESH＝最新、STALE＝逾期待更新、MISSING＝來源缺席未回應。', where: '來源狀態' },
  { term: '連接器', en: 'Connectors', desc: '串接各交易所與資料源的介接元件，負責持續抓取報價、成交量與情緒資料。', where: '來源狀態 · 管理' },
  { term: 'LLM Runtime · Bedrock', en: 'LLM runtime', desc: '驅動 HERMES 推理與生成報告的大型語言模型執行環境（AWS Bedrock）。', where: '來源狀態' },
  { term: 'TOKEN 用量', en: 'Token usage', desc: 'LLM 處理文字的計費單位。一次 run 消耗的 token 越多，成本越高。', where: '成本帳本' },
  { term: '不可竄改', en: 'Immutable', desc: '快照一旦寫入即無法修改，用於事後稽核與追溯 HERMES 當時看到的原始資料。', where: '快照 Modal' },
  { term: '血統', en: 'Lineage', desc: '資料的來源與處理歷程鏈，可追溯每個數字來自哪個來源、經過哪些步驟得出。', where: '快照 Modal' },
  { term: '權重', en: 'Weight', desc: '該來源在計分時的重要程度。信譽高、越新鮮的來源權重越高，對最終信任分影響越大。', where: '拆解抽屜 · 快照 Modal' },
]

const STAGES: Stage[] = [
  { num: '01', label: '來源掃描', desc: '從 150+ 個交易所、資料源與情緒來源持續擷取原始快照。' },
  { num: '02', label: '可信過濾', desc: '剔除過期、低可信或明顯異常的資料，只留下可用來源。' },
  { num: '03', label: '交叉驗證', desc: '讓多個獨立來源互相印證同一項數據，量化彼此的分歧程度。' },
  { num: '04', label: '操縱偵測', desc: '掃描 wash-trading、spoofing 等人為操縱訊號並標記。' },
  { num: '05', label: 'HERMES ENGINE', desc: '綜合以上結果，由 LLM 推理生成 0–100 信任分數與白話報告。', highlight: true },
]

const BANDS: Band[] = [
  { range: '0–39', label: '低信任', color: 'var(--color-tf-bad)', bg: 'color-mix(in srgb, var(--color-tf-bad) 8%, transparent)', border: 'color-mix(in srgb, var(--color-tf-bad) 40%, transparent)', desc: '資料分歧大或偵測到操縱訊號，強烈建議自行交叉確認再做任何判斷。' },
  { range: '40–69', label: '中度信任', color: 'var(--color-tf-warn)', bg: 'color-mix(in srgb, var(--color-tf-warn) 8%, transparent)', border: 'color-mix(in srgb, var(--color-tf-warn) 40%, transparent)', desc: '多數來源一致，但仍有部分分歧或時效問題，可參考但保留餘地。' },
  { range: '70–100', label: '高信任', color: 'var(--color-tf-green)', bg: 'color-mix(in srgb, var(--color-tf-green) 8%, transparent)', border: 'color-mix(in srgb, var(--color-tf-green) 40%, transparent)', desc: '來源充足、彼此高度一致且無明顯操縱訊號，資料可信度高。' },
]

const FAQS: Faq[] = [
  { q: '信任分數低，代表這個幣有問題嗎？', a: '不一定。信任分數衡量的是「市場資料的可信度」，不是幣本身好壞。分數低通常代表來源分歧大、資料過期，或偵測到操縱訊號——意思是「現在的資料不夠可靠，別急著下判斷」，而不是「這個幣是騙局」。' },
  { q: 'HERMES 會給我投資建議或買賣訊號嗎？', a: '不會。HERMES 只評估資料可信度，不預測價格、不提供買賣建議。它幫你判斷「手上的市場資料能不能信」，最終決策仍由你自己負責。' },
  { q: '信任分數會隨時間改變嗎？', a: '會。市場資料持續變動，連續引擎會不斷重新掃描來源。你可以在「歷史趨勢」看到每日定格快照，觀察信任分數與資料充足度的演變。' },
  { q: 'multi_source 和 single_source 有什麼差別？', a: 'multi_source 同時比對多個獨立來源並交叉驗證，較難被單一來源造假，信任評估更穩健但成本較高；single_source 只看單一來源，快速但抗操縱能力較弱。' },
  { q: '為什麼同一個幣，不同來源的數字會不一樣？', a: '各交易所流動性、掛單與撮合機制不同，成交量與報價本來就會有差異。當差異超過合理範圍（跨來源分歧過高），HERMES 會視為警訊並下修信任分數。' },
  { q: '快照為什麼「不可竄改」？有什麼用？', a: '每次分析都會把當下看到的原始資料存成不可修改的快照，並保留完整血統鏈。這讓任何結論日後都能被稽核與追溯——你能查到每個數字來自哪裡、怎麼算出來的。' },
]

function AnchorNav() {
  const items: { href: string; label: string }[] = [
    { href: '#glossary', label: '① 名詞解釋' },
    { href: '#how', label: '② 運作原理' },
    { href: '#score', label: '③ 信任分數怎麼算' },
    { href: '#faq', label: '④ 常見問題' },
  ]
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <a
          key={item.href}
          href={item.href}
          className="rounded-[7px] border border-tf-border bg-tf-card px-3.5 py-1.5 text-xs text-tf-muted no-underline hover:text-tf-text"
        >
          {item.label}
        </a>
      ))}
    </div>
  )
}

export default function HelpCenterPage() {
  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-5 px-4 py-6 sm:px-6" style={{ background: 'radial-gradient(ellipse at 70% -10%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 60%)', minHeight: 'calc(100vh - 57px)' }}>
      <div className="border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">HERMES · 使用說明</p>
        <h1 className="mt-1 text-2xl font-bold text-tf-text">說明中心</h1>
        <p className="mt-1 max-w-2xl text-sm text-tf-text2">
          HERMES 幫你判斷「一個幣的市場資料有多可信」——不是價格預測，也不是投資建議。本頁彙整所有專業術語的白話說明、系統運作原理，以及常見問題。
        </p>
      </div>

      <AnchorNav />

      <section id="glossary" className="hermes-clip scroll-mt-5 rounded-lg border border-tf-border bg-tf-card p-4 sm:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">01 · 名詞解釋 GLOSSARY</p>
        <p className="mb-4 mt-1 text-xs text-tf-muted">白話對照 · 附出現頁面</p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-tf-border text-xs uppercase tracking-wide text-tf-muted">
                <th className="px-2 py-2 font-medium">術語</th>
                <th className="px-2 py-2 font-medium">白話說明</th>
                <th className="px-2 py-2 font-medium">會在哪看到</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-tf-border">
              {GLOSSARY.map((g) => (
                <tr key={g.term}>
                  <td className="px-2 py-3 align-top">
                    <p className="text-sm font-semibold text-tf-text">{g.term}</p>
                    <p className="text-xs text-tf-muted">{g.en}</p>
                  </td>
                  <td className="px-2 py-3 align-top text-xs leading-relaxed text-tf-text2">{g.desc}</td>
                  <td className="px-2 py-3 align-top text-xs leading-relaxed text-tf-muted">{g.where}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section id="how" className="hermes-clip scroll-mt-5 rounded-lg border border-tf-border bg-tf-card p-4 sm:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">02 · 運作原理 HOW IT WORKS</p>
        <p className="mb-4 mt-1 text-xs text-tf-muted">signature 5-stage HERMES pipeline</p>
        <div className="flex flex-col gap-2">
          {STAGES.map((s) => (
            <div key={s.num} className={`flex items-start gap-3 rounded-[9px] border p-3 ${s.highlight ? 'border-tf-warn/50 bg-[color-mix(in_srgb,var(--color-tf-warn)_10%,transparent)]' : 'border-tf-border bg-tf-bg'}`}>
              <span
                className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[7px] text-xs font-bold"
                style={s.highlight
                  ? { background: 'color-mix(in srgb, var(--color-tf-warn) 20%, transparent)', border: '1px solid var(--color-tf-warn)', color: 'var(--color-tf-warn)' }
                  : { background: 'color-mix(in srgb, var(--color-tf-link) 15%, transparent)', border: '1px solid var(--color-tf-link)', color: 'var(--color-tf-link)' }}
              >
                {s.num}
              </span>
              <div>
                <p className="text-sm font-semibold text-tf-text">{s.label}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-tf-muted">{s.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section id="score" className="hermes-clip scroll-mt-5 rounded-lg border border-tf-border bg-tf-card p-4 sm:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">03 · 信任分數怎麼算 TRUST SCORE</p>
        <p className="mb-4 mt-1 text-xs text-tf-muted">0–100 score → color mapping</p>
        <p className="mb-4 max-w-3xl text-sm leading-relaxed text-tf-text2">
          HERMES 綜合「來源信譽、交叉佐證、資料時效、抗操縱能力」等維度，算出 0–100 的信任分數，並對應到紅／金／綠三色帶。
          <span className="font-semibold text-tf-text">分數高≠幣價會漲</span>
          ，只代表當下的市場資料越一致、越難被操縱、越適合作為判斷依據。
        </p>
        <div
          className="mb-2 h-4 rounded-lg"
          style={{ background: 'linear-gradient(90deg, var(--color-tf-bad) 0%, var(--color-tf-bad) 39%, var(--color-tf-warn) 40%, var(--color-tf-warn) 69%, var(--color-tf-green) 70%, var(--color-tf-green) 100%)' }}
        />
        <div className="mb-5 flex justify-between font-mono text-[10px] text-tf-muted">
          <span>0</span><span>39</span><span>40</span><span>69</span><span>70</span><span>100</span>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {BANDS.map((b) => (
            <div key={b.range} className="rounded-[9px] border p-4" style={{ background: b.bg, borderColor: b.border }}>
              <div className="mb-2 flex items-center gap-2">
                <span className="tf-num text-lg font-bold" style={{ color: b.color }}>{b.range}</span>
                <span className="text-sm font-semibold" style={{ color: b.color }}>{b.label}</span>
              </div>
              <p className="text-xs leading-relaxed text-tf-muted">{b.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="faq" className="hermes-clip scroll-mt-5 rounded-lg border border-tf-border bg-tf-card p-4 sm:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">04 · 常見問題 FAQ</p>
        <p className="mb-4 mt-1 text-xs text-tf-muted">frequently asked</p>
        <div className="flex flex-col gap-3">
          {FAQS.map((f) => (
            <div key={f.q} className="rounded-[10px] border border-tf-border bg-tf-bg p-4">
              <div className="mb-2 flex items-start gap-2">
                <span className="flex-shrink-0 text-sm font-bold text-tf-link">Q</span>
                <span className="text-sm font-semibold text-tf-text">{f.q}</span>
              </div>
              <div className="flex items-start gap-2">
                <span className="flex-shrink-0 text-sm font-bold text-tf-warn">A</span>
                <span className="text-xs leading-relaxed text-tf-muted">{f.a}</span>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-tf-border pt-4">
          <span className="text-xs text-tf-muted">
            還有問題？從任一頁面右上角的 <span className="text-tf-link">? 說明</span> 隨時回到這裡。
          </span>
          <a
            href="/analyze"
            className="rounded-[7px] px-4 py-2.5 text-xs font-bold tracking-wide text-tf-bg no-underline"
            style={{ background: 'linear-gradient(135deg,var(--color-tf-accent),#3bc0c8)' }}
          >
            開始一次分析 →
          </a>
        </div>
      </section>
    </main>
  )
}
