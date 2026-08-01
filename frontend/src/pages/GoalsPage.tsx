import { useHermesI18n } from '../hermes/hermesI18n'

export default function GoalsPage() {
  const { locale } = useHermesI18n()
  const isZh = locale === 'zh-TW'

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '32px 24px' }}>
      <h1 style={{ fontSize: 22, marginBottom: 8, color: 'var(--color-hermes-tx)' }}>
        {isZh ? '🎯 TrustForge 專案目標' : '🎯 TrustForge Project Goals'}
      </h1>
      <p style={{ fontSize: 13, color: 'var(--color-hermes-tx2)', marginBottom: 32 }}>
        {isZh
          ? '我們的定位：不預測價格，專注提供可溯源、可審計的多源資訊信任層。'
          : 'Our positioning: not price prediction, but a traceable, auditable trust layer for multi-source information.'}
      </p>

      {/* 短期 */}
      <section style={{ marginBottom: 28 }}>
        <h2 style={{ fontSize: 15, color: 'var(--color-hermes-cyan)', marginBottom: 12, letterSpacing: 1 }}>
          {isZh ? '🏁 短期目標（本次黑客松）' : '🏁 Short-term (This Hackathon)'}
        </h2>
        <ul style={{ listStyle: 'none', padding: 0, display: 'grid', gap: 8 }}>
          {(isZh ? [
            '15 分鐘內完成完整分析（實測 25-68 秒）',
            '產出官方四件交付物（報告 + Evidence + Log + Code）',
            '每條結論可溯源到原始來源（claim_id → source_url）',
            '矛盾訊號不隱藏，誠實標明限制與不確定性',
            '信任分數四維拆解完全透明，使用者可自行驗證',
          ] : [
            'Complete full analysis within 15 minutes (tested: 25-68s)',
            'Produce all 4 official deliverables (Report + Evidence + Log + Code)',
            'Every conclusion traceable to original source (claim_id → source_url)',
            'Contradictions visible, limitations honestly stated',
            'Trust score 4D breakdown fully transparent and verifiable',
          ]).map((item, i) => (
            <li key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, color: 'var(--color-hermes-tx)' }}>
              <span style={{ color: '#86efac', flexShrink: 0 }}>✅</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* 中期 */}
      <section style={{ marginBottom: 28 }}>
        <h2 style={{ fontSize: 15, color: 'var(--color-hermes-amber)', marginBottom: 12, letterSpacing: 1 }}>
          {isZh ? '📈 中期目標（3-6 個月）' : '📈 Mid-term (3-6 months)'}
        </h2>
        <ul style={{ listStyle: 'none', padding: 0, display: 'grid', gap: 8 }}>
          {(isZh ? [
            '整合進 HOYA BIT AI 市場資訊服務，每條分析帶可信度＋溯源按鈕',
            '歷史信任分數追蹤——哪些來源長期最準',
            '白標 Trust Layer API 對外開放，按呼叫次數計費',
            '自適應來源信譽系統（動態更新，不再只有預設值）',
          ] : [
            'Integrate into HOYA BIT AI market info service with trust + traceability',
            'Historical trust score tracking — which sources are most reliable over time',
            'White-label Trust Layer API, billed per call',
            'Adaptive source reputation system (dynamic updates)',
          ]).map((item, i) => (
            <li key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, color: 'var(--color-hermes-tx)' }}>
              <span style={{ color: 'var(--color-hermes-amber)', flexShrink: 0 }}>🔜</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* 長期 */}
      <section style={{ marginBottom: 28 }}>
        <h2 style={{ fontSize: 15, color: '#a78bfa', marginBottom: 12, letterSpacing: 1 }}>
          {isZh ? '🌟 長期願景' : '🌟 Long-term Vision'}
        </h2>
        <ul style={{ listStyle: 'none', padding: 0, display: 'grid', gap: 8 }}>
          {(isZh ? [
            '成為加密市場的「可審計信任標準」',
            '跨平台信任層整合（不只 HOYA BIT）',
            '建立來源信譽歷史資料庫，供整個生態系使用',
          ] : [
            'Become the "auditable trust standard" for crypto markets',
            'Cross-platform trust layer integration (beyond HOYA BIT)',
            'Build source reputation historical database for the entire ecosystem',
          ]).map((item, i) => (
            <li key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, color: 'var(--color-hermes-tx)' }}>
              <span style={{ color: '#a78bfa', flexShrink: 0 }}>🌟</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* 不追求 */}
      <section style={{ marginBottom: 28, padding: 16, border: '1px solid rgba(239,68,68,0.3)', borderRadius: 10, background: 'rgba(239,68,68,0.04)' }}>
        <h2 style={{ fontSize: 15, color: '#ef4444', marginBottom: 12, letterSpacing: 1 }}>
          {isZh ? '🚫 我們不追求的' : '🚫 What We Do NOT Pursue'}
        </h2>
        <ul style={{ listStyle: 'none', padding: 0, display: 'grid', gap: 8 }}>
          {(isZh ? [
            '價格預測（內部驗證 AUC≈0.49，近隨機，誠實不做）',
            '代替人類決策（我們提供輔助判斷，不給買賣建議）',
            '黑箱分數（每個分數都有四維拆解可查）',
          ] : [
            'Price prediction (internal AUC≈0.49, near random — honestly not pursuing)',
            'Replacing human decisions (we assist judgment, never give buy/sell advice)',
            'Black-box scores (every score has a 4D breakdown you can inspect)',
          ]).map((item, i) => (
            <li key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, color: 'var(--color-hermes-tx)' }}>
              <span style={{ color: '#ef4444', flexShrink: 0 }}>❌</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* 核心技術 */}
      <section>
        <h2 style={{ fontSize: 15, color: 'var(--color-hermes-cyan)', marginBottom: 12, letterSpacing: 1 }}>
          {isZh ? '⚙️ 核心技術' : '⚙️ Core Technologies'}
        </h2>
        <div style={{ display: 'grid', gap: 6 }}>
          {(isZh ? [
            ['Trust Layer（純演算法）', '四維信任評分，不靠 LLM，不被幻覺汙染'],
            ['Claim Extraction', '從原始文件抽取結構化主張（Bedrock）'],
            ['Cross-Source Conflation 防禦', '防止來源歸因錯誤'],
            ['來源獨立性去重', '近似重複偵測，先去重再計票'],
            ['操縱訊號偵測', '匿名喊單語意 + 協同發文密集度'],
            ['資訊完整度分級', '高/中/低/棄權，非單一黑箱數字'],
            ['Reasoning Provenance', '報告標記 pipeline 判斷 vs LLM 行文'],
            ['14-Tool Hermes Agent', '持續研究循環 + 有界自我改善'],
            ['反事實 A/B 對照', '離線對照展示 Trust Layer 價值'],
            ['負空間情報', '把缺席當情報，不略過空維度'],
            ['Token Gate', '防 Bedrock 被濫用，保護 AWS 費用'],
            ['AWS Kiro IDE', '全程 AI 整合開發環境'],
          ] : [
            ['Trust Layer (pure algorithm)', '4D trust scoring without LLM, hallucination-proof'],
            ['Claim Extraction', 'Structured claim extraction from raw documents (Bedrock)'],
            ['Cross-Source Conflation Defense', 'Prevent source misattribution'],
            ['Source Independence Dedup', 'Near-duplicate detection, deduplicate before counting'],
            ['Manipulation Signal Detection', 'Anonymous shilling + coordinated posting patterns'],
            ['Information Completeness Grading', 'High/Med/Low/Abstain, not a single black-box number'],
            ['Reasoning Provenance', 'Report marks pipeline judgment vs LLM narration'],
            ['14-Tool Hermes Agent', 'Continuous research cycle + bounded self-improvement'],
            ['Counterfactual A/B Contrast', 'Offline comparison showing Trust Layer value'],
            ['Negative Space Intelligence', 'Treat absence as intelligence, never skip empty dimensions'],
            ['Token Gate', 'Prevent Bedrock abuse, protect AWS costs'],
            ['AWS Kiro IDE', 'Full AI-integrated development environment'],
          ]).map(([title, desc], i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 8, padding: '6px 10px', borderBottom: '1px solid var(--color-hermes-bd)', fontSize: 12 }}>
              <strong style={{ color: 'var(--color-hermes-tx)' }}>{title}</strong>
              <span style={{ color: 'var(--color-hermes-tx2)' }}>{desc}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
