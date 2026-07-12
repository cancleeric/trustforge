"""生產限流常數鎖（issue #110，security 相關技術債）。

這些數字直接決定真實部署的「成本護欄」與「DoS 抗性」，誤改（尤其放寬）會
讓限流失效或被多實例部署放大，屬於安全相關常數：

- `web.py` 的 per-IP 限流常數：live（保護真 Bedrock 花費，刻意設緊）、
  real-off（真資料·$0 預設檔位，只擋洪水級 DoS）、/status 觀測端點、
  online-stance（真 Bedrock stance 呼叫）、以及 IP bucket 上限（防限流
  機制本身被 IPv6/偽造來源 IP 高頻換位變成記憶體耗盡向量）。
- `budget_guard.py` 的生產預設 cap：`DEFAULT_BEDROCK_DAILY_USD_CAP`
  （每日 Bedrock 花費硬上限）、`DEFAULT_REQUEST_MAX_USD`（單次請求成本
  安全下界，避免異常配置讓單次呼叫 bypass 每日 cap）。

這支測試把它們「鎖」在當前生產值——任何 careless 改動（例如把 live 從
5 次/60s 放寬成 500）都會讓 CI 紅。要調整這些數字必須經 CISO（harper）
雙審，並同步更新這裡的斷言與對應的安全設計說明。
"""
from __future__ import annotations

import pytest

from trustforge import budget_guard
from trustforge import web


def test_live_rate_limit_production_constants():
    # live = 真 Bedrock 路徑，刻意設最緊（保護花費）
    assert web._RATE_WINDOW == 60
    assert web._RATE_MAX == 5


def test_real_rate_limit_production_constants():
    # real-off = 真資料·$0 預設檔位，免費、只擋洪水級 DoS，門檻遠寬
    assert web._REAL_RATE_WINDOW == 60
    assert web._REAL_RATE_MAX == 60


def test_status_rate_limit_production_constants():
    # /status 唯讀觀測端點，門檻可更寬鬆
    assert web._STATUS_RATE_WINDOW == 30
    assert web._STATUS_RATE_MAX == 10


def test_online_stance_rate_limit_production_constants():
    # online-stance 間接燒 Bedrock stance 呼叫，獨立一組更緊的門檻
    assert web._ONLINE_STANCE_RATE_WINDOW == 3600
    assert web._ONLINE_STANCE_RATE_MAX == 20


def test_rate_limit_max_tracked_ips_constant():
    # IP bucket 數量硬上限：防限流機制本身被高頻換源 IP 變成記憶體耗盡向量
    assert web._RATE_LIMIT_MAX_TRACKED_IPS == 5000


def test_budget_guard_production_caps():
    # 每日 Bedrock 花費硬上限（USD）
    assert budget_guard.DEFAULT_BEDROCK_DAILY_USD_CAP == 3.0
    # 單次請求成本安全下界（USD），避免異常配置 bypass 每日 cap
    assert budget_guard.DEFAULT_REQUEST_MAX_USD == 0.05


def test_live_is_strictly_tighter_than_real_off():
    # 不變量：保護真花費的 live 緊限流，必須比免費的 real-off 緊——
    # 若有人把兩者調成一致，live 的「花費護欄」語意就破了。
    assert web._RATE_MAX < web._REAL_RATE_MAX
    assert web._RATE_WINDOW == web._REAL_RATE_WINDOW
