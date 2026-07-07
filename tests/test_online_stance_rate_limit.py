"""#9 online-stance 預算配額硬化 — per-IP 限流（`web.py`）測試。

online-stance 限流是「degrade，不是拒絕」：超量時 `_online_stance_force_offline`
回傳 `True`（呼叫端據此把 `force_stance_offline` 傳給 `run()`/`run_comparison()`
誠實 degrade 回離線 stance），而不是像 `_check_live_rate_limit`/
`_check_real_rate_limit` 那樣 raise `TooManyRequests` → HTTP 429。

⛔ 全程 monkeypatch `web.run`/`web.run_comparison`（不打真連接器/AWS）。
"""
from __future__ import annotations

import pytest

from trustforge import web


def setup_function(_fn):
    """每個測試前清空所有限流 bucket，避免測試之間互相污染。"""
    web._rate_buckets.clear()
    web._real_rate_buckets.clear()
    web._online_stance_rate_buckets.clear()


# ---------------------------------------------------------------------------
# 1) `_check_online_stance_rate_limit`：獨立 bucket，超量 raise TooManyRequests
# ---------------------------------------------------------------------------

def test_check_online_stance_rate_limit_allows_up_to_max():
    ip = "10.10.0.1"
    for _ in range(web._ONLINE_STANCE_RATE_MAX):
        web._check_online_stance_rate_limit(ip)  # 不應 raise


def test_check_online_stance_rate_limit_raises_after_max():
    ip = "10.10.0.2"
    for _ in range(web._ONLINE_STANCE_RATE_MAX):
        web._check_online_stance_rate_limit(ip)
    with pytest.raises(web.TooManyRequests):
        web._check_online_stance_rate_limit(ip)


def test_check_online_stance_rate_limit_independent_per_ip():
    """不同 IP 各自獨立計數，互不影響。"""
    for _ in range(web._ONLINE_STANCE_RATE_MAX):
        web._check_online_stance_rate_limit("10.10.0.3")
    # 另一個 IP 完全沒受影響，仍可正常呼叫
    web._check_online_stance_rate_limit("10.10.0.4")


def test_check_online_stance_rate_limit_independent_bucket_from_real_and_live():
    """online-stance 的 bucket 跟既有 live/real 的 bucket 各自獨立，互不干擾。"""
    ip = "10.10.0.5"
    for _ in range(web._ONLINE_STANCE_RATE_MAX):
        web._check_online_stance_rate_limit(ip)
    assert web._rate_buckets == {}
    assert web._real_rate_buckets == {}


# ---------------------------------------------------------------------------
# 2) `_online_stance_force_offline`：switch 未開 → 永遠 False（零開銷）
# ---------------------------------------------------------------------------

def test_online_stance_force_offline_false_when_switch_off(monkeypatch):
    monkeypatch.setattr(web, "online_stance_requested", lambda: False)
    ip = "10.10.0.6"
    for _ in range(web._ONLINE_STANCE_RATE_MAX + 5):
        assert web._online_stance_force_offline(ip) is False
    # switch 關閉時完全不動用這組 bucket（零開銷、行為不變）。
    assert web._online_stance_rate_buckets == {}


def test_online_stance_force_offline_false_when_no_client_ip(monkeypatch):
    monkeypatch.setattr(web, "online_stance_requested", lambda: True)
    assert web._online_stance_force_offline("") is False
    assert web._online_stance_rate_buckets == {}


# ---------------------------------------------------------------------------
# 3) `_online_stance_force_offline`：switch 開啟 → 限流生效，超量回 True
#    （degrade，不 raise）
# ---------------------------------------------------------------------------

def test_online_stance_force_offline_degrades_not_raises_after_limit(monkeypatch):
    monkeypatch.setattr(web, "online_stance_requested", lambda: True)
    ip = "10.10.0.7"
    for _ in range(web._ONLINE_STANCE_RATE_MAX):
        assert web._online_stance_force_offline(ip) is False
    # 超量：回 True（degrade），不是 raise TooManyRequests。
    assert web._online_stance_force_offline(ip) is True


# ---------------------------------------------------------------------------
# 4) `_do_analyze`/`_do_comparison` 整合：預設（switch 關閉）完全不觸碰
#    online-stance bucket，對 `run()` 的呼叫方式逐字不變（現行測試全數覆蓋
#    的路徑）。
# ---------------------------------------------------------------------------

def test_do_analyze_default_switch_off_never_touches_online_stance_bucket(monkeypatch):
    # 測試套件效能優化：`_ONLINE_STANCE_RATE_MAX` 生產值是 20，這裡逐次真跑
    # （離線）pipeline，monkeypatch 成小很多的門檻，迴圈次數/斷言都改讀 patch
    # 後的值，驗證的仍是同一套邏輯，跟門檻實際數字無關。
    monkeypatch.setattr(web, "_ONLINE_STANCE_RATE_MAX", 3)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    monkeypatch.setattr(web, "online_stance_requested", lambda: False)

    captured = []

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        captured.append({"data_mode": data_mode, "llm_mode": llm_mode})
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    ip = "10.10.0.8"
    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "real": ["1"]}
    for _ in range(web._ONLINE_STANCE_RATE_MAX + 3):
        web._do_analyze(qs, client_ip=ip)

    assert web._online_stance_rate_buckets == {}
    assert len(captured) == web._ONLINE_STANCE_RATE_MAX + 3
    assert all(c == {"data_mode": "live", "llm_mode": "off"} for c in captured)


def test_do_analyze_switch_on_passes_force_stance_offline_only_after_limit(monkeypatch):
    """switch 開啟：前 `_ONLINE_STANCE_RATE_MAX` 次呼叫 `run()` 不帶
    `force_stance_offline`（未超量，行為跟預設相同）；第
    `_ONLINE_STANCE_RATE_MAX + 1` 次才帶 `force_stance_offline=True`
    （誠實 degrade，而非 429/報錯）。

    測試套件效能優化：`_ONLINE_STANCE_RATE_MAX` monkeypatch 成小很多的門檻，
    理由同上一測試。"""
    monkeypatch.setattr(web, "_ONLINE_STANCE_RATE_MAX", 3)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    monkeypatch.setattr(web, "online_stance_requested", lambda: True)

    captured = []

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None, **kwargs):
        captured.append(kwargs.get("force_stance_offline", False))
        import trustforge.pipeline as _pl
        return _pl.run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", fake_run)

    ip = "10.10.0.9"
    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "real": ["1"]}
    for _ in range(web._ONLINE_STANCE_RATE_MAX):
        web._do_analyze(qs, client_ip=ip)
    web._do_analyze(qs, client_ip=ip)  # 第 N+1 次：應 degrade

    assert captured[:-1] == [False] * web._ONLINE_STANCE_RATE_MAX
    assert captured[-1] is True
    # 沒有任何一次因為超量而 raise/回錯——`_do_analyze` 正常回傳，不拋例外。


def test_do_analyze_switch_on_returns_200_style_result_not_error_when_over_limit(monkeypatch):
    """超量時 `_do_analyze` 仍正常回傳 (report, evidence, log) 三元組，不 raise，
    對應 web handler 層應回 HTTP 200（誠實 degrade，不是報錯）。

    測試套件效能優化：`_ONLINE_STANCE_RATE_MAX` monkeypatch 成小很多的門檻，
    理由同上方測試。"""
    monkeypatch.setattr(web, "_ONLINE_STANCE_RATE_MAX", 3)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    monkeypatch.setattr(web, "online_stance_requested", lambda: True)

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None, **kwargs):
        import trustforge.pipeline as _pl
        return _pl.run(
            coin, query, qtype, offline=True,
            force_stance_offline=kwargs.get("force_stance_offline", False),
        )

    monkeypatch.setattr(web, "run", fake_run)

    ip = "10.10.0.10"
    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"], "real": ["1"]}
    for _ in range(web._ONLINE_STANCE_RATE_MAX):
        web._do_analyze(qs, client_ip=ip)
    report, evidence, log = web._do_analyze(qs, client_ip=ip)  # 第 N+1 次
    assert report.coin == "BTC"


def test_do_comparison_switch_on_passes_force_stance_offline_only_after_limit(monkeypatch):
    """`_do_comparison` 走同一個 online-stance 限流判定（同一個 client_ip
    key），驗證兩個入口共用同一組 bucket、行為一致。

    測試套件效能優化：`_ONLINE_STANCE_RATE_MAX` monkeypatch 成小很多的門檻，
    理由同上方測試。"""
    monkeypatch.setattr(web, "_ONLINE_STANCE_RATE_MAX", 3)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    monkeypatch.setattr(web, "online_stance_requested", lambda: True)

    captured = []

    def fake_run_comparison(coin_a, coin_b, query, offline=False, data_dir=None,
                             data_mode=None, llm_mode=None, **kwargs):
        captured.append(kwargs.get("force_stance_offline", False))
        import trustforge.pipeline as _pl
        return _pl.run_comparison(coin_a, coin_b, query, offline=True)

    monkeypatch.setattr(web, "run_comparison", fake_run_comparison)

    ip = "10.10.0.11"
    qs = {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["test"], "real": ["1"]}
    for _ in range(web._ONLINE_STANCE_RATE_MAX):
        web._do_comparison(qs, client_ip=ip)
    web._do_comparison(qs, client_ip=ip)  # 第 N+1 次：應 degrade

    assert captured[:-1] == [False] * web._ONLINE_STANCE_RATE_MAX
    assert captured[-1] is True
