"""#121.7 時序修正回歸：reconcile 後 trustforge.service 必須真注入 LoadCredential= 行。

驗證 `scripts/reconcile_trustforge_unit.sh` 在既有 unit 檔上補上
`LoadCredential=trustforge-*` 行（由 ssm_params.runtime_token_load_credential_line
產生，嚴格對齊 app 讀取層），且拉起獨立 oneshot 憑證 unit
（Wants/After=trustforge-credentials.service）。

這條測試刻意跑「真 reconcile 腳本」而非隔離單元——證明生產環境 user-data /
update-in-place 真的會把 LoadCredential 行寫進 unit，而不是只在隔離測試裡假綠。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_RECONCILE = _REPO / "scripts" / "reconcile_trustforge_unit.sh"
_SRC = _REPO / "src"


@pytest.mark.skipif(not _RECONCILE.exists(), reason="reconcile 腳本不存在")
def test_reconcile_injects_loadcredential_lines_and_oneshot_wiring(tmp_path):
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
    # 關鍵斷言：生產環境真的注入 LoadCredential=trustforge-* 行。
    assert "LoadCredential=trustforge-admin-token:" in content
    assert "LoadCredential=trustforge-live-token:" in content
    # 獨立 oneshot 憑證 unit 被 Wants/After 拉起（不用 ExecStartPre 產憑證檔）。
    assert "Wants=trustforge-credentials.service" in content
    assert "After=trustforge-credentials.service" in content
    # 時序倒置的舊設計不該再出現：憑證不該由 trustforge.service 的 ExecStartPre 產生。
    assert "ExecStartPre=/opt/trustforge/deploy/setup_runtime_credentials.sh" not in content
    # 檔名必須帶 trustforge- 前綴，對齊 app 讀取層 $CREDENTIALS_DIRECTORY/trustforge-<name>。
    assert "LoadCredential=trustforge-admin-token:/run/trustforge-credentials/trustforge-admin-token" in content


@pytest.mark.skipif(not _RECONCILE.exists(), reason="reconcile 腳本不存在")
def test_reconcile_is_idempotent(tmp_path):
    """重跑 reconcile 不應重複插入 LoadCredential / Wants / After 行。"""
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
    assert content.count("LoadCredential=trustforge-admin-token:") == 1
    assert content.count("Wants=trustforge-credentials.service") == 1
    assert content.count("After=trustforge-credentials.service") == 1
