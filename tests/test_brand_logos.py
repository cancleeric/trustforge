"""品牌 LOGO 白名單（`trustforge.brand_logos`）單元測試——商業級視覺
（Nansen/Messari 級）需求：每個幣別 + 每個獨立來源放原廠 LOGO。

涵蓋：
1. COIN_POOL 五幣皆有真官方 inline SVG（`coin_logo_html`）。
2. simple-icons 有收錄的來源品牌（reddit/blockchain-info）走真官方 SVG，
   且 `coingecko-*`/`reddit-<subreddit>` 這種帶後綴的連接器 name 能正確
   正規化成同一個品牌。
3. simple-icons **沒有**收錄的來源（coindesk/decrypt/cryptopanic/sec-gov/
   alternative-me-fng/coingecko/ohlcv-csv 等）一律 fallback 成中性字首
   徽章，不放錯 LOGO（#24 鐵律）。
4. 白名單查無對應（未知幣種/來源）優雅降級，不炸、不留破圖標記。
5. CSP 相容性：本模組輸出只含 `<svg>`/`<span>`，不含 `<img`、外部 URL、
   `data:` URI（`web.py` CSP header 因此不需要、也沒有被放寬）。
6. `web.py`/`fetch_scheduler.py` 端到端整合：分析頁幣種標題、evidence
   來源 pill、首頁多幣總覽卡都真的嵌了 LOGO；且 CSP header 逐字不變。
"""

import re

import pytest

from trustforge import web
from trustforge.brand_logos import (
    COIN_LOGO_SVG,
    SOURCE_LOGO_SVG,
    coin_logo_html,
    source_display_name,
    source_logo_html,
)
from trustforge.schema import COIN_POOL, Evidence


# ---------------------------------------------------------------------------
# 1. 幣別 LOGO
# ---------------------------------------------------------------------------

def test_coin_pool_all_five_have_real_logo():
    """COIN_POOL（BTC/ETH/SOL/BNB/XRP）五幣皆有 inline SVG，非 fallback。"""
    for coin in COIN_POOL:
        assert coin in COIN_LOGO_SVG, f"{coin} 缺官方 LOGO"
        out = coin_logo_html(coin)
        assert out.startswith("<svg"), f"{coin} 應為真 <svg>，不是 fallback"
        assert "<img" not in out
        # `xmlns="http://www.w3.org/2000/svg"` 是 SVG 命名空間宣告
        # （瀏覽器不會拿去發請求），不算外部資源引用；真正要擋的是
        # `src="http...`/`href="http...` 這種會觸發載入的屬性。
        assert 'src="http' not in out and "src='http" not in out
        assert 'href="http' not in out and "href='http" not in out
        assert "data:" not in out


def test_coin_logo_unknown_coin_returns_empty_not_broken_markup():
    """不在白名單的幣種（非 COIN_POOL）回空字串，不印破圖/猜測的 LOGO。"""
    assert coin_logo_html("DOGE") == ""
    assert coin_logo_html("") == ""


def test_coin_logo_svg_titles_match_brand_name():
    """每個幣的 <title> 唸出正確品牌名（螢幕閱讀器 alt 文字對應），不會唸錯。"""
    expected_title = {
        "BTC": "Bitcoin",
        "ETH": "Ethereum",
        "SOL": "Solana",
        "BNB": "Binance",
        "XRP": "XRP",
    }
    for coin, title in expected_title.items():
        assert f"<title>{title}</title>" in coin_logo_html(coin)


# ---------------------------------------------------------------------------
# 2. 來源 LOGO —— simple-icons 有收錄的品牌
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source_name",
    ["reddit-cryptocurrency", "reddit-bitcoin", "reddit"],
)
def test_reddit_variants_normalize_to_same_real_logo(source_name):
    """`reddit-<subreddit>`（每個 subreddit 各自的連接器 name）都正規化到
    同一個 Reddit 官方 LOGO，不因為 subreddit 不同而查無或印錯。"""
    out = source_logo_html(source_name)
    assert out.startswith("<svg")
    assert "<title>Reddit</title>" in out


def test_blockchain_info_maps_to_blockchaindotcom_official_logo():
    """`blockchain-info`（ingestion/onchain.py 連接器 name）對應到
    simple-icons 收錄的同一家公司品牌 "Blockchain.com"。"""
    out = source_logo_html("blockchain-info")
    assert out.startswith("<svg")
    assert "<title>Blockchain.com</title>" in out


@pytest.mark.parametrize(
    "source_name",
    ["coingecko-price", "coingecko-sentiment", "coingecko-dev"],
)
def test_coingecko_variants_normalize_to_same_brand_key(source_name):
    """三個 CoinGecko 連接器（price/sentiment/dev）共用同一個品牌 key——
    simple-icons 沒收錄 CoinGecko，三者應得到同一個 fallback 徽章，而非
    各自產生不一致的結果。"""
    out = source_logo_html(source_name)
    assert out == source_logo_html("coingecko-price")


# ---------------------------------------------------------------------------
# 3. simple-icons 沒收錄的來源 → fallback 徽章，不放錯 LOGO
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source_name,expected_abbr",
    [
        ("coindesk", "CD"),
        ("decrypt", "DE"),
        ("cryptopanic", "CP"),
        ("sec-gov", "SEC"),
        ("alternative-me-fng", "F&amp;G"),  # `&` 經 html.escape 變 `&amp;`
        ("coingecko-price", "CG"),
        ("ohlcv-csv", "HB"),
        # 資料密度第一批（#24，docs/archive/plans/PLAN-data-density.md）新增 6 家新聞
        # RSS，simple-icons 逐一查證無收錄，一律 fallback 徽章。
        ("cointelegraph", "CT"),
        ("bitcoinmagazine", "BM"),
        ("cryptoslate", "CS"),
        ("bitcoinist", "BI"),
        ("newsbtc", "NB"),
        ("dailyhodl", "DH"),
        # 資料密度第二批（#24，docs/archive/plans/PLAN-data-density.md）新增 3 家新聞
        # RSS + 3 個鏈上來源，simple-icons 逐一查證無收錄，一律 fallback 徽章。
        ("theblock", "TB"),
        ("utoday", "UT"),
        ("blockworks", "BW"),
        ("mempool-space-fees", "MP"),
        ("mempool-space-difficulty", "MP"),
        ("blockchair", "BC"),
    ],
)
def test_sources_without_simple_icons_entry_use_fallback_badge(
    source_name, expected_abbr
):
    """simple-icons 查無條目（已逐一 grep slugs.md 確認）的來源一律走中性
    2-3 字縮寫徽章，不是 <svg> 官方 LOGO——避免放錯/瞎猜的品牌識別。"""
    out = source_logo_html(source_name, fallback_color="#3fb950")
    assert "<svg" not in out, f"{source_name} 不應誤植真 LOGO（simple-icons 未收錄）"
    assert 'class="tf-brand-fallback"' in out
    assert f">{expected_abbr}<" in out
    assert "#3fb950" in out, "fallback 顏色應沿用呼叫端傳入的 tier 顏色"


def test_fallback_badge_never_contains_img_or_external_url_or_data_uri():
    """fallback 徽章本身也要符合 CSP 相容規則：無 <img>、無外部 URL、無 data:。"""
    out = source_logo_html("totally-unknown-source")
    assert "<img" not in out
    assert "http://" not in out and "https://" not in out
    assert "data:" not in out


def test_unknown_source_falls_back_gracefully_not_broken():
    """完全未在白名單出現過的來源字串也不炸、不印出使用者可控字串本身。"""
    out = source_logo_html("some-random-unrecognized-source-xyz")
    assert 'class="tf-brand-fallback"' in out


# ---------------------------------------------------------------------------
# 4. simple-icons 條目正確性（防止路徑/hex 手抄打錯）
# ---------------------------------------------------------------------------

def test_source_logo_dict_only_contains_verified_brands():
    """`SOURCE_LOGO_SVG` 白名單目前只應收錄 reddit / blockchain-info 兩個
    真的在 simple-icons 有條目的品牌——新增品牌需同步補測試，避免有人
    誤把猜測的 SVG 塞進白名單而沒被發現。"""
    assert set(SOURCE_LOGO_SVG.keys()) == {"reddit", "blockchain-info"}


# ---------------------------------------------------------------------------
# 5. web.py 整合：分析頁幣種標題 + evidence 來源 pill
# ---------------------------------------------------------------------------

def _make_evidence(source: str, kind: str = "news") -> Evidence:
    return Evidence(
        source=source,
        fetched_at="2026-07-01T00:00:00Z",
        content_reference="ref",
        related_claim="claim",
        source_url="https://example.com/a",
        kind=kind,
        trust=0.8,
    )


def test_render_evidence_list_embeds_source_logo_or_fallback():
    """`_render_evidence_list` 產出的 <tr> 內，來源 pill 旁真的嵌了
    LOGO/徽章（reddit 走真 SVG，sec-gov 走 fallback），而不是裸 source 字串。"""
    evidence = [
        _make_evidence("reddit-cryptocurrency", kind="social"),
        _make_evidence("sec-gov", kind="regulatory"),
    ]
    out = web._render_evidence_list(evidence)
    assert "<title>Reddit</title>" in out
    assert 'class="tf-brand-fallback"' in out


def test_render_report_coin_badge_has_coin_logo(monkeypatch):
    """分析結果頁（`_render_report`）的 `tf-coin-badge` 幣種標題旁含官方 LOGO。"""
    from trustforge.ingestion.base import Document

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return [
            Document(id="d1", kind="price", source="fake-ohlcv", text=f"{coin} price"),
            Document(id="d2", kind="news", source="fake-news", text=f"{coin} news"),
        ]

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report, evidence, _log = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["分析BTC"]},
        client_ip="",
    )
    out = web._render_report(report, evidence)
    assert '<span class="tf-coin-badge">' in out
    # BTC 的 <title>Bitcoin</title> 應緊跟在 tf-coin-badge 開頭附近出現
    badge_pos = out.index('<span class="tf-coin-badge">')
    assert "<title>Bitcoin</title>" in out[badge_pos:badge_pos + 400]


# ---------------------------------------------------------------------------
# 6. CSP header 逐字不變（inline SVG 不需要、也沒有放寬 CSP）
# ---------------------------------------------------------------------------

_EXPECTED_CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com"
)


def test_csp_header_unchanged_after_inline_logo_render():
    """商業級視覺（inline SVG LOGO）落地後，`Handler._send` 送出的 CSP
    header 必須逐字跟原本一致——inline SVG 是 HTML 標記，不需要、也不能
    放寬 `default-src 'none'` 才能顯示。"""
    from io import BytesIO

    buf = BytesIO()
    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.wfile = buf

    captured_headers: list[tuple] = []
    h.send_response = lambda code: None
    h.send_header = lambda name, val: captured_headers.append((name, val))
    h.end_headers = lambda: None

    h._send(200, "<html>ok</html>")

    csp_values = [val for name, val in captured_headers if name == "Content-Security-Policy"]
    assert csp_values == [_EXPECTED_CSP]


def test_home_overview_html_svg_has_no_external_resource_refs(monkeypatch):
    """首頁多幣總覽卡（`_render_home_page` 讀取的 overview blob）就算含
    LOGO，也不會引入外部請求／`<img>`／`data:`——本測試直接檢查
    `scripts/fetch_scheduler.py::_render_overview_html` 的輸出。"""
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "scripts"))
    import fetch_scheduler  # noqa: E402

    snapshots = [
        {
            "coin": "BTC",
            "trust_score": 0.8,
            "direction": "看多",
            "calibrated_confidence": 0.7,
            "decision_state": "high_confidence",
            "generated_at": "2026-07-01T00:00:00Z",
        }
    ]
    out = fetch_scheduler._render_overview_html(snapshots)
    assert "<svg" in out
    assert "<title>Bitcoin</title>" in out
    assert "<img" not in out
    assert re.search(r'(src|href)\s*=\s*["\']https?://', out) is None
    assert "data:" not in out


# ---------------------------------------------------------------------------
# 7. source_display_name() —— 12 slug 品牌化顯示名 + 無 slug 洩漏
#    （docs/archive/plans/PLAN-source-branding.md：老闆真 Chrome 看到 `coingecko-sentiment`/
#    `ohlcv-csv` 這種工程師代號的直接修法）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "slug,expected_display",
    [
        ("coingecko-price", "CoinGecko · 即時報價"),
        ("coingecko-sentiment", "CoinGecko · 社群情緒"),
        ("coingecko-dev", "CoinGecko · 開發活動"),
        ("reddit-cryptocurrency", "Reddit · r/CryptoCurrency"),
        ("reddit-bitcoin", "Reddit · r/Bitcoin"),
        ("coindesk", "CoinDesk"),
        ("decrypt", "Decrypt"),
        ("cryptopanic", "CryptoPanic"),
        ("alternative-me-fng", "Alternative.me · 恐懼貪婪指數"),
        ("blockchain-info", "Blockchain.com"),
        ("sec-gov", "美國 SEC"),
        ("ohlcv-csv", "HOYA BIT · 官方 OHLCV"),
        # 資料密度第一批（#24，docs/archive/plans/PLAN-data-density.md）新增 6 家新聞 RSS。
        ("cointelegraph", "CoinTelegraph"),
        ("bitcoinmagazine", "Bitcoin Magazine"),
        ("cryptoslate", "CryptoSlate"),
        ("bitcoinist", "Bitcoinist"),
        ("newsbtc", "NewsBTC"),
        ("dailyhodl", "The Daily Hodl"),
        # 資料密度第二批（#24，docs/archive/plans/PLAN-data-density.md）新增 3 家新聞
        # RSS + 3 個鏈上來源。
        ("theblock", "The Block"),
        ("utoday", "U.Today"),
        ("blockworks", "Blockworks"),
        ("mempool-space-fees", "mempool.space · 建議手續費"),
        ("mempool-space-difficulty", "mempool.space · 難度調整進度"),
        ("blockchair", "Blockchair · BTC 鏈上統計"),
    ],
)
def test_source_display_name_covers_all_known_slugs(slug, expected_display):
    """gray plan 逐 slug 對照表：目前全部 24 個真連接器 slug（原 12 個 +
    資料密度第一批新增 6 家新聞 RSS + 第二批新增 6 個源）都要有明確、不
    裸露內部代號的品牌顯示名。同一品牌不同資料面向（3 個 coingecko-*、
    2 個 reddit-*、2 個 mempool-space-*）要顯示不同文字，不能因為共用
    同一顆 LOGO icon 就把文字也併成同一句。"""
    assert source_display_name(slug) == expected_display
    # 顯示名本身不應該就是裸 slug（防呆：白名單填錯成原樣時要能抓到）
    assert source_display_name(slug) != slug


def test_source_display_name_unknown_slug_gracefully_title_cased():
    """未來新連接器（如尚未實裝的 whale-alert）查無白名單時，優雅降級成
    title case，不留原始裸 slug 的痕跡（連字號被拿掉，不是原樣印出）。"""
    out = source_display_name("whale-alert")
    assert out == "Whale Alert"
    assert out != "whale-alert"
    assert "-" not in out


def test_source_display_name_empty_string_does_not_crash():
    """空字串輸入（理論上不會發生，防禦性測試）不炸、給明確中性文字。"""
    assert source_display_name("") == "未知來源"


def test_evidence_pill_uses_display_name_not_raw_slug():
    """`_render_evidence_list` 的 evidence pill 文字必須是品牌顯示名，
    絕不能出現原始裸 slug（web.py:1809 曾經直接印 `ev.source` 的根因回歸
    鎖，見 docs/archive/plans/PLAN-source-branding.md）。"""
    evidence = [
        _make_evidence("coingecko-sentiment", kind="social"),
        _make_evidence("ohlcv-csv", kind="price"),
        _make_evidence("reddit-bitcoin", kind="social"),
    ]
    out = web._render_evidence_list(evidence)
    assert "CoinGecko · 社群情緒" in out
    assert "HOYA BIT · 官方 OHLCV" in out
    assert "Reddit · r/Bitcoin" in out
    # 原始裸 slug 不應該以「顯示文字」的身分出現在 pill 裡
    assert ">coingecko-sentiment<" not in out
    assert ">ohlcv-csv<" not in out
    assert ">reddit-bitcoin<" not in out


def test_pipeline_missing_source_message_uses_display_name_not_raw_slug(monkeypatch):
    """report.limits 的「以下來源本輪未取得資料」清單同樣要用品牌顯示名，
    不得印裸 slug（pipeline.py 的 `_failed` 收集邏輯，同一個根因的另一個
    呈現點，見 docs/archive/plans/PLAN-source-branding.md）。"""
    from trustforge.ingestion.base import Document
    from trustforge.pipeline import run
    from trustforge.schema import QuestionType

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        if _failed is not None:
            _failed.append("coingecko-sentiment")
        return [Document(id="d1", kind="price", source="ohlcv-csv", text=f"{coin} price")]

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)
    report, _evidence, _log = run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE, offline=True
    )
    limits_text = " ".join(report.limits)
    assert "CoinGecko · 社群情緒" in limits_text
    assert "coingecko-sentiment" not in limits_text
