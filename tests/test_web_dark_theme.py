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
    """Query Console 面板應含幣種/題型/問題/分析按鈕（表單移進左側常駐面板）。

    第三輪 claude.ai/design world-class handoff（task #18）：問題欄位改用
    `<textarea>`（設計稿 Prompt textarea），純視覺升級，非資料/邏輯變動。
    """
    htmlout = web.render_page("")
    assert "Query Console" in htmlout
    assert '<select name="coin">' in htmlout
    assert '<select name="type">' in htmlout
    assert '<textarea name="q"' in htmlout
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
    """背離 + stance_pairs → 結構化雙欄 BULLISH/BEARISH，附誠實來源數對比
    （不做假精度的量化 Δ% 徽章，見 codex provenance review 第二輪修正）。"""
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
    assert "看漲 1 來源" in htmlout and "看跌 1 來源" in htmlout, "應顯示誠實的來源數對比"
    assert "ETH 現價上漲" in htmlout
    assert "ETH 社群看跌" in htmlout


def test_cross_signal_divergence_never_shows_fabricated_delta_percentage():
    """核心回歸鎖：背離雙欄絕不可出現 Δ%／百分比幅度徽章——stance_pairs 是
    未加權去重矛盾集，筆數差換算成百分比等於假精度（codex MEDIUM，PR #35）。"""
    signal = {
        "type": "divergence",
        "summary": "s",
        "supporting_claim_ids": [],
        "stance_pairs": [
            {"source": "a", "stance": "bullish", "claim_id": "c1", "text": "t1"},
            {"source": "b", "stance": "bullish", "claim_id": "c2", "text": "t2"},
            {"source": "c", "stance": "bearish", "claim_id": "c3", "text": "t3"},
        ],
    }
    htmlout = web._render_cross_signal(signal)
    assert "&Delta;" not in htmlout, "不應出現 Δ 符號"
    assert "Δ" not in htmlout
    assert "CONFLICT" not in htmlout
    assert "%" not in htmlout, "不應出現任何百分比幅度徽章"
    assert "看漲 2 來源" in htmlout and "看跌 1 來源" in htmlout


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


# ---------------------------------------------------------------------------
# D 輪：CEO Chrome 複審 4 項修正回歸測試（PR #39）
# ---------------------------------------------------------------------------
#
# 1. [HIGH] 主題切換改 cookie-based + /theme 輕量路由，結構上不呼叫 pipeline。
# 2. [MEDIUM] RUN STATS 命名誠實化（Flagged 不是 Flagged dropped）+ 可對帳。
# 4. [MEDIUM] light 主題全卡片一致（header/gauge 卡/信任分析卡不再寫死深色）。
#    （[MEDIUM] header cost O(1) 快取的回歸測試放在 tests/test_cost_ledger.py。）

def _make_mock_handler(path: str):
    """建一個最小化 web.Handler mock，能讓 do_GET 真的跑、但不開真 socket。
    沿用 tests/test_security.py::test_web_handler_502_on_unexpected_exception
    的既有模式。"""
    from io import BytesIO

    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.path = path
    h.wfile = BytesIO()
    h._captured = []

    def fake_send_response(code):
        h._captured.append(("status", code))

    def fake_send_header(name, val):
        h._captured.append(("header", name, val))

    def fake_end_headers():
        pass

    h.send_response = fake_send_response
    h.send_header = fake_send_header
    h.end_headers = fake_end_headers
    return h


def test_theme_toggle_with_rtok_never_calls_pipeline(monkeypatch):
    """HIGH 修正核心斷言：`/theme?...&rtok=...` 直接用 `_render_cache` 重繪，
    結構上完全不呼叫 `run`/`run_comparison`——即使把兩者都 monkeypatch 成
    「一被呼叫就 raise」，主題切換仍應正常回 200，證明真的沒被呼叫到
    （避免 live 模式下切主題重複計費/重複命中限流）。"""
    def _boom(*a, **kw):
        raise AssertionError("主題切換不應該呼叫 run()/run_comparison()")

    monkeypatch.setattr(web, "run", _boom)
    monkeypatch.setattr(web, "run_comparison", _boom)

    rtok = web._render_cache_put("<p>cached report body</p>", "offline", "")
    h = _make_mock_handler(f"/theme?to=light&rtok={rtok}")
    h.do_GET()  # 若內部誤呼叫 run()，_boom 會在這裡 raise，測試會失敗

    statuses = [c[1] for c in h._captured if c[0] == "status"]
    assert statuses == [200]
    body = h.wfile.getvalue().decode("utf-8")
    assert "cached report body" in body
    assert 'data-theme="light"' in body


def test_theme_toggle_without_rtok_redirects_and_never_calls_pipeline(monkeypatch):
    """沒有 rtok（如首頁的主題切換）走 302 導回，一樣結構上不呼叫 pipeline。"""
    def _boom(*a, **kw):
        raise AssertionError("主題切換不應該呼叫 run()/run_comparison()")

    monkeypatch.setattr(web, "run", _boom)
    monkeypatch.setattr(web, "run_comparison", _boom)

    h = _make_mock_handler("/theme?to=dark&next=%2Fcosts")
    h.do_GET()

    statuses = [c[1] for c in h._captured if c[0] == "status"]
    assert statuses == [302]
    locations = [c[2] for c in h._captured if c[0] == "header" and c[1] == "Location"]
    assert locations == ["/costs"]


def test_theme_toggle_sets_cookie_not_query_param():
    """主題持久化改靠 `Set-Cookie: tf_theme=...`，不再靠 query string。"""
    h = _make_mock_handler("/theme?to=light&next=%2F")
    h.do_GET()
    cookies = [c[2] for c in h._captured if c[0] == "header" and c[1] == "Set-Cookie"]
    assert any(c.startswith("tf_theme=light") for c in cookies)


def test_theme_toggle_rejects_open_redirect_next():
    """`next` 只允許站內相對路徑，帶 `//evil.com` 這種會被瀏覽器當成
    protocol-relative URL 的值一律退回首頁，不可用來做開放重導向。"""
    h = _make_mock_handler("/theme?to=dark&next=%2F%2Fevil.com")
    h.do_GET()
    locations = [c[2] for c in h._captured if c[0] == "header" and c[1] == "Location"]
    assert locations == ["/"]


# ---------------------------------------------------------------------------
# HIGH 安全修正（codex 複審，PR #39）：`/theme` 的 `next` 若只檢查
# 「以單一 `/` 開頭」，percent-decode 後帶 CRLF/控制字元/backslash 的值
# 會被原封不動塞進 `Location` header，造成 response splitting／header 注入。
# `_sanitize_theme_next` 必須嚴格擋下這些變體，只放行 allowlist 內的路由。
# ---------------------------------------------------------------------------

def test_theme_next_crlf_injection_rejected_and_location_header_clean():
    """`next=%2F%0D%0ASet-Cookie%3Aattacker%3D1`（percent-encoded CRLF）
    decode 後會變成帶 `\\r\\n` 的字串——必須被拒絕、fallback `/`，且
    `Location` header 裡完全不能出現 `\\r`/`\\n`（不能被拿來注入額外
    response header，例如偽造 `Set-Cookie`）。"""
    h = _make_mock_handler(
        "/theme?to=dark&next=%2F%0D%0ASet-Cookie%3Aattacker%3D1"
    )
    h.do_GET()
    locations = [c[2] for c in h._captured if c[0] == "header" and c[1] == "Location"]
    assert locations == ["/"]
    assert "\r" not in locations[0]
    assert "\n" not in locations[0]
    # 確認沒有任何回應 header 被偷渡了額外的 Set-Cookie（只能有 /theme
    # 自己合法設的那個 tf_theme cookie，不能有 attacker=1）。
    set_cookies = [c[2] for c in h._captured if c[0] == "header" and c[1] == "Set-Cookie"]
    assert all("attacker" not in c for c in set_cookies)


def test_theme_next_backslash_variant_rejected():
    """`/\\evil.com`（單一 `/` 開頭但夾帶 backslash）——部分瀏覽器/代理
    會把 `\\` 正規化成 `/`，等同 `//evil.com` 的 protocol-relative 外部
    導向，必須拒絕。"""
    h = _make_mock_handler("/theme?to=dark&next=%2F%5Cevil.com")
    h.do_GET()
    locations = [c[2] for c in h._captured if c[0] == "header" and c[1] == "Location"]
    assert locations == ["/"]


def test_theme_next_tab_control_char_rejected():
    """`next=%2F%09`（tab，ASCII 0x09，屬控制字元）——即使以單一 `/`
    開頭也必須拒絕，不能只驗開頭字元就放行。"""
    h = _make_mock_handler("/theme?to=dark&next=%2F%09evil")
    h.do_GET()
    locations = [c[2] for c in h._captured if c[0] == "header" and c[1] == "Location"]
    assert locations == ["/"]


def test_theme_next_not_in_allowlist_rejected():
    """就算是「看起來正常」的站內相對路徑，只要不在已知路由 allowlist
    內（`/`、`/analyze`、`/analyze.json`、`/costs`），一律 fallback，
    不接受任意站內路徑當開放跳板。"""
    h = _make_mock_handler("/theme?to=dark&next=%2Fadmin%2Fsecret")
    h.do_GET()
    locations = [c[2] for c in h._captured if c[0] == "header" and c[1] == "Location"]
    assert locations == ["/"]


def test_theme_next_valid_analyze_path_with_query_round_trips():
    """正常案例：`next=/analyze?coin=BTC&...` 這種帶合法 query string 的
    allowlist 路徑必須原樣導回，不能被過度攔阻（功能不能被安全修正誤傷）。"""
    h = _make_mock_handler(
        "/theme?to=dark&next=%2Fanalyze%3Fcoin%3DBTC%26type%3Dmulti_source%26q%3Dtest"
    )
    h.do_GET()
    locations = [c[2] for c in h._captured if c[0] == "header" and c[1] == "Location"]
    assert locations == ["/analyze?coin=BTC&type=multi_source&q=test"]


def test_sanitize_theme_next_unit_coverage():
    """直接單元測試 `_sanitize_theme_next`，涵蓋 codex 列出的所有變體。"""
    assert web._sanitize_theme_next(None) == "/"
    assert web._sanitize_theme_next("") == "/"
    assert web._sanitize_theme_next("/") == "/"
    assert web._sanitize_theme_next("/costs") == "/costs"
    assert web._sanitize_theme_next("/analyze?coin=BTC") == "/analyze?coin=BTC"
    assert web._sanitize_theme_next("/analyze.json?coin=BTC") == "/analyze.json?coin=BTC"
    # protocol-relative
    assert web._sanitize_theme_next("//evil.com") == "/"
    # backslash 變體
    assert web._sanitize_theme_next("/\\evil.com") == "/"
    assert web._sanitize_theme_next("\\evil.com") == "/"
    # CRLF / 控制字元注入
    assert web._sanitize_theme_next("/\r\nSet-Cookie:attacker=1") == "/"
    assert web._sanitize_theme_next("/\tevil") == "/"
    assert web._sanitize_theme_next("/\x00evil") == "/"
    # 不在 allowlist
    assert web._sanitize_theme_next("/admin/secret") == "/"
    assert web._sanitize_theme_next("/healthz") == "/"
    # 絕對 URL（帶 scheme/netloc）
    assert web._sanitize_theme_next("http://evil.com") == "/"
    assert web._sanitize_theme_next("https://evil.com/analyze") == "/"


def test_analyze_success_page_theme_link_uses_rtok_not_query_theme():
    """`/analyze` 成功頁的主題切換連結必須帶 rtok、指向 `/theme`；
    不能再把 `theme=` 塞進 `/analyze` 本身的網址（HIGH 修正的直接體現：
    重點分析結果頁本身的網址／自我連結都不該含 theme 參數）。"""
    h = _make_mock_handler("/analyze?coin=BTC&type=multi_source&q=test")
    h.do_GET()
    body = h.wfile.getvalue().decode("utf-8")
    assert "/theme?to=" in body
    assert "rtok=" in body
    assert "&amp;theme=light" not in body
    assert "?theme=light" not in body


def test_render_cache_get_missing_or_expired_returns_none():
    """查無 token／已過期一律回 `None`；呼叫端據此只是不還原內容，
    不會 fallback 成重新呼叫 pipeline。"""
    assert web._render_cache_get(None) is None
    assert web._render_cache_get("not-a-real-token") is None


# ---------------------------------------------------------------------------
# RUN STATS 誠實命名 + 可對帳（MEDIUM 修正）
# ---------------------------------------------------------------------------

def test_run_stats_uses_honest_flagged_label_not_fake_dropped():
    """flagged 證據仍顯示在報告裡（帶🚩徽章），不是真的被 drop 掉，
    命名只能叫「Flagged」，不能叫「Flagged dropped」（假語意，CLAUDE #24）。"""
    evidence = [
        Evidence(
            source="a", fetched_at="2026-01-01T00:00:00Z",
            content_reference="ref-a", related_claim="c-a", trust=0.8, flags=[],
        ),
        Evidence(
            source="b", fetched_at="2026-01-01T00:00:00Z",
            content_reference="ref-b", related_claim="c-b", trust=0.8,
            flags=["manipulation_keyword"],
        ),
    ]
    out = web._render_run_stats(evidence)
    assert "Flagged</span>" in out
    assert "Flagged dropped" not in out


def test_run_stats_scanned_reconciles_with_passed_flagged_below_threshold():
    """Sources scanned 必須等於 passed + flagged + below-threshold，三者
    加總不能對不上——不能有落在門檻之間、既沒被判 passed 也沒被判
    flagged 的來源憑空從統計裡消失（MEDIUM 修正：可對帳）。"""
    evidence = [
        Evidence(
            source="high", fetched_at="2026-01-01T00:00:00Z",
            content_reference="r1", related_claim="c1", trust=0.9, flags=[],
        ),
        Evidence(
            source="flagged", fetched_at="2026-01-01T00:00:00Z",
            content_reference="r2", related_claim="c2", trust=0.9,
            flags=["x"],
        ),
        Evidence(
            source="low", fetched_at="2026-01-01T00:00:00Z",
            content_reference="r3", related_claim="c3", trust=0.1, flags=[],
        ),
    ]
    out = web._render_run_stats(evidence)
    assert '<span class="tf-stat-k">Sources scanned</span><span class="tf-stat-v">3</span>' in out
    assert '<span class="tf-stat-k">Passed filter</span><span class="tf-stat-v">1</span>' in out
    assert '<span class="tf-stat-k">Flagged</span><span class="tf-stat-v">1</span>' in out
    assert '<span class="tf-stat-k">Below threshold</span><span class="tf-stat-v">1</span>' in out


# ---------------------------------------------------------------------------
# light 主題全卡片一致（MEDIUM 修正：header/gauge 卡/信任分析卡不再寫死深色）
# ---------------------------------------------------------------------------

def test_light_theme_header_gradient_uses_css_vars_not_hardcoded_dark():
    """header 背景漸層先前寫死 `#12171e`/`#0f141a`，light 模式下仍然一片深色
    ——改用 `var(--tf-hdr-g1)`/`var(--tf-hdr-g2)`，light token 才會真的生效。"""
    htmlout = web.render_page("")
    assert "background:linear-gradient(var(--tf-hdr-g1),var(--tf-hdr-g2))" in htmlout
    assert "background:linear-gradient(#12171e,#0f141a)" not in htmlout


def test_confidence_gauge_wrap_uses_css_var_not_hardcoded_dark():
    """信心 gauge 卡（`.tf-conf-wrap`）先前背景寫死 `#0f141a`，light 模式下
    仍是深色卡片——改用 `var(--tf-inset)`。"""
    htmlout = web.render_page("")
    assert ".tf-conf-wrap{background:var(--tf-inset)" in htmlout
    assert ".tf-conf-wrap{background:#0f141a" not in htmlout


def test_trust_breakdown_card_uses_css_var_not_hardcoded_dark():
    """信任分析卡（`_render_trust_breakdown` 內層卡片）先前背景寫死
    `#0f141a`，light 模式下仍是深色——改用 `var(--tf-inset)`。"""
    out = web._render_trust_breakdown(
        {"reputation": 0.8, "corroboration": 0.7, "recency": 0.6, "manipulation": 0.0},
        0.75,
    )
    assert "background:var(--tf-inset)" in out
    assert "background:#0f141a" not in out
