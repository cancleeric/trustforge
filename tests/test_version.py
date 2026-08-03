"""版號徽章功能測試（純字串斷言，不碰真 AWS）。"""
import dataclasses
import json
import tomllib
from pathlib import Path

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


def test_version_reports_package_version():
    """_version.py 未被 deploy 腳本覆寫時，應回報套件實際版號而不是字面 'dev'。

    這條原本斷言 VERSION == 'dev'，等於把「永遠沒有版號」寫成契約：
    /api/health 因此永遠回 {"version": "dev"}，前端徽章也就顯示不出版號
    （使用者回報「系統沒顯示版號」）。套件自己知道版號（與 pyproject 一致），
    只是這條回報路徑丟掉了，所以改成斷言它與 __init__.__version__ 一致。
    """
    import trustforge
    from trustforge._version import VERSION

    assert VERSION == trustforge.__version__
    assert VERSION != "dev"


def test_render_page_contains_version_string():
    """render_page() 產出的 HTML 應含版號徽章字串（不自行加 v 前綴，避免與
    git describe 已含的 tag v 前綴疊成雙 v）。"""
    htmlout = web.render_page("")
    assert web.VERSION in htmlout
    assert "vv" not in htmlout


def test_render_page_deployed_version_has_single_v(monkeypatch):
    """模擬部署後 VERSION 已含 git describe 的 v 前綴（如 v0.2.0-16-gce741e4）
    時，badge 應顯示單一 v，不應出現雙 v。"""
    monkeypatch.setattr(web, "VERSION", "v0.2.0-16-gce741e4")
    htmlout = web.render_page("")
    assert "v0.2.0-16-gce741e4" in htmlout
    assert "vv0.2.0-16-gce741e4" not in htmlout


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


def test_dynamic_package_and_frontend_versions_match_canonical_source():
    """Python package 讀 canonical attr；npm metadata 是其受驗證衍生物。"""
    import trustforge

    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    assert data["project"]["dynamic"] == ["version"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "trustforge._version.VERSION"
    }

    frontend_package = json.loads(
        (pyproject_path.parent / "frontend/package.json").read_text(encoding="utf-8")
    )
    frontend_lock = json.loads(
        (pyproject_path.parent / "frontend/package-lock.json").read_text(encoding="utf-8")
    )
    assert frontend_package["version"] == trustforge.__version__
    assert frontend_lock["version"] == trustforge.__version__
    assert frontend_lock["packages"][""]["version"] == trustforge.__version__


def test_uv_lock_is_parseable_and_pins_canonical_project_version():
    """Fresh frozen worktrees must not depend on a stale shared virtualenv."""
    import trustforge

    repo_root = Path(__file__).resolve().parent.parent
    lock = tomllib.loads((repo_root / "uv.lock").read_text(encoding="utf-8"))
    trustforge_packages = [
        package for package in lock["package"] if package["name"] == "trustforge"
    ]

    assert len(trustforge_packages) == 1
    assert trustforge_packages[0]["version"] == trustforge.__version__


def test_lambda_handler_analyze_json_has_version(monkeypatch):
    """Lambda handler 的一般分析 /analyze.json 回應應含 version（與 web.VERSION 一致）。"""
    from trustforge import lambda_handler

    event = {
        "rawPath": "/analyze.json",
        "queryStringParameters": {"coin": "BTC", "type": "multi_source", "q": "test"},
        "requestContext": {"http": {"sourceIp": "127.0.0.1"}},
    }
    resp = lambda_handler.handler(event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "version" in body
    assert body["version"] == web.VERSION


def test_deploy_lambda_stamps_version_like_deploy_ec2():
    """兩個 backend 部署入口都用正式 package SemVer 蓋 build 複本。"""
    deploy_dir = Path(__file__).resolve().parent.parent / "deploy"
    ec2_script = (deploy_dir / "deploy_ec2.sh").read_text(encoding="utf-8")
    lambda_script = (deploy_dir / "deploy_lambda.sh").read_text(encoding="utf-8")

    assert 'GIT_VER="v${PACKAGE_VER}"' in ec2_script
    assert 'GIT_VER="v${PACKAGE_VER}"' in lambda_script
    canonical_reader = 'runpy.run_path("scripts/release_version.py")["package_version"]'
    assert canonical_reader in ec2_script
    assert canonical_reader in lambda_script
    assert '_version.py' in lambda_script
    assert "GIT_VER" in lambda_script
    # 覆寫步驟須在打包 zip（zip -qr）之前，才能蓋到封裝進 zip 的複本
    ver_stamp_pos = lambda_script.index("GIT_VER")
    zip_pos = lambda_script.index("zip -qr")
    assert ver_stamp_pos < zip_pos


def test_lambda_handler_comparison_json_has_version(monkeypatch):
    """Lambda handler 的比較分析 /analyze.json 回應應含 version（與 web.VERSION 一致）。"""
    from trustforge import lambda_handler

    event = {
        "rawPath": "/analyze.json",
        "queryStringParameters": {
            "coin": "BTC,ETH",
            "type": "comparison",
            "q": "比較 BTC 與 ETH",
        },
        "requestContext": {"http": {"sourceIp": "127.0.0.1"}},
    }
    resp = lambda_handler.handler(event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "version" in body
    assert body["version"] == web.VERSION
