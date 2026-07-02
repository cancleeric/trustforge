"""階段1：解耦 offline/Bedrock —— 三檔模式測試。

背景：舊版 `offline` 單一 bool 同時控制「樣本 vs 真連接器」與「Bedrock 開關」，
耦合點在 `pipeline.run()` 建構 `BedrockClient(offline=offline)`。本測試驗證新增
`data_mode`（live|sample）/ `llm_mode`（off|bedrock）兩個獨立旗標後：

  1. 向後相容：只傳 `offline` 時，行為與改動前完全一致。
  2. 新增的第三檔「真資料·$0」（data_mode=live, llm_mode=off）：
     走真連接器（collect(offline=False)），但 Bedrock 呼叫完全關閉 → 成本恆為 $0。
  3. web.py `?real=1` 開關正確路由到第三檔，且不依賴 HAS_BEDROCK / token。

⛔ 全程 monkeypatch `collect`（比照既有測試慣例，如 test_comparison.py），
   禁止真打任何連接器 / AWS Bedrock。
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import parse_qs, quote, urlparse

import pytest

from trustforge import pipeline as pl
from trustforge import web
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType


def _doc(id, kind, source, text, ts=1_000.0):
    return Document(id=id, kind=kind, source=source, text=text, ts=ts)


def _make_real_docs(coin: str):
    """模擬「真連接器」回傳的文件（非 OfflineSampleSource 樣本）。"""
    return [
        _doc(f"{coin}_p1", "price", "real-hoya-ohlcv", f"{coin} 今日收盤 30000 美元，漲幅 1.2%。"),
        _doc(f"{coin}_n1", "news", "real-coindesk-rss", f"{coin} 本週分析師偏多。"),
    ]


def _cost_events(log):
    return [ev["params"] for ev in log.events if ev.get("tool") == "llm.cost"]


def _total_cost(log):
    return sum(float(p.get("cost_usd", 0.0) or 0.0) for p in _cost_events(log))


# ---------------------------------------------------------------------------
# 1. pipeline._resolve_modes：推導與驗證
# ---------------------------------------------------------------------------

def test_resolve_modes_offline_true_derives_sample_off():
    data_mode, llm_mode = pl._resolve_modes(True, None, None)
    assert (data_mode, llm_mode) == ("sample", "off")


def test_resolve_modes_offline_false_derives_live_bedrock():
    data_mode, llm_mode = pl._resolve_modes(False, None, None)
    assert (data_mode, llm_mode) == ("live", "bedrock")


def test_resolve_modes_explicit_overrides_offline():
    """明確傳 data_mode/llm_mode 時，offline 的推導完全被覆寫——這是解耦的核心。"""
    data_mode, llm_mode = pl._resolve_modes(True, "live", "off")
    assert (data_mode, llm_mode) == ("live", "off")


def test_resolve_modes_invalid_data_mode_raises():
    with pytest.raises(ValueError, match="data_mode"):
        pl._resolve_modes(False, "bogus", None)


def test_resolve_modes_invalid_llm_mode_raises():
    with pytest.raises(ValueError, match="llm_mode"):
        pl._resolve_modes(False, None, "bogus")


# ---------------------------------------------------------------------------
# 2. pipeline.run 向後相容：只用 offline 時行為不變
# ---------------------------------------------------------------------------

def test_run_offline_true_still_uses_sample_and_free_llm(monkeypatch):
    """舊呼叫方式 offline=True：collect(offline=True) + Bedrock 關閉，行為不變。"""
    seen = {}

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        seen["offline"] = offline
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report, evidence, log = pl.run("BTC", "分析 BTC", QuestionType.MULTI_SOURCE, offline=True)
    assert seen["offline"] is True
    assert _total_cost(log) == 0.0
    assert report.coin == "BTC"


def test_run_offline_false_still_uses_live_collect(monkeypatch):
    """舊呼叫方式 offline=False：collect(offline=False)（真連接器路徑），Bedrock 開放
    （即 client.offline=False；未設 BEDROCK_MODEL_ID 時 complete() 內部失敗會被
    orchestrator 既有的 try/except 降級吸收，不中斷管線——此為既有行為，非本次改動範圍，
    這裡只驗證 collect() 收到的 offline 旗標與改動前一致）。
    """
    seen = {}

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        seen["offline"] = offline
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report, evidence, log = pl.run("BTC", "分析 BTC", QuestionType.MULTI_SOURCE, offline=False)
    assert seen["offline"] is False
    assert report.coin == "BTC"


# ---------------------------------------------------------------------------
# 3. 核心新增：data_mode=live + llm_mode=off ＝ 真資料·$0
# ---------------------------------------------------------------------------

def test_run_live_data_off_llm_is_credit_safe(monkeypatch):
    """data_mode=live + llm_mode=off：collect 走真連接器（offline=False），
    但 Bedrock 完全關閉 → cost_ledger 全 $0，且 evidence 來源非樣本。
    """
    seen = {}

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        seen["offline"] = offline
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE,
        data_mode="live", llm_mode="off",
    )

    # 3a. collect() 收到 offline=False → 走真連接器分支，不是 OfflineSampleSource 樣本路徑
    assert seen["offline"] is False, "data_mode=live 應以 offline=False 呼叫 collect()"

    # 3b. 沒有任何真 Bedrock 花費
    assert _total_cost(log) == 0.0, "llm_mode=off 必須保證成本恆為 $0"
    cost_events = _cost_events(log)
    assert cost_events, "Step3 仍應記一筆 $0 的 llm.cost（離線佔位），只是金額為 0"
    assert all((p.get("model") or "offline") == "offline" for p in cost_events), (
        "llm_mode=off 時不應出現任何真實 model id 的成本事件"
    )

    # 3c. evidence 來源沿用真連接器 mock 的來源名稱（非樣本資料）
    sources = {ev.source for ev in evidence}
    assert sources & {"real-hoya-ohlcv", "real-coindesk-rss"}, (
        f"evidence 應來自真連接器 mock 來源，實際 {sources}"
    )
    assert report.coin == "BTC"


def test_run_live_data_off_llm_credit_safe_even_with_model_id_configured(monkeypatch):
    """加碼驗證：即使環境變數把 BEDROCK_MODEL_ID/HAIKU 都設成非空值，
    llm_mode=off 仍必須保證 $0（offline 旗標對 BedrockClient 是唯一真值來源，
    不會因為 model_id 有設定就漏放行任何真呼叫）。
    """
    monkeypatch.setenv("BEDROCK_MODEL_ID", "")
    monkeypatch.setenv("BEDROCK_HAIKU_MODEL_ID", "")

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report, evidence, log = pl.run(
        "ETH", "分析 ETH", QuestionType.MULTI_SOURCE,
        data_mode="live", llm_mode="off",
    )
    assert _total_cost(log) == 0.0


def test_run_comparison_live_data_off_llm_credit_safe(monkeypatch):
    """run_comparison 同步支援 data_mode/llm_mode：兩幣皆走真連接器 + 免 Bedrock。"""
    seen_offline = []

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        seen_offline.append(offline)
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report_a, ev_a, report_b, ev_b, log = pl.run_comparison(
        "BTC", "ETH", "比較", data_mode="live", llm_mode="off",
    )
    assert seen_offline == [False, False]
    assert _total_cost(log) == 0.0
    assert report_a.coin == "BTC"
    assert report_b.coin == "ETH"


# ---------------------------------------------------------------------------
# 4. web.py：?real=1 路由到「真資料·$0」檔，不依賴 HAS_BEDROCK / token
# ---------------------------------------------------------------------------

def test_web_real_mode_routes_to_live_data_off_llm(monkeypatch):
    """?real=1：即使 HAS_BEDROCK=False、無 token，也能走 data_mode=live/llm_mode=off。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    captured = {}

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        captured["data_mode"] = data_mode
        captured["llm_mode"] = llm_mode
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)  # 強制真正執行時用離線樣本，避免真打連接器

    monkeypatch.setattr(web, "run", fake_run)

    report, evidence, log = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "real": ["1"]}
    )
    assert captured == {"data_mode": "live", "llm_mode": "off"}
    assert report.coin == "BTC"


def test_web_real_mode_not_rate_limited_by_live_bucket_conflict(monkeypatch):
    """real=1 生效時仍會走 per-IP 限流（避免真連接器被打爆），超量應拋 TooManyRequests。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._rate_buckets.clear()

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    ip = "10.0.0.42"
    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "real": ["1"]}
    for _ in range(web._RATE_MAX):
        web._do_analyze(qs, client_ip=ip)
    with pytest.raises(web.TooManyRequests):
        web._do_analyze(qs, client_ip=ip)


def test_web_real_mode_without_flag_is_new_default(monkeypatch):
    """世界第一重寫 Phase 2：不帶 real/live/sample 參數 → 落在新預設「真資料·$0」
    （data_mode=live, llm_mode=off），不再是離線樣本檔——這正是本輪重寫的
    核心行為變更（見 DEV-PLAN-REWRITE.md Task 1），不是回歸。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    captured = {}

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        captured["offline"] = offline
        captured["data_mode"] = data_mode
        captured["llm_mode"] = llm_mode
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)  # 強制真正執行時用離線樣本，避免真打連接器

    monkeypatch.setattr(web, "run", fake_run)

    report, evidence, log = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    assert captured == {"offline": False, "data_mode": "live", "llm_mode": "off"}


def test_web_sample_flag_still_reaches_offline_sample_path(monkeypatch):
    """`?sample=1`：離線示範沙盒 opt-in，行為與舊版「無參數預設」完全一致
    （只是現在要顯式帶 `sample=1` 才觸發，向後相容既有離線樣本語意）。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    captured = {}

    def fake_run(coin, query, qtype, offline=False, data_dir=None):
        captured["offline"] = offline
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    report, evidence, log = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "sample": ["1"]}
    )
    assert captured == {"offline": True}


def test_web_live_takes_priority_over_real(monkeypatch):
    """live=1 帶正確 token 時優先於 real=1（同時給兩者 → 走真 Bedrock 檔，非真資料·$0 檔）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "secret")
    web._rate_buckets.clear()

    captured = {}

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        captured["offline"] = offline
        captured["data_mode"] = data_mode
        captured["llm_mode"] = llm_mode
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)  # 強制離線避免真打 Bedrock

    monkeypatch.setattr(web, "run", fake_run)

    report, evidence, log = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"],
         "live": ["1"], "token": ["secret"], "real": ["1"]},
        client_ip="1.2.3.4",
    )
    # live 優先：走 offline=not live（=False），而非 real 分支的 data_mode/llm_mode
    assert captured["offline"] is False
    assert captured["data_mode"] is None
    assert captured["llm_mode"] is None


def test_web_do_analyze_real_mode_increments_service_counter(monkeypatch):
    """成本會計階段3：real=1（真連接器路徑）要記 1 次「真服務」呼叫，供
    `/status`「快取節省」估算用。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._analyze_service_count = 0

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    before = web._get_analyze_service_count()
    web._do_analyze({"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "real": ["1"]})
    assert web._get_analyze_service_count() == before + 1
    web._analyze_service_count = 0


def test_web_do_analyze_sample_flag_does_not_increment_service_counter(monkeypatch):
    """`?sample=1`（離線示範，樣本資料，未觸碰任何連接器/cache）不該計入
    「真服務」次數，否則快取節省估算會被離線 demo 流量污染。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._analyze_service_count = 0

    def fake_run(coin, query, qtype, offline=False, data_dir=None):
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    web._do_analyze({"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "sample": ["1"]})
    assert web._get_analyze_service_count() == 0


def test_web_do_analyze_default_mode_increments_service_counter(monkeypatch):
    """世界第一重寫 Phase 2：不帶任何 mode 參數 → 落在新預設「真資料·$0」，
    要計入「真服務」次數（跟顯式 `?real=1` 行為一致，因為現在就是同一檔位）。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._analyze_service_count = 0

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    before = web._get_analyze_service_count()
    web._do_analyze({"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]})
    assert web._get_analyze_service_count() == before + 1
    web._analyze_service_count = 0


def test_web_do_comparison_real_mode(monkeypatch):
    """_do_comparison 同步支援 ?real=1。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    captured = {}

    def fake_run_comparison(coin_a, coin_b, query, offline=False, data_dir=None,
                            data_mode=None, llm_mode=None):
        captured["data_mode"] = data_mode
        captured["llm_mode"] = llm_mode
        import trustforge.pipeline as _pl
        return _pl.run_comparison(coin_a, coin_b, query, offline=True)

    monkeypatch.setattr(web, "run_comparison", fake_run_comparison)

    result = web._do_comparison(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["比較 BTC 與 ETH"],
         "real": ["1"]},
    )
    assert captured == {"data_mode": "live", "llm_mode": "off"}
    assert len(result) == 5


def test_web_do_comparison_real_mode_increments_service_counter_by_two(monkeypatch):
    """comparison 一次分析兩個幣種，各自都要讀一輪多來源資料，記 2 次。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._analyze_service_count = 0

    def fake_run_comparison(coin_a, coin_b, query, offline=False, data_dir=None,
                            data_mode=None, llm_mode=None):
        import trustforge.pipeline as _pl
        return _pl.run_comparison(coin_a, coin_b, query, offline=True)

    monkeypatch.setattr(web, "run_comparison", fake_run_comparison)

    before = web._get_analyze_service_count()
    web._do_comparison(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["比較 BTC 與 ETH"], "real": ["1"]},
    )
    assert web._get_analyze_service_count() == before + 2
    web._analyze_service_count = 0


# ---------------------------------------------------------------------------
# 5. render_page：頂欄徽章清楚顯示三檔
# ---------------------------------------------------------------------------

def test_render_page_shows_three_mode_badges_bedrock_unset(monkeypatch):
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    htmlout = web.render_page("")
    assert "離線示範" in htmlout
    assert "真資料" in htmlout and "?real=1" in htmlout
    assert "真 Bedrock" in htmlout


def test_render_page_shows_three_mode_badges_bedrock_set(monkeypatch):
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    htmlout = web.render_page("")
    assert "離線示範" in htmlout
    assert "真資料" in htmlout and "?real=1" in htmlout
    assert "真 Bedrock" in htmlout
    assert "?live=1" in htmlout


# ---------------------------------------------------------------------------
# 6. MEDIUM 修復：JSON 下載連結（及任何自我連結）保留當前模式參數
#
# 背景：real/live 模式的報告頁若沒把模式參數帶進 /analyze.json 下載連結，
# 點下載會落回預設 offline/sample 分支，匯出的 report/evidence 跟畫面看到的
# 不一致，破壞溯源/可重現性（見 web.py:717-720 附近的 codex 覆核意見）。
# ---------------------------------------------------------------------------

def _extract_json_link(html_out: str) -> str:
    """從渲染出的 HTML 抓出 `/analyze.json` 下載連結，回傳「可直接拿去當
    URL/path 使用」的字串（已 `html.unescape`）。

    HIGH 根治修復揭露：`_analyze_json_href()` 現在對整條 href 做 `html.escape`
    （包含把參數間的分隔字元 `&` 轉成 HTML entity `&amp;`）——這是正確、安全的
    HTML 屬性寫法，但代表**從 HTML 原始碼裡截出來的字串本身不是一個合法的
    query string**，必須先 `html.unescape` 解回真正的 `&` 等字元，才能拿去
    `urlparse`/`parse_qs`，或當成 path 丟給 `Handler.do_GET`——這正是真實瀏覽器
    點擊連結時做的事（先解 HTML entity，才拿到實際要 navigate 的 URL）。
    先前版本因為 href 只逐段做值層 `html.escape`、分隔符 `&` 從未跳脫，這一步
    「巧合地」不需要也能動，但那正是這幾輪 codex 抓出的根因所在。
    """
    m = re.search(r'href="(/analyze\.json\?[^"]*)"', html_out)
    assert m, f"找不到 /analyze.json 下載連結，HTML 片段：{html_out[:500]}"
    return html.unescape(m.group(1))


def test_mode_link_suffix_real_is_default_no_suffix_needed():
    """世界第一重寫 Phase 2：real 現在是預設檔位，顯式 `?real=1` 等同預設，
    自我連結不需要再帶 `real=1` 才能保留模式（少一個參數，畫面更乾淨）。"""
    assert web._mode_link_suffix({"real": ["1"]}) == ""


def test_mode_link_suffix_sample():
    """`?sample=1`（離線示範沙盒 opt-in）自我連結須保留 `sample=1`，否則
    重新請求會落回新預設「真資料·$0」，跟畫面顯示的離線樣本資料不一致。"""
    assert web._mode_link_suffix({"sample": ["1"]}) == "&sample=1"


def test_mode_link_suffix_live(monkeypatch):
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "secret")
    suffix = web._mode_link_suffix({"live": ["1"], "token": ["secret"]})
    assert suffix == "&live=1&token=secret"


def test_mode_link_suffix_default_empty():
    assert web._mode_link_suffix({"coin": ["BTC"]}) == ""


def test_mode_link_suffix_live_priority_over_real(monkeypatch):
    """兩者同時給 → live 優先，suffix 只帶 live（不重複帶 real），與 _parse_real 邏輯一致。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "secret")
    suffix = web._mode_link_suffix({"live": ["1"], "token": ["secret"], "real": ["1"]})
    assert suffix == "&live=1&token=secret"


@pytest.mark.parametrize("token", ["a&b=1", "x+y", "50%off", "id#1", "a=b&c=d"])
def test_mode_link_suffix_url_encodes_special_char_token_survives_round_trip(
    monkeypatch, token
):
    """MEDIUM 修復：token 含 query string 保留字（& + = % #）時，舊版只
    `html.escape(token)`，未做 URL 編碼——href 尾端會出現未逸出的 `&`/`=`，
    瀏覽器依 query string 語法解析會在第一個保留字處把 token 截斷，
    `_active_mode`/`_parse_live` 比對不到正確 token，靜默落回 offline，
    破壞「畫面顯示的模式＝實際資料來源」的 provenance 保證。

    驗證方式：完整跑一次「URL 編碼 → 嵌進 href → 用 parse_qs 解碼」（模擬瀏覽器
    點擊該連結後，伺服器端 `do_GET` 用 `parse_qs(urlparse(path).query)` 解析的
    行為），斷言解出來的 qs 餵給 `_active_mode` 仍正確判定為 `"live"`
    （而不是靜默退化成 `"offline"`）。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", token)

    suffix = web._mode_link_suffix({"live": ["1"], "token": [token]})
    href = f"/analyze.json?coin=BTC&type=multi_source&q=test{suffix}"

    # 模擬瀏覽器點擊 href 後，伺服器端 do_GET 用 parse_qs 解析出來的 qs
    decoded_qs = parse_qs(urlparse(href).query)
    assert decoded_qs.get("token", [""])[0] == token, (
        f"token 經 href 往返後應原樣還原，實際 {decoded_qs.get('token')!r}，"
        f"href={href!r}"
    )
    assert web._active_mode(decoded_qs) == "live", (
        f"含特殊字元 token 往返後應仍判定為 live，實際落回 "
        f"{web._active_mode(decoded_qs)!r}，href={href!r}"
    )


def test_mode_link_suffix_has_no_rate_limit_side_effect(monkeypatch):
    """_mode_link_suffix 是純函式：呼叫多次不得消耗/建立任何限流 bucket
    （do_GET 會在 _do_analyze 內部已判斷一次 real/live 之後，再呼叫本函式重算
    一次同樣的結果字串——若這裡也觸發限流，同一次請求會被錯誤地扣兩次額度）。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    web._rate_buckets.clear()
    qs = {"real": ["1"]}
    for _ in range(50):
        web._mode_link_suffix(qs)
    assert web._rate_buckets == {}, "_mode_link_suffix 不應消耗/建立任何限流 bucket"


def test_do_analyze_real_mode_json_link_round_trips_without_needing_real_param(monkeypatch):
    """世界第一重寫 Phase 2：real 現在是預設檔位，下載連結**不需要**帶
    `real=1`（`_mode_extra_params` 對預設檔位回傳 `{}`）——點擊後重新請求
    （沒有任何 mode 參數）仍會落在同一個預設檔位（data_mode=live/llm_mode=off），
    輸出與畫面一致（$0、來源仍是真連接器）。少一個參數，畫面更乾淨，且不影響
    可重現性（因為現在「無參數」本身就是唯一、確定的真資料·$0 檔位）。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "real": ["1"]}
    report, evidence, log = web._do_analyze(qs, client_ip="")
    mode_extra = web._mode_extra_params(qs)
    html_out = web._render_report(report, evidence, log, mode_extra=mode_extra)

    link = _extract_json_link(html_out)
    assert "real=1" not in link, f"real 是預設檔位，下載連結不應多帶 real=1：{link}"

    # 模擬使用者點擊該下載連結 → 重新請求（不帶任何 mode 參數），斷言仍以
    # data_mode=live/llm_mode=off 執行（因為這正是新預設）
    link_qs = parse_qs(urlparse(link).query)
    report2, evidence2, log2 = web._do_analyze(link_qs, client_ip="")

    assert _total_cost(log2) == 0.0, "下載連結重新請求後仍必須是 $0（real 模式一致）"
    sources2 = {ev.source for ev in evidence2}
    assert sources2 & {"real-hoya-ohlcv", "real-coindesk-rss"}, (
        f"下載連結重新請求後應仍走真連接器資料，不可落回離線樣本，實際來源 {sources2}"
    )
    assert report2.coin == report.coin
    assert report2.question_type == report.question_type


def test_do_analyze_live_mode_json_link_preserves_live_and_token(monkeypatch):
    """live 模式報告的下載連結須帶 live=1&token=<token>（token 語意照舊）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "secret")
    web._rate_buckets.clear()

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)  # 強制離線，避免真打 Bedrock

    monkeypatch.setattr(web, "run", fake_run)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"],
          "live": ["1"], "token": ["secret"]}
    report, evidence, log = web._do_analyze(qs, client_ip="5.6.7.8")
    mode_extra = web._mode_extra_params(qs)
    html_out = web._render_report(report, evidence, log, mode_extra=mode_extra)

    link = _extract_json_link(html_out)
    assert "live=1" in link, f"live 模式的下載連結未帶 live=1：{link}"
    assert "token=secret" in link, f"live 模式的下載連結未帶 token：{link}"


def test_do_analyze_default_mode_json_link_has_no_mode_param(monkeypatch):
    """離線示範（預設，未帶 real/live）：下載連結不應出現任何模式參數（向後相容）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    report, evidence, log = web._do_analyze(qs, client_ip="")
    mode_extra = web._mode_extra_params(qs)
    html_out = web._render_report(report, evidence, log, mode_extra=mode_extra)

    link = _extract_json_link(html_out)
    assert "real=1" not in link
    assert "live=1" not in link


def test_do_comparison_real_mode_json_link_has_both_coins_no_extra_mode_param(monkeypatch):
    """comparison + real 模式：唯一一條 top-level 下載連結須同時含雙幣。

    HIGH 修復揭露（既有測試語意變更）：舊版本測試名為
    `test_do_comparison_real_mode_nested_json_links_preserve_real_param`，斷言「內嵌
    兩份單幣詳細分析的下載連結都帶 real=1」——但那兩條內嵌連結各自只用單一
    `report.coin`（如 "BTC"）配 `type=comparison` 建，實際上點下去會因缺第二個幣種
    讓 `_parse_comparison_coins` 400（見 codex HIGH 覆核意見，web.py 舊 727 行附近）。
    舊測試只驗字串含 "real=1"，從未實際跟隨連結，因此完全沒抓到這個壞連結。
    修復後 comparison 頁面只有**一條** top-level 連結（`coin=A,B&type=comparison`），
    內嵌的兩份單幣詳細分析改用 `show_json_link=False` 不再各自產生壞連結。

    世界第一重寫 Phase 2：real 現在是預設檔位，`real=1` 不再需要（也不應該）
    出現在自我連結——本測試改為斷言恰好一條連結、含兩個幣種、且**不**多帶
    任何模式參數（見 `test_mode_link_suffix_real_is_default_no_suffix_needed`）。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    qs = {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["比較 BTC 與 ETH"], "real": ["1"]}
    report_a, evidence_a, report_b, evidence_b, log = web._do_comparison(qs, client_ip="")
    mode_extra = web._mode_extra_params(qs)
    html_out = web._render_comparison(
        report_a, evidence_a, report_b, evidence_b, "比較 BTC 與 ETH", log,
        mode_extra=mode_extra,
    )

    links = [
        html.unescape(m) for m in re.findall(r'href="(/analyze\.json\?[^"]*)"', html_out)
    ]
    assert len(links) == 1, (
        f"comparison 頁面應恰好只有一條 top-level /analyze.json 連結（內嵌單幣連結"
        f"應已關閉），實際找到 {len(links)} 條：{links}"
    )
    link = links[0]
    assert "real=1" not in link, f"real 是預設檔位，comparison 下載連結不應多帶 real=1：{link}"
    link_qs = parse_qs(urlparse(link).query)
    coins_param = link_qs.get("coin", [""])[0]
    assert set(coins_param.split(",")) == {"BTC", "ETH"}, (
        f"comparison 下載連結遺失幣種，coin 參數應同時含 BTC 與 ETH，實際：{coins_param!r}"
    )


def test_do_comparison_json_link_actually_follows_to_200_with_both_coins_pair_only_in_coin_param(
    monkeypatch,
):
    """HIGH e2e：coin 配對只能從 `coin=BTC,ETH` 參數取得（q 文字裡沒有幣名），
    實際跟隨 comparison 頁面產生的下載連結，斷言真的回 200 且兩幣皆在。

    這是 codex 覆核明確要求的驗收方式：不能只靠字串/regex 斷言連結「看起來」正確，
    必須真的照瀏覽器點擊的路徑跑一次 `Handler.do_GET`（見 `_run_do_get`），
    因為修復前的壞連結（單幣 + type=comparison）字串上看不出問題，只有實際
    request 才會在 `_parse_comparison_coins` 炸出 400。查詢文字刻意用不含任何
    COIN_POOL 幣名的句子，確保回應對的是 `coin` 參數本身被正確帶了兩個幣，而不是
    `_do_comparison` 從 q 文字裡「意外」解析出兩個幣僥倖過關。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._rate_buckets.clear()

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    query_no_coin_names = "這兩個資產最近誰比較強？"
    path = (
        "/analyze?coin=BTC,ETH&type=comparison"
        f"&q={quote(query_no_coin_names)}&real=1"
    )
    first = _run_do_get(path)
    assert first["code"] == 200, f"comparison 頁面首次請求應回 200，實際 {first}"

    link = _extract_json_link(first["body"])
    link_qs = parse_qs(urlparse(link).query)
    assert set(link_qs.get("coin", [""])[0].split(",")) == {"BTC", "ETH"}, (
        f"下載連結遺失幣種：{link}"
    )

    # 真的跟隨連結（模擬使用者點擊下載）
    followed = _run_do_get(link)
    assert followed["code"] == 200, (
        f"跟隨 comparison 下載連結應回 200（修復前因單幣配 type=comparison 回 400），"
        f"實際 {followed}"
    )
    payload = json.loads(followed["body"])
    assert payload["report_a"]["coin"] in {"BTC", "ETH"}
    assert payload["report_b"]["coin"] in {"BTC", "ETH"}
    assert {payload["report_a"]["coin"], payload["report_b"]["coin"]} == {"BTC", "ETH"}, (
        f"跟隨下載連結後應同時看到 BTC 與 ETH 兩份報告，實際 {payload['report_a']['coin']!r}/"
        f"{payload['report_b']['coin']!r}"
    )


_SPECIAL_CHAR_QUERY = 'a&b+c#d%e"f 中文字元查詢'


def test_do_analyze_json_link_round_trips_special_char_query_and_follows_to_200(monkeypatch):
    """HIGH 根治 e2e（單幣）：q 含 `& + # % "` 與非 ASCII 中文，產生下載連結後
    `urlparse`+`parse_qs` 解碼出的 q/coin/mode 須與原始逐字相同，且實際跟隨
    連結必須回 200（而非因參數被誤判分隔符而 400 或解出錯誤內容）。

    根因回顧：舊版 coin/type/q 逐段只 `html.escape`、從未 percent-encode，
    q 含這些保留字或非 ASCII 字元時會在 query string 語法層被誤判成分隔符。
    現在改用 `_analyze_json_href()`（coin/type/q/mode 一次 `urlencode`，整段
    再 `html.escape`），本測試驗證這個雙層編碼組合的正確性與可逆性。

    世界第一重寫 Phase 2：real 現在是預設檔位，`real=1` 不再出現在自我連結
    （見 `test_mode_link_suffix_real_is_default_no_suffix_needed`）——請求本身
    仍可顯式帶 `real=1`（向後相容），但產生的下載連結不會多帶這個參數。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._rate_buckets.clear()

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    path = (
        "/analyze?coin=BTC&type=multi_source"
        f"&q={quote(_SPECIAL_CHAR_QUERY)}&real=1"
    )
    first = _run_do_get(path)
    assert first["code"] == 200, f"首次請求應回 200，實際 {first}"

    link = _extract_json_link(first["body"])
    link_qs = parse_qs(urlparse(link).query)
    assert link_qs.get("q", [""])[0] == _SPECIAL_CHAR_QUERY, (
        f"下載連結解碼出的 q 應與原始 q 逐字相同，實際 {link_qs.get('q')!r}"
    )
    assert link_qs.get("coin", [""])[0] == "BTC"
    assert "real" not in link_qs, (
        f"real 是預設檔位，下載連結不應多帶 real 參數，實際 {link_qs!r}"
    )

    followed = _run_do_get(link)
    assert followed["code"] == 200, (
        f"跟隨下載連結應回 200（修復前特殊字元 q 會讓參數被誤判分隔符），實際 {followed}"
    )
    payload = json.loads(followed["body"])
    assert payload["report"]["coin"] == "BTC"
    assert payload["report"]["question"] == _SPECIAL_CHAR_QUERY, (
        f"跟隨下載連結後解析出的 report.question 應與原始 q 逐字相同，"
        f"實際 {payload['report']['question']!r}"
    )


def test_do_comparison_json_link_round_trips_special_char_query_and_follows_to_200(
    monkeypatch,
):
    """HIGH 根治 e2e（比較）：同上一測試，但走 comparison 路徑（q 同樣含
    `& + # % "` 與非 ASCII 中文；coin 配對走 `coin=BTC,ETH` 參數，與 q 內容
    互不干擾）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._rate_buckets.clear()

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    path = (
        "/analyze?coin=BTC,ETH&type=comparison"
        f"&q={quote(_SPECIAL_CHAR_QUERY)}&real=1"
    )
    first = _run_do_get(path)
    assert first["code"] == 200, f"首次請求應回 200，實際 {first}"

    link = _extract_json_link(first["body"])
    link_qs = parse_qs(urlparse(link).query)
    assert link_qs.get("q", [""])[0] == _SPECIAL_CHAR_QUERY, (
        f"下載連結解碼出的 q 應與原始 q 逐字相同，實際 {link_qs.get('q')!r}"
    )
    assert set(link_qs.get("coin", [""])[0].split(",")) == {"BTC", "ETH"}
    assert "real" not in link_qs, (
        f"real 是預設檔位，comparison 下載連結不應多帶 real 參數，實際 {link_qs!r}"
    )

    followed = _run_do_get(link)
    assert followed["code"] == 200, (
        f"跟隨下載連結應回 200（修復前特殊字元 q 會讓參數被誤判分隔符），實際 {followed}"
    )
    payload = json.loads(followed["body"])
    assert {payload["report_a"]["coin"], payload["report_b"]["coin"]} == {"BTC", "ETH"}
    assert payload["report_a"]["question"] == _SPECIAL_CHAR_QUERY
    assert payload["report_b"]["question"] == _SPECIAL_CHAR_QUERY


# ---------------------------------------------------------------------------
# 7. MEDIUM 修復：頂欄三檔徽章同時顯 active → 使用者分不清畫面資料來源
#    （provenance 誤導）。本次請求實際生效的模式須傳進 render_page，且畫面
#    永遠恰好一個徽章帶 active，其餘兩檔為灰色靜態能力標籤。
# ---------------------------------------------------------------------------

_ACTIVE_BADGE_RE = re.compile(r'class="tf-mode-badge ([a-z-]+) active"')


def test_active_mode_no_params_request_shows_only_real_active(monkeypatch):
    """世界第一重寫 Phase 2：未帶任何 mode 參數的一般請求 → _active_mode 判為
    real（新預設「真資料·$0」），畫面恰好真資料徽章 active（不再是離線示範）。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    report, evidence, log = web._do_analyze(qs, client_ip="")
    active_mode = web._active_mode(qs)
    assert active_mode == "real"

    html_out = web.render_page(web._render_report(report, evidence, log), active_mode=active_mode)
    actives = _ACTIVE_BADGE_RE.findall(html_out)
    assert actives == ["tf-real"], f"未帶參數的請求應恰好 tf-real active，實際：{actives}"


def test_active_mode_sample_request_shows_only_offline_active(monkeypatch):
    """`?sample=1`：_active_mode 判為 offline，畫面恰好離線示範徽章 active
    （離線示範沙盒 opt-in，見 `_is_sample_request`）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    def fake_run(coin, query, qtype, offline=False, data_dir=None):
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "sample": ["1"]}
    report, evidence, log = web._do_analyze(qs, client_ip="")
    active_mode = web._active_mode(qs)
    assert active_mode == "offline"

    html_out = web.render_page(web._render_report(report, evidence, log), active_mode=active_mode)
    actives = _ACTIVE_BADGE_RE.findall(html_out)
    assert actives == ["tf-offline"], f"?sample=1 請求應恰好 tf-offline active，實際：{actives}"


def test_active_mode_real_request_shows_only_real_active(monkeypatch):
    """?real=1：_active_mode 判為 real，畫面恰好真資料徽章 active（不依賴 HAS_BEDROCK）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "real": ["1"]}
    report, evidence, log = web._do_analyze(qs, client_ip="")
    active_mode = web._active_mode(qs)
    assert active_mode == "real"

    html_out = web.render_page(web._render_report(report, evidence, log), active_mode=active_mode)
    actives = _ACTIVE_BADGE_RE.findall(html_out)
    assert actives == ["tf-real"], f"?real=1 請求應恰好 tf-real active，實際：{actives}"


def test_active_mode_live_request_shows_only_live_active(monkeypatch):
    """?live=1&token=<正確 token>：_active_mode 判為 live，畫面恰好真 Bedrock 徽章 active。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "secret")
    web._rate_buckets.clear()

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)  # 強制離線避免真打 Bedrock

    monkeypatch.setattr(web, "run", fake_run)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"],
          "live": ["1"], "token": ["secret"]}
    report, evidence, log = web._do_analyze(qs, client_ip="9.9.9.9")
    active_mode = web._active_mode(qs)
    assert active_mode == "live"

    html_out = web.render_page(web._render_report(report, evidence, log), active_mode=active_mode)
    actives = _ACTIVE_BADGE_RE.findall(html_out)
    assert actives == ["tf-live"], f"?live=1 請求應恰好 tf-live active，實際：{actives}"
    assert "LIVE" in html_out


@pytest.mark.parametrize(
    "qs,has_bedrock,live_token,expected",
    [
        # 世界第一重寫 Phase 2：未帶任何 mode 參數 → 新預設「真資料·$0」（real）
        ({"coin": ["BTC"], "type": ["multi_source"], "q": ["t"]}, False, "", "real"),
        ({"coin": ["BTC"], "type": ["multi_source"], "q": ["t"], "real": ["1"]}, False, "", "real"),
        (
            {"coin": ["BTC"], "type": ["multi_source"], "q": ["t"],
             "live": ["1"], "token": ["secret"]},
            True, "secret", "live",
        ),
        # live 沒帶正確 token → 不算 live，落回新預設 real（非 offline，未帶 sample 參數）
        (
            {"coin": ["BTC"], "type": ["multi_source"], "q": ["t"], "live": ["1"]},
            True, "secret", "real",
        ),
        # `?sample=1`：離線示範沙盒 opt-in
        (
            {"coin": ["BTC"], "type": ["multi_source"], "q": ["t"], "sample": ["1"]},
            False, "", "offline",
        ),
    ],
)
def test_active_mode_matches_exactly_one_badge_across_requests(
    monkeypatch, qs, has_bedrock, live_token, expected
):
    """跨多種請求形態，_active_mode 判斷結果與 render_page 畫面恰好一個 active 徽章一致。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", has_bedrock)
    monkeypatch.setattr(web, "LIVE_TOKEN", live_token)

    active_mode = web._active_mode(qs)
    assert active_mode == expected

    html_out = web.render_page("", active_mode=active_mode)
    actives = _ACTIVE_BADGE_RE.findall(html_out)
    assert len(actives) == 1, f"{qs} 應恰好 1 個 active 徽章，實際：{actives}"


# ---------------------------------------------------------------------------
# 8. MEDIUM 修復（追加）：429/400/502 錯誤頁也要帶 active_mode，不能落回預設
#    offline——否則 real/live 模式的請求一旦失敗，錯誤頁反而誤標成離線示範，
#    一樣是 provenance 誤導。active_mode 須在執行分析「之前」就算好，success
#    與 error 分支共用同一個值。
# ---------------------------------------------------------------------------


def _run_do_get(path: str, client_ip: str = "1.2.3.4") -> dict:
    """呼叫 `Handler.do_GET` 但不建立真實 socket。

    `BaseHTTPRequestHandler.do_GET` 的邏輯只讀 `self.path` / `self.client_address`，
    並透過 `self._send(code, body, ctype)` 輸出——用 `__new__` 跳過
    socketserver 的連線初始化，並以 instance attribute 覆寫 `_send` 攔截輸出，
    不需要真的開 socket／起 HTTP server。
    """
    h = web.Handler.__new__(web.Handler)
    h.path = path
    h.client_address = (client_ip, 55555)
    captured: dict = {}

    def fake_send(code, body, ctype="text/html; charset=utf-8", extra_headers=None):
        captured["code"] = code
        captured["body"] = body
        captured["ctype"] = ctype
        captured["extra_headers"] = extra_headers

    h._send = fake_send
    h.do_GET()
    return captured


def test_error_400_real_mode_keeps_real_active_badge(monkeypatch):
    """?real=1 但幣種非法 → 400，錯誤頁仍應標 tf-real active（不落回 offline）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._rate_buckets.clear()

    result = _run_do_get(
        "/analyze?coin=NOPE&type=multi_source&q=t&real=1", client_ip="10.0.0.1"
    )

    assert result["code"] == 400
    actives = _ACTIVE_BADGE_RE.findall(result["body"])
    assert actives == ["tf-real"], f"400 real=1 應恰好 tf-real active，實際：{actives}"


def test_error_429_live_mode_keeps_live_active_badge(monkeypatch):
    """?live=1&token=<正確 token> 但超過限流 → 429，錯誤頁仍應標 tf-live active（不落回 offline）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "secret")
    web._rate_buckets.clear()
    ip = "10.0.0.2"
    for _ in range(web._RATE_MAX):
        web._check_live_rate_limit(ip)  # 灌爆同一 IP 的限流桶

    result = _run_do_get(
        "/analyze?coin=BTC&type=multi_source&q=t&live=1&token=secret", client_ip=ip
    )

    assert result["code"] == 429
    actives = _ACTIVE_BADGE_RE.findall(result["body"])
    assert actives == ["tf-live"], f"429 live=1 應恰好 tf-live active，實際：{actives}"


def test_error_502_real_mode_keeps_real_active_badge(monkeypatch):
    """?real=1 時 pipeline 內部丟未預期例外 → 502，錯誤頁仍應標 tf-real active（不落回 offline）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._rate_buckets.clear()

    def boom(*a, **k):
        raise RuntimeError("connector 掛了（測試模擬，非真連接器）")

    monkeypatch.setattr(web, "run", boom)

    result = _run_do_get(
        "/analyze?coin=BTC&type=multi_source&q=t&real=1", client_ip="10.0.0.3"
    )

    assert result["code"] == 502
    actives = _ACTIVE_BADGE_RE.findall(result["body"])
    assert actives == ["tf-real"], f"502 real=1 應恰好 tf-real active，實際：{actives}"


def test_error_400_no_params_request_keeps_real_active_badge(monkeypatch):
    """世界第一重寫 Phase 2：一般請求（無 real/live/sample）幣種非法 → 400，
    錯誤頁應維持新預設 tf-real active（不落回舊版 offline）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._rate_buckets.clear()

    result = _run_do_get(
        "/analyze?coin=NOPE&type=multi_source&q=t", client_ip="10.0.0.4"
    )

    assert result["code"] == 400
    actives = _ACTIVE_BADGE_RE.findall(result["body"])
    assert actives == ["tf-real"], f"400 real 應恰好 tf-real active，實際：{actives}"


def test_error_400_sample_request_keeps_offline_active_badge(monkeypatch):
    """`?sample=1` 幣種非法 → 400，錯誤頁維持 tf-offline active（既有離線
    示範沙盒行為不回歸，只是現在要顯式帶 `sample=1`）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    web._rate_buckets.clear()

    result = _run_do_get(
        "/analyze?coin=NOPE&type=multi_source&q=t&sample=1", client_ip="10.0.0.5"
    )

    assert result["code"] == 400
    actives = _ACTIVE_BADGE_RE.findall(result["body"])
    assert actives == ["tf-offline"], f"400 sample=1 應恰好 tf-offline active，實際：{actives}"
