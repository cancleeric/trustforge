"""#91 CI 安全網：鎖定 Bedrock 呼叫的「有界 timeout + 不重試」安全線。

背景：`BedrockClient._runtime()`（主敘事）與 `_stance_runtime()`（stance 分類）
必須帶明確的 botocore `Config`——若有人不小心把 `Config` 拔掉、或把 timeout
改成 `None`/0、或把 `retries` 從 `total_max_attempts=1` 改成會重試（甚至用
`max_attempts=1` 這種「初始 + 1 次重試 = 2 次」的語意），單次呼叫的最壞牆鐘
時間就失去上界（boto3 預設等於無限期等待），或重試把等待/花費翻倍：
  - 敘事：leader thread 可能永久卡住（#51 的 dedup stale-leader 事故根因）；
  - stance：scoring.py 的 O(n²) 迴圈中單一慢呼叫就能吃光官方 15 分鐘窗口；
  - 兩者都會**多燒 credit**（重試 = 多打一次真 API）。

本檔是這條安全線的回歸測試：純本地 monkeypatch `boto3.client`，斷言真正建
client 時傳入的 `config` 帶預期的有界 timeout 與 `total_max_attempts=1`。
**不連真 AWS、不花任何 credit。** 若安全線被誤移除/放寬，這裡會立刻變紅。

⚠️ 若日後有意調整 timeout 數字（例如現場實測後放寬），請同步更新這裡的斷言——
本測試刻意把值鎖到 `bedrock.py` 的模組常數上，讓「數字改了但沒人意識到」無所遁形。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trustforge import bedrock as bedrock_module
from trustforge.bedrock import BedrockClient, BedrockConfig


class _FakeBoto3Module:
    """假 boto3：擷取每次 `client(...)` 的 config，回傳 MagicMock，不連真 AWS。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def client(self, service_name, region_name=None, config=None):
        self.calls.append(
            {"service_name": service_name, "region_name": region_name, "config": config}
        )
        return MagicMock()


@pytest.fixture
def fake_boto3(monkeypatch):
    fake = _FakeBoto3Module()
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake)
    return fake


def _last_config(fake: _FakeBoto3Module):
    assert fake.calls, "應至少建立一次 boto3 client"
    return fake.calls[-1]["config"]


# ── 主敘事 runtime（complete()）──────────────────────────────────────────────

def test_narrative_runtime_passes_explicit_config(fake_boto3):
    """回歸：`_runtime()` 必須傳入明確 Config，不能沿用 boto3 預設（=無限期等待）。"""
    client = BedrockClient(offline=False, stance_offline=True)
    client._runtime()

    call = fake_boto3.calls[-1]
    assert call["service_name"] == "bedrock-runtime"
    cfg = call["config"]
    assert cfg is not None, "拔掉 Config → boto3 預設無限期等待，credit/time 失控"


def test_narrative_runtime_has_bounded_read_and_connect_timeout(fake_boto3):
    """敘事 client 的 read/connect timeout 必須是有限正數（有牆鐘上界）。"""
    BedrockClient(offline=False, stance_offline=True)._runtime()
    cfg = _last_config(fake_boto3)

    assert cfg.read_timeout is not None and cfg.read_timeout > 0
    assert cfg.connect_timeout is not None and cfg.connect_timeout > 0
    # 鎖到模組常數，避免「值被改了但沒人意識到」
    assert cfg.read_timeout == bedrock_module._NARRATIVE_READ_TIMEOUT_SEC
    assert cfg.connect_timeout == bedrock_module._NARRATIVE_CONNECT_TIMEOUT_SEC


def test_narrative_runtime_does_not_retry(fake_boto3):
    """credit 安全線：敘事 client 必須用 `total_max_attempts=1`（只打一次、不重試），
    不能改用會重試的設定（重試 = 多打真 API = 多燒 credit + 最壞耗時翻倍）。"""
    BedrockClient(offline=False, stance_offline=True)._runtime()
    cfg = _last_config(fake_boto3)

    assert cfg.retries == {"total_max_attempts": 1}
    # 明確防呆：不可退化成 `max_attempts`（語意是「初始 + N 次重試」，=1 其實是 2 次）
    assert "max_attempts" not in cfg.retries


# ── stance runtime（classify_stance）────────────────────────────────────────

def test_stance_runtime_passes_explicit_config(fake_boto3):
    """回歸：`_stance_runtime()` 必須傳入明確 Config。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False, stance_offline=False)
    client._stance_runtime()

    call = fake_boto3.calls[-1]
    assert call["service_name"] == "bedrock-runtime"
    assert call["config"] is not None


def test_stance_runtime_has_bounded_read_and_connect_timeout(fake_boto3):
    """stance client 的 read/connect timeout 必須是有限正數。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    BedrockClient(config=config, offline=False, stance_offline=False)._stance_runtime()
    cfg = _last_config(fake_boto3)

    assert cfg.read_timeout is not None and cfg.read_timeout > 0
    assert cfg.connect_timeout is not None and cfg.connect_timeout > 0
    assert cfg.read_timeout == bedrock_module._STANCE_READ_TIMEOUT_SEC
    assert cfg.connect_timeout == bedrock_module._STANCE_CONNECT_TIMEOUT_SEC


def test_stance_runtime_does_not_retry(fake_boto3):
    """credit 安全線：stance client 同樣必須 `total_max_attempts=1`（不重試）——
    stance 在 O(n²) 迴圈深處被高頻呼叫，重試放大效應更嚴重。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    BedrockClient(config=config, offline=False, stance_offline=False)._stance_runtime()
    cfg = _last_config(fake_boto3)

    assert cfg.retries == {"total_max_attempts": 1}
    assert "max_attempts" not in cfg.retries


# ── 跨兩者的整體不變式 ────────────────────────────────────────────────────

def test_module_timeout_constants_are_positive_ints():
    """所有 timeout 常數必須是正整數（不是 None/0）——即真的有上界。"""
    for name in (
        "_NARRATIVE_READ_TIMEOUT_SEC",
        "_NARRATIVE_CONNECT_TIMEOUT_SEC",
        "_STANCE_READ_TIMEOUT_SEC",
        "_STANCE_CONNECT_TIMEOUT_SEC",
    ):
        val = getattr(bedrock_module, name)
        assert isinstance(val, int), f"{name} 應為 int，實得 {type(val).__name__}"
        assert val > 0, f"{name} 必須 > 0（有界），實得 {val}"


def test_stance_read_timeout_not_looser_than_narrative():
    """stance 是高頻小任務，其 read timeout 不應比敘事更寬鬆（守住 15 分鐘窗口預算）。"""
    assert (
        bedrock_module._STANCE_READ_TIMEOUT_SEC
        <= bedrock_module._NARRATIVE_READ_TIMEOUT_SEC
    )


def test_runtime_client_is_cached_not_rebuilt_each_call(fake_boto3):
    """回歸：`_runtime()` 建好後應快取重用，不每次呼叫都重建 client
    （重建無害但代表快取被破壞；也避免多打不必要的 client 初始化）。"""
    client = BedrockClient(offline=False, stance_offline=True)
    first = client._runtime()
    calls_after_first = len(fake_boto3.calls)
    second = client._runtime()
    assert first is second
    assert len(fake_boto3.calls) == calls_after_first, "第二次呼叫不應再建新 client"
