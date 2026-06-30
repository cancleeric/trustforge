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

from .schema import COIN_POOL, QuestionType
from .pipeline import run

PORT = int(os.getenv("PORT", "8080"))
HAS_BEDROCK = bool(os.getenv("BEDROCK_MODEL_ID"))
LIVE_TOKEN = os.getenv("TRUSTFORGE_LIVE_TOKEN", "")

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
</style></head><body>
<h1>TrustForge</h1><p class="sub">加密市場分析 AI Agent — 多源資訊的信任提煉　<span class="badge">{mode}</span></p>
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


def render_page(body: str = "") -> str:
    """組完整 HTML（模式徽章 + 表單 + body）。CLI web 與 Lambda handler 共用。"""
    mode = "AWS Bedrock 就緒（?live=1 啟用）" if HAS_BEDROCK else "離線示範模式（未設 BEDROCK_MODEL_ID）"
    return _PAGE.format(
        mode=html.escape(mode), body=body,
        coins=_opts(COIN_POOL),
        types=_opts([t.value for t in QuestionType],
                    {"multi_source": "多源整合", "hypothesis": "假設驗證", "comparison": "比較分析"}),
    )


def _render_report(report, evidence) -> str:
    e = html.escape
    rows = "".join(
        f"<tr><td>E{i}</td><td>{e(ev.source)}</td><td>{e(ev.fetched_at)}</td>"
        f"<td>{ev.trust:.2f}</td><td>{e(ev.content_reference[:90])}</td></tr>"
        for i, ev in enumerate(evidence)
    )
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
    return f"""
<h2>{e(report.coin)} · {e(report.question_type)}</h2>
<p class="j">市場判斷：{e(report.market_judgment)}</p>
<p>整體信心：<span class="conf">{report.confidence_label()}（{report.confidence:.2f}）</span></p>
<h3>事實（客觀資料）</h3><ul>{facts}</ul>
<h3>推論（Agent 推理）</h3><ul>{infer}</ul>
<h3>關鍵依據 → 證據</h3><ul>{basis}</ul>
<h3>信心說明 · 限制</h3><ul>{limits or '<li>—</li>'}</ul>
<h3>可能推翻結論的條件</h3><ul>{flips}</ul>
<h3>反方 / 低信任（未納入主結論）</h3><ul>{contra or '<li>—</li>'}</ul>
<h3>證據清單（會被抽查回溯）</h3>
<table><tr><th>#</th><th>source</th><th>fetched_at</th><th>trust</th><th>content_reference</th></tr>{rows}</table>
<p><a href="/analyze.json?coin={e(report.coin)}&type={e(report.question_type)}&q={e(report.question)}">下載 JSON（report+evidence+log）</a></p>
"""


def _do_analyze(qs: dict, client_ip: str = ""):
    """共用分析入口。

    Args:
        qs:        query string 字典（值為 list[str]，與 parse_qs 相同格式）
        client_ip: 呼叫方 IP，供 per-IP 限流使用；空字串略過限流
    Raises:
        ValueError:        幣種非法 / q 過長 / pipeline 無資料
        TooManyRequests:   同 IP live 請求超速
        其餘 Exception:    由呼叫方捕捉後回 502
    """
    coin = (qs.get("coin", ["BTC"])[0]).upper()
    if coin not in COIN_POOL:
        raise ValueError(f"幣種須為 {COIN_POOL} 之一")
    qtype = QuestionType(qs.get("type", ["multi_source"])[0])
    query = qs.get("q", ["分析該幣種近兩週市場狀況"])[0]
    if len(query) > 1000:
        raise ValueError(f"問題長度不能超過 1000 字元（目前 {len(query)} 字元）")

    req_token = qs.get("token", [""])[0]
    live = (
        HAS_BEDROCK
        and qs.get("live", ["0"])[0] == "1"
        and bool(LIVE_TOKEN)
        and hmac.compare_digest(req_token, LIVE_TOKEN)
    )
    if live and client_ip:
        _check_live_rate_limit(client_ip)

    report, evidence, log = run(coin, query, qtype, offline=not live)
    return report, evidence, log


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
        if u.path in ("/analyze", "/analyze.json"):
            try:
                report, evidence, log = _do_analyze(qs, client_ip=client_ip)
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
            if u.path == "/analyze.json":
                payload = {
                    "report": dataclasses.asdict(report),
                    "evidence": [ev.to_dict() for ev in evidence],
                    "execution_log": log.events,
                }
                return self._send(200, json.dumps(payload, ensure_ascii=False, indent=2),
                                  "application/json; charset=utf-8")
            return self._send(200, page(_render_report(report, evidence)))
        return self._send(404, page("<p>404</p>"))


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"TrustForge web on :{PORT}  (bedrock={'live-capable' if HAS_BEDROCK else 'offline'})",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
