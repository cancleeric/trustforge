"""測試 trustforge.ssm_params 模組的 get_runtime_token 行為。

涵蓋範圍：
- 旗標 TRUSTFORGE_TOKEN_SSM_PREFIX 未設或為空字串時，完全不呼叫 boto3.client。
- 旗標有設時，透過注入 fake client 模擬 SSM get_parameter 的各種回應：
  - 正常回傳值
  - 空字串值
  - ClientError(ParameterNotFound) → WARNING 級日誌含參數全名
  - 一般例外 → ERROR 級日誌含參數全名
- name 參數涵蓋 "admin-token" 與 "live-token"。
全部離線執行，不打真實 AWS。
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from botocore.exceptions import ClientError

from trustforge import ssm_params


class FakeSSMClient:
    """模擬 boto3 SSM client，可設定 get_parameter 的回傳值或副作用。"""

    def __init__(
        self,
        return_value: dict[str, Any] | None = None,
        side_effect: BaseException | None = None,
    ) -> None:
        self._return_value = return_value
        self._side_effect = side_effect
        self.calls: list[dict[str, Any]] = []

    def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._side_effect is not None:
            raise self._side_effect
        if self._return_value is None:
            return {}
        return self._return_value


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    """每個測試前後清空環境變數與模組級 client，避免跨測試污染。"""
    monkeypatch.delenv("TRUSTFORGE_TOKEN_SSM_PREFIX", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    ssm_params.set_client_for_tests(None)
    yield
    ssm_params.set_client_for_tests(None)


def _make_client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "GetParameter",
    )


def test_no_prefix_returns_none_and_no_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """旗標未設時，回傳 None 且完全不應呼叫 boto3.client。"""

    def _boto3_should_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("不該呼叫 boto3.client")

    monkeypatch.setattr("boto3.client", _boto3_should_not_be_called)
    # 不注入 fake client，驗證真的不會走到 _get_or_create_client

    assert ssm_params.get_runtime_token("admin-token") is None
    assert ssm_params.get_runtime_token("live-token") is None


def test_empty_prefix_returns_none_and_no_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """旗標為空字串時，視同未設，仍不該呼叫 boto3.client。"""

    def _boto3_should_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("不該呼叫 boto3.client")

    monkeypatch.setattr("boto3.client", _boto3_should_not_be_called)
    monkeypatch.setenv("TRUSTFORGE_TOKEN_SSM_PREFIX", "")

    assert ssm_params.get_runtime_token("admin-token") is None
    assert ssm_params.get_runtime_token("live-token") is None


@pytest.mark.parametrize("name", ["admin-token", "live-token"])
def test_prefix_set_returns_value(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """旗標有設且 mock client 回傳正常值 → 回傳該字串，且呼叫參數正確。"""
    prefix = "/trustforge/runtime"
    token_value = f"secret-{name}-value"

    fake = FakeSSMClient(
        return_value={"Parameter": {"Name": f"{prefix}/{name}", "Value": token_value}}
    )
    ssm_params.set_client_for_tests(fake)
    monkeypatch.setenv("TRUSTFORGE_TOKEN_SSM_PREFIX", prefix)

    result = ssm_params.get_runtime_token(name)
    assert result == token_value

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["Name"] == f"{prefix}/{name}"
    assert call["WithDecryption"] is True


@pytest.mark.parametrize("name", ["admin-token", "live-token"])
def test_prefix_set_empty_value_returns_none(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """旗標有設但 mock client 回傳空字串值 → 回傳 None。"""
    prefix = "/trustforge/runtime"

    fake = FakeSSMClient(
        return_value={"Parameter": {"Name": f"{prefix}/{name}", "Value": ""}}
    )
    ssm_params.set_client_for_tests(fake)
    monkeypatch.setenv("TRUSTFORGE_TOKEN_SSM_PREFIX", prefix)

    assert ssm_params.get_runtime_token(name) is None


@pytest.mark.parametrize("name", ["admin-token", "live-token"])
def test_prefix_set_parameter_not_found_returns_none_and_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    name: str,
) -> None:
    """旗標有設但 SSM 拋 ParameterNotFound → 回傳 None，且 WARNING 日誌含參數全名。

    因為例外在取得值之前就拋出，日誌中不會有 token 值，重點是訊息格式正確。
    """
    prefix = "/trustforge/runtime"
    full_name = f"{prefix}/{name}"

    fake = FakeSSMClient(side_effect=_make_client_error("ParameterNotFound", "not here"))
    ssm_params.set_client_for_tests(fake)
    monkeypatch.setenv("TRUSTFORGE_TOKEN_SSM_PREFIX", prefix)

    with caplog.at_level(logging.WARNING, logger="trustforge.ssm_params"):
        result = ssm_params.get_runtime_token(name)

    assert result is None

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "應至少有一筆 WARNING 級日誌"
    assert any(full_name in r.getMessage() for r in warnings), (
        f"WARNING 日誌應含參數全名 {full_name}"
    )


@pytest.mark.parametrize("name", ["admin-token", "live-token"])
def test_prefix_set_generic_exception_returns_none_and_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    name: str,
) -> None:
    """旗標有設但 SSM 拋一般例外 → 回傳 None，且 ERROR 日誌含參數全名。"""
    prefix = "/trustforge/runtime"
    full_name = f"{prefix}/{name}"

    fake = FakeSSMClient(side_effect=RuntimeError("network down"))
    ssm_params.set_client_for_tests(fake)
    monkeypatch.setenv("TRUSTFORGE_TOKEN_SSM_PREFIX", prefix)

    with caplog.at_level(logging.ERROR, logger="trustforge.ssm_params"):
        result = ssm_params.get_runtime_token(name)

    assert result is None

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "應至少有一筆 ERROR 級日誌"
    assert any(full_name in r.getMessage() for r in errors), (
        f"ERROR 日誌應含參數全名 {full_name}"
    )
