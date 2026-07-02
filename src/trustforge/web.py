"""TrustForge Live Demo web 服務（純 stdlib，App Runner / 容器就緒）。

路由：
  GET /            首頁表單（選幣種/題型/問題）
  GET /healthz     健康檢查（App Runner 用）→ 200 "ok"
  GET /analyze     ?coin=BTC&q=...&type=multi_source[&live=1&token=<TOKEN>][&real=1] → HTML 報告
  GET /analyze.json 同上參數 → JSON {report, evidence, log}

三檔模式（`data_mode`/`llm_mode` 解耦，見 `pipeline.run`）：
  1. 離線示範（預設）：樣本資料 + Bedrock stub，未設 AWS 也能跑，$0。
  2. 真資料·$0（`?real=1`）：走真連接器抓真資料，但 Bedrock 關閉（llm_mode=off），
     不依賴 HAS_BEDROCK/token，仍是 $0，credit-safe。
  3. 真 Bedrock（`?live=1&token=<TOKEN>`）：需同時滿足設了 BEDROCK_MODEL_ID、
     TRUSTFORGE_LIVE_TOKEN，且請求帶正確 token 參數（用 hmac.compare_digest 比對）。
     `live` 優先於 `real`：兩者同時滿足時走 live。
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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from .schema import COIN_POOL, QuestionType, comparison_to_markdown
from .pipeline import run, run_comparison
from .ledger import JsonlLedger, get_ledger

try:
    from ._version import VERSION
except Exception:
    VERSION = "dev"

PORT = int(os.getenv("PORT", "8080"))
HAS_BEDROCK = bool(os.getenv("BEDROCK_MODEL_ID"))
LIVE_TOKEN = os.getenv("TRUSTFORGE_LIVE_TOKEN", "")
# 累計花費超過此門檻（USD）→ /costs 頁面卡片轉紅告警。未設定則不告警。
COST_BUDGET_USD = os.getenv("COST_BUDGET_USD")

# `/status` 頁面「運行時間」：本程序（web worker）匯入這個模組的當下當作起點，
# 不是真的 process 啟動時間（stdlib 無法可靠取得），但對觀測用途已足夠。
_START_TIME = time.time()

# per-IP 限流：每 IP 每 60 秒最多 5 次 live 請求
_RATE_WINDOW = 60
_RATE_MAX = 5
_rate_lock = threading.Lock()
_rate_buckets: dict[str, list[float]] = {}

# `/status` 專用 per-IP 限流：獨立於上面 live/real 的 bucket，避免互相干擾
# （/status 是唯讀觀測端點，不消耗真連接器/Bedrock 配額，門檻可以更寬鬆）。
_STATUS_RATE_WINDOW = 30
_STATUS_RATE_MAX = 10
_status_rate_lock = threading.Lock()
_status_rate_buckets: dict[str, list[float]] = {}

# `/status` 頁面級 TTL 快取（跨 IP 共用，非安全機制，純降低重算頻率）：資料
# 鮮度矩陣要逐 (source, coin) 讀 cache backend，組合數量多，DynamoDB backend
# 下每次都重算有明顯延遲，也容易被打爆，見 `_render_status_page_cached()`。
_STATUS_CACHE_TTL_SECONDS = 30.0
_status_cache_lock = threading.Lock()
_status_cache: dict[str, float | str] = {"expires_at": 0.0, "html": ""}

_PAGE = """<!doctype html><html lang="zh-Hant" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TrustForge — 加密市場分析 AI Agent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
 :root{{--tf-bg:#0d1117;--tf-card:#161b22;--tf-border:#30363d;--tf-text:#e6edf3;--tf-muted:#8b949e;--tf-muted2:#6e7681;--tf-hdr-g1:#12171e;--tf-hdr-g2:#0f141a;--tf-inset:#0f141a;--tf-text2:#c9d1d9}}
 :root[data-theme="light"]{{--tf-bg:#f6f8fa;--tf-card:#ffffff;--tf-border:#d0d7de;--tf-text:#1f2328;--tf-muted:#57606a;--tf-muted2:#6e7781;--tf-hdr-g1:#ffffff;--tf-hdr-g2:#f6f8fa;--tf-inset:#eef2f6;--tf-text2:#3d444d}}
 *{{box-sizing:border-box}}
 body{{font-family:'IBM Plex Sans',-apple-system,"PingFang TC",sans-serif;max-width:1280px;margin:2rem auto;padding:0 1rem;color:var(--tf-text);background:var(--tf-bg);-webkit-font-smoothing:antialiased}}
 h1{{margin-bottom:.2rem}} .sub{{color:var(--tf-muted);margin-top:0}}
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
 button{{background:#1f6feb;color:#fff;border:0;cursor:pointer;font-weight:600;letter-spacing:.01em}}
 button .tf-kbd{{opacity:.75;font-family:'IBM Plex Mono',monospace;margin-left:.3rem}}
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
 .tf-tier-pill{{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:.68rem;font-weight:600;border-radius:4px;padding:.05rem .4rem;margin-right:.3rem;text-transform:uppercase;vertical-align:middle}}
 .tf-div-grid{{display:grid;grid-template-columns:1fr 34px 1fr;gap:0;align-items:stretch;margin-top:.6rem}}
 .tf-div-side{{border-radius:9px;padding:.7rem .8rem}}
 .tf-div-bull{{background:rgba(63,185,80,.08);border:1px solid rgba(63,185,80,.35)}}
 .tf-div-bear{{background:rgba(248,81,73,.08);border:1px solid rgba(248,81,73,.35)}}
 .tf-div-mid{{display:flex;align-items:center;justify-content:center;color:#f85149;font-weight:700;font-size:1rem}}
 .tf-div-tag{{font-family:'IBM Plex Mono',monospace;font-size:.68rem;font-weight:600;border-radius:4px;padding:.1rem .5rem;margin-right:.4rem}}
 @media (max-width:900px){{
  body{{margin:1rem auto}}
  header.tf-hdr{{flex-direction:column;align-items:flex-start}}
  .tf-hdr-spacer{{display:none}}
  .tf-layout{{grid-template-columns:1fr}}
  .tf-query-panel{{position:static}}
  .tf-hero-row{{grid-template-columns:1fr}}
  .tf-div-grid{{grid-template-columns:1fr}}
  .tf-div-mid{{padding:.3rem 0}}
 }}
</style></head><body>
<header class="tf-hdr">
 <span class="tf-logo"><span class="tf-logo-mark">&#9670;</span>Trust<b>Forge</b></span>
 <span class="tf-version">{version}</span>
 {mode}
 <div class="tf-hdr-spacer"></div>
 <a class="tf-costlink" href="/status">系統狀態</a>
 <a class="tf-costlink" href="/costs">cost ledger {cost_display}</a>
</header>
<div class="tf-layout">
 <aside class="tf-query-panel">
  <h3>Query Console</h3>
  <p class="sub" style="margin:0;font-size:.8rem">加密市場分析 AI Agent — 多源資訊的信任提煉</p>
  <form action="/analyze" method="get">
   <div><label>幣種</label><select name="coin">{coins}</select></div>
   <div><label>題型</label><select name="type">{types}</select></div>
   <div><label>問題</label><textarea name="q" rows="3">分析該幣種近兩週市場狀況，整合多源資料</textarea></div>
   <button type="submit">Run analysis<span class="tf-kbd">&#8629;</span></button>
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


# 兩個限流 bucket（`_rate_buckets`/`_status_rate_buckets`）共用的硬上限：
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


def _opts(values, labels=None):
    labels = labels or {v: v for v in values}
    return "".join(
        f'<option value="{html.escape(v)}">{html.escape(labels[v])}</option>'
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
    try:
        cache_backend.get(cache_key("__status_probe__", ""))
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
    run_count = len(summary.get("runs", []) or [])

    try:
        freshness = get_freshness_snapshot(backend=cache_backend)
    except Exception:
        freshness = []
    fresh_n = sum(1 for r in freshness if r.get("status") == "fresh")
    stale_n = sum(1 for r in freshness if r.get("status") == "stale")
    missing_n = sum(1 for r in freshness if r.get("status") == "missing")
    freshness_html = _render_freshness_table(freshness)

    recent_run_html = _render_recent_scheduler_run()

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
        return 429, render_page(f"<p style='color:#c00'>{html.escape(str(exc))}</p>")
    return 200, render_page(_render_status_page_cached())


_LEDGER_SUMMARY_CACHE_TTL_SEC = 20.0
_LEDGER_SUMMARY_CACHE_MAX = 32
_ledger_summary_cache: dict[int, tuple[float, dict]] = {}
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
    latency。這裡加一層以 `id(get_ledger)`（目前生效的 ledger 工廠函式身分）
    為 key、TTL 20 秒、上限 32 筆的 bounded 記憶體快取：同一個 `get_ledger`
    在 20 秒內重複呼叫只真的全掃一次（含同一次請求內 header + `/costs` 內容
    各呼叫一次，也只掃一次）；只要 `get_ledger` 被換掉（例如測試
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
    """
    key = id(get_ledger)
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
    runs = summary.get("runs", []) or []

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
  <p style="color:var(--tf-muted);font-size:.85rem">共 {len(runs)} 個 run（跨 run 持久化，見 out/cost_ledger.jsonl）</p>
</div>

<div class="tf-section">
  <h3>依 Model 分組</h3>
  <table><tr><th>Model</th><th>累計成本</th></tr>{model_rows or '<tr><td colspan="2">&#8212;</td></tr>'}</table>
</div>

<div class="tf-section">
  <h3>Per-run 明細（最近 {len(recent)} 筆，最新在前）</h3>
  <table>
    <tr><th>時間</th><th>幣種</th><th>題型</th><th>LLM 呼叫數</th><th>本次成本</th></tr>
    {run_rows_html or '<tr><td colspan="5">&#8212;（尚無紀錄）</td></tr>'}
  </table>
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
        f'<div style="color:var(--tf-muted2);font-size:.7rem;padding-left:.2rem">WHY {e(why_rep)}</div></div>'
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
            f'<span class="tf-src-pill">{e(ev.source)}</span>'
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


def render_page(
    body: str = "",
    active_mode: str = "offline",
    run_stats_html: str = "",
) -> str:
    """組完整 HTML（三檔模式徽章 + 表單 + body）。CLI web 與 Lambda handler 共用。

    三檔徽章（dark 樣式，見 .tf-mode-badge）恆同時列出：離線示範／真資料·$0
    （?real=1）／真 Bedrock（?live=1+token）——但**只有 `active_mode` 指定的
    那一檔**渲染成 active（動畫脈動點 + 該檔專屬色），其餘兩檔渲染成灰色靜態
    能力標籤（無動畫），代表「可用但非本次」。

    修復 MEDIUM：三檔徽章若同時顯示 active，使用者無法判斷本次畫面的證據到底
    來自樣本 / 真連接器 / 真 Bedrock，對「信任提煉」產品是實質誤導（provenance
    不清）。`active_mode` 須為呼叫方對本次請求實際生效模式的判斷結果（見
    `_active_mode()`），而非畫面能力宣告。

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
    """
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
    return _PAGE.format(
        mode=mode, body=body,
        version=html.escape(VERSION),
        cost_display=html.escape(_header_cost_display()),
        run_stats=run_stats_html,
        coins=_opts(COIN_POOL),
        types=_opts([t.value for t in QuestionType],
                    {"multi_source": "多源整合", "hypothesis": "假設驗證", "comparison": "比較分析"}),
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

    - Sources scanned／Passed filter／Flagged／Below threshold：四者都是對
      同一份 `evidence`（`_render_report`/`_render_comparison` 已收到的真實
      證據清單，即「證據清單」表格會逐列渲染的同一批物件）的計數，口徑彼此
      一致、可對帳（passed + flagged + below-threshold ＝ scanned，恆成立）：
        * scanned         = len(evidence)（本輪納入報告的證據總筆數）
        * flagged         = ev.flags 非空的筆數（`trust.scoring._manipulation_flags`
                            命中，即證據清單裡渲染 &#128681; 操縱紅旗徽章的同一批）
                            —— 命名故意不用「dropped」：這批證據**仍顯示在
                            報告裡**（只是帶紅旗警示），沒有真的被過濾掉，
                            標「dropped」是失真宣稱（CEO Chrome 複審 MEDIUM
                            修正，CLAUDE 規範 #24 不做假語意）。
        * passed filter   = 其餘「未被紅旗、且 trust>=0.3」的筆數（沿用
                            `_render_evidence_list` 既有的 tf-low 0.3 門檻，
                            不新造閾值）
        * below threshold = scanned − passed − flagged（未被紅旗但
                            trust<0.3 的證據；只在 >0 時才顯示這列，避免
                            多一列恆為 0 的雜訊）——先前版本沒有這一列，
                            導致 passed+flagged 對不上 scanned 總數，這裡
                            補上讓四個數字永遠能對帳。
      注意：這是「證據清單」這個階段的口徑（claim 抽取＋信任評分之後），不等於
      pipeline 最前端 `ingestion.collect()` 抓到的原始文件數（該數字目前只存在
      於 log 的自由文字 summary，沒有結構化欄位可安全取用，寧可不顯示也不用
      正則從文字反推假裝結構化——見 CLAUDE 規範 #24）。
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
        rows.append(("Sources scanned", str(len(evidence))))
        rows.append(("Passed filter", str(n_passed)))
        rows.append(("Flagged", str(n_flagged)))
        if n_below > 0:
            rows.append(("Below threshold", str(n_below)))

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
    ev_rows = _render_evidence_list(evidence)
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
  <span class="tf-coin-badge">{e(report.coin)}</span>
  <span class="tf-dash-sep">●</span>
  <span class="tf-dash-q">{e(report.question)}</span>
</div>

<div class="tf-section" style="background:rgba(31,111,235,.08);border-color:#1f6feb">
  <h2 style="margin:0 0 .4rem">{e(report.coin)} · {e(report.question_type)}</h2>
  <p class="j">市場判斷：{e(report.market_judgment)}</p>
  <div class="tf-hero-row">
    {conf_html}
    <div>{breakdown_html}</div>
  </div>
</div>

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
            raise ValueError(
                f"逗號分隔幣種必須剛好 2 個（目前 {len(parts)} 個），請如：coin=BTC,ETH"
            )
        invalid = [p for p in parts if p not in COIN_POOL]
        if invalid:
            raise ValueError(
                f"幣種 {invalid} 不在可選範圍 {COIN_POOL}，請選擇其中兩個"
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


def _parse_live(qs: dict, client_ip: str) -> bool:
    """從 qs 解析 live 模式開關，並在 live+有 IP 時執行限流。"""
    live = _is_live_request(qs)
    if live and client_ip:
        _check_live_rate_limit(client_ip)
    return live


def _is_real_request(qs: dict, live: bool) -> bool:
    """純判斷「真資料·$0」（?real=1）是否成立，無副作用、不觸發限流。見 `_is_live_request`。"""
    if live:
        return False
    return qs.get("real", ["0"])[0] == "1"


def _parse_real(qs: dict, client_ip: str, live: bool) -> bool:
    """從 qs 解析「真資料·$0」開關（?real=1）：走真連接器、免 Bedrock。

    不依賴 HAS_BEDROCK / token（與 live 檔位互相獨立）；`live` 已成立時
    real 不重複判斷（live 優先，走真 Bedrock 就不必再走真資料免敘事檔）。
    real 生效時比照 live 走同一組 per-IP 限流，避免真連接器被打爆。
    """
    real = _is_real_request(qs, live)
    if real and client_ip:
        _check_live_rate_limit(client_ip)
    return real


def _mode_extra_params(qs: dict) -> dict:
    """算出目前請求應在自我連結（如 `/analyze.json` 下載連結）保留的模式參數，
    以 dict 形式回傳（`{}` / `{"real": "1"}` / `{"live": "1", "token": <token>}`），
    交給呼叫端跟 coin/type/q 等其他參數**一起**丟進同一次 `urllib.parse.urlencode`
    組出完整 query string（見 `_analyze_json_href`）。

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
    if _is_real_request(qs, live):
        return {"real": "1"}
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

    純函式：邏輯與 `_mode_link_suffix`/`_parse_live`/`_parse_real` 完全一致
    （皆基於 `_is_live_request`/`_is_real_request`），不呼叫
    `_check_live_rate_limit`，不會讓同一次請求的畫面渲染重複消耗限流額度。
    """
    live = _is_live_request(qs)
    if live:
        return "live"
    if _is_real_request(qs, live):
        return "real"
    return "offline"


def _do_analyze(qs: dict, client_ip: str = "") -> tuple:
    """單幣分析入口，永遠回傳 (report, evidence, log) 三元組。

    只處理 multi_source / hypothesis；comparison 請改用 _do_comparison。

    Raises:
        ValueError:        幣種非法 / q 過長 / pipeline 無資料
        TooManyRequests:   同 IP live 請求超速
        其餘 Exception:    由呼叫方捕捉後回 502
    """
    coin_raw = (qs.get("coin", ["BTC"])[0]).strip()
    qtype = QuestionType(qs.get("type", ["multi_source"])[0])
    query = qs.get("q", ["分析該幣種近兩週市場狀況"])[0]
    if len(query) > 1000:
        raise ValueError(f"問題長度不能超過 1000 字元（目前 {len(query)} 字元）")

    live = _parse_live(qs, client_ip)
    real = _parse_real(qs, client_ip, live)

    coin = coin_raw.upper()
    if coin not in COIN_POOL:
        raise ValueError(f"幣種須為 {COIN_POOL} 之一")

    if real:
        report, evidence, log = run(coin, query, qtype, data_mode="live", llm_mode="off")
    else:
        report, evidence, log = run(coin, query, qtype, offline=not live)
    return report, evidence, log


def _do_comparison(qs: dict, client_ip: str = "") -> tuple:
    """雙幣比較分析入口，回傳 (report_a, evidence_a, report_b, evidence_b, log) 五元組。

    Raises:
        ValueError:        無法解析兩個幣種 / q 過長 / pipeline 無資料
        TooManyRequests:   同 IP live 請求超速
        其餘 Exception:    由呼叫方捕捉後回 502
    """
    coin_raw = (qs.get("coin", ["BTC"])[0]).strip()
    query = qs.get("q", ["分析該幣種近兩週市場狀況"])[0]
    if len(query) > 1000:
        raise ValueError(f"問題長度不能超過 1000 字元（目前 {len(query)} 字元）")

    live = _parse_live(qs, client_ip)
    real = _parse_real(qs, client_ip, live)

    pair = _parse_comparison_coins(coin_raw, query)
    if pair is None:
        raise ValueError(
            "comparison 題型需兩個幣種，請用逗號分隔（coin=BTC,ETH）"
            f"或在問題中提及兩個幣種（可選：{COIN_POOL}）"
        )
    coin_a, coin_b = pair
    if real:
        report_a, evidence_a, report_b, evidence_b, log = run_comparison(
            coin_a, coin_b, query, data_mode="live", llm_mode="off"
        )
    else:
        report_a, evidence_a, report_b, evidence_b, log = run_comparison(
            coin_a, coin_b, query, offline=not live
        )
    return report_a, evidence_a, report_b, evidence_b, log


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8", extra_headers=None):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; "
            "style-src 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com",
        )
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
        client_ip = self.client_address[0]

        if u.path == "/healthz":
            return self._send(200, "ok", "text/plain")

        # CEO 決策（PR #39，收斂）：theme toggle 切換機制（/theme 路由、
        # rtok render cache、cookie 讀寫、header ★ 按鈕）整個拆除——rtok
        # cache 是 process-local，重啟/部署/多 worker/TTL 過期都會 cache
        # miss，"切主題不重跑 pipeline" 與 "不遺失已產出報告" 在無狀態 SSR
        # 架構下本質難兩全。固定 dark（`render_page()` 預設值），
        # `var(--tf-*)` token 架構保留，等 #20（結果持久化）做對後再重新
        # 開放。`page()` 因此只是 `render_page()` 的薄包裝，不再需要算
        # toggle_href。
        def page(body="", active_mode="offline", run_stats_html=""):
            return render_page(body, active_mode=active_mode, run_stats_html=run_stats_html)

        if u.path == "/":
            return self._send(200, page(""))
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
                return self._send(429, page(
                    f"<p style='color:#c00'>{html.escape(str(exc))}</p>",
                    active_mode=active_mode))
            except ValueError as exc:
                return self._send(400, page(
                    f"<p style='color:#c00'>{html.escape(str(exc))}</p>",
                    active_mode=active_mode))
            except Exception:
                logging.exception("TrustForge analyze error")
                return self._send(502, page(
                    "<p style='color:#c00'>分析服務暫時無法使用，請稍後再試</p>",
                    active_mode=active_mode))
        return self._send(404, page("<p>404</p>"))


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"TrustForge web on :{PORT}  (bedrock={'live-capable' if HAS_BEDROCK else 'offline'})",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
