"""reconcile 回歸：reconcile 後 trustforge.service 必須含 sweep ExecStartPre，
且**絕不含**任何 systemd tmpfs 憑證層（LoadCredential= / CREDENTIALS_DIRECTORY /
trustforge-credentials.service）。

#121.7 的 systemd tmpfs 憑證層經 codex-review 第三輪實測確認在真實部署路徑完全
失效且有「假安全感 + 服務起不來」風險，已移除並回退 SSM 路徑。本測試刻意跑「真
reconcile 腳本」而非隔離單元——證明生產環境 update-in-place 真的**不會**再注入
任何會讓服務起不來的 LoadCredential 行。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_RECONCILE = _REPO / "scripts" / "reconcile_trustforge_unit.sh"
_SRC = _REPO / "src"

_CREDENTIAL_LAYER_TOKENS = (
    "LoadCredential=",
    "CREDENTIALS_DIRECTORY",
    "trustforge-credentials.service",
    "Wants=trustforge-credentials.service",
    "After=trustforge-credentials.service",
)


@pytest.mark.skipif(not _RECONCILE.exists(), reason="reconcile 腳本不存在")
def test_reconcile_injects_sweep_and_no_credential_layer(tmp_path):
    unit = tmp_path / "trustforge.service"
    unit.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=TrustForge web",
                "After=network.target",
                "[Service]",
                "Environment=PYTHONPATH=/opt/trustforge",
                "ExecStart=/usr/bin/python3 -m trustforge.web",
                "Restart=always",
                "[Install]",
                "WantedBy=multi-user.target",
                "",
            ]
        ),
        encoding="utf-8",
    )

    env = {**os.environ, "PYTHONPATH": str(_SRC), "UNIT_PATH": str(unit)}
    result = subprocess.run(
        ["bash", str(_RECONCILE)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr

    content = unit.read_text(encoding="utf-8")
    # 部署期臨時參數 sweep 仍被注入（#121.6，正確保留部分）。
    assert "ExecStartPre=/opt/trustforge/scripts/sweep_deploy_parameters.sh" in content
    # 關鍵斷言：已徹底移除 systemd tmpfs 憑證層——絕不能再注入這些行，否則
    # 來源憑證檔缺失時服務會起不來。
    for token in _CREDENTIAL_LAYER_TOKENS:
        assert token not in content, f"reconcile 仍注入了憑證層標記：{token!r}"


@pytest.mark.skipif(not _RECONCILE.exists(), reason="reconcile 腳本不存在")
def test_reconcile_is_idempotent_and_stays_credential_free(tmp_path):
    """重跑 reconcile 不應重複插入 sweep；且任何情況下都不該出現憑證層。"""
    unit = tmp_path / "trustforge.service"
    unit.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=TrustForge web",
                "After=network.target",
                "[Service]",
                "Environment=PYTHONPATH=/opt/trustforge",
                "ExecStart=/usr/bin/python3 -m trustforge.web",
                "Restart=always",
                "[Install]",
                "WantedBy=multi-user.target",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(_SRC), "UNIT_PATH": str(unit)}
    for _ in range(2):
        res = subprocess.run(
            ["bash", str(_RECONCILE)], capture_output=True, text=True, env=env
        )
        assert res.returncode == 0, res.stderr

    content = unit.read_text(encoding="utf-8")
    assert content.count(
        "ExecStartPre=/opt/trustforge/scripts/sweep_deploy_parameters.sh"
    ) == 1
    for token in _CREDENTIAL_LAYER_TOKENS:
        assert token not in content, f"reconcile 仍注入了憑證層標記：{token!r}"
