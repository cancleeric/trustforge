"""Tests for RateLimitStore using moto to simulate real DynamoDB grammar and semantics.

vp-eng review Suggestion: use moto to simulate real DynamoDB grammar/semantics,
verify production rate_limit_store.py's boto3 calls are actually legal, not just
passing a hand-rolled fake table which could falsely accept invalid grammar. Moto
is pure Python and requires no Docker/network, so it can run directly in CI.

範圍限定說明：這個檔案只驗證 boto3 呼叫語法（UpdateExpression /
ConditionExpression / ExpressionAttributeNames / ExpressionAttributeValues）是
DynamoDB 真的接受的合法語法，**不含**多執行緒併發原子性斷言——實測發現 moto
的 DynamoDB backend 沒有內部鎖（檢查過 moto/dynamodb/models/__init__.py 原始碼
確認），對同一個 item 的 `update_item` 沒有序列化保證，用 50-thread 直打會出現
真實的 over-admission race（true_count 落在 14～32 之間而非精確等於
max_requests），這是 moto 本身的限制，不是 production code 的 bug。併發原子性
驗證改用 `tests/test_rate_limit_store.py` 裡有鎖的 `_FakeDynamoDBTable`（見
`test_concurrent_atomicity_no_fail_open`），該處才是可靠、非 flaky 的原子性
測試。
"""
import os
import time
import boto3
import pytest
from trustforge.rate_limit_store import RateLimitStore

# `moto` 是 dev-only 依賴（pyproject.toml `[project.optional-dependencies].dev`）。
# 用 `importorskip` 而非 module-level `from moto import ...`：CI 走
# `pip install -e ".[dev]"` 一定會裝到，但本機若只裝了最小依賴集（沒跑過
# `.[dev]` 安裝），module-level import 失敗會讓整個 pytest collection 中斷、
# 拖垮其他所有測試檔——改用 importorskip 讓「沒裝 moto」時只跳過本檔，不影響
# 其餘套件執行。
mock_aws = pytest.importorskip("moto").mock_aws

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

TABLE_NAME = "trustforge-connector-cache"

@pytest.fixture
def dynamodb_table():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
            AttributeDefinitions=[
                {"AttributeName": "source_id", "AttributeType": "S"},
                {"AttributeName": "coin", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "source_id", "KeyType": "HASH"},
                {"AttributeName": "coin", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        client = boto3.client("dynamodb", region_name="us-east-1")
        while True:
            response = client.describe_table(TableName=TABLE_NAME)
            if response["Table"]["TableStatus"] == "ACTIVE":
                break
            time.sleep(0.1)

        yield table

@pytest.fixture
def store():
    return RateLimitStore(table_name=TABLE_NAME, region="us-east-1")

def test_moto_smoke_first_request_allowed(dynamodb_table, store):
    result = store.try_increment("bucket1", "key1", window_seconds=60, max_requests=5, now=1000.0)
    assert result is True

def test_moto_smoke_blocks_after_max_requests(dynamodb_table, store):
    now = 1000.0
    results = []
    for _ in range(5):
        results.append(store.try_increment("bucket2", "key2", window_seconds=60, max_requests=3, now=now))

    assert results == [True, True, True, False, False]

def test_moto_smoke_window_rollover_resets(dynamodb_table, store):
    assert store.try_increment("bucket3", "key3", window_seconds=60, max_requests=1, now=1000.0) is True
    assert store.try_increment("bucket3", "key3", window_seconds=60, max_requests=1, now=1000.0) is False
    assert store.try_increment("bucket3", "key3", window_seconds=60, max_requests=1, now=1061.0) is True

def test_moto_smoke_max_requests_zero_blocks_even_first_request(dynamodb_table, store):
    result = store.try_increment("bucket4", "key4", window_seconds=60, max_requests=0, now=1000.0)
    assert result is False
