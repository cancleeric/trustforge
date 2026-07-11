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
            if isinstance(self._side_effect, list):
                exc_or_val = self._side_effect.pop(0)
            else:
                exc_or_val = self._side_effect
            if isinstance(exc_or_val, BaseException):
                raise exc_or_val
            return exc_or_val
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


# ---------------------------------------------------------------------------
# #121 follow-up：IAM 傳播重試 / sweep / token 不回吐
# ---------------------------------------------------------------------------


def test_iam_propagation_retry_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """實例角色剛建立、IAM 傳播尚未收斂 → 第一次 `AccessDeniedException`，退避
    重試後第二次成功讀到 token（#121 IAM 傳播重試）。"""
    prefix = "/trustforge/runtime"
    denied = _make_client_error("AccessDeniedException", "propagating")
    fake = FakeSSMClient(
        side_effect=[denied, {"Parameter": {"Name": f"{prefix}/admin-token", "Value": "tok-val"}}]
    )
    ssm_params.set_client_for_tests(fake)
    monkeypatch.setenv("TRUSTFORGE_TOKEN_SSM_PREFIX", prefix)

    result = ssm_params.get_runtime_token("admin-token", max_attempts=3, backoff_base=0)
    assert result == "tok-val"
    assert len(fake.calls) == 2


def test_throttling_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ThrottlingException` 同屬暫時性錯誤，應退避重試。"""
    prefix = "/trustforge/runtime"
    throttled = _make_client_error("ThrottlingException", "slow down")
    fake = FakeSSMClient(
        side_effect=[throttled, {"Parameter": {"Name": f"{prefix}/live-token", "Value": "lt"}}]
    )
    ssm_params.set_client_for_tests(fake)
    monkeypatch.setenv("TRUSTFORGE_TOKEN_SSM_PREFIX", prefix)

    assert ssm_params.get_runtime_token("live-token", max_attempts=3, backoff_base=0) == "lt"
    assert len(fake.calls) == 2


def test_iam_propagation_retry_exhausted_returns_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """重試耗盡（仍拿不到）視同失敗回 None，不 raise。"""
    prefix = "/trustforge/runtime"
    denied = _make_client_error("AccessDeniedException", "still propagating")
    fake = FakeSSMClient(side_effect=[denied, denied, denied])
    ssm_params.set_client_for_tests(fake)
    monkeypatch.setenv("TRUSTFORGE_TOKEN_SSM_PREFIX", prefix)

    with caplog.at_level(logging.WARNING, logger="trustforge.ssm_params"):
        result = ssm_params.get_runtime_token("admin-token", max_attempts=3, backoff_base=0)
    assert result is None
    assert len(fake.calls) == 3
    assert any("重試耗盡" in r.getMessage() for r in caplog.records)


def test_parameter_not_found_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ParameterNotFound` 是非暫時性錯誤，不應重試，直接回 None。"""
    prefix = "/trustforge/runtime"
    fake = FakeSSMClient(side_effect=_make_client_error("ParameterNotFound", "nope"))
    ssm_params.set_client_for_tests(fake)
    monkeypatch.setenv("TRUSTFORGE_TOKEN_SSM_PREFIX", prefix)

    assert ssm_params.get_runtime_token("admin-token", max_attempts=3, backoff_base=0) is None
    assert len(fake.calls) == 1


def test_token_value_never_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#121：runtime token 絕不回吐——讀取成功時，token 值不得出現在任何 log
    訊息中（含例外）。"""
    prefix = "/trustforge/runtime"
    secret = "SUPERSECRET1234567890abcdef"
    fake = FakeSSMClient(
        return_value={"Parameter": {"Name": f"{prefix}/admin-token", "Value": secret}}
    )
    ssm_params.set_client_for_tests(fake)
    monkeypatch.setenv("TRUSTFORGE_TOKEN_SSM_PREFIX", prefix)

    with caplog.at_level(logging.DEBUG, logger="trustforge.ssm_params"):
        result = ssm_params.get_runtime_token("admin-token")
    assert result == secret
    assert secret not in caplog.text


class FakeSweepClient:
    """模擬 SSM client 的 `describe_parameters` / `delete_parameter`（只涵蓋
    `sweep_deploy_parameters` 實際會呼叫的形狀）。"""

    def __init__(self) -> None:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        now = _dt.now(_tz.utc)
        self._params = [
            {"Name": "/trustforge/deploy/fresh", "LastModifiedDate": now - _td(seconds=10)},
            {"Name": "/trustforge/deploy/stale", "LastModifiedDate": now - _td(seconds=7200)},
            # 常駐參數不在 deploy 路徑下，sweep 不該碰
            {"Name": "/trustforge/runtime/admin-token", "LastModifiedDate": now - _td(seconds=99999)},
        ]
        self.deleted: list[str] = []

    def describe_parameters(self, **kwargs) -> dict:
        # 模擬 SSM Path 過濾：只回傳名稱以指定 prefix 開頭的參數（真實 AWS
        # `describe_parameters` 的 `ParameterFilters` Path 行為）。
        prefix = ""
        for f in kwargs.get("ParameterFilters", []) or []:
            if f.get("Key") == "Path":
                prefix = f.get("Values", [[""]])[0] if isinstance(f.get("Values"), list) else ""
                if isinstance(f.get("Values"), list) and f["Values"]:
                    prefix = f["Values"][0]
        return {
            "Parameters": [p for p in self._params if p["Name"].startswith(prefix)]
        }

    def delete_parameter(self, **kwargs) -> None:
        self.deleted.append(kwargs["Name"])


def test_sweep_deletes_only_expired_deploy_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sweep 只刪除 `deploy/*` 路徑下、超過時間窗的殘留參數；常駐參數與尚未
    過期的參數不動。"""
    fake = FakeSweepClient()
    ssm_params.set_client_for_tests(fake)

    deleted = ssm_params.sweep_deploy_parameters(
        "/trustforge/deploy", max_age_seconds=3600.0
    )
    assert deleted == ["/trustforge/deploy/stale"]
    assert "/trustforge/runtime/admin-token" not in fake.deleted


class FakePagedSweepClient:
    """模擬 `describe_parameters` 分多頁回傳（#121.6 NextToken 迴圈）。

    第一頁回 2 筆 + NextToken，第二頁回剩餘 2 筆（其中含過期項）。
    """

    def __init__(self) -> None:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        now = _dt.now(_tz.utc)
        self._pages = [
            (
                [
                    {"Name": "/trustforge/deploy/a", "LastModifiedDate": now - _td(seconds=10)},
                    {"Name": "/trustforge/deploy/b", "LastModifiedDate": now - _td(seconds=7200)},
                ],
                "tok2",
            ),
            (
                [
                    {"Name": "/trustforge/deploy/c", "LastModifiedDate": now - _td(seconds=20)},
                    {"Name": "/trustforge/deploy/d", "LastModifiedDate": now - _td(seconds=9000)},
                ],
                None,
            ),
        ]
        self.calls = 0
        self.deleted: list[str] = []

    def describe_parameters(self, **kwargs) -> dict:
        page = self._pages[self.calls]
        if self.calls == 1:
            # 第二頁必須帶上第一頁回傳的 NextToken（驗證迴圈有收斂地跟頁）
            assert kwargs.get("NextToken") == "tok2", kwargs
        self.calls += 1
        return {"Parameters": page[0], "NextToken": page[1]}

    def delete_parameter(self, **kwargs) -> None:
        self.deleted.append(kwargs["Name"])


def test_sweep_paginates_next_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """#121.6：describe_parameters 多頁時必須跟 NextToken 分頁，所有頁的過期
    殘留參數都應被清掉（不漏清後面幾頁）。"""
    fake = FakePagedSweepClient()
    ssm_params.set_client_for_tests(fake)

    deleted = ssm_params.sweep_deploy_parameters("/trustforge/deploy", max_age_seconds=3600.0)
    # 兩頁都列舉過（NextToken 迴圈生效）
    assert fake.calls == 2
    # 過期的 b / d 都被清掉
    assert set(deleted) == {"/trustforge/deploy/b", "/trustforge/deploy/d"}
