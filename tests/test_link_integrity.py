"""連結 & CTA 完整性測試 —— QA-PLAN.md Phase 1 對應實作。

背景（P-2026 生產 UX bug）：首頁 hero CTA 原本是 `href="#tf-query-console"`，
桌面雙欄版面下該錨點目標（Query Console 側欄）與 hero 同時已在首屏可見，
點擊後瀏覽器原生錨點跳轉沒有可視位移，使用者感覺「按了沒反應」；多幣總覽
5 張卡（`tf-overview-card`）原本是純 `<div>`，同樣點下去零反應。這個 bug 在
既有測試 100% 綠燈下發生——舊版 `test_web.py::test_render_home_page_has_
query_console_cta` 只斷言 `href="#tf-query-console"` 存在，等於把 bug 斷言
成預期行為，未斷言其可用性/可視效果，正是本檔案要補的缺口類型。

修法：hero CTA、每張多幣總覽卡皆已改成連到真 `/analyze` 分析連結（見
`web.py::_hero_analyze_href`、`scripts/fetch_scheduler.py::_overview_card_
href`），本檔案的 `test_hero_cta_is_real_analyze_link_not_dead_end_anchor`
即為此事故的永久 regression guard（原骨架為 xfail，修復後已轉綠）。

覆蓋範圍（仍非完整驗收測試，見 QA-PLAN.md）：
  - 尚未涵蓋 `/analyze`（表單提交）、`/costs`、`/status` 的完整旅程斷言
  - 「錨點是否為已可見元素的唯一互動」這類版面/視覺判斷，SSR 字串測試
    無法完整覆蓋（見 QA-PLAN.md 第 6 節），這裡只做得到的部分：
      (a) 錨點目標 id 是否存在於同一頁（非死錨點）
      (b) 內部路徑 href 是否真的 route 得到、回 200、非 error 頁
      (c) hero CTA 本身不能再是純錨點跳轉（本次事故根因）
"""
from __future__ import annotations

import re
from email.message import Message
from io import BytesIO

import pytest

from trustforge import web


def _get(path: str) -> tuple[int, str]:
    """比照 `tests/test_web.py::_do_get` 既有慣例，端到端呼叫
    `Handler.do_GET`（不開真 socket、不需瀏覽器），回傳 (status_code, body)。
    """
    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.path = path
    h.wfile = BytesIO()
    h.headers = Message()

    captured = []
    h.send_response = lambda code: captured.append(code)
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    h.do_GET()

    body = h.wfile.getvalue().decode("utf-8")
    return captured[0], body


# 有 id 的元素 pattern：id="xxx"（雙引號，符合本專案既有 render 慣例）
_ID_RE = re.compile(r'id="([^"]+)"')
# <a ... href="...">（含跨多屬性順序，只抓 href 值本身）
_HREF_RE = re.compile(r'<a\b[^>]*\bhref="([^"]*)"')

# 已知的「刻意示意用途、非 4xx/5xx 也非真頁面」外部 host allowlist
# （字型 CDN 等，不需要真的發請求驗證可達性——zero-dep 環境不打外部網路）
_EXTERNAL_ALLOWLIST_PREFIXES = ("https://fonts.googleapis.com", "https://fonts.gstatic.com")


def _extract_links(html_body: str) -> list[str]:
    return _HREF_RE.findall(html_body)


def _extract_ids(html_body: str) -> set[str]:
    return set(_ID_RE.findall(html_body))


@pytest.fixture(autouse=True)
def _isolate_home_overview_cache(monkeypatch, tmp_path):
    """比照 `tests/test_web.py` 既有 autouse fixture：避免首頁總覽背景
    thread 打到真 DynamoDB / 汙染跨測試狀態。"""
    monkeypatch.setenv("CACHE_BACKEND", "json")
    stop_event = web._overview_bg_stop_event
    if stop_event is not None:
        stop_event.set()
    thread = web._overview_bg_thread
    if thread is not None:
        thread.join(timeout=3.0)
    web._overview_bg_thread = None
    web._overview_bg_stop_event = None
    web._overview_html = None
    web._overview_expiry_epoch = 0.0
    # This module verifies link reachability, not rate limiting. Concurrent
    # reachability cases deliberately share the synthetic 127.0.0.1 client;
    # isolate that unrelated quota so xdist ordering cannot turn a valid link
    # into a spurious 429.
    monkeypatch.setattr(
        web, "_analyze_enforce_caller_rate_limit", lambda qs, client_ip: None
    )
    from trustforge.idempotency_lease import JsonLeaseBackend, set_lease_backend

    set_lease_backend(JsonLeaseBackend(tmp_path / "analyze-leases.json"))
    yield
    set_lease_backend(None)


def test_home_page_has_no_dead_fragment_anchors():
    """[核心防退回] 首頁所有 `href="#xxx"` 錨點，目標 id 必須真的存在於
    同一頁渲染結果中。hero CTA、多幣總覽卡修復後首頁目前已無任何錨點 CTA
    （全部改連真 `/analyze` 頁面），本測試在「零錨點」情況下仍應通過
    （vacuously true）——若未來又新增錨點式 CTA，這裡會繼續守住「不能是
    死錨點」這個下限。
    """
    status, body = _get("/")
    assert status == 200

    ids = _extract_ids(body)
    fragment_hrefs = [h for h in _extract_links(body) if h.startswith("#") and h != "#"]

    for href in fragment_hrefs:
        target_id = href[1:]
        assert target_id in ids, (
            f"死錨點：href=\"{href}\" 找不到對應的 id=\"{target_id}\""
            "（reachability 斷了，使用者點下去必然無反應）"
        )


def test_home_page_internal_links_route_to_200():
    """[核心防退回] 首頁上所有內部路徑 href（非錨點、非外部 host）
    真的 GET 得到、回 200、非明顯錯誤頁。"""
    status, body = _get("/")
    assert status == 200

    internal_paths = set()
    for href in _extract_links(body):
        if href.startswith("#") or not href:
            continue
        if href.startswith("http"):
            if not href.startswith(_EXTERNAL_ALLOWLIST_PREFIXES):
                pytest.fail(f"首頁出現未列入 allowlist 的外部連結，需人工確認：{href}")
            continue
        # 內部路徑（含 query string），去掉 query 部分只留 path 供路由比對，
        # 但實際請求時原樣送出（含 query）才能驗證完整旅程。
        internal_paths.add(href)

    assert internal_paths, "首頁應至少有一個內部導向連結（否則是純資訊頁，需確認是否符合預期）"

    for path in internal_paths:
        s, b = _get(path)
        assert s == 200, f"內部連結 {path} 沒有回 200（實際 {s}）"
        assert "Traceback" not in b, f"內部連結 {path} 回傳了未捕捉例外的錯誤頁"


def test_hero_cta_is_real_analyze_link_not_dead_end_anchor():
    """[本次事故 regression] Hero 主 CTA（`tf-hero-cta`）不能再是「唯一動作
    只有錨點跳轉、且目標與 hero 同時已在首屏可見」這種桌面上零視覺回饋的
    設計——這是本次生產 bug 的根因。

    修法：CTA 改連真正的 `/analyze` 頁面（見 `web.py::_hero_analyze_href`）。
    本測試取代原骨架的 xfail guard，轉為永久 regression 斷言：hero CTA href
    必須是會真的導航、回 200、且含分析結果的 `/analyze` 連結，不是 `#錨點`。
    """
    status, body = _get("/")
    assert status == 200

    hero_cta_match = re.search(r'<a class="tf-hero-cta"[^>]*href="([^"]+)"', body)
    assert hero_cta_match, "首頁應有 class=tf-hero-cta 的主要 CTA"
    href = hero_cta_match.group(1)

    assert not href.startswith("#"), (
        f"hero CTA 又變回純錨點跳轉（href={href!r}）——這正是本次生產事故的根因，"
        "桌面雙欄版面下目標本來就可見，點擊零視覺回饋。"
    )
    assert href.startswith("/analyze?"), f"hero CTA 應導向真 /analyze 分析頁，實際 href={href!r}"

    s, b = _get(href)
    assert s == 200, f"hero CTA href {href} 沒有回 200（實際 {s}）"
    assert "市場判斷" in b, "hero CTA 點下去應該看到真分析報告（含市場判斷區塊）"


def test_overview_cards_are_real_analyze_links_not_dead_divs():
    """[本次事故 regression·第二處] 多幣總覽卡（`tf-overview-card`）不能是
    純 `<div>`——若首頁當下有渲染總覽區塊，每張卡都必須是可導航、回 200
    的 `/analyze` 連結（見 `scripts/fetch_scheduler.py::_overview_card_
    href`）。本測試環境預設沒有總覽快照（見 `_isolate_home_overview_cache`
    autouse fixture），因此多半是「優雅缺席」——本測試在無總覽區塊時視為
    通過（vacuously true，跟 `test_overview_card_link.py` 的直接單元測試互補，
    那邊才是覆蓋「卡片本身怎麼組」的主要測試）；若總覽區塊存在，則強制
    比對死 `<div>` 版本不得出現。
    """
    status, body = _get("/")
    assert status == 200

    if "tf-overview-card" not in body:
        return  # 優雅缺席：本次首頁沒有總覽區塊可測

    assert '<div class="tf-overview-card"' not in body, "多幣總覽卡片不能是死 <div>"
    for href in re.findall(r'<a class="tf-overview-card" href="([^"]+)"', body):
        assert href.startswith("/analyze?"), f"總覽卡片應導向真 /analyze 分析頁，實際 href={href!r}"
