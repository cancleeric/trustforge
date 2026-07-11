"""deploy_ec2.sh 產出的 trustforge.service unit 不得含任何 systemd tmpfs 憑證層。

#121.7 的 LoadCredential 憑證層已移除並回退 SSM 路徑。本測試解析 deploy_ec2.sh
內嵌的 user-data（其 `cat > /etc/systemd/system/trustforge.service <<UNIT` heredoc）
並斷言該 unit 不含 `LoadCredential=` / `CREDENTIALS_DIRECTORY` /
`trustforge-credentials`——確認部署腳本不會再生產出會讓服務起不來的 unit。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_DEPLOY_EC2 = _REPO / "deploy" / "deploy_ec2.sh"

_CREDENTIAL_LAYER_TOKENS = (
    "LoadCredential=",
    "CREDENTIALS_DIRECTORY",
    "trustforge-credentials",
    "runtime_token_load_credential_line",
    "setup_runtime_credentials",
)


def _extract_trustforge_unit(script: str) -> str:
    """從 deploy_ec2.sh 抽出內嵌的 trustforge.service heredoc 內容。

    user-data 用 `cat > "$UD" <<EOF` 包住；其中 unit 用
    `cat > /etc/systemd/system/trustforge.service <<UNIT ... UNIT` 定義。
    回傳 UNIT ... UNIT 之間的本文。
    """
    # 找到 unit heredoc 起始行
    start = re.search(r"cat > /etc/systemd/system/trustforge\.service <<UNIT", script)
    if not start:
        pytest.skip("deploy_ec2.sh 未內嵌 trustforge.service unit heredoc")
    body = script[start.end():]
    # unit heredoc 以單獨一行的 `UNIT` 結束
    end = re.search(r"\nUNIT\n", body)
    if not end:
        pytest.skip("找不到 unit heredoc 結束標記")
    return body[: end.start()]


@pytest.mark.skipif(not _DEPLOY_EC2.exists(), reason="deploy_ec2.sh 不存在")
def test_deploy_ec2_produced_unit_has_no_credential_layer() -> None:
    script = _DEPLOY_EC2.read_text(encoding="utf-8")
    unit = _extract_trustforge_unit(script)
    for token in _CREDENTIAL_LAYER_TOKENS:
        assert token not in unit, (
            f"deploy_ec2.sh 產出的 unit 仍含憑證層標記：{token!r}"
        )
