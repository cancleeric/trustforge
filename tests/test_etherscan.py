"""#1168 Etherscan V2 連接器整合測試 — CI 不打真網路（monkeypatch etherscan._fetch_url
與 etherscan.resolve_api_key）。

涵蓋：
  - V2 URL（chainid=1 + module=account + action=txlist + page=1 + offset=20
    + sort=desc + apikey=；page/offset 修1 限縮回應大小避免截斷）。
  - whale tx parse：from/to/value/hash/timeStamp → Document（kind=whale_onchain）。
  - value 大額過濾（< 10**17 wei 跳過）。
  - 方向分類（修2 誠實降級：hex 地址無 exchange label → 一律 whale_transfer 中性）。
  - 跨地址同 tx 去重（修3：by tx hash）。
  - Document.url 乾淨（https://etherscan.io/tx/{hash}，不含 apikey）。
  - 非 ETH 幣回 []（不打網路）。
  - HTTPError（429/5xx）→ raise sanitized RuntimeError（訊息只用 status code/
    reason，絕不含 URL/apikey；`from None` 斷 chain 防 traceback 外洩）—codex P1。
  - Etherscan API error（HTTP 200 + status!="1"）→ raise sanitized（訊息固定
    字串，不帶 response 細節）—codex P1。status=="1" + 空 list → []（正常）。
  - 節流器（_throttle_before_request / reset_throttle）。
  - 無 key → unconfigured 靜默降級；unavailable → raise。
  - build_etherscan_sources 永遠註冊（同 cmc/whale 慣例）。
"""

from __future__ import annotations

import json
import time

import pytest
from urllib.error import HTTPError

from trustforge.ingestion import etherscan


# ── 固定 fixture（模擬 Etherscan V2 txlist 回應）──────────────────────────────

# 一筆大額（5 ETH = 5e18 wei）轉帳。
# 一筆小額（0.01 ETH = 1e16 wei < 10**17）→ 應被過濾掉。
# 注意：地址一律用合法 hex（0x+40hex），不造假交易所關鍵字（修2：hex 無 exchange
# label，方向恆中性，舊版 "0x...binance" 假地址測試已移除）。
_TXLIST_FIXTURE = json.dumps(
    {
        "status": "1",
        "message": "OK",
        "result": [
            {  # 5 ETH 大額轉帳（兩端皆合法 hex → 方向中性 whale_transfer）
                "from": "0x" + "a" * 40,
                "to": "0x" + "b" * 40,
                "value": str(5 * 10**18),
                "hash": "0xdeadbeef0001",
                "timeStamp": "1700000000",
                "gas": "21000",
                "gasPrice": "20000000000",
            },
            {  # 0.01 ETH（1e16 wei）< 10**17 → 過濾掉
                "from": "0x" + "c" * 40,
                "to": "0x" + "d" * 40,
                "value": str(10**16),
                "hash": "0xdeadbeef0002",
                "timeStamp": "1700000001",
                "gas": "21000",
                "gasPrice": "20000000000",
            },
        ],
    }
).encode()


def _patch_key(monkeypatch, key="controlled-etherscan-key-1234567890"):
    """讓 connector 認為有 key（不打真 SSM/env）。"""
    monkeypatch.setattr(
        "trustforge.ingestion.etherscan.resolve_api_key",
        lambda: (key, "ssm"),
    )


# ── A. V2 URL + 基本 parse ─────────────────────────────────────────────────────


def test_fetch_uses_v2_txlist_url_with_chainid_and_apikey(monkeypatch):
    """URL 是 V2 endpoint，含 chainid=1、module=account、action=txlist、sort=desc、apikey=、
    page=1、offset=20（修1：分頁限縮回應大小避免截斷）。"""
    _patch_key(monkeypatch)
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: _TXLIST_FIXTURE)
    etherscan.reset_throttle()
    captured = []

    def cap(url):
        captured.append(url)
        return _TXLIST_FIXTURE

    monkeypatch.setattr(etherscan, "_fetch_url", cap)
    etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    assert captured
    url = captured[0]
    assert url.startswith("https://api.etherscan.io/v2/api")
    assert "chainid=1" in url
    assert "module=account" in url
    assert "action=txlist" in url
    assert "sort=desc" in url
    assert "apikey=controlled-etherscan-key-1234567890" in url
    # 修1：分頁參數（避免高活躍地址回應 >512KB 被截斷）。
    assert "page=1" in url
    assert "offset=20" in url


def test_fetch_parses_large_whale_tx(monkeypatch):
    """5 ETH（5e18）轉帳產生一筆 Document；0.01 ETH（1e16）被過濾。"""
    _patch_key(monkeypatch)
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: _TXLIST_FIXTURE)
    etherscan.reset_throttle()
    docs = etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    assert len(docs) == 1
    d = docs[0]
    assert d.kind == "whale_onchain"
    assert d.source == "etherscan-whale"
    assert d.meta["coin"] == "ETH"
    assert d.meta["amount_eth"] == 5.0
    assert d.meta["value_wei"] == 5 * 10**18
    assert d.ts == 1700000000.0


def test_fetch_value_filter_skips_small_transfer(monkeypatch):
    """只有小額（< 10**17 wei）的交易 → 0 筆 Document。"""
    _patch_key(monkeypatch)
    small_only = json.dumps(
        {
            "status": "1",
            "message": "OK",
            "result": [
                {
                    "from": "0x" + "a" * 40,
                    "to": "0x" + "b" * 40,
                    "value": str(10**16),  # 0.01 ETH < 0.1 ETH
                    "hash": "0xsmall0001",
                    "timeStamp": "1700000000",
                    "gas": "21000",
                    "gasPrice": "1",
                }
            ],
        }
    ).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: small_only)
    etherscan.reset_throttle()
    assert etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH") == []


def test_fetch_reverted_tx_is_filtered_no_document(monkeypatch):
    """修1（codex P1）：reverted/失敗 tx 過濾（防假證據）。Etherscan txlist 每筆
    tx 帶 isError（"0"成功/"1"失敗）與 txreceipt_status（"0"/"1"）。reverted tx
    的 value 仍記錄企圖轉帳額但實際未轉 → 納入會把「企圖但失敗的大額轉帳」當成
    真 whale 證據（假證據）。斷言：isError=="1" 或 txreceipt_status=="0" 的大額 tx
    **不產** Document。"""
    _patch_key(monkeypatch)
    reverted_fixture = json.dumps(
        {
            "status": "1",
            "message": "OK",
            "result": [
                {  # isError="1"（失敗）的大額 tx → 必須被過濾（不產 Document）
                    "from": "0x" + "a" * 40,
                    "to": "0x" + "b" * 40,
                    "value": str(5 * 10**18),  # 大額（5 ETH），但 tx 失敗
                    "hash": "0xreverted0001",
                    "timeStamp": "1700000000",
                    "gas": "21000",
                    "gasPrice": "20000000000",
                    "isError": "1",
                    "txreceipt_status": "0",
                },
            ],
        }
    ).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: reverted_fixture)
    etherscan.reset_throttle()
    assert etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH") == []


def test_fetch_reverted_tx_only_is_error_also_filtered(monkeypatch):
    """修1 邊界：只有 isError=="1"（缺 txreceipt_status）的大額 tx 也被過濾。"""
    _patch_key(monkeypatch)
    reverted_fixture = json.dumps(
        {
            "status": "1",
            "message": "OK",
            "result": [
                {
                    "from": "0x" + "a" * 40,
                    "to": "0x" + "b" * 40,
                    "value": str(5 * 10**18),
                    "hash": "0xreverted0002",
                    "timeStamp": "1700000000",
                    "isError": "1",  # 只標 isError，無 txreceipt_status
                }
            ],
        }
    ).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: reverted_fixture)
    etherscan.reset_throttle()
    assert etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH") == []


def test_fetch_reverted_tx_only_receipt_zero_also_filtered(monkeypatch):
    """修1 邊界：只有 txreceipt_status=="0"（缺 isError）的大額 tx 也被過濾。"""
    _patch_key(monkeypatch)
    reverted_fixture = json.dumps(
        {
            "status": "1",
            "message": "OK",
            "result": [
                {
                    "from": "0x" + "a" * 40,
                    "to": "0x" + "b" * 40,
                    "value": str(5 * 10**18),
                    "hash": "0xreverted0003",
                    "timeStamp": "1700000000",
                    "txreceipt_status": "0",  # 只標 receipt，無 isError
                }
            ],
        }
    ).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: reverted_fixture)
    etherscan.reset_throttle()
    assert etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH") == []


def test_fetch_success_tx_with_explicit_is_error_zero_passes_filter(monkeypatch):
    """修1 正向：成功 tx（isError=="0" + txreceipt_status=="1"）的大額 → 正常產
    Document（不被過濾）。鎖住「成功標記不誤殺」。"""
    _patch_key(monkeypatch)
    ok_fixture = json.dumps(
        {
            "status": "1",
            "message": "OK",
            "result": [
                {
                    "from": "0x" + "a" * 40,
                    "to": "0x" + "b" * 40,
                    "value": str(5 * 10**18),
                    "hash": "0xok0001",
                    "timeStamp": "1700000000",
                    "isError": "0",
                    "txreceipt_status": "1",
                }
            ],
        }
    ).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: ok_fixture)
    etherscan.reset_throttle()
    docs = etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    assert len(docs) == 1
    assert docs[0].meta["value_wei"] == 5 * 10**18


def test_fetch_direction_always_neutral_hex_addr_has_no_exchange_label(monkeypatch):
    """修2（codex P1 + harper High，誠實降級）：Etherscan txlist 回 raw hex 地址
    （0x+40hex），hex 只含 [0-9a-f]，永遠不含 "binance"/"coinbase"… 等交易所
    關鍵字 → inflow/outflow 恆不可判定。舊版抄自 whale_trades 的關鍵字比對是
    死碼；現在一律誠實標為中性 whale_transfer。本測用真實合法 hex 地址（非
    "0x...binance" 假地址）鎖住此行為。"""
    _patch_key(monkeypatch)
    fixture = json.dumps(
        {
            "status": "1",
            "message": "OK",
            "result": [
                {
                    "from": "0x" + "1" * 40,
                    "to": "0x" + "2" * 40,
                    "value": str(2 * 10**18),
                    "hash": "0xneutral0001",
                    "timeStamp": "1700000000",
                    "gas": "21000",
                    "gasPrice": "1",
                }
            ],
        }
    ).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: fixture)
    etherscan.reset_throttle()
    docs = etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    assert docs[0].meta["direction"] == "whale_transfer"
    # 誠實中性：不假冒 inflow/outflow 方向訊號。
    assert "鯨魚間轉帳" in docs[0].text


# ── B'. 跨地址去重（修3）────────────────────────────────────────────────────────


def test_fetch_dedupes_same_tx_across_multiple_addresses(monkeypatch):
    """修3（codex P2）：追蹤多個 whale 地址時，同筆 tx（from/to 都是追蹤地址）
    會在兩次 txlist query 各回一次。by tx hash 去重 → 同 hash 只產一份 Document。"""
    _patch_key(monkeypatch)
    # 兩個追蹤地址；同一筆 tx（hash 相同、from/to 都在追蹤清單）會在兩次 query
    # 各回一次。
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_WHALE_ADDRESSES",
        "0x" + "a" * 40 + ",0x" + "b" * 40,
    )
    shared_tx = {
        "from": "0x" + "a" * 40,
        "to": "0x" + "b" * 40,
        "value": str(2 * 10**18),  # 大額，會通過 value 過濾
        "hash": "0xdupbeef0001",  # 同一筆 tx
        "timeStamp": "1700000000",
        "gas": "21000",
        "gasPrice": "1",
    }
    fixture = json.dumps(
        {"status": "1", "message": "OK", "result": [shared_tx]}
    ).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: fixture)
    etherscan.reset_throttle()
    docs = etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    # 兩次 query 回同一筆 tx → 只一份 Document（by hash 去重）。
    assert len(docs) == 1
    assert docs[0].meta["value_wei"] == 2 * 10**18


# ── B. security：URL 乾淨、HTTPError/API-error raise sanitized ────────────────


def test_fetch_document_url_is_clean_without_apikey(monkeypatch):
    """Document.url 是 etherscan.io/tx/{hash}，絕不含 apikey。"""
    _patch_key(monkeypatch, key="secret-etherscan-key-abcdef123456")
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: _TXLIST_FIXTURE)
    etherscan.reset_throttle()
    docs = etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    assert docs
    for d in docs:
        assert d.url == "https://etherscan.io/tx/0xdeadbeef0001"
        assert "apikey" not in d.url
        assert "secret-etherscan-key-abcdef123456" not in d.url
        # meta 也不含 key。
        assert "secret-etherscan-key-abcdef123456" not in json.dumps(d.meta)


def test_fetch_http_error_raises_sanitized_without_leaking_url_or_apikey(monkeypatch):
    """修1（codex P1）：HTTPError（429/5xx）原本 silent `return []`（排程器誤判
    成功空結果、覆蓋 cache、無故障）。現改 raise sanitized RuntimeError，讓
    fetch_scheduler 的 catch+log+failure 路徑接住（可觀測、保留舊 cache）。

    ⛔ query-key 防線三重斷言：
      1. 訊息含 status code（429，可觀測用）。
      2. 訊息**絕不含** URL（URL 帶 apikey）——用真 key 字串反向驗證。
      3. `__cause__ is None`——`from None` 已中斷 exception chain，原 HTTPError
         （其 .url 屬性含 apikey）不會透過 traceback/logging.exception 外洩。
    """
    secret_key = "secret-etherscan-key-abcdef123456"
    _patch_key(monkeypatch, key=secret_key)
    call_count = {"n": 0}

    def boom(url):
        call_count["n"] += 1
        # url 含 apikey=<secret_key>；HTTPError 建構時也會把 url 帶進去——若
        # connector 直接 str(exc) 或讓 chain 保留，key 就會外洩。
        raise HTTPError(url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(etherscan, "_fetch_url", boom)
    etherscan.reset_throttle()
    with pytest.raises(RuntimeError) as exc_info:
        etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    msg = str(exc_info.value)
    # 1. 可觀測：訊息標出 status code。
    assert "429" in msg
    assert "Etherscan request failed" in msg
    # 2. query-key 防線：訊息絕不含 URL/apikey。
    assert secret_key not in msg
    assert "apikey" not in msg
    assert "api.etherscan.io" not in msg
    # 3. chain 已斷：__cause__ is None（from None），原 HTTPError（.url 含 key）
    #    不會被 __cause__ 帶出來。
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    assert call_count["n"] == 1  # 確實打了第一次（未重試）


def test_fetch_http_error_5xx_also_raises_sanitized(monkeypatch):
    """修1 涵蓋 5xx：不只 429，所有 HTTPError 都 raise sanitized（不 silent []）。"""
    _patch_key(monkeypatch, key="leak-guard-key-zzz")
    monkeypatch.setattr(
        etherscan,
        "_fetch_url",
        lambda url: (_ for _ in ()).throw(HTTPError(url, 503, "Service Unavailable", {}, None)),
    )
    etherscan.reset_throttle()
    with pytest.raises(RuntimeError, match="503") as exc_info:
        etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    assert "leak-guard-key-zzz" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_fetch_status_zero_payload_raises_sanitized(monkeypatch):
    """修2（codex P1）：Etherscan 常以 HTTP 200 + status="0" + 字串 result 報
    invalid/suspended/rate-limited credential。原本 silent `return []`（排程器
    誤判成功覆蓋 cache）。現 raise sanitized RuntimeError（固定字串，不帶 response
    的 message/result——防 address/key 透過 response 文字 side-channel 外洩）。

    合法空 list（status=="1"+result:[]）仍正常回 []，見下一個測試。
    """
    secret_key = "secret-etherscan-key-status0"
    _patch_key(monkeypatch, key=secret_key)
    # Etherscan 拒絕憑證時的典型 payload（result 是錯誤訊息字串，status=0）。
    payload = json.dumps(
        {
            "status": "0",
            "message": "Invalid API Key",
            "result": "Invalid API Key",
        }
    ).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: payload)
    etherscan.reset_throttle()
    with pytest.raises(RuntimeError) as exc_info:
        etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    msg = str(exc_info.value)
    # 可觀測：標記 status=0 事實。
    assert "status=0" in msg
    # 固定字串，不帶 response 的 message/result（防 address/key side-channel）。
    assert "Invalid API Key" not in msg
    assert secret_key not in msg
    assert exc_info.value.__cause__ is None


def test_fetch_missing_status_with_list_result_processed_not_raised(monkeypatch):
    """修2（codex P2）：status 欄位**不再**用於判 raise——判準改為 result 型別。
    缺 status 但 result 是 list（即使是空 []）→ 正常處理回 []（不 raise）。這鎖住
    「status 不再是決定因子」的新語義：result 是 list 就走正常處理路徑。"""
    _patch_key(monkeypatch)
    payload = json.dumps({"message": "OK", "result": []}).encode()  # 無 status 鍵
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: payload)
    etherscan.reset_throttle()
    assert etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH") == []


def test_fetch_status_one_empty_list_returns_empty_normally(monkeypatch):
    """修2 正常路徑：status=="1" + result 是 list（即使空 []）→ 正常處理回 []。
    合法「無大額轉帳」不是故障，排程器記成功、可寫 cache（這是正確的空結果）。"""
    _patch_key(monkeypatch)
    payload = json.dumps({"status": "1", "message": "OK", "result": []}).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: payload)
    etherscan.reset_throttle()
    assert etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH") == []


def test_fetch_status_zero_empty_list_is_normal_empty_not_failure(monkeypatch):
    """修2（codex P2）：Etherscan 對「合法地址無 tx」回
    `{"status":"0","message":"No transactions found","result":[]}`——result 是
    **空 list**，這是正常空結果非 failure。舊版用 status 欄位判 raise 把它當故障
    → 排程器誤報故障、覆蓋 stale cache。現用 result 型別判：list → 正常處理 → 回 []
    （**不 raise**）。"""
    _patch_key(monkeypatch)
    payload = json.dumps(
        {"status": "0", "message": "No transactions found", "result": []}
    ).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: payload)
    etherscan.reset_throttle()
    assert etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH") == []


def test_fetch_non_dict_response_raises_sanitized(monkeypatch):
    """修2 邊界：回應 JSON 解出非 dict（如 list/null）→ 非預期格式 → raise
    （非 silent []），可觀測。固定字串，不帶 response 細節。"""
    _patch_key(monkeypatch, key="leak-guard-key-nd")
    # 回應是 list 而非 dict（非 Etherscan 正常契約）。
    payload = json.dumps([{"unexpected": "shape"}]).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: payload)
    etherscan.reset_throttle()
    with pytest.raises(RuntimeError) as exc_info:
        etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    assert "unexpected response format" in str(exc_info.value)
    assert "leak-guard-key-nd" not in str(exc_info.value)


def test_fetch_status_one_non_list_result_raises_sanitized(monkeypatch):
    """修2（codex P2）：判準改為 result 型別——status=="1" 但 result 非 list
    （契約異常）仍 raise（非 silent []）。所有非 list result 一律 raise 統一的
    sanitized 訊息（status 欄位不再是因子）。固定字串，不帶 response 細節。"""
    _patch_key(monkeypatch, key="leak-guard-key-nl")
    payload = json.dumps({"status": "1", "message": "OK", "result": "not-a-list"}).encode()
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: payload)
    etherscan.reset_throttle()
    with pytest.raises(RuntimeError) as exc_info:
        etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    assert "status=0" in str(exc_info.value)
    assert "leak-guard-key-nl" not in str(exc_info.value)


# ── C. 幣種過濾 + 憑證狀態 ──────────────────────────────────────────────────────


def test_fetch_non_eth_coin_returns_empty(monkeypatch):
    """非 ETH 幣（如 BTC）直接回 []，不打網路。"""
    _patch_key(monkeypatch)

    def boom(url):  # pragma: no cover - 不應被呼叫
        raise AssertionError(f"非 ETH 不應打網路：{url}")

    monkeypatch.setattr(etherscan, "_fetch_url", boom)
    assert etherscan.EtherscanWhaleSource().fetch("BTC", coin="BTC") == []


def test_fetch_no_key_unconfigured_silent_degrade(monkeypatch):
    """無 key（unconfigured）→ 回 []，不打網路。"""
    monkeypatch.setattr(
        "trustforge.ingestion.etherscan.resolve_api_key", lambda: (None, "unconfigured")
    )

    def boom(url):  # pragma: no cover - 不應被呼叫
        raise AssertionError(f"無 key 不應打網路：{url}")

    monkeypatch.setattr(etherscan, "_fetch_url", boom)
    assert etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH") == []


def test_fetch_unavailable_raises_for_observability(monkeypatch):
    """已配置但 SSM/網路暫失敗（unavailable）→ raise RuntimeError（可觀測）。"""
    monkeypatch.setattr(
        "trustforge.ingestion.etherscan.resolve_api_key", lambda: (None, "unavailable")
    )

    def boom(url):  # pragma: no cover - 不應被呼叫
        raise AssertionError(f"憑證取不到不應打網路：{url}")

    monkeypatch.setattr(etherscan, "_fetch_url", boom)
    with pytest.raises(RuntimeError, match="unavailable"):
        etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")


# ── D. 節流器 ──────────────────────────────────────────────────────────────────


def test_throttle_enforces_min_interval_between_requests(monkeypatch):
    """兩次真請求之間至少間隔 _MIN_INTERVAL_SECONDS（0.25s）。"""
    _patch_key(monkeypatch)
    sleeps = []
    monkeypatch.setattr(etherscan.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(etherscan.time, "monotonic", time.monotonic)
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: _TXLIST_FIXTURE)
    # 多地址 → 多次真請求，第二次必須被節流。
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_WHALE_ADDRESSES",
        "0x" + "a" * 40 + ",0x" + "b" * 40,
    )
    etherscan.reset_throttle()
    etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    # 第一次無需 sleep（_last_request_monotonic is None），第二次需 sleep 補足間隔。
    assert any(s > 0 for s in sleeps), f"第二次請求應被節流 sleep，實得 {sleeps}"


def test_reset_throttle_clears_state(monkeypatch):
    """reset_throttle 後下一次請求不被 sleep（狀態歸零）。"""
    _patch_key(monkeypatch)
    sleeps = []
    monkeypatch.setattr(etherscan.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(etherscan, "_fetch_url", lambda url: _TXLIST_FIXTURE)
    etherscan.reset_throttle()
    etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH")
    # reset 後單一地址、單次請求：_last_request_monotonic 起初為 None → 不 sleep。
    assert sleeps == []


# ── E. build_etherscan_sources ─────────────────────────────────────────────────


def test_build_etherscan_sources_always_registers(monkeypatch):
    """build_etherscan_sources() 永遠註冊來源（同 cmc/whale 慣例），不在 build-time
    resolve 憑證——避免 SSM 暫時不可用時 source 從 registry 消失、憑證中斷變隱形。"""
    monkeypatch.setattr(
        "trustforge.ingestion.etherscan.resolve_api_key", lambda: (None, "unconfigured")
    )
    sources = etherscan.build_etherscan_sources()
    assert len(sources) == 1
    assert sources[0].name == "etherscan-whale"
    assert sources[0].kind == "whale_onchain"

    # 已配置但暫時取不到 → 同樣必須註冊。
    monkeypatch.setattr(
        "trustforge.ingestion.etherscan.resolve_api_key", lambda: (None, "unavailable")
    )
    sources = etherscan.build_etherscan_sources()
    assert len(sources) == 1
    assert sources[0].name == "etherscan-whale"


def test_build_etherscan_sources_returns_source_when_key_present(monkeypatch):
    """有 key → build_etherscan_sources() 回 [EtherscanWhaleSource()]。"""
    monkeypatch.setattr(
        "trustforge.ingestion.etherscan.resolve_api_key",
        lambda: ("controlled-etherscan-key-1234567890", "ssm"),
    )
    sources = etherscan.build_etherscan_sources()
    assert len(sources) == 1
    assert sources[0].name == "etherscan-whale"
    assert sources[0].kind == "whale_onchain"


# ── F. collect 接線（degrade-gracefully）────────────────────────────────────────


def test_fetch_failure_does_not_crash_collect(monkeypatch):
    """連接器例外 → collect 跳過該來源，不拋例外。"""
    from trustforge.ingestion import base

    _patch_key(monkeypatch)
    monkeypatch.setattr(
        etherscan,
        "_fetch_url",
        lambda url: (_ for _ in ()).throw(HTTPError(url, 500, "err", {}, None)),
    )
    src = etherscan.EtherscanWhaleSource()
    docs = base.collect("ETH", coin="ETH", sources=[src], offline=False)
    assert isinstance(docs, list)
