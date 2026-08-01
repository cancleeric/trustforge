from __future__ import annotations

from dataclasses import dataclass

from trustforge import lambda_provider_cache
from trustforge.ingestion.base import Document


@dataclass
class _Result:
    ok: bool = True


class _Source:
    def __init__(self, name, *, docs=None, error=None):
        self.name = name
        self.docs = docs or []
        self.error = error
        self.calls = []

    def fetch(self, query, coin=""):
        self.calls.append((query, coin))
        if self.error:
            raise self.error
        return self.docs


def _doc(source):
    return Document(
        id=f"{source}-1", kind="price_live", source=source,
        text="evidence", url="https://example.com/evidence", ts=1_800_000_000.0,
    )


def test_refresh_fetches_and_writes_all_four_sources(monkeypatch):
    sources = [_Source(name, docs=[_doc(name)]) for name in sorted(lambda_provider_cache._PROVIDER_NAMES)]
    writes = []
    monkeypatch.setattr(lambda_provider_cache, "_sources", lambda: sources)
    monkeypatch.setattr(lambda_provider_cache, "get_cache_backend", lambda: object())
    monkeypatch.setattr(lambda_provider_cache, "cache_get", lambda *args: None)
    monkeypatch.setattr(
        lambda_provider_cache,
        "cache_set",
        lambda backend, key, docs, **kwargs: writes.append((key, docs)) or _Result(),
    )

    result = lambda_provider_cache.refresh_provider_cache("btc")

    assert set(result) == lambda_provider_cache._PROVIDER_NAMES
    assert all(status == "refreshed" and count == 1 for status, count in result.values())
    assert all(source.calls == [("", "BTC")] for source in sources)
    assert len(writes) == 4


def test_refresh_failure_never_exposes_exception_text(monkeypatch):
    secret = "must-not-leak"
    source = _Source("whale-alert", error=RuntimeError(f"url?api_key={secret}"))
    monkeypatch.setattr(lambda_provider_cache, "_sources", lambda: [source])
    monkeypatch.setattr(lambda_provider_cache, "get_cache_backend", lambda: object())
    monkeypatch.setattr(lambda_provider_cache, "cache_get", lambda *args: None)

    result = lambda_provider_cache.refresh_provider_cache("ETH")

    assert result == {"whale-alert": ("failed:RuntimeError", 0)}
    assert secret not in repr(result)


def test_fresh_entry_skips_provider_call(monkeypatch):
    source = _Source("arkham-intel", docs=[_doc("arkham-intel")])
    monkeypatch.setattr(lambda_provider_cache, "_sources", lambda: [source])
    monkeypatch.setattr(lambda_provider_cache, "get_cache_backend", lambda: object())
    monkeypatch.setattr(
        lambda_provider_cache,
        "cache_get",
        lambda *args: {"fetched_at": lambda_provider_cache.time.time(), "docs": [{"id": "1"}]},
    )

    assert lambda_provider_cache.refresh_provider_cache("BTC") == {
        "arkham-intel": ("cached", 1)
    }
    assert source.calls == []
