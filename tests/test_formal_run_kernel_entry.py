"""Formal pipeline entrypoint Trust Kernel regression (#420)."""

from __future__ import annotations

from trustforge.agent import orchestrator as orch
from trustforge.ingestion.base import Document
from trustforge.pipeline import run
from trustforge.schema import QuestionType
from trustforge.trust.kernel import KernelOutput


def test_formal_pipeline_run_enters_trust_kernel(monkeypatch):
    """The public formal run path must pass normalized claims through run_kernel()."""

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return [
            Document(
                id="formal-price",
                kind="price",
                source="hoya-ohlcv",
                text=f"{coin} Daily OHLCV close rose from 45000 to 46600.",
                ts=1_700_000_000.0,
                meta={"coin": coin, "close": 46600.0},
            ),
            Document(
                id="formal-news",
                kind="news",
                source="coindesk",
                text=f"Analysts say {coin} spot demand improved after ETF inflows expanded.",
                ts=1_700_000_100.0,
                meta={"coin": coin},
            ),
        ]

    seen: dict[str, object] = {}
    real_run_kernel = orch.run_kernel

    def spy_run_kernel(inp):
        seen["coin"] = inp.coin
        seen["query"] = inp.query
        seen["claims"] = len(inp.claims)
        out = real_run_kernel(inp)
        assert isinstance(out, KernelOutput)
        return out

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)
    monkeypatch.setattr(orch, "run_kernel", spy_run_kernel)

    report, evidence, log = run(
        "BTC",
        "formal BTC multi-source analysis",
        QuestionType.MULTI_SOURCE,
        offline=True,
    )

    assert report.coin == "BTC"
    assert evidence
    assert seen["coin"] == "BTC"
    assert seen["query"] == "formal BTC multi-source analysis"
    assert seen["claims"] > 0
    assert any(
        event.get("tool") == "judgment.derive"
        and "kernel_confidence" in event.get("params", {})
        for event in log.events
    )
