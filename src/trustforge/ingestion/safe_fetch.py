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
  第 4 輪（codex，安全+可用性，本檔本次修復的問題）：
    (a) [HIGH 可用性] 第 3 輪只 pin「第一個」已驗證公開 IP、connect 只試
        它一個——標準 `socket.create_connection(hostname)` 會依序嘗試
        `getaddrinfo` 回傳的**所有**位址，但第 3 輪的實作只試第一個就放
        棄。若 DNS 把不可達的 AAAA(IPv6) 排在可用 A(IPv4) 前面（IPv4-only
        部署環境常見排序），第一個「已驗證安全」的位址其實連不通，整個
        請求直接失敗、不會 fallback 到後面能連的 IPv4——而 5 個連接器共
        用這一條路，一個網路環境設定就能讓真資料管線全滅，正是這個健康
        修復任務原本要解決、卻反而被自己新增的邏輯弄壞的那類問題。
        修法：`_resolve_safe_ips` 回傳**所有**已驗證非私有公開位址的清單
        （每個都各自通過完整的私有 IP 檢查，不會為了 fallback 放寬任何
        一個候選的安全性），連線階段**依序嘗試每一個 pinned IP**（前一個
        TCP 連線失敗才試下一個，每次仍然是 DNS pinning 到那個已驗證的
        IP，hostname 依然只作 SNI/憑證/Host），全部位址都失敗才對外拋出
        錯誤。
    (b) [MEDIUM 安全] 轉址回應的 body 原本用無界 `resp.read()` 排空（想
        避免殘留資料影響下一跳），但轉址情境根本不需要讀 body、也不該無
        界讀——同網域的上游若被入侵/誤配置在轉址回應裡塞超大或無限長度
        的 body，會被硬讀到記憶體耗盡或卡到 timeout。修法：轉址時**完全
        不讀 body**，直接關閉這一跳的連線（反正下一跳本來就是全新的
        `_PinnedHTTPSConnection`，不會有「殘留資料汙染下一跳」的問題，
        關閉舊連線就足夠乾淨）。非轉址（含錯誤狀態碼）的最終回應維持原
        本 `resp.read(max_bytes)` 的有界讀取，不受影響。

安全模型摘要（每一跳，含初始 URL，都要通過全部檢查才連線）：
  - scheme 必須是 `https`
  - hostname 必須與「最初呼叫 `fetch_url()` 時傳入的 URL」的 hostname
    完全相同（轉址鏈不能中途變更 host，也不是只比對「上一跳」——第二跳
    才變更 host 一樣會被擋，見 `test_safe_fetch.py` 回歸鎖）
  - port 必須是 https 預設 443（或 URL 未寫明 port）
  - hostname 解析出的**每一個**候選 IP 都必須各自非私有/迴環/link-local/
    保留/多播/未指定（`ipaddress` 標準庫判斷，涵蓋 169.254.169.254 這類
    雲端 metadata endpoint）才會進入「已驗證清單」；只要有任一個安全 IP
    存在就算通過驗證（不要求全部位址都安全，但不安全的位址永遠不會被
    拿去連線）
  - 驗證通過後，**依序拿已驗證清單裡的 IP** 建立實際 TCP 連線（DNS
    pinning，每個候選各自 pin），不再讓連線階段重新解析一次 hostname；
    前一個連線失敗才試下一個，全部失敗才對外拋錯
  - 最多跟 `max_redirects`（預設 3）跳，避免無限轉址鏈；轉址回應一律不
    讀 body（見上方第 4 輪 (b)）

安全措施（沿用各連接器原本就有的規格，這裡集中實作一次）：
  - timeout / 回應大小上限（超過截斷，僅套用在最終非轉址回應）
  - 固定 User-Agent（各連接器自帶各自的 UA 字串傳入）
  - 完整 TLS 憑證驗證（`ssl._create_default_https_context()`，不降級）
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import certifi
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse

_REDIRECT_CODES = (301, 302, 303, 307, 308)


def _verified_tls_context() -> ssl.SSLContext:
    """Return the only TLS policy accepted by external connectors.

    Keeping this explicit makes certificate and hostname verification auditable;
    callers cannot silently replace it with an unverified context.
    """
    # macOS framework Python and minimal containers do not always expose a
    # complete system trust store.  Use certifi's Mozilla CA bundle; verification
    # remains mandatory and callers still cannot inject an unverified context.
    context = ssl.create_default_context(
        purpose=ssl.Purpose.SERVER_AUTH, cafile=certifi.where(),
    )
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    if hasattr(ssl, "TLSVersion"):
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


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


def _resolve_safe_ips(hostname: str) -> list[str]:
    """解析 `hostname`，回傳**所有**「非私有/迴環/link-local/保留/多播/
    未指定」的 IP 字串清單（保留 `getaddrinfo` 原始順序，去重）；一個都
    沒有（含完全解析失敗）一律拋出 `OSError`，由呼叫端轉成
    `SSRFBlockedError`。

    刻意回傳清單而非單一 IP：見模組頂部第 4 輪 (a) 說明——只 pin 第一個
    位址會在該位址剛好不可達時（如 IPv4-only 部署遇到排序在前的 AAAA）
    整條路直接斷掉，不會 fallback 到清單裡其他同樣已驗證安全的位址。
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise OSError(f"{hostname}: DNS 解析失敗（{exc}）") from exc
    safe_ips: list[str] = []
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
        if ip_str not in safe_ips:
            safe_ips.append(ip_str)
    if not safe_ips:
        raise OSError(f"{hostname}: 解析出的 IP 皆非安全位址（私有/迴環/link-local/保留）")
    return safe_ips


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


def _validate_hop(url: str, allowed_hostname: str) -> tuple[str, list[str], int]:
    """驗證單一跳（含初始 URL）：scheme=https、hostname 與
    `allowed_hostname` 完全相同、port=443（或未指定）、解析出至少一個
    非私有 IP。回傳 `(hostname, pinned_ips, port)`（`pinned_ips` 是**全部**
    已驗證安全的候選 IP，見 `_resolve_safe_ips`）；任一項不合格拋出
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
        pinned_ips = _resolve_safe_ips(parsed.hostname)
    except OSError as exc:
        raise SSRFBlockedError(url, str(exc)) from exc
    return parsed.hostname, pinned_ips, port


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
    驗證 scheme/hostname/port/私有 IP，驗證用的每個安全 IP 依序拿去 DNS
    pinning 連線（前一個連線失敗才試下一個，見模組頂部第 4 輪 (a)），
    轉址完全手動處理（不使用 `urllib.request` 的自動轉址機制），最多
    `max_redirects` 跳。回應大小超過 `max_bytes` 截斷（僅套用在最終
    非轉址回應；轉址回應完全不讀 body，見模組頂部第 4 輪 (b)）。

    `extra_headers`（如有）與固定 `User-Agent` 一併附加在請求 header 上
    （如 CoinGecko 選用的 `x-cg-demo-api-key`）；`Host` header 由
    `http.client` 依連線用的 `host`（原始 hostname，非 pinned IP）自動
    附加，不需要也不應該手動再加一次。
    """
    allowed_hostname = urlparse(url).hostname
    if not allowed_hostname:
        raise SSRFBlockedError(url, "URL 缺少 hostname")
    current_url = url
    tls_context = _verified_tls_context()

    for hop in range(max_redirects + 1):
        hostname, pinned_ips, port = _validate_hop(current_url, allowed_hostname)
        parsed = urlparse(current_url)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        headers = {"User-Agent": user_agent}
        if extra_headers:
            headers.update(extra_headers)

        resp = None
        conn = None
        last_connect_error: OSError | None = None
        for pinned_ip in pinned_ips:
            candidate = _PinnedHTTPSConnection(
                pinned_ip, hostname, port, timeout=timeout, context=tls_context,
            )
            try:
                candidate.request("GET", path, headers=headers)
                resp = candidate.getresponse()
            except OSError as exc:
                # 這個已驗證安全的 IP 連不上（如 IPv4-only 環境遇到排序在
                # 前但不可達的 AAAA）——試清單裡下一個已驗證安全的 IP，不
                # 是直接判定整個請求失敗。
                last_connect_error = exc
                candidate.close()
                continue
            conn = candidate
            break

        if resp is None:
            raise OSError(
                f"{hostname}: 已驗證的 {len(pinned_ips)} 個安全 IP 全部連線失敗"
                f"（最後錯誤：{last_connect_error}）"
            ) from last_connect_error

        try:
            status = resp.status
            resp_headers = resp.headers
            if status in _REDIRECT_CODES:
                location = resp_headers.get("Location")
                # 轉址回應完全不讀 body（見模組頂部第 4 輪 (b)）：不需要、
                # 也不該無界排空——直接關閉這一跳的連線即可，下一跳本來就
                # 是全新連線，沒有「殘留資料汙染下一跳」的問題。
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
