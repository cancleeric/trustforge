"""#9 codex HIGH 追加 — unpriced model 破壞 cap，修法驗證。

背景（見 `budget_guard.py` 對應 docstring）：`request_max_cost_usd()` 是固定
保守估值（預設 $0.05），估值本身只對 `ledger.PRICING` 已登記的模型
（sonnet/haiku）成立。若 `BEDROCK_MODEL_ID`（narrative）或
`BEDROCK_HAIKU_MODEL_ID`（stance）換成不在計價表的模型，真實單價未知，
固定估值可能嚴重低估真實花費，讓「原子預留」修好的並行 TOCTOU 又以另一種
方式讓 $3/day cap 名存實亡。修法：narrative／stance 各自獨立檢查是否已
計價，未計價一律 fail-closed（不放行、強制該段落離線 abstain，誠實
degrade，見 #24）。

⛔ 全程不打真 AWS/Bedrock：
- 單元層測試（第 1 節）直接測 `model_is_priced`/`narrative_model_priced`/
  `stance_model_priced`/`warn_if_bedrock_model_unpriced`，純字串比對 +
  log 斷言，無網路呼叫。
- 整合層測試（第 2 節）沿用 `test_budget_guard_concurrency.py` 的
  `_fake_collect`/單一 news 來源慣例（避免觸發 `classify_stance`）。narrative
  「已計價、真的放行」的情境**不能**沿用「wrap 真 BedrockClient + 不設
  BEDROCK_MODEL_ID 讓 complete() 提早丟 RuntimeError」這招——因為這裡故意
  設定一個**已計價**的真實 model id，若真的用真 `BedrockClient`，
  `offline=False` 會讓 `complete()` 跑到 `self._runtime().invoke_model()`
  （真的碰 boto3/AWS）。因此这些情境改用完全不 wrap 真類別的
  `_StubBedrockClient`（見下方），無論 `offline` 傳什麼值都只做記憶體內
  fallback，100% 不碰網路，只驗證 `pipeline.run()` 傳給建構子的
  `offline`/`stance_offline` 旗標本身正確。
"""
from __future__ import annotations

import logging
import os

import pytest

import trustforge.budget_guard as bg
import trustforge.pipeline as pl
from trustforge.bedrock import LLMResult
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType

_PRICED_NARRATIVE_MODEL = "apac.anthropic.claude-sonnet-4-6"
_PRICED_STANCE_MODEL = "au.anthropic.claude-haiku-4-5-20251001-v1:0"
_UNPRICED_MODEL = "some-unknown-future-model-id"


# ---------------------------------------------------------------------------
# 1) 單元層：model_is_priced / narrative_model_priced / stance_model_priced /
#    warn_if_bedrock_model_unpriced
# ---------------------------------------------------------------------------


def test_model_is_priced_known_model_true():
    assert bg.model_is_priced(_PRICED_NARRATIVE_MODEL) is True
    assert bg.model_is_priced(_PRICED_STANCE_MODEL) is True


def test_model_is_priced_unknown_none_empty_false():
    assert bg.model_is_priced(_UNPRICED_MODEL) is False
    assert bg.model_is_priced(None) is False
    assert bg.model_is_priced("") is False


def test_narrative_model_priced_reads_bedrock_model_id_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_NARRATIVE_MODEL)
    assert bg.narrative_model_priced() is True

    monkeypatch.setenv("BEDROCK_MODEL_ID", _UNPRICED_MODEL)
    assert bg.narrative_model_priced() is False

    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    assert bg.narrative_model_priced() is False  # 未設定 = unpriced，fail-closed


def test_stance_model_priced_defaults_to_priced_haiku_when_unset(monkeypatch):
    """`BEDROCK_HAIKU_MODEL_ID` 未設定時的預設值本身已是計價表內的
    haiku model，不應該因為「沒顯式設定」就被誤判 unpriced。"""
    monkeypatch.delenv("BEDROCK_HAIKU_MODEL_ID", raising=False)
    assert bg.stance_model_priced() is True


def test_stance_model_priced_reads_bedrock_haiku_model_id_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_HAIKU_MODEL_ID", _UNPRICED_MODEL)
    assert bg.stance_model_priced() is False

    monkeypatch.setenv("BEDROCK_HAIKU_MODEL_ID", _PRICED_STANCE_MODEL)
    assert bg.stance_model_priced() is True


def test_warn_if_bedrock_model_unpriced_logs_warning_but_does_not_crash(
    monkeypatch, caplog
):
    monkeypatch.setenv("BEDROCK_MODEL_ID", _UNPRICED_MODEL)
    with caplog.at_level(logging.WARNING, logger="trustforge.budget_guard"):
        bg.warn_if_bedrock_model_unpriced()  # 不應 raise
    assert any(_UNPRICED_MODEL in r.message for r in caplog.records)


def test_warn_if_bedrock_model_unpriced_no_warning_when_priced(monkeypatch, caplog):
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_NARRATIVE_MODEL)
    with caplog.at_level(logging.WARNING, logger="trustforge.budget_guard"):
        bg.warn_if_bedrock_model_unpriced()
    assert caplog.records == []


def test_warn_if_bedrock_model_unpriced_no_warning_when_unset(monkeypatch, caplog):
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    with caplog.at_level(logging.WARNING, logger="trustforge.budget_guard"):
        bg.warn_if_bedrock_model_unpriced()  # 未設定 = 這條 pipeline 本來就走離線，不用警告
    assert caplog.records == []


# ---------------------------------------------------------------------------
# 2) 整合層：pipeline.run() 依 narrative/stance model 是否計價 fail-closed
# ---------------------------------------------------------------------------


def _doc(id: str, kind: str, source: str, text: str = "") -> Document:
    return Document(id=id, kind=kind, source=source, text=text, ts=1_000.0)


def _make_real_docs(coin: str) -> list[Document]:
    """單一 price + 單一 news（不同來源）：跨源 stance_pairs 沒有候選配對，
    `classify_stance` 在資料層面就不會被呼叫（比照
    `test_budget_guard_concurrency.py` 慣例）。"""
    return [
        _doc(f"{coin}_p1", "price", "real-hoya-ohlcv", f"{coin} 現價 30000。"),
        _doc(f"{coin}_n1", "news", "real-coindesk-rss", f"{coin} 市場情緒正向。"),
    ]


def _fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
    return _make_real_docs(coin)


class _StubConfig:
    """輕量假 config：只提供 orchestrator 需要讀的 `.model_id` 屬性，動態
    反映當下 env（不像 `bedrock.BedrockConfig`，其 dataclass 欄位預設值
    在模組匯入當下就固定了，測試裡 monkeypatch env 不會反映到既有
    instance 上——這裡改成每次建構都重新讀 env，確保跟 `pipeline.run()`
    當下看到的設定一致）。"""

    def __init__(self):
        self.model_id = os.environ.get("BEDROCK_MODEL_ID", "")


class _StubBedrockClient:
    """完全不碰網路的假 `BedrockClient`：無論 `offline` 傳什麼值，
    `extract_claims_with_llm`/`complete` 都只做記憶體內 fallback。用於
    「narrative model 已計價、pipeline 決定真的放行」情境——這種情境下
    `offline=False`，若用真 `BedrockClient` 會實際觸發 `invoke_model()`
    （真碰 boto3），改用這個 stub 才能在驗證「旗標決策正確」的同時保證
    $0、零網路呼叫。"""

    def __init__(self, config=None, offline: bool = False, stance_offline=None):
        self.config = config or _StubConfig()
        self.offline = offline
        self.stance_offline = offline if stance_offline is None else stance_offline
        self.cost_events: list[dict] = []

    def extract_claims_with_llm(
        self, docs, log=None, *, mode=None, question=None,
    ):
        from trustforge.trust.scoring import extract_claims

        return extract_claims(docs)

    def complete(self, system: str, prompt: str) -> LLMResult:
        return LLMResult(
            text="[STUB] no real Bedrock call", input_tokens=0, output_tokens=0, model_id=None,
        )

    def classify_stance(self, a: str, b: str) -> str:
        return "neutral"  # pragma: no cover - 本測試資料無候選配對，不會被呼叫


def test_pipeline_run_unpriced_narrative_model_never_admits_bedrock(monkeypatch):
    """`llm_mode="bedrock"` + `BEDROCK_MODEL_ID` 是未計價 model → 即使今日
    預算/並行預留都放行，narrative 也必須 fail-closed 強制離線；stance
    在 `llm_mode="bedrock"` 情境本來就跟著 narrative 走，同樣被迫離線。
    degrade 說明要誠實標明「模型尚未計價」，不能跟 cap/rate_limit 混為一談。
    """
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "3.0")
    monkeypatch.setenv("BEDROCK_MODEL_ID", _UNPRICED_MODEL)
    monkeypatch.setattr(pl, "collect", _fake_collect)
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)

    captured: list[dict] = []

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return _StubBedrockClient(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    from trustforge.budget_guard import _RESERVATION

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE,
        data_mode="live", llm_mode="bedrock",
        run_scope_id="test-budget-unpriced-bedrock",
    )

    assert captured[0] == {"offline": True, "stance_offline": True}
    assert any("尚未登記計價" in s for s in report.limits)
    # 不應該誤標成 cap/rate_limit 的說明（三種 degrade 原因要能分辨）
    assert not any("已達上限" in s for s in report.limits)
    assert not any("請求過於頻繁" in s for s in report.limits)
    # 從未真的放行，預留必須完全沒有殘留佔用
    assert _RESERVATION._reserved == pytest.approx(0.0)


def test_pipeline_run_known_priced_model_admits_normally(monkeypatch):
    """已計價 model（sonnet）→ 不受這個新護欄影響，narrative 正常放行
    （`offline=False`），跟加入 unpriced-fail-closed 之前行為一致。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "3.0")
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_NARRATIVE_MODEL)
    monkeypatch.setattr(pl, "collect", _fake_collect)
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)

    captured: list[dict] = []

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return _StubBedrockClient(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    from trustforge.budget_guard import _RESERVATION

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE,
        data_mode="live", llm_mode="bedrock",
        run_scope_id="test-budget-unpriced-bedrock",
    )

    assert captured[0] == {"offline": False, "stance_offline": False}
    assert not any("尚未登記計價" in s for s in report.limits)
    # 正常放行後 reconcile 完成，預留照樣歸零（不是「沒被 unpriced 擋下」
    # 就代表沒有經過原子預留這條路徑）。
    assert _RESERVATION._reserved == pytest.approx(0.0)


def test_pipeline_run_unpriced_stance_model_only_blocks_stance_not_narrative(
    monkeypatch,
):
    """narrative model 已計價（真的放行）、但 stance model（`BEDROCK_HAIKU_
    MODEL_ID`）是未計價 model → 只擋 stance 這一段，narrative 不受影響
    仍正常放行。兩者要能各自獨立判斷，不能因為其中一個過了就連坐放行/
    連坐擋下另一個。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "3.0")
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_NARRATIVE_MODEL)
    monkeypatch.setenv("BEDROCK_HAIKU_MODEL_ID", _UNPRICED_MODEL)
    monkeypatch.setattr(pl, "collect", _fake_collect)
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)

    captured: list[dict] = []

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return _StubBedrockClient(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    from trustforge.budget_guard import _RESERVATION

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE,
        data_mode="live", llm_mode="bedrock",
        run_scope_id="test-budget-unpriced-bedrock",
    )

    assert captured[0] == {"offline": False, "stance_offline": True}
    assert any("尚未登記計價" in s for s in report.limits)
    assert _RESERVATION._reserved == pytest.approx(0.0)


def test_pipeline_run_unpriced_stance_model_blocks_online_stance_only_mode(
    monkeypatch,
):
    """真資料·$0 檔位（`llm_mode="off"`）+ online-stance 開關開著 +
    `BEDROCK_HAIKU_MODEL_ID` 是未計價 model → stance 判斷必須 fail-closed
    維持離線，不能因為開關開著、cap 也還沒達標就放行一個真實單價未知的
    model。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "3.0")
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", "1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_NARRATIVE_MODEL)  # HAS_BEDROCK 判斷需要
    monkeypatch.setenv("BEDROCK_HAIKU_MODEL_ID", _UNPRICED_MODEL)
    monkeypatch.setattr(pl, "collect", _fake_collect)
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)

    captured: list[dict] = []

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return _StubBedrockClient(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    from trustforge.budget_guard import _RESERVATION

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE,
        data_mode="live", llm_mode="off",
        run_scope_id="test-budget-unpriced-off",
    )

    assert captured[0] == {"offline": True, "stance_offline": True}
    assert any("尚未登記計價" in s for s in report.limits)
    # narrative 本來就是離線（llm_mode=off），這次也沒有任何段落真的放行
    # 過，預留必須完全歸零。
    assert _RESERVATION._reserved == pytest.approx(0.0)


def test_pipeline_run_priced_stance_model_online_stance_still_works(monkeypatch):
    """對照組：stance model 已計價（預設 haiku）時，online-stance 開關仍能
    正常放行，確認新護欄沒有連坐擋掉既有正常路徑。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "3.0")
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", "1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_NARRATIVE_MODEL)
    monkeypatch.delenv("BEDROCK_HAIKU_MODEL_ID", raising=False)  # 用預設已計價 haiku id
    monkeypatch.setattr(pl, "collect", _fake_collect)
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)

    captured: list[dict] = []

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return _StubBedrockClient(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    from trustforge.budget_guard import _RESERVATION

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE,
        data_mode="live", llm_mode="off",
        run_scope_id="test-budget-unpriced-off",
    )

    assert captured[0] == {"offline": True, "stance_offline": False}
    assert not any("尚未登記計價" in s for s in report.limits)
    assert _RESERVATION._reserved == pytest.approx(0.0)
