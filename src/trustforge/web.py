"""TrustForge Live Demo web 服務（純 stdlib，App Runner / 容器就緒）。

路由：
  GET /            首頁表單（選幣種/題型/問題）
  GET /healthz     健康檢查（App Runner 用）→ 200 "ok"
  GET /analyze     ?coin=BTC&q=...&type=multi_source[&live=1&token=<TOKEN>] → HTML 報告
  GET /analyze.json 同上參數 → JSON {report, evidence, log}

預設離線模式（用官方 OHLCV + 樣本來源 + Bedrock stub），故未設 AWS 也能跑出 Live Demo。
live 模式需同時滿足：設了 BEDROCK_MODEL_ID、TRUSTFORGE_LIVE_TOKEN，
且請求帶正確 token 參數（用 hmac.compare_digest 比對）。
埠口取自環境變數 PORT（App Runner 預設 8080）。
"""
from __future__ import annotations

import dataclasses
import hmac
import html
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

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

# per-IP 限流：每 IP 每 60 秒最多 5 次 live 請求
_RATE_WINDOW = 60
_RATE_MAX = 5
_rate_lock = threading.Lock()
_rate_buckets: dict[str, list[float]] = {}

_PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TrustForge — 加密市場分析 AI Agent</title>
<style>
 body{{font-family:-apple-system,"PingFang TC",sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem;color:#1a1a1a;background:#fafafa}}
 h1{{margin-bottom:.2rem}} .sub{{color:#666;margin-top:0}}
 form{{background:#fff;border:1px solid #e2e2e2;border-radius:12px;padding:1.2rem;display:flex;gap:.8rem;flex-wrap:wrap;align-items:end}}
 label{{display:block;font-size:.8rem;color:#555;margin-bottom:.2rem}}
 select,input,button{{padding:.5rem .7rem;border:1px solid #ccc;border-radius:8px;font-size:1rem}}
 input[name=q]{{min-width:340px}} button{{background:#1f6feb;color:#fff;border:0;cursor:pointer}}
 .badge{{display:inline-block;background:#eef;border-radius:6px;padding:.1rem .5rem;font-size:.75rem;color:#356}}
 pre{{background:#fff;border:1px solid #e2e2e2;border-radius:12px;padding:1rem;white-space:pre-wrap;word-break:break-word}}
 table{{border-collapse:collapse;width:100%;background:#fff;font-size:.85rem}} td,th{{border:1px solid #e2e2e2;padding:.4rem;text-align:left}}
 .j{{font-size:1.1rem;font-weight:600}} .conf{{color:#1f6feb}}
 .tf-section{{background:#fff;border:1px solid #e2e2e2;border-radius:10px;padding:1rem;margin:.8rem 0}}
 .tf-section h3{{margin-top:0;font-size:1rem;border-bottom:1px solid #eee;padding-bottom:.4rem;margin-bottom:.7rem}}
 .tf-bar-wrap{{display:inline-block;vertical-align:middle;width:90px;height:10px;background:#e8e8e8;border-radius:5px;overflow:hidden;margin-right:4px}}
 .tf-bar{{height:100%;border-radius:5px}}
 .tf-low{{display:inline-block;background:#fde;border:1px solid #f99;color:#900;border-radius:4px;padding:.1rem .35rem;font-size:.68rem;font-weight:600;margin-left:4px}}
 .tf-conf-wrap{{background:#f6f8fa;border:1px solid #e2e2e2;border-radius:8px;padding:.8rem;margin:.5rem 0}}
 .tf-conf-big{{font-size:1.6rem;font-weight:700;margin:0 0 .2rem}}
</style></head><body>
<h1>TrustForge</h1><p class="sub">加密市場分析 AI Agent — 多源資訊的信任提煉　<span class="badge">{mode}</span>　<a href="/costs">成本帳本</a></p>
<p><span class="badge" style="opacity:.6">v{version}</span></p>
<form action="/analyze" method="get">
 <div><label>幣種</label><select name="coin">{coins}</select></div>
 <div><label>題型</label><select name="type">{types}</select></div>
 <div><label>問題</label><input name="q" value="分析該幣種近兩週市場狀況，整合多源資料"></div>
 <button type="submit">分析</button>
</form>
{body}
</body></html>"""


class TooManyRequests(Exception):
    """per-IP 限流超量時拋出，對應 HTTP 429。"""


def _check_live_rate_limit(ip: str) -> None:
    """IP 在滑動視窗內超過 _RATE_MAX 次 live 請求 → raise TooManyRequests。"""
    now = time.time()
    with _rate_lock:
        ts = [t for t in _rate_buckets.get(ip, []) if now - t < _RATE_WINDOW]
        if len(ts) >= _RATE_MAX:
            raise TooManyRequests(f"請求過於頻繁，請 {_RATE_WINDOW} 秒後再試")
        ts.append(now)
        _rate_buckets[ip] = ts


def _opts(values, labels=None):
    labels = labels or {v: v for v in values}
    return "".join(
        f'<option value="{html.escape(v)}">{html.escape(labels[v])}</option>'
        for v in values
    )


def _trust_bar(trust: float) -> str:
    """CSS 信任橫條（依層級上色：高綠/中橙/低紅）。"""
    pct = max(0, min(100, int(trust * 100)))
    if trust >= 0.7:
        color, label = "#22863a", "高"
    elif trust >= 0.3:
        color, label = "#d9832a", "中"
    else:
        color, label = "#cb2431", "低"
    return (
        f'<span class="tf-bar-wrap">'
        f'<span class="tf-bar" style="width:{pct}%;background:{color}"></span>'
        f'</span>'
        f'<span style="color:{color};font-size:.8rem"> {trust:.2f} {label}</span>'
    )


def _conf_gauge(confidence: float, label: str) -> str:
    """整體信心視覺化：大字標籤 + 橫條。"""
    pct = max(0, min(100, int(confidence * 100)))
    if confidence >= 0.7:
        color = "#22863a"
    elif confidence >= 0.45:
        color = "#d9832a"
    else:
        color = "#cb2431"
    return (
        f'<div class="tf-conf-wrap">'
        f'<div class="tf-conf-big" style="color:{color}">{html.escape(label)}</div>'
        f'<div style="font-size:.85rem;color:#555">整體信心指數 {confidence:.2f}</div>'
        f'<div class="tf-bar-wrap" style="width:180px;height:13px;margin-top:.4rem">'
        f'<div class="tf-bar" style="width:{pct}%;background:{color}"></div>'
        f'</div></div>'
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
        f'<p style="color:#666;font-size:.85rem">'
        f'共 {len(cost_events)} 次 LLM 呼叫；輸入 {tokens_in} tokens／輸出 {tokens_out} tokens</p>'
        f'<table><tr><th>Model</th><th>輸入 tokens</th><th>輸出 tokens</th><th>估算成本</th></tr>'
        f'{rows}</table>'
        f'</div>'
    )


def _render_costs_page() -> str:
    """`/costs`：跨 run 持久化成本帳本彙總頁 —— 累計總花費、依 model 分組、per-run 明細。

    累計總花費超過 env `COST_BUDGET_USD` 門檻 → 卡片轉紅告警。帳本 backend 讀取
    失敗（如 `COST_LEDGER_BACKEND=dynamodb` 但未實作）→ fallback 讀 `JsonlLedger`，
    與 `ledger.append_run()` 的 fallback 邏輯一致，確保頁面永遠可顯示。
    """
    e = html.escape
    try:
        summary = get_ledger().summary()
    except Exception:
        summary = JsonlLedger().summary()

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
        "border-color:#cb2431;background:#fff5f5" if over_budget else "border-color:#1f6feb;background:#f0f6ff"
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
        offline_badge = " <small style='color:#888'>(離線)</small>" if r.get("offline") else ""
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
  <p style="color:#666;font-size:.85rem">共 {len(runs)} 個 run（跨 run 持久化，見 out/cost_ledger.jsonl）</p>
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

    manip_color  = "#cb2431" if manip > 0 else "#333"
    manip_weight = "font-weight:600;" if manip > 0 else ""

    corr_text  = "✓ 有獨立來源交叉佐證" if corr > 0 else "— 無交叉佐證"
    corr_color = "#22863a"              if corr > 0 else "#888"

    return (
        f'<div style="margin:.35rem 0;padding:.4rem .6rem;background:#f8f9fa;'
        f'border-radius:6px;border:1px solid #e2e2e2;font-size:.78rem">'
        f'<div style="color:#888;font-size:.7rem;font-weight:600;margin-bottom:.25rem">'
        f'信任分析（信譽×0.5 + 佐證×0.25 + 時效×0.15 − 操縱×0.4）</div>'
        f'<div style="display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;margin-bottom:.2rem">'
        # 信譽
        f'<span style="white-space:nowrap">'
        f'<span style="color:#555">信譽</span> '
        f'{mini_bar(rep, "#22863a")} '
        f'<span style="color:#333">{rep:.2f}</span>'
        f'<span style="color:#888"> ×0.5</span></span>'
        # 佐證
        f'<span style="color:#bbb">｜</span>'
        f'<span style="white-space:nowrap">'
        f'<span style="color:#555">佐證</span> '
        f'{mini_bar(corr, "#1f6feb")} '
        f'<span style="color:#333">{corr:.2f}</span>'
        f'<span style="color:#888"> ×0.25</span></span>'
        # 時效
        f'<span style="color:#bbb">｜</span>'
        f'<span style="white-space:nowrap">'
        f'<span style="color:#555">時效</span> '
        f'{mini_bar(rec, "#8957e5")} '
        f'<span style="color:#333">{rec:.2f}</span>'
        f'<span style="color:#888"> ×0.15</span></span>'
        # 操縱
        f'<span style="color:#bbb">｜</span>'
        f'<span style="white-space:nowrap">'
        f'<span style="color:#555">操縱</span> '
        f'{mini_bar(manip, "#cb2431")} '
        f'<span style="color:{manip_color};{manip_weight}">{manip:.2f}</span>'
        f'<span style="color:#888"> ×0.4</span></span>'
        # 結果
        f'<span style="color:#bbb">→</span>'
        f'<span style="white-space:nowrap;font-weight:600">信任 {trust:.2f}</span>'
        f'</div>'
        f'<div style="color:{corr_color};font-size:.75rem">{e(corr_text)}</div>'
        f'</div>'
    )


def _render_evidence_list(
    evidence: list, coin: str | None = None, start_idx: int = 0
) -> str:
    """evidence 渲染為帶信任橫條 + 可展開 <details> 的 <tr> 列表。

    - trust < 0.3 或 contrarian 項目顯示紅色 tf-low badge。
    - source_url 透過 _safe_href 渲染：http/https 輸出連結，其餘輸出純文字。
    - trust_components 有值時在 <details> 內顯示分項拆解。
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
        # source_url 安全連結：_safe_href 驗 scheme，escape 由其內部保留
        if ev.source_url:
            url_html = _safe_href(ev.source_url)
        else:
            url_html = "&#8212;"
        coin_td = f"<td>{e(coin)}</td>" if coin is not None else ""
        row_style = ' style="background:#fff5f5"' if is_low else ""
        rows.append(
            f"<tr{row_style}>"
            f"<td>E{idx}{badge}</td>"
            f"{coin_td}"
            f"<td>"
            f"<details><summary>{e(ev.source)} · {e(ev.fetched_at)}</summary>"
            f"<p style='margin:.3rem 0;font-size:.85rem'>{e(ev.content_reference)}</p>"
            f"<p style='margin:.3rem 0;font-size:.82rem'>URL: {url_html}</p>"
            f"{_render_trust_breakdown(ev.trust_components, ev.trust)}"
            f"</details>"
            f"</td>"
            f"<td>{_trust_bar(ev.trust)}</td>"
            f"</tr>"
        )
    return "".join(rows)


def render_page(body: str = "") -> str:
    """組完整 HTML（模式徽章 + 表單 + body）。CLI web 與 Lambda handler 共用。"""
    mode = "AWS Bedrock 就緒（?live=1 啟用）" if HAS_BEDROCK else "離線示範模式（未設 BEDROCK_MODEL_ID）"
    return _PAGE.format(
        mode=html.escape(mode), body=body,
        version=html.escape(VERSION),
        coins=_opts(COIN_POOL),
        types=_opts([t.value for t in QuestionType],
                    {"multi_source": "多源整合", "hypothesis": "假設驗證", "comparison": "比較分析"}),
    )


def _render_cross_signal(signal: dict) -> str:
    """跨源訊號帶色框渲染（inline style，CSP 相容，無外部資源/JS）。

    背離 = 橙色系（#d9832a）；共識 = 藍色系（#1f6feb）。
    summary 與所有字串一律 html.escape（縱深防禦）。
    """
    e = html.escape
    sig_type = signal.get("type", "")
    if sig_type == "divergence":
        border_color = "#d9832a"
        bg_color = "#fff8f0"
        type_label = "背離"
    else:
        border_color = "#1f6feb"
        bg_color = "#f0f6ff"
        type_label = "共識"
    summary_esc = e(signal.get("summary", ""))
    ids = signal.get("supporting_claim_ids", [])
    ids_html = (
        f'<small style="color:#666">佐證 claim_ids：{e(", ".join(ids))}</small>'
        if ids else ""
    )
    return (
        f'<div class="tf-section" style="border-left:4px solid {border_color};background:{bg_color}">'
        f'<h3 style="color:{border_color}">跨源訊號（{e(type_label)}）</h3>'
        f'<p style="margin:.3rem 0">{summary_esc}</p>'
        f'{ids_html}'
        f'</div>'
    )


def _render_report(report, evidence, log=None) -> str:
    """分析結果渲染為信任儀表板（事實→推論→結論三段 + 信任橫條 + 可展開 evidence）。

    `log`：可選的 `ExecutionLog`，提供時嵌入「本次分析成本」卡（見 `_render_cost_card`）。
    comparison 頁面內嵌的單幣詳細分析不傳 `log`（避免重複顯示合併後的整體成本卡）。
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
    conf_html = _conf_gauge(report.confidence, report.confidence_label())
    ev_rows = _render_evidence_list(evidence)
    cross_html = (
        _render_cross_signal(report.cross_source_signal)
        if getattr(report, "cross_source_signal", None) else ""
    )
    cost_html = _render_cost_card(log) if log is not None else ""
    return f"""
<div class="tf-section" style="background:#f0f6ff;border-color:#1f6feb">
  <h2 style="margin:0 0 .4rem">{e(report.coin)} · {e(report.question_type)}</h2>
  <p class="j">市場判斷：{e(report.market_judgment)}</p>
  {conf_html}
</div>

<div class="tf-section" style="border-left:4px solid #22863a">
  <h3>事實（客觀資料）</h3>
  <ul>{facts or '<li>&#8212;</li>'}</ul>
</div>

<div class="tf-section" style="border-left:4px solid #d9832a">
  <h3>推論（Agent 推理）</h3>
  <ul>{infer or '<li>&#8212;</li>'}</ul>
</div>

<div class="tf-section" style="border-left:4px solid #1f6feb">
  <h3>結論 / 關鍵依據</h3>
  <ul>{basis or '<li>&#8212;</li>'}</ul>
</div>

<div class="tf-section">
  <h3>信心說明 · 限制</h3>
  <ul>{limits or '<li>&#8212;</li>'}</ul>
  <h4>可能推翻結論的條件</h4>
  <ul>{flips or '<li>&#8212;</li>'}</ul>
</div>

{cross_html}

<div class="tf-section" style="border-left:4px solid #cb2431">
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

<p><a href="/analyze.json?coin={e(report.coin)}&type={e(report.question_type)}&q={e(report.question)}">下載 JSON（report+evidence+log）</a></p>
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


def _render_comparison(report_a, evidence_a, report_b, evidence_b, query: str, log=None) -> str:
    """comparison 結果渲染成 HTML（並列比較儀表板 + 信任橫條 + 可展開 evidence）。

    `log`：兩幣共用同一個 `ExecutionLog`（見 `pipeline.run_comparison`），提供時
    在頂層嵌一張合併「本次分析成本」卡（涵蓋兩幣總花費）；內嵌的單幣詳細分析
    不重複帶 log（避免同一份合併成本重複顯示兩次）。
    """
    e = html.escape
    dir_a = report_a.direction or report_a._direction_label()
    dir_b = report_b.direction or report_b._direction_label()

    def _cmp_conf(conf: float, label: str) -> str:
        pct = max(0, min(100, int(conf * 100)))
        color = "#22863a" if conf >= 0.7 else "#d9832a" if conf >= 0.45 else "#cb2431"
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
    return f"""
<div class="tf-section" style="background:#f0f6ff;border-color:#1f6feb">
  <h2 style="margin:0 0 .3rem">{e(report_a.coin)} vs {e(report_b.coin)} · comparison</h2>
  <p style="color:#555;margin:.2rem 0">{e(query)}</p>
</div>

<div class="tf-section">
  <h3>1. 相對強弱比較</h3>
  <table>
    <tr><th>項目</th><th>{e(report_a.coin)}</th><th>{e(report_b.coin)}</th></tr>
    <tr><td>市場方向</td><td>{e(dir_a)}</td><td>{e(dir_b)}</td></tr>
    <tr><td>整體信心</td>
        <td>{_cmp_conf(report_a.confidence, report_a.confidence_label())}</td>
        <td>{_cmp_conf(report_b.confidence, report_b.confidence_label())}</td></tr>
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

<details class="tf-section"><summary>&#9654; {e(report_a.coin)} 詳細分析</summary>
{_render_report(report_a, evidence_a)}
</details>
<details class="tf-section"><summary>&#9654; {e(report_b.coin)} 詳細分析</summary>
{_render_report(report_b, evidence_b)}
</details>
"""



def _parse_live(qs: dict, client_ip: str) -> bool:
    """從 qs 解析 live 模式開關，並在 live+有 IP 時執行限流。"""
    req_token = qs.get("token", [""])[0]
    live = (
        HAS_BEDROCK
        and qs.get("live", ["0"])[0] == "1"
        and bool(LIVE_TOKEN)
        and hmac.compare_digest(req_token, LIVE_TOKEN)
    )
    if live and client_ip:
        _check_live_rate_limit(client_ip)
    return live


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

    coin = coin_raw.upper()
    if coin not in COIN_POOL:
        raise ValueError(f"幣種須為 {COIN_POOL} 之一")

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

    pair = _parse_comparison_coins(coin_raw, query)
    if pair is None:
        raise ValueError(
            "comparison 題型需兩個幣種，請用逗號分隔（coin=BTC,ETH）"
            f"或在問題中提及兩個幣種（可選：{COIN_POOL}）"
        )
    coin_a, coin_b = pair
    report_a, evidence_a, report_b, evidence_b, log = run_comparison(
        coin_a, coin_b, query, offline=not live
    )
    return report_a, evidence_a, report_b, evidence_b, log


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("X-Content-Type-Options", "nosniff")
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
        page = render_page
        if u.path == "/":
            return self._send(200, page(""))
        if u.path == "/costs":
            return self._send(200, page(_render_costs_page()))
        if u.path in ("/analyze", "/analyze.json"):
            # 提前解析 qtype 以便分流，不依賴回傳 tuple 長度
            try:
                qtype = QuestionType(qs.get("type", ["multi_source"])[0])
            except ValueError:
                qtype = QuestionType.MULTI_SOURCE

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
                    return self._send(
                        200,
                        page(_render_comparison(report_a, evidence_a, report_b, evidence_b, query, log)),
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
                    return self._send(200, page(_render_report(report, evidence, log)))
            except TooManyRequests as exc:
                return self._send(429, page(
                    f"<p style='color:#c00'>{html.escape(str(exc))}</p>"))
            except ValueError as exc:
                return self._send(400, page(
                    f"<p style='color:#c00'>{html.escape(str(exc))}</p>"))
            except Exception:
                logging.exception("TrustForge analyze error")
                return self._send(502, page(
                    "<p style='color:#c00'>分析服務暫時無法使用，請稍後再試</p>"))
        return self._send(404, page("<p>404</p>"))


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"TrustForge web on :{PORT}  (bedrock={'live-capable' if HAS_BEDROCK else 'offline'})",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
