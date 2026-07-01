"""A 輪：claude.ai/design handoff dark dashboard 視覺落地驗收測試。

純視覺回歸測試，不動資料流欄位。驗證：
- dark token 色值真的出現在渲染輸出中
- SVG gauge 存在（_trust_bar / _conf_gauge 均已改弧形）
- 操縱（manipulation）以紅色扣分方向呈現，不是正向第四個 stacked block
- responsive media query 存在
- XSS regression：dark 卡片樣式包裝後，_safe_href / html.escape 仍擋住危險輸入
"""
from trustforge import web
from trustforge.schema import Evidence


# ---------------------------------------------------------------------------
# dark token 色值
# ---------------------------------------------------------------------------

def test_page_contains_dark_theme_tokens():
    """_PAGE 骨架應含 dark dashboard 色票（bg/card/text/green/red/border）。"""
    htmlout = web.render_page("")
    for token in ("#0d1117", "#161b22", "#e6edf3", "#3fb950", "#f85149", "#30363d"):
        assert token in htmlout, f"缺少 dark token 色值 {token}"


def test_orange_token_appears_in_mid_trust_rendering():
    """橙色 #d9832a 用於中信任層級（0.3~0.7），透過 _trust_bar 驗證色票存在。"""
    out = web._trust_bar(0.5)
    assert "#d9832a" in out, "中信任層級應使用 #d9832a"


def test_page_uses_ibm_plex_fonts():
    """頁面應載入 IBM Plex Sans / Mono 字體。"""
    htmlout = web.render_page("")
    assert "IBM Plex Sans" in htmlout
    assert "IBM Plex Mono" in htmlout


def test_page_has_responsive_media_query():
    """_PAGE 應含 @media(max-width:900px) 響應式規則。"""
    htmlout = web.render_page("")
    assert "@media (max-width:900px)" in htmlout, "缺少 responsive media query"


def test_page_has_top_header_bar_elements():
    """頂欄含 logo、VERSION 徽章、LIVE/離線 pulse 徽章、成本帳本連結。"""
    htmlout = web.render_page("")
    assert 'class="tf-hdr"' in htmlout
    assert 'class="tf-logo"' in htmlout
    assert 'class="tf-version"' in htmlout
    assert web.VERSION in htmlout
    assert "tf-mode-badge" in htmlout
    assert "tf-mode-dot" in htmlout
    assert 'href="/costs"' in htmlout


def test_offline_mode_badge_shows_offline_class():
    """HAS_BEDROCK=False 時，mode badge 應為 tf-offline，且顯示離線字樣。"""
    import trustforge.web as web_mod
    orig = web_mod.HAS_BEDROCK
    try:
        web_mod.HAS_BEDROCK = False
        htmlout = web_mod.render_page("")
        assert "tf-offline" in htmlout
        assert "離線" in htmlout
    finally:
        web_mod.HAS_BEDROCK = orig


def test_live_mode_badge_shows_live_class():
    """HAS_BEDROCK=True 且本次請求 active_mode="live" 時，mode badge 應為
    tf-live active，且顯示 LIVE 字樣。

    修法變更（MEDIUM provenance 修復後）：舊版此測試只憑 HAS_BEDROCK 就斷言
    LIVE 字樣一定出現，等同「只要設定就永遠顯示 LIVE」——這正是被回報的誤導
    行為本身，因此改為顯式帶入 active_mode="live" 才斷言 LIVE 出現。
    """
    import trustforge.web as web_mod
    orig = web_mod.HAS_BEDROCK
    try:
        web_mod.HAS_BEDROCK = True
        htmlout = web_mod.render_page("", active_mode="live")
        assert 'class="tf-mode-badge tf-live active"' in htmlout
        assert "LIVE" in htmlout
    finally:
        web_mod.HAS_BEDROCK = orig


def test_default_page_without_active_mode_shows_offline_active_only():
    """未帶 active_mode（如首頁 `/`、`/costs`）→ 預設離線示範為 active，
    真資料／真 Bedrock 兩檔僅渲染成灰色靜態能力標籤（無 active class）。
    """
    import trustforge.web as web_mod
    orig = web_mod.HAS_BEDROCK
    try:
        web_mod.HAS_BEDROCK = True
        htmlout = web_mod.render_page("")
        assert 'class="tf-mode-badge tf-offline active"' in htmlout
        assert 'class="tf-mode-badge tf-real active"' not in htmlout
        assert 'class="tf-mode-badge tf-live active"' not in htmlout
        # LIVE 字樣不應在非 live 請求的畫面出現，否則使用者誤以為本次是真 Bedrock 結果
        assert "LIVE" not in htmlout
    finally:
        web_mod.HAS_BEDROCK = orig


def test_exactly_one_mode_badge_active_per_request():
    """MEDIUM 修復核心斷言：任一 active_mode 狀態下，恰好一個徽章帶 active class
    （不會出現三檔同時 active，也不會一個都沒有）。
    """
    import re
    import trustforge.web as web_mod
    orig = web_mod.HAS_BEDROCK
    try:
        web_mod.HAS_BEDROCK = True
        for mode in ("offline", "real", "live"):
            htmlout = web_mod.render_page("", active_mode=mode)
            actives = re.findall(r'class="tf-mode-badge [a-z-]+ active"', htmlout)
            assert len(actives) == 1, (
                f"active_mode={mode!r} 應恰好 1 個 active 徽章，實際：{actives}"
            )
    finally:
        web_mod.HAS_BEDROCK = orig


# ---------------------------------------------------------------------------
# SVG gauge
# ---------------------------------------------------------------------------

def test_trust_bar_renders_svg_arc():
    """_trust_bar(trust:float) 應輸出 SVG 弧形（<svg>...<circle>），簽名不變。"""
    out = web._trust_bar(0.82)
    assert "<svg" in out
    assert "<circle" in out
    assert "0.82" in out


def test_conf_gauge_renders_svg_arc():
    """_conf_gauge 應輸出 270 度 SVG 弧形 gauge（tf-conf-wrap 容器 + svg + circle）。"""
    out = web._conf_gauge(0.91, "高信心")
    assert "tf-conf-wrap" in out
    assert "<svg" in out
    assert "<circle" in out
    assert "高信心" in out


# ---------------------------------------------------------------------------
# 操縱＝紅色扣分方向，非正向第四塊
# ---------------------------------------------------------------------------

def test_manipulation_rendered_as_deficit_not_positive_block():
    """manipulation 應以獨立紅色扣分 bar 呈現（含負號/扣分文字），
    且不可與信譽/佐證/時效並列成正向第四個 stacked segment。
    """
    tc = {"reputation": 0.9, "corroboration": 0.5, "recency": 0.8, "manipulation": 0.6}
    out = web._render_trust_breakdown(tc, trust=0.42)

    # 扣分方向的視覺/文字證據：負號 + 「扣分」字樣 + 操縱數值
    assert "−" in out or "-" in out, "應有負號代表扣分方向"
    assert "扣分" in out
    assert "0.60" in out  # manipulation 值有顯示

    # 正向 stacked bar 只由信譽/佐證/時效三個分項組成（用 title 屬性驗證只有三段
    # 權重樣式；操縱只能以「操縱扣分」的獨立 deficit bar 出現，不可有 "操縱 x×0.40"
    # 這種與其他三項同格式的正向 segment title）
    assert 'title="信譽 ' in out
    assert 'title="佐證 ' in out
    assert 'title="時效 ' in out
    assert 'title="操縱 ' not in out, "操縱不可並列成正向 stacked bar 的第四塊"
    assert 'title="操縱扣分' in out, "操縱應以獨立扣分 bar 呈現"

    # weight 字樣：信譽0.50/佐證0.25/時效0.15/操縱0.40
    assert "×0.50" in out
    assert "×0.25" in out
    assert "×0.15" in out
    assert "×0.40" in out


def test_manipulation_zero_no_deficit_text_confusion():
    """manipulation=0 時仍應正常顯示（不崩潰），且不主張有扣分。"""
    tc = {"reputation": 0.9, "corroboration": 0.5, "recency": 0.8, "manipulation": 0.0}
    out = web._render_trust_breakdown(tc, trust=0.7)
    assert "0.00" in out
    assert isinstance(out, str)


def test_trust_breakdown_has_why_caption():
    """四分項均應附上 WHY caption 說明行。"""
    tc = {"reputation": 0.9, "corroboration": 0.5, "recency": 0.8, "manipulation": 0.0}
    out = web._render_trust_breakdown(tc, trust=0.7)
    assert out.count("WHY") == 4, "四分項應各有一行 WHY caption"


# ---------------------------------------------------------------------------
# XSS regression（dark 樣式包裝後，逃逸點仍必須擋住）
# ---------------------------------------------------------------------------

def test_dark_theme_evidence_card_still_escapes_script_tag():
    """dark 卡片樣式（tf-src-pill / tf-ev-*）套上後，<script> 仍必須被 escape。"""
    ev = [
        Evidence(
            source="<script>alert(1)</script>",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="<script>alert(2)</script>",
            related_claim="claim",
            source_url="javascript:alert(3)",
            trust=0.5,
        )
    ]
    report, _, _ = web._do_analyze({"coin": ["BTC"], "type": ["multi_source"], "q": ["t"]})
    out = web._render_report(report, ev)
    assert "<script>" not in out
    assert 'href="javascript:' not in out
    # dark pill class 仍應正常渲染（沒有因為包 class 而漏掉 escape）
    assert "tf-src-pill" in out


def test_dark_theme_cost_card_still_renders_amounts_unchanged():
    """cost card 套 dark 樣式後，金額字樣（$0.00（離線）等）不變、計算不受影響。"""
    report, evidence, log = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, evidence, log)
    assert "本次分析成本" in htmlout
    assert "$0.00（離線）" in htmlout


# ---------------------------------------------------------------------------
# 雙欄 dashboard 佈局（CEO Chrome 對稿後第二輪：佈局重構）
# ---------------------------------------------------------------------------

def test_page_has_two_column_layout_grid():
    """render_page() 應含左 Query Console 面板 + 右 dashboard 主欄的 grid 容器。"""
    htmlout = web.render_page("")
    assert 'class="tf-layout"' in htmlout
    assert 'class="tf-query-panel"' in htmlout
    assert 'class="tf-dashboard"' in htmlout
    assert "grid-template-columns" in htmlout


def test_query_panel_contains_form_fields():
    """Query Console 面板應含幣種/題型/問題/分析按鈕（表單移進左側常駐面板）。"""
    htmlout = web.render_page("")
    assert "Query Console" in htmlout
    assert '<select name="coin">' in htmlout
    assert '<select name="type">' in htmlout
    assert '<input name="q"' in htmlout
    assert "<form" in htmlout


def test_responsive_query_collapses_two_column_to_single():
    """@media(max-width:900px) 應把雙欄 grid 收成單欄（手機堆疊）。"""
    htmlout = web.render_page("")
    media_idx = htmlout.index("@media (max-width:900px)")
    media_block = htmlout[media_idx: media_idx + 400]
    assert ".tf-layout" in media_block
    assert "grid-template-columns:1fr" in media_block


def test_conf_gauge_enlarged_for_hero_row():
    """_conf_gauge 應放大（>=160px 尺寸），符合設計稿大 Trust Score gauge。"""
    out = web._conf_gauge(0.8, "高信心")
    assert 'width="168"' in out
    assert 'height="168"' in out


def test_report_hero_row_places_gauge_and_breakdown_side_by_side():
    """_render_report 頂部 hero 應把大 gauge 與 Trust Breakdown 四分項並排在同一列。"""
    report, evidence, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, evidence)
    assert "tf-hero-row" in htmlout
    hero_idx = htmlout.index("tf-hero-row")
    hero_block = htmlout[hero_idx: hero_idx + 4000]
    assert "tf-conf-wrap" in hero_block, "hero row 應含大 gauge"
    assert "信任分析" in hero_block, "hero row 應含 Trust Breakdown 分項"


def test_report_has_coin_badge_and_question_header():
    """結果頁頂部應有「幣種 ● 問題」的 dashboard 標題列。"""
    report, evidence, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["測試問題"]}
    )
    htmlout = web._render_report(report, evidence)
    assert "tf-dash-hdr" in htmlout
    assert "tf-coin-badge" in htmlout
    assert "BTC" in htmlout
    assert "測試問題" in htmlout


def test_report_facts_inferences_conclusion_use_step_ladder_class():
    """事實/推論/結論改用 tf-step 階梯卡片樣式（非純 <ul>）。"""
    report, evidence, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, evidence)
    assert htmlout.count('class="tf-step"') >= 3


def test_aggregate_trust_components_pure_average_no_mutation():
    """_aggregate_trust_components 純渲染層平均，不改 evidence 原始 trust_components。"""
    ev = [
        Evidence(source="a", fetched_at="t", content_reference="c", related_claim="x",
                 trust=0.8, trust_components={"reputation": 0.9, "corroboration": 1.0,
                                               "recency": 0.5, "manipulation": 0.0}),
        Evidence(source="b", fetched_at="t", content_reference="c", related_claim="x",
                 trust=0.4, trust_components={"reputation": 0.5, "corroboration": 0.0,
                                               "recency": 0.5, "manipulation": 0.4}),
    ]
    orig_a = dict(ev[0].trust_components)
    orig_b = dict(ev[1].trust_components)
    agg = web._aggregate_trust_components(ev)
    assert agg["reputation"] == 0.7
    assert agg["corroboration"] == 0.5
    assert agg["manipulation"] == 0.2
    # 原始 evidence 資料未被改動
    assert ev[0].trust_components == orig_a
    assert ev[1].trust_components == orig_b


def test_aggregate_trust_components_empty_when_no_data():
    """全部 evidence 都沒有 trust_components 時，回傳空 dict（不崩、不誤導顯示）。"""
    ev = [Evidence(source="a", fetched_at="t", content_reference="c", related_claim="x", trust=0.5)]
    assert web._aggregate_trust_components(ev) == {}
    assert web._aggregate_trust_components([]) == {}


# ---------------------------------------------------------------------------
# Tier2 可解釋 UX：來源獨立性標籤 + 操縱紅旗 + 跨源背離雙欄（gray 計劃 W2）
# ---------------------------------------------------------------------------

def test_independence_tier_official_kinds_are_hoyabit_regulatory_price_only():
    """真正一手權威（官方）僅限 hoyabit（交易所一手行情）、regulatory（SEC 直接
    feed）、price（HOYA 官方基準 OHLCV）——不可再擴大範圍（codex provenance
    review，PR #35）。"""
    for kind in ("hoyabit", "regulatory", "price"):
        label, _color = web._independence_tier(kind)
        assert "官方" in label, f"{kind} 應歸官方，實得 {label}"
        assert "高" in label


def test_independence_tier_price_live_coingecko_never_renders_as_official():
    """CoinGecko(price_live) 是第三方聚合器，永不可標「官方」——只能是「高·第三方」。
    這是本輪 codex provenance 準確性修復的核心斷言：破這條＝回歸誤導 UX。"""
    label, _color = web._independence_tier("price_live")
    assert "官方" not in label, f"price_live 不應標官方，實得 {label}"
    assert label == "高·第三方", f"price_live 應為『高·第三方』，實得 {label}"


def test_independence_tier_onchain_third_party_aggregator_not_official():
    """onchain（blockchain.info／Alternative.me FNG，第三方公開 API）同樣不是
    一手權威，不可標官方，應為『高·第三方』。"""
    label, _color = web._independence_tier("onchain")
    assert "官方" not in label, f"onchain 不應標官方，實得 {label}"
    assert label == "高·第三方", f"onchain 應為『高·第三方』，實得 {label}"


def test_independence_tier_maps_sentiment_kinds_to_community_medium():
    """情緒／社群（news/social/sentiment）→ 中·社群（中等獨立性）。"""
    for kind in ("news", "social", "sentiment"):
        label, _color = web._independence_tier(kind)
        assert label == "中·社群", f"{kind} 應歸『中·社群』，實得 {label}"


def test_independence_tier_unclassified_kind_falls_back_to_general():
    """未分類的 kind（如 dev_activity）→ 一般，不誤標成官方/第三方/社群。"""
    label, _color = web._independence_tier("dev_activity")
    assert label == "一般·輔助", f"dev_activity 應歸『一般·輔助』，實得 {label}"


def test_evidence_list_shows_tier_pill_next_to_source():
    """evidence 清單來源 pill 旁應附 tier·權威性標籤；CoinGecko(price_live) 的
    evidence 渲染出來的 pill 絕不可含「官方」字樣。"""
    ev = [Evidence(source="coingecko-price", fetched_at="t", content_reference="c",
                    related_claim="x", trust=0.9, kind="price_live")]
    htmlout = web._render_evidence_list(ev)
    assert "tf-tier-pill" in htmlout
    assert "高·第三方" in htmlout
    assert "官方" not in htmlout, "CoinGecko evidence pill 不應出現『官方』字樣"


def test_evidence_list_shows_manipulation_red_flag_badge():
    """ev.flags 非空 → 顯示操縱紅旗徽章（沿用 tf-low 樣式）+ 命中關鍵詞原文。"""
    ev = [Evidence(source="x-anon", fetched_at="t", content_reference="穩賺翻倍！",
                    related_claim="x", trust=0.15, kind="social",
                    flags=["穩賺", "翻倍"])]
    htmlout = web._render_evidence_list(ev)
    assert "&#128681;" in htmlout, "應有紅旗 emoji entity"
    assert "穩賺" in htmlout
    assert "翻倍" in htmlout


def test_evidence_list_no_flag_badge_when_flags_empty():
    """ev.flags 為空 list 時不應出現紅旗徽章。"""
    ev = [Evidence(source="coindesk", fetched_at="t", content_reference="正常新聞",
                    related_claim="x", trust=0.8, kind="news")]
    htmlout = web._render_evidence_list(ev)
    assert "&#128681;" not in htmlout


def test_evidence_list_tier_pill_escapes_xss_in_source():
    """tier pill 套上後，source 的 XSS payload 仍須被 html.escape（縱深防禦回歸）。"""
    ev = [Evidence(source="<script>alert(1)</script>", fetched_at="t", content_reference="c",
                    related_claim="x", trust=0.9, kind="price_live",
                    flags=["<img onerror=alert(1)>"])]
    htmlout = web._render_evidence_list(ev)
    assert "<script>alert(1)</script>" not in htmlout
    assert "<img onerror=alert(1)>" not in htmlout
    assert "&lt;script&gt;" in htmlout


def test_cross_signal_divergence_renders_bullish_bearish_columns_via_stance_pairs():
    """背離 + stance_pairs → 結構化雙欄 BULLISH/BEARISH + Δ%。"""
    signal = {
        "type": "divergence",
        "summary": "客觀與情緒方向相反",
        "supporting_claim_ids": ["c1", "c2"],
        "stance_pairs": [
            {"source": "coingecko-price", "stance": "bullish", "claim_id": "c1", "text": "ETH 現價上漲"},
            {"source": "coingecko-sentiment", "stance": "bearish", "claim_id": "c2", "text": "ETH 社群看跌"},
        ],
    }
    htmlout = web._render_cross_signal(signal)
    assert "BULLISH" in htmlout
    assert "BEARISH" in htmlout
    assert "tf-div-grid" in htmlout
    assert "&Delta;" in htmlout, "應含 Δ% 徽章"
    assert "ETH 現價上漲" in htmlout
    assert "ETH 社群看跌" in htmlout


def test_cross_signal_divergence_renders_columns_via_aggregate_directions():
    """背離但無 stance_pairs（純聚合層級）→ 仍能從 objective/sentiment_direction 推導雙欄。"""
    signal = {
        "type": "divergence",
        "summary": "客觀看漲、情緒看跌",
        "supporting_claim_ids": [],
        "objective_direction": "bullish",
        "sentiment_direction": "bearish",
    }
    htmlout = web._render_cross_signal(signal)
    assert "BULLISH" in htmlout
    assert "BEARISH" in htmlout
    assert "tf-div-grid" in htmlout


def test_cross_signal_consensus_has_no_bullish_bearish_columns():
    """共識（非背離）不應出現雙欄結構——舊版純文字渲染保留。"""
    signal = {
        "type": "consensus",
        "summary": "多源一致看漲",
        "supporting_claim_ids": ["c1"],
    }
    htmlout = web._render_cross_signal(signal)
    assert "跨源訊號（共識）" in htmlout
    assert "tf-div-grid" not in htmlout
    assert "BULLISH" not in htmlout


def test_cross_signal_divergence_without_sides_falls_back_to_summary_only():
    """背離但既無 stance_pairs 也無完整 objective/sentiment_direction（如舊版
    fixture）→ 不強行湊雙欄，退回舊版純文字渲染，功能零損。"""
    signal = {"type": "divergence", "summary": "背離摘要", "supporting_claim_ids": []}
    htmlout = web._render_cross_signal(signal)
    assert "跨源訊號（背離）" in htmlout
    assert "背離摘要" in htmlout
    assert "tf-div-grid" not in htmlout


def test_cross_signal_xss_escaped_in_stance_pair_text_and_source():
    """雙欄結構化渲染下，stance_pairs 的 source/text XSS payload 仍須被 escape。"""
    signal = {
        "type": "divergence",
        "summary": "s",
        "supporting_claim_ids": [],
        "stance_pairs": [
            {"source": "<script>a1</script>", "stance": "bullish", "claim_id": "c1",
             "text": "<img onerror=alert(2)>"},
            {"source": "ok-src", "stance": "bearish", "claim_id": "c2", "text": "正常內容"},
        ],
    }
    htmlout = web._render_cross_signal(signal)
    assert "<script>a1</script>" not in htmlout
    assert "<img onerror=alert(2)>" not in htmlout


def test_render_report_step_ladder_headers_have_step_numbers():
    """事實鏈加序號：事實/推論/結論標題應附「步驟 N/3」徽章。"""
    report, evidence, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, evidence)
    assert "步驟 1/3" in htmlout
    assert "步驟 2/3" in htmlout
    assert "步驟 3/3" in htmlout
