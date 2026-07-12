"""PR8（issue #115，security）：請求入口 config 快照下傳，消除 live/real
跨快照時序不一致。

對外行為（live/real 分流結果）不變，只消除「同一次請求內多次重算
config 導致前半段判 live、後半段因 config 被外部改動判 real」的內部
不一致；極端下這會讓 live 結果落到 real-mode key 被 $0 命中（#115 核心
bug）。

收斂清單：
  (a) 同一請求內多次判斷 live/real 結果一致（`_is_live_request` /
      `_active_mode` / `_mode_extra_params` / `_analyze_effective_mode`）。
  (b) 極端：請求進行中 config 被外部改動（admin 輪替 live token /
      關 bedrock_enabled）不影響「本請求」判定（快照隔離）。
  (c) live 結果不會被誤歸到 real-mode key（回歸 #115 核心 bug）：
      請求中途把 live 關掉，本請求仍一致判定為 live，不會中途翻轉成
      real 讓 live 的真 Bedrock 結果落到 real/$0 key。
  (d) 快照不跨 thread 串味（thread-local 隔離）：A thread 的快照不會
      被 B thread 讀到。

全程不打真 AWS：monkeypatch `admin_config.get_config` 注入假 config。
"""
from __future__ import annotations

import threading

import pytest

from trustforge import admin_config, web
from trustforge.admin_config import (
    AdminConfig,
    hash_live_token,
    put_config,
)

CONFIG_TOKEN = "config-live-token-0123456789abcdef"
ENV_TOKEN = "env-live-token-fedcba9876543210"


def _mock_config(monkeypatch, config: AdminConfig) -> None:
    """讓 config 層讀到指定快照（get_config_cached 透過模組全域呼叫
    get_config，monkeypatch 後即生效）。"""
    monkeypatch.setattr(admin_config, "get_config", lambda *a, **k: config)


def _mock_config_cached(monkeypatch, config: AdminConfig) -> None:
    """繞過 15s TTL 快取，每次都讀到指定快照（模擬「config 被外部
    即時改動」後的讀取結果）。"""
    monkeypatch.setattr(
        admin_config, "get_config_cached", lambda *a, **k: config
    )


def _reset_caches():
    admin_config._reset_admin_config_cache_for_tests()
    web._reset_admin_cfg_fail_window_for_tests()


def _live_qs(token: str) -> dict:
    return {"live": ["1"], "token": [token]}


# ---------------------------------------------------------------------------
# (a) 同一請求內多次判斷 live/real 結果一致
# ---------------------------------------------------------------------------
def test_snapshot_consistent_within_request(monkeypatch):
    """請求快照內，四個讀 mode 的入口都回傳同一份 `live` 判定，
    不會因為各自重算 config 而分岔。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    _mock_config(
        monkeypatch,
        AdminConfig(
            bedrock_enabled=True,
            live_token_hash=hash_live_token(CONFIG_TOKEN),
            exists=True,
        ),
    )
    qs = _live_qs(CONFIG_TOKEN)
    with web.request_snapshot(qs):
        live = web._is_live_request(qs)
        assert live is True
        # 同一請求內稍後再判斷：必須與上面一致（不重算 config）
        assert web._is_live_request(qs) is True
        assert web._active_mode(qs) == "live"
        assert web._mode_extra_params(qs) == {"live": "1"}
        assert web._analyze_effective_mode(qs) == "live"
        # is_real_request / is_sample_request 也與 frozen live 一致
        assert web._is_real_request(qs, live) is False
        assert web._is_sample_request(qs, live) is False


# ---------------------------------------------------------------------------
# (b) 請求進行中 config 被外部改動不影響本請求判定（快照隔離）
# ---------------------------------------------------------------------------
def test_snapshot_isolated_from_mid_request_config_change(monkeypatch):
    """請求開始後，管理面把 live token 換掉、且關掉 bedrock——本請求
    仍堅持用自己的（請求入口凍結的）快照判定為 live，不會中途翻轉。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    # 請求入口當下生效的 config：認 CONFIG_TOKEN、bedrock 開
    old_cfg = AdminConfig(
        bedrock_enabled=True,
        live_token_hash=hash_live_token(CONFIG_TOKEN),
        exists=True,
    )
    _mock_config(monkeypatch, old_cfg)

    qs = _live_qs(CONFIG_TOKEN)
    with web.request_snapshot(qs):
        assert web._is_live_request(qs) is True
        # —— 管理面在「本請求進行中」把 config 整個換掉 ——
        # 新 token、且 bedrock 關閉；模擬外部即時改動（cache 也刷新）。
        new_cfg = AdminConfig(
            bedrock_enabled=False,
            live_token_hash=hash_live_token("rotated-token-now-in-effect"),
            exists=True,
        )
        _reset_caches()
        _mock_config_cached(monkeypatch, new_cfg)
        # 本請求仍應堅持「入口凍結的」live 判定，不受影響
        assert web._is_live_request(qs) is True
        assert web._active_mode(qs) == "live"
        assert web._analyze_effective_mode(qs) == "live"

    # 離開快照後，新的請求（無快照）應反映「外部改動後」的 config：
    # 新 token 未知 + bedrock 關 → live 不成立
    assert web._is_live_request(_live_qs(CONFIG_TOKEN)) is False
    assert web._is_live_request(_live_qs("rotated-token-now-in-effect")) is False


# ---------------------------------------------------------------------------
# (c) live 結果不會被誤歸到 real-mode key（回歸 #115 核心 bug）
# ---------------------------------------------------------------------------
def test_live_result_not_misrouted_to_real_key(monkeypatch):
    """#115 核心：請求前半段判 live（走真 Bedrock）、後半段若因 config
    中途翻轉判成 real，會讓本該是 live 的真結果落到 real/$0 key。
    快照凍結後，整個請求的 mode 判定一致為 live，不會中途翻轉成 real。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    live_cfg = AdminConfig(
        bedrock_enabled=True,
        live_token_hash=hash_live_token(CONFIG_TOKEN),
        exists=True,
    )
    _mock_config(monkeypatch, live_cfg)

    qs = _live_qs(CONFIG_TOKEN)
    with web.request_snapshot(qs):
        # 請求前半段：分流決定走 live（真 Bedrock）
        assert web._is_live_request(qs) is True  # 實際會觸發真 Bedrock
        # —— 管理面在此刻把 bedrock 關掉（模擬 #115 的極端翻轉）——
        offline_cfg = AdminConfig(bedrock_enabled=False, exists=True)
        _reset_caches()
        _mock_config_cached(monkeypatch, offline_cfg)
        # 請求後半段（渲染 active_mode、組自我連結、算 dedup key）仍必須
        # 與前半段一致判 live——否則同一份真 Bedrock 結果會被標成 real/$0
        assert web._is_live_request(qs) is True
        assert web._analyze_effective_mode(qs) == "live"
        assert web._mode_extra_params(qs) == {"live": "1"}
        dedup_live = web._analyze_dedup_key(
            qtype=__import__("trustforge.schema", fromlist=["QuestionType"]).QuestionType.MULTI_SOURCE,
            coin_key="BTC",
            query="分析該幣種",
            qs=qs,
        )
        assert '"live"' in dedup_live  # dedup key 也鎖定 live，不會變 real


# ---------------------------------------------------------------------------
# (d) 快照不跨 thread 串味（thread-local 隔離）
# ---------------------------------------------------------------------------
def test_snapshot_thread_local_isolation(monkeypatch):
    """A thread 凍結的請求快照，不會被 B thread 讀到（避免併發請求
    間 live/real 判定互相污染）。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    _mock_config(
        monkeypatch,
        AdminConfig(
            bedrock_enabled=True,
            live_token_hash=hash_live_token(CONFIG_TOKEN),
            exists=True,
        ),
    )
    qs = _live_qs(CONFIG_TOKEN)
    web._begin_request_snapshot(qs)
    try:
        assert web._is_live_request(qs) is True  # 當前 thread 看得到快照

        result = {}

        def other_thread():
            # 另一個 thread 不應讀到本 thread 的快照
            result["snap"] = web._current_request_snapshot()
            # 且直接呼叫會回退成「現讀 config」的舊行為（這裡與本
            # thread 的 config 設定相同，只是要證明它**不是**讀快照）
            result["live"] = web._is_live_request(qs)

        t = threading.Thread(target=other_thread)
        t.start()
        t.join()
        assert result["snap"] is None, "快照不應跨 thread 洩漏"
        assert result["live"] is True
    finally:
        web._end_request_snapshot()


# ---------------------------------------------------------------------------
# 對外行為不變：無快照上下文（如單元測試直接呼叫）回退現讀 config
# ---------------------------------------------------------------------------
def test_no_snapshot_falls_back_to_fresh_config(monkeypatch):
    """未經 `_begin_request_snapshot` 設定的上下文，行為與舊版逐字相同
    （每次現讀 config），確保既有直接呼叫的單元測試與對外行為不變。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    _mock_config(monkeypatch, AdminConfig())
    qs = _live_qs(ENV_TOKEN)
    assert web._current_request_snapshot() is None
    assert web._is_live_request(qs) is True
    assert web._active_mode(qs) == "live"
    # 切換 config 後，無快照的直接呼叫應立即反映新值（不凍結）
    _reset_caches()
    _mock_config_cached(monkeypatch, AdminConfig(bedrock_enabled=False))
    assert web._is_live_request(qs) is False
    assert web._active_mode(qs) == "real"
