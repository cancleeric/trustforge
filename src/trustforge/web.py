"""TrustForge Live Demo web 服務（純 stdlib，App Runner / 容器就緒）。

路由：
  GET /            首頁表單（選幣種/題型/問題）
  GET /healthz     健康檢查（App Runner 用）→ 200 "ok"
  GET /analyze     ?coin=BTC&q=...&type=multi_source[&live=1&token=<TOKEN>][&sample=1] → HTML 報告
  GET /analyze.json 同上參數 → JSON {report, evidence, log}

三檔模式（`data_mode`/`llm_mode` 解耦，見 `pipeline.run`）：
  1. 真資料·$0（**預設**，世界第一重寫 Phase 2 起）：未帶任何 mode 參數即走
     真連接器抓真資料（data_mode=live），但 Bedrock 關閉（llm_mode=off）——
     不依賴 HAS_BEDROCK/token，仍是 $0，credit-safe。這是差異化賣點「真多源
     信任提煉」第一眼就要被看見，故不再需要 `?real=1` 才能觸發（`?real=1`
     仍相容接受，效果與預設相同）。
  2. 離線示範沙盒（`?sample=1`，opt-in）：樣本資料 + Bedrock stub，未設 AWS
     也能跑，$0——想看離線 demo 的人才需要，不再是預設，畫面會清楚標示
     「離線示範」。
  3. 真 Bedrock（`?live=1&token=<TOKEN>`）：需同時滿足設了 BEDROCK_MODEL_ID、
     TRUSTFORGE_LIVE_TOKEN，且請求帶正確 token 參數（用 hmac.compare_digest 比對）。
     `live` 優先於「真資料/離線示範」：滿足時一律走 live。
埠口取自環境變數 PORT（App Runner 預設 8080）。
"""
from __future__ import annotations

import dataclasses
import hmac
import html
import json
import logging
import math
import os
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from .agent.orchestrator import aggregate_trust_by_kind
from .schema import COIN_POOL, QuestionType, comparison_to_markdown
from .brand_logos import coin_logo_html, source_display_name, source_logo_html
from .budget_guard import online_stance_requested, warn_if_bedrock_model_unpriced
from .pipeline import run, run_comparison
from .ledger import PRICING, JsonlLedger, get_ledger
from .cost_model import CONNECTOR_COST_MODEL, SHARED_POOL_LABEL, estimate_connector_cost

try:
    from ._version import VERSION
except Exception:
    VERSION = "dev"

PORT = int(os.getenv("PORT", "8080"))
HAS_BEDROCK = bool(os.getenv("BEDROCK_MODEL_ID"))
# codex HIGH 追加（unpriced model 破壞 cap）：啟動期就檢查 BEDROCK_MODEL_ID
# 是否已在計價表登記，未登記只記警告 log（不 crash）——實際 fail-closed
# 降級離線由 pipeline.run() 每次請求各自判斷，這裡只是讓維運及早在啟動
# log 發現設定錯誤，不必等第一個公開請求才發現整天都在離線。
warn_if_bedrock_model_unpriced()
LIVE_TOKEN = os.getenv("TRUSTFORGE_LIVE_TOKEN", "")
# 累計花費超過此門檻（USD）→ /costs 頁面卡片轉紅告警。未設定則不告警。
COST_BUDGET_USD = os.getenv("COST_BUDGET_USD")

# 前後端分離 Phase 3（task #28，harper CISO 安全審 must-have）：
# ────────────────────────────────────────────────────────────────────
# `TRUSTFORGE_TRUST_PROXY`：**預設關**（維持現況：信任 TCP 對端
# `client_address[0]` 當真實使用者 IP，per-IP 限流 bucket key 不變、
# 行為逐字不變）。只有明確設成 truthy 值才會改讀 `X-Real-IP`／
# `X-Forwarded-For` header 當真實 IP——這兩個 header 是請求端可自由偽造
# 的欄位，**絕對不能無條件信任**，故必須 config-gated opt-in，且只在
# 「python 綁定 127.0.0.1（不對外，前面一定有 nginx）」這個拓樸下才安全
# （見 `main()`：開啟本旗標會強制把監聽 host 收斂成 127.0.0.1，即使
# `TRUSTFORGE_BIND_HOST` 設了別的值，避免有心人繞過 nginx 直接對 python
# 打偽造 header 繞過限流）。
TRUST_PROXY = os.getenv("TRUSTFORGE_TRUST_PROXY", "").strip().lower() in (
    "1", "true", "yes", "on",
)

# CSP 切換旗標：**預設 `legacy`**——沿用既有 zero-JS SSR 的嚴格 CSP
# （`default-src 'none'`），cutover 前行為逐字不變。cutover 當天由 CEO+
# CISO+CPO 三審+老闆簽核後，才把這個環境變數切成 `react`，套用 harper
# 訂的新指令集（給 Vite build 出的 React 前端用，允許 `'self'` script/
# style/connect 等）。同一支程式碼、單一環境變數即可切換，方便快速
# 回滾（見 `deploy/nginx-react.conf` 與 `docs/PLAN-frontend-backend-split.md`
# P3 一週觀察期）。
CSP_MODE = os.getenv("TRUSTFORGE_CSP_MODE", "legacy").strip().lower()

_CSP_LEGACY = (
    "default-src 'none'; "
    "style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com"
)
# harper 指令集（React 前端專用，PLAN §4 + task #28 CISO 複審）。
_CSP_REACT = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


def _resolve_client_ip(direct_ip: str, headers) -> str:
    """依 `TRUST_PROXY` 決定 per-IP 限流要 keyed 用哪個 IP。

    - 關（預設）：原樣回傳 `direct_ip`（TCP 對端 IP，即 `client_address[0]`），
      行為與 cutover 前逐字相同。
    - 開：優先信任 `X-Real-IP`（nginx 設定固定寫死 `$remote_addr`，見
      `deploy/nginx-react.conf`／`deploy/nginx-legacy.conf`），沒有才退回
      `X-Forwarded-For` 取第一段（逗號分隔，取最左——即最原始的用戶端）。
      兩者都沒有才退回 `direct_ip`。
      ⚠️ 只有在 python 綁定 127.0.0.1（見 `main()`）且 nginx 是唯一對外
      入口時，這兩個 header 才可信；`TRUST_PROXY` 本身不做拓樸檢查，
      拓樸保證由 `main()` 的綁定收斂 + 部署腳本共同確保。
    """
    if not TRUST_PROXY:
        return direct_ip
    real_ip = headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    forwarded = headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return direct_ip

# `/status` 頁面「運行時間」：本程序（web worker）匯入這個模組的當下當作起點，
# 不是真的 process 啟動時間（stdlib 無法可靠取得），但對觀測用途已足夠。
_START_TIME = time.time()

# per-IP 限流：每 IP 每 60 秒最多 5 次 **live（真 Bedrock）**請求。
# 這組門檻是為了保護 Bedrock 花費而刻意設緊的——只給真的會燒錢的 live 路徑用，
# 不要跟下面的 real-off 共用（codex HIGH，PR #44：real-off 曾誤套這組緊限流，
# 導致反向代理後所有使用者共用一個來源 IP，5 次/60s 就整批 429）。
_RATE_WINDOW = 60
_RATE_MAX = 5
_rate_lock = threading.Lock()
_rate_buckets: dict[str, list[float]] = {}

# real-off（真資料·$0，PR #44 起的預設檔位）專用 per-IP 限流：獨立於上面
# live 的緊 bucket。real-off 不呼叫 Bedrock、只讀 cache，完全免費——緊限流
# 存在的理由（保護 Bedrock 花費）在這裡不成立，只需擋洪水級濫用（DoS），
# 門檻可以遠比 live 寬鬆，一般使用者連續瀏覽/重整/跑比較分析不會誤中。
_REAL_RATE_WINDOW = 60
_REAL_RATE_MAX = 60
_real_rate_lock = threading.Lock()
_real_rate_buckets: dict[str, list[float]] = {}

# `/status` 專用 per-IP 限流：獨立於上面 live/real 的 bucket，避免互相干擾
# （/status 是唯讀觀測端點，不消耗真連接器/Bedrock 配額，門檻可以更寬鬆）。
_STATUS_RATE_WINDOW = 30
_STATUS_RATE_MAX = 10
_status_rate_lock = threading.Lock()
_status_rate_buckets: dict[str, list[float]] = {}

# #9 online-stance 預算配額硬化：online-stance（`TRUSTFORGE_ONLINE_STANCE` 開
# 啟時，讓 real-off「真資料·$0」檔位的 stance 判斷也打真 Bedrock）專用 per-IP
# 限流，獨立於上面 real-off 的寬鬆 bucket——real-off 本身免費、門檻寬鬆是對的，
# 但一旦疊加 online-stance，同一個 IP 就能間接燒 Bedrock stance 呼叫，需要單獨
# 一組更緊的門檻。刻意比 real-off 緊很多、比 live 寬（stance 呼叫比敘事生成
# 便宜很多），門檻例子：每 IP 每小時 20 次。超量時**不 raise/429**——見
# `_do_analyze`/`_do_comparison` 呼叫端，改成把該次請求的 `force_stance_offline`
# 設 True，誠實 degrade 回離線 stance，而不是讓整個分析請求失敗（#24 不造假：
# 照常回傳結果、只是清楚標明本次未用線上深度分析）。
_ONLINE_STANCE_RATE_WINDOW = 3600
_ONLINE_STANCE_RATE_MAX = 20
_online_stance_rate_lock = threading.Lock()
_online_stance_rate_buckets: dict[str, list[float]] = {}

# `/status` 頁面級 TTL 快取（跨 IP 共用，非安全機制，純降低重算頻率）：資料
# 鮮度矩陣要逐 (source, coin) 讀 cache backend，組合數量多，DynamoDB backend
# 下每次都重算有明顯延遲，也容易被打爆，見 `_render_status_page_cached()`。
_STATUS_CACHE_TTL_SECONDS = 30.0
_status_cache_lock = threading.Lock()
_status_cache: dict[str, float | str] = {"expires_at": 0.0, "html": ""}

# `/status` 連線探測用的保留 canary key。⚠️ `DynamoDBCache` 表結構 PK=`source_id`、
# SK=`coin`，**SK 一律非空字串**（見 `ingestion/cache.py::DynamoDBCache` docstring）
# ——空字串會讓 DynamoDB 直接丟 `ValidationException`（GetItem 對空字串 key
# 屬性一律拒絕），跟「backend 連不上」是完全不同的兩件事，卻會被下面的
# try/except 誤判成 disconnected。兩個欄位都必須給非空、不會撞到真實
# source/coin 的保留值。
_STATUS_PROBE_SOURCE = "__status_probe__"
_STATUS_PROBE_COIN = "__status_probe__"

# Axis C #1（task #23，PLAN docs/PLAN-axisC-snapshots.md）：首頁「多幣總覽」
# 正確讀路徑。
#
# 第一輪（module 級 TTL 快取 + 鎖內 single-flight，比照 `_status_cache`）
# 已被 codex 兩輪 HIGH review 推翻，記錄在此避免之後重踩：
#   - HIGH #1：reader 只檢查 cache entry 是否非空、未驗新鮮度，DynamoDB
#     TTL 刪除是 best-effort（可能延遲數小時到 48 小時），排程停擺時會一
#     直顯示過期判斷——修法是加 `TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS` 新鮮
#     度自驗（見 `_overview_bg_refresh_once()` 沿用）。
#   - HIGH #2（PR #47 二輪 review）：即使新鮮度驗證修好了，request 路徑
#     本身仍在鎖內**同步呼叫 `cache_get()`**——`DynamoDBCache` 的 0.5s
#     timeout 只保證 socket connect/read 有上限，**不涵蓋憑證發現、DNS
#     解析、`cache_get()` 內建的 JsonCacheBackend fallback 檔案 I/O、或任
#     意形式的 backend stall（非拋錯、單純不回應）**——這些情況下 request
#     執行緒仍會被真的卡住，且同一顆 single-flight 鎖會讓併發首頁 request
#     全部排隊卡住，等於變相把 P3 事故的「ThreadPool 孤兒累積」換成「鎖
#     隊列累積」，本質是同一個可用性 class 的問題：**I/O 出現在 request
#     路徑上**。
#
# 正解（by construction，把所有 I/O 徹底移出 request 路徑）：
#   1. 唯一一顆 module 級變數 `_overview_html`（`str | None`）—— request
#      路徑**只**在持有 `_overview_state_lock`（極短、microsecond，鎖內
#      絕不做任何 I/O）的情況下讀這個變數，讀完立刻放鎖。
#   2. 唯一一條背景 daemon thread（`_overview_bg_loop`，首次首頁 request
#      時懶啟動、`_ensure_overview_bg_thread_started()` 用
#      `_overview_bg_thread_lock` + `is_alive()` 保證只會啟動一次），每
#      `_OVERVIEW_BG_INTERVAL_SECONDS` 秒做一次
#      `cache_get(__trust_overview_html__)` + 新鮮度驗證（沿用
#      `TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS`），成功且新鮮才更新
#      `_overview_html`；backend stall/失敗/過期一律讓它變成 `None`（不顯
#      示過期或壞資料），見 `_overview_bg_refresh_once()`。**所有 I/O、
#      stall、timeout 只發生在這條背景 thread 身上**，它卡死也只影響它
#      自己下一輪要不要重試，完全不會拖到任何一個首頁 request。
#   3. 首頁 request 因此永遠即時（zero I/O）——backend 再怎麼 stall、憑證
#      /DNS 再怎麼有問題，都碰不到 request 執行緒。
#
#   - HIGH #3（PR #47 三輪 review，最後一個微妙變體）：即使 I/O 全部移到
#     背景 thread，新鮮度也只在**背景 thread `cache_get()` 成功返回後**才
#     被檢查一次——如果背景那一輪讀取本身**永久 stall**（唯一一條 worker
#     thread 卡住不返回、不拋錯，也不會有替補 thread 接手），它永遠到不了
#     「判過期就設 `None`」那行程式碼。此時 `_overview_html` 會停在**上一
#     輪成功時寫入的舊值**，且 request 路徑本身完全不檢查這個值的年齡，
#     於是舊的信任判斷會無限期地繼續顯示，即使早就超過新鮮窗——對信任產
#     品是致命的「過期當即時」。
#     修法：**in-memory 值連同 expiry 時間戳一起存**（`_overview_html` +
#     `_overview_expiry_epoch`，expiry = 該 blob 的 `fetched_at` +
#     `TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS`，背景 thread 寫值時一併寫
#     expiry）。request 路徑讀完後、回傳前，多做**一次純記憶體時鐘比較**
#     （`time.time() <= expiry`，零 I/O、鎖內完成）——即使背景 thread 永久
#     stall、再也不會有新的一輪去把值設回 `None`，這顆 in-memory 現貨也會
#     在時鐘走到 expiry 那一刻**自己失效**，不依賴背景 thread 主動清除。
_OVERVIEW_BG_INTERVAL_SECONDS = 30.0  # 背景刷新頻率；跟首頁「即不即時」
# 完全無關（首頁只讀 in-memory 現貨），純粹是「總覽資料多快跟上寫入者」
# 的取捨，30–60s 區間內任一值皆可，取下限求新鮮。

_overview_state_lock = threading.Lock()  # 只護 `_overview_html`／
# `_overview_expiry_epoch` 這兩個變數本身的讀寫，鎖內絕不做 I/O（見上）。
_overview_html: str | None = None  # in-memory 現貨；`None` = 目前沒有可
# 顯示的新鮮總覽（首次啟動前、或背景 thread 判定 stale/失敗）。
_overview_expiry_epoch: float = 0.0  # 這顆現貨的絕對到期時間（`time.time()`
# 座標系，等於寫入當下 blob 的 `fetched_at + TRUST_SNAPSHOT_FRESH_WINDOW_
# SECONDS`）。request 路徑讀取後會拿現在時間跟這個值比較（HIGH #3
# 修復）——就算背景 thread 之後永久 stall、再也不會來更新/清空
# `_overview_html`，這個絕對時間戳到了照樣讓現貨在 request 端自己失效。

_overview_bg_thread_lock = threading.Lock()  # 只護「有沒有啟動背景
# thread」這個判斷本身（`is_alive()` 檢查 + `Thread.start()`），同樣不是
# I/O——`Thread.start()` 本身非阻塞，實際工作在新 thread 裡非同步跑。
_overview_bg_thread: threading.Thread | None = None
_overview_bg_stop_event: threading.Event | None = None  # 供測試用：設了
# 這個事件，目前這條背景 thread 下一次檢查迴圈條件時就會自然結束（見
# `_overview_bg_loop()`）；每次啟動都是全新的 `Event()`，不重用舊的（見
# `_ensure_overview_bg_thread_started()` 理由）。

# 首頁總覽背景刷新專用的 DynamoDB 連線 timeout（秒）——`DynamoDBCache.__init__`
# 三個 timeout/重試參數專為此保留（見該類別 docstring「這三個參數留著給
# Axis C 用」）：`get_cache_backend()`（給排程器用）刻意不帶
# timeout（容錯優先，讀失敗頂多多一次 cache-miss 降級）。這個短 timeout
# 現在只保護**背景 thread**（減少它自己卡住的時間），首頁 request 路徑
# 本身已經零 I/O、不受這個 timeout 涵不涵蓋 stall 影響（見上方 HIGH #2）。
_HOME_OVERVIEW_TIMEOUT_SECONDS = 0.5

# `/api/status` 專用的 DynamoDB 連線 timeout/重試上限——codex 複審 HIGH
# （production 安全）：`/api/status` 是同步 request 路徑（監控/使用者主動
# 打），跟首頁背景 thread 一樣不能吃 SDK 預設可達分鐘級的 timeout/重試；
# 額度沿用 `scripts/fetch_scheduler.py::_probe_cache_backend()` 既有的
# `_PROBE_DYNAMODB_*` 慣例（connect/read 各 3s、max_attempts=2）。
# ⚠️ 光有短 timeout 還不夠：`get_freshness_snapshot()` 逐 (source, coin)
# 迴圈約 115 格，若每格都重新嘗試一次 primary，115 × (3s+3s) × 2 attempts
# 仍是分鐘級——真正的 bounded worst-case 要靠
# `get_freshness_snapshot(..., circuit_breaker=True)`：同一次請求內第一次
# 偵測到 primary 失敗後，後續格子直接跳過 primary，兩者要一起用（見
# `_status_cache_backend()`／`_handle_api_status()`）。
_STATUS_CACHE_CONNECT_TIMEOUT_SECONDS = 3.0
_STATUS_CACHE_READ_TIMEOUT_SECONDS = 3.0
_STATUS_CACHE_MAX_ATTEMPTS = 2

_PAGE = """<!doctype html><html lang="zh-Hant" data-theme="dark"><head><meta charset="utf-8">

<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TrustForge — 加密市場分析 AI Agent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
 :root{{--tf-bg:#0d1117;--tf-card:#161b22;--tf-border:#30363d;--tf-text:#e6edf3;--tf-muted:#8b949e;--tf-muted2:#6e7681;--tf-hdr-g1:#12171e;--tf-hdr-g2:#0f141a;--tf-inset:#0f141a;--tf-text2:#c9d1d9;--tf-fs-h1:1.6rem;--tf-fw-h1:700;--tf-fs-h2:1.3rem;--tf-fw-h2:700;--tf-fs-h3:1rem;--tf-fw-h3:600;--tf-fs-h4:.85rem;--tf-fw-h4:600;--tf-fs-body:1rem;--tf-lh-body:1.55}}
 :root[data-theme="light"]{{--tf-bg:#f6f8fa;--tf-card:#ffffff;--tf-border:#d0d7de;--tf-text:#1f2328;--tf-muted:#57606a;--tf-muted2:#6e7781;--tf-hdr-g1:#ffffff;--tf-hdr-g2:#f6f8fa;--tf-inset:#eef2f6;--tf-text2:#3d444d}}
 *{{box-sizing:border-box}}
 body{{font-family:'IBM Plex Sans',-apple-system,"PingFang TC",sans-serif;font-size:var(--tf-fs-body);line-height:var(--tf-lh-body);max-width:1280px;margin:2rem auto;padding:0 1rem;color:var(--tf-text);background:var(--tf-bg);-webkit-font-smoothing:antialiased}}
 h1{{margin-bottom:.2rem;font-size:var(--tf-fs-h1);font-weight:var(--tf-fw-h1);letter-spacing:-.01em;line-height:1.25}}
 h2{{font-size:var(--tf-fs-h2);font-weight:var(--tf-fw-h2);line-height:1.3}}
 h3{{font-size:var(--tf-fs-h3);font-weight:var(--tf-fw-h3);line-height:1.35}}
 h4{{font-size:var(--tf-fs-h4);font-weight:var(--tf-fw-h4)}}
 .sub{{color:var(--tf-muted);margin-top:0}}
 a{{color:#1f6feb}}
 header.tf-hdr{{display:flex;align-items:center;gap:14px;padding:.7rem 1rem;border:1px solid var(--tf-border);border-radius:12px;background:linear-gradient(var(--tf-hdr-g1),var(--tf-hdr-g2));margin-bottom:1rem;flex-wrap:wrap}}
 .tf-logo{{font-weight:700;font-size:1.05rem;letter-spacing:-.2px;color:var(--tf-text)}}
 .tf-logo b{{color:#1f6feb}}
 .tf-version{{font-family:'IBM Plex Mono',monospace;font-size:.7rem;color:var(--tf-muted);border:1px solid var(--tf-border);border-radius:5px;padding:.15rem .5rem}}
 .tf-mode-badge{{display:inline-flex;align-items:center;gap:6px;font-family:'IBM Plex Mono',monospace;font-size:.7rem;border-radius:5px;padding:.2rem .55rem;border:1px solid var(--tf-border)}}
 .tf-mode-badge.active.tf-live{{color:#3fb950;background:rgba(63,185,80,.12);border-color:rgba(63,185,80,.4)}}
 .tf-mode-badge.active.tf-offline{{color:var(--tf-muted);background:rgba(139,148,158,.10);border-color:var(--tf-border)}}
 .tf-mode-badge.active.tf-real{{color:#79c0ff;background:rgba(31,111,235,.12);border-color:rgba(31,111,235,.4)}}
 .tf-mode-badge.tf-static{{color:var(--tf-muted2);background:transparent;border-color:var(--tf-border);opacity:.7}}
 .tf-mode-dot{{width:7px;height:7px;border-radius:50%;background:currentColor;flex-shrink:0;animation:tf-pulse 1.8s infinite}}
 @keyframes tf-pulse{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}
 .tf-hdr-spacer{{flex:1}}
 .tf-costlink{{font-family:'IBM Plex Mono',monospace;font-size:.75rem;color:var(--tf-muted);text-decoration:none;border:1px solid var(--tf-border);border-radius:6px;padding:.3rem .7rem;white-space:nowrap}}
 .tf-costlink:hover{{border-color:#1f6feb;color:var(--tf-text)}}
 .tf-hdr-status-link{{font-size:.72rem;color:var(--tf-muted2);text-decoration:none;white-space:nowrap;opacity:.75}}
 .tf-hdr-status-link:hover{{color:var(--tf-muted);opacity:1;text-decoration:underline}}
 .tf-hdr-version{{font-family:'IBM Plex Mono',monospace;font-size:.7rem;color:var(--tf-muted2);opacity:.7;white-space:nowrap;margin-right:.5rem}}
 .tf-layout{{display:grid;grid-template-columns:290px minmax(0,1fr);gap:1.2rem;align-items:start}}
 .tf-query-panel{{position:sticky;top:1rem;background:var(--tf-card);border:1px solid var(--tf-border);border-radius:12px;padding:1.2rem;display:flex;flex-direction:column;gap:.9rem}}
 .tf-query-panel h3{{margin:0;font-family:'IBM Plex Mono',monospace;font-size:.72rem;font-weight:700;color:var(--tf-muted2);text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid var(--tf-border);padding-bottom:.6rem}}
 .tf-logo-mark{{color:#1f6feb;margin-right:.15rem}}
 .tf-run-stats{{border-top:1px solid var(--tf-border);padding-top:.8rem;margin-top:.1rem;display:flex;flex-direction:column;gap:.35rem}}
 .tf-run-stats h3{{margin-bottom:.4rem}}
 .tf-stat-row{{display:flex;justify-content:space-between;gap:.6rem;font-size:.78rem}}
 .tf-stat-k{{color:var(--tf-muted);font-family:'IBM Plex Mono',monospace;font-size:.7rem;letter-spacing:.03em}}
 .tf-stat-v{{color:var(--tf-text);font-family:'IBM Plex Mono',monospace;font-weight:600}}
 .tf-dashboard{{min-width:0}}
 form{{background:transparent;border:0;padding:0;display:flex;flex-direction:column;gap:.8rem;align-items:stretch}}
 label{{display:block;font-size:.8rem;color:var(--tf-muted);margin-bottom:.2rem}}
 select,input,textarea,button{{width:100%;padding:.5rem .7rem;border:1px solid var(--tf-border);border-radius:8px;font-size:1rem;background:var(--tf-bg);color:var(--tf-text);font-family:inherit}}
 textarea[name=q]{{min-width:0;min-height:5.2rem;resize:vertical;line-height:1.4}}
 button{{background:#1f6feb;color:#fff;border:0;cursor:pointer;font-weight:600;letter-spacing:.01em;position:relative}}
 button .tf-kbd{{opacity:.75;font-family:'IBM Plex Mono',monospace;margin-left:.3rem}}
 /* 世界第一重寫 Phase 3：純 CSS loading 回饋——zero-JS 頁面（CSP `default-src 'none'`
    已擋死所有 script），全頁 GET 表單送出到瀏覽器完成導航前無法用 JS 插入 spinner，
    只能靠 `:active`（滑鼠按下/觸控/多數瀏覽器對 Enter 觸發的送出也會套用）在舊頁面
    卸載前的最後幾個 render frame 換上 disabled 外觀＋spinner＋提示文字，讓使用者
    在等待網路請求時至少有即時視覺回饋，不是完全白屏。不改變 `<form>` 本身的 GET
    行為、不影響 `_do_analyze` 參數解析。

    商業級修復（防呆補強）：誠實標註零 JS/CSP 下的天花板——`button[type=
    submit]:active` 只能在按住/觸控/鍵盤啟動的當下短暫套用，瀏覽器一放開
    就結束，**無法**真正做到「送出後整個禁用直到新頁載入」（那需要 JS 監聽
    submit 事件切 disabled 屬性，CSP `default-src 'none'` 完全擋死）。這裡
    追加 `touch-action:manipulation`：關掉行動瀏覽器對這顆按鈕的雙擊縮放
    手勢判斷延遲，是業界常見、真實有效的零 JS 手機端「誤觸連點」緩解手法
    （非萬用解，但目前架構下可行且真的有效果）。

    codex MEDIUM 複審更正（誠實聲明，別再誤導）：`:active` 一放開就恢復
    可點，導航完成前使用者仍能再點一次 → **這不是真正的防重複送出**，只是
    zero-JS 架構下 best-effort 的視覺 loading 回饋（有勝於無，但不保證）。
    先前這裡曾寫「`/analyze` 是唯讀 GET，重複送出不會有破壞性後果，殘餘
    風險可接受」——這個推論本身沒錯（GET 不寫入、不會資料髒污/重複扣款），
    但誤導在於：GET 唯讀跟「防不防得住重複執行」是兩回事，**不代表重複
    送出沒有成本風險**。現況（離線 sample、`llm_mode=off`）重複送出頂多
    白工重算一次確定性結果，$0 代價，可以接受；但 Bedrock 開啟後
    （`llm_mode=bedrock`）每次重複送出都是真實 token 成本，`:active` 這個
    best-effort 視覺回饋完全防不住連點/導航中再點造成的重複計費。真正的
    防重複送出需要 JS 監聽 submit 事件（會破壞現有 strict CSP `default-src
    'none'`）或 server 端 idempotency key／去重機制，兩者都是架構層級決策
    （非本輪 CTO 快修範圍），已列為 follow-up 記錄在
    `docs/OPTIMIZATION-PLAN-weakness.md` 第 5 項，**Bedrock 正式開啟前必須
    先做**。 */
 button[type=submit]{{touch-action:manipulation}}
 button[type=submit]:active{{display:flex;align-items:center;justify-content:center;gap:.5rem;cursor:progress;pointer-events:none;background:#1a5fc7}}
 button[type=submit]:active .tf-btn-label{{display:none}}
 button[type=submit]:active::before{{content:"";width:14px;height:14px;flex-shrink:0;border-radius:50%;border:2px solid rgba(255,255,255,.35);border-top-color:#fff;animation:tf-spin .6s linear infinite}}
 button[type=submit]:active::after{{content:"正在整合多源資料…";font-size:.85rem}}
 @keyframes tf-spin{{to{{transform:rotate(360deg)}}}}
 .badge{{display:inline-block;background:rgba(31,111,235,.14);border:1px solid rgba(31,111,235,.4);border-radius:6px;padding:.1rem .5rem;font-size:.75rem;color:#79c0ff}}
 pre{{background:var(--tf-card);border:1px solid var(--tf-border);border-radius:12px;padding:1rem;white-space:pre-wrap;word-break:break-word;color:var(--tf-text)}}
 table{{border-collapse:collapse;width:100%;background:var(--tf-card);font-size:.85rem;color:var(--tf-text)}} td,th{{border:1px solid var(--tf-border);padding:.4rem;text-align:left}}
 .j{{font-size:1.1rem;font-weight:600}} .conf{{color:#1f6feb}}
 .tf-section{{background:var(--tf-card);border:1px solid var(--tf-border);border-radius:10px;padding:1rem;margin:.8rem 0}}
 .tf-section h3{{margin-top:0;font-size:1rem;border-bottom:1px solid var(--tf-border);padding-bottom:.4rem;margin-bottom:.7rem;color:var(--tf-text)}}
 .tf-bar-wrap{{display:inline-block;vertical-align:middle;width:90px;height:10px;background:var(--tf-bg);border:1px solid var(--tf-border);border-radius:5px;overflow:hidden;margin-right:4px}}
 .tf-bar{{height:100%;border-radius:5px}}
 .tf-low{{display:inline-block;background:rgba(248,81,73,.14);border:1px solid rgba(248,81,73,.4);color:#f85149;border-radius:4px;padding:.1rem .35rem;font-size:.68rem;font-weight:600;margin-left:4px}}
 .tf-info{{display:inline-block;background:rgba(139,148,158,.14);border:1px solid rgba(139,148,158,.4);color:var(--tf-muted);border-radius:4px;padding:.1rem .35rem;font-size:.68rem;font-weight:600;margin-left:4px}}
 .tf-conf-wrap{{background:var(--tf-inset);border:1px solid var(--tf-border);border-radius:8px;padding:.8rem;margin:.5rem 0}}
 .tf-conf-big{{font-size:1.6rem;font-weight:700;margin:0 0 .2rem}}
 .tf-src-pill{{display:inline-block;font-weight:600;font-size:.82rem;color:var(--tf-text);background:var(--tf-bg);border:1px solid var(--tf-border);border-radius:12px;padding:.05rem .6rem;margin-right:.4rem}}
 .tf-ev-date{{font-family:'IBM Plex Mono',monospace;font-size:.72rem;color:var(--tf-muted2)}}
 .tf-ev-summary{{cursor:pointer}}
 .tf-ev-body{{padding-top:.2rem}}
 .tf-dash-hdr{{display:flex;align-items:center;gap:.6rem;margin-bottom:.6rem;flex-wrap:wrap}}
 .tf-coin-badge{{font-family:'IBM Plex Mono',monospace;font-weight:700;font-size:1rem;color:var(--tf-text);background:var(--tf-card);border:1px solid var(--tf-border);border-radius:8px;padding:.25rem .7rem}}
 .tf-dash-sep{{color:var(--tf-border)}}
 .tf-dash-q{{color:var(--tf-muted);font-size:.9rem}}
 .tf-hero-row{{display:grid;grid-template-columns:auto minmax(0,1fr);gap:1.2rem;align-items:center}}
 .tf-step{{border-left:4px solid var(--tf-border);padding:.3rem 0 .3rem .9rem;margin:.5rem 0}}
 .tf-step li{{margin:.25rem 0}}
 .tf-step-badge{{font-family:'IBM Plex Mono',monospace;font-size:.68rem;color:var(--tf-muted2);font-weight:400;margin-left:.4rem}}
 .tf-tier-pill{{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:.68rem;font-weight:600;border-radius:4px;padding:.05rem .4rem;margin-right:.3rem;text-transform:uppercase;vertical-align:middle;background:color-mix(in srgb,currentColor 14%,transparent)}}
 .tf-div-grid{{display:grid;grid-template-columns:1fr 34px 1fr;gap:0;align-items:stretch;margin-top:.6rem}}
 .tf-div-side{{border-radius:9px;padding:.7rem .8rem}}
 .tf-div-bull{{background:rgba(63,185,80,.08);border:1px solid rgba(63,185,80,.35)}}
 .tf-div-bear{{background:rgba(248,81,73,.08);border:1px solid rgba(248,81,73,.35)}}
 .tf-div-mid{{display:flex;align-items:center;justify-content:center;color:#f85149;font-weight:700;font-size:1rem}}
 .tf-div-tag{{font-family:'IBM Plex Mono',monospace;font-size:.68rem;font-weight:600;border-radius:4px;padding:.1rem .5rem;margin-right:.4rem}}
 .tf-home-hero h1{{font-size:1.6rem;margin:0 0 .5rem}}
 .tf-hero-cta{{display:inline-block;background:#1f6feb;color:#fff;text-decoration:none;font-weight:600;padding:.55rem 1.1rem;border-radius:8px;font-size:.9rem;margin-top:.3rem}}
 .tf-hero-cta:hover{{background:#3b82f6}}
 .tf-hero-cta.tf-hero-cta-ghost{{background:transparent;border:1px solid var(--tf-border);color:var(--tf-text)}}
 .tf-hero-cta.tf-hero-cta-ghost:hover{{border-color:#1f6feb;color:#79c0ff}}
 .tf-home-steps{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem;margin-top:.6rem}}
 .tf-home-step{{background:var(--tf-inset);border:1px solid var(--tf-border);border-radius:8px;padding:.8rem;cursor:default}}
 .tf-home-step .sub{{font-size:.8rem;margin:.3rem 0 0}}
 /* 商業級修復：多幣總覽卡（`a.tf-overview-card`，見 `fetch_scheduler.py::
    _render_overview_html()`）已是真 `<a href="/analyze?...">`，但缺 CSS
    的 `cursor:pointer`／hover 回饋，使用者滑過去看不出能點，跟旁邊純資訊
    的 `.tf-home-step` 步驟卡（不可點）視覺上無法區分。選用 `a.tf-overview-
    card` 限定選擇器（而非泛用 `.tf-overview-card`）：非白名單幣種防呆時
    仍會渲染成純 div 版本卡片（class 相同、標籤換成 div，見同一支檔案
    `_render_overview_html` docstring），那種情況本來就不可點，不該套用
    pointer/hover，用 `a` 前綴精準只命中真正可點的卡片。hover 用
    `box-shadow` 而非 `border-color`——卡片本身在 inline style 上已寫死
    `border:1px solid var(--tf-border)`，inline style 的優先度高於外部
    stylesheet 的同屬性宣告，`box-shadow`／`transform` 不受影響，才會實際
    生效。 */
 a.tf-overview-card{{cursor:pointer;transition:box-shadow .15s,transform .15s}}
 a.tf-overview-card:hover{{box-shadow:0 0 0 1px #1f6feb,0 4px 14px rgba(31,111,235,.18);transform:translateY(-1px)}}
 @media (max-width:900px){{
  body{{margin:1rem auto}}
  header.tf-hdr{{flex-direction:column;align-items:flex-start}}
  .tf-hdr-spacer{{display:none}}
  .tf-layout{{grid-template-columns:1fr}}
  .tf-query-panel{{position:static}}
  .tf-hero-row{{grid-template-columns:1fr}}
  .tf-div-grid{{grid-template-columns:1fr}}
  .tf-div-mid{{padding:.3rem 0}}
  .tf-home-steps{{grid-template-columns:1fr}}
 }}
 /* 世界第一重寫 Phase 3：375px 手機補強。表格橫向捲（而非強制欄位換行/擠壓）
    ——`.tf-section` 是所有表格既有的統一容器（見 evidence 清單／資料鮮度矩陣／
    連接器用量／成本帳本各表），讓容器本身可橫向捲動即可在不改任何 HTML 結構
    的前提下讓表格內容在窄螢幕保持可讀，不強制每個 <td> 換行擠壞版面。 */
 @media (max-width:480px){{
  body{{padding:0 .6rem}}
  .tf-section{{padding:.8rem;overflow-x:auto}}
  .tf-section table{{min-width:640px}}
  .tf-dash-hdr{{gap:.4rem}}
  .tf-coin-badge{{font-size:.9rem;padding:.2rem .55rem}}
 }}
</style></head><body>
{header}
<div class="tf-layout">
 <aside class="tf-query-panel" id="tf-query-console">
  <h3>Query Console</h3>
  <p class="sub" style="margin:0;font-size:.8rem">加密市場分析 AI Agent — 多源資訊的信任提煉</p>
  <form action="/analyze" method="get">
   <div><label>幣種</label><select name="coin">{coins}</select></div>
   <div><label>比較幣種<span style="font-weight:400;color:var(--tf-muted2)"> ｜ 題型選「比較分析」時使用</span></label><select name="coin2">{coins2}</select></div>
   <div><label>題型</label><select name="type">{types}</select></div>
   <div><label>問題</label><textarea name="q" rows="3">{default_query}</textarea></div>
   <button type="submit"><span class="tf-btn-label">Run analysis<span class="tf-kbd">&#8629;</span></span></button>
  </form>
  {run_stats}
 </aside>
 <main class="tf-dashboard">
{body}
 </main>
</div>
</body></html>"""


class TooManyRequests(Exception):
    """per-IP 限流超量時拋出，對應 HTTP 429。"""


# 三個限流 bucket（`_rate_buckets`/`_real_rate_buckets`/`_status_rate_buckets`）共用的硬上限：
# 光靠「修剪單一 IP 內過期的時間戳」不夠——bucket dict 本身的 *key 數量*
# （歷史上出現過的不同 IP 數）才是真正的記憶體風險，尤其 IPv6/偽造來源 IP
# 高頻換位的情境下，dict 會無限增長成一個記憶體耗盡向量（防 DoS 反成
# DoS）。達上限時：先掃掉整批「視窗內已完全無動靜」的 IP（O(n)，但只在
# 達上限時才觸發，攤還成本有界）；掃完仍超過上限（同一視窗內大量不同 IP
# 高頻打），退化成逐出最舊活動時間的 IP 直到降回上限之下——犧牲極少數最
# 不活躍 IP 的限流狀態換取整體記憶體有界，是刻意的 trade-off。
_RATE_LIMIT_MAX_TRACKED_IPS = 5000


def _evict_stale_rate_buckets(
    buckets: dict[str, list[float]], window: float, now: float, max_tracked_ips: int
) -> None:
    """呼叫端已持有對應的 lock。bucket 數未達上限時直接返回，不做任何事
    （正常情況下零額外成本）。"""
    if len(buckets) < max_tracked_ips:
        return
    stale_ips = [ip for ip, ts in buckets.items() if not any(now - t < window for t in ts)]
    for ip in stale_ips:
        del buckets[ip]
    while len(buckets) >= max_tracked_ips:
        oldest_ip = min(buckets, key=lambda k: max(buckets[k], default=0.0))
        del buckets[oldest_ip]


def _check_live_rate_limit(ip: str) -> None:
    """IP 在滑動視窗內超過 _RATE_MAX 次 live 請求 → raise TooManyRequests。"""
    now = time.time()
    with _rate_lock:
        _evict_stale_rate_buckets(_rate_buckets, _RATE_WINDOW, now, _RATE_LIMIT_MAX_TRACKED_IPS)
        ts = [t for t in _rate_buckets.get(ip, []) if now - t < _RATE_WINDOW]
        if len(ts) >= _RATE_MAX:
            raise TooManyRequests(f"請求過於頻繁，請 {_RATE_WINDOW} 秒後再試")
        ts.append(now)
        _rate_buckets[ip] = ts


def _check_real_rate_limit(ip: str) -> None:
    """real-off（真資料·$0，預設檔位）專用 per-IP 限流（獨立 bucket，見模組
    頂部 `_REAL_RATE_*` 常數）：IP 在滑動視窗內超過 `_REAL_RATE_MAX` 次請求
    → raise `TooManyRequests`。

    刻意不共用 `_check_live_rate_limit` 的緊 bucket/門檻（codex HIGH，PR
    #44）：那組 5 次/60s 是為了保護真的會燒錢的 Bedrock 配額，real-off 只讀
    cache、不打 Bedrock，完全免費，不該套一樣緊的限流——尤其 real-off 現在
    是 `/analyze` 的預設檔位，一般使用者不帶任何參數就會走到這裡，若沿用
    live 的緊門檻，反向代理後所有使用者共用一個來源 IP，5 次/60s 後就會
    整批被 429。這裡改成 DoS 洪水級門檻（見 `_REAL_RATE_MAX`），只擋高頻
    濫用，正常瀏覽/連跑幾次不會誤中。"""
    now = time.time()
    with _real_rate_lock:
        _evict_stale_rate_buckets(
            _real_rate_buckets, _REAL_RATE_WINDOW, now, _RATE_LIMIT_MAX_TRACKED_IPS
        )
        ts = [t for t in _real_rate_buckets.get(ip, []) if now - t < _REAL_RATE_WINDOW]
        if len(ts) >= _REAL_RATE_MAX:
            raise TooManyRequests(f"請求過於頻繁，請 {_REAL_RATE_WINDOW} 秒後再試")
        ts.append(now)
        _real_rate_buckets[ip] = ts


def _check_status_rate_limit(ip: str) -> None:
    """`/status` 專用 per-IP 限流（獨立 bucket，見模組頂部 `_STATUS_RATE_*`
    常數）：IP 在滑動視窗內超過 `_STATUS_RATE_MAX` 次請求 → raise
    `TooManyRequests`。防的是「資料鮮度矩陣逐 (source,coin) 讀 cache 的頁面
    被當 DoS 高頻打」，跟 `_check_live_rate_limit` 保護真連接器/Bedrock 配額
    的目的不同，故不共用同一組 bucket/門檻（但共用同一套 `_evict_stale_rate_buckets`
    上限保護邏輯）。"""
    now = time.time()
    with _status_rate_lock:
        _evict_stale_rate_buckets(
            _status_rate_buckets, _STATUS_RATE_WINDOW, now, _RATE_LIMIT_MAX_TRACKED_IPS
        )
        ts = [t for t in _status_rate_buckets.get(ip, []) if now - t < _STATUS_RATE_WINDOW]
        if len(ts) >= _STATUS_RATE_MAX:
            raise TooManyRequests(f"請求過於頻繁，請 {_STATUS_RATE_WINDOW} 秒後再試")
        ts.append(now)
        _status_rate_buckets[ip] = ts


def _check_online_stance_rate_limit(ip: str) -> None:
    """online-stance 專用 per-IP 限流（獨立 bucket，見模組頂部
    `_ONLINE_STANCE_RATE_*` 常數）：IP 在滑動視窗內超過 `_ONLINE_STANCE_RATE_MAX`
    次請求 → raise `TooManyRequests`。

    只在 `online_stance_requested()` 為真（`TRUSTFORGE_ONLINE_STANCE` 開啟）時
    由呼叫端（`_do_analyze`/`_do_comparison`）呼叫；呼叫端會 catch 這個例外並
    轉成 `force_stance_offline=True` 誠實 degrade，而不是讓例外往外傳播成
    HTTP 429（#9 online-stance 預算配額硬化 item 4：度耗盡/限流時分析仍要
    正常回傳，只是誠實標明本次未用線上深度分析，不是報錯）。"""
    now = time.time()
    with _online_stance_rate_lock:
        _evict_stale_rate_buckets(
            _online_stance_rate_buckets, _ONLINE_STANCE_RATE_WINDOW, now,
            _RATE_LIMIT_MAX_TRACKED_IPS,
        )
        ts = [
            t for t in _online_stance_rate_buckets.get(ip, [])
            if now - t < _ONLINE_STANCE_RATE_WINDOW
        ]
        if len(ts) >= _ONLINE_STANCE_RATE_MAX:
            raise TooManyRequests(f"請求過於頻繁，請 {_ONLINE_STANCE_RATE_WINDOW} 秒後再試")
        ts.append(now)
        _online_stance_rate_buckets[ip] = ts


def _online_stance_force_offline(client_ip: str) -> bool:
    """供 `_do_analyze`/`_do_comparison` 呼叫：online-stance 未啟用時直接回
    `False`（零開銷、行為與加入本護欄前逐字相同）；已啟用時跑 per-IP 限流，
    超量回 `True`（呼叫端應把這次請求的 `force_stance_offline` 設 True，誠實
    degrade 回離線 stance），未超量回 `False`。刻意用回傳值而非例外，讓呼叫端
    不需要額外包一層 try/except（也避免跟既有 `TooManyRequests` → 429 的錯誤
    路徑混淆——這個護欄的語意是「degrade」不是「拒絕」）。"""
    if not client_ip or not online_stance_requested():
        return False
    try:
        _check_online_stance_rate_limit(client_ip)
    except TooManyRequests:
        return True
    return False


# 世界第一重寫 Phase 2：預設查詢文案（表單 textarea / _do_analyze・
# _do_comparison 的 q-缺 fallback）改回 date-agnostic 常數，不再內嵌任何
# 具體日期。
#
# 背景（codex MEDIUM #2，PR #44）：先前版本用 `_hoya_baseline_phrase(coin)`
# 把「近兩週」換成「基準資料涵蓋至 {日期}」動態日期，但這串文字是塞進
# **表單 textarea 的預填值**——zero-JS 頁面裡，使用者切換幣種下拉選單時
# textarea 內容不會跟著更新；一旦送出表單，textarea 當時顯示的日期字串
# 就整段變成 `q` 參數送進 `_do_analyze`。而 `_do_analyze`/`_do_comparison`
# 只在 `q` 完全缺席時才會用當次請求的 coin 重新產生文案——表單正常送出
# 一定帶著 `q`，所以真實使用路徑永遠拿到 textarea 預填當下的舊日期，
# 選 ETH 送出卻夾帶 BTC 的日期，日期與實際分析幣種對不上。
#
# 正解（by construction 封閉整個 class）：查詢文字本身不做任何具體時間
# 宣稱，只用「近期」這種模糊措辭；精確、可回溯、各幣正確配對的日期改由
# 結果頁 `_render_price_provenance()` 專職負責——那裡直接讀當次分析
# 用到的 `evidence`（`ohlcv-csv`／`coingecko-price`），保證日期一定跟著
# 實際分析的幣種走，不會有「查詢文字日期」與「證據日期」不同步的可能。
_DATE_AGNOSTIC_QUERY_SUFFIX = "近期市場狀況"


def _opts(values, labels=None, selected=None):
    labels = labels or {v: v for v in values}
    return "".join(
        f'<option value="{html.escape(v)}"'
        f'{" selected" if selected is not None and v == selected else ""}>'
        f'{html.escape(labels[v])}</option>'
        for v in values
    )


def _trust_bar(trust: float) -> str:
    """SVG 弧形信任量表（依層級上色：高綠/中橙/低紅）。取代舊版 CSS 橫條。"""
    pct = max(0.0, min(1.0, trust))
    if trust >= 0.7:
        color, label = "#3fb950", "高"
    elif trust >= 0.3:
        color, label = "#d9832a", "中"
    else:
        color, label = "#f85149", "低"
    r = 14
    circumference = 2 * math.pi * r
    arc_val = circumference * pct
    return (
        f'<span style="display:inline-flex;align-items:center;gap:.35rem;vertical-align:middle">'
        f'<svg width="34" height="34" viewBox="0 0 34 34" style="flex-shrink:0">'
        f'<circle cx="17" cy="17" r="{r}" fill="none" stroke="var(--tf-border)" stroke-width="4"></circle>'
        f'<circle cx="17" cy="17" r="{r}" fill="none" stroke="{color}" stroke-width="4" '
        f'stroke-linecap="round" stroke-dasharray="{arc_val:.2f} {circumference:.2f}" '
        f'transform="rotate(-90 17 17)"></circle>'
        f'</svg>'
        f'<span style="color:{color};font-size:.8rem">{trust:.2f} {label}</span>'
        f'</span>'
    )


def _conf_gauge(confidence: float, label: str) -> str:
    """整體信心視覺化：SVG 弧形 Trust Score gauge（270° 弧）+ 大字標籤。"""
    pct = max(0.0, min(1.0, confidence))
    if confidence >= 0.7:
        color = "#3fb950"
    elif confidence >= 0.45:
        color = "#d9832a"
    else:
        color = "#f85149"
    r = 72
    circumference = 2 * math.pi * r
    arc_span = 0.75 * circumference  # 270 度弧，缺口朝下（呼應 dc-handoff 設計稿）
    arc_val = arc_span * pct
    score100 = int(round(confidence * 100))
    return (
        f'<div class="tf-conf-wrap" style="display:flex;align-items:center;gap:1.2rem;flex-wrap:wrap">'
        f'<div style="position:relative;width:168px;height:168px;flex-shrink:0">'
        f'<svg viewBox="0 0 168 168" width="168" height="168">'
        f'<circle cx="84" cy="84" r="{r}" fill="none" stroke="var(--tf-border)" stroke-width="13" '
        f'stroke-linecap="round" stroke-dasharray="{arc_span:.1f} {circumference:.1f}" '
        f'transform="rotate(135 84 84)"></circle>'
        f'<circle cx="84" cy="84" r="{r}" fill="none" stroke="{color}" stroke-width="13" '
        f'stroke-linecap="round" stroke-dasharray="{arc_val:.1f} {circumference:.1f}" '
        f'transform="rotate(135 84 84)"></circle>'
        f'</svg>'
        f'<div style="position:absolute;inset:0;display:flex;flex-direction:column;'
        f'align-items:center;justify-content:center">'
        f'<div style="font-family:\'IBM Plex Mono\',monospace;font-weight:700;font-size:2.6rem;'
        f'color:{color}">{score100}</div>'
        f'<div style="font-size:.7rem;color:var(--tf-muted2)">/ 100</div>'
        f'</div>'
        f'</div>'
        f'<div>'
        f'<div class="tf-conf-big" style="color:{color}">{html.escape(label)}</div>'
        f'<div style="font-size:.85rem;color:var(--tf-muted)">整體信心指數 {confidence:.2f}</div>'
        f'</div>'
        f'</div>'
    )


def _render_cost_card(log) -> str:
    """本次分析成本卡（`.tf-section` 慣例）：從 `log.events` 篩 `tool=="llm.cost"` 加總。

    離線 run（Step3 一定會呼叫一次 `client.complete()`，離線回傳 token=0/cost=0，
    仍會記一筆 `llm.cost`，model 記為 "offline"）顯示 `$0.00（離線）`，讓使用者
    一眼看出這次分析沒有實際 Bedrock 花費，而不是誤以為 UI 沒算對。
    沒有任何 `llm.cost` 事件（理論上不會發生，Step3 恆記一筆）時回空字串，優雅略過。
    """
    e = html.escape
    cost_events = [ev["params"] for ev in log.events if ev.get("tool") == "llm.cost"]
    if not cost_events:
        return ""
    total = sum(float(p.get("cost_usd", 0.0) or 0.0) for p in cost_events)
    tokens_in = sum(int(p.get("tokens_in", 0) or 0) for p in cost_events)
    tokens_out = sum(int(p.get("tokens_out", 0) or 0) for p in cost_events)
    is_offline = all((p.get("model") or "offline") == "offline" for p in cost_events)
    cost_display = "$0.00（離線）" if is_offline else f"${total:.4f}"
    rows = "".join(
        f"<tr><td>{e(str(p.get('model') or 'offline'))}</td>"
        f"<td>{int(p.get('tokens_in', 0) or 0)}</td>"
        f"<td>{int(p.get('tokens_out', 0) or 0)}</td>"
        f"<td>${float(p.get('cost_usd', 0.0) or 0.0):.4f}</td></tr>"
        for p in cost_events
    )
    return (
        f'<div class="tf-section">'
        f'<h3>本次分析成本</h3>'
        f'<p class="j">{e(cost_display)}</p>'
        f'<p style="color:var(--tf-muted);font-size:.85rem">'
        f'共 {len(cost_events)} 次 LLM 呼叫；輸入 {tokens_in} tokens／輸出 {tokens_out} tokens</p>'
        f'<table><tr><th>Model</th><th>輸入 tokens</th><th>輸出 tokens</th><th>估算成本</th></tr>'
        f'{rows}</table>'
        f'</div>'
    )


def _format_uptime(seconds: float) -> str:
    """把秒數格式化成「N天N小時N分N秒」，供 `/status` 顯示運行時間用。"""
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours or days:
        parts.append(f"{hours}小時")
    if minutes or hours or days:
        parts.append(f"{minutes}分")
    parts.append(f"{secs}秒")
    return "".join(parts)


_FRESHNESS_STATUS_LABEL = {
    "fresh": ("新鮮", "#3fb950"),
    "stale": ("過期", "#d9832a"),
    "missing": ("缺", "#f85149"),
}


def _render_freshness_table(snapshot: list[dict]) -> str:
    """把 `cache.get_freshness_snapshot()` 的結果渲染成「來源 × 幣種」矩陣表格。

    純渲染層：不做任何額外查詢，`snapshot` 完全由呼叫端（`_render_status_page`）
    一次算好傳入。`snapshot` 為空（如讀取整批失敗降級）→ 顯示提示文字，不崩。
    """
    e = html.escape
    if not snapshot:
        return '<p style="color:var(--tf-muted);font-size:.85rem">（暫無資料可顯示）</p>'

    sources: list[str] = []
    coins: list[str] = []
    by_key: dict[tuple[str, str], dict] = {}
    for row in snapshot:
        src, coin = row.get("source", ""), row.get("coin", "")
        if src not in sources:
            sources.append(src)
        if coin not in coins:
            coins.append(coin)
        by_key[(src, coin)] = row

    header = "<th>來源</th>" + "".join(f"<th>{e(c)}</th>" for c in coins)
    body_rows = []
    for src in sources:
        cells = []
        for coin in coins:
            row = by_key.get((src, coin))
            if row is None:
                cells.append("<td>&#8212;</td>")
                continue
            label, color = _FRESHNESS_STATUS_LABEL.get(row.get("status", ""), ("未知", "var(--tf-muted)"))
            cells.append(f'<td><span style="color:{color}">{label}</span></td>')
        body_rows.append(f"<tr><td>{e(src)}</td>{''.join(cells)}</tr>")

    return f"<table><tr>{header}</tr>{''.join(body_rows)}</table>"


def _render_recent_scheduler_run() -> str:
    """`/status`「最近排程執行」區塊：讀 Phase3 `scheduler_log.get_last_scheduler_run()`
    寫入的 run record（唯讀）。取不到（尚未跑過排程／讀取失敗已降級）一律顯示
    提示文字，不崩頁面。"""
    e = html.escape
    try:
        from .scheduler_log import get_last_scheduler_run

        run = get_last_scheduler_run()
    except Exception:
        run = None

    if not run:
        return '<p style="color:var(--tf-muted);font-size:.85rem">（尚無排程執行紀錄）</p>'

    ts = e(str(run.get("ts", "")))
    success = run.get("success_count", 0)
    failure = run.get("failure_count", 0)
    total_docs = run.get("total_docs", 0)
    failures = run.get("failures") or []
    failures_html = (
        f'<p style="color:#f85149;font-size:.8rem">失敗目標：{e("、".join(str(x) for x in failures))}</p>'
        if failures else ""
    )
    return (
        "<table><tr><th>時間</th><th>成功目標數</th><th>失敗目標數</th><th>寫入文件數</th></tr>"
        f"<tr><td>{ts}</td><td>{success}</td><td>{failure}</td><td>{total_docs}</td></tr>"
        f"</table>{failures_html}"
    )


def _get_connector_usage_summary(records: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """近期（最近 `scheduler_log.RECENT_WINDOW_SIZE` 次以內）排程執行的
    `source_calls` 加總，供 `/status`「連接器用量」表使用（成本會計階段2）。

    ⚠️ O(1)-相容：只呼叫 `scheduler_log.get_recent_scheduler_runs()`（內部對
    `JsonlSchedulerRunLog`/`DynamoDBSchedulerRunLog` 都是讀一份獨立維護的
    bounded window，見 `scheduler_log.py` 模組頂部 `RECENT_WINDOW_SIZE`
    說明），不掃描完整排程歷史。任何讀取失敗（尚未跑過排程／backend 故障）
    一律降級回空 dict，`/status` 頁面必須永遠能顯示。

    ⚠️ 這是「最近 N 次排程執行」的加總，不是嚴格日曆 30 天——呼叫端顯示文案
    需誠實反映這點，不宣稱「近 30 天」（見 `_render_connector_usage_table`）。

    ⚠️ split-brain dual-read（codex HIGH，PR #41）：不能只讀 primary 的
    `recent()`——DynamoDB backend 的 recent-window 更新若樂觀鎖衝突耗盡/
    寫入暫時失敗，該筆記錄會 fallback 進本地 JSONL，但 primary 的
    recent-window 不會回頭補上，導致該筆 `source_calls` 永久漏算。
    `get_recent_scheduler_runs()` 會同時讀 primary + JSONL fallback、去重、
    排序、截斷，詳見該函式 docstring。

    ⚠️ `records` 可選（codex MEDIUM，PR #41）：`/status` 一次 render 需要
    「連接器用量」表 + 「快取節省」卡兩處都彙總 `source_calls`，若各自獨立
    呼叫 `get_recent_scheduler_runs()`，同一次 render 會重複讀取（dual-read
    下等於重複讀 4 次而非 2 次），沒必要。呼叫端（見 `_render_status_page`）
    應該只呼叫一次，把結果透過 `records` 參數**共用**給兩個彙總點；未傳
    （`None`，預設值，供獨立呼叫/測試用）才退回原本獨立呼叫的行為，向後
    相容。
    """
    if records is None:
        try:
            from .scheduler_log import get_recent_scheduler_runs

            records = get_recent_scheduler_runs()
        except Exception:
            records = []

    totals: dict[str, int] = {}
    for rec in records or []:
        for source, count in (rec.get("source_calls") or {}).items():
            totals[str(source)] = totals.get(str(source), 0) + int(count or 0)
    return totals


def _render_connector_usage_table(records: list[dict[str, Any]] | None = None) -> str:
    """`/status`「連接器用量」表：各連接器最近 N 次排程執行的呼叫數加總、
    估計成本；共用同一組配額 key 的 source（見 `cost_model.py::shared_pool`，
    目前是 3 個 coingecko-* source）合併成一行、呼叫數加總顯示。

    純讀既有資料（`_get_connector_usage_summary`）+ 純函式計算
    （`cost_model.py` 常數），不觸發任何連接器抓取。free tier（目前全部
    連接器）估計成本恆為 $0，但仍顯示真實用量（誠實原則，見 `cost_model.py`
    模組頂部說明）。

    ⛔ codex HIGH（#24、PR #41）：本表**刻意不顯示「配額使用%」**。呼叫數
    來源是 rolling「最近 N 次排程執行」window（見 `RECENT_WINDOW_SIZE`），
    不是嚴格日曆月配額會計；直接拿 rolling window 值除以官方「月配額」算
    百分比是語意錯誤的數字。對 3 個 coingecko-* source 共用同一組 key 額度
    來說，逐 source 各自算 % 還會嚴重低估真實使用率（例：3 源各顯示 40%，
    但共用 key 實際已耗用 120% 超額——逐 source % 會把超額隱藏起來）。改為：
    共用池合併一行加總呼叫數，只顯示原始呼叫數＋官方配額參考文字，不假裝
    精確百分比；未來若要提供真正的配額%，需先做月曆月 bucket 計數（本 PR
    範圍外）。

    `records`：見 `_get_connector_usage_summary` 的同名參數說明——`/status`
    一次 render 應共用同一份 `recent()` 結果傳進來（codex MEDIUM，PR #41），
    不要各自獨立呼叫 `recent()`。未傳（`None`）才退回獨立呼叫，供直接單獨
    呼叫本函式（如測試）使用。
    """
    e = html.escape
    try:
        from .scheduler_log import RECENT_WINDOW_SIZE
    except Exception:
        RECENT_WINDOW_SIZE = 30  # noqa: N806 — 匯入失敗時的顯示用退路，不影響實際彙總邏輯

    usage = _get_connector_usage_summary(records)
    # 顯示 CONNECTOR_COST_MODEL 登記過的全部來源（含用量 0 的），讓維運者
    # 一眼看到「哪些來源這個視窗內完全沒被排程呼叫到」；usage 裡若出現不在
    # 登記表的來源名稱（理論上不會，防禦性容錯）一併補上顯示，不吞掉。
    all_sources = sorted(set(CONNECTOR_COST_MODEL) | set(usage))
    if not all_sources:
        return (
            '<p style="color:var(--tf-muted);font-size:.85rem">'
            "（尚無排程執行紀錄，無連接器用量可顯示）</p>"
        )

    # 依 shared_pool 分組：同一組（如 3 個 coingecko-* source）合併成一行、
    # 呼叫數加總，不逐 source 假裝獨立配額（codex HIGH，見上方 docstring）。
    pool_members: dict[str, list[str]] = {}
    standalone: list[str] = []
    for source in all_sources:
        model = CONNECTOR_COST_MODEL.get(source)
        pool_key = model.shared_pool if model else None
        if pool_key:
            pool_members.setdefault(pool_key, []).append(source)
        else:
            standalone.append(source)

    def _row(label: str, count: int, cost: float, note: str) -> str:
        cost_cell = f"${cost:.4f}" if cost > 0 else "$0.00（free tier）"
        return (
            f"<tr><td>{e(label)}</td><td>{count}</td><td>{cost_cell}</td></tr>"
            f'<tr><td colspan="3" style="color:var(--tf-muted2);font-size:.7rem;'
            f'border-top:none;padding-top:0">{note}</td></tr>'
        )

    rows = []
    for pool_key in sorted(pool_members):
        members = pool_members[pool_key]
        total_count = sum(usage.get(m, 0) for m in members)
        total_cost = sum(estimate_connector_cost(m, usage.get(m, 0)) for m in members)
        label = SHARED_POOL_LABEL.get(pool_key, pool_key)
        first_model = CONNECTOR_COST_MODEL.get(members[0])
        ref = e(first_model.free_tier_reference) if first_model else ""
        note = (
            f"{ref}；此列為 {len(members)} 個 source"
            f"（{e('、'.join(members))}）呼叫數加總（共用同一組 key 額度）——"
            "rolling window 非月曆月配額會計，不顯示百分比，請自行對照官方配額判讀。"
        )
        rows.append(_row(label, total_count, total_cost, note))

    for source in standalone:
        count = usage.get(source, 0)
        model = CONNECTOR_COST_MODEL.get(source)
        cost = estimate_connector_cost(source, count)
        note = e(model.free_tier_reference) if model else "（未登記於成本模型）"
        rows.append(_row(source, count, cost, note))

    return (
        f'<p style="color:var(--tf-muted);font-size:.85rem">'
        f"最近（&#8804; {RECENT_WINDOW_SIZE} 次）排程執行加總——是「最近 N 次排程執行」"
        f"視窗，非嚴格日曆 30 天（排程間隔可調整，N 筆不保證恰好對應 30 個日曆天）。</p>"
        "<table><tr><th>連接器</th><th>呼叫數</th><th>估計成本</th></tr>"
        f"{''.join(rows)}</table>"
    )


# 成本會計階段3：`/analyze`（含 comparison）真實服務次數計數器——比照 `_rate_buckets`
# 限流計數器慣例，純記憶體、process 重啟歸零，不持久化，只是粗略觀測指標。
# 只在請求實際走「真連接器」路徑（`real=1` 或 `live=1`，pipeline 透過 cache 讀
# 連接器資料）時才計數；純離線示範（樣本資料，未觸碰任何連接器/cache）不計，
# 否則「快取節省」估算會被離線 demo 流量污染，失去意義。
_analyze_service_lock = threading.Lock()
_analyze_service_count = 0


def _record_analyze_service_calls(n: int) -> None:
    """記錄 `n` 次「真連接器」分析服務事件（單幣 `_do_analyze` 記 1，雙幣
    `_do_comparison` 記 2——各自都要讀一輪多來源資料），供 `/status`
    「快取節省」估算用。"""
    global _analyze_service_count
    with _analyze_service_lock:
        _analyze_service_count += n


def _get_analyze_service_count() -> int:
    with _analyze_service_lock:
        return _analyze_service_count


def _render_cache_savings_card(records: list[dict[str, Any]] | None = None) -> str:
    """`/status`「快取節省」卡：估算「若無快取」需要的連接器呼叫次數，對比
    scheduler 實際呼叫次數，算出估計省下的次數／成本（**標「估算」**，見下方
    明確算式；多數連接器是 free tier，故以次數為主，成本欄目前恆為 $0）。

    算式：
      若無快取 ≈ analyze 服務次數（本 process 累計，見 `_record_analyze_service_calls`）
                × 已知連接器來源數（`len(CONNECTOR_COST_MODEL)`——每次真分析理論上
                  都要重新打一輪全部已知來源）
      實際 scheduler 呼叫次數 = 連接器用量表（近期排程執行）加總
      省下次數 = max(0, 若無快取 − 實際)

    ⚠️ 兩個數字時間窗不同源（analyze 次數是本 process 啟動以來累計，scheduler
    呼叫數是最近 N 次排程執行 window，見 `_get_connector_usage_summary`）——
    刻意不假裝精確對齊同一個時間窗，這正是要標「估算」的原因，不是抓 bug。

    `records`：同 `_render_connector_usage_table` 的同名參數——`/status` 一次
    render 應共用同一份 `recent()` 結果（codex MEDIUM，PR #41），不要各自
    獨立呼叫 `recent()`。未傳（`None`）才退回獨立呼叫。
    """
    analyze_count = _get_analyze_service_count()
    usage = _get_connector_usage_summary(records)
    actual_calls = sum(usage.values())
    n_sources = len(CONNECTOR_COST_MODEL)
    would_be_calls = analyze_count * n_sources
    saved_calls = max(0, would_be_calls - actual_calls)

    would_be_cost = sum(
        estimate_connector_cost(source, analyze_count) for source in CONNECTOR_COST_MODEL
    )
    actual_cost = sum(estimate_connector_cost(source, count) for source, count in usage.items())
    saved_cost = max(0.0, round(would_be_cost - actual_cost, 6))

    return (
        f'<p class="j">省下約 {saved_calls} 次連接器呼叫'
        f'（≈ ${saved_cost:.4f}，多數 free source 故以次數為主）</p>'
        f'<p style="color:var(--tf-muted);font-size:.85rem">'
        f'算式（估算）：若無快取 ≈ analyze 服務次數（{analyze_count}）'
        f'× 已知連接器來源數（{n_sources}）＝ {would_be_calls} 次；'
        f'實際 scheduler 呼叫次數（近期排程執行加總）＝ {actual_calls} 次'
        f' → 估計省 {saved_calls} 次。</p>'
        f'<p style="color:var(--tf-muted2);font-size:.75rem">'
        f'analyze 服務次數為本 process 啟動以來累計（見上方「運行時間」），'
        f'與 scheduler 的近期視窗非同一時間窗，此數字僅供估算參考。</p>'
    )


def _render_status_page() -> str:
    """`/status`：系統可觀測性頁——版本、執行模式能力、快取 backend 連線探測、
    成本摘要、資料鮮度矩陣、最近排程執行。

    ⚠️ credit-safe 鐵律：本頁**只讀**既有 cache/ledger/scheduler run log，不呼叫
    Bedrock、不觸發任何連接器真抓取。快取 backend 連線探測與資料鮮度矩陣都是
    對既有 cache backend 的唯讀 `get()`（資料只可能來自 `scripts/fetch_scheduler.py`
    排程既有寫入的內容），不是新的連接器外呼；成本摘要重用 `/costs` 頁面同一套
    `get_ledger().summary()` fallback 邏輯，零新查詢語意。
    """
    e = html.escape
    uptime_html = e(_format_uptime(time.time() - _START_TIME))

    mode_rows = f"""
      <tr><td>版本</td><td>{e(VERSION)}</td></tr>
      <tr><td>Bedrock（HAS_BEDROCK）</td>
          <td style="color:{'#3fb950' if HAS_BEDROCK else 'var(--tf-muted)'}">
            {'已設定（真 Bedrock 模式可用）' if HAS_BEDROCK else '未設定（僅離線示範／真資料·$0 模式可用）'}
          </td></tr>
      <tr><td>LIVE_TOKEN</td>
          <td style="color:{'#3fb950' if LIVE_TOKEN else 'var(--tf-muted)'}">
            {'已設定' if LIVE_TOKEN else '未設定'}
          </td></tr>
      <tr><td>成本預算門檻（COST_BUDGET_USD）</td>
          <td>{e(COST_BUDGET_USD) if COST_BUDGET_USD else '未設定'}</td></tr>
      <tr><td>運行時間</td><td>{uptime_html}</td></tr>
    """

    # 延遲匯入：避免 web.py 模組載入順序把 ingestion 子套件提前拉進來。
    from .ingestion.cache import cache_key, get_cache_backend, get_freshness_snapshot

    cache_backend = get_cache_backend()
    # 唯讀連線探測：對保留的探測 key 做一次 `get()`（cache-miss 也算探測成功，
    # 只要呼叫本身沒丟例外就代表 backend 讀寫路徑通——不影響任何真實資料，
    # 更不會觸發任何連接器抓取）。故意繞過 `cache_get()` 的自動 fallback 語意，
    # 才能問到「primary backend 本身」通不通，而非被 fallback 悄悄接住。
    # ⚠️ source/coin 兩個欄位都必須非空（見 `_STATUS_PROBE_SOURCE`/
    # `_STATUS_PROBE_COIN` 定義處註解）：`DynamoDBCache` 的 SK 絕不接受空字串，
    # 傳空字串會被 DynamoDB 當成參數錯誤直接拒絕（`ValidationException`），
    # 跟「backend 真的連不上」是兩回事，不能混為一談誤報 disconnected。
    try:
        cache_backend.get(cache_key(_STATUS_PROBE_SOURCE, _STATUS_PROBE_COIN))
        cache_color = "#3fb950"
        cache_text = f"connected（backend={e(type(cache_backend).__name__)}）"
    except Exception as exc:
        cache_color = "#f85149"
        cache_text = f"disconnected（backend={e(type(cache_backend).__name__)}）：{e(str(exc))}"

    try:
        summary = get_ledger().summary()
    except Exception:
        summary = JsonlLedger().summary()
    total_cost = float(summary.get("total_cost_usd", 0.0) or 0.0)
    # codex HIGH：`summary()["runs"]` 現在只回最近 SUMMARY_RECENT_RUNS_CAP 筆（有界），
    # 真實總筆數要讀新加的 `run_count` 欄位；沒有該欄位（理論上不會，防呆）才退回
    # 用 `runs` 長度估算。
    run_count = int(summary.get("run_count", len(summary.get("runs", []) or [])) or 0)

    try:
        freshness = get_freshness_snapshot(backend=cache_backend)
    except Exception:
        freshness = []
    fresh_n = sum(1 for r in freshness if r.get("status") == "fresh")
    stale_n = sum(1 for r in freshness if r.get("status") == "stale")
    missing_n = sum(1 for r in freshness if r.get("status") == "missing")
    freshness_html = _render_freshness_table(freshness)

    recent_run_html = _render_recent_scheduler_run()

    # codex MEDIUM+HIGH（PR #41）：「連接器用量」表 + 「快取節省」卡都需要
    # 排程執行記錄的彙總結果，一次 render 只呼叫一次、結果共用給兩處——不要
    # 各自獨立呼叫（沒必要的重複讀取；比照本頁面本身已有的 TTL +
    # single-flight 快取精神，同一次 render 內部也不重複讀同一份資料）。
    # 用 `get_recent_scheduler_runs()`（dual-read 合併 primary + JSONL
    # fallback，見該函式 docstring）而不是只讀 `get_scheduler_run_log().recent()`
    # ——只讀 primary 在 DynamoDB backend 上有 split-brain 風險：recent-window
    # 若更新失敗會 fallback 進本地 JSONL，但 primary 的 recent-window 不會
    # 補上，導致該筆記錄的 source_calls 永久漏算（codex HIGH）。任何讀取
    # 失敗一律降級回空清單，兩個渲染函式各自對空清單有防禦性處理，不會讓
    # `/status` 崩頁。
    try:
        from .scheduler_log import get_recent_scheduler_runs

        scheduler_records = get_recent_scheduler_runs()
    except Exception:
        scheduler_records = []

    return f"""
<div class="tf-section">
  <h2 style="margin:0 0 .3rem">系統狀態</h2>
  <p style="color:var(--tf-muted);font-size:.85rem">本頁純讀既有資料，不觸發任何連接器抓取／Bedrock 呼叫。</p>
  <table>{mode_rows}</table>
</div>

<div class="tf-section">
  <h3>連線狀態</h3>
  <table>
    <tr><th>元件</th><th>狀態</th></tr>
    <tr><td>快取 backend（DynamoDB／JSON，依 CACHE_BACKEND）</td>
        <td style="color:{cache_color}">{cache_text}</td></tr>
  </table>
</div>

<div class="tf-section">
  <h3>成本摘要</h3>
  <p class="j">累計花費 ${total_cost:.4f}</p>
  <p style="color:var(--tf-muted);font-size:.85rem">共 {run_count} 筆歷史 run 紀錄，詳見 <a href="/costs">成本帳本</a>。</p>
  {_render_model_token_table(summary)}
</div>

<div class="tf-section">
  <h3>連接器用量（近期排程執行加總）</h3>
  {_render_connector_usage_table(scheduler_records)}
</div>

<div class="tf-section">
  <h3>快取節省（估算）</h3>
  {_render_cache_savings_card(scheduler_records)}
</div>

<div class="tf-section">
  <h3>資料鮮度矩陣（各連接器 × 各幣種）</h3>
  <p style="color:var(--tf-muted);font-size:.85rem">
    <span style="color:#3fb950">新鮮 {fresh_n}</span> ·
    <span style="color:#d9832a">過期 {stale_n}</span> ·
    <span style="color:#f85149">缺 {missing_n}</span>
  </p>
  {freshness_html}
</div>

<div class="tf-section">
  <h3>最近排程執行</h3>
  {recent_run_html}
</div>
"""


def _render_status_page_cached() -> str:
    """`_render_status_page()` 的 ~30 秒 module 級 TTL 快取包裝。

    資料鮮度矩陣要逐 (source, coin) 讀 cache backend（見 `get_freshness_snapshot`），
    組合數不小，DynamoDB backend 下每次請求都重算會有明顯延遲，也容易被打爆。
    這是**跨 IP 共用**的頁面級快取（非安全機制，`_check_status_rate_limit` 才是），
    純粹降低重算頻率。

    ⚠️ single-flight：cache 過期瞬間可能有多個 `ThreadingHTTPServer` 併發請求
    同時進來（跨多個 client IP，per-IP 限流擋不住）。若鎖只保護「檢查/寫入」
    兩小段、`_render_status_page()` 本身在鎖外跑，這些併發請求會全部 miss、
    各自獨立重算整份 freshness matrix + ledger summary + scheduler run 讀取
    ——thundering herd，DynamoDB backend 下造成負載/成本尖峰。因此把
    `_render_status_page()` 整個放進鎖內序列化執行：只有第一個發現 cache
    過期的請求真的重算，其餘請求卡在鎖外等待；等它們拿到鎖時，上面的
    `now < expires_at` 檢查已經因為前者剛寫入的新值而成立，直接吃新值
    返回，不會各自重算。代價是過期瞬間的併發請求會被序列化等重算完成
    （而非立刻各自平行拿到舊值），但這正是 TTL 快取本來就允許的等級（本
    來單一請求重算也要付這個延遲），且比起 thundering herd 更安全。
    """
    with _status_cache_lock:
        now = time.time()
        if now < _status_cache["expires_at"]:
            return _status_cache["html"]  # type: ignore[return-value]
        rendered = _render_status_page()
        _status_cache["html"] = rendered
        _status_cache["expires_at"] = time.time() + _STATUS_CACHE_TTL_SECONDS
        return rendered


def _handle_status(client_ip: str = "") -> tuple[int, str]:
    """處理 `/status` 請求邏輯，回傳 `(http_status, html_body)`——由 `Handler.do_GET`
    包一層 `self._send`；抽出成獨立函式方便測試直接呼叫，不需開真 socket
    （比照 `_do_analyze`/`_do_comparison` 的抽出慣例）。

    CEO 決策（PR #39，收斂）：theme toggle 機制整個拆除，`render_page()`
    固定 dark，這裡不再需要接受/轉傳 `theme`/`theme_toggle_href` 參數。
    """
    try:
        _check_status_rate_limit(client_ip)
    except TooManyRequests as exc:
        # 商業級修復：跟 `/analyze` 錯誤頁一致，統一走品牌化錯誤卡（見
        # `_render_error_card`），不留一處裸紅字例外。
        return 429, render_page(
            _render_error_card("請求過於頻繁", str(exc), retry_href="/status")
        )
    return 200, render_page(_render_status_page_cached())


_LEDGER_SUMMARY_CACHE_TTL_SEC = 20.0
_LEDGER_SUMMARY_CACHE_MAX = 32
_ledger_summary_cache: dict[Callable[[], object], tuple[float, dict]] = {}
_ledger_summary_cache_lock = threading.Lock()


def _get_ledger_summary() -> dict:
    """讀取跨 run 持久化成本帳本彙總（`get_ledger().summary()`），backend 讀取失敗
    （如 `COST_LEDGER_BACKEND=dynamodb` 但未實作）時 fallback 讀 `JsonlLedger`，
    與 `ledger.append_run()` 的 fallback 邏輯一致，確保呼叫端永遠拿得到 dict。

    供 `/costs` 頁面與 header「cost ledger $X」連結共用同一份真實累計數字。

    效能修正（CEO Chrome 複審 MEDIUM）：`Ledger.summary()`（JSONL 全檔重讀／
    DynamoDB 全表 Scan + JSONL fallback 合併）成本隨歷史紀錄筆數線性增長；
    先前版本每個頁面（含每次 render_page 都會渲染的 header cost ledger 連結、
    以及 `/costs` 本身）都重新全掃一次帳本，隨帳本增長會拖慢每個頁面的
    latency。這裡加一層以「目前生效的 `get_ledger` 工廠函式本身」為 key、
    TTL 20 秒、上限 32 筆的 bounded 記憶體快取：同一個 `get_ledger` 在 20
    秒內重複呼叫只真的全掃一次（含同一次請求內 header + `/costs` 內容各
    呼叫一次，也只掃一次）；只要 `get_ledger` 被換掉（例如測試
    `monkeypatch.setattr(web, "get_ledger", lambda: fake_ledger)` 換一顆新的
    fake），key 立刻不同、絕不會讀到舊 ledger 的快取值——`test_costs_page_*`
    系列測試每個都換一顆全新 fake ledger 並斷言各自數字，因此不受影響。

    ⚠️ single-flight 修正（codex 複審 MEDIUM，PR #39，與 `_render_status_page_cached`
    同類 bug）：先前版本鎖只保護「檢查 cache」與「寫入 cache」兩小段，中間
    `get_ledger().summary()` 的實際計算是在**鎖外**跑的——冷啟動或 TTL 剛過期
    那一瞬間，`ThreadingHTTPServer` 下每個併發進來的頁面請求（header cost
    ledger 連結每頁都會觸發，`/costs` 本身也會）都各自判定 cache miss，
    全部平行呼叫一次 `summary()`，等同 thundering herd：JSONL 全檔重讀／
    DynamoDB 全表 Scan 被同時打好幾份，延遲尖峰、DynamoDB backend 下還有
    成本放大。修法比照 `_render_status_page_cached()` 已驗證過的做法：把
    「檢查 cache → miss 就地計算 → 寫回 cache」整段放進同一把鎖內序列化
    執行。只有第一個發現 miss 的請求真的呼叫 `summary()`，其餘請求卡在鎖
    外等待；等它們拿到鎖時，前者剛寫入的新值已經讓 cache 命中，直接吃新
    值返回，不會各自重算。TTL 語意不變（20 秒），只是把「讀什麼」跟「算
    什麼」的鎖粒度對齊，不再有鎖外的計算窗口。

    ⚠️ cache key 修正（codex 複審 MEDIUM，PR #39）：先前用 `id(get_ledger)`
    （純整數）當 key，字典本身不持有 `get_ledger` 函式物件的參照。若
    `get_ledger` 被重新綁定（例如測試換一顆新的 fake 工廠）、舊的函式物件
    又剛好被 GC 回收，CPython 有機率把同一個記憶體位址（同一個 `id()`）
    重新分配給新建立的另一個函式物件——20 秒 TTL 內若新工廠恰好拿到舊 id，
    會直接命中舊工廠留下的摘要快取，顯示錯誤（過期）的成本數字，而且是
    悄無聲息、不會噴錯的資料錯誤。修法：**直接用 `get_ledger` 這個函式物件
    本身作 dict key**（函式可雜湊、預設用身分比較），而不是它的 `id()`。
    字典的 key 引用本身會讓該函式物件在快取存活期間至少多一份強參照，
    杜絕「物件已死、id 被別人撿走」這個時間窗——只要 key 還在快取裡，
    Python 就不可能把同一個 id 分配給另一個物件；等 TTL 過期或被 32 筆
    上限淘汰、key 才會真的釋放。TTL、single-flight、bounded 淘汰邏輯全部
    不變。
    """
    key = get_ledger
    with _ledger_summary_cache_lock:
        now = time.monotonic()
        cached = _ledger_summary_cache.get(key)
        if cached is not None and (now - cached[0]) < _LEDGER_SUMMARY_CACHE_TTL_SEC:
            return cached[1]
        try:
            summary = get_ledger().summary()
        except Exception:
            summary = JsonlLedger().summary()
        _ledger_summary_cache[key] = (now, summary)
        if len(_ledger_summary_cache) > _LEDGER_SUMMARY_CACHE_MAX:
            oldest_key = min(_ledger_summary_cache, key=lambda k: _ledger_summary_cache[k][0])
            _ledger_summary_cache.pop(oldest_key, None)
        return summary


def _header_cost_display() -> str:
    """Header「cost ledger $X」連結顯示用字串：累計真實花費（`$0.0000` 起跳）。

    讀取失敗時（理論上不會，`_get_ledger_summary` 已有 fallback）優雅退回 `$0.0000`，
    不讓整頁因帳本 I/O 例外而掛掉。
    """
    try:
        total = float(_get_ledger_summary().get("total_cost_usd", 0.0) or 0.0)
    except Exception:
        total = 0.0
    return f"${total:.4f}"


def _model_price_display(model: str) -> str:
    """`model` 在 `PRICING`（ledger.py）的單價顯示字串（USD／百萬 tokens，輸入/輸出）。

    不在 `PRICING` 的 model_id（如 "offline"、未知/淘汰的 model_id）一律顯示
    「—（無定價／離線）」，不猜測、不誤植價格——與 `ledger.py::estimate_cost()`
    「未知 model_id 一律 $0，不誤套價格」的既有契約一致。
    """
    rates = PRICING.get(model)
    if rates is None:
        return "—（無定價／離線）"
    in_rate, out_rate = rates
    return f"入 ${in_rate:.2f}／出 ${out_rate:.2f}（每百萬 tokens）"


def _render_model_token_table(summary: dict) -> str:
    """成本會計階段1：把 `summary()` 的 `by_model_detail` 渲染成
    「Model｜輸入tokens｜輸出tokens｜單價(來自 PRICING)｜成本」明細表。

    純顯示層：資料完全來自 `Ledger.summary()` 既有彙總（見 `ledger.py` 階段1
    註解），這裡不做任何額外查詢/計算，只負責排版。`by_model_detail` 缺欄位
    （理論上不會，`summary()` 已保證每個 model 都有完整三欄）時用 `.get(...,0)`
    防呆，不讓渲染因缺欄位而拋例外。
    """
    e = html.escape
    detail = summary.get("by_model_detail", {}) or {}
    if not detail:
        return '<p style="color:var(--tf-muted);font-size:.85rem">（尚無 LLM 呼叫紀錄）</p>'
    rows = "".join(
        f"<tr><td>{e(str(m))}</td>"
        f"<td>{int(d.get('tokens_in', 0) or 0)}</td>"
        f"<td>{int(d.get('tokens_out', 0) or 0)}</td>"
        f"<td>{e(_model_price_display(m))}</td>"
        f"<td>${float(d.get('cost_usd', 0.0) or 0.0):.4f}</td></tr>"
        for m, d in sorted(detail.items(), key=lambda kv: -kv[1].get("cost_usd", 0.0))
    )
    return (
        '<table><tr><th>Model</th><th>輸入 tokens</th><th>輸出 tokens</th>'
        f'<th>單價</th><th>成本</th></tr>{rows}</table>'
    )


def _render_costs_page() -> str:
    """`/costs`：跨 run 持久化成本帳本彙總頁 —— 累計總花費、依 model 分組、per-run 明細。

    累計總花費超過 env `COST_BUDGET_USD` 門檻 → 卡片轉紅告警。帳本 backend 讀取
    失敗（如 `COST_LEDGER_BACKEND=dynamodb` 但未實作）→ fallback 讀 `JsonlLedger`，
    與 `ledger.append_run()` 的 fallback 邏輯一致，確保頁面永遠可顯示。
    """
    e = html.escape
    summary = _get_ledger_summary()

    total = float(summary.get("total_cost_usd", 0.0) or 0.0)
    by_model = summary.get("by_model", {}) or {}
    # codex HIGH：`runs` 現在只含最近 SUMMARY_RECENT_RUNS_CAP 筆（有界），真實總筆數
    # 讀 `run_count`；`recent`（下方 per-run 明細）本來就只顯示最近 50 筆，`runs` 本身
    # ≤50 筆時 `reversed(runs)[:50]` 結果不變，帳本 >50 筆時兩者挑出的仍是同一批
    # 「最近 50 筆」，SSR 輸出逐字不變。
    runs = summary.get("runs", []) or []
    run_count = int(summary.get("run_count", len(runs)) or 0)

    over_budget = False
    if COST_BUDGET_USD:
        try:
            over_budget = total > float(COST_BUDGET_USD)
        except ValueError:
            over_budget = False

    card_style = (
        "border-color:#cb2431;background:rgba(203,36,49,.08)"
        if over_budget else "border-color:#1f6feb;background:rgba(31,111,235,.08)"
    )
    alert_html = (
        f'<p style="color:#cb2431;font-weight:600">'
        f'&#9888; 累計花費已超過預算門檻 ${e(COST_BUDGET_USD)}</p>'
        if over_budget else ""
    )

    model_rows = "".join(
        f"<tr><td>{e(str(m))}</td><td>${float(c):.4f}</td></tr>"
        for m, c in sorted(by_model.items(), key=lambda kv: -kv[1])
    )

    # per-run 明細：最近 N 筆（最新在前）
    recent = list(reversed(runs))[:50]
    run_rows = []
    for r in recent:
        calls = r.get("calls", []) or []
        offline_badge = " <small style='color:var(--tf-muted2)'>(離線)</small>" if r.get("offline") else ""
        run_rows.append(
            f"<tr><td>{e(str(r.get('ts', '')))}</td>"
            f"<td>{e(str(r.get('coin', '')))}</td>"
            f"<td>{e(str(r.get('question_type', '')))}{offline_badge}</td>"
            f"<td>{len(calls)}</td>"
            f"<td>${float(r.get('total_cost_usd', 0.0) or 0.0):.4f}</td></tr>"
        )
    run_rows_html = "".join(run_rows)

    return f"""
<div class="tf-section" style="{card_style}">
  <h2 style="margin:0 0 .3rem">累計成本帳本</h2>
  <p class="j">${total:.4f}</p>
  {alert_html}
  <p style="color:var(--tf-muted);font-size:.85rem">共 {run_count} 個 run（跨 run 持久化，見 out/cost_ledger.jsonl）</p>
</div>

<div class="tf-section">
  <h3>依 Model 分組</h3>
  <table><tr><th>Model</th><th>累計成本</th></tr>{model_rows or '<tr><td colspan="2">&#8212;</td></tr>'}</table>
</div>

<div class="tf-section">
  <h3>LLM 成本明細（依 Model，含 tokens）</h3>
  <p style="color:var(--tf-muted);font-size:.85rem">單價取自 <code>ledger.py::PRICING</code>（USD／百萬 tokens）；
  成本＝輸入 tokens×入單價 ＋ 輸出 tokens×出單價，與上方「累計成本」同一份資料，純拆分顯示。</p>
  {_render_model_token_table(summary)}
</div>

<div class="tf-section">
  <h3>Per-run 明細（最近 {len(recent)} 筆，最新在前）</h3>
  <table>
    <tr><th>時間</th><th>幣種</th><th>題型</th><th>LLM 呼叫數</th><th>本次成本</th></tr>
    {run_rows_html or '<tr><td colspan="5">&#8212;（尚無紀錄）</td></tr>'}
  </table>
</div>
"""


def _hero_analyze_href() -> str:
    """首頁 hero「立即開始分析」CTA 連結：真的觸發一次 `/analyze` 真資料
    分析（`_is_real_request()` 預設檔位——未帶 `sample`/`live` 即走真連接器
    + Bedrock off，$0），不是離線示範、也不是錨點捲動。

    P-2026 生產 UX bug：原本是 `href="#tf-query-console"`，桌面版 Query
    Console 本來就在左側可見，點下去等於捲到已可見處＝視覺零反應，使用者
    以為壞掉。改成連到真正會導航、真的跑一次分析的連結，符合「立即開始
    分析」語意，且維持 zero-JS（純 `<a href>`，無 JS 監聽）。

    coin 固定 `COIN_POOL[0]`（即 BTC，既有預設幣種），問題文字沿用既有
    `_DATE_AGNOSTIC_QUERY_SUFFIX` 常數組出、不新造文案、不帶日期（避免
    `_render_price_provenance()` 之外的地方自行宣稱日期，見該常數旁註解）。
    刻意**不帶** `sample=1`：這顆 CTA 要展示的正是「真資料」這個賣點。
    """
    coin = COIN_POOL[0]
    params = {
        "coin": coin,
        "type": QuestionType.MULTI_SOURCE.value,
        "q": f"分析{coin}{_DATE_AGNOSTIC_QUERY_SUFFIX}，整合多源資料",
    }
    return html.escape(f"/analyze?{urlencode(params)}")


def _example_analyze_href() -> str:
    """首頁「看範例報告」CTA 連結：沿用 Query Console 表單本身的預設幣種／
    題型／問題文字（`_PAGE` 表單預設值），走一般 `/analyze` GET 路由。

    ⛔ credit-safe / #24：世界第一重寫 Phase 2 起，「真資料·$0」是 `/analyze`
    的預設檔位——這條範例 CTA 明確標示「示意用途，離線示範資料」（見
    `_render_home_page`），必須顯式帶 `?sample=1` 才會落在離線示範沙盒，
    否則不帶參數會觸發真連接器（跟畫面文案「非即時市場資料」自相矛盾）。
    結果頁會照常顯示「離線示範」為 active 模式（見 `render_page`
    active_mode 徽章），對使用者誠實揭露這是示範資料，不是即時市場資料、
    也不會觸發任何真連接器或 Bedrock 呼叫。coin/q 皆為既有既定文案，不新增
    / 虛構任何樣本資料。
    """
    params = {
        "coin": COIN_POOL[0],
        "type": QuestionType.MULTI_SOURCE.value,
        "q": f"分析該幣種{_DATE_AGNOSTIC_QUERY_SUFFIX}，整合多源資料",
        "sample": "1",
    }
    return html.escape(f"/analyze?{urlencode(params)}")


def _home_overview_backend():
    """首頁總覽讀路徑專用 cache backend：短 timeout（見
    `_HOME_OVERVIEW_TIMEOUT_SECONDS`），跟 `get_cache_backend()`（給排程器／
    `/status` 用，無 timeout、容錯優先）刻意分開建構——不能共用同一個無
    timeout 版本，否則慢/掛掉的 backend 會讓首頁 request 被拖住（P3 事故的
    根因就是首頁請求直接吃了 DynamoDB 的預設容錯行為）。

    沿用 `CACHE_BACKEND` env 決定 dynamodb/json（跟 `get_cache_backend()`
    同一套判斷邏輯，只是 dynamodb 分支多帶三個 timeout 參數）：`json` 分支
    是本機檔案 I/O，本來就不會 hang，不需要 timeout。
    """
    from .ingestion.cache import DynamoDBCache, JsonCacheBackend

    backend_name = os.getenv("CACHE_BACKEND", "dynamodb").strip().lower()
    if backend_name == "json":
        return JsonCacheBackend()
    return DynamoDBCache(
        connect_timeout=_HOME_OVERVIEW_TIMEOUT_SECONDS,
        read_timeout=_HOME_OVERVIEW_TIMEOUT_SECONDS,
        max_attempts=1,
    )


def _status_cache_backend():
    """`/api/status`（JSON API，不是 SSR `/status` 頁）專用 cache backend：
    短 timeout + 限重試（見 `_STATUS_CACHE_*` 常數），跟 `get_cache_backend()`
    （給排程器／SSR `/status` 頁用，無 timeout、容錯優先，這裡刻意不動）分開
    建構，理由同 `_home_overview_backend()`——`/api/status` 是同步 request
    路徑，不能吃 DynamoDB SDK 預設可達分鐘級的 timeout/重試。

    codex 複審 HIGH（production 安全）：光有這個短 timeout 還不足以讓
    `/api/status` 真正「有界」——`get_freshness_snapshot()` 逐 (source,
    coin) 迴圈約 115 格，若每格都重新嘗試一次 primary，115 次 × 短
    timeout 仍是分鐘級。這個短 timeout 只保證『每一次 primary 嘗試』本身
    有界；真正的整體 bounded worst-case 要搭配
    `get_freshness_snapshot(..., circuit_breaker=True)`（見
    `_handle_api_status()`）：同一次請求內第一次 primary 失敗後，後續格子
    直接跳過 primary，兩者合起來才是完整修復。

    沿用 `CACHE_BACKEND` env 決定 dynamodb/json（跟 `get_cache_backend()`
    同一套判斷邏輯，只是 dynamodb 分支多帶三個 timeout 參數）：`json` 分支
    是本機檔案 I/O，本來就不會 hang，不需要 timeout。
    """
    from .ingestion.cache import DynamoDBCache, JsonCacheBackend

    backend_name = os.getenv("CACHE_BACKEND", "dynamodb").strip().lower()
    if backend_name == "json":
        return JsonCacheBackend()
    return DynamoDBCache(
        connect_timeout=_STATUS_CACHE_CONNECT_TIMEOUT_SECONDS,
        read_timeout=_STATUS_CACHE_READ_TIMEOUT_SECONDS,
        max_attempts=_STATUS_CACHE_MAX_ATTEMPTS,
    )


def _overview_bg_refresh_once() -> None:
    """單輪背景刷新：真的做 `cache_get()` + 新鮮度驗證——**這是整個首頁總
    覽功能裡唯一允許發生 I/O 的地方**，只在背景 daemon thread 裡被呼叫
    （見 `_overview_bg_loop()`），永遠不會被首頁 request 執行緒呼叫。

    成功且新鮮才更新 `_overview_html`；backend stall（真的卡住，不是拋
    例外）、任何例外（含 `_home_overview_backend()` 建構本身出錯、憑證/
    DNS 問題、`cache_get()` 內建的 `JsonCacheBackend` fallback 檔案 I/O
    失敗）、或讀到的 entry 已過期，一律讓 `_overview_html` 變成 `None`
    （不顯示過期或壞資料）——複用上一輪 codex HIGH 修復訂出的
    `TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS`（45 分鐘）新鮮度窗口，不依賴
    DynamoDB TTL 的非同步刪除語意。

    這個函式本身可能被 backend stall 卡住任意長時間——**這是刻意允許
    的**：卡住的只是這條背景 thread 自己，下一輪迴圈（或下次首頁 request
    觸發 `_ensure_overview_bg_thread_started()` 檢查 `is_alive()`）不受
    影響，首頁 request 執行緒從頭到尾不會呼叫到這個函式。

    codex HIGH #3：如果**這一輪呼叫本身永久 stall**（`cache_get()` 卡住不
    返回、不拋錯），這個函式永遠不會走到下面「寫回 `_overview_html`」那
    行——舊值會停在記憶體裡。所以新鮮度不能只在「這輪成功」時檢查一次就
    算數，還要讓 request 端有辦法自己判斷「這顆現貨是不是已經過期了」，
    因此這裡連同 expiry 時間戳一起寫回：`_overview_expiry_epoch =
    fetched_at + TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS`（絕對時間，不是
    倒數計時器）——即使之後再也沒有一輪成功刷新，request 路徑仍能靠純
    記憶體時鐘比較讓這顆現貨準時失效（見 `_render_home_overview_cached()`）。
    """
    from .ingestion.cache import (
        TRUST_OVERVIEW_COIN,
        TRUST_OVERVIEW_SOURCE,
        TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS,
        cache_get,
        cache_key,
    )

    global _overview_html, _overview_expiry_epoch
    new_html: str | None = None
    new_expiry = 0.0
    try:
        backend = _home_overview_backend()
        entry = cache_get(backend, cache_key(TRUST_OVERVIEW_SOURCE, TRUST_OVERVIEW_COIN))
        if entry is not None:
            fetched_at = float(entry.get("fetched_at", 0.0) or 0.0)
            age = time.time() - fetched_at
            if age <= TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS:
                docs = entry.get("docs") or []
                if docs and isinstance(docs[0], dict):
                    candidate = str(docs[0].get("html", "") or "")
                    if candidate:
                        new_html = candidate
                        new_expiry = fetched_at + TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS
            # else：過期，`new_html`/`new_expiry` 維持初值——視同 miss，不顯示過期判斷。
    except Exception as exc:  # noqa: BLE001 — 背景 thread 專用防禦：任何
        # I/O 問題（含真 stall 後才拋出、憑證/DNS/JSON fallback 檔案 I/O）
        # 都只影響這一輪結果，絕不往外傳、也絕不讓首頁 request 承擔。
        print(f"[web] WARNING: overview 背景刷新讀取失敗：{exc}", file=sys.stderr)
        new_html = None
        new_expiry = 0.0

    with _overview_state_lock:  # 極短：只做兩次變數賦值，鎖內零 I/O
        _overview_html = new_html
        _overview_expiry_epoch = new_expiry


def _overview_bg_loop(stop_event: threading.Event, interval: float) -> None:
    """背景刷新迴圈本體；只在自己專屬的 daemon thread 裡跑。"""
    while not stop_event.is_set():
        try:
            _overview_bg_refresh_once()
        except Exception as exc:  # pragma: no cover - 防禦性，背景 thread
            # 本身不能因為未預期例外而整條死掉、永遠不再刷新。
            print(f"[web] WARNING: overview 背景刷新迴圈例外：{exc}", file=sys.stderr)
        stop_event.wait(interval)  # 用 `wait()` 取代 `sleep()`：收到停止
        # 訊號可以立刻跳出這輪等待，不用真的睡滿一整個 interval（主要是
        # 測試/優雅關閉友善；production 下兩者行為等價）。


def _ensure_overview_bg_thread_started() -> None:
    """懶啟動背景 thread；`is_alive()` 檢查 + `Thread.start()` 都是純
    in-memory 操作、非阻塞，不算 I/O。用 `_overview_bg_thread_lock` 包住
    避免併發首頁 request 同時進來重複啟動同一份背景工作（idempotent）。
    """
    global _overview_bg_thread, _overview_bg_stop_event
    if _overview_bg_thread is not None and _overview_bg_thread.is_alive():
        return
    with _overview_bg_thread_lock:
        if _overview_bg_thread is not None and _overview_bg_thread.is_alive():
            return  # 併發 request 同時進來，只有一個真的啟動
        stop_event = threading.Event()  # 每次啟動都是全新的 event，不重用
        # 舊的——避免舊 thread 收到「新一輪啟動」誤觸發的停止訊號，或反過
        # 來新 thread 被舊的已設 event 誤判該立刻停止。
        thread = threading.Thread(
            target=_overview_bg_loop,
            args=(stop_event, _OVERVIEW_BG_INTERVAL_SECONDS),
            name="tf-overview-bg",
            daemon=True,
        )
        _overview_bg_stop_event = stop_event
        _overview_bg_thread = thread
        thread.start()


def _render_home_overview_cached() -> str:
    """首頁「多幣總覽」區塊讀路徑——**零 I/O**：只確保背景 thread 已啟動
    （`_ensure_overview_bg_thread_started()`，非阻塞），然後持極短鎖讀一
    次 in-memory 現貨 `_overview_html` + `_overview_expiry_epoch`，外加一
    次**純記憶體時鐘比較**。不管 backend 有沒有 stall、DynamoDB 憑證/DNS
    有沒有問題，這個函式的執行時間都跟那些完全無關，恆定是微秒級——真正
    的 I/O 全部關在 `_overview_bg_refresh_once()` 裡，只被背景 thread 呼叫
    （設計理由完整版見模組頂部「首頁『多幣總覽』」大段註解，含 codex 三
    輪 HIGH review 的教訓）。

    codex HIGH #3：光靠背景 thread「判過期就設 None」不夠——如果背景那
    一輪呼叫本身永久 stall，它永遠到不了那行程式碼，舊值會停在記憶體裡
    不會失效。所以這裡除了讀 `_overview_html`，還要拿現在時間跟寫入時算
    好的絕對到期時間 `_overview_expiry_epoch` 比較：即使背景 thread 已經
    卡死、再也不會來更新/清空這顆現貨，時鐘一旦走過 expiry，這個函式自
    己就會判定它失效並回傳空字串——不依賴背景 thread 主動清除。

    讀到 `None`／已過期一律回空字串——首頁其餘內容照常渲染，只有總覽區
    塊優雅缺席（見 `_render_home_page()` 呼叫處）。
    """
    _ensure_overview_bg_thread_started()
    with _overview_state_lock:
        html = _overview_html
        expiry = _overview_expiry_epoch
    if not html:
        return ""
    if time.time() > expiry:  # 純記憶體比較，零 I/O——即使背景 thread 永久
        # stall、再也不會來更新，現貨到了絕對到期時間也會在這裡自己失效。
        return ""
    return html


def _render_home_page() -> str:
    """首頁（`/`）內容：Hero/總覽/範例三段以純字串組裝，比照
    `_render_status_page`／`_render_costs_page` 寫法。包含「多幣總覽」區
    塊在內，整個函式現在是**零 I/O、零外部讀取**的純靜態渲染（credit-
    safe：首頁流量最高，不能是計費或可用性熱點）——「多幣總覽」的資料來
    自 `_render_home_overview_cached()` 讀 in-memory 現貨，真正的 I/O 全
    部移到背景 daemon thread，見該函式與模組頂部大段註解。

    Axis C #1（task #23）：「多幣總覽」區塊正確讀路徑演進三輪，皆為 codex
    review 抓出的同一個可用性 class（I/O 出現在 request 路徑上）的不同變
    體，記錄於此避免之後重踩：
      1. Phase 3：在首頁 request 當下逐幣讀 DynamoDB + ThreadPool，backend
         永久阻塞時 ThreadPool 孤兒執行緒無限累積、耗盡進程資源——整個
         移除。
      2. Axis C 第一版：module 級 TTL 快取 + 鎖內 single-flight + 短
         timeout 單次讀取單一預渲染 blob，但 reader 沒驗新鮮度，DynamoDB
         TTL 非同步刪除可能延遲數小時到 48 小時，會一直顯示過期判斷。
      3. Axis C 第二版：修好新鮮度驗證，但 request 路徑仍在鎖內同步呼叫
         `cache_get()`——0.5s timeout 不涵蓋憑證發現/DNS/JSON fallback
         I/O/任意形式的 backend stall，真的 stall 時 request 執行緒照樣
         被卡住，且 single-flight 鎖讓併發 request 全部排隊卡住。
    現在（第三版，by construction）：唯一一條背景 daemon thread 負責所有
    I/O（含新鮮度驗證），首頁 request 只讀 in-memory 變數，backend 再怎麼
    stall 都碰不到 request 執行緒——見 `_render_home_overview_cached()`
    docstring 完整論證。

    三段：Hero（一句話定位 + CTA 直接觸發一次真 BTC 多源分析，見
    `_hero_analyze_href`）、多幣總覽（若總覽
    blob 可讀，顯示各幣真信任分卡；讀失敗/miss 則整段不渲染）、產品總覽
    （事實→推論→結論三層架構，語彙沿用 `_render_report` 既有「步驟
    1/3、2/3、3/3」，不新發明一套說法）、範例入口（連到一個真實可執行的
    `/analyze` 查詢，非虛構資料——見 `_example_analyze_href`）。
    """
    e = html.escape
    hero_href = _hero_analyze_href()
    example_href = _example_analyze_href()
    overview_html = _render_home_overview_cached()
    overview_section = (
        f"""
<div class="tf-section">
  <h3>多幣信任總覽</h3>
  <p class="sub" style="margin:0 0 .4rem">背景排程定期快照，非即時計算——每張卡片皆為真實 pipeline 分析結果。</p>
  {overview_html}
</div>
"""
        if overview_html else ""
    )
    return f"""
<div class="tf-section tf-home-hero" style="border-color:#1f6feb;background:linear-gradient(135deg,rgba(31,111,235,.10),rgba(31,111,235,.02))">
  <h1>多源市場情報的信任提煉——不只給分數，給你為什麼</h1>
  <p class="sub" style="margin:0 0 .8rem">輸入幣種與問題，TrustForge 整合多來源證據，拆解成「事實 &#8594; 推論 &#8594; 結論」三層，
  附上信任評分與可展開的原始依據——不是一句話式的黑箱結論。</p>
  <a class="tf-hero-cta" href="{hero_href}">立即開始分析 &#8594;</a>
</div>
{overview_section}
<div class="tf-section">
  <h3>怎麼運作</h3>
  <p class="sub" style="margin:0 0 .3rem">左側 Query Console 選幣種、題型、輸入問題，送出後三層架構逐層產出：</p>
  <div class="tf-home-steps">
    <div class="tf-home-step">
      <span class="tf-step-badge">步驟 1/3</span><br><b>事實</b>
      <p class="sub">客觀資料——價格、鏈上數據、官方公告，逐條列出可追溯來源與時間戳。</p>
    </div>
    <div class="tf-home-step">
      <span class="tf-step-badge">步驟 2/3</span><br><b>推論</b>
      <p class="sub">Agent 綜合多來源證據的分析推理，附信任評分拆解與潛在操縱／協同訊號提示。</p>
    </div>
    <div class="tf-home-step">
      <span class="tf-step-badge">步驟 3/3</span><br><b>結論</b>
      <p class="sub">市場判斷、關鍵依據與限制、可能推翻結論的條件——結論可回溯，不是黑箱一句話。</p>
    </div>
  </div>
</div>

<div class="tf-section">
  <h3>範例</h3>
  <p class="sub" style="margin:0 0 .5rem">想先看看實際輸出長什麼樣？</p>
  <a class="tf-hero-cta tf-hero-cta-ghost" href="{example_href}">看範例報告 &#8594;</a>
  <p style="color:var(--tf-muted);font-size:.75rem;margin-top:.5rem">示意用途，離線示範資料，非即時市場資料。</p>
</div>
"""


def _safe_href(url: str) -> str:
    """安全連結產生器：scheme 為 http/https 才輸出 <a>，否則輸出純 html.escape 文字（不可點）。

    防護向量：javascript:、data:、vbscript:、file://、大小寫混用（JaVaScRiPt:）、
    前導空白（ javascript:）、空字串、相對路徑。
    escape 在任何分支都保留。
    """
    if not url:
        return html.escape(url)
    # 去除前後空白（前導空白是常見繞過手法：" javascript:alert(1)"）
    normalized = url.strip()
    parsed = urlparse(normalized)
    # 白名單：只允許 http / https（scheme 可能含大寫，統一 lower 比較）
    if parsed.scheme.lower() in {"http", "https"}:
        escaped_url = html.escape(normalized)
        escaped_display = html.escape(normalized[:80])
        return (
            f'<a href="{escaped_url}" target="_blank" rel="noopener">'
            f"{escaped_display}</a>"
        )
    # 非 http/https → 純文字，不產生可點擊連結
    return html.escape(url)


def _render_trust_breakdown(tc: dict, trust: float) -> str:
    """信任分項拆解 HTML 區塊（inline CSS，純 stdlib，免 JS）。

    顯示四分項（信譽/佐證/時效/操縱）＋公式 ＋ 佐證白話說明。
    tc 為空 dict（舊資料）→ 回空字串，優雅略過，不崩。
    操縱分項 > 0 時以紅色標示。
    """
    if not tc:
        return ""
    e = html.escape

    def _f(v) -> float:
        # 防禦：None/非數字/NaN/Inf → 0.0（trust_components 理應為合法 float，但不信任輸入）
        try:
            x = float(v)
        except (TypeError, ValueError):
            return 0.0
        return x if (x == x and x not in (float("inf"), float("-inf"))) else 0.0

    rep   = _f(tc.get("reputation",    0.0))
    corr  = _f(tc.get("corroboration", 0.0))
    rec   = _f(tc.get("recency",       0.0))
    manip = _f(tc.get("manipulation",  0.0))

    def mini_bar(val: float, color: str) -> str:
        pct = max(0, min(100, int(val * 100)))
        return (
            f'<span class="tf-bar-wrap" style="width:54px;height:7px;vertical-align:middle">'
            f'<span class="tf-bar" style="width:{pct}%;background:{color}"></span>'
            f'</span>'
        )

    # manip>0 一律紅色 #cb2431（回歸測試鎖定的顏色碼，勿改）；manip==0 用中性灰
    manip_color  = "#cb2431" if manip > 0 else "var(--tf-muted)"
    manip_weight = "font-weight:600;" if manip > 0 else ""

    corr_text  = "✓ 有獨立來源交叉佐證" if corr > 0 else "— 無交叉佐證"
    corr_color = "#3fb950"              if corr > 0 else "var(--tf-muted)"

    # ---- WHY caption：純由既有 float 值推導的白話說明，不新增資料欄位 ----
    why_rep = "高信譽來源佐證" if rep >= 0.7 else ("中等信譽來源" if rep >= 0.4 else "低信譽來源，需查證")
    why_corr = "有獨立來源交叉佐證" if corr > 0 else "單一來源，無交叉佐證"
    why_rec = "資料具時效性" if rec >= 0.7 else ("時效性中等" if rec >= 0.4 else "資料可能已過時")
    why_manip = "偵測到操縱風險信號，予以扣分" if manip > 0 else "未偵測到操縱風險信號"

    # W2 可解釋性接線：`agent.orchestrator._scored_to_evidence` 在
    # `dynamic_reputation=True` 時會把 `reputation_prior/final/agree_n/
    # contradict_n` 併進 `trust_components`（見該處）。這裡純渲染層再多加一行
    # WHY caption，說明「信譽為何是這個數字」——不是新資料，只是把已有的
    # trace 數值人話化，`tc` 沒有這幾個 key（`dynamic_reputation=False`／舊
    # 資料）時整段回空字串，優雅略過。
    rep_trace_html = ""
    if "reputation_prior" in tc and "reputation_final" in tc:
        rep_prior, rep_final = _f(tc.get("reputation_prior")), _f(tc.get("reputation_final"))
        agree_n = tc.get("reputation_agree_n", 0)
        contra_n = tc.get("reputation_contradict_n", 0)
        if abs(rep_final - rep_prior) > 1e-9:
            arrow = "↑" if rep_final > rep_prior else "↓"
            contra_part = f"，{contra_n} 源矛盾" if contra_n else ""
            trace_text = (
                f"動態信譽 {arrow}：{rep_prior:.2f}→{rep_final:.2f}"
                f"（{agree_n} 源互證{contra_part}）"
            )
        else:
            trace_text = f"動態信譽：{rep_prior:.2f}（樣本不足或無互證/矛盾，維持先驗）"
        rep_trace_html = (
            f'<div style="color:var(--tf-muted2);font-size:.68rem;padding-left:.2rem">'
            f'{e(trace_text)}</div>'
        )

    # ---- composite stacked bar：僅信譽/佐證/時效三個正向分項疊加；
    # 操縱不可並列成正向第四塊，改在下方以獨立紅色 deficit bar（靠右生長、代表扣分）呈現，
    # 對應真實公式：信任 = 信譽×0.5 + 佐證×0.25 + 時效×0.15 − 操縱×0.4 ----
    pos_weight = 0.5 + 0.25 + 0.15
    rep_c, corr_c, rec_c = rep * 0.5, corr * 0.25, rec * 0.15

    def _seg_pct(contrib: float) -> float:
        return max(0.0, min(100.0, contrib / pos_weight * 100))

    manip_deficit = manip * 0.4
    stacked_bar = (
        f'<div style="display:flex;height:14px;width:100%;background:var(--tf-bg);'
        f'border-radius:4px;overflow:hidden;border:1px solid var(--tf-border)">'
        f'<span style="height:100%;width:{_seg_pct(rep_c):.1f}%;background:#3fb950" '
        f'title="信譽 {rep:.2f}×0.50"></span>'
        f'<span style="height:100%;width:{_seg_pct(corr_c):.1f}%;background:#1f6feb" '
        f'title="佐證 {corr:.2f}×0.25"></span>'
        f'<span style="height:100%;width:{_seg_pct(rec_c):.1f}%;background:#8957e5" '
        f'title="時效 {rec:.2f}×0.15"></span>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;font-size:.65rem;'
        f'color:var(--tf-muted2);margin-top:.15rem">'
        f'<span>0</span><span>正向合計 {(rep_c + corr_c + rec_c):.2f}</span>'
        f'<span>{pos_weight:.2f}</span></div>'
        f'<div style="display:flex;height:8px;width:100%;background:var(--tf-bg);'
        f'border-radius:4px;overflow:hidden;border:1px solid rgba(203,36,49,.4);margin-top:.35rem" '
        f'title="操縱扣分 −{manip_deficit:.2f}">'
        f'<span style="margin-left:auto;height:100%;'
        f'width:{max(0.0, min(100.0, manip * 100)):.1f}%;background:#cb2431"></span>'
        f'</div>'
        f'<div style="color:#cb2431;font-size:.65rem;margin-top:.1rem">'
        f'扣分：操縱 {manip:.2f} × 0.40 = −{manip_deficit:.2f}</div>'
    )

    return (
        f'<div style="margin:.35rem 0;padding:.5rem .6rem;background:var(--tf-inset);'
        f'border-radius:6px;border:1px solid var(--tf-border);font-size:.78rem">'
        f'<div style="color:var(--tf-muted2);font-size:.7rem;font-weight:600;margin-bottom:.3rem">'
        f'信任分析（信譽×0.50 + 佐證×0.25 + 時效×0.15 − 操縱×0.40）</div>'
        f'{stacked_bar}'
        f'<div style="display:flex;flex-direction:column;gap:.3rem;margin-top:.5rem">'
        # 信譽
        f'<div><span style="white-space:nowrap">'
        f'<span style="color:var(--tf-muted)">信譽</span> '
        f'{mini_bar(rep, "#3fb950")} '
        f'<span style="color:var(--tf-text)">{rep:.2f}</span>'
        f'<span style="color:var(--tf-muted2)"> ×0.50</span></span>'
        f'<div style="color:var(--tf-muted2);font-size:.7rem;padding-left:.2rem">WHY {e(why_rep)}</div>'
        f'{rep_trace_html}</div>'
        # 佐證
        f'<div><span style="white-space:nowrap">'
        f'<span style="color:var(--tf-muted)">佐證</span> '
        f'{mini_bar(corr, "#1f6feb")} '
        f'<span style="color:var(--tf-text)">{corr:.2f}</span>'
        f'<span style="color:var(--tf-muted2)"> ×0.25</span></span>'
        f'<div style="color:var(--tf-muted2);font-size:.7rem;padding-left:.2rem">WHY {e(why_corr)}</div></div>'
        # 時效
        f'<div><span style="white-space:nowrap">'
        f'<span style="color:var(--tf-muted)">時效</span> '
        f'{mini_bar(rec, "#8957e5")} '
        f'<span style="color:var(--tf-text)">{rec:.2f}</span>'
        f'<span style="color:var(--tf-muted2)"> ×0.15</span></span>'
        f'<div style="color:var(--tf-muted2);font-size:.7rem;padding-left:.2rem">WHY {e(why_rec)}</div></div>'
        # 操縱（紅色扣分方向，非正向第四塊）
        f'<div><span style="white-space:nowrap">'
        f'<span style="color:var(--tf-muted)">操縱</span> '
        f'{mini_bar(manip, "#cb2431")} '
        f'<span style="color:{manip_color};{manip_weight}">{manip:.2f}</span>'
        f'<span style="color:var(--tf-muted2)"> ×0.40</span></span>'
        f'<div style="color:var(--tf-muted2);font-size:.7rem;padding-left:.2rem">WHY {e(why_manip)}</div></div>'
        f'</div>'
        f'<div style="margin-top:.4rem">'
        f'<span style="color:var(--tf-muted2)">→</span> '
        f'<span style="font-weight:600;color:var(--tf-text)">信任 {trust:.2f}</span>'
        f'</div>'
        f'<div style="color:{corr_color};font-size:.75rem;margin-top:.15rem">{e(corr_text)}</div>'
        f'</div>'
    )


def _render_trust_radar(dims: dict, evidence: list) -> str:
    """新核心#2（gray docs/PLAN-multicore-worldfirst.md，task #25）：多維度信任
    區塊（inline CSS，純 stdlib，zero-JS，CSP 不變）。

    `dims`：`agent.orchestrator.aggregate_trust_by_kind()` 的回傳值；空 dict
    （無 evidence／舊呼叫端未接線）→ 回空字串，優雅略過，不崩（比照
    `_render_trust_breakdown` 慣例）。`evidence`：同一次分析的完整證據清單，
    僅用於「展開查看該維度證據」——純渲染層重新依 kind 分組一次，不改動任何
    既有物件、不影響信任總分。

    每個維度一列（長條 + 分數 + 來源數／證據筆數），`<details>` 展開可回溯
    到該維度實際引用的證據來源。**誠實標記**：
    - `has_data=False`（本次未取得該類資料，如未啟用 coingecko 連接器時的
      price_live/sentiment/dev_activity）→ 灰底顯示「— 無資料」，不用 0
      冒充「查過但很低」（#24）。
    - `single_source=True`（gray 抓出 regulatory 僅 SEC 1 源、social 僅
      Reddit 1 源）→ 橘色「⚠ 單一來源」徽章，明確跟多源維度（如 news 12 源）
      區隔開，不包裝成同等可信。
    """
    if not dims:
        return ""
    e = html.escape

    ev_by_kind: dict[str, list] = {}
    for ev in evidence:
        ev_by_kind.setdefault(ev.kind, []).append(ev)

    def _color(v: float) -> str:
        if v >= 0.7:
            return "#3fb950"
        if v >= 0.4:
            return "#d9832a"
        return "#cb2431"

    rows: list[str] = []
    for kind, d in dims.items():
        label = e(str(d.get("label", kind)))
        if not d.get("has_data"):
            rows.append(
                f'<div style="display:flex;align-items:center;gap:.5rem;'
                f'padding:.3rem 0;opacity:.55;border-bottom:1px solid var(--tf-border)">'
                f'<span style="width:6.5em;flex:0 0 auto;font-size:.78rem">{label}</span>'
                f'<span class="tf-bar-wrap" style="flex:1 1 auto;height:9px">'
                f'<span class="tf-bar" style="width:0%;background:var(--tf-muted)"></span></span>'
                f'<span style="font-size:.72rem;color:var(--tf-muted);white-space:nowrap">— 無資料</span>'
                f'</div>'
            )
            continue
        trust = float(d.get("trust") or 0.0)
        pct = max(0, min(100, int(trust * 100)))
        n_sources = int(d.get("n_sources", 0))
        n_evidence = int(d.get("n_evidence", 0))
        single = bool(d.get("single_source"))
        single_badge = (
            ' <span style="color:#d9832a;font-weight:600" '
            'title="僅單一來源，非多源獨立交叉驗證，可信度不等同多源維度">'
            '&#9888; 單一來源</span>'
        ) if single else ""
        detail_items = ev_by_kind.get(kind, [])
        detail_html = "".join(
            f'<li>{e(source_display_name(it.source))}｜信任 {it.trust:.2f}｜'
            f'{e((it.content_reference or "")[:80])}</li>'
            for it in detail_items
        )
        rows.append(
            f'<details style="padding:.3rem 0;border-bottom:1px solid var(--tf-border)">'
            f'<summary style="cursor:pointer;display:flex;align-items:center;gap:.5rem">'
            f'<span style="width:6.5em;flex:0 0 auto;font-size:.78rem">{label}</span>'
            f'<span class="tf-bar-wrap" style="flex:1 1 auto;height:9px">'
            f'<span class="tf-bar" style="width:{pct}%;background:{_color(trust)}"></span></span>'
            f'<span style="font-size:.78rem;color:var(--tf-text);white-space:nowrap">'
            f'{trust:.2f}（{n_sources} 源／{n_evidence} 筆）</span>{single_badge}'
            f'</summary>'
            f'<ul style="margin:.35rem 0 0 1.3rem;padding:0;font-size:.7rem;'
            f'color:var(--tf-muted2)">{detail_html}</ul>'
            f'</details>'
        )

    return (
        '<div class="tf-section">'
        '<h3>多維度信任雷達</h3>'
        '<p style="color:var(--tf-muted2);font-size:.72rem;margin:.1rem 0 .6rem">'
        '按來源類型分維度聚合信任分（複用同一套「信譽×佐證×時效−操縱」公式），'
        '單一來源維度已明確標示、不等同多源交叉驗證；點各列可展開查看該維度證據。</p>'
        + "".join(rows) +
        '</div>'
    )


# Tier2 可解釋 UX：來源獨立性標籤依 kind 映射推導（純渲染層，不新增 schema
# 欄位）。故意拆成「獨立性層級（高/中/一般）」×「權威性（官方/第三方/社群）」
# 兩個維度——不可混為一談：CoinGecko（price_live）與 onchain（blockchain.info／
# Alternative.me FNG）雖然客觀、獨立性高，但都是**第三方聚合／公開 API**，
# 不是一手權威來源，標「官方」會讓使用者誤以為是交易所/監管機關一手資料，
# 破壞溯源 UX 的誠信基礎（codex provenance 準確性 review，PR #35 修正）。
#
# 官方／一手權威：hoyabit（交易所一手行情）、regulatory（SEC EDGAR 直接
# feed）、price（HOYA BIT 官方基準 OHLCV，見 ingestion/base.py
# OFFICIAL_OHLCV_DIR 說明）。
_OFFICIAL_KINDS = {"price", "hoyabit", "regulatory"}
# 第三方聚合／高獨立（客觀但非一手）：price_live（CoinGecko）、onchain
# （blockchain.info/Alternative.me，第三方公開 API，非鏈上一手節點）。
_THIRD_PARTY_KINDS = {"price_live", "onchain"}
# 社群／情緒：news/social/sentiment，中等獨立性。
_COMMUNITY_KINDS = {"news", "social", "sentiment"}


def _independence_tier(kind: str) -> tuple[str, str]:
    """回傳 (顯示標籤, 顏色) 供來源 pill 的 tier·權威性徽章使用。

    標籤格式「層級·權威性」：高·官方 / 高·第三方 / 中·社群 / 一般·輔助。
    `price_live`（CoinGecko）與 `onchain`（第三方聚合 API）獨立性層級雖與
    官方同屬「高」，但權威性標「第三方」，絕不會渲染成「官方」。
    """
    if kind in _OFFICIAL_KINDS:
        return "高·官方", "#3fb950"
    if kind in _THIRD_PARTY_KINDS:
        return "高·第三方", "#3fb950"
    if kind in _COMMUNITY_KINDS:
        return "中·社群", "#d9832a"
    return "一般·輔助", "var(--tf-muted)"


def _render_evidence_list(
    evidence: list, coin: str | None = None, start_idx: int = 0
) -> str:
    """evidence 渲染為帶信任橫條 + 可展開 <details> 的 <tr> 列表。

    - trust < 0.3 或 contrarian 項目顯示紅色 tf-low badge。
    - source_url 透過 _safe_href 渲染：http/https 輸出連結，其餘輸出純文字。
    - trust_components 有值時在 <details> 內顯示分項拆解。
    - 來源 pill 旁附 tier·獨立性標籤（依 kind 映射，見 `_independence_tier`）。
    - `ev.flags` 非空時附操縱紅旗徽章（沿用既有 `.tf-low` badge 樣式，見
      `trust.scoring._manipulation_flags` / `Evidence.flags`）——這是「確定
      判定為操縱」，已反映在 trust 分數。
    - `ev.info_flags` 非空時另外附中性資訊徽章（`.tf-info`，ℹ️，不用紅旗樣式，
      見 `trust.scoring._coordination_signals` / `Evidence.info_flags`）——
      這**不是**操縱判定，只是「文字相似度高，供人工判讀」的透明化提示，
      不影響 trust 分數，UI 上刻意與操縱紅旗區分開來，避免誤導。
    """
    e = html.escape
    rows: list[str] = []
    for i, ev in enumerate(evidence):
        idx = start_idx + i
        is_low = ev.trust < 0.3
        badge = (
            f' <span class="tf-low">&#9888; 低信任/操縱</span>'
            if is_low else ""
        )
        flags = getattr(ev, "flags", None) or []
        flags_badge = ""
        if flags:
            flags_text = "、".join(flags)
            flags_badge = (
                f' <span class="tf-low" title="操縱關鍵詞：{e(flags_text)}">'
                f'&#128681; {e(flags_text)}</span>'
            )
        info_flags = getattr(ev, "info_flags", None) or []
        info_flags_badge = ""
        if info_flags:
            info_flags_text = "、".join(info_flags)
            info_flags_badge = (
                f' <span class="tf-info" title="{e(info_flags_text)}">'
                f'&#8505; 相似簇</span>'
            )
        tier_label, tier_color = _independence_tier(ev.kind)
        tier_pill = (
            f'<span class="tf-tier-pill" '
            f'style="color:{tier_color};border:1px solid {tier_color}">'
            f'{e(tier_label)}</span>'
        )
        # 商業級視覺（Nansen/Messari 級）：來源 pill 旁附官方 LOGO（inline
        # SVG，simple-icons CC0）或（查無收錄品牌時）中性縮寫徽章——見
        # trustforge.brand_logos 模組 docstring，#24 鐵律不放錯 LOGO。
        # `ev.source` 出自各 ingestion 連接器固定的 `Source.name` 常數
        # （非使用者輸入）；fallback 顏色沿用這裡剛算出的 tier 顏色，跟
        # tier pill 視覺語言一致，不另外硬編品牌色。
        src_logo = source_logo_html(ev.source, fallback_color=tier_color)
        # docs/PLAN-source-branding.md：evidence pill 文字曾經直接印
        # `ev.source` 原始 slug（如 `coingecko-sentiment`），老闆真 Chrome
        # 看到工程師代號 —— 改用 `source_display_name()` 取品牌顯示名，
        # 不得再印裸 slug。仍走 `e()` 跳脫（跟其餘欄位一致，防禦性處理，
        # 即使目前 `ev.source` 非使用者輸入）。
        src_display = source_display_name(ev.source)
        # source_url 安全連結：_safe_href 驗 scheme，escape 由其內部保留
        if ev.source_url:
            url_html = _safe_href(ev.source_url)
        else:
            url_html = "&#8212;"
        coin_td = f"<td>{e(coin)}</td>" if coin is not None else ""
        row_style = ' style="background:rgba(248,81,73,.07)"' if is_low else ""
        # 來源 pill + 深色 <details> 卡殼（僅外包 class/樣式，e()／_safe_href 呼叫點與參數不變）
        rows.append(
            f"<tr{row_style}>"
            f"<td>E{idx}{badge}</td>"
            f"{coin_td}"
            f"<td>"
            f"<details>"
            f'<summary class="tf-ev-summary">'
            f'<span class="tf-src-pill">{src_logo} {e(src_display)}</span>'
            f'{tier_pill}'
            f'<span class="tf-ev-date">{e(ev.fetched_at)}</span>'
            f'{flags_badge}'
            f'{info_flags_badge}'
            f"</summary>"
            f'<div class="tf-ev-body">'
            f"<p style='margin:.3rem 0;font-size:.85rem;color:var(--tf-text2)'>{e(ev.content_reference)}</p>"
            f"<p style='margin:.3rem 0;font-size:.82rem;color:var(--tf-muted)'>URL: {url_html}</p>"
            f"{_render_trust_breakdown(ev.trust_components, ev.trust)}"
            f"</div>"
            f"</details>"
            f"</td>"
            f"<td>{_trust_bar(ev.trust)}</td>"
            f"</tr>"
        )
    return "".join(rows)


def _render_price_provenance(evidence: list) -> str:
    """把 HOYA 官方基準 OHLCV 與 CoinGecko 即時現價並列顯示、各自標明資料
    時間戳——世界第一重寫 Phase 2：修復「HOYA OHLCV 過期日期破綻」。

    背景（#24 誠實原則）：HOYA OHLCV 是定期更新的官方基準檔（非即時串流），
    只靠把預設問題文案的「近兩週」換成絕對日期還不夠——那段文字是塞進
    zero-JS 表單的 textarea 預填值，使用者切換幣種送出時常常帶著舊幣種
    的日期字樣一起送出，反而變成新的誤導來源（見 codex MEDIUM #2，PR
    #44）。所以查詢文字本身改回不含日期的 date-agnostic 措辭
    （`_DATE_AGNOSTIC_QUERY_SUFFIX`），精確日期只在**這裡**、結果頁本身
    負責——判審看到結果頁，要能一眼分辨「這份分析裡哪個數字是官方歷史
    基準、哪個是真即時報價」，不能讓兩者混在證據清單裡各自一行毫不起眼，
    含糊帶過「這是即時資料」的錯覺。

    做法：從既有 `evidence` 直接找 `source == "ohlcv-csv"`（OHLCV 價格事實，
    `ingestion/prices.py::price_facts`）與 `source == "coingecko-price"`
    （CoinGecko 即時現價，`ingestion/coingecko.py::CoinGeckoPriceSource`）
    各一筆，並列渲染各自的 `content_reference`（原始事實敘述）與
    `fetched_at`（真實時間戳，非現算）——**不新增任何資料流、不重寫既有
    連接器**，純粹是既有 evidence 的一層更顯眼的複寫呈現。

    缺源優雅處理：任一來源本輪未取得（cache miss / 429 等）→ 該行直接不
    渲染，不報錯、不留刺眼「無法取得」字樣（呼應 pipeline.py 的
    `report.limits` 中性化）；兩者皆缺 → 回傳空字串，整個區塊不顯示。
    """
    e = html.escape
    ohlcv_ev = next((ev for ev in evidence if ev.source == "ohlcv-csv"), None)
    live_ev = next((ev for ev in evidence if ev.source == "coingecko-price"), None)
    if ohlcv_ev is None and live_ev is None:
        return ""
    rows = []
    if ohlcv_ev is not None:
        rows.append(
            "<p style='margin:.4rem 0'><b>HOYA 官方基準 OHLCV</b>（歷史基準，非即時）："
            f"{e(ohlcv_ev.content_reference)}"
            f"<br><span class='tf-ev-date'>基準資料時間：{e(ohlcv_ev.fetched_at)}</span></p>"
        )
    if live_ev is not None:
        rows.append(
            "<p style='margin:.4rem 0'><b>CoinGecko 即時現價</b>（真 API 回應，非模擬）："
            f"{e(live_ev.content_reference)}"
            f"<br><span class='tf-ev-date'>擷取時間：{e(live_ev.fetched_at)}</span></p>"
        )
    return (
        '<div class="tf-section" style="border-left:4px solid #6e7681">'
        "<h3>資料基準（官方歷史 OHLCV vs 即時現價）</h3>"
        + "".join(rows)
        + "</div>"
    )


def _render_header(active_mode: str = "offline", *, minimal: bool = False) -> str:
    """組 `<header class="tf-hdr">`。

    `minimal=True`（世界第一重寫 Phase 1，老闆 Chrome 複驗後調整）：首頁 `/`
    專用——只留 logo + **小字/muted 版號** + 一個極簡的 `/status` 小連結，
    **不顯示**三檔模式徽號／`cost ledger` 連結。版號是老闆明確要求保留的
    （靠版號確認每次上版有沒有正常部署），小字不搶眼、不算 dev 雜訊；三檔
    模式徽號／cost ledger 連結才是雜訊，這兩樣仍是移位（不是刪功能）：
    模式能力／成本摘要仍完整顯示於 `/status`（見 `_render_status_page`）。
    版號一律讀既有 `VERSION`（`_version.py`），fallback 是 `"dev"` 就照實
    顯示 `"dev"`，不美化成假版號（老闆要看真實部署版號）。

    `minimal=False`（預設，`/costs`／`/status`／`/analyze` 結果頁沿用不變）：
    三檔徽號（dark 樣式，見 `.tf-mode-badge`）恆同時列出：離線示範／真資料·$0
    （?real=1）／真 Bedrock（?live=1+token）——但**只有 `active_mode` 指定的
    那一檔**渲染成 active（動畫脈動點 + 該檔專屬色），其餘兩檔渲染成灰色靜態
    能力標籤（無動畫），代表「可用但非本次」。這是分析結果頁的 provenance
    標示（本次畫面的證據到底來自樣本 / 真連接器 / 真 Bedrock），非首頁「dev
    artifacts」問題的範疇，不受本次首頁重寫影響（修復 MEDIUM 的既有邏輯
    原樣保留）。

    `active_mode`：`"offline"` | `"real"` | `"live"`，預設 `"offline"`。
    """
    # 商業級修復：logo 從純 <span>（死文字，內頁點下去無反應）改成
    # <a href="/">——所有頁面（含 minimal/一般 header）點 logo 都能回首頁，
    # 這是商用網站的基本期待。`text-decoration:none;color:inherit` 讓連結
    # 視覺上維持原本純文字外觀，不變成一般藍色底線超連結。
    logo = (
        '<a href="/" class="tf-logo" style="text-decoration:none;color:inherit;'
        'display:inline-flex;align-items:center">'
        '<span class="tf-logo-mark">&#9670;</span>Trust<b>Forge</b></a>'
    )
    if minimal:
        return (
            '<header class="tf-hdr">'
            f'{logo}'
            '<div class="tf-hdr-spacer"></div>'
            f'<span class="tf-hdr-version">{html.escape(VERSION)}</span>'
            '<a class="tf-hdr-status-link" href="/status">系統狀態</a>'
            '</header>'
        )

    live_capable = HAS_BEDROCK
    live_is_active = active_mode == "live" and live_capable

    def _badge(css_class: str, text: str, is_active: bool) -> str:
        # 文案皆為固定常數（非使用者輸入），escape 仍保留縱深防禦
        if is_active:
            return (
                f'<span class="tf-mode-badge {css_class} active">'
                f'<span class="tf-mode-dot"></span>{html.escape(text)}</span>'
            )
        return f'<span class="tf-mode-badge tf-static">{html.escape(text)}</span>'

    if live_capable:
        live_text = "LIVE · 真 Bedrock（?live=1）" if live_is_active else "真 Bedrock（?live=1）"
    else:
        live_text = "真 Bedrock（未設 BEDROCK_MODEL_ID）"

    mode = (
        _badge("tf-offline", "離線示範", active_mode == "offline")
        + _badge("tf-real", "真資料·$0（?real=1）", active_mode == "real")
        + _badge("tf-live", live_text, live_is_active)
    )
    return (
        '<header class="tf-hdr">'
        f'{logo}'
        f'<span class="tf-version">{html.escape(VERSION)}</span>'
        f'{mode}'
        '<div class="tf-hdr-spacer"></div>'
        '<a class="tf-costlink" href="/status">系統狀態</a>'
        f'<a class="tf-costlink" href="/costs">cost ledger {html.escape(_header_cost_display())}</a>'
        '</header>'
    )


def render_page(
    body: str = "",
    active_mode: str = "offline",
    run_stats_html: str = "",
    minimal_header: bool = False,
) -> str:
    """組完整 HTML（header + 表單 + body）。CLI web 與 Lambda handler 共用。

    `active_mode`：`"offline"` | `"real"` | `"live"`，預設 `"offline"`
    （首頁 `/`、`/costs` 等未經過分析流程的頁面，視為離線示範為預設 active 檔）。

    CEO 決策（PR #39，收斂）：拆掉 theme toggle 切換機制，固定
    `data-theme="dark"`（見 `_PAGE`），不再接受外部 `theme` 參數。原因：
    rtok render cache 是 process-local（重啟/部署/多 worker/TTL 過期即
    cache miss），"切主題不重跑 pipeline" 與 "不遺失已產出報告" 在無狀態
    SSR 架構下本質難以兩全——與其留一個會在特定條件下把使用者已產出的
    真報告弄丟的功能，不如先收斂成 dark-only，等 #20（結果持久化）做對
    後再重新開放 theme toggle。`var(--tf-*)` CSS custom properties／
    `:root[data-theme="light"]` 色票**仍保留**（不刪 token），只是目前
    沒有任何切換入口能到達 light。

    `run_stats_html`：左側 Query Console 面板的「RUN STATS」區塊（見
    `_render_run_stats()`），只有跑過一次真實分析（`/analyze` 成功）才有資料，
    預設空字串（首頁／`/costs`／尚未分析的錯誤頁不顯示）。

    `minimal_header`：世界第一重寫 Phase 1 新增。`True` 只有首頁 `/` 會傳
    （見 `do_GET`），其餘所有既有呼叫端（`/costs`、`/status`、`/analyze`、
    comparison、429/400/502 錯誤頁）維持預設 `False`，header 行為與既有
    測試斷言完全不變，零回歸。細節見 `_render_header`。
    """
    header_html = _render_header(active_mode, minimal=minimal_header)
    # 商業級修復：比較分析表單斷——原本只有單一 `coin` 下拉，選「比較分析」
    # 題型後送出必定缺第二個幣種、丟 ValueError。zero-JS 下無法依題型動態
    # 顯示/隱藏欄位，改為第二個幣種下拉常駐顯示（label 註明「比較分析時
    # 使用」，非比較題型下這個欄位單純被忽略，不影響 multi_source/
    # hypothesis）。預設值特意選 `COIN_POOL[1]`（非第一個幣），讓使用者
    # 一開始就看到兩個「不同」幣種，避免預設就撞到「不能相同」錯誤。
    # 實際合併成 `coin=A,B` 給 `_do_comparison` 見該函式。
    coin2_default = COIN_POOL[1] if len(COIN_POOL) > 1 else COIN_POOL[0]
    return _PAGE.format(
        header=header_html, body=body,
        run_stats=run_stats_html,
        coins=_opts(COIN_POOL),
        coins2=_opts(COIN_POOL, selected=coin2_default),
        types=_opts([t.value for t in QuestionType],
                    {"multi_source": "多源整合", "hypothesis": "假設驗證", "comparison": "比較分析"}),
        default_query=html.escape(f"分析該幣種{_DATE_AGNOSTIC_QUERY_SUFFIX}，整合多源資料"),
    )


def _cross_signal_sides(signal: dict) -> tuple[list[dict], list[dict]]:
    """從既有 `cross_source_signal` 欄位純推導雙欄 BULLISH/BEARISH 資料，供
    `_render_cross_signal` 背離時的結構化雙欄呈現使用。

    不新增資料流——只重組已存在的欄位：優先用 `stance_pairs`（若有，每筆
    `{"source","stance","text",...}` 直接對應一欄一則）；沒有 `stance_pairs`
    時退回聚合層級的 `objective_direction`/`sentiment_direction`（各代表一欄，
    描述文字為靜態說明，不引入新數值）。兩者皆缺 → 回傳 `([], [])`，呼叫端
    據此判斷是否顯示雙欄區塊（向後相容，舊有僅 summary 的樣態仍可運作）。
    """
    bullish: list[dict] = []
    bearish: list[dict] = []
    for p in signal.get("stance_pairs") or []:
        side = bullish if p.get("stance") == "bullish" else bearish
        side.append({"label": p.get("source", ""), "detail": p.get("text", "")})
    if bullish or bearish:
        return bullish, bearish

    obj_dir = signal.get("objective_direction")
    sent_dir = signal.get("sentiment_direction")
    if obj_dir in ("bullish", "bearish") and sent_dir in ("bullish", "bearish"):
        (bullish if obj_dir == "bullish" else bearish).append(
            {"label": "客觀數據（現價／鏈上）", "detail": "信任加權多數方向"}
        )
        (bullish if sent_dir == "bullish" else bearish).append(
            {"label": "情緒類（新聞／社群）", "detail": "信任加權多數方向"}
        )
    return bullish, bearish


def _render_cross_signal(signal: dict) -> str:
    """跨源訊號帶色框渲染（inline style，CSP 相容，無外部資源/JS）。

    背離 = 橙色系（#d9832a）；共識 = 藍色系（#1f6feb）。
    summary 與所有字串一律 html.escape（縱深防禦）。

    背離（`type == "divergence"`）且能從 `signal` 推導出雙方陣營（見
    `_cross_signal_sides`）時，額外附加結構化雙欄 BULLISH/BEARISH 對照，並標示
    誠實的「看漲 N 來源 · 看跌 M 來源」筆數對比（純渲染層計數，不新增資料流）。

    刻意不做「Δ%」這類量化幅度徽章：`stance_pairs`/聚合方向是去重矛盾集，
    未按信任加權，筆數差（如 2:1）換算成百分比會讓使用者誤把「來源數量的
    偶然性」當成可比的市場/背離強度——這是假精度，違反 #24 不造假原則
    （codex provenance 準確性 review 第二輪修正，PR #35）。要做真正的量化
    背離強度是正式工作，非本輪範圍；現在只誠實顯示筆數。

    推不出雙方（如舊資料 fixture 只有 summary）→ 保留舊版純文字渲染，功能零損。
    """
    e = html.escape
    sig_type = signal.get("type", "")
    if sig_type == "divergence":
        border_color = "#d9832a"
        bg_color = "rgba(217,131,42,.08)"
        type_label = "背離"
    else:
        border_color = "#1f6feb"
        bg_color = "rgba(31,111,235,.08)"
        type_label = "共識"
    summary_esc = e(signal.get("summary", ""))
    ids = signal.get("supporting_claim_ids", [])
    ids_html = (
        f'<small style="color:var(--tf-muted)">佐證 claim_ids：{e(", ".join(ids))}</small>'
        if ids else ""
    )

    div_html = ""
    if sig_type == "divergence":
        bullish, bearish = _cross_signal_sides(signal)
        if bullish or bearish:
            count_label = f"看漲 {len(bullish)} 來源 &middot; 看跌 {len(bearish)} 來源"

            def _side_body(items: list[dict]) -> str:
                if not items:
                    return '<p style="margin:0;font-size:.8rem;color:var(--tf-muted2)">&#8212;</p>'
                return "".join(
                    f'<p style="margin:.3rem 0 .1rem;font-size:.85rem;color:var(--tf-text)">'
                    f'<b>{e(it.get("label", ""))}</b></p>'
                    f'<p style="margin:0 0 .3rem;font-size:.8rem;color:var(--tf-muted)">'
                    f'{e(it.get("detail", ""))}</p>'
                    for it in items
                )

            bull_html = (
                f'<div class="tf-div-side tf-div-bull">'
                f'<span class="tf-div-tag" style="color:#3fb950;background:rgba(63,185,80,.12);'
                f'border:1px solid rgba(63,185,80,.4)">&#9650; BULLISH</span>'
                f'{_side_body(bullish)}'
                f'</div>'
            )
            bear_html = (
                f'<div class="tf-div-side tf-div-bear">'
                f'<span class="tf-div-tag" style="color:#f85149;background:rgba(248,81,73,.12);'
                f'border:1px solid rgba(248,81,73,.4)">&#9660; BEARISH</span>'
                f'{_side_body(bearish)}'
                f'</div>'
            )
            div_html = (
                f'<div style="margin:.5rem 0 0">'
                f'<span class="tf-div-tag" style="color:#f85149;background:rgba(248,81,73,.12);'
                f'border:1px solid rgba(248,81,73,.4)" '
                f'title="各陣營來源數量，非量化背離幅度/價格漲跌">{count_label}</span>'
                f'</div>'
                f'<div class="tf-div-grid">'
                f'{bull_html}'
                f'<div class="tf-div-mid">&#8800;</div>'
                f'{bear_html}'
                f'</div>'
            )

    return (
        f'<div class="tf-section" style="border-left:4px solid {border_color};background:{bg_color}">'
        f'<h3 style="color:{border_color}">跨源訊號（{e(type_label)}）</h3>'
        f'<p style="margin:.3rem 0">{summary_esc}</p>'
        f'{ids_html}'
        f'{div_html}'
        f'</div>'
    )


def _aggregate_trust_components(evidence: list) -> dict:
    """純渲染層彙總：對 evidence 逐筆 trust_components 取平均，供 dashboard hero 區塊
    的「Trust Breakdown」並排面板顯示。不新增資料欄位、不改真實信任公式——
    每筆 evidence 的 trust/trust_components 仍是 pipeline 算出的原值，這裡只是
    純視覺呈現用的算術平均，供使用者一眼看整體分項分布。
    """
    keys = ("reputation", "corroboration", "recency", "manipulation")
    sums = {k: 0.0 for k in keys}
    n = 0
    for ev in evidence:
        tc = getattr(ev, "trust_components", None) or {}
        if not tc:
            continue
        n += 1
        for k in keys:
            try:
                sums[k] += float(tc.get(k, 0.0))
            except (TypeError, ValueError):
                pass
    if n == 0:
        return {}
    return {k: sums[k] / n for k in keys}


def _render_run_stats(evidence: list, log=None) -> str:
    """左側 Query Console 面板的「RUN STATS」區塊——只用本次分析已產生的真實
    物件（`evidence`/`log.events`）算出，沒有任何示範/假造欄位（#24）：

    - Evidence rows／Unflagged ≥0.3／Flagged／Below 0.3：四者都是對同一份
      `evidence`（`_render_report`/`_render_comparison` 已收到的真實證據
      清單，即「證據清單」表格會逐列渲染的同一批物件）的計數，口徑彼此
      一致、可對帳（unflagged + flagged + below-0.3 ＝ evidence rows，恆
      成立）：
        * evidence rows   = len(evidence)（本輪納入報告的證據**列數**——
                            標籤故意不叫「Sources scanned」：這是「證據
                            清單」表格的列數，不是唯一來源數（同一來源
                            可能貢獻多筆證據，會被重複計入），也不是
                            pipeline 前端真的「掃描」了幾個來源，標
                            「scanned」是誇大成結構化計數的假語意（codex
                            複審 MEDIUM，CLAUDE 規範 #24 不做假語意）。
        * flagged         = ev.flags 非空的筆數（`trust.scoring._manipulation_flags`
                            命中，即證據清單裡渲染 &#128681; 操縱紅旗徽章的同一批）
                            —— 命名故意不用「dropped」：這批證據**仍顯示在
                            報告裡**（只是帶紅旗警示），沒有真的被過濾掉，
                            標「dropped」是失真宣稱（CEO Chrome 複審 MEDIUM
                            修正，CLAUDE 規範 #24 不做假語意）。
        * unflagged ≥0.3  = 其餘「未被紅旗、且 trust>=0.3」的筆數（沿用
                            `_render_evidence_list` 既有的 tf-low 0.3 門檻，
                            不新造閾值）——標籤故意不叫「Passed filter」：
                            低分／被紅旗的證據並沒有真的被過濾掉、仍全部
                            顯示在報告裡，稱「filter」暗示有東西被擋下來
                            了，是另一個假語意（codex 複審 MEDIUM）。
        * below 0.3       = evidence rows − unflagged≥0.3 − flagged（未被
                            紅旗但 trust<0.3 的證據；只在 >0 時才顯示這列，
                            避免多一列恆為 0 的雜訊）——先前版本沒有這一列，
                            導致兩者對不上證據總數，這裡補上讓四個數字永遠
                            能對帳。
      注意：這是「證據清單」這個階段的口徑（claim 抽取＋信任評分之後），不等於
      pipeline 最前端 `ingestion.collect()` 抓到的原始文件數（該數字目前只存在
      於 log 的自由文字 summary，沒有結構化欄位可安全取用，寧可不顯示也不用
      正則從文字反推假裝結構化——見 CLAUDE 規範 #24）。標籤本身也刻意選字
      面上精確等於它顯示的數字之真義，不誇大成「掃了幾個來源、過濾掉幾個」
      （codex 複審 MEDIUM，PR #39）。
    - Latency：`log.events` 最後一筆的 `elapsed_sec`（`ExecutionLog` 本就用來
      追蹤官方 15 分鐘執行預算的即時累積耗時，真實量測值，非估算）。
    - Model：`log.events` 內最後一筆 `tool=="bedrock.complete"` 的
      `params["model"]`（`agent.orchestrator` 已記錄的真實模型 id，或離線/
      未設模型時的 `"offline/regex-fallback"` 字面值）。

    任何一項算不出來就整列省略（不是顯示「—」或 0 這種看起來像真值的假值）；
    整體都沒有資料時回傳空字串，讓呼叫端不渲染這個區塊。
    """
    e = html.escape
    rows: list[tuple[str, str]] = []

    if evidence:
        n_flagged = sum(1 for ev in evidence if getattr(ev, "flags", None))
        n_passed = sum(
            1 for ev in evidence
            if not getattr(ev, "flags", None) and float(getattr(ev, "trust", 0.0)) >= 0.3
        )
        n_below = len(evidence) - n_passed - n_flagged
        rows.append(("Evidence rows", str(len(evidence))))
        rows.append(("Unflagged ≥ 0.3", str(n_passed)))
        rows.append(("Flagged", str(n_flagged)))
        if n_below > 0:
            rows.append(("Below 0.3", str(n_below)))

    if log is not None and getattr(log, "events", None):
        last_elapsed = log.events[-1].get("elapsed_sec")
        if isinstance(last_elapsed, (int, float)):
            rows.append(("Latency", f"{last_elapsed:.2f}s"))
        model = None
        for ev in reversed(log.events):
            if ev.get("tool") == "bedrock.complete":
                model = (ev.get("params") or {}).get("model")
                if model:
                    break
        if model:
            rows.append(("Model", str(model)))

    if not rows:
        return ""

    row_html = "".join(
        f'<div class="tf-stat-row"><span class="tf-stat-k">{e(k)}</span>'
        f'<span class="tf-stat-v">{e(v)}</span></div>'
        for k, v in rows
    )
    return f'<div class="tf-run-stats"><h3>Run Stats</h3>{row_html}</div>'


def _render_report(
    report, evidence, log=None, mode_extra: dict | None = None, show_json_link: bool = True
) -> str:
    """分析結果渲染為信任儀表板（頂部 hero：大 gauge + Trust Breakdown 並排，
    事實→推論→結論三段階梯卡片 + 信任橫條 + 可展開 evidence）。

    `log`：可選的 `ExecutionLog`，提供時嵌入「本次分析成本」卡（見 `_render_cost_card`）。
    comparison 頁面內嵌的單幣詳細分析不傳 `log`（避免重複顯示合併後的整體成本卡）。
    `mode_extra`：由 `_mode_extra_params()` 算出的模式參數 dict（`{}` /
    `{"real": "1"}` / `{"live": "1", "token": ...}`），跟 `coin`/`type`/`q` 一起
    交給 `_analyze_json_href()` 一次 `urlencode`，確保 real/live 模式點「下載
    JSON」時仍匯出同一模式的資料，不會落回預設 offline/sample 分支造成匯出跟
    畫面不一致。預設 `None`（視同 `{}`）＝不帶模式參數（向後相容）。

    HIGH 根治修復（連結構造根因）：舊版 `coin`/`type`/`q` 逐段只 `html.escape`
    串接進 href，從未 percent-encode——`q` 含 `& + # % "` 或非 ASCII 字元時，
    這些字元在 query string 語法裡的地位沒被正確轉義，瀏覽器/後端 `parse_qs`
    解碼後會誤判成參數分隔符，重新請求解出的參數跟畫面原始值兜不起來，破壞
    溯源。現在改由 `_analyze_json_href()` 統一處理：coin/type/q/mode 參數
    一次進同一個 `urlencode`（值層 percent-encode），組出的完整 query string
    再整段做一次 `html.escape`（href 屬性層），兩層各司其職、不再逐段補丁。

    `show_json_link`：是否渲染本函式自己的「下載 JSON」連結。HIGH 修復：comparison
    頁面內嵌的單幣詳細分析（`report.coin` 只有單一幣種，如 "BTC"）若各自帶一條用
    `coin={report.coin}&type=comparison` 建的下載連結，點下去會因缺第二個幣種、
    `_parse_comparison_coins` 無法從單一幣種重建雙幣配對而回 400——匯出連結整個壞掉。
    因此 comparison 場景（見 `_render_comparison`）呼叫內嵌的 `_render_report` 時傳
    `show_json_link=False`，改由 `_render_comparison` 自己用原始雙幣參數
    （`coin=A,B&type=comparison`）組出唯一一條正確的 top-level 下載連結。
    """
    e = html.escape
    # 商業級視覺（Nansen/Messari 級）：幣種標題旁附官方 LOGO（inline SVG，
    # simple-icons CC0，見 trustforge.brand_logos 模組 docstring）。
    # `report.coin` 一律出自 pipeline 已驗證過的 `COIN_POOL` 白名單（見
    # `_do_analyze`），非使用者輸入直接控制；`coin_logo_html` 內部仍只認
    # 白名單 dict，查無對應幣種回空字串，不會印出破圖。
    coin_logo = coin_logo_html(report.coin)
    facts = "".join(f"<li>{e(f)}</li>" for f in report.facts)
    infer = "".join(f"<li>{e(i)}</li>" for i in report.inferences)
    basis = "".join(
        f"<li><b>{e(b.claim)}</b> {''.join(f'[E{i}]' for i in b.evidence_idx)}"
        f"<br><small>{e(b.explanation)}</small></li>"
        for b in report.key_basis
    )
    limits = "".join(f"<li>{e(x)}</li>" for x in report.limits)
    flips = "".join(f"<li>{e(x)}</li>" for x in report.could_flip)
    contra = "".join(f"<li>{e(x)}</li>" for x in report.contrarian)
    # W4 codex 對抗審第 2 輪 [HIGH-1]：主 gauge 改用校準值＋三態標籤
    # （confidence_label() 已含三態），避免弱證據 abstain 時裸 confidence
    # （supporting 均值恆為 0 或 >=0.5）讓信心欄仍顯示「中/高」，跟
    # market_judgment 的「資料不足、暫不判斷」矛盾。
    conf_html = _conf_gauge(report.calibrated_confidence, report.confidence_label())
    agg_tc = _aggregate_trust_components(evidence)
    breakdown_html = _render_trust_breakdown(agg_tc, report.confidence) if agg_tc else ""
    # 新核心#2（task #25）：多維度信任雷達——按 source kind 聚合出分維度信任分，
    # 純渲染層重新聚合既有 evidence.trust，不多打任何呼叫（$0）。
    radar_html = _render_trust_radar(aggregate_trust_by_kind(evidence), evidence)
    ev_rows = _render_evidence_list(evidence)
    price_provenance_html = _render_price_provenance(evidence)
    cross_html = (
        _render_cross_signal(report.cross_source_signal)
        if getattr(report, "cross_source_signal", None) else ""
    )
    cost_html = _render_cost_card(log) if log is not None else ""
    json_link_html = (
        f'<p><a href="{_analyze_json_href(report.coin, report.question_type, report.question, mode_extra)}">'
        f'下載 JSON（report+evidence+log）</a></p>'
        if show_json_link else ""
    )
    return f"""
<div class="tf-dash-hdr">
  <span class="tf-coin-badge">{coin_logo}{' ' if coin_logo else ''}{e(report.coin)}</span>
  <span class="tf-dash-sep">●</span>
  <span class="tf-dash-q">{e(report.question)}</span>
</div>

{price_provenance_html}

<div class="tf-section" style="background:rgba(31,111,235,.08);border-color:#1f6feb">
  <h2 style="margin:0 0 .4rem">{e(report.coin)} · {e(report.question_type)}</h2>
  <p class="j">市場判斷：{e(report.market_judgment)}</p>
  <div class="tf-hero-row">
    {conf_html}
    <div>{breakdown_html}</div>
  </div>
</div>

{radar_html}

<div class="tf-section" style="border-left:4px solid #3fb950">
  <h3>事實（客觀資料）<span class="tf-step-badge">步驟 1/3</span></h3>
  <ul class="tf-step">{facts or '<li>&#8212;</li>'}</ul>
</div>

<div class="tf-section" style="border-left:4px solid #d9832a">
  <h3>推論（Agent 推理）<span class="tf-step-badge">步驟 2/3</span></h3>
  <ul class="tf-step">{infer or '<li>&#8212;</li>'}</ul>
</div>

<div class="tf-section" style="border-left:4px solid #1f6feb">
  <h3>結論 / 關鍵依據<span class="tf-step-badge">步驟 3/3</span></h3>
  <ul class="tf-step">{basis or '<li>&#8212;</li>'}</ul>
</div>

<div class="tf-section">
  <h3>信心說明 · 限制</h3>
  <ul>{limits or '<li>&#8212;</li>'}</ul>
  <h4>可能推翻結論的條件</h4>
  <ul>{flips or '<li>&#8212;</li>'}</ul>
</div>

{cross_html}

<div class="tf-section" style="border-left:4px solid #f85149">
  <h3>反方 / 低信任（未納入主結論）</h3>
  <ul>{contra or '<li>&#8212;</li>'}</ul>
</div>

<div class="tf-section">
  <h3>證據清單（信任橫條 · 點擊展開）</h3>
  <table>
    <tr><th>#</th><th>來源 / 摘要</th><th>信任分數</th></tr>
    {ev_rows}
  </table>
</div>

{cost_html}

{json_link_html}
"""


def _parse_comparison_coins(coin_raw: str, query: str) -> tuple[str, str] | None:
    """從 coin 參數或 query 文字解析出兩個比較幣種。

    解析優先順序：
      1. coin 參數含逗號（"BTC,ETH"，含全形逗號「，」正規化）
         → 必須剛好 2 個合法且相異幣種，否則 raise ValueError
      2. query 含「A 與/和/vs B」模式
      3. query 含兩個 COIN_POOL 幣種名稱（按文字左到右順序）

    Returns: (coin_a, coin_b) or None（無法從文字解析時）
    Raises:  ValueError（含逗號但不合格時）
    """
    import re

    # 全形逗號正規化
    coin_raw = coin_raw.replace("，", ",")

    # 1. coin 參數逗號分隔 ── 有逗號時強制嚴格驗證
    if "," in coin_raw:
        parts = [c.strip().upper() for c in coin_raw.split(",") if c.strip()]
        if len(parts) != 2:
            # 商業級修復：原訊息含 `coin=BTC,ETH` 這種內部查詢字串語法，
            # 使用者看不懂也不該看到系統內部參數格式——改成純中文引導。
            raise ValueError(
                f"比較分析需剛好選擇 2 個幣種（目前偵測到 {len(parts)} 個），"
                "請重新選擇兩個不同的幣種"
            )
        invalid = [p for p in parts if p not in COIN_POOL]
        if invalid:
            # 商業級修復：原訊息直接印出 Python list/tuple repr（如
            # `['DOGE']`／`('BTC', 'ETH', ...)`），對使用者不友善——改成
            # 自然語言列點。
            raise ValueError(
                f"幣種「{'、'.join(invalid)}」不在可選範圍內，"
                f"請選擇：{'、'.join(COIN_POOL)}"
            )
        if parts[0] == parts[1]:
            raise ValueError(
                f"兩個幣種不能相同（{parts[0]}），請選擇不同幣種"
            )
        return parts[0], parts[1]

    # 2. query 中的「A 與/和/vs B」
    m = re.search(
        r"([A-Za-z]{2,5})\s*(?:與|和|vs\.?|VS\.?)\s*([A-Za-z]{2,5})",
        query,
    )
    if m:
        a, b = m.group(1).upper(), m.group(2).upper()
        if a in COIN_POOL and b in COIN_POOL and a != b:
            return a, b

    # 3. query 出現任意兩個幣種名稱（按文字左到右順序，非 COIN_POOL 順序）
    q_upper = query.upper()
    positions = sorted(
        [(q_upper.find(c), c) for c in COIN_POOL if c in q_upper],
        key=lambda x: x[0],
    )
    seen: set[str] = set()
    found: list[str] = []
    for _, c in positions:
        if c not in seen:
            seen.add(c)
            found.append(c)
    if len(found) >= 2:
        return found[0], found[1]

    return None


def _render_comparison(
    report_a, evidence_a, report_b, evidence_b, query: str, log=None,
    mode_extra: dict | None = None,
) -> str:
    """comparison 結果渲染成 HTML（並列比較儀表板 + 信任橫條 + 可展開 evidence）。

    `log`：兩幣共用同一個 `ExecutionLog`（見 `pipeline.run_comparison`），提供時
    在頂層嵌一張合併「本次分析成本」卡（涵蓋兩幣總花費）；內嵌的單幣詳細分析
    不重複帶 log（避免同一份合併成本重複顯示兩次）。
    `mode_extra`：見 `_render_report`——由 `_mode_extra_params()` 算出的模式參數
    dict，供 top-level 下載連結跟 coin/type/q 一起進同一次 `urlencode`
    （預設 `None`，視同 `{}`＝不帶，向後相容）。

    HIGH 修復（comparison JSON 下載連結掉第二個幣）：內嵌的兩份單幣詳細分析各自
    只知道自己那一個 `report.coin`（如 "BTC"），若各自照 `_render_report` 預設行為
    產生「下載 JSON」連結，會建出 `coin=BTC&type=comparison` 這種單幣配 comparison
    型別的連結——`_do_comparison`/`_parse_comparison_coins` 需要 `coin=A,B` 兩個
    幣種才能重建配對，單幣版本一律 400，連結整個是壞的。修法：內嵌呼叫
    `_render_report(..., show_json_link=False)` 關掉各自的連結，改由本函式用
    原始雙幣（`report_a.coin`, `report_b.coin`）組唯一一條 top-level 正確連結
    （`coin=A,B&type=comparison&q=<query>`）。

    HIGH 根治修復（連結構造根因，見 `_render_report`/`_analyze_json_href`）：
    coin/type/query 一律跟 `mode_extra` 併成同一個 dict、一次 `urlencode`，不再
    逐段 `html.escape` 串接——`query` 含 `& + # % "` 或非 ASCII 中文時同樣受影響。
    """
    e = html.escape
    dir_a = report_a.direction or report_a._direction_label()
    dir_b = report_b.direction or report_b._direction_label()

    def _cmp_conf(conf: float, label: str) -> str:
        pct = max(0, min(100, int(conf * 100)))
        color = "#3fb950" if conf >= 0.7 else "#d9832a" if conf >= 0.45 else "#f85149"
        return (
            f'<span style="color:{color};font-weight:600">{html.escape(label)}'
            f"（{conf:.2f}）</span>"
            f'<div class="tf-bar-wrap" style="width:100px;margin-top:3px">'
            f'<div class="tf-bar" style="width:{pct}%;background:{color}"></div>'
            f"</div>"
        )

    src_a = len({ev.source for ev in evidence_a})
    src_b = len({ev.source for ev in evidence_b})
    ev_rows_a = _render_evidence_list(evidence_a, coin=report_a.coin, start_idx=0)
    ev_rows_b = _render_evidence_list(
        evidence_b, coin=report_b.coin, start_idx=len(evidence_a)
    )
    cost_html = _render_cost_card(log) if log is not None else ""
    # HIGH 修復：唯一一條 top-level 下載連結，用原始雙幣參數（coin=A,B）建，
    # 才能讓 _do_comparison/_parse_comparison_coins 重建正確的雙幣配對。
    json_link_html = (
        f'<p><a href="{_analyze_json_href(f"{report_a.coin},{report_b.coin}", "comparison", query, mode_extra)}">'
        f'下載 JSON（report_a+report_b+evidence+log）</a></p>'
    )
    return f"""
<div class="tf-dash-hdr">
  <span class="tf-coin-badge">{e(report_a.coin)}</span>
  <span class="tf-dash-sep">vs</span>
  <span class="tf-coin-badge">{e(report_b.coin)}</span>
  <span class="tf-dash-sep">●</span>
  <span class="tf-dash-q">{e(query)}</span>
</div>

<div class="tf-section" style="background:rgba(31,111,235,.08);border-color:#1f6feb">
  <h2 style="margin:0 0 .3rem">{e(report_a.coin)} vs {e(report_b.coin)} · comparison</h2>
  <p style="color:var(--tf-muted);margin:.2rem 0">{e(query)}</p>
</div>

<div class="tf-section">
  <h3>1. 相對強弱比較</h3>
  <table>
    <tr><th>項目</th><th>{e(report_a.coin)}</th><th>{e(report_b.coin)}</th></tr>
    <tr><td>市場方向</td><td>{e(dir_a)}</td><td>{e(dir_b)}</td></tr>
    <tr><td>整體信心</td>
        <td>{_cmp_conf(report_a.calibrated_confidence, report_a.confidence_label())}</td>
        <td>{_cmp_conf(report_b.calibrated_confidence, report_b.confidence_label())}</td></tr>
    <tr><td>獨立來源數</td><td>{src_a}</td><td>{src_b}</td></tr>
    <tr><td>反方訊號數</td><td>{len(report_a.contrarian)}</td><td>{len(report_b.contrarian)}</td></tr>
  </table>
</div>

<div class="tf-section">
  <h3>2. 合併證據清單（標明幣種，點擊展開）</h3>
  <table>
    <tr><th>#</th><th>幣種</th><th>來源 / 摘要</th><th>信任分數</th></tr>
    {ev_rows_a}
    {ev_rows_b}
  </table>
</div>

{cost_html}

{json_link_html}

<details class="tf-section"><summary>&#9654; {e(report_a.coin)} 詳細分析</summary>
{_render_report(report_a, evidence_a, show_json_link=False)}
</details>
<details class="tf-section"><summary>&#9654; {e(report_b.coin)} 詳細分析</summary>
{_render_report(report_b, evidence_b, show_json_link=False)}
</details>
"""



def _is_live_request(qs: dict) -> bool:
    """純判斷 live=1 是否成立（HAS_BEDROCK + token 正確），無副作用、不觸發限流。

    供 `_parse_live`（有副作用版）與 `_mode_link_suffix`（自我連結用，不能重複
    消耗限流額度）共用同一套判斷邏輯，避免兩處各寫一份、日後改一邊漏改另一邊。
    """
    req_token = qs.get("token", [""])[0]
    return (
        HAS_BEDROCK
        and qs.get("live", ["0"])[0] == "1"
        and bool(LIVE_TOKEN)
        and hmac.compare_digest(req_token, LIVE_TOKEN)
    )


def _parse_live(qs: dict, client_ip: str, *, enforce_rate_limit: bool = True) -> bool:
    """從 qs 解析 live 模式開關，並在 live+有 IP 時執行限流。

    `enforce_rate_limit=False`（#51 codex HIGH 複審：dedup×限流交互）：
    呼叫端（`_dedup_analyze_call` 的 leader）已經在 dedup 查找**之前**
    對這個 caller 自己的 IP 呼叫過 `_analyze_enforce_caller_rate_limit()`
    ，這裡就不能再重複呼叫 `_check_live_rate_limit`——否則同一個邏輯請求
    的 IP 會被計入限流 bucket 兩次，等於平白把該 IP 的額度砍半。
    `/analyze`、`/analyze.json` 兩條非 dedup 路由不受影響，維持預設
    `True`、原樣在這裡做限流。"""
    live = _is_live_request(qs)
    if live and client_ip and enforce_rate_limit:
        _check_live_rate_limit(client_ip)
    return live


def _is_sample_request(qs: dict, live: bool) -> bool:
    """純判斷「離線示範沙盒」（?sample=1）是否成立，無副作用、不觸發限流。

    世界第一重寫 Phase 2：離線示範不再是預設，改成 opt-in——想看樣本資料
    demo 的人才需要明確帶 `?sample=1`。`live` 已成立時 sample 不適用
    （live 優先，跟 `_is_real_request` 對稱）。
    """
    if live:
        return False
    return qs.get("sample", ["0"])[0] == "1"


def _is_real_request(qs: dict, live: bool) -> bool:
    """純判斷「真資料·$0」是否生效，無副作用、不觸發限流。見 `_is_live_request`。

    世界第一重寫 Phase 2：這是**預設**檔位——未帶任何 mode 參數即視為
    real（`data_mode=live, llm_mode=off`），是差異化賣點「真多源信任提煉」
    第一眼就要被看見，不必再靠 `?real=1` 才能觸發。唯二例外：`live` 已
    成立（live 優先），或明確帶 `?sample=1`（離線示範沙盒，opt-in）。
    `?real=1` 仍相容接受（顯式等同預設，不影響結果，向後相容既有連結）。
    """
    if live:
        return False
    return not _is_sample_request(qs, live)


def _parse_real(qs: dict, client_ip: str, live: bool, *, enforce_rate_limit: bool = True) -> bool:
    """從 qs 解析「真資料·$0」是否生效（預設檔位，見 `_is_real_request`）：
    走真連接器、免 Bedrock。

    不依賴 HAS_BEDROCK / token（與 live 檔位互相獨立）；`live` 已成立時
    real 不重複判斷（live 優先，走真 Bedrock 就不必再走真資料免敘事檔）。

    real 生效時走**自己獨立**的 per-IP 限流（`_check_real_rate_limit`／
    `_REAL_RATE_*`），刻意不共用 `_parse_live` 那組緊 bucket（codex HIGH，
    PR #44）：live 的 5 次/60s 是為了保護真的會燒錢的 Bedrock 配額，
    real-off 只讀 cache、不打 Bedrock，完全免費，套一樣緊的限流反而傷到
    「真資料·$0 成為預設」後的一般使用者——不帶任何參數瀏覽 `/analyze`
    就會觸發這條路徑，若沿用 live 的緊門檻，反向代理後所有使用者共用一個
    來源 IP，5 次/60s 就會整批 429。real-off 仍需要限流（防真連接器被
    洪水級高頻打爆），只是門檻改成 DoS 洪水級而非 Bedrock 成本級。

    `enforce_rate_limit=False`（#51 codex HIGH 複審：dedup×限流交互，
    見 `_parse_live` 同名參數）：`_dedup_analyze_call` 的 leader 已經在
    dedup 查找之前對這個 caller 自己的 IP 做過
    `_analyze_enforce_caller_rate_limit()`，這裡跳過避免重複計入同一個
    IP 的限流 bucket。
    """
    real = _is_real_request(qs, live)
    if real and client_ip and enforce_rate_limit:
        _check_real_rate_limit(client_ip)
    return real


def _mode_extra_params(qs: dict) -> dict:
    """算出目前請求應在自我連結（如 `/analyze.json` 下載連結）保留的模式參數，
    以 dict 形式回傳（`{}` / `{"sample": "1"}` / `{"live": "1", "token": <token>}`），
    交給呼叫端跟 coin/type/q 等其他參數**一起**丟進同一次 `urllib.parse.urlencode`
    組出完整 query string（見 `_analyze_json_href`）。

    世界第一重寫 Phase 2：「真資料·$0」是預設檔位，未帶任何 mode 參數即
    生效，故 real 生效時**不**額外帶參數（`{}`，維持連結乾淨、行為與預設
    一致）；只有離線示範沙盒（`sample=1`，opt-in）才需要在自我連結顯式帶
    `sample=1`，否則點下載/重新整理會落回預設的真資料檔位，跟畫面看到的
    離線樣本不一致。

    HIGH 根治修復（連結構造根因）：先前 `_mode_link_suffix` 回傳的是「半截字串」
    （如 `"&real=1"`、`"&live=1&token=<urlencoded token>"`），由呼叫端直接用
    f-string 接在 `coin=...&type=...&q=...` 這種逐段 html.escape 的字串尾巴——
    這代表 query string 的「值層編碼」被拆成兩種不一致的作法（coin/type/q 只
    html.escape、mode 參數才 urlencode），且 coin/type/q 從未做 percent-encoding，
    q 含 `& + # % "` 或非 ASCII 中文時，這些字元在 query string 語法裡的地位
    未被正確轉義，瀏覽器/後端 `parse_qs` 解碼後會把它們誤判成參數分隔符，重新
    請求解出來的 coin/type/q 跟畫面顯示的原始值兜不起來——不只 token（上輪已修），
    coin/type/q 全部都中，單幣與比較頁面都有問題。

    根治：不再讓任何一處「自己組半截 query 片段再字串串接」，改成所有自我連結
    一律呼叫本函式取得「額外模式參數」的 dict，跟 coin/type/q 合併成同一個 dict
    後，一次丟給 `urlencode()`（見 `_analyze_json_href`）——確保**所有**參數
    （不只 mode 參數）都經過同一套 percent-encoding，不會有漏做 urlencode 的
    參數存在。

    純函式：只重算跟 `_parse_live`/`_parse_real` 相同的判斷邏輯（`_is_live_request`/
    `_is_real_request`），不呼叫 `_check_live_rate_limit`，才不會讓同一次請求的
    自我連結重複消耗限流額度。
    """
    live = _is_live_request(qs)
    if live:
        return {"live": "1", "token": qs.get("token", [""])[0]}
    if _is_sample_request(qs, live):
        return {"sample": "1"}
    return {}


def _mode_link_suffix(qs: dict) -> str:
    """`_mode_extra_params(qs)` 的字串版包裝（向後相容既有呼叫端/測試對回傳格式
    的假設，例如 `"&real=1"`、`"&live=1&token=secret"`）。

    本身**不**再用於組 `/analyze.json` 自我連結（見 `_analyze_json_href`，連結
    一律用 `_mode_extra_params` 的 dict 版直接併入完整參數字典後一次 `urlencode`）
    ——但字串仍是用 `urlencode(_mode_extra_params(qs))` 產生（而非手動字串接），
    維持「值層一律 urlencode」的一致性，同時保留舊介面供其他呼叫端沿用。
    """
    extra = _mode_extra_params(qs)
    if not extra:
        return ""
    return f"&{urlencode(extra)}"


def _analyze_json_href(coin: str, qtype: str, q: str, extra: dict | None = None) -> str:
    """組出 `/analyze.json` 自我連結的完整、安全 href 屬性字串（可直接嵌進
    `href="..."`，已含 HTML escape）。

    HIGH 根治修復：兩層編碼分清楚、對所有參數一致套用，不可漏、不可混用：
      1. **query 值層**：`coin`/`type`/`q` 與 `extra`（`_mode_extra_params()` 算出
         的 real=1／live=1+token）**一次**用 `urllib.parse.urlencode` 組出完整
         query string——負責 URL 語法安全：任一參數值裡的 `& + = % # "` 或非
         ASCII 字元都會被正確 percent-encode，不會被瀏覽器/後端 `parse_qs`
         誤判成參數分隔符，重新請求時才能逐字還原成畫面上的原始值。
      2. **href 屬性層**：對第 1 步組出、已經 percent-encode 過的完整 query
         string 再做**一次** `html.escape`——負責 HTML 屬性語法安全（`&`→`&amp;`
         等，避免被誤判成 HTML entity 起點或造成屬性逃逸/HTML 注入）。
    兩層各司其職、只做一次，不疊加、不遺漏任何參數。
    """
    params = {"coin": coin, "type": qtype, "q": q}
    if extra:
        params.update(extra)
    return html.escape(f"/analyze.json?{urlencode(params)}")


def _active_mode(qs: dict) -> str:
    """算出本次請求實際生效的模式：`"offline"` | `"real"` | `"live"`。

    供 `render_page(..., active_mode=...)` 判斷「只標一個徽章 active」——修復
    MEDIUM：舊版三檔徽章恆同時顯示為可用/動畫，使用者無法判斷本次畫面的證據
    到底來自樣本、真連接器、還是真 Bedrock，對信任提煉產品是實質誤導。

    世界第一重寫 Phase 2：`"real"` 是**預設**回傳值——未帶任何 mode 參數的
    請求即判定為 real（呼應 `_is_real_request` 的新預設），`"offline"` 只在
    明確帶 `?sample=1` 時才成立。

    純函式：邏輯與 `_mode_link_suffix`/`_parse_live`/`_parse_real` 完全一致
    （皆基於 `_is_live_request`/`_is_real_request`/`_is_sample_request`），
    不呼叫 `_check_live_rate_limit`，不會讓同一次請求的畫面渲染重複消耗
    限流額度。
    """
    live = _is_live_request(qs)
    if live:
        return "live"
    if _is_sample_request(qs, live):
        return "offline"
    return "real"


def _do_analyze(
    qs: dict,
    client_ip: str = "",
    *,
    enforce_rate_limit: bool = True,
    online_stance_force_offline: bool | None = None,
) -> tuple:
    """單幣分析入口，永遠回傳 (report, evidence, log) 三元組。

    只處理 multi_source / hypothesis；comparison 請改用 _do_comparison。

    Raises:
        ValueError:        幣種非法 / q 過長 / pipeline 無資料
        TooManyRequests:   同 IP live 請求超速（`enforce_rate_limit=True` 時）
        其餘 Exception:    由呼叫方捕捉後回 502

    `enforce_rate_limit=False`（#51 codex HIGH 複審：dedup×限流交互）：
    只有 `/api/analyze` 的 dedup leader（`_dedup_analyze_call`）會傳
    `False`——因為 `_handle_api_analyze` 已經在 dedup 查找之前，對這個
    caller 自己的 IP 做過一次限流（`_analyze_enforce_caller_rate_limit`），
    這裡不能再重複計入同一個 IP 的 bucket。`/analyze`、`/analyze.json`
    （非 dedup 的畫面/匯出路由）維持預設 `True`，行為不變。

    `online_stance_force_offline`（#51 codex HIGH 複審 Round 11：key 漏
    caller-specific online-stance 降級）：`None`（預設，`/analyze`、
    `/analyze.json` 等非 dedup 路由沿用）維持原行為，這裡自己呼叫
    `_online_stance_force_offline(client_ip)` 決定要不要 degrade。若
    傳入具體 `bool`——只有 `/api/analyze` 的 dedup leader 會傳，值是
    `_handle_api_analyze` 在 dedup 查找之前，已經對這個 caller 自己的
    `client_ip` 算好（`_analyze_online_stance_force_offline_for_caller`）
    並納入 dedup key 的同一個判斷結果——直接沿用這個值，不再對同一個
    IP 重複呼叫、重複消耗 online-stance 限流 bucket。
    """
    coin_raw = (qs.get("coin", ["BTC"])[0]).strip()
    qtype = QuestionType(qs.get("type", ["multi_source"])[0])
    coin = coin_raw.upper()
    # codex MEDIUM #2（PR #44）：預設查詢文案改回 date-agnostic 常數，不再
    # 依 coin 動態組日期——精確、正確配對的日期改由結果頁
    # `_render_price_provenance()` 專職負責，見該函式 docstring。
    query = qs.get("q", [f"分析該幣種{_DATE_AGNOSTIC_QUERY_SUFFIX}"])[0]
    if len(query) > 1000:
        raise ValueError(f"問題長度不能超過 1000 字元（目前 {len(query)} 字元）")

    live = _parse_live(qs, client_ip, enforce_rate_limit=enforce_rate_limit)
    real = _parse_real(qs, client_ip, live, enforce_rate_limit=enforce_rate_limit)

    if coin not in COIN_POOL:
        # 同一批商業級修復：不印 Python tuple repr（如 `('BTC', 'ETH', ...)`）
        # 給使用者看，改自然語言列點。
        raise ValueError(f"幣種須為以下其中之一：{'、'.join(COIN_POOL)}")

    if real:
        # #9 online-stance 預算配額硬化：online-stance 未啟用時
        # `_online_stance_force_offline` 直接回 False（零開銷）；已啟用且
        # 本 IP 超過 online-stance 專用限流時，誠實 degrade 這次請求的
        # stance 判斷回離線，而不是讓整個分析失敗（見該函式 docstring）。
        # 只在真的要 degrade（True）時才多帶這個 kwarg 呼叫 `run()`——刻意
        # 不無條件帶 `force_stance_offline=False`，讓「未啟用 online-stance」
        # 這條（現行測試全部涵蓋的）路徑對 `run()` 的呼叫方式逐字不變，不會
        # 因為既有測試 monkeypatch 的窄簽名 fake_run（無此參數）而炸掉。
        _extra: dict = {}
        _force_offline = (
            _online_stance_force_offline(client_ip)
            if online_stance_force_offline is None
            else online_stance_force_offline
        )
        if _force_offline:
            _extra["force_stance_offline"] = True
        report, evidence, log = run(
            coin, query, qtype, data_mode="live", llm_mode="off", **_extra,
        )
    else:
        report, evidence, log = run(coin, query, qtype, offline=not live)
    # 成本會計階段3：只有 real/live（data_mode 最終落在 "live"，真的透過
    # CachedSource 讀連接器資料）才計入「真連接器」服務次數；純離線示範
    # （樣本資料，未觸碰任何連接器/cache）不計，見 `_record_analyze_service_calls`。
    if real or live:
        _record_analyze_service_calls(1)
    return report, evidence, log


def _do_comparison(
    qs: dict,
    client_ip: str = "",
    *,
    enforce_rate_limit: bool = True,
    online_stance_force_offline: bool | None = None,
) -> tuple:
    """雙幣比較分析入口，回傳 (report_a, evidence_a, report_b, evidence_b, log) 五元組。

    Raises:
        ValueError:        無法解析兩個幣種 / q 過長 / pipeline 無資料
        TooManyRequests:   同 IP live 請求超速（`enforce_rate_limit=True` 時）
        其餘 Exception:    由呼叫方捕捉後回 502

    `enforce_rate_limit=False`：見 `_do_analyze` 同名參數 docstring，
    語意完全一致（#51 codex HIGH 複審：dedup×限流交互）。

    `online_stance_force_offline`：見 `_do_analyze` 同名參數 docstring，
    語意完全一致（#51 codex HIGH 複審 Round 11：key 漏 caller-specific
    online-stance 降級）。
    """
    coin_raw = (qs.get("coin", ["BTC"])[0]).strip()
    # 商業級修復：表單新增常駐第二個幣種下拉（`coin2`，見 `render_page()`），
    # 這裡是它跟既有 `coin=A,B` 逗號語法（API 直連/下載 JSON 連結沿用）匯合
    # 的地方——只有 `coin_raw` 本身還沒帶逗號時才拼接 `coin2`，避免蓋掉
    # 明確的 `coin=A,B` 直連呼叫（後者才是唯一真相來源）。`coin2` 留白
    # （或跟表單未選比較分析題型時一起送出、被忽略）都不影響既有行為。
    coin2_raw = (qs.get("coin2", [""])[0]).strip()
    if coin2_raw and "," not in coin_raw:
        coin_raw = f"{coin_raw},{coin2_raw}"
    # codex MEDIUM #2（PR #44）：預設查詢文案改回 date-agnostic 常數，不再
    # 依 coin 動態組日期（先前版本曾用 probe 出 coin_a/coin_b 各自組日期，
    # 但根源問題是「查詢文字本身不該宣稱日期」，改文案內容治標不治本）。
    # 精確、正確配對兩幣各自的日期改由結果頁 `_render_price_provenance()`
    # 專職負責，見該函式 docstring。
    query = qs.get("q", [f"分析該幣種{_DATE_AGNOSTIC_QUERY_SUFFIX}"])[0]
    if len(query) > 1000:
        raise ValueError(f"問題長度不能超過 1000 字元（目前 {len(query)} 字元）")

    live = _parse_live(qs, client_ip, enforce_rate_limit=enforce_rate_limit)
    real = _parse_real(qs, client_ip, live, enforce_rate_limit=enforce_rate_limit)

    pair = _parse_comparison_coins(coin_raw, query)
    if pair is None:
        # 商業級修復：原訊息含 `coin=BTC,ETH` 內部查詢字串語法，直接洩露給
        # 使用者看——改成純中文引導，且不暴露任何內部參數命名/格式。
        raise ValueError(
            "比較分析需要選擇兩個幣種，請在左側「比較幣種」欄位選擇一個跟"
            f"「幣種」不同的幣種（可選：{'、'.join(COIN_POOL)}），"
            "或在問題文字中同時提及兩個幣種名稱"
        )
    coin_a, coin_b = pair
    if real:
        # #9 online-stance 預算配額硬化：見 `_do_analyze` 同名區塊註解，
        # comparison 兩幣共用同一次請求的限流判定/degrade 決定；同樣只在
        # 真的要 degrade 時才多帶 `force_stance_offline` kwarg。
        _extra: dict = {}
        _force_offline = (
            _online_stance_force_offline(client_ip)
            if online_stance_force_offline is None
            else online_stance_force_offline
        )
        if _force_offline:
            _extra["force_stance_offline"] = True
        report_a, evidence_a, report_b, evidence_b, log = run_comparison(
            coin_a, coin_b, query, data_mode="live", llm_mode="off", **_extra,
        )
    else:
        report_a, evidence_a, report_b, evidence_b, log = run_comparison(
            coin_a, coin_b, query, offline=not live
        )
    # 成本會計階段3：comparison 一次分析兩個幣種，各自都要讀一輪多來源資料，
    # 記 2 次（見 `_record_analyze_service_calls` docstring），理由同 `_do_analyze`。
    if real or live:
        _record_analyze_service_calls(2)
    return report_a, evidence_a, report_b, evidence_b, log


def _render_error_card(title: str, detail: str, *, retry_href: str | None = None) -> str:
    """商業級修復：統一錯誤頁品牌卡片，取代原本裸 `<p style='color:#c00'>...</p>`
    /純 `<p>404</p>`——4xx/5xx 一律走這裡，維持 `.tf-section` 卡片視覺（跟
    其餘頁面一致），並附「返回首頁」出口，讓使用者永遠有路可走，不會卡在
    死路錯誤頁。

    `title`：固定中文常數（依狀態碼），`detail`：實際錯誤訊息（可能含使用者
    輸入回顯，如題型驗證訊息——呼叫端已保證訊息本身不含內部參數語法，這裡
    仍統一 `html.escape` 縱深防禦）。

    `retry_href`：只有暫時性錯誤（429 限流、502 服務暫時無法使用）才給，
    讓使用者能直接重試同一個請求；使用者輸入錯誤（400）/路徑不存在（404）
    給了也沒意義（同樣輸入重試仍會失敗），維持 `None`。
    """
    e = html.escape
    retry_html = (
        f'<a class="tf-hero-cta tf-hero-cta-ghost" href="{e(retry_href)}" '
        'style="margin-left:.6rem">重試</a>'
        if retry_href else ""
    )
    return (
        '<div class="tf-section" style="border-color:#f85149;text-align:center;'
        'padding:2rem 1rem">'
        '<div style="font-size:1.8rem;line-height:1;color:#f85149;margin-bottom:.5rem">'
        "&#9670;</div>"
        f"<h2 style='margin:0 0 .5rem'>{e(title)}</h2>"
        f"<p class='sub' style='margin:0 0 1.1rem'>{e(detail)}</p>"
        f'<a class="tf-hero-cta" href="/">返回首頁</a>{retry_html}'
        "</div>"
    )


# ---------------------------------------------------------------------------
# 前後端分離 Phase 1（task #28，docs/PLAN-frontend-backend-split.md）：純新增
# JSON API 端點，統一 `{ok, data, error}` 信封。⛔ 鐵律：以下函式只新增，
# 絕不改動既有 SSR HTML 渲染函式（`_render_report`/`_render_home_page`/
# `_render_status_page`/`_render_costs_page` 等）——那些頁面是 LIVE，逐字
# 輸出不能變。這裡只是額外的資料組裝 + JSON 呈現層，直接重用既有「純資料」
# 函式（`aggregate_trust_by_kind`/`_aggregate_trust_components`/
# `get_freshness_snapshot`/`_get_ledger_summary`/`get_trust_history` 等本來
# 就回傳 dict/list，不是 HTML），不重寫任何既有計算邏輯。
#
# harper（CISO）安全審 must-have（本輪放行條件，逐項對應）：
#   1. 每個 `/api/*` 套用對應既有限流函式，換路徑不漏接限流：
#      `/api/analyze` 透過重用 `_do_analyze`/`_do_comparison`（其內部已呼叫
#      `_parse_live`/`_parse_real` → `_check_live_rate_limit`/
#      `_check_real_rate_limit`）自動繼承同一組限流；`/api/status`/
#      `/api/overview`/`/api/costs`/`/api/history` 皆為「逐 key 讀 cache
#      backend」同一類讀取工作，統一套用 `_check_status_rate_limit`（跟
#      `/status` 頁同一組 bucket/門檻）。`/api/health` 比照既有 `/healthz`
#      不設限流（零 I/O，無 cache/連接器讀取）。
#   2. `/api/analyze`、`/api/history` 的 `coin` 一律過既有 `COIN_POOL`
#      白名單，非法回 400 + 通用訊息（不洩露內部參數語法）。
#   3. 錯誤一律不透傳例外訊息/traceback/DynamoDB 錯誤細節，只回
#      `{ok:false,error:{code,message}}` 通用訊息（`/api/status` 甚至比
#      SSR `/status` 頁更保守，見 `_handle_api_status` docstring）。
#   4. `Content-Type: application/json; charset=utf-8`（`Handler._send()`
#      本身已恆定加 `X-Content-Type-Options: nosniff`，見該方法，兩者併用）。
#   5. 同源、不開 CORS；全部唯讀，`/api/analyze` 沿用既有預設 real-off
#      （$0）行為，不新增任何寫入路徑，也不放寬既有 live/real 限流。
# ---------------------------------------------------------------------------


def _json_envelope_ok(data) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def _json_envelope_err(code: str, message: str) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False
    )


def _price_provenance_data(evidence: list) -> dict:
    """`_render_price_provenance()` 的 JSON 資料版本——**不重用/不修改**該
    HTML 渲染函式本身（避免任何意外牽動 SSR 輸出），純粹從同一份 `evidence`
    重新找 `ohlcv-csv`/`coingecko-price` 兩筆來源，回結構化 dict。任一來源
    本輪未取得（cache miss / 429 等）該 key 直接不存在，兩者皆缺回空 dict
    ——與 HTML 版本「優雅缺席」語意一致。"""
    ohlcv_ev = next((ev for ev in evidence if ev.source == "ohlcv-csv"), None)
    live_ev = next((ev for ev in evidence if ev.source == "coingecko-price"), None)
    data: dict = {}
    if ohlcv_ev is not None:
        data["ohlcv"] = {
            "content_reference": ohlcv_ev.content_reference,
            "fetched_at": ohlcv_ev.fetched_at,
            "source_url": ohlcv_ev.source_url,
        }
    if live_ev is not None:
        data["live"] = {
            "content_reference": live_ev.content_reference,
            "fetched_at": live_ev.fetched_at,
            "source_url": live_ev.source_url,
        }
    return data


# ---------------------------------------------------------------------------
# #51 /api/analyze server-side idempotency（防重複送出）：Bedrock 開通前最後
# prereq——護欄 #9（daily cap + 並行原子預留）擋的是「同一 process 內累計花費
# 超上限」，擋不住「使用者連點兩下、或前端重試造成兩個獨立 request 各自真的
# 打一次 Bedrock」這種單純重複——兩次都在 cap 之內、各自都合法放行，但語意上
# 是同一件事被做了兩遍，白白多花一次 token 成本。
#
# 做法：in-flight dedup（single-flight coalescing）——**不含**任何
# post-completion 結果快取（見下方 codex HIGH 複審 Round 12：曾經有過
# 60 秒 TTL 結果快取，因為會 replay 過時分析結果而移除）。
#   - 同一 (type, coin[,coin2], query, sample/live/real/token, online-stance
#     force_offline) 的請求同時有一個正在跑時，後到的相同請求原地等待、
#     共用同一份真實結果，不各自進 `_do_analyze`/`_do_comparison`（因此也
#     不會各自呼叫 `_check_live_rate_limit`/`_check_real_rate_limit`，更
#     不會各自走到 `pipeline.run` 的 `try_reserve_request_budget()`）。
#   - leader 完成後，把結果發布給**當下這一批**還在等待的 in-flight
#     follower，然後立刻清掉這把 key 的 in-flight entry——**不**額外把
#     結果留存供之後才進來的全新請求複用。緊接在後（沒能排進同一輪
#     in-flight 等待）的下一個請求，會發現 in-flight 已清空，直接成為新
#     leader、對依賴 fresh 重新呼叫一次。
#   - key 用 per-key `threading.Event`（不是單一全域鎖），`compute()`
#     本身（可能是慢速真連接器/Bedrock I/O）在鎖外執行——不同 key 的請求
#     完全並行，不會被彼此拖慢。
#
# codex HIGH 複審（follower 無限阻塞資源耗盡）：leader（真連接器/Bedrock）
# 卡住/hang 死時，若 follower 的 `event.wait()` 沒有逾時上界，會無限期
# 阻塞該 server thread——同一 key 的重複請求越多，卡住的 thread 就越多，
# 一個 degraded 依賴（單純變慢或掛掉）會被放大成整個 server 的 thread
# 池耗盡（`ThreadingHTTPServer` 每個連線一條 thread）。修法：
#   - follower `event.wait(timeout=_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS)`
#     bounded wait；逾時未等到結果 → 拋 `_AnalyzeDedupTimeout`，
#     `_handle_api_analyze` 轉成 503 + 可重試訊息（不落回自己真的跑一次
#     ——避免「等太久」跟「真的失敗」混在一起放大 Bedrock 花費／
#     thundering herd；請 client 自行重試，仍受 #9 護欄與限流保護）。
#   - in-flight entry 額外記 leader 開始時間戳：往後新進來的請求（不是已
#     經在等待中的 follower）發現該 entry 已超過同一個逾時上界仍未完成，
#     視為「leader 已死掉/永久 hang 住」的 stale entry，直接取代成為新
#     leader（不再讓後續所有請求永遠 follow 一個死掉的 leader）。
#
# codex HIGH 複審#3（stale-leader 取代新 race）：上面「取代」只解決了
# 「follower 不會永遠等一個死掉的 leader」，但沒解決「舊 leader 之後
# 無條件寫共用快取」——舊（stale）leader 其實沒有被 kill、只是被取代成
# 不再是 in-flight 記錄裡「認可」的那個而已，它自己那個 Python thread
# 仍在背景繼續跑 `compute()`；若它稍後才完成（例如真的只是很慢，不是
# 真的 hang 死），先前的程式碼會無條件把它的結果寫進共用快取／清掉
# in-flight——覆寫掉新 leader 已經算出來的（更新的）結果，讓之後所有
# 呼叫端都拿到「過時」的 stale 結果。修法（generation/lease token 圍欄）：
#   - 每次成為某把 key 的 leader（不論是全新 key，還是取代一個 stale
#     entry），都從單一全域、單調遞增的 `_analyze_dedup_generation_seq`
#     領一個新的世代編號，隨 in-flight tuple 一起存
#     `(event, start_ts, generation)`。用全域計數器（而非每把 key 各自的
#     計數器字典）而不怕記憶體無限增長——不需要額外一個會無限增長的
#     per-key dict，且世代編號全域唯一、永不重複使用，即使某把 key 的
#     in-flight/cache 已經清掉很久之後才有新的 leader，也不會跟很久以前
#     某個仍在背景執行、最終才醒來的 stale leader 世代編號「巧合撞號」。
#   - leader 發布結果前（成功寫快取、失敗寫快取、清 in-flight 這三件事
#     都算，統一在同一個檢查點）：在鎖內重新讀 `_analyze_dedup_inflight`
#     目前存的世代編號，跟自己創建時領到的世代編號比對——**不相等**代表
#     自己已經被取代（stale），這次寫入整段 no-op（不寫快取、不動
#     in-flight，因為那已經不是自己的 entry）；只有世代編號仍相符（自己
#     還是「目前認可」的 leader）才真的發布。`event.set()` 則不受這個
#     檢查影響、永遠執行——讓當初 join 在這個（已被取代的舊）Event 上的
#     follower 能提早醒來，落回 fail-safe 分支（見 `_dedup_analyze_call`
#     docstring）自己去查一次目前的快取／視情況獨立跑一次，而不是傻等到
#     自己的逾時上界。
#   - **取捨（刻意接受、不是缺陷）**：Python 原生 thread 沒有辦法從外部
#     強制 cancel/kill 一個已經在跑的 `compute()`——所以「stale leader
#     被取代後，兩個 thread 在 45 秒重疊期內同時真的各打一次連接器/
#     Bedrock」這件事本身**沒有被消除**，只是被 fencing 保證「兩者之中
#     只有目前世代的那個會被寫進共用快取、服務給後續呼叫端」。這個重複
#     計算在架構上是罕見事件（只有 leader 真的卡到超過 45 秒門檻才會
#     觸發取代），且已經被 #9 護欓（每日 $ 上限 + atomic 預留）封頂，不會
#     無限放大——這裡要修的是「正確性」（絕不發布/服務過時結果），不是
#     「零重複」（那需要能真正 cancel 執行中 thread 的機制，超出目前
#     `ThreadingHTTPServer` + 原生 thread 的能力範圍）。
#
# codex HIGH 複審 Round 12（結果 staleness——60 秒 TTL 結果快取 replay
# 過時分析，#51 最終收斂）：先前版本在 leader 完成後，除了發布給當下
# in-flight follower，還額外把結果寫進一份共用的 60 秒 TTL 結果快取
# （key 是「請求內容」），供之後才進來、沒排進同一輪 in-flight 的重複
# 請求直接複用。但加密市場資料（價格/情緒…）時效敏感：使用者 30 秒後
# **刻意**重送相同 query 通常是要**最新**資料，"request 內容相等" 不等於
# "同一個邏輯操作"；60 秒 TTL 快取卻會把第一次跑出來的舊報告原封不動
# 回給第二次請求，完全不查一次更新的市場資料——這正是 #51 的目標
# 「防雙送＝防雙倍 Bedrock 花費」被過度延伸成「防雙送＝永遠共用同一份
# 結果」，兩者不是同一件事。
#
# 修法（移除 TTL 結果快取，只留 in-flight coalescing）：#51 真正要防的
# 是「並行/極短間隔內的相同請求各自重複觸發真連接器/Bedrock」——這件事
# 光靠 in-flight coalescing（followers join 目前正在跑的 leader）就已經
# 完整達成，不需要額外的 post-completion 快取。因此：leader 完成、把
# 結果發布給當下這一批 in-flight follower 之後，立刻清掉這把 key 的
# in-flight entry；之後**循序**進來的新請求（不管是 1 秒後還是 1 小時
# 後）一律 fresh 重新呼叫依賴，拿到當下最新的市場資料。這個簡化同時
# 消除了前幾輪一整類問題的根源——結果 staleness（本輪）、失敗快取
# 誤傷全新 caller（複審#5）、跨 mode/跨 IP replay（見 `_analyze_dedup_key`
# 歷次複審）、TTL 內請求彼此順序相依——這些全部都是「post-completion
# 結果快取」這個機制本身引入的，移掉它，這一整類問題不會再發生。**保留**
# 不變：in-flight 協調（generation fencing、single-flight 重入、
# stale-leader 喚醒，見上方複審#1~#4/MEDIUM）、per-caller 限流前置
# （`_analyze_enforce_caller_rate_limit`）、`effective_mode`/online-stance
# 這些「決定誰能 join 誰」的 key 組成變數（見 `_analyze_dedup_key`
# docstring）——這幾層都只影響「誰跟誰共用同一次還在跑的 compute()」，
# 跟「結果要不要在 compute() 完成後繼續留存」是兩個獨立的問題，只有後者
# 被本輪移除。原本用來在 leader 失敗時只共用給當下 in-flight follower的
# per-generation 暫存區（複審#5 引入的 `_analyze_dedup_follower_failure`）
# 現在擴大成同時承載**成功與失敗**兩種結果（改名
# `_analyze_dedup_follower_result`）——這正是「只給當下已在等待的
# follower 看、絕不影響任何全新請求」這個語意本來就該同時適用於成功與
# 失敗結果，不該只限定給失敗（成功結果原本額外進 TTL 快取是特例，拿掉
# TTL 快取後兩者理應走同一條路徑）。
# ---------------------------------------------------------------------------

# follower bounded wait 上界，同時也是「leader 多久沒完成算 stale」的門檻
# ——比單次分析（含真連接器/Bedrock）預期最長時間（見 #9 護欄 docstring）
# 略長，但仍是有限值，不讓一個掛掉的依賴無限期拖垮 server threads。
_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS = 45.0
# codex HIGH 複審 Round 12（結果 staleness，#51 最終收斂，見模組頂部大段
# 說明）：leader 完成（不論成功/失敗）後，結果**不**進任何以「請求內容」
# 為鍵、供之後全新請求複用的共用快取——只寫進這個**鍵是世代編號**的獨立
# 小暫存區，只給「當下已經在 `event.wait()` 上等這個世代的 follower」讀。
# 世代編號全域唯一、永不重複使用，因此往後任何全新請求（不論多快進來）
# **永遠不可能**巧合去查到這個世代——不需要用短 TTL 才能保證「不影響
# 全新 caller」，這裡的秒數只是給記憶體回收用的寬限期，不是安全邊界。
_ANALYZE_DEDUP_FOLLOWER_RESULT_GRACE_SECONDS = 5.0

_analyze_dedup_lock = threading.Lock()
# key -> (leader 完成時會 set() 的 Event, leader 開始時間戳, leader 的世代編號)
_analyze_dedup_inflight: dict[str, tuple[threading.Event, float, int]] = {}
# 世代編號 -> (寬限期到期時間戳, (ok, payload))——見上方
# `_ANALYZE_DEDUP_FOLLOWER_RESULT_GRACE_SECONDS` 說明；`ok=True` 時
# `payload` 是成功結果、`ok=False` 時 `payload` 是例外物件。只給當下已在
# 等該世代的 follower 讀，**不是**給全新請求命中的結果快取——這把 key
# 的下一個全新請求永遠是查 `_analyze_dedup_inflight`（空的）後 fresh
# 自己成為新 leader，不會查這個字典。
_analyze_dedup_follower_result: dict[int, tuple[float, tuple[bool, object]]] = {}
# 全域、單調遞增、永不重複使用的世代編號來源（見上方模組頂部大段說明的
# codex HIGH 複審#3）；只在持有 `_analyze_dedup_lock` 時讀寫。用單一全域
# 計數器而非每把 key 各自的計數器字典，避免額外一個會無限增長的 dict，
# 且保證世代編號全域唯一——不會有「同一把 key 被回收後世代編號重新從頭
# 算」而跟舊 stale leader 巧合撞號的風險。
_analyze_dedup_generation_seq = 0
# 舊世代編號 -> (寬限期到期時間戳, 取代它的新世代編號)。
#
# codex HIGH 複審 Round 12 補丁（stale-leader 取代時的 follower 交棒
# race）：一個 follower 原本 join 在舊 leader A（世代 a_gen）的 event
# 上；A 被取代成新 leader B（世代 b_gen）時，A 的 event 會被立刻 signal
# 喚醒這些 follower，但 follower 要等醒來後重新搶到
# `_analyze_dedup_lock`、重新讀一次 `_analyze_dedup_inflight`，才會知道
# 「現在的 leader 是 B」並把自己的 `joined_leader_generation` 更新成
# b_gen。若 B 快到「follower 還沒來得及重新搶到鎖」就已經完成並清空
# in-flight entry，follower 重新搶到鎖時會同時看到「a_gen 沒有已發布
# 結果（A 還在背景 hang 著）」跟「in-flight 是空的」——若不做任何補救，
# 它會誤判「沒有任何 leader 在跑」而自己 fresh 重新 compute() 一次，
# 在「stale leader 被取代」這個已知、罕見、且被本檔案模組頂部大段說明
# 視為可接受取捨的事件之上，再多引入一次原本不必要的重複真呼叫。
#
# 修法：每次「取代」發生時，除了 signal 舊 event，也在這裡記一筆
# 「a_gen 已經被 b_gen 取代」；follower 醒來後、查
# `_analyze_dedup_follower_result` 前，先沿著這個對照表把自己手上過時
# 的世代編號追到最新的一個，再用追到的世代編號去查結果／查 in-flight。
# 即使 follower 醒來時 B 早已完成並清空 in-flight，也一定能在這裡查到
# b_gen（寫入這個對照表跟建立 B 的 in-flight entry 是同一個臨界區內
# 完成，順序上必然早於 B 自己完成 compute() 並清 in-flight），從而正確
# 查到 `_analyze_dedup_follower_result[b_gen]`（B 完成時一定會寫入，
# 無論當下有沒有 follower 在等）拿到 B 真正算出來的結果，而不是誤判成
# 全新請求。寬限期沿用 `_ANALYZE_DEDUP_FOLLOWER_RESULT_GRACE_SECONDS`，
# 純粹是記憶體回收，不是安全邊界（全域世代編號唯一、永不重複使用）。
_analyze_dedup_generation_supersede: dict[int, tuple[float, int]] = {}


def _analyze_dedup_resolve_superseded_generation_locked(generation: int, now: float) -> int:
    """沿著 `_analyze_dedup_generation_supersede` 把過時的世代編號追到
    最新的一個。必須在持有 `_analyze_dedup_lock` 時呼叫。"""

    seen: set[int] = set()
    while generation not in seen:
        seen.add(generation)
        redirect = _analyze_dedup_generation_supersede.get(generation)
        if redirect is None or redirect[0] <= now:
            break
        generation = redirect[1]
    return generation


class _AnalyzeDedupTimeout(Exception):
    """#51 codex HIGH 複審：follower 等待 leader 結果超過
    `_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS` 仍未等到——leader（真連接器/
    Bedrock）可能單純變慢，也可能已經 hang 死。不落回自己真的跑一次
    （那樣等於「等太久」跟「真的失敗」處理方式一樣，會在依賴本來就
    degraded 時放大成 thundering herd/重複 Bedrock 花費），改由
    `_handle_api_analyze` 轉成 503 + 可重試訊息，交給 client 自行重試
    （仍受 #9 護欄與既有限流保護）。"""


def _analyze_effective_mode(qs: dict) -> str:
    """算出一次請求**實際會生效**的單一分析檔位：`"live"` / `"real"` /
    `"sample"`。

    codex HIGH 複審（key 構造正確 canonicalization，收斂前幾輪
    token/query 糾結，見 `_analyze_dedup_key` docstring 的完整問題背景）：
    這裡刻意重用跟 `_parse_live`/`_parse_real`（`_do_analyze`/
    `_do_comparison` 實際呼叫、決定 `pipeline.run` 的 `data_mode`/
    `llm_mode`/`offline` 的那組函式）**完全相同**的判斷邏輯——只是換成
    無副作用、不觸發限流的純函式版本（`_is_live_request`/
    `_is_sample_request`），跟 `_mode_extra_params()` 算自我連結參數用的
    是同一套（見該函式 docstring），確保「這裡算出來的 mode」跟「pipeline
    實際執行的 mode」永遠是同一個 source of truth，不會分岔。

    live 優先於 sample，sample 優先於 real（預設檔位）——跟
    `_is_sample_request`/`_is_real_request` 的優先序完全一致。
    """
    live = _is_live_request(qs)
    if live:
        return "live"
    if _is_sample_request(qs, live):
        return "sample"
    return "real"


def _analyze_dedup_key(
    *,
    qtype: QuestionType,
    coin_key: str,
    query: str,
    qs: dict,
    force_offline: bool = False,
) -> str:
    """把一次 `/api/analyze` 請求正規化成 in-flight dedup / 短期結果快取的
    key：`(type, coin[,coin2], query, effective_mode, force_offline)`
    （`force_offline`：Round 11 新增，見下方說明）。

    `coin_key`：呼叫端（`_handle_api_analyze`）已完成正規化＋白名單驗證的
    幣種鍵——單幣是已 `.upper()` 過的 `coin_raw`；comparison 是
    `_parse_comparison_coins()` 回傳的 `(coin_a, coin_b)` **依請求原始
    順序** `join(",")`，刻意不排序（codex HIGH 複審：`/api/analyze`
    comparison 回應是**有序**欄位——`report_a`/`evidence_a`/
    `price_provenance_a` 描述的是 `coin_a`，`_b` 系列描述 `coin_b`，順序
    對調語意就不同，不是同一份可互換的結果。若排序正規化成同一把 key，
    `coin=ETH,coin2=BTC`（`coin_a=ETH,coin_b=BTC`）會跟
    `coin=BTC,coin2=ETH`（`coin_a=BTC,coin_b=ETH`）命中同一份快取，讓後者
    的請求者拿到 A/B 對調、實際描述反過來的報告——同順序（同一個
    `coin_a,coin_b` 序列）的重複請求本來就會命中同一把 key、正常
    dedup；順序不同視為不同請求，各自跑一次是正確行為，不是「沒
    dedup 到」。

    `query`：已通過長度驗證的原始字串，**刻意不 `strip()`**（codex MEDIUM
    複審：key⟺實際執行必須一致）。`_do_analyze`/`_do_comparison` 內部
    重新讀 `qs.get("q", [...])[0]` 傳給 `pipeline.run`/`run_comparison`
    時**同樣不 strip**——若這裡對 key 做 strip，`"foo"` 跟 `" foo "`
    會被誤判成同一把 key、共用同一份 in-flight/快取 entry，但兩者傳給
    pipeline 的 prompt 其實不同（有無頭尾空白）：先到的那個請求會決定
    「共用」的實際執行內容與結果，後到的另一個字串不同的請求卻拿到
    別人 prompt 跑出來的答案——跟先前修 `token` 的 strip 問題同一個
    道理（見下方 `token` 段落），任何一段只要「key 正規化跟實際判斷/
    執行不一致」就會讓 dedup 錯誤地把「本該獨立」的兩個請求綁在一起。
    不做 strip 後，`"foo"` 與 `" foo "` 是不同 key、各自獨立 compute()，
    正確地各自跑各自的 prompt；沒有空白差異的一般重複請求（多數情況）
    不受影響，仍正常 dedup。刻意不做大小寫正規化（casefold）——中文
    查詢字大小寫不影響語意，但英文查詢字大小寫可能承載使用者刻意的
    語意差異，保守不動。

    `effective_mode`（codex HIGH 複審：key 構造正確 canonicalization，
    收斂前幾輪 token/query 糾結）：**不再**直接把 `sample`/`live`/`real`/
    `token` 四個原始 qs 值塞進 key，改用 `_analyze_effective_mode(qs)`
    算出的單一 `"live"`/`"real"`/`"sample"` 字串。

    先前版本的根本問題：這四個原始欄位裡，有些其實會被**忽略**——
    `live=1`（且 token 驗證通過）生效時，`sample`/`real` 完全不影響
    `_do_analyze`/`_do_comparison` 實際呼叫 `pipeline.run` 的方式（live
    優先，見 `_is_sample_request`/`_is_real_request` 的 `if live: return
    False`）；`real` 這個 query 參數本身也從來不被 `_is_real_request`
    讀取（它是「預設檔位」的向後相容顯式寫法，见該函式 docstring）。但
    先前的 key 卻原封不動把這些「會被忽略／不影響實際執行」的原始值也
    塞進 key——後果是：
      - `live=1&token=<TOKEN>&sample=1` 跟 `live=1&token=<TOKEN>&sample=2`
        （或任何不同的 `sample`/`real` 原始值）**實際執行完全相同**
        （都是同一次真 Bedrock 呼叫），但因為原始 `sample` 字串不同，
        舊 key 判成不同 entry、各自獨立 `compute()`——等於讓使用者只要
        任意變動一個「反正會被忽略」的參數，就能繞過 dedup、重複觸發
        真 Bedrock 呼叫（重複花費，正是 #51 要防的事）。
      - real 檔位下同理：`real=1` 跟不帶 `real` 參數（兩者都落在「預設
        真資料·$0」檔位，`_is_real_request` 根本不讀 `real` 這個 key）
        實際執行完全相同，舊 key 卻因為原始 `real` 字串不同判成不同
        entry，讓語意相同的請求需要各自 compute()，多做不必要的重複
        真連接器呼叫（雖然免費，但仍是「沒 dedup 到」，違反 dedup 的
        設計目的）。

    修法：key 只保留**跟實際執行結果真正相關**的單一 canonical 欄位
    `effective_mode`——用跟 `_do_analyze`/`_do_comparison` 呼叫
    `pipeline.run` 時**完全相同**的判斷邏輯（`_is_live_request`/
    `_is_sample_request`，見 `_analyze_effective_mode`）算出來，確保
    「key 相同 ⟺ 實際執行的 data_mode/llm_mode/offline 也相同」這個
    不變量精確成立，不多不少：
      - `live=1`（token 驗證通過）不管 `sample`/`real` 帶什麼原始值，
        `effective_mode` 都是 `"live"`——同一把 key，正確 dedup 成
        1 次真 Bedrock 呼叫，不再能靠變動被忽略的參數繞過。
      - `real=1` 跟不帶 `real` 參數（都落在預設真資料檔位）
        `effective_mode` 都是 `"real"`——同一把 key，同樣正確 dedup。
      - `sample=1` 精確比對成立時 `effective_mode="sample"`；`live`
        不成立且 `sample` 不精確等於 `"1"` 時落回 `"real"`——跟
        `_is_sample_request`/`_is_real_request` 的判斷完全一致。
      - token 的效果已經被 `effective_mode` 完整捕捉（不需要再單獨把
        `token` 塞進 key）：`token` 的唯一作用是透過
        `_is_live_request()` 的 `hmac.compare_digest` 逐位元組比對決定
        `live` 是否成立——比對通過 ⟹ `effective_mode="live"`；比對失敗
        （含先前 codex 複審抓到的「尾端多一個空白」case）⟹ 不成立
        `live`，`effective_mode` 落回 `"real"`（或 `"sample"`）——這正是
        `effective_mode` 這個單一欄位本來就該捕捉到的差異，不需要額外
        欄位。

    `qtype`/`coin_key`/`query` 三段維持不變（見上方對應段落：
    comparison 幣種順序不排序、query 不 strip）。

    序列化格式（codex HIGH 複審：key delimiter 注入跨 mode 碰撞，
    先前輪次修復，維持不變）：`json.dumps(...)`
    （`separators=(",", ":")`，緊湊、無空白）序列化成一個有序 list——
    JSON 字串序列化會對字串內容裡的雙引號、反斜線、控制字元（含
    `\x1f`）做逃逸，欄位之間的分隔（逗號）只會出現在字串**引號之外**
    ——user 輸入的原始位元組（`query` 仍是唯一保留的原始使用者輸入
    欄位）不管含什麼字元，都只能出現在自己那個被逃逸/包住的 JSON
    字串值裡，不可能偽造出跟別的欄位邊界（含 `effective_mode`
    這個由伺服器端計算、非 user 直接控制的枚舉字串）重疊的位元組序列。
    codex HIGH 複審（Round 11：key 漏 caller-specific online-stance
    降級）：real-mode 執行實際上還依賴一個**跟 caller 的 `client_ip`
    有關**的變數——`_online_stance_force_offline(client_ip)`（見該函式
    docstring；#9 online-stance 預算配額硬化：per-IP 的 online-stance
    專用限流耗盡時，`_do_analyze`/`_do_comparison` 會誠實 degrade 這次
    請求成 `force_stance_offline=True`，結果內容因此不同）。先前的 key
    完全沒有捕捉這個變數，導致：(1) 一個 online-stance 配額已耗盡的
    IP 當 leader 時，它的降級結果會被發布給當下同一把 key 命中的所有
    in-flight follower——配額本來還很充裕的其他 IP，若剛好在它 compute()
    期間送出同一把 key 的請求，會被迫共用（join）到這份降級結果；
    (2) 反過來，配額充裕的 IP 當 leader、產出正常 online-stance 結果，
    一個配額早就耗盡的 IP 若剛好 join 到同一輪 in-flight，會白拿一份
    「本來該被 degrade」的結果、完全繞過自己的配額限制，沒有真的消耗
    到它自己的 online-stance 限流 bucket。兩個方向都違反「per-IP
    配額」的護欄語意，而且哪個先到、哪個後到決定了另一方拿到什麼結果
    ——結果依到達順序而定，不是 deterministic。（Round 12 之後：共用
    的 60 秒 TTL 結果快取已整個移除，只剩 in-flight coalescing——這個
    污染風險改成只發生在「兩個 IP 剛好同時在等同一輪 compute()」的
    in-flight 期間，但風險本質跟修法完全不變：仍然是「key 沒捕捉到
    caller-specific 變數，導致不該共用的請求被迫共用同一份結果」。）

    修法：呼叫端（`_handle_api_analyze`）在算這個 key **之前**，先對
    這個 caller 自己的 `client_ip` 算一次
    `_analyze_online_stance_force_offline_for_caller(qs, client_ip)`
    ——跟 `_do_analyze`/`_do_comparison` 實際執行時判斷 degrade 用的
    完全同一套邏輯（`effective_mode == "real"` 時才呼叫
    `_online_stance_force_offline`），把算出來的 `bool` 傳進來當
    `force_offline` 參數，納入 key 的一部分：同一個 caller 不管最後是
    leader、follower、還是命中快取，都先用自己的 IP 決定「這次算出來
    是不是該 degrade」，狀態相同（都耗盡／都可用）的 caller 才會落在
    同一把 key、正確共用同一份結果；狀態不同（一個耗盡、一個可用）的
    caller 會落在不同把 key，各自拿到跟自己配額狀態相符的結果，不會
    互相污染，也不會有任何一方繞過自己的限流。`effective_mode` 不是
    `"real"` 時，`_do_analyze`/`_do_comparison` 根本不會呼叫
    `_online_stance_force_offline`（該邏輯整段包在 `if real:` 底下），
    因此這裡強制把 `force_offline` 正規化成 `False`，不讓呼叫端誤傳的
    值意外拆散本來該共用同一把 key 的 live/sample 請求（維持「key 只
    捕捉『實際執行真正用得到』的變數」這個 Round 9 就確立的原則）。
    """
    effective_mode = _analyze_effective_mode(qs)
    effective_force_offline = force_offline if effective_mode == "real" else False
    return json.dumps(
        [qtype.value, coin_key, query, effective_mode, effective_force_offline],
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _analyze_enforce_caller_rate_limit(qs: dict, client_ip: str) -> None:
    """#51 codex HIGH 複審（dedup×限流交互，兩個方向都要修）：

    1. 只有 dedup 的 leader 會真的呼叫 `_do_analyze`/`_do_comparison`，
       因而只有 leader 的 IP 會被 `_check_live_rate_limit`/
       `_check_real_rate_limit` 檢查、計入 bucket；follower（in-flight
       join 到 leader 的請求）完全跳過這個檢查——一個已經被限流的 IP，
       只要送出的請求「碰巧」跟某個正在進行中的合法請求同一把 dedup
       key，就能繞過自己的限流白拿一份真結果。
    2. 反過來，若 leader 因為自己 IP 超速而收到 `TooManyRequests`，
       這個 429 若被當成一般失敗共用給 follower（見 `_dedup_analyze_call`
       docstring），會讓完全不相干、根本沒超速的其他 IP 平白拿到別人的
       429（"429-poisoning"）。

    修法：把限流檢查搬到**每一個 caller 自己**、在 dedup 查找
    （`_analyze_dedup_key`/`_dedup_analyze_call`）之前執行一次——不管
    這次請求最後是變成 leader、follower、還是直接命中短期快取，都先
    對這個 caller 自己的 IP 過一次限流；限流本身開銷很小（純記憶體
    bucket 查表），跟共用「昂貴」的分析結果（真連接器/Bedrock）分開，
    各司其職。跟 `_parse_live`/`_parse_real` 用完全一致的純判斷邏輯
    （`_is_live_request`/`_is_real_request`），只是抽出來獨立於
    leader/follower 之外、對每個 caller 都執行；真正的
    `_do_analyze`/`_do_comparison`（只有 leader 會呼叫，見
    `_handle_api_analyze` 傳入的 `enforce_rate_limit=False`）不會再重複
    檢查同一個 IP，避免同一個邏輯請求的限流額度被計入兩次。

    這個檢查本身完全在 dedup 的 lock/cache 之外執行、不寫入任何共用
    狀態——429 只回給這個 caller 自己，不可能 poisoning 到別的 IP。
    """
    live = _is_live_request(qs)
    if live and client_ip:
        _check_live_rate_limit(client_ip)
    real = _is_real_request(qs, live)
    if real and client_ip:
        _check_real_rate_limit(client_ip)


def _analyze_online_stance_force_offline_for_caller(qs: dict, client_ip: str) -> bool:
    """#51 codex HIGH 複審（Round 11：key 漏 caller-specific online-stance
    降級）：跟 `_analyze_enforce_caller_rate_limit` 同樣的道理——只有
    dedup 的 leader 會真的呼叫 `_do_analyze`/`_do_comparison`，若
    online-stance 降級判斷（`_online_stance_force_offline`）繼續留在
    那裡面才算，就只有 leader 自己的 `client_ip` 會被檢查/計入
    online-stance 專用限流 bucket，且這個判斷結果只跟 leader 的 IP
    有關，卻會透過共用 dedup 快取 replay 給狀態完全不同的其他 IP（見
    `_analyze_dedup_key` docstring 的完整說明）。

    修法：在 dedup 查找之前，對**每一個 caller**（不管最後是 leader、
    follower、還是命中短期快取）各自的 `client_ip` 先算一次這個判斷，
    結果納入 `_analyze_dedup_key` 的 `force_offline` 參數；真正的
    `_do_analyze`/`_do_comparison`（只有 leader 會呼叫到）改傳這裡
    算好的值（`online_stance_force_offline=` 參數），不會再對同一個
    IP 重複呼叫 `_online_stance_force_offline`——避免同一個邏輯請求的
    online-stance 限流額度被消耗兩次。

    只在 `effective_mode == "real"` 時才呼叫 `_online_stance_force_
    offline`（回傳 `False` 前完全零開銷、不消耗任何 bucket）：跟
    `_do_analyze`/`_do_comparison` 實際執行時只在 `if real:` 分支底下
    才會用到這個判斷完全一致——`live`/`sample` 請求既然執行時根本不會
    走到這段邏輯，這裡也不該白白消耗這個 caller 的 online-stance 配額。
    """
    if _analyze_effective_mode(qs) != "real":
        return False
    return _online_stance_force_offline(client_ip)


def _analyze_dedup_follower_result_put_locked(
    generation: int, ok: bool, payload: object
) -> None:
    """呼叫端須已持有 `_analyze_dedup_lock`。codex HIGH 複審 Round 12（結果
    staleness，#51 最終收斂）：leader 完成（成功或失敗皆同一條路徑）時，
    把結果寫進**這個**世代編號專屬的小暫存區——**不是**任何以「請求
    內容」為鍵、供之後全新請求複用的共用快取——只給當下已在等這個世代的
    follower 讀（見 `_analyze_dedup_follower_result` 模組頂部說明）。世代
    編號全域唯一、永不重複使用，往後任何全新請求都不可能巧合命中這個
    世代——這裡的清掉過期項目純粹是記憶體回收，不是安全機制。

    `ok=True`：`payload` 是成功結果本身；`ok=False`：`payload` 是 leader
    遇到的例外物件，供 follower 原樣 `raise`。"""
    now = time.time()
    expired = [g for g, (exp, _) in _analyze_dedup_follower_result.items() if exp <= now]
    for g in expired:
        _analyze_dedup_follower_result.pop(g, None)
    _analyze_dedup_follower_result[generation] = (
        now + _ANALYZE_DEDUP_FOLLOWER_RESULT_GRACE_SECONDS,
        (ok, payload),
    )


def _dedup_analyze_call(key: str, compute: Callable[[], Any]) -> Any:
    """#51 server-side idempotency 核心：同一 `key` 的重複/並行請求只讓
    「第一個」（leader）真的呼叫 `compute()`（＝觸發 `_do_analyze`/
    `_do_comparison` → `pipeline.run` → 真連接器/Bedrock）；後到的相同
    請求（follower）原地等待，共用同一份真實結果物件本身——#24 不造假：
    follower 拿到的不是另外偽造的假資料，就是 leader 那份真結果。

    #9 護欄協同：dedup 判斷在 `compute()` **之前**——被 dedup 掉的
    follower 完全不會呼叫 `compute()`，因此也不會走到 `pipeline.run` 的
    `try_reserve_request_budget()` 每日預留——不重複佔用、不重複消耗任何
    護欄額度，甚至根本不進 Bedrock。

    leader 完成（成功或失敗皆同一條路徑，見下方 codex HIGH 複審 Round 12）
    後，把結果只發布給**當下這一批**已經在 `event.wait()` 上等待的
    follower（透過鍵是世代編號的 `_analyze_dedup_follower_result`），然後
    立刻清掉這把 `key` 的 in-flight entry——**不**額外把結果留存供之後
    才進來的全新請求複用。緊接在後（沒能排進同一輪 in-flight 等待）的
    下一個請求，會發現 in-flight 已清空，直接 fresh 成為新 leader，對
    依賴重新呼叫一次。exception 交由 `_handle_api_analyze` 既有的
    `except` 分支處理，跟 leader 收到的路徑完全一致。

    codex HIGH 複審#1（follower 無限阻塞資源耗盡，見模組頂部大段說明）：
    follower 用 `event.wait(timeout=_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS)`
    bounded wait，逾時拋 `_AnalyzeDedupTimeout`（轉 503，不落回自己真的
    跑一次）；leader 存活時間戳超過同一門檻的 in-flight entry 視為
    stale，新請求會直接取代成為新 leader，而不是永遠 follow 一個死掉的
    leader。

    codex HIGH 複審#2（dedup×限流交互）：`_check_live_rate_limit`/
    `_check_real_rate_limit` 已經搬到 `_handle_api_analyze` 呼叫
    `_dedup_analyze_call` **之前**，對每一個 caller（leader/follower 都
    一樣）各自的 IP 執行一次（見 `_analyze_enforce_caller_rate_limit`），
    這裡的 `compute()`（`enforce_rate_limit=False`）理論上不會再自己
    raise `TooManyRequests`。但為了 defense-in-depth（也對齊
    coordinator 明確要求），這裡仍明確**不快取、不 replay**
    `TooManyRequests`——那是 caller-specific 的失敗（限流/授權類，
    "只跟這個 caller 的身分/歷史有關"，不是分析本身的結果），絕不能被
    當成「一般失敗」存進供全新請求複用的共用快取 replay 給不相干的
    其他 IP（"429 poisoning"）。leader 遇到 `TooManyRequests` 時正常
    `raise` 給自己（Round 12 之後：跟其他例外走同一條「只共用給當下
    in-flight follower、絕不留存給全新請求」的路徑，見下方）；不在當下
    這批 in-flight follower 之列的下一個全新請求，會落回下面「fail-safe：
    自己真的跑一次」分支——這正是我們要的：每個 follower 各自的限流
    早就在 `_handle_api_analyze` 前置檢查過了（合格才會走到這裡），
    所以此時獨立跑一次是安全、正確的，不是「沒 dedup 到」。（Round 12
    之後：`TooManyRequests` 跟其他例外走同一條「只共用給當下 in-flight
    follower、絕不留存」的路徑，這裡的 defense-in-depth 保證自動成立，
    不需要再對例外型別特判，見下方複審#5/Round 12 段落。）

    codex HIGH 複審#3（stale-leader 取代新 race，見模組頂部大段說明）：
    「取代」stale in-flight entry 只解決了 follower 不會永遠等一個死掉的
    leader，沒解決舊（stale）leader 的 Python thread 其實還在背景繼續跑
    `compute()`、稍後才完成時「無條件」寫共用快取／清 in-flight 的問題
    ——那會覆寫掉新 leader 已經算出來的結果。修法：leader 創建時從全域
    單調遞增計數器 `_analyze_dedup_generation_seq` 領一個世代編號，跟
    in-flight tuple 存在一起；發布前（成功寫快取／失敗寫快取／清
    in-flight，三件事統一同一個檢查點）重新比對目前 in-flight 存的世代
    編號是否還等於自己領到的那個——不等於代表自己已被取代（stale），
    整段發布 no-op（不寫快取、不動 in-flight）；`event.set()` 不受這個
    檢查影響、永遠執行，讓 join 在這個（已被取代的）Event 上的 follower
    提早醒來、落回下方的協調 loop。取捨：Python thread 無法從外部強制
    cancel，stale leader 背景仍會把這次 `compute()` 跑完（重複呼叫一次
    真連接器/Bedrock）——這裡保證的是「不發布/服務過時結果」，不是
    「零重複呼叫」；重複呼叫本身罕見（只有真的卡超過 45 秒門檻才會被
    取代）且已受 #9 護欄（每日 $ 上限）封頂。

    codex HIGH 複審#4（thundering herd，見模組頂部大段說明）：複審#3 的
    generation fencing 只保證「stale leader 的結果不會被發布/服務」，但
    早期版本的 follower fallback 是「醒來、cache 沒有就直接自己
    `compute()`」——這在 stale leader 於 replacement 已經接手、但
    followers 逾時之前完成的情況下，會讓**每一個**原本 join 在 stale
    leader 上的 follower 各自獨立呼叫一次 `compute()`（cache 剛好是空的，
    因為 stale leader 的發布被 fence 掉了）——N 個 follower 就是 N 次多餘
    的真連接器/Bedrock 呼叫，直接打爆 single-flight 的成本安全承諾。

    修法：follower 的 fallback 改成**重入協調 lookup loop**（而不是直接
    `compute()`）：
      1. 若自己剛才實際加入等待的是某個世代（`joined_leader_generation`
         不是 `None`），先查那個世代專屬的 `_analyze_dedup_follower_
         result`——命中就直接用（大機率是自己原本在等的 leader，或
         replacement leader，剛發布給當下這批 follower 的結果）。
      2. 沒命中 → 查目前是否有活著的 leader（新世代的 in-flight entry）
         ——有就 join 它的 event，繼續等（用**同一個**、從本次呼叫一開始
         就固定下來的 deadline，不會因為多次重新 join 而不斷展延，整體
         阻塞時間仍然有界）。
      3. 都沒有（沒有自己這個世代的已發布結果、沒有活著的 leader）→
         在鎖內原子地創建新 in-flight entry、自己成為這把 key 唯一的新
         leader，跳出 loop 去 `compute()`。
      由於「查世代結果/查 in-flight/搶 leadership」這三步都在同一個
      `_analyze_dedup_lock` 臨界區內完成，多個同時醒來的 follower 之間
      不會重複搶到 leadership——只有其中恰好一個會在某次迭代看到
      「沒有活著的 leader」而真的成為新 leader，其餘的會在稍晚一點點的
      迭代看到這個新 leader（entry 已存在）而正確 join 它。整組相同請求
      任何時刻最多 1 個活著的 leader 在算，stale leader 造成的重複執行
      永遠被限制在「舊 leader 1 次 + 頂多 1 個 replacement leader」，不會
      是 N 個 follower 各自一次。

    codex MEDIUM 複審（follower liveness，最後一關）：複審#3／#4 解決了
    「stale leader 的結果不會被錯誤發布」跟「thundering herd」，但留下
    一個「舊 followers 白等」的活性（liveness）漏洞：
      1. **取代當下沒 signal 舊 event**：複審#3 的「取代」只是把
         `_analyze_dedup_inflight[key]` 的字典值換成新的 `(event,
         start_ts, generation)` tuple——但**已經**卡在 `event.wait()` 上
         的舊 follower，手上抓的是取代前的**舊 Event 物件參照**（Python
         區域變數，不會因為字典裡的值被換掉而自動更新），舊 leader 沒被
         喚醒、繼續等舊 event。若舊（stale）leader 是真的 hang 死、永遠
         不會自己完成（也就永遠不會呼叫 `event.set()`），這些舊
         follower 就會白等到自己那個從函式一開始就固定的 `deadline`——
         即使 replacement leader 早就成功把新結果發布到共用快取，這些
         舊 follower 對此一無所知（他們在等的是另一個永遠不會被 set 的
         Event），最終逾時只能回 503——這正是「本要靠 dedup+取代機制救
         回來的 degraded 情境」反而失敗收場。
      2. **自己 deadline 到期時沒有最後一次複查就直接 503**：即使沒有
         (1) 這個問題，也存在更窄的一個 race：follower 自己的固定
         `deadline` 到期（`remaining <= 0` 或 `event.wait()` 逾時）的那一
         刻，可能剛好跟「其他 follower 搶到 stale entry、取代成為新
         leader」的那一刻幾乎同時發生——由於這裡使用的是「函式一進來就
         固定死的 `deadline`」，就算取代/新 leader 幾乎立刻就緒，這個
         follower 也已經沒有預算再等，會直接 503，完全無視剛好近乎同時
         出現的新答案。

      修法：
      (a) **取代 stale leader 時對舊 event `.set()`**：偵測到 in-flight
          entry stale、決定取代它成為新 leader 的當下，先記住那個「即將
          被取代掉」的舊 Event 物件，寫入新 entry 後（鎖外，`Event.set()`
          本身無鎖、執行緒安全）呼叫它的 `.set()`——立刻喚醒所有卡在舊
          event 上的舊 follower，讓他們馬上重新回到協調 loop 頂端（見上
          方複審#4 的三步查找），而不是繼續空等一個永遠不會被 stale
          leader 自己 set 的 event。
      (b) **deadline 到期、真的要 503 前，做最後一次有界複查**：不管是
          `remaining <= 0` 還是 `event.wait()` 逾時，都先呼叫
          `_final_grace_check_before_giving_up()`——鎖內重查一次自己
          已加入的世代是否剛好已有發布結果（有→回傳 True，讓呼叫端
          `continue` 回 loop 頂端撿走）；沒有但有一個「活著」（未超過
          `_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS`
          門檻）的 in-flight entry，就 join 它剩餘的逾時預算（`event.wait
          (timeout=該 entry 剩餘秒數)`）——這一次 join 是**有界**的（頂多
          再多等一個新 leader「剩餘」的逾時預算，不是重新展延整個
          `deadline`），也**只做這一次**（不是新的無界迴圈）：join 到的
          新 leader 若也逾時，直接回傳 False，呼叫端照常 503。整體上，
          即使真的遇到連續多輪 stale-leader 取代鏈，每多等一輪都要求
          「真的有另一個活著的新 leader」且「真的又多流逝了將近一個
          `_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS`」的實際牆鐘時間，不是
          數學上無界，是任何實務部署都會自然收斂的有界重試，不違反
          bounded wait 的核心承諾。
      保留既有 generation fencing（複審#3）、JSON 序列化 key（見
      `_analyze_dedup_key` docstring）、single-flight 重入協調 loop
      （複審#4）、per-caller 限流前置（複審#2）全部不變。

    codex HIGH 複審#5（快取暫時性失敗把短暫故障變 60 秒故障，原始版本）：
    當時的版本在 leader `compute()` 拋出**任何**非 `TooManyRequests`
    例外時，都會把這個例外整個存進共用的 60 秒 TTL 結果快取——連接器
    timeout、Bedrock 暫時性失敗這類**本質上是暫時的**依賴故障，一旦被
    寫進去，接下來整整 60 秒內任何命中同一把 key 的請求（**不只是**
    當下已經在等的 follower，還包含 60 秒內才陸續進來、跟這次失敗完全
    無關的全新 caller）都會直接命中這筆快取、立刻重拋同一個過時例外
    ——完全不檢查依賴是否早就恢復。等於把一次可能只有幾百毫秒的暫時性
    故障，人為放大成保證整整 60 秒的故障，還波及所有剛好共用這把 key、
    跟原始那次失敗毫無關係的其他 caller。

    當時的修法（暫時性失敗不進 TTL 結果快取，只共用給當下 in-flight
    follower 然後清 entry）：leader 失敗時，`except` 分支不再把結果寫進
    共用的 60 秒 TTL 結果快取；改寫進另一個**鍵是這次失敗的世代編號**、
    獨立於 `key` 的小暫存區，然後清掉這把 `key` 的 in-flight entry，最後
    `event.set()`。

    為什麼需要另一個以世代編號為鍵的暫存區，而不是單純「清 entry 後靠
    重入協調 loop 讓 follower 自己重新判斷」：若只清 entry、什麼都不
    另外記錄，被 `event.set()` 喚醒的多個 follower 會依序重新搶
    `_analyze_dedup_lock`——查 in-flight（剛被清掉，沒有）——**其中
    一個**會原子地成為新 leader、立刻對依賴發起一次全新 `compute()`；
    但因為原本的失敗發生時可能有「好幾個」follower **同時**在等（不是
    只有 1 個），這個新 leader 完成（若依賴仍故障，很快又失敗）後，
    剩下沒搶到 leadership 的 follower 會被新 leader 的 `event.set()` 再
    喚醒一輪，其中又有一個成為下一個新 leader……如此每一輪只吸收 1 個
    follower、依序輪替，在 N 個 follower 同時等待、依賴仍持續故障的
    情況下會演變成 N 次依序、各自真的觸發依賴（含真連接器/Bedrock）的
    重複呼叫——這正是 single-flight dedup 一開始要防止的「重複花費」，
    等於在故障期間完全失去 dedup 的保護（且累積延遲、依賴呼叫次數都
    隨 follower 數量線性增長，不是 O(1)）。

    codex HIGH 複審 Round 12（結果 staleness，#51 最終收斂——見模組頂部
    大段說明）：後來連「成功結果」也發現同一類問題（60 秒 TTL 快取會
    把過時的分析結果 replay 給時效敏感的加密市場查詢），因此整個共用的
    60 秒 TTL 結果快取（不論成功/失敗）被**完全移除**，只留 in-flight
    coalescing。複審#5 當初「失敗結果只共用給當下這批 in-flight
    follower，讀完即棄、不留存給全新請求」這個設計，現在**原封不動
    套用到成功結果**——两者統一走同一個以世代編號為鍵的暫存區
    `_analyze_dedup_follower_result`（`ok, payload`；成功時
    `ok=True, payload=result`，失敗時 `ok=False, payload=exc`）：

    leader 完成（不論成功/失敗）時，把 `(ok, payload)` 寫進以**這次
    的世代編號**（`my_generation`，全域單調遞增、永不重複使用）為鍵的
    `_analyze_dedup_follower_result`（`_analyze_dedup_follower_result_
    put_locked`），然後清掉這把 `key` 的 in-flight entry，最後
    `event.set()`。每個 follower 在真正加入等待某個 leader 之前，都會
    記住自己實際加入的是哪個世代（`joined_leader_generation`，見下方
    迴圈實作）。被 `event.set()` 喚醒、回到迴圈頂端時，**優先**（在查
    `_analyze_dedup_inflight` 之前）用**非破壞性**的 `dict.get()` 查詢
    `_analyze_dedup_follower_result[joined_leader_generation]`——因為是
    `.get()` 不是 `.pop()`，當下所有等著同一個世代的 follower 都能各自
    讀到**同一個**結果（或例外）並直接回傳/`raise`，不需要誰先讀到就
    清掉、也不需要任何 follower 落回「自己變成新 leader」去重新
    `compute()`：真正做到「當下所有 follower 共用這一次結果」，而不是
    依序各自重跑（不論這次結果是成功還是失敗）。世代編號全域唯一、
    永不重複使用，因此**任何在這次完成之後才進來的全新請求**（不論
    多快到達）永遠不可能有 `joined_leader_generation` 剛好等於這個
    已經作廢的世代——安全性完全不依賴
    `_ANALYZE_DEDUP_FOLLOWER_RESULT_GRACE_SECONDS` 這個寬限期的長短，
    那只是給記憶體回收用，不是安全邊界（見該常數 docstring）。in-flight
    entry 在同一時刻被清空，讓真正的「下一個」請求（沒有
    `joined_leader_generation` 可查、從頭進來）走「查 in-flight（沒有）
    →自己成為新 leader」這條路徑，是不折不扣的 fresh retry：不論依賴
    這時是已經恢復（成功案例）還是市場資料已經更新（一般案例），都會
    拿到當下最新的一次真實呼叫結果，不會被任何舊快取卡住/頂替。
    `TooManyRequests` 先前的特判排除（避免 429-poisoning 到不相干的
    IP）已經被「一律寫進 per-generation 暫存區、只給當下那批 follower
    讀、絕不留存給全新請求」這個更嚴格的範圍自然涵蓋，不需要再對例外
    型別特判。
    """
    def _final_grace_check_before_giving_up() -> bool:
        """codex MEDIUM 複審（follower liveness，最後一關）：真的要放棄
        （拋出 `_AnalyzeDedupTimeout` → 503）之前的最後一次有界複查。

        回傳 `True`：呼叫端應該 `continue` 回協調 loop 頂端重新查一次
        （這一刻剛好有自己那個世代已發布的結果、或剛好有一個活著的新
        leader 完成/被喚醒了，loop 頂端會正確撿到）。
        回傳 `False`：真的什麼都沒有（沒有自己世代的已發布結果、沒有
        活著的 in-flight entry，或那個 entry 本身也已經逾時/stale），
        呼叫端照常 503。

        只執行「這一次」，不是新的無界迴圈：這裡 join 的新 leader 若也
        逾時，直接回傳 False，不會遞迴地一直找下一個又下一個 replacement
        無限期等下去；額外多等的時間上限是「這個新 leader 剩餘的逾時
        預算」（`_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS` 扣掉它已經跑了
        多久），不是重新展延整個 `deadline`。
        """
        now2 = time.time()
        with _analyze_dedup_lock:
            if joined_leader_generation is not None:
                resolved_generation2 = (
                    _analyze_dedup_resolve_superseded_generation_locked(
                        joined_leader_generation, now2
                    )
                )
                pending2 = _analyze_dedup_follower_result.get(resolved_generation2)
                if pending2 is not None and pending2[0] > now2:
                    return True
            inflight2 = _analyze_dedup_inflight.get(key)
            if inflight2 is None:
                return False
            grace_event, grace_started_at, _grace_generation = inflight2
            grace_remaining = _ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS - (now2 - grace_started_at)
            if grace_remaining <= 0:
                return False  # 這個 entry 本身也已經逾時/stale，沒東西可 join
        # 鎖外阻塞等待：避免在持有 `_analyze_dedup_lock` 期間睡眠，阻塞
        # 其他 caller 對 dedup 狀態的存取。
        grace_event.wait(timeout=grace_remaining)
        # 不論 wait 是被 set() 喚醒還是再次逾時，都回 True 讓呼叫端
        # `continue` 回 loop 頂端重新查一次——真的還是沒有（該 entry 逾時
        # 又沒人取代它），loop 頂端會依既有邏輯判斷 stale 並取代，或者
        # 頂端的 `remaining <= 0` 檢查會再次呼叫到這個函式，這次多半會
        # 因為 in-flight 也早已 stale 而回傳 False，正確收斂到 503。
        return True

    deadline = time.time() + _ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS
    # codex HIGH 複審#5 / Round 12：只有「這一輪迭代剛從 `event.wait()`
    # 醒來的 follower」才會非 None——記住自己剛才實際加入等待的是哪個
    # 世代的 leader，醒來後優先檢查那個世代是不是剛好已經有結果（見下方
    # `_analyze_dedup_follower_result` 檢查），跟真正全新的請求（從頭
    # 進來，從沒加入過任何世代）區分開。
    joined_leader_generation: int | None = None
    while True:
        now = time.time()
        stale_event_to_wake: threading.Event | None = None
        with _analyze_dedup_lock:
            if joined_leader_generation is not None:
                # codex HIGH 複審 Round 12 補丁：先把手上的世代編號沿著
                # `_analyze_dedup_generation_supersede` 追到最新的一個
                # （見該對照表的完整說明）——這樣即使自己是在 stale-leader
                # 被取代後才醒來、且取代者早已完成並清空 in-flight，也還
                # 是能正確查到取代者已發布的結果，不會誤判成全新請求。
                joined_leader_generation = (
                    _analyze_dedup_resolve_superseded_generation_locked(
                        joined_leader_generation, now
                    )
                )
                pending_result = _analyze_dedup_follower_result.get(joined_leader_generation)
                if pending_result is not None and pending_result[0] > now:
                    # codex HIGH 複審#5 / Round 12：我剛才等的那個世代的
                    # leader 已經完成（成功或失敗）——這個結果只共用給
                    # 「當下就已經在等它」的 follower（就是現在的我），
                    # 不會被寫進任何供全新請求複用的共用快取（Round 12
                    # 已整個移除）。拿到就直接回傳/raise，不落回下面變成
                    # 新 leader 重新 compute()——否則多個同時醒來的
                    # follower 會依序各自輪流變成新 leader、各自真的再
                    # 打一次依賴（見上方函式 docstring 的完整說明），
                    # 失去「共用同一次結果」的意義，也會在依賴仍故障時
                    # 放大成 N 次重複呼叫。
                    ok, payload = pending_result[1]
                    if ok:
                        return payload
                    raise payload

            inflight = _analyze_dedup_inflight.get(key)
            if inflight is not None:
                _leader_event, leader_started_at, _leader_generation = inflight
                if now - leader_started_at > _ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS:
                    # leader 早已超過逾時上界仍未完成——很可能已經 hang
                    # 死，視為 stale，直接取代成為新 leader（新 leader 會
                    # 領一個新的世代編號；舊 leader 稍後才完成時靠世代
                    # 編號比對 fencing，見上方 docstring 與下方發布段落）。
                    #
                    # codex MEDIUM 複審（follower liveness）：先記住這個
                    # 「即將被取代掉」的舊 Event 物件——若舊 leader 真的
                    # hang 死、永遠不會自己完成、永遠不會呼叫
                    # `event.set()`，卡在它上面等待的舊 follower 會白等到
                    # 自己的 deadline；取代它的當下就主動 signal 這個舊
                    # event，讓所有卡在它上面的舊 follower 立刻醒來、重新
                    # 回到協調 loop 頂端，而不是繼續空等一個可能永遠不會
                    # 被 set 的 Event。
                    stale_event_to_wake = _leader_event
                    stale_generation_replaced = _leader_generation
                    inflight = None
                else:
                    stale_generation_replaced = None
            else:
                stale_generation_replaced = None

            is_leader = inflight is None
            # codex HIGH 複審 Round 13：這一輪迭代能走到「成為/取代
            # leader」這條路，通常是因為前一輪 `event.wait()` 逾時後
            # `_final_grace_check_before_giving_up()` 回了 `True` 讓我們
            # `continue` 回到這裡——但那個 grace check 只確認「當下有
            # 東西可以再等一下」，並不保證「我自己排隊等候的 deadline
            # 還沒到」。若這個 caller 自己的 `deadline` 早就已經過了才
            # 繞回這裡，絕對不能讓它就地升級成新 leader 去 compute()：
            # 它已經沒有「還在使用者可接受的等待時間內」這個正當性，讓
            # 它去 compute() 只會 (1) 使用者早該收到 503 卻繼續被晾著、
            # (2) 跟真正剛進來的 fresh 請求重疊，多打一次依賴、(3) 若
            # 依賴仍持續故障/hang，這個執行緒會再被永久卡住一次、永遠
            # 不釋放。只有「deadline 還沒到」的 fresh 呼叫才有資格取代
            # stale leader，或在真空時成為新 leader；已逾時的一律視同
            # 逾時、回 503，且不動用/建立任何 in-flight entry——原本
            # stale 的 entry（若有）就留著給下一個真正 fresh 的呼叫處理，
            # 不會因為這裡跳過而遺失。
            caller_expired_before_claim = is_leader and now >= deadline
            if not caller_expired_before_claim:
                if is_leader:
                    event = threading.Event()
                    global _analyze_dedup_generation_seq
                    _analyze_dedup_generation_seq += 1
                    my_generation = _analyze_dedup_generation_seq
                    _analyze_dedup_inflight[key] = (event, now, my_generation)
                    if stale_generation_replaced is not None:
                        # codex HIGH 複審 Round 12 補丁：記一筆「舊世代已被我
                        # 取代」，讓卡在舊 leader event 上、稍後才醒來重新搶
                        # 到鎖的 follower 能追到我這裡（見
                        # `_analyze_dedup_generation_supersede` 完整說明）。
                        _analyze_dedup_generation_supersede[stale_generation_replaced] = (
                            now + _ANALYZE_DEDUP_FOLLOWER_RESULT_GRACE_SECONDS,
                            my_generation,
                        )
                else:
                    event = inflight[0]
                    my_generation = None  # follower 不發布，不需要世代編號
                    # codex HIGH 複審#5 / Round 12：記住這次實際加入等待的是
                    # 哪個世代，供醒來後在迴圈頂端查
                    # `_analyze_dedup_follower_result` 用（見上方檢查）。
                    joined_leader_generation = inflight[2]

        if stale_event_to_wake is not None:
            # 鎖外呼叫：`Event.set()` 本身無鎖、執行緒安全，鎖外呼叫純粹是
            # 為了盡快釋放 `_analyze_dedup_lock`。即使「我」自己已經逾時、
            # 沒資格取代這個 stale leader，還是主動 signal 它的舊
            # event——純粹是讓其他還沒逾時的 follower 提早發現 leader 已
            # 死、不用空等到自己的 deadline 才知道，不影響正確性。
            stale_event_to_wake.set()

        if caller_expired_before_claim:
            raise _AnalyzeDedupTimeout(
                f"分析請求排隊等候逾時（前一個相同請求執行超過"
                f"{int(_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS)} 秒），請稍後再試一次"
            )

        if is_leader:
            break  # 跳出協調 loop，往下真的去 compute()

        remaining = deadline - time.time()
        if remaining <= 0:
            if _final_grace_check_before_giving_up():
                continue
            raise _AnalyzeDedupTimeout(
                f"分析請求排隊等候逾時（前一個相同請求執行超過"
                f"{int(_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS)} 秒），請稍後再試一次"
            )
        completed = event.wait(timeout=remaining)
        if not completed:
            if _final_grace_check_before_giving_up():
                continue
            raise _AnalyzeDedupTimeout(
                f"分析請求排隊等候逾時（前一個相同請求執行超過"
                f"{int(_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS)} 秒），請稍後再試一次"
            )
        # 醒來後**不直接 fallback 自己 compute()**——重新回到 loop
        # 頂端：可能命中 replacement 已發布的快取、可能 join 到目前真正
        # 活著的 leader（不論是 replacement 本身，或它之後又被取代出的
        # 下一個 leader），或者（罕見：真的沒人在跑）在下一輪迭代原子地
        # 自己成為新 leader。三種結果都不是「盲目各自 compute()」，見上方
        # 複審#4 docstring。這裡的 bounded wait 沿用同一個從函式一開始
        # 就固定的 `deadline`（不因為重新 join 而展延整體等待時間），
        # 所以即使 leadership 被連續取代好幾輪，這個呼叫端的總阻塞時間
        # 仍然有界，跟修復前一樣不會無限期等待；`_final_grace_check_
        # before_giving_up()` 額外多給的一次機會也是有界的（見其
        # docstring），不破壞這個承諾。

    try:
        result = compute()
    except Exception as exc:
        with _analyze_dedup_lock:
            current = _analyze_dedup_inflight.get(key)
            is_current_leader = current is not None and current[2] == my_generation
            if is_current_leader:
                # codex HIGH 複審#5（快取暫時性失敗把短暫故障變 60 秒
                # 故障，原始版本）／Round 12（結果 staleness，#51 最終
                # 收斂，見上方函式 docstring）：不論例外種類（含先前
                # 特別排除在外的 `TooManyRequests`——那個排除本來是為了
                # 防 429-poisoning，現在改寫進世代專屬的 `_analyze_
                # dedup_follower_result`、不再是任何供全新請求複用的
                # 共用快取，429-poisoning 疑慮已經一併涵蓋，原本的特判
                # 分支不再需要）都寫進 `_analyze_dedup_follower_result`
                # （鍵是這次失敗的世代編號，只有當下已經在等**這個
                # 世代**的 follower 會去查它，見迴圈頂端的檢查），再清掉
                # in-flight entry——讓下一個全新請求（in-flight 已清空）
                # 走 fresh retry，不被任何快取卡住。
                _analyze_dedup_follower_result_put_locked(my_generation, False, exc)
                _analyze_dedup_inflight.pop(key, None)
            # else：generation fencing——自己已被取代（stale），這次發布
            # 整段 no-op：不寫 follower-result（沒有 follower 在等一個
            # 早就被取代掉的世代）、不動 in-flight（那已經不是自己的
            # entry，貿然 pop 會誤刪新 leader 的 entry）。
        event.set()
        raise
    with _analyze_dedup_lock:
        current = _analyze_dedup_inflight.get(key)
        is_current_leader = current is not None and current[2] == my_generation
        if is_current_leader:
            # codex HIGH 複審 Round 12：只發布給當下這批 in-flight
            # follower（鍵是 `my_generation`，非 `key`），然後立刻清掉
            # in-flight entry——**不**額外寫進任何以 `key`（請求內容）為
            # 鍵、供之後全新請求複用的共用快取（那個機制本輪已整個
            # 移除，見模組頂部大段說明）。
            _analyze_dedup_follower_result_put_locked(my_generation, True, result)
            _analyze_dedup_inflight.pop(key, None)
        # else：同上，generation fencing——stale leader 晚到的成功結果，
        # 一樣不能覆寫已經被取代後的狀態，整段 no-op。
    event.set()
    return result


def _handle_api_analyze(qs: dict, client_ip: str = "") -> tuple[int, str]:
    """`/api/analyze`：`/analyze.json` 既有輸出的擴充版，統一
    `{ok,data,error}` 信封 + 補上雷達（`aggregate_trust_by_kind`）／
    trust_components 聚合（`_aggregate_trust_components`）／
    price_provenance（`_price_provenance_data`）——三者輸入都已在既有
    `evidence` 陣列裡，純渲染層再彙總一次，不多打任何連接器/Bedrock 呼叫
    （$0）。

    完全重用 `_do_analyze`/`_do_comparison`（含其內建限流與驗證），不重寫
    分析邏輯；既有 `/analyze`、`/analyze.json` 兩條路由原樣不動。

    codex 複審 HIGH（同分支修復）：`_do_analyze`/`_do_comparison` 內部的
    `ValueError` 其實混雜兩種完全不同性質的情況（見兩者 docstring）——
    「幣種非法／q 過長」是**使用者輸入錯**，但「pipeline 無資料」
    （`pipeline.py::run()` 在 offline 樣本資料缺失時 raise）、以及深層
    ingestion 層解析上游回應失敗時的 `ValueError`，其實是**依賴/上游失敗**
    ——先前一律用同一個 `except ValueError → 400` 接住，會把真正的依賴
    問題偽裝成「你輸入錯」。

    修法（不碰 `_do_analyze`/`_do_comparison`/`_parse_comparison_coins`
    本身——三者純函式、無 I/O，這裡只是在呼叫依賴**之前**先呼叫同一批
    純函式的驗證邏輯做重複確認，兩邊邏輯是同一個 source of truth，不會
    分岔）：
      1. 先做零依賴的純請求驗證（type／query 長度／coin 白名單／
         comparison 兩幣解析）——驗證失敗在呼叫任何依賴之前就回 400。
      2. 驗證通過後才進 `try` 呼叫 `_do_analyze`/`_do_comparison`（含其
         內部的連接器/Bedrock/pipeline 呼叫）＋ payload 組裝／信封序列化，
         整段用單一 `except Exception`（含 `ValueError`）接住 → 一律回
         通用 502，不再按例外型別分流成 400。

    #51 server-side idempotency（防重複送出，Bedrock 開通前最後
    prereq）：驗證通過後、真的呼叫 `_do_analyze`/`_do_comparison` 之前，
    先算出 `_analyze_dedup_key()`（正規化 `type,coin[,coin2],query,
    effective_mode,force_offline`——`effective_mode` 是 `live`/`real`/
    `sample` 三選一的實際生效檔位；`force_offline` 是這個 caller 自己的
    `client_ip` 在 online-stance 配額上是否已耗盡（Round 11 codex HIGH
    複審新增），兩者皆見該函式 docstring），透過 `_dedup_analyze_call()`
    包一層 in-flight dedup（single-flight coalescing，**不含**
    post-completion 結果快取，見 Round 12 codex HIGH 複審）——同一組
    參數的並行/極短間隔內重複請求只有第一個（leader）真的觸發依賴呼叫，
    其餘原地等待、共用同一份真實結果，不會各自再打一次真連接器/
    Bedrock；leader 完成後 in-flight entry 立刻清空，之後**循序**進來
    的新請求一律 fresh 重新呼叫，拿當下最新資料。詳見
    `_dedup_analyze_call()` docstring。

    codex HIGH 複審（dedup×限流交互，兩個方向都要修，見
    `_analyze_enforce_caller_rate_limit` docstring）：per-IP 限流
    （`_check_live_rate_limit`/`_check_real_rate_limit`）改成在
    `_dedup_analyze_call()` **之前**，對每一個 caller（不管最後是
    leader、follower、還是命中短期快取）各自的 `client_ip` 執行一次；
    真正呼叫 `_do_analyze`/`_do_comparison`（只有 leader 會執行到）改傳
    `enforce_rate_limit=False`，避免同一個 caller 的 IP 被重複計入限流
    bucket 兩次。這樣一來：(1) 沒有任何 caller 能靠共用 leader 的結果
    繞過自己的限流；(2) 限流本身完全在 dedup 的共用 lock/cache 之外，
    一個 IP 的 429 不可能透過 dedup 快取 poisoning 到別的 IP（見
    `_dedup_analyze_call` 對 `TooManyRequests` 的特殊處理）。

    codex HIGH 複審 Round 11（key 漏 caller-specific online-stance
    降級，見 `_analyze_dedup_key`／`_analyze_online_stance_force_
    offline_for_caller` docstring）：跟上面 per-IP 限流同樣的道理，
    `_do_analyze`/`_do_comparison` real-mode 執行時是否 degrade 成
    `force_stance_offline=True` 也是跟這個 caller 的 `client_ip` 有關
    的變數，一樣改成在 `dedup_key` 算出來之前，對每一個 caller 各自的
    `client_ip` 先算一次（`force_offline`），納入 `dedup_key`；真正呼叫
    `_do_analyze`/`_do_comparison` 改傳算好的
    `online_stance_force_offline=force_offline`，不會再對同一個 IP
    重複呼叫 `_online_stance_force_offline`、重複消耗它的 online-stance
    限流 bucket。這樣一來，online-stance 配額已耗盡跟配額充裕的兩個
    caller，即使命中同一把 `key` 的其他欄位（type/coin/query/
    effective_mode 都相同），也會因為 `force_offline` 不同而落在
    不同的 `dedup_key`、各自拿到跟自己配額狀態相符的結果，不會互相
    污染、也不會有任何一方繞過自己的限流。
    """
    try:
        qtype = QuestionType(qs.get("type", ["multi_source"])[0])
    except ValueError:
        return 400, _json_envelope_err("bad_request", "無效的題型（type）參數")

    # --- 1. 純請求驗證（零依賴，呼叫任何 backend/連接器之前）---
    query = qs.get("q", [f"分析該幣種{_DATE_AGNOSTIC_QUERY_SUFFIX}"])[0]
    if len(query) > 1000:
        return 400, _json_envelope_err(
            "bad_request", f"問題長度不能超過 1000 字元（目前 {len(query)} 字元）"
        )

    if qtype == QuestionType.COMPARISON:
        coin_raw = (qs.get("coin", [""])[0]).strip()
        coin2_raw = (qs.get("coin2", [""])[0]).strip()
        if coin2_raw and "," not in coin_raw:
            coin_raw = f"{coin_raw},{coin2_raw}"
        try:
            pair = _parse_comparison_coins(coin_raw, query)
        except ValueError as exc:
            return 400, _json_envelope_err("bad_request", str(exc))
        if pair is None:
            return 400, _json_envelope_err(
                "bad_request",
                "比較分析需要選擇兩個幣種，請在左側「比較幣種」欄位選擇一個跟"
                "主要幣種不同的幣種",
            )
    else:
        coin_raw = (qs.get("coin", ["BTC"])[0]).strip().upper()
        if coin_raw not in COIN_POOL:
            return 400, _json_envelope_err(
                "bad_request", f"幣種須為以下其中之一：{'、'.join(COIN_POOL)}"
            )

    # #51 idempotency（codex HIGH 複審修正）：comparison 保留 `pair`
    # 原始請求順序組 key（`coin_a,coin_b`），刻意不排序——`report_a`/
    # `report_b` 是有序欄位，`coin=A,B` 與 `coin=B,A` 是 A/B 對調、語意
    # 不同的兩份報告，不能共用同一份快取（見 `_analyze_dedup_key`
    # docstring）；單幣直接用已驗證的 `coin_raw`。
    coin_key = ",".join(pair) if qtype == QuestionType.COMPARISON else coin_raw

    # --- 2. 驗證通過後才碰依賴（連接器/Bedrock/pipeline/序列化）---
    try:
        # codex HIGH 複審（dedup×限流交互）：每個 caller 都先過自己的
        # 限流，再進 dedup 查找——不管這次請求最後是 leader、follower、
        # 還是命中短期快取，都不能繞過自己的限流；限流本身完全在
        # dedup 共用狀態之外，不會被快取/replay 給別的 IP。見
        # `_analyze_enforce_caller_rate_limit` docstring。
        _analyze_enforce_caller_rate_limit(qs, client_ip)

        # codex HIGH 複審 Round 11（key 漏 caller-specific online-stance
        # 降級）：online-stance degrade 判斷也是跟這個 caller 的
        # `client_ip` 有關、real-mode 執行實際上依賴的變數，必須在
        # dedup key 算出來**之前**、對這個 caller 自己的 IP 先算一次
        # （見 `_analyze_online_stance_force_offline_for_caller`／
        # `_analyze_dedup_key` docstring 的完整說明），才能讓 key 精確
        # 捕捉「這次請求實際上會拿到 online-stance 結果還是被 degrade」。
        force_offline = _analyze_online_stance_force_offline_for_caller(qs, client_ip)
        dedup_key = _analyze_dedup_key(
            qtype=qtype, coin_key=coin_key, query=query, qs=qs, force_offline=force_offline,
        )

        if qtype == QuestionType.COMPARISON:
            report_a, evidence_a, report_b, evidence_b, log = _dedup_analyze_call(
                dedup_key,
                lambda: _do_comparison(
                    qs,
                    client_ip=client_ip,
                    enforce_rate_limit=False,
                    online_stance_force_offline=force_offline,
                ),
            )
            payload = {
                "version": VERSION,
                "report_a": dataclasses.asdict(report_a),
                "evidence_a": [ev.to_dict() for ev in evidence_a],
                "trust_radar_a": aggregate_trust_by_kind(evidence_a),
                "trust_components_aggregate_a": _aggregate_trust_components(evidence_a),
                "price_provenance_a": _price_provenance_data(evidence_a),
                "report_b": dataclasses.asdict(report_b),
                "evidence_b": [ev.to_dict() for ev in evidence_b],
                "trust_radar_b": aggregate_trust_by_kind(evidence_b),
                "trust_components_aggregate_b": _aggregate_trust_components(evidence_b),
                "price_provenance_b": _price_provenance_data(evidence_b),
                "execution_log": log.events,
            }
        else:
            report, evidence, log = _dedup_analyze_call(
                dedup_key,
                lambda: _do_analyze(
                    qs,
                    client_ip=client_ip,
                    enforce_rate_limit=False,
                    online_stance_force_offline=force_offline,
                ),
            )
            payload = {
                "version": VERSION,
                "report": dataclasses.asdict(report),
                "evidence": [ev.to_dict() for ev in evidence],
                "trust_radar": aggregate_trust_by_kind(evidence),
                "trust_components_aggregate": _aggregate_trust_components(evidence),
                "price_provenance": _price_provenance_data(evidence),
                "execution_log": log.events,
            }
        return 200, _json_envelope_ok(payload)
    except _AnalyzeDedupTimeout as exc:
        # codex HIGH 複審：follower bounded wait 逾時——回可重試的 503，
        # 不落回自己真的跑一次（見 `_dedup_analyze_call`/`_AnalyzeDedupTimeout`
        # docstring），交給 client 自行重試。
        return 503, _json_envelope_err("timeout", str(exc))
    except TooManyRequests as exc:
        return 429, _json_envelope_err("rate_limited", str(exc))
    except Exception:
        logging.exception("TrustForge /api/analyze error")
        return 502, _json_envelope_err("upstream_error", "分析服務暫時無法使用，請稍後再試")


def _handle_api_overview(client_ip: str = "") -> tuple[int, str]:
    """`/api/overview`：多幣總覽結構化資料——逐幣讀 `__trust_snapshot__:{coin}`
    最新一筆快照（`scripts/fetch_scheduler.py --snapshot` 既有寫入的
    `_snapshot_dict()` 內容原樣），回傳結構化 JSON。

    刻意不是讀首頁用的 `TRUST_OVERVIEW_SOURCE`（那顆是預先渲染好給
    `_render_home_page()` 直接嵌字串用的 HTML blob）——API 消費者要的是可
    程式化解析的資料，這裡改直接讀每幣的原始結構化快照；兩條讀路徑各自
    獨立，互不影響，首頁背景刷新 thread／in-memory 現貨機制原樣不動。

    套用 `_check_status_rate_limit`（跟 `/status` 同一組 bucket/門檻——這裡
    做的是同一類逐 key 讀 cache backend 的工作，換路徑不能漏接限流）。只讀
    既有排程寫入的快取，不寫入、不觸發任何連接器/Bedrock 呼叫。

    codex 複審 HIGH（同分支修復 #1）：backend 建構＋逐幣 cache 讀取整段包
    `except Exception`——比照 `_overview_bg_refresh_once()` 既有慣例（backend
    建構本身出錯、憑證/DNS 問題、`cache_get()` 內建 fallback 檔案 I/O 失敗都
    算），一律回通用 502，不讓 DynamoDB/config 例外穿透 `do_GET` 吐 traceback
    （harper CISO must-have #3）。

    codex 複審 HIGH（根因修復）：`cache_get()` 預設會把「primary+fallback
    都讀取失敗（outage）」跟「單純沒這筆資料（miss）」兩者一樣吞成 `None`
    ——這裡改用 `cache_get(..., strict=True)`，outage 時改成
    `raise CacheReadFailure`，讓上面這層 `except Exception` 接住轉 502；
    單純 miss（某幣還沒排程寫過快照）維持原樣正常跳過，回 200 該幣缺席。
    """
    try:
        _check_status_rate_limit(client_ip)
    except TooManyRequests as exc:
        return 429, _json_envelope_err("rate_limited", str(exc))

    from .ingestion.cache import TRUST_SNAPSHOT_SOURCE, cache_get, cache_key

    try:
        backend = _home_overview_backend()
        coins_data = []
        for coin in COIN_POOL:
            entry = cache_get(backend, cache_key(TRUST_SNAPSHOT_SOURCE, coin), strict=True)
            if entry is None:
                continue
            docs = entry.get("docs") or []
            if not docs or not isinstance(docs[0], dict):
                continue
            snap = dict(docs[0])
            snap["fetched_at_epoch"] = entry.get("fetched_at")
            coins_data.append(snap)
        return 200, _json_envelope_ok({"coins": coins_data})
    except Exception:
        logging.exception("TrustForge /api/overview error")
        return 502, _json_envelope_err("upstream_error", "總覽資料暫時無法讀取，請稍後再試")


def _handle_api_status(client_ip: str = "") -> tuple[int, str]:
    """`/api/status`：`/status` 頁資料的 JSON 化版本——版本／模式能力／
    快取 backend 連線健康／資料鮮度矩陣／運行時間。

    刻意比 SSR `/status` 頁更保守：連線探測失敗時**不**回傳原始例外訊息
    （SSR 頁面歷史上會顯示 `str(exc)` 供人工除錯，這裡是機器可讀 API，更
    容易被大量掃描/爬取，只回通用訊息，不洩露 DynamoDB 錯誤細節，見 harper
    CISO must-have #3）。只做被要求的「連接器健康/鮮度/版本/uptime」四項，
    不含成本帳本明細（見 `/api/costs`）／連接器用量／最近排程執行，避免
    範圍蔓延。

    codex 複審 HIGH（同分支修復 #1）：`get_cache_backend()` **建構本身**
    （跟下面 `.get()` 探測是兩回事）先前沒包例外邊界——config/憑證錯誤會
    直接穿透 `do_GET` 吐 traceback。現在建構失敗也整個回通用 502，不洩露
    細節。

    codex 複審 HIGH（同分支修復 #2，對齊 502 契約）：cache probe（`.get()`）
    與 `get_freshness_snapshot()` **先前各自 swallow 例外**，退化成
    `connected: false` / 空鮮度 + 仍然 HTTP 200——這會讓只看 HTTP 狀態碼
    的監控把「依賴不可用」誤判成「API 健康」。現在這兩步依賴一旦拋例外，
    整個請求回通用 502（跟其餘 `/api/*` 端點一致的「依賴失敗→502」契約），
    診斷細節只進 server log，不進回應 body；只有兩步依賴都成功時才回
    200 + 各元件狀態資料。

    ⚠️ 這只改本 API 端點的契約；**既有 SSR `/status` HTML 頁面**
    （`_handle_status`）的 dashboard 行為（顯示紅/綠元件狀態卡片）完全
    沒有被觸碰，維持逐字不變（LIVE 頁面）——它呼叫 `get_freshness_snapshot()`
    時不傳 `strict`，用預設 `False`，行為不受下面這個改動影響。

    codex 複審 HIGH（根因修復）：`get_freshness_snapshot()` 內部逐
    (source, coin) 呼叫 `cache_get()`，預設會把「讀取真的失敗（outage）」
    跟「單純沒這個 (source, coin) 快照（missing，本來就合法）」都變成同一
    種「這格標 missing」結果，讓監控看不出 cache 依賴其實掛了。這裡改用
    `get_freshness_snapshot(..., strict=True)`，outage 時改成
    `raise CacheReadFailure`，被下面這層 `except Exception` 接住轉 502；
    純粹沒快照（`missing` 狀態）維持原樣正常回 200。

    codex 複審 HIGH（最終閉合）：先前這裡在呼叫 `get_freshness_snapshot()`
    前，還有一個**獨立、繞過 `cache_get()` fallback 機制**的 probe
    （直接 `cache_backend.get(...)`，回傳值完全沒被使用，純粹「呼叫本身
    有沒有丟例外」）。這個 probe 只要 **primary 拋例外就立刻 502**，即使
    本地 `JsonCacheBackend` fallback 其實讀得到——跟 overview/history
    「primary+fallback 都失敗才 502」的 outage 定義不一致，會把單純的
    transient DynamoDB 失敗（fallback 正常）誤判成整個依賴掛掉。
    現在移除這個冗餘 probe，502 判定完全交給下面
    `get_freshness_snapshot(..., strict=True)`——它本身就是透過
    `cache_get(..., strict=True)` 逐格讀取，天生 fallback-aware：primary
    失敗但 fallback 讀得到 → 正常回值（不 raise）；只有 primary+fallback
    都失敗才 `raise CacheReadFailure` → 502。跟 overview/history 的 502
    條件完全對齊。

    codex 複審 MEDIUM（觀測準確性，最終閉合）：上面這條「primary 失敗但
    fallback 成功 → 200」的路徑修好之後，發現這裡**無條件**回報
    `cache_backend: {name: type(cache_backend).__name__, connected: True}`
    ——也就是說 primary（例如 DynamoDB）整段 outage、完全靠本地
    `JsonCacheBackend` fallback 撐著時，回應照樣講「DynamoDB
    connected:true」，掩蓋了長時間降級，違反 `/api/status` 本身作為觀測
    端點的目的。現在用 `cache_get()`/`get_freshness_snapshot()` 新增的
    `degradation_out` 訊號（OR 聚合：矩陣裡任一格用過 fallback 就算
    degraded）如實分開回報：
      - `primary_connected`：primary backend 是否真的親自答對（沒有任何
        一格靠 fallback）。
      - `active_backend`：這次請求實際拿資料的 backend 類名——正常時等於
        `name`（primary 自己）；degraded 時是 `"JsonCacheBackend"`。
      - `degraded`：`primary_connected` 的反面，明講「這次回應是降級模式
        的結果」。
      - `connected`（沿用既有欄位，語意收斂成跟 `primary_connected`
        一致，不再無條件硬寫 `True`）：避免保留一個名字聽起來像「服務
        正常」但其實只代表 fallback 撐住的舊欄位，混淆意義。
    Fallback 成功仍然回 200（服務本身可用，只是繞去讀本地備援）；只有
    primary+fallback 都失敗（`get_freshness_snapshot` raise
    `CacheReadFailure`）才會落進下面的 `except Exception` 回 502，跟
    codex 前一輪定義的 outage 條件一致。

    codex 複審 HIGH（production 安全，circuit breaker + 短 timeout，最終
    最終閉合）：上面「primary 失敗、fallback 成功仍回 200」這條路徑本身沒
    問題，但 `get_freshness_snapshot()` 逐 (source, coin) 迴圈約 115 格，
    先前**每一格都重新嘗試一次 primary**——DynamoDB outage 時，這是
    (1) 對已經掛掉的依賴疊加 ~115 倍流量（可能讓 outage 更嚴重），
    (2) 就算 SDK 有預設 timeout，115 次疊加仍可能讓整支請求拖到多分鐘，
    「degraded 仍回 200」的承諾在實務上變成「多分鐘 hang」。現在兩處一起
    修：
      - **短 timeout + 限重試**：改用 `_status_cache_backend()`（而非
        `get_cache_backend()`）建構 DynamoDB backend，帶明確
        `connect_timeout`/`read_timeout`/`max_attempts`（見該函式與
        `_STATUS_CACHE_*` 常數），對齊
        `scripts/fetch_scheduler.py::_probe_cache_backend()` 既有慣例，讓
        「每一次 primary 嘗試」本身有界。SSR `/status` 頁仍用
        `get_cache_backend()`（無 timeout），這裡刻意不動。
      - **request-scoped circuit breaker**：`get_freshness_snapshot(...,
        circuit_breaker=True)`——同一次請求內第一次偵測到 primary 失敗
        後，後續格子直接跳過 primary、只讀本地 `JsonCacheBackend`，不再
        逐格重試已知掛掉的依賴。兩者合起來，整支請求的 primary 嘗試次數
        全程 ≤1 次，延遲不再隨格數線性放大。
    degraded/502 語意跟上一輪完全一致：fallback 成功仍 200 + 如實回報
    降級 metadata；primary+fallback 都失敗才 502。
    """
    try:
        _check_status_rate_limit(client_ip)
    except TooManyRequests as exc:
        return 429, _json_envelope_err("rate_limited", str(exc))

    from .ingestion.cache import get_freshness_snapshot

    try:
        cache_backend = _status_cache_backend()
    except Exception:
        logging.exception("TrustForge /api/status error（cache backend 建構失敗）")
        return 502, _json_envelope_err("upstream_error", "狀態資料暫時無法讀取，請稍後再試")

    try:
        degradation: dict[str, bool] = {}
        freshness = get_freshness_snapshot(
            backend=cache_backend, strict=True, degradation_out=degradation,
            circuit_breaker=True,
        )
        used_fallback = degradation.get("used_fallback", False)
        primary_connected = not used_fallback
        active_backend = "JsonCacheBackend" if used_fallback else type(cache_backend).__name__
        fresh_n = sum(1 for r in freshness if r.get("status") == "fresh")
        stale_n = sum(1 for r in freshness if r.get("status") == "stale")
        missing_n = sum(1 for r in freshness if r.get("status") == "missing")
        data = {
            "version": VERSION,
            "uptime_seconds": round(time.time() - _START_TIME, 3),
            "bedrock_capable": bool(HAS_BEDROCK),
            "live_token_set": bool(LIVE_TOKEN),
            "cache_backend": {
                "name": type(cache_backend).__name__,
                "connected": primary_connected,
                "primary_connected": primary_connected,
                "active_backend": active_backend,
                "degraded": used_fallback,
            },
            "freshness": {
                "fresh": fresh_n,
                "stale": stale_n,
                "missing": missing_n,
                "entries": freshness,
            },
        }
        return 200, _json_envelope_ok(data)
    except Exception:
        logging.exception("TrustForge /api/status error（freshness 讀取失敗，primary+fallback 皆不可用）")
        return 502, _json_envelope_err("upstream_error", "狀態資料暫時無法讀取，請稍後再試")


def _handle_api_costs(client_ip: str = "") -> tuple[int, str]:
    """`/api/costs`：成本帳本 JSON 化版本——直接重用 `_get_ledger_summary()`
    （既有 20 秒 TTL + single-flight 快取，`/status`／`/costs` 頁共用同一份
    真實累計數字），不新增查詢語意。套用 `_check_status_rate_limit`（理由同
    `_handle_api_overview`）。

    codex 複審 HIGH（同分支修復）：`_get_ledger_summary()` 呼叫＋序列化包
    `except Exception`——即使該函式內部已有 fallback，fallback 本身讀檔
    失敗仍可能往上炸，不包會讓 ledger I/O 例外穿透 `do_GET` 吐 traceback。

    codex 複審 HIGH（成本端點可擴展性，同分支修復）：回應 shape 為有界摘要
    `{total_cost_usd, by_model, by_model_detail, run_count, runs}`——`run_count`
    是帳本真實總筆數，`runs` 只含最近 `ledger.SUMMARY_RECENT_RUNS_CAP`（50）筆，
    不再是無界成長的完整清單（見 `Ledger.summary()`）。前端要顯示「總筆數」一律
    讀 `run_count`，不可用 `runs.length` 估算（帳本 >50 筆時會低估）。
    """
    try:
        _check_status_rate_limit(client_ip)
    except TooManyRequests as exc:
        return 429, _json_envelope_err("rate_limited", str(exc))
    try:
        return 200, _json_envelope_ok(_get_ledger_summary())
    except Exception:
        logging.exception("TrustForge /api/costs error")
        return 502, _json_envelope_err("upstream_error", "成本資料暫時無法讀取，請稍後再試")


# 對齊 `cache.py::TRUST_SNAPSHOT_HISTORY_TTL_SECONDS`（90 天保留期限）——問
# 超過保留期限的天數本來就查無資料，直接在 API 層擋掉，不做無意義的大量
# cache 逐日讀取。
_API_HISTORY_MAX_DAYS = 90


def _handle_api_history(qs: dict, client_ip: str = "") -> tuple[int, str]:
    """`/api/history`（淨新增）：PIT 歷史——`get_trust_history()`（PR#59 已
    寫入但零路由消費）按日信任序列 JSON 化。`coin` 過既有 `COIN_POOL` 白
    名單，`days` 限制在 `[1, _API_HISTORY_MAX_DAYS]`，兩者不合法皆回 400 +
    通用訊息。

    套用 `_check_status_rate_limit`（`get_trust_history` 逐日各讀一次
    cache，`days` 越大讀取量越大，同一類「逐 key 讀 cache backend」風險，
    理由同 `_handle_api_overview`）。

    codex 複審 HIGH（同分支修復 #1）：`get_trust_history()` 呼叫＋信封序列化
    整段包進同一個 `except Exception`，避免序列化那一步漏接。

    codex 複審 HIGH（根因修復）：`get_trust_history()` 傳 `strict=True`——
    逐日 `cache_get()` 若「讀取真的失敗（outage）」不再被吞成「那天沒快
    照」，改成 `raise CacheReadFailure`，被下面 `except Exception` 接住轉
    502；單純某幾天沒排程寫過快照（合法 miss）維持原樣正常跳過，回 200
    + 較短的 history 陣列。
    """
    try:
        _check_status_rate_limit(client_ip)
    except TooManyRequests as exc:
        return 429, _json_envelope_err("rate_limited", str(exc))

    coin_raw = (qs.get("coin", [""])[0]).strip().upper()
    if len(coin_raw) > 20 or coin_raw not in COIN_POOL:
        return 400, _json_envelope_err(
            "bad_request", f"幣種須為以下其中之一：{'、'.join(COIN_POOL)}"
        )

    days_raw = (qs.get("days", ["30"])[0]).strip()
    if len(days_raw) > 10 or not days_raw.lstrip("-").isdigit():
        return 400, _json_envelope_err("bad_request", "days 須為正整數")
    days = int(days_raw)
    if days < 1 or days > _API_HISTORY_MAX_DAYS:
        return 400, _json_envelope_err(
            "bad_request", f"days 須介於 1 至 {_API_HISTORY_MAX_DAYS} 之間"
        )

    from .ingestion.cache import get_trust_history

    try:
        history = get_trust_history(
            coin_raw, days, backend=_home_overview_backend(), strict=True
        )
        return 200, _json_envelope_ok({"coin": coin_raw, "days": days, "history": history})
    except Exception:
        logging.exception("TrustForge /api/history error")
        return 502, _json_envelope_err("upstream_error", "歷史資料暫時無法讀取，請稍後再試")


def _handle_api_health() -> tuple[int, str]:
    """`/api/health`：JSON 版健康檢查，補在既有純文字 `/healthz` 之外（見
    `Handler.do_GET`）——同樣零 I/O、不設限流，供偏好 JSON 回應格式的健康
    檢查探針使用。

    codex 複審 HIGH 巡查範圍內：本端點沒有任何 backend/ledger 依賴，理論上
    不會拋例外，仍比照其餘 5 個 `/api/*` 端點包一層 `except Exception`，統一
    防禦邊界，避免未來改動不小心引入依賴卻漏包。
    """
    try:
        return 200, _json_envelope_ok(
            {
                "status": "ok",
                "version": VERSION,
                "uptime_seconds": round(time.time() - _START_TIME, 3),
            }
        )
    except Exception:
        logging.exception("TrustForge /api/health error")
        return 502, _json_envelope_err("upstream_error", "健康檢查暫時無法讀取，請稍後再試")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8", extra_headers=None):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        # CSP_MODE 預設 "legacy"：byte-identical 沿用既有 zero-JS 嚴格 CSP，
        # cutover 前不破 SSR。切成 "react" 才套用 harper 新指令集 + 追加的
        # clickjacking/referrer 防護（見模組頂部 CSP_MODE 說明）。
        if CSP_MODE == "react":
            self.send_header("Content-Security-Policy", _CSP_REACT)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        else:
            self.send_header("Content-Security-Policy", _CSP_LEGACY)
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, val in (extra_headers or {}).items():
            self.send_header(name, val)
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):  # 靜音預設存取日誌
        pass

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        # `client_address[0]` 是 TCP 對端 IP，用來 keyed per-IP 限流
        # （`_check_live_rate_limit`/`_check_real_rate_limit`/
        # `_check_status_rate_limit`）。這在**直連部署**（沒有 reverse
        # proxy/LB）下才是真使用者 IP，per-user bucket 才正確。
        #
        # 前後端分離 Phase 3（task #28）：nginx 反代上線後，
        # `client_address[0]` 會變成 nginx 自己的 IP（127.0.0.1），所有
        # 使用者會共用一個 bucket、限流失效。`_resolve_client_ip()` 依
        # `TRUSTFORGE_TRUST_PROXY`（預設關）決定要不要改讀
        # `X-Real-IP`/`X-Forwarded-For`——**絕對不能無條件信任**這兩個
        # header（使用者可自由偽造），因此 config-gated，且只在 python
        # 綁定 127.0.0.1（見 `main()`，nginx 是唯一對外入口）這個拓樸下
        # 才安全開啟。預設關閉時行為與過去逐字相同（codex 確認，PR #44）。
        # `getattr(self, "headers", {})`：正常請求路徑一定有 `self.headers`
        # （`BaseHTTPRequestHandler.parse_request()` 設好），這裡防禦性處理
        # 是為了相容既有測試用 `Handler.__new__` 建構的最小化 mock（不走
        # 真 socket handshake，不會有 `.headers`）。
        client_ip = _resolve_client_ip(self.client_address[0], getattr(self, "headers", {}))

        if u.path == "/healthz":
            return self._send(200, "ok", "text/plain")

        # 前後端分離 Phase 1（task #28）：純新增 JSON API 端點，統一
        # `{ok,data,error}` 信封，見 `_handle_api_*` 系列函式 docstring/
        # 模組頂部大段說明。⛔ 完全獨立於下方既有 SSR 路由，不改動、不共用
        # 任何既有分支的程式碼路徑。
        if u.path == "/api/health":
            code, body = _handle_api_health()
            return self._send(code, body, "application/json; charset=utf-8")
        if u.path == "/api/status":
            code, body = _handle_api_status(client_ip)
            return self._send(code, body, "application/json; charset=utf-8")
        if u.path == "/api/costs":
            code, body = _handle_api_costs(client_ip)
            return self._send(code, body, "application/json; charset=utf-8")
        if u.path == "/api/overview":
            code, body = _handle_api_overview(client_ip)
            return self._send(code, body, "application/json; charset=utf-8")
        if u.path == "/api/history":
            code, body = _handle_api_history(qs, client_ip)
            return self._send(code, body, "application/json; charset=utf-8")
        if u.path == "/api/analyze":
            code, body = _handle_api_analyze(qs, client_ip)
            return self._send(code, body, "application/json; charset=utf-8")

        # CEO 決策（PR #39，收斂）：theme toggle 切換機制（/theme 路由、
        # rtok render cache、cookie 讀寫、header ★ 按鈕）整個拆除——rtok
        # cache 是 process-local，重啟/部署/多 worker/TTL 過期都會 cache
        # miss，"切主題不重跑 pipeline" 與 "不遺失已產出報告" 在無狀態 SSR
        # 架構下本質難兩全。固定 dark（`render_page()` 預設值），
        # `var(--tf-*)` token 架構保留，等 #20（結果持久化）做對後再重新
        # 開放。`page()` 因此只是 `render_page()` 的薄包裝，不再需要算
        # toggle_href。
        def page(body="", active_mode="offline", run_stats_html="", minimal_header=False):
            return render_page(
                body, active_mode=active_mode, run_stats_html=run_stats_html,
                minimal_header=minimal_header,
            )

        if u.path == "/":
            # 世界第一重寫 Phase 1：首頁不再是空白 body（見 `_render_home_page`），
            # header 用 `minimal_header=True`——只留 logo + 極簡 /status 連結，
            # 版號／模式徽號／cost ledger 移到 /status（不是刪功能，是移位）。
            # 世界第一重寫 Phase 2：`active_mode` 對齊 `_active_mode(qs)`（不再
            # 寫死 "offline"）——首頁本身仍是零連接器呼叫的純靜態渲染，只是
            # 讓「未帶參數＝真資料·$0 預設」在模式判斷上也對齊首頁請求本身，
            # 不留一處寫死舊預設值的死角（minimal header 目前不顯示徽章，
            # 這裡對齊只是不留技術債，不影響本次畫面）。
            return self._send(
                200,
                page(_render_home_page(), active_mode=_active_mode(qs), minimal_header=True),
            )
        if u.path == "/costs":
            return self._send(200, page(_render_costs_page()))
        if u.path == "/status":
            code, body = _handle_status(client_ip)
            return self._send(code, body)
        if u.path in ("/analyze", "/analyze.json"):
            # 提前解析 qtype 以便分流，不依賴回傳 tuple 長度
            try:
                qtype = QuestionType(qs.get("type", ["multi_source"])[0])
            except ValueError:
                qtype = QuestionType.MULTI_SOURCE

            # MEDIUM 修復：在執行分析「之前」就算好本次請求生效的模式，success／
            # error（429/400/502）路徑的 page() 都要帶，否則 real/live 模式的
            # 請求一旦失敗，錯誤頁會落回 render_page 預設值顯示 offline-active，
            # 跟本次請求實際嘗試的模式不符，一樣是 provenance 誤導。
            # _active_mode 為純函式（只讀 qs），提前呼叫不影響限流/分析行為。
            active_mode = _active_mode(qs)

            try:
                if qtype == QuestionType.COMPARISON:
                    report_a, evidence_a, report_b, evidence_b, log = _do_comparison(
                        qs, client_ip=client_ip
                    )
                    query = qs.get("q", [""])[0]
                    if u.path == "/analyze.json":
                        payload = {
                            "version": VERSION,
                            "report_a": dataclasses.asdict(report_a),
                            "evidence_a": [ev.to_dict() for ev in evidence_a],
                            "report_b": dataclasses.asdict(report_b),
                            "evidence_b": [ev.to_dict() for ev in evidence_b],
                            "execution_log": log.events,
                        }
                        return self._send(
                            200, json.dumps(payload, ensure_ascii=False, indent=2),
                            "application/json; charset=utf-8",
                        )
                    # 自我連結（下載 JSON）須保留當次請求實際生效的模式參數，
                    # 否則點下載會落回預設 offline/sample，匯出跟畫面看到的不一致。
                    # HIGH 根治：改傳 dict（_mode_extra_params），由 _render_comparison
                    # 內部併入 coin/type/q 一次 urlencode，不再自己組半截字串。
                    mode_extra = _mode_extra_params(qs)
                    comparison_body = _render_comparison(
                        report_a, evidence_a, report_b, evidence_b, query, log,
                        mode_extra=mode_extra,
                    )
                    comparison_stats = _render_run_stats(evidence_a + evidence_b, log)
                    return self._send(
                        200,
                        page(
                            comparison_body, active_mode=active_mode,
                            run_stats_html=comparison_stats,
                        ),
                    )
                else:
                    report, evidence, log = _do_analyze(qs, client_ip=client_ip)
                    if u.path == "/analyze.json":
                        payload = {
                            "version": VERSION,
                            "report": dataclasses.asdict(report),
                            "evidence": [ev.to_dict() for ev in evidence],
                            "execution_log": log.events,
                        }
                        return self._send(
                            200, json.dumps(payload, ensure_ascii=False, indent=2),
                            "application/json; charset=utf-8",
                        )
                    mode_extra = _mode_extra_params(qs)
                    report_body = _render_report(report, evidence, log, mode_extra=mode_extra)
                    report_stats = _render_run_stats(evidence, log)
                    return self._send(
                        200,
                        page(
                            report_body,
                            active_mode=active_mode,
                            run_stats_html=report_stats,
                        ),
                    )
            except TooManyRequests as exc:
                # 商業級修復：品牌化錯誤卡取代裸紅字，429 是暫時性限流，附
                # 「重試」直接導回同一個請求。
                return self._send(429, page(
                    _render_error_card(
                        "請求過於頻繁", str(exc), retry_href=self.path,
                    ),
                    active_mode=active_mode))
            except ValueError as exc:
                # 使用者輸入本身有誤（幣種/題型/長度），重試同樣輸入還是會
                # 錯，不給「重試」，只給「返回首頁」重新開始。
                return self._send(400, page(
                    _render_error_card("輸入有誤", str(exc)),
                    active_mode=active_mode))
            except Exception:
                logging.exception("TrustForge analyze error")
                # 未預期例外一律 502，不回顯任何原始例外訊息（縱深防禦，維持
                # 既有行為）；服務暫時性問題，給「重試」。
                return self._send(502, page(
                    _render_error_card(
                        "服務暫時無法使用", "分析服務暫時無法使用，請稍後再試",
                        retry_href=self.path,
                    ),
                    active_mode=active_mode))
        return self._send(404, page(
            _render_error_card("找不到頁面", "您造訪的網址不存在，請確認網址是否正確。"),
        ))


def main():
    # 前後端分離 Phase 3（task #28，harper 安全審 must-have）：`TRUST_PROXY`
    # 開啟時，代表部署拓樸是「nginx 對外、python 只對內」，此時**強制**把
    # 監聽 host 收斂成 127.0.0.1——即使 `TRUSTFORGE_BIND_HOST` 被設成別的
    # 值，也不允許 python 對外直接可連（否則有心人可繞過 nginx 直接對
    # python 打偽造的 X-Real-IP/X-Forwarded-For header，繞過限流）。
    # 預設（TRUST_PROXY 關）沿用舊行為 0.0.0.0，cutover 前不破現有直連部署。
    host = os.getenv("TRUSTFORGE_BIND_HOST", "0.0.0.0")
    if TRUST_PROXY and host != "127.0.0.1":
        logging.warning(
            "TRUSTFORGE_TRUST_PROXY=1 但 TRUSTFORGE_BIND_HOST=%s 非 127.0.0.1，"
            "強制改綁 127.0.0.1（信任反代 header 只在 python 不對外時安全）",
            host,
        )
        host = "127.0.0.1"
    srv = ThreadingHTTPServer((host, PORT), Handler)
    print(
        f"TrustForge web on {host}:{PORT}  "
        f"(bedrock={'live-capable' if HAS_BEDROCK else 'offline'}, "
        f"trust_proxy={TRUST_PROXY}, csp_mode={CSP_MODE})",
        flush=True,
    )
    srv.serve_forever()


if __name__ == "__main__":
    main()
