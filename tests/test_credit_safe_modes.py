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

import re
from urllib.parse import parse_qs, urlparse

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


def test_web_real_mode_without_flag_stays_offline_default(monkeypatch):
    """不帶 real/live 參數 → 仍是預設離線樣本檔，行為不變（向後相容基準線）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    captured = {}

    def fake_run(coin, query, qtype, offline=False, data_dir=None):
        captured["offline"] = offline
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    report, evidence, log = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
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
    m = re.search(r'href="(/analyze\.json\?[^"]*)"', html_out)
    assert m, f"找不到 /analyze.json 下載連結，HTML 片段：{html_out[:500]}"
    return m.group(1)


def test_mode_link_suffix_real():
    assert web._mode_link_suffix({"real": ["1"]}) == "&real=1"


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


def test_do_analyze_real_mode_json_link_preserves_real_param(monkeypatch):
    """real 模式報告的下載連結須帶 real=1；點擊後重新請求仍以
    data_mode=live/llm_mode=off 執行，輸出與畫面一致（$0、來源仍是真連接器）。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "real": ["1"]}
    report, evidence, log = web._do_analyze(qs, client_ip="")
    suffix = web._mode_link_suffix(qs)
    html_out = web._render_report(report, evidence, log, mode_suffix=suffix)

    link = _extract_json_link(html_out)
    assert "real=1" in link, f"real 模式的下載連結未帶 real=1：{link}"

    # 模擬使用者點擊該下載連結 → 重新請求，斷言仍以 data_mode=live/llm_mode=off 執行
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
    suffix = web._mode_link_suffix(qs)
    html_out = web._render_report(report, evidence, log, mode_suffix=suffix)

    link = _extract_json_link(html_out)
    assert "live=1" in link, f"live 模式的下載連結未帶 live=1：{link}"
    assert "token=secret" in link, f"live 模式的下載連結未帶 token：{link}"


def test_do_analyze_default_mode_json_link_has_no_mode_param(monkeypatch):
    """離線示範（預設，未帶 real/live）：下載連結不應出現任何模式參數（向後相容）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    report, evidence, log = web._do_analyze(qs, client_ip="")
    suffix = web._mode_link_suffix(qs)
    html_out = web._render_report(report, evidence, log, mode_suffix=suffix)

    link = _extract_json_link(html_out)
    assert "real=1" not in link
    assert "live=1" not in link


def test_do_comparison_real_mode_nested_json_links_preserve_real_param(monkeypatch):
    """comparison + real 模式：內嵌兩份單幣詳細分析的下載連結也都要帶 real=1。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_real_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    qs = {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["比較 BTC 與 ETH"], "real": ["1"]}
    report_a, evidence_a, report_b, evidence_b, log = web._do_comparison(qs, client_ip="")
    suffix = web._mode_link_suffix(qs)
    html_out = web._render_comparison(
        report_a, evidence_a, report_b, evidence_b, "比較 BTC 與 ETH", log,
        mode_suffix=suffix,
    )

    links = re.findall(r'href="(/analyze\.json\?[^"]*)"', html_out)
    assert links, "comparison 頁面應含至少一個 /analyze.json 連結"
    assert all("real=1" in link for link in links), (
        f"comparison 內嵌的 JSON 連結未全部帶 real=1：{links}"
    )
