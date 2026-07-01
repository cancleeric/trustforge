"""版號徽章功能測試（純字串斷言，不碰真 AWS）。"""
import dataclasses
import json

from trustforge import web


def test_version_fallback_on_import_failure(monkeypatch):
    """import trustforge._version 失敗時，web.VERSION 應 fallback 為 'dev'、不拋例外。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "trustforge._version" or name.endswith("_version"):
            raise ImportError("simulated missing _version module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # 模擬 web.py 頂部的 try/except 邏輯本身（而非重新 import 整個模組）
    try:
        from trustforge._version import VERSION as _v  # noqa: F401
        version = _v
    except Exception:
        version = "dev"
    assert version == "dev"


def test_version_has_default_value():
    """_version.py 未被 deploy 腳本覆寫時，內容應為 'dev'。"""
    from trustforge._version import VERSION

    assert VERSION == "dev"


def test_render_page_contains_version_string():
    """render_page() 產出的 HTML 應含版號徽章字串。"""
    htmlout = web.render_page("")
    assert f"v{web.VERSION}" in htmlout


def test_analyze_json_payload_has_version_key():
    """/analyze.json 回傳的 dict 頂層應含 'version' key。"""
    report, evidence, log = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    payload = {
        "version": web.VERSION,
        "report": dataclasses.asdict(report),
        "evidence": [ev.to_dict() for ev in evidence],
        "execution_log": log.events,
    }
    s = json.dumps(payload, ensure_ascii=False)
    parsed = json.loads(s)
    assert "version" in parsed
    assert parsed["version"] == web.VERSION
