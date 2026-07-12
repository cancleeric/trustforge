"""DynamoDB 400KB item 上限邊界測試（issue #110 技術債的一部分）。

`DynamoDBCache.set` 寫入的 item 一旦超過 DynamoDB 單一 item 400KB 上限，
真實 DynamoDB 會回 `ValidationException`（"Item size has exceeded the
maximum allowed size"）。本測試用一顆「嚴格執行 400KB 上限」的假
DynamoDB table，鎖住邊界行為：

- item 序列化後**恰為** 400*1024 位元組 → `set` 成功（未被 DynamoDB 拒絕）。
- item 序列化後為 400*1024 + 1 位元組 → `set` 直接把 `ValidationException`
  往上拋（不靜默吞掉、不偷偷截斷、不讓 request 誤以為寫入成功）。

這確保「超大 item」這條失敗路徑是真實可觀測的——未來若有人在 `set` 路徑
加 try/except 把 `ValidationException` 吃掉（導致快取寫入靜默失敗、下游以為
有鮮度資料），這個測試會紅。
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from trustforge.ingestion.cache import DynamoDBCache

_DYNAMODB_ITEM_LIMIT_BYTES = 400 * 1024


def _make_item(docs, fetched_at: float = 1000.0, ttl: float = 300.0,
               source_id: str = "coindesk", coin: str = "BTC") -> dict:
    """逐字複製 `DynamoDBCache.set` 內部組出的 item 結構，供計算大小與斷言。"""
    return {
        "source_id": source_id,
        "coin": coin,
        "docs_json": json.dumps(docs, ensure_ascii=False),
        "fetched_at": Decimal(str(fetched_at)),
        "ttl": int(fetched_at + ttl),
    }


def _item_bytes(item: dict) -> int:
    """近似 DynamoDB 官方 item 大小計算（属性名 + 值 UTF-8 位元組總和）。

    真實 DynamoDB 的計算對 String 取 UTF-8 位元組數、對 Number 取最多 21
    位元組；這裡對兩者都取 `str(value)` 的 UTF-8 長度，足以精確鎖住邊界
    （我們只關心「恰好 400KB」與「400KB+1」的相對差 1 位元組，絕對公式誤差
    不影響邊界斷言）。"""
    total = 0
    for key, value in item.items():
        total += len(key.encode("utf-8"))
        if isinstance(value, Decimal):
            total += len(str(value).encode("utf-8"))
        else:
            total += len(str(value).encode("utf-8"))
    return total


def _find_pad_len_for_bytes(target: int) -> int:
    """找一個 ascii padding 長度 n，使 `set` 寫出的 item 恰好為 target 位元組。

    每多一個 ascii 字元，docs_json 多 1 位元組（ascii 'x' 永不觸發 JSON
    跳脫），故 item 總大小對 n 嚴格線性 +1——直接閉式解出 n，避免逐字搜尋
    在大 padding 下爆量（400KB 會跑 40 萬次 400KB 字串建構）。"""
    base = _item_bytes(_make_item([{"pad": ""}]))
    n = target - base
    if n < 0:
        raise AssertionError(f"target {target} 小於 base {base}，無法命中")
    # 防呆：確認真的恰好命中（線性關係不被 JSON 跳脫破壞）
    assert _item_bytes(_make_item([{"pad": "x" * n}])) == target
    return n


class _FakeDynamoDBTable400KBLimit:
    """極簡假 DynamoDB Table，唯一職責：嚴格執行 400KB item 上限。

    只實作 `DynamoDBCache.set` 實際會送出的 `put_item` 呼叫形狀（外加
    `get_item` 供斷言命中）。`put_item` 在 item 超過上限時拋出與真實
    DynamoDB 同款的 `ValidationException`。"""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict] = {}

    def put_item(self, *, Item, **kwargs):
        from botocore.exceptions import ClientError

        if _item_bytes(Item) > _DYNAMODB_ITEM_LIMIT_BYTES:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ValidationException",
                        "Message": "Item size has exceeded the maximum allowed size (400 KB)",
                    }
                },
                "PutItem",
            )
        self.items[(Item["source_id"], Item["coin"])] = Item

    def get_item(self, *, Key, **kwargs):
        item = self.items.get((Key["source_id"], Key["coin"]))
        return {"Item": item} if item is not None else {}


def test_dynamodb_400kb_boundary_set_accepted_at_exactly_limit():
    cache = DynamoDBCache(table_name="trustforge-connector-cache", region="ap-southeast-2")
    cache._table = _FakeDynamoDBTable400KBLimit()

    n = _find_pad_len_for_bytes(_DYNAMODB_ITEM_LIMIT_BYTES)
    # 防呆：確認我們真的算到「恰好 400KB」
    assert _item_bytes(_make_item([{"pad": "x" * n}])) == _DYNAMODB_ITEM_LIMIT_BYTES

    cache.set("coindesk:BTC", [{"pad": "x" * n}], 1000.0)
    assert ("coindesk", "BTC") in cache._table.items


def test_dynamodb_400kb_boundary_set_rejected_one_byte_over_limit():
    cache = DynamoDBCache(table_name="trustforge-connector-cache", region="ap-southeast-2")
    cache._table = _FakeDynamoDBTable400KBLimit()

    n = _find_pad_len_for_bytes(_DYNAMODB_ITEM_LIMIT_BYTES) + 1
    assert _item_bytes(_make_item([{"pad": "x" * n}])) == _DYNAMODB_ITEM_LIMIT_BYTES + 1

    with pytest.raises(Exception) as exc_info:
        cache.set("coindesk:BTC", [{"pad": "x" * n}], 1000.0)
    # 必須是 DynamoDB 同款的 ValidationException，且「沒有」被靜默吞掉
    assert "ValidationException" in str(exc_info.value)
    # 超限 item 絕對不能被寫入
    assert ("coindesk", "BTC") not in cache._table.items
