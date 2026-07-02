"""SSRF-safe 共用 HTTP fetch helper（`news.py` / `coingecko.py` / `onchain.py` /
`regulatory.py` / `social.py` 共用，取代各自獨立實作的 `_fetch_url`）。

生產/安全事故修復歷史（codex 對抗審 3 輪，逐層挖出更深的洞，這裡一次收斂）：
  第 1 輪：coindesk 全 308 Permanent Redirect（生產真事故）——白名單 URL
    搬家，第一版只加了「跟 308」的邏輯，寫在 `except HTTPError` 裡手動
    比對「同 host」才跟轉。
  第 2 輪（codex MEDIUM 安全）：第 1 輪的「同 host」防護在 Python 3.11+
    （本專案 Docker 用 `python:3.12-slim`）對 308、以及**所有 Python 版本**
    對 301/302/303/307，都會被 `urlopen()` 預設安裝的 `HTTPRedirectHandler`
    自動跟轉、完全不檢查目的地 host/scheme/IP——手寫的驗證邏輯在 except
    區塊裡，自動跟轉發生時根本不會被執行，形同虛設。改用自訂
    `HTTPRedirectHandler` 禁用自動跟轉 + 手動逐跳驗證（scheme/hostname/
    port/私有 IP）。
  第 3 輪（codex HIGH 安全，本檔修復的問題）：第 2 輪只驗證了「轉址目的
    地」，**初始白名單 URL 本身直接連，完全沒驗證**——即使 URL 字面上是
    寫死白名單，仍可能因為：
      (a) 該網域的 DNS 被污染，或該網域自己的 A/AAAA record 被改指到
          內網位址（含雲端 metadata endpoint 169.254.169.254），第一個
          請求就直接 SSRF，不需要任何轉址；
      (b) 就算驗證了轉址目的地的 IP，`urlopen()`/`http.client` 實際建立
          連線時還是會**用 hostname 自己重新解析一次 DNS**——「驗證」跟
          「連線」中間有一個時間窗口，DNS 答案可以在這個窗口內切換
          （DNS rebinding / TOCTOU），讓驗證形同虛設。
  修法（本檔）：
    1. **每一跳都驗證，含初始 URL**——不是只驗轉址目的地。
    2. **DNS pinning**：驗證出的「安全 IP」直接拿去建立 TCP 連線
       （`_PinnedHTTPSConnection`），不讓 `http.client`/`urlopen` 自己再
       解析一次 hostname——徹底杜絕「驗證」與「連線」之間的窗口。
       `server_hostname`（TLS SNI + 憑證 CN/SAN 驗證）與 Host header 仍
       使用原始 hostname，確保 TLS 憑證驗證與虛擬主機路由都正確
       （等同瀏覽器對「已知 IP + SNI」的標準連線模式）。
    3. **抽成這個共用模組，套用到所有連接器**——不只 news.py，
       coingecko.py/onchain.py/regulatory.py/social.py 一樣是「urlopen +
       白名單 host」的模式，同樣有第 2/3 輪的洞，一次性 by construction
       封閉整個 SSRF class，不是逐檔案各修一次。

安全模型摘要（每一跳，含初始 URL，都要通過全部檢查才連線）：
  - scheme 必須是 `https`
  - hostname 必須與「最初呼叫 `fetch_url()` 時傳入的 URL」的 hostname
    完全相同（轉址鏈不能中途變更 host，也不是只比對「上一跳」——第二跳
    才變更 host 一樣會被擋，見 `test_safe_fetch.py` 回歸鎖）
  - port 必須是 https 預設 443（或 URL 未寫明 port）
  - hostname 解析出的**所有** IP 都必須非私有/迴環/link-local/保留/
    多播/未指定（`ipaddress` 標準庫判斷，涵蓋 169.254.169.254 這類雲端
    metadata endpoint）
  - 驗證通過後，**直接拿驗證用的那個 IP** 建立實際 TCP 連線（DNS
    pinning），不再讓連線階段重新解析一次 hostname
  - 最多跟 `max_redirects`（預設 3）跳，避免無限轉址鏈

安全措施（沿用各連接器原本就有的規格，這裡集中實作一次）：
  - timeout / 回應大小上限（超過截斷）
  - 固定 User-Agent（各連接器自帶各自的 UA 字串傳入）
  - 完整 TLS 憑證驗證（`ssl._create_default_https_context()`，不降級）
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse

_REDIRECT_CODES = (301, 302, 303, 307, 308)


class SSRFBlockedError(HTTPError):
    """某一跳（含初始 URL）未通過 SSRF 安全驗證而被擋下。

    繼承 `urllib.error.HTTPError`：這個 repo 既有的呼叫端（`base.collect()`
    的 degrade-gracefully 機制、各連接器測試的 `except HTTPError`/
    `pytest.raises(HTTPError)`）原本就是針對 `HTTPError` 設計，繼承後
    不需要額外改動呼叫端就能正確被既有的錯誤處理路徑接住。`code=0`
    代表「連真 HTTP 請求都還沒送出就被擋下」，不是任何伺服器真的回應
    的狀態碼。
    """

    def __init__(self, url: str, reason: str):
        super().__init__(url, 0, f"SSRF blocked: {reason}", None, None)


def _resolve_safe_ip(hostname: str) -> str:
    """解析 `hostname`，回傳第一個「非私有/迴環/link-local/保留/多播/
    未指定」的 IP 字串；沒有任何安全 IP 可用（含完全解析失敗）一律拋出
    `OSError`，由呼叫端轉成 `SSRFBlockedError`。"""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise OSError(f"{hostname}: DNS 解析失敗（{exc}）") from exc
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            continue
        return ip_str
    raise OSError(f"{hostname}: 解析出的 IP 皆非安全位址（私有/迴環/link-local/保留）")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """DNS pinning 版 `HTTPSConnection`：TCP 連線目的地固定用已驗證安全
    的 `pinned_ip`（`socket.create_connection((pinned_ip, port), ...)`
    ——`pinned_ip` 是 IP 字面值，不會觸發第二次 hostname DNS 解析，徹底
    杜絕「驗證」與「連線」之間的 rebinding 窗口）；`server_hostname`
    （TLS SNI + 憑證驗證）與 Host header 仍使用原始 `host`，確保 TLS
    握手與虛擬主機路由都正確。"""

    def __init__(self, pinned_ip: str, host: str, port: int = 443, **kwargs):
        super().__init__(host, port, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        if getattr(self, "_tunnel_host", None):
            self.sock = sock
            self._tunnel()
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _validate_hop(url: str, allowed_hostname: str) -> tuple[str, str, int]:
    """驗證單一跳（含初始 URL）：scheme=https、hostname 與
    `allowed_hostname` 完全相同、port=443（或未指定）、解析出的 IP 非
    私有。回傳 `(hostname, pinned_ip, port)`；任一項不合格拋出
    `SSRFBlockedError`。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SSRFBlockedError(url, "scheme 必須是 https")
    if not parsed.hostname or parsed.hostname != allowed_hostname:
        raise SSRFBlockedError(url, f"hostname 須與白名單 {allowed_hostname!r} 完全相同")
    port = parsed.port or 443
    if port != 443:
        raise SSRFBlockedError(url, "port 須為 https 預設 443")
    try:
        pinned_ip = _resolve_safe_ip(parsed.hostname)
    except OSError as exc:
        raise SSRFBlockedError(url, str(exc)) from exc
    return parsed.hostname, pinned_ip, port


def fetch_url(
    url: str,
    *,
    user_agent: str,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 5,
    max_bytes: int = 512 * 1024,
    max_redirects: int = 3,
) -> bytes:
    """SSRF-safe 的 HTTPS GET（見模組頂部說明）：每一跳（含初始 URL）都
    驗證 scheme/hostname/port/私有 IP，驗證用的 IP 直接拿去 DNS pinning
    連線，轉址完全手動處理（不使用 `urllib.request` 的自動轉址機制），
    最多 `max_redirects` 跳。回應大小超過 `max_bytes` 截斷。

    `extra_headers`（如有）與固定 `User-Agent` 一併附加在請求 header 上
    （如 CoinGecko 選用的 `x-cg-demo-api-key`）；`Host` header 由
    `http.client` 依連線用的 `host`（原始 hostname，非 pinned IP）自動
    附加，不需要也不應該手動再加一次。
    """
    allowed_hostname = urlparse(url).hostname
    if not allowed_hostname:
        raise SSRFBlockedError(url, "URL 缺少 hostname")
    current_url = url

    for hop in range(max_redirects + 1):
        hostname, pinned_ip, port = _validate_hop(current_url, allowed_hostname)
        parsed = urlparse(current_url)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        headers = {"User-Agent": user_agent}
        if extra_headers:
            headers.update(extra_headers)

        conn = _PinnedHTTPSConnection(pinned_ip, hostname, port, timeout=timeout)
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            resp_headers = resp.headers
            if status in _REDIRECT_CODES:
                location = resp_headers.get("Location")
                resp.read()  # 排空這一跳的連線，避免殘留資料影響下一跳
                if not location:
                    raise HTTPError(current_url, status, resp.reason, resp_headers, None)
                if hop >= max_redirects:
                    raise HTTPError(
                        current_url, status,
                        f"{resp.reason}（超過最大轉址跳數 {max_redirects}）",
                        resp_headers, None,
                    )
                current_url = urljoin(current_url, location)
                continue
            body = resp.read(max_bytes)
            if status >= 400:
                raise HTTPError(current_url, status, resp.reason, resp_headers, None)
            return body
        finally:
            conn.close()

    raise AssertionError("unreachable：迴圈必定在跳數上限內 return 或 raise")
