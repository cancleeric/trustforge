"""SSRF-safe 共用 fetch helper（`trustforge.ingestion.safe_fetch`）核心測試。

codex 對抗審第 3 輪 HIGH 安全發現：第 2 輪只驗證了「轉址目的地」，初始
白名單 URL 本身完全沒驗證，且驗證用的 DNS 解析結果沒有真正「固定」給
實際連線用（TOCTOU / DNS rebinding 窗口）。以下測試直接針對
`safe_fetch.py` 的核心邏輯，全部 mock `socket.getaddrinfo`/
`socket.create_connection`/連線物件本身，不打真網路、不真的碰 DNS
（credit-safe）。
"""
from __future__ import annotations

import socket
import ssl

import pytest

from trustforge.ingestion import safe_fetch


# ── 測試替身 ──────────────────────────────────────────────────────────────────

class _FakeHeaders(dict):
    """`http.client.HTTPResponse.headers`（`email.message.Message`）的最小
    替身：只需要 `.get(name, default)`。"""

    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class _FakeHTTPResponse:
    def __init__(self, status: int, headers: dict | None = None, body: bytes = b""):
        self.status = status
        self.reason = "OK" if status < 300 else ("Redirect" if status < 400 else "Error")
        self.headers = _FakeHeaders(headers or {})
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]


class _FakeConnection:
    """`_PinnedHTTPSConnection` 的測試替身：`request()`/`getresponse()` 皆
    可控，記錄每次呼叫用的 path/headers 供斷言。"""

    def __init__(self, response: _FakeHTTPResponse, pinned_ip: str, host: str, port: int):
        self._response = response
        self.pinned_ip = pinned_ip
        self.host = host
        self.port = port
        self.requested: dict | None = None

    def request(self, method, path, headers=None):
        self.requested = {"method": method, "path": path, "headers": headers}

    def getresponse(self):
        return self._response

    def close(self):
        pass


def _make_fake_connection_factory(responses: list[_FakeHTTPResponse]):
    """回傳一個可取代 `safe_fetch._PinnedHTTPSConnection` 的 factory：依序
    對每一跳（每次被呼叫）回傳 `responses` 裡對應的假回應。`factory.
    connections` 記錄每次建立的假連線物件，供斷言 pinned_ip/host/headers。"""
    connections: list[_FakeConnection] = []

    def _factory(pinned_ip, host, port, timeout=None, context=None):
        idx = len(connections)
        conn = _FakeConnection(responses[idx], pinned_ip, host, port)
        conn.tls_context = context
        connections.append(conn)
        return conn

    _factory.connections = connections
    return _factory


def _fake_getaddrinfo_returning(*ips: str):
    def _fake(host, port=None, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]
    return _fake


PUBLIC_IP = "93.184.216.34"  # example.com 的真實公開 IP（僅當測試字面值，不會真的連線）


def test_verified_tls_context_requires_certificate_and_hostname_verification():
    context = safe_fetch._verified_tls_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    if hasattr(ssl, "TLSVersion"):
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_verified_tls_context_loads_certifi_bundle(monkeypatch):
    calls = []
    original = safe_fetch.ssl.create_default_context

    def capture(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(safe_fetch.ssl, "create_default_context", capture)

    context = safe_fetch._verified_tls_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert calls == [{
        "purpose": ssl.Purpose.SERVER_AUTH,
        "cafile": safe_fetch.certifi.where(),
    }]


# ── 初始 URL 驗證（codex HIGH：這是本輪修復的核心，之前完全沒驗）────────────

def test_initial_url_private_ip_rejected_before_any_connection(monkeypatch):
    """核心驗收：初始白名單 URL 解析出私有 IP（DNS 污染/來源自己的 A
    record 被改指向內網）時，必須在**建立任何連線之前**就被拒絕。"""
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning("10.0.0.5"))

    connection_attempted = []

    def _boom_connection_factory(*args, **kwargs):
        connection_attempted.append(args)
        raise AssertionError("私有 IP 驗證失敗時不該嘗試建立任何連線")

    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", _boom_connection_factory)

    with pytest.raises(safe_fetch.SSRFBlockedError):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")
    assert connection_attempted == []


@pytest.mark.parametrize("private_ip", [
    "127.0.0.1",        # 迴環
    "10.0.0.5",          # 私有（RFC1918）
    "192.168.1.1",       # 私有（RFC1918）
    "172.16.0.1",        # 私有（RFC1918）
    "169.254.169.254",   # link-local，雲端 metadata endpoint（AWS/GCP/Azure 常見攻擊目標）
    "224.0.0.1",         # 多播
    "0.0.0.0",           # 未指定
])
def test_initial_url_various_unsafe_ips_rejected(monkeypatch, private_ip):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(private_ip))
    with pytest.raises(safe_fetch.SSRFBlockedError):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")


def test_initial_url_http_scheme_rejected(monkeypatch):
    """非 https（如降級到 http）一律拒絕，不嘗試解析 DNS。"""
    def _boom(*args, **kwargs):
        raise AssertionError("非 https scheme 不該觸發 DNS 解析")
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _boom)
    with pytest.raises(safe_fetch.SSRFBlockedError):
        safe_fetch.fetch_url("http://example.com/feed", user_agent="UA")


def test_initial_url_dns_resolution_failure_rejected(monkeypatch):
    """DNS 完全解析失敗（如網域不存在）也視為不安全，拒絕而非讓例外原樣
    往外飄（統一走 `SSRFBlockedError`）。"""
    def _fail(host, port=None, *a, **kw):
        raise socket.gaierror("Name or service not known")
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fail)
    with pytest.raises(safe_fetch.SSRFBlockedError):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")


def test_ssrf_blocked_error_is_http_error_subclass():
    """既有呼叫端（`base.collect()` 的 degrade-gracefully、各連接器測試的
    `except HTTPError`）不需要額外改動就能接住這個新例外類別。"""
    from urllib.error import HTTPError
    assert issubclass(safe_fetch.SSRFBlockedError, HTTPError)


# ── DNS pinning / rebinding 抵抗力（codex HIGH 核心驗收）───────────────────

class _ConnectRecorded(Exception):
    """測試用哨兵例外：`socket.create_connection` 一被呼叫就丟出，藉此
    在「連線層」擋住，不需要真的完成 TCP/TLS 握手就能斷言連線目標。"""


def test_dns_pinning_connects_to_validated_ip_not_reresolved_hostname(monkeypatch):
    """核心驗收：驗證階段解析出的 IP，會被直接拿去做實際 TCP 連線
    （DNS pinning）——`socket.getaddrinfo` 全程只被呼叫 1 次（驗證階段），
    連線階段（`_PinnedHTTPSConnection.connect()`）用的是那個「已經拿到手
    的 IP 字面值」，不會再讓 `getaddrinfo` 重新解析一次 hostname——沒有
    第二次解析，就沒有「驗證後 DNS 被 rebinding」的窗口可鑽。"""
    getaddrinfo_calls: list[str] = []

    def _fake_getaddrinfo(host, port=None, *a, **kw):
        getaddrinfo_calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]

    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo)

    connect_targets: list[tuple] = []

    def _fake_create_connection(addr, *a, **kw):
        connect_targets.append(addr)
        raise _ConnectRecorded()

    monkeypatch.setattr(safe_fetch.socket, "create_connection", _fake_create_connection)

    with pytest.raises(_ConnectRecorded):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")

    assert getaddrinfo_calls == ["example.com"], (
        f"驗證只該對 hostname 解析一次 DNS，實得呼叫：{getaddrinfo_calls}"
    )
    assert connect_targets == [(PUBLIC_IP, 443)], (
        f"實際 TCP 連線目標須是驗證通過的 IP 字面值，不是重新解析 hostname 的結果，"
        f"實得 {connect_targets}"
    )


def test_dns_pinning_rebinding_after_validation_does_not_affect_connection(monkeypatch):
    """模擬攻擊情境：`getaddrinfo` 若在「驗證之後」被呼叫第二次會回傳
    私有 IP（rebinding 攻擊的預期效果）——驗證這個第二次呼叫**根本不會
    發生**（`safe_fetch` 的實作原生不會在連線階段重新解析 hostname），
    因此 rebinding 攻擊對這個實作無效。"""
    call_count = {"n": 0}

    def _fake_getaddrinfo(host, port=None, *a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]
        # 若程式碼有 bug 真的呼叫了第二次，回傳私有 IP 模擬 rebinding 已發生
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))]

    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo)

    connect_targets: list[tuple] = []

    def _fake_create_connection(addr, *a, **kw):
        connect_targets.append(addr)
        raise _ConnectRecorded()

    monkeypatch.setattr(safe_fetch.socket, "create_connection", _fake_create_connection)

    with pytest.raises(_ConnectRecorded):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")

    # 就算 mock 已經準備好在「第二次呼叫」回傳私有 IP，我們的程式碼根本
    # 不會產生第二次呼叫——這就是 pinning 生效的直接證據。
    assert call_count["n"] == 1
    assert connect_targets == [(PUBLIC_IP, 443)]


# ── 多位址 fallback（codex 第 4 輪 HIGH 可用性核心驗收）────────────────────
#    只 pin 第一個已驗證 IP、connect 只試一次的舊實作，在 IPv4-only 部署
#    遇到「不可達的 AAAA 排序在可用 A 前面」時會整個請求直接失敗、不
#    fallback。修法：`_resolve_safe_ips` 回傳全部已驗證安全 IP，連線階段
#    依序嘗試，前一個連線失敗才試下一個。

class _FlakyThenOkConnection:
    """測試替身：對指定的「會失敗」IP 在 `request()` 階段丟出 `OSError`
    （模擬 TCP 連線失敗），其餘 IP 正常回應——用來證明 fallback 機制真的
    會試下一個已驗證安全的 IP，而不是直接放棄整個請求。"""

    def __init__(self, unreachable_ips, response, attempted: list):
        self._unreachable_ips = set(unreachable_ips)
        self._response = response
        self._attempted = attempted

    def __call__(self, pinned_ip, host, port, timeout=None, context=None):
        self._attempted.append(pinned_ip)
        return _FlakyThenOkConnectionInstance(pinned_ip, pinned_ip in self._unreachable_ips, self._response)


class _FlakyThenOkConnectionInstance:
    def __init__(self, pinned_ip, should_fail, response):
        self.pinned_ip = pinned_ip
        self._should_fail = should_fail
        self._response = response

    def request(self, method, path, headers=None):
        if self._should_fail:
            raise OSError("Network is unreachable")

    def getresponse(self):
        return self._response

    def close(self):
        pass


def test_multi_ip_fallback_first_unreachable_second_succeeds(monkeypatch):
    """第一個已驗證 IP（模擬排序在前但不可達的 AAAA）連線失敗，必須自動
    fallback 試清單裡下一個已驗證安全的 IP，整體請求仍要成功——不能因為
    單一位址不可達就讓整條真資料管線斷掉。"""
    # 兩個都是真實可路由的公開 IP（避免用 RFC 3849/5737 文件保留範圍——那些
    # 會被 `_resolve_safe_ips` 正確判定為 is_private 而濾掉，不適合用來模擬
    # 「已驗證安全、但網路層連不上」的情境）。
    unreachable_public_ip = "1.1.1.1"
    reachable_public_ip = PUBLIC_IP
    monkeypatch.setattr(
        safe_fetch.socket, "getaddrinfo",
        _fake_getaddrinfo_returning(unreachable_public_ip, reachable_public_ip),
    )
    attempted: list[str] = []
    factory = _FlakyThenOkConnection(
        unreachable_ips=[unreachable_public_ip],
        response=_FakeHTTPResponse(200, body=b"fallback worked"),
        attempted=attempted,
    )
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    body = safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")

    assert body == b"fallback worked"
    assert attempted == [unreachable_public_ip, reachable_public_ip], (
        f"應先試第一個已驗證 IP、失敗後 fallback 試下一個，實得嘗試順序 {attempted}"
    )


def test_multi_ip_fallback_all_unreachable_raises(monkeypatch):
    """已驗證清單裡的 IP 全部連線失敗時，才對外拋出錯誤（不會無限重試，
    也不會悄悄回傳空結果）。"""
    ip_a, ip_b = "1.1.1.1", "8.8.4.4"
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(ip_a, ip_b))
    attempted: list[str] = []
    factory = _FlakyThenOkConnection(
        unreachable_ips=[ip_a, ip_b],
        response=_FakeHTTPResponse(200, body=b"unreachable"),
        attempted=attempted,
    )
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    with pytest.raises(OSError):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")
    assert attempted == [ip_a, ip_b]


def test_multi_ip_fallback_never_attempts_private_ip(monkeypatch):
    """`getaddrinfo` 混雜私有與公開位址時，fallback 清單只會包含已驗證
    的公開 IP——就算公開 IP 連線失敗，也絕對不會退而求其次試私有 IP（不
    能為了「至少要連得上」而放行不安全的候選）。"""
    private_ip = "10.0.0.1"
    public_ip = "93.184.216.34"
    monkeypatch.setattr(
        safe_fetch.socket, "getaddrinfo",
        _fake_getaddrinfo_returning(private_ip, public_ip),
    )
    attempted: list[str] = []
    factory = _FlakyThenOkConnection(
        unreachable_ips=[public_ip],  # 唯一的公開 IP 也連線失敗
        response=_FakeHTTPResponse(200, body=b"n/a"),
        attempted=attempted,
    )
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    with pytest.raises(OSError):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")
    # 私有 IP 從頭到尾沒有出現在嘗試清單裡（`_resolve_safe_ips` 已經濾掉）
    assert private_ip not in attempted
    assert attempted == [public_ip]


# ── 轉址回應 body 有界讀取（codex 第 4 輪 MEDIUM 安全核心驗收）─────────────

class _ReadTrackingResponse(_FakeHTTPResponse):
    """記錄 `.read()` 是否真的被呼叫過——用來證明轉址回應完全不會被讀取
    body（見模組頂部第 4 輪 (b)：舊版無界 `resp.read()` 排空轉址回應，
    同 host 上游回超大/無限 body 轉址時會耗記憶體/卡到 timeout）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.read_called = False

    def read(self, n: int = -1) -> bytes:
        self.read_called = True
        return super().read(n)


def test_fetch_url_redirect_body_never_read(monkeypatch):
    """轉址回應（即使 body 巨大/模擬無限長）完全不會被讀取——直接關閉這
    一跳的連線即可，不需要、也不該無界排空。"""
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    huge_redirect_resp = _ReadTrackingResponse(
        308, headers={"Location": "/feed-new"}, body=b"x" * (10 * 1024 * 1024),
    )
    final_resp = _FakeHTTPResponse(200, body=b"ok")
    factory = _make_fake_connection_factory([huge_redirect_resp, final_resp])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    body = safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")

    assert body == b"ok"
    assert huge_redirect_resp.read_called is False, (
        "轉址回應的 body 不該被讀取（避免無界排空耗記憶體/卡 timeout）"
    )


def test_fetch_url_final_response_still_bounded_by_max_bytes(monkeypatch):
    """非轉址（最終）回應仍然維持原本 `max_bytes` 的有界讀取，這條修復
    只針對轉址回應，不影響既有的終端回應大小上限行為。"""
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    factory = _make_fake_connection_factory([_FakeHTTPResponse(200, body=b"0123456789")])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    body = safe_fetch.fetch_url("https://example.com/feed", user_agent="UA", max_bytes=4)
    assert body == b"0123"


# ── 轉址鏈邏輯（沿用假連線 factory，不牽涉真的 socket/TLS）─────────────────

def test_fetch_url_success_no_redirect(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    factory = _make_fake_connection_factory([
        _FakeHTTPResponse(200, body=b"hello world"),
    ])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    body = safe_fetch.fetch_url("https://example.com/feed", user_agent="MyUA/1.0")
    assert body == b"hello world"
    conn = factory.connections[0]
    assert conn.pinned_ip == PUBLIC_IP
    assert conn.host == "example.com"
    assert conn.tls_context.verify_mode == ssl.CERT_REQUIRED
    assert conn.tls_context.check_hostname is True
    assert conn.requested["headers"]["User-Agent"] == "MyUA/1.0"


def test_fetch_url_extra_headers_merged(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    factory = _make_fake_connection_factory([_FakeHTTPResponse(200, body=b"ok")])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    safe_fetch.fetch_url(
        "https://example.com/feed", user_agent="MyUA/1.0",
        extra_headers={"x-cg-demo-api-key": "fake-key"},
    )
    headers = factory.connections[0].requested["headers"]
    assert headers["User-Agent"] == "MyUA/1.0"
    assert headers["x-cg-demo-api-key"] == "fake-key"


def test_fetch_url_max_bytes_truncates(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    factory = _make_fake_connection_factory([_FakeHTTPResponse(200, body=b"0123456789")])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    body = safe_fetch.fetch_url("https://example.com/feed", user_agent="UA", max_bytes=4)
    assert body == b"0123"


def test_fetch_url_308_same_host_redirect_followed(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    factory = _make_fake_connection_factory([
        _FakeHTTPResponse(308, headers={"Location": "/feed-new"}),
        _FakeHTTPResponse(200, body=b"new content"),
    ])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    body = safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")
    assert body == b"new content"
    assert len(factory.connections) == 2
    assert factory.connections[1].requested["path"] == "/feed-new"


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_fetch_url_all_redirect_codes_require_validation(monkeypatch, code):
    """安全回歸鎖：301/302/303/307/308 全部一視同仁走同一套手動驗證，不
    是只有 308 才修——跨 host 目標一律拒絕。"""
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    factory = _make_fake_connection_factory([
        _FakeHTTPResponse(code, headers={"Location": "https://evil.example.com/steal"}),
    ])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    with pytest.raises(safe_fetch.SSRFBlockedError):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")


def test_fetch_url_second_hop_cross_host_redirect_rejected(monkeypatch):
    """SSRF 回歸鎖：第一跳同 host（通過驗證、正常跟轉），第一跳的目的地
    緊接著把請求轉到不同 host——不能只比對「上一跳」而放行第二跳才變更
    host 的繞過手法，整條鏈都要跟最初白名單 host 相同。"""
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    factory = _make_fake_connection_factory([
        _FakeHTTPResponse(308, headers={"Location": "https://example.com/feed-hop2"}),
        _FakeHTTPResponse(302, headers={"Location": "https://evil.example.com/steal"}),
    ])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    with pytest.raises(safe_fetch.SSRFBlockedError):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")
    # 第一跳確實被打過（同 host，通過驗證），第二跳的跨 host 目標沒有被連線
    assert len(factory.connections) == 2


def test_fetch_url_redirect_to_private_ip_rejected(monkeypatch):
    """轉址目標 host 字串跟白名單相同，但（模擬 rebinding）這次解析出的
    IP 是私有位址時，拒絕跟轉。"""
    call_count = {"n": 0}

    def _fake_getaddrinfo(host, port=None, *a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))]

    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo)
    factory = _make_fake_connection_factory([
        _FakeHTTPResponse(308, headers={"Location": "/feed-new"}),
    ])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    with pytest.raises(safe_fetch.SSRFBlockedError):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")


def test_fetch_url_redirect_non_standard_port_rejected(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    factory = _make_fake_connection_factory([
        _FakeHTTPResponse(308, headers={"Location": "https://example.com:8443/feed-new"}),
    ])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)
    with pytest.raises(safe_fetch.SSRFBlockedError):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")


def test_fetch_url_exceeds_max_redirects_rejected(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    responses = [
        _FakeHTTPResponse(308, headers={"Location": f"/hop{i}"}) for i in range(1, 6)
    ]
    factory = _make_fake_connection_factory(responses)
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    with pytest.raises(Exception):  # HTTPError（超過跳數）
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA", max_redirects=3)
    # 最初一次 + 最多 3 跳 = 4 次連線，不會無止盡跟下去
    assert len(factory.connections) == 4


def test_fetch_url_redirect_missing_location_raises(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    factory = _make_fake_connection_factory([_FakeHTTPResponse(302, headers={})])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)
    from urllib.error import HTTPError
    with pytest.raises(HTTPError):
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")


def test_fetch_url_non_redirect_http_error_propagates(monkeypatch):
    """非轉址類錯誤（如 429/500）原樣拋出 `HTTPError`，帶正確狀態碼。"""
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(PUBLIC_IP))
    factory = _make_fake_connection_factory([_FakeHTTPResponse(429, body=b"slow down")])
    monkeypatch.setattr(safe_fetch, "_PinnedHTTPSConnection", factory)

    from urllib.error import HTTPError
    with pytest.raises(HTTPError) as exc_info:
        safe_fetch.fetch_url("https://example.com/feed", user_agent="UA")
    assert exc_info.value.code == 429


# ── `_resolve_safe_ips` 單元測試（涵蓋 ipaddress 判斷的每個分類）──────────

@pytest.mark.parametrize("ip,should_pass", [
    ("93.184.216.34", True),     # 公開 IP
    ("8.8.8.8", True),           # 公開 IP
    ("127.0.0.1", False),        # 迴環
    ("10.1.2.3", False),         # 私有 RFC1918
    ("172.31.255.255", False),   # 私有 RFC1918
    ("192.168.0.1", False),      # 私有 RFC1918
    ("169.254.169.254", False),  # link-local / 雲端 metadata endpoint
    ("224.0.0.251", False),      # 多播
    ("0.0.0.0", False),          # 未指定
    ("::1", False),              # IPv6 迴環
    ("fc00::1", False),          # IPv6 私有 (ULA)
])
def test_resolve_safe_ips_classification(monkeypatch, ip, should_pass):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", _fake_getaddrinfo_returning(ip))
    if should_pass:
        assert safe_fetch._resolve_safe_ips("example.com") == [ip]
    else:
        with pytest.raises(OSError):
            safe_fetch._resolve_safe_ips("example.com")


def test_resolve_safe_ips_skips_unsafe_keeps_all_safe_in_order(monkeypatch):
    """`getaddrinfo` 回傳多筆時，跳過不安全的，**保留全部**安全的 IP（依
    原始順序、去重）——第 4 輪 (a) 修復：不能只挑第一個，第一個不可達時
    要有清單可以 fallback。"""
    monkeypatch.setattr(
        safe_fetch.socket, "getaddrinfo",
        _fake_getaddrinfo_returning("127.0.0.1", "10.0.0.1", PUBLIC_IP, "8.8.8.8", PUBLIC_IP),
    )
    # 私有的兩筆被濾掉；PUBLIC_IP 重複只留一筆；順序照 getaddrinfo 原始順序
    assert safe_fetch._resolve_safe_ips("example.com") == [PUBLIC_IP, "8.8.8.8"]


def test_resolve_safe_ips_never_includes_private_ip_even_as_fallback(monkeypatch):
    """即使清單裡只剩私有 IP 可用（沒有任何安全位址），也絕對不會把私有
    IP 當成「反正有得連就好」的 fallback 塞進已驗證清單——寧可整跳直接
    失敗（`OSError`），不會為了可用性放寬安全性。"""
    monkeypatch.setattr(
        safe_fetch.socket, "getaddrinfo",
        _fake_getaddrinfo_returning("127.0.0.1", "10.0.0.1", "169.254.169.254"),
    )
    with pytest.raises(OSError):
        safe_fetch._resolve_safe_ips("example.com")
