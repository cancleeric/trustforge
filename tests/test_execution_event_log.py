"""Generic execution event log tests (#410)."""
from __future__ import annotations

import json
from pathlib import Path

from trustforge.execution_event_log import (
    REDACTED,
    ExecutionEventLog,
    ExecutionStepRecord,
    PUBLIC_EVENT_FIELDS,
    _PUBLIC_HERMES_KEYS,
    _PUBLIC_INGESTION_SOURCE_KEYS,
    _public_params,
    _scrub_summary,
    redact_secrets,
    record_to_dict,
    to_public_event_dict,
    to_public_events,
)
from trustforge.execlog import ExecutionLog


def test_generic_event_log_serializes_legacy_jsonl_shape():
    log = ExecutionEventLog(
        run_id="run-1",
        started_at="2026-07-22T00:00:00Z",
        budget_sec=900,
    )

    log.append(
        ts="2026-07-22T00:00:01Z",
        elapsed_sec=1.234,
        tool="provider.invoke",
        params={"provider": "fake"},
        summary="ran provider",
        step=ExecutionStepRecord(step_id="provider", label="Provider", order=1),
    )

    line = json.loads(log.to_jsonl())
    assert line == {
        "ts": "2026-07-22T00:00:01Z",
        "elapsed_sec": 1.23,
        "tool": "provider.invoke",
        "params": {"provider": "fake"},
        "summary": "ran provider",
    }


def test_execution_log_keeps_manifest_and_jsonl_compatibility():
    log = ExecutionLog(now_fn=lambda: 1000.0, run_id="hermes-compat")
    log.record("provider.resolve", params={"provider": "null"})

    assert log.manifest()["run_id"] == "hermes-compat"
    assert log.manifest()["agent"] == "hermes"
    lines = [json.loads(line) for line in log.to_jsonl().splitlines()]
    assert set(lines[0]) == {"ts", "elapsed_sec", "tool", "params", "summary"}
    assert lines[-1]["params"]["provider"] == "null"
    assert lines[-1]["params"]["hermes"]["agent"] == "hermes"
    assert log.events == lines


def test_secret_redaction_is_recursive_and_key_based():
    value = {
        "api_key": "abc",
        "nested": {
            "Authorization": "Bearer token",
            "items": [{"password": "pw"}, {"safe": "ok"}],
        },
        "safe": "visible",
    }

    assert redact_secrets(value) == {
        "api_key": REDACTED,
        "nested": {
            "Authorization": REDACTED,
            "items": [{"password": REDACTED}, {"safe": "ok"}],
        },
        "safe": "visible",
    }


def test_secret_redaction_preserves_token_count_fields():
    value = {
        "tokens_in": 100,
        "tokens_out": 50,
        "nested": {"token": "secret"},
    }

    assert redact_secrets(value) == {
        "tokens_in": 100,
        "tokens_out": 50,
        "nested": {"token": REDACTED},
    }


def test_execution_log_redacts_secrets_before_jsonl_output():
    log = ExecutionLog(now_fn=lambda: 1000.0, run_id="hermes-redact")
    log.record(
        "provider.resolve",
        params={"api_key": "abc", "nested": {"token": "secret", "safe": "ok"}},
    )

    event = json.loads(log.to_jsonl().splitlines()[-1])

    assert event["params"]["api_key"] == REDACTED
    assert event["params"]["nested"]["token"] == REDACTED
    assert event["params"]["nested"]["safe"] == "ok"


def test_generic_run_record_manifest_is_json_compatible():
    log = ExecutionEventLog(
        run_id="run-2",
        started_at="2026-07-22T00:00:00Z",
        budget_sec=60,
    )
    log.append(
        ts="2026-07-22T00:00:01Z",
        elapsed_sec=1,
        tool="step",
        step=ExecutionStepRecord(step_id="s1", status="completed"),
    )

    assert record_to_dict(log.manifest()) == {
        "run_id": "run-2",
        "started_at": "2026-07-22T00:00:00Z",
        "elapsed_sec": 1.0,
        "budget_sec": 60,
        "steps": [{"step_id": "s1", "label": "", "order": 0, "status": "completed"}],
    }


def test_generic_execution_event_log_has_no_hermes_node_names():
    source = Path("src/trustforge/execution_event_log.py").read_text(encoding="utf-8").lower()

    # #943 deliberately introduces a curated, allowlisted reference to the public
    # ``params.hermes`` context key (node_id/node_label/status/...) and the
    # ``ingestion.source`` tool name so the public projection can keep the
    # frontend-execution UI fed. That is a reviewed exception — product-specific
    # node *names* (source_ingestion / trust_reasoning) still must NOT leak into
    # this provider-neutral module.
    assert "source_ingestion" not in source
    assert "trust_reasoning" not in source


# ── #943 public execution-log allowlist (deny-by-default) ──────────────────


def _sensitive_event() -> dict:
    """Mimic an ``ingestion.source`` event: hermes context (auto-added by execlog)
    + ingestion public keys + sensitive keys + one unlisted key."""
    return {
        "ts": "2026-07-31T00:00:00Z",
        "elapsed_sec": 1.5,
        "tool": "ingestion.source",
        "params": {
            # sensitive keys — must NEVER be public
            "api_key": "sk-SECRET",
            "authorization": "Bearer JWTTOKEN",
            "wallet": "0xABC",
            "url": "https://x/?token=LEAK",
            # ingestion.source public keys — projected
            "source": "coinapi",
            "kind": "ohlcv",
            "coin": "BTC",
            "duration_ms": 42,
            "document_count": 3,
            "outcome": "ok",
            "data_mode": "live",
            # unlisted key — must NEVER be public
            "internal_extra": "should-not-leak",
            # hermes context (auto-added by execlog) — projected (curated)
            "hermes": {
                "node_id": "source_ingestion",
                "node_label": "來源蒐集",
                "node_order": 1,
                "status": "completed",
                "run_id": "hermes-1",
                "agent": "hermes",
                # an extra hermes sub-key NOT in the allowlist — must drop
                "internal_hermes_field": "leak?",
            },
        },
        "summary": "auth via Bearer ABC123 then called https://x/?token=LEAK",
        "internal_trace": "stack-with-9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c",
    }


def test_public_event_dict_projects_curated_params_only():
    """#943 curated allowlist: hermes context + ingestion.source public keys pass;
    all other params (secrets, unlisted keys) are dropped."""
    public = to_public_event_dict(_sensitive_event())

    # top-level allowlist still enforced (no internal_trace, no unlisted field)
    assert "internal_trace" not in public
    assert set(public) <= PUBLIC_EVENT_FIELDS | {"params"}
    # curated hermes context survives (frontend reads node_id/status/node_label)
    assert public["params"]["hermes"]["node_id"] == "source_ingestion"
    assert public["params"]["hermes"]["status"] == "completed"
    # curated ingestion.source public keys survive (frontend source-details table)
    assert public["params"]["source"] == "coinapi"
    assert public["params"]["duration_ms"] == 42
    assert public["params"]["document_count"] == 3
    assert public["params"]["outcome"] == "ok"
    # sensitive + unlisted param keys dropped
    for bad in ("api_key", "url", "wallet", "authorization", "internal_extra"):
        assert bad not in public["params"]
    # unlisted hermes sub-key dropped (only _PUBLIC_HERMES_KEYS pass)
    assert "internal_hermes_field" not in public["params"]["hermes"]
    assert public["params"]["hermes"]["node_label"] == "來源蒐集"


def test_public_event_dict_leaks_no_secret_value_anywhere():
    public = to_public_event_dict(_sensitive_event())
    blob = json.dumps(public, ensure_ascii=False)

    # No secret value nor unlisted value from params may survive into the
    # public payload (substring scan over the whole projected output).
    for needle in (
        "sk-SECRET",
        "JWTTOKEN",
        "0xABC",
        "LEAK",
        "Bearer ABC123",
        "internal_extra",
        "should-not-leak",
        "internal_hermes_field",
        "leak?",
    ):
        assert needle not in blob, f"leaked secret/unlisted value in public event: {needle!r}"


def test_public_event_dict_scrubs_token_like_patterns_from_summary():
    public = to_public_event_dict(_sensitive_event())
    # defense-in-depth: even though summary is allowlisted, token-like values
    # inside it are scrubbed (allowlist is primary; this is the second layer).
    assert "LEAK" not in public["summary"]
    assert "ABC123" not in public["summary"]
    assert REDACTED in public["summary"]


def test_public_params_unit_projects_hermes_and_ingestion_only():
    """Direct unit cover for the curated projection rules (deny-by-default)."""
    event = {
        "tool": "ingestion.source",
        "params": {
            "api_key": "sk",
            "hermes": {"node_id": "n", "status": "ok", "secret_field": "x"},
            "source": "s",
            "duration_ms": 9,
            "rogue": "drop",
        },
    }
    pp = _public_params(event)
    assert set(pp["hermes"]) <= _PUBLIC_HERMES_KEYS
    assert pp["hermes"] == {"node_id": "n", "status": "ok"}
    # ingestion keys present are bounded by the ingestion allowlist; rogue dropped
    assert set(pp) <= {"hermes"} | _PUBLIC_INGESTION_SOURCE_KEYS
    assert pp["source"] == "s"
    assert pp["duration_ms"] == 9
    assert "api_key" not in pp and "rogue" not in pp

    # non-ingestion tool → only hermes projected, ingestion keys dropped
    only_hermes = _public_params({**event, "tool": "provider.invoke"})
    assert set(only_hermes) == {"hermes"}
    assert "source" not in only_hermes

    # missing / non-dict params → still always emits hermes (frontend reads
    # event.params.hermes unconditionally; omitting params would throw).
    assert _public_params({"tool": "x"}) == {"hermes": {}}
    assert _public_params({"tool": "x", "params": None}) == {"hermes": {}}


def test_to_public_events_projects_curated_params_per_item():
    events = [
        _sensitive_event(),
        {"ts": "t2", "elapsed_sec": 0.1, "tool": "x", "params": {"token": "hush"}},
    ]
    out = to_public_events(events)

    assert [e["ts"] for e in out] == ["2026-07-31T00:00:00Z", "t2"]
    # event 0 (ingestion.source) keeps curated params (hermes + ingestion keys)
    assert "params" in out[0]
    assert "hermes" in out[0]["params"]
    assert out[0]["params"]["source"] == "coinapi"
    # event 1 (no hermes, not ingestion.source) → params still present (always
    # emitted) with an empty hermes so the frontend never throws on a missing key.
    assert "params" in out[1]
    assert out[1]["params"]["hermes"] == {}
    # the unlisted ``token`` param value is still dropped (deny-by-default)
    assert "token" not in out[1]["params"]
    # no secret values leak anywhere across the batch
    blob = json.dumps(out, ensure_ascii=False)
    for needle in ("sk-SECRET", "JWTTOKEN", "0xABC", "LEAK", "hush"):
        assert needle not in blob
    assert all(set(e) <= PUBLIC_EVENT_FIELDS | {"params"} for e in out)


def test_scrub_summary_is_best_effort_and_misses_arbitrary_secrets():
    """#943 (Low, defense-in-depth limitation): ``_scrub_summary`` only catches
    *structured* token-like patterns (URL ``?token=``, bearer, ``header:``, long
    hex). An arbitrary prefixless secret in free-text is NOT scrubbed — which is
    exactly why the allowlist (deny-by-default) is the PRIMARY defense, not scrub.
    """
    event = {
        "ts": "t",
        "elapsed_sec": 0.1,
        "tool": "x",
        "summary": "fetched with key=sk_live_abc123 then logged pw=hunter2",
    }
    public = to_public_event_dict(event)
    # scrub did not catch the bare ``key=`` / ``pw=`` secrets (no structured marker)
    assert "sk_live_abc123" in public["summary"]
    assert "hunter2" in public["summary"]
    # ...but the allowlist still dropped all param *values* (only the empty
    # hermes context survives — deny-by-default keeps the bare secrets out).
    assert public["params"] == {"hermes": {}}


def test_internal_log_events_still_carry_params_for_cost_card():
    """#943 guard: the public serializer must NOT mutate the internal log.

    ``web.py::_render_cost_card`` and latency/model diagnostics read
    ``log.events[*]["params"]`` directly (server-side, not public); the public
    projection keeps only curated params (hermes context) and drops cost fields.
    """
    log = ExecutionLog(now_fn=lambda: 1000.0, run_id="hermes-cost")
    log.record_llm_cost("claude-x", tokens_in=10, tokens_out=5, cost_usd=0.0123)

    cost_params = [ev["params"] for ev in log.events if ev.get("tool") == "llm.cost"]
    assert cost_params and cost_params[0]["cost_usd"] == 0.0123
    assert cost_params[0]["model"] == "claude-x"

    # ...while the public projection of the same events keeps only curated params:
    # cost/model/token fields are dropped, only hermes context survives.
    public = to_public_events(log.events)
    assert all("cost_usd" not in e for e in public)
    assert all("model" not in e for e in public)
    assert all("tokens_in" not in e for e in public)
    assert all(set(e.get("params", {})) <= {"hermes"} for e in public)


def test_public_event_dict_always_emits_params_hermes_to_prevent_frontend_throw():
    """#943 P1 (codex): 前端 ``HermesExecutionPanel.eventNode()`` 無條件讀
    ``event.params.hermes``（frontend/src/components/HermesExecutionPanel.tsx:15）。
    即使 event 沒有 hermes（也不是 ingestion.source），公開投影也必須發出
    ``params.hermes``（值 ``{}``），否則 legacy 快照 reproject 路徑
    (web.py::_handle_api_analysis_snapshot) 會讓前端 throw。
    """
    # event with params but no hermes, generic tool
    public = to_public_event_dict(
        {"ts": "t", "elapsed_sec": 0.1, "tool": "provider.invoke", "params": {"x": 1}}
    )
    assert "params" in public
    assert public["params"]["hermes"] == {}
    # event with no params at all → params.hermes still present (frontend-safe)
    public_no_params = to_public_event_dict({"ts": "t", "elapsed_sec": 0.1, "tool": "x"})
    assert "params" in public_no_params
    # direct key access must not raise (mirrors the unconditional frontend read)
    assert public_no_params["params"]["hermes"] == {}


def test_scrub_summary_covers_all_secret_marker_colon_forms():
    """#943 P1 (codex): colon-form ``key: value`` scrub 的 key 名集合對齊
    :data:`_SECRET_KEY_MARKERS`（api_key/apikey/auth/authorization/credential/
    password/secret/token），並補 passwd/access_token/key 等常見別名。summary 內
    ``token:`` / ``auth:`` / ``credential:`` / ``passwd:`` 等 colon 形樣的值必須被遮成
    [REDACTED]，不得原樣外露。
    """
    summary = "token: SECRET1\nauth: SECRET2\ncredential: SECRET3\npasswd: SECRET4"
    scrubbed = _scrub_summary(summary)
    for secret in ("SECRET1", "SECRET2", "SECRET3", "SECRET4"):
        assert secret not in scrubbed, f"colon-form secret leaked: {secret!r}"
    # marker prefixes preserved; only the secret values redacted (4 colon forms)
    assert scrubbed.count(REDACTED) == 4
    for marker in ("token:", "auth:", "credential:", "passwd:"):
        assert marker in scrubbed


def test_scrub_summary_known_residual_eq_base64_short_hex_allowlist_still_holds():
    """#943 characterization（harper/CISO merge 條件）：documenting the KNOWN
    residual that ``_scrub_summary`` does NOT cover, while pinning that the
    allowlist remains the effective control — main control is the allowlist.

    將 codex 點名、今日 scrub 不覆蓋的形樣整批放進 ``summary``：
      * ``=``-form（``key=``/``pw=``）—— 無結構化 marker 前綴；
      * base64-like blob —— 非認得的 token-like pattern；
      * <32-char hex（``abc1def2`` 僅 8 字元）—— 低於 long-hex（≥32）門檻。
    這些是已知殘餘，追蹤於 summary-hardening follow-up issue。**主防線是 deny-by-default
    allowlist**：同一 event 的敏感 param 值一律被丟棄（斷言 A）；本測試誠實記錄 scrub 邊界
    現狀（斷言 B），而非假裝 scrub 抓得到這些形樣。
    """
    event = {
        "ts": "t",
        "elapsed_sec": 0.1,
        "tool": "ingestion.source",
        "params": {
            # 敏感 param「值」故意用與 summary 不同的字面值，讓 substring 掃描能區分
            # 「來自 params 的洩漏」與「summary 本身的殘餘」。allowlist 必須全數丟棄。
            "api_key": "sk_live_PARAM_SECRET_DISTINCT",
            "url": "https://host/?token=PARAMLEAK",
            "wallet": "0xPARAMWALLET",
            "authorization": "Bearer PARAMJWT",
            # ingestion.source 公開鍵（curated）—— 應投影
            "source": "coinapi",
            "coin": "BTC",
        },
        # codex 點名的不可 scrub 形樣（已知殘餘，追蹤於 summary-hardening follow-up）
        "summary": "key=sk_live_abc123 pw=hunter2 blob=$(base64-ish) short=abc1def2",
    }
    public = to_public_event_dict(event)
    blob = json.dumps(public, ensure_ascii=False)

    # 斷言 A（主控制項仍有效）：deny-by-default allowlist 把每個敏感 param 值完全丟棄。
    # 即使下方 summary scrub 留下殘餘形樣，merge 依據（params 不外洩）依然成立。
    for needle in (
        "sk_live_PARAM_SECRET_DISTINCT",
        "PARAMLEAK",
        "0xPARAMWALLET",
        "PARAMJWT",
    ):
        assert needle not in blob, (
            f"allowlist 把敏感 param 值洩進公開 payload: {needle!r}"
        )
    # 敏感鍵被丟；curated 鍵存活
    for bad in ("api_key", "url", "wallet", "authorization"):
        assert bad not in public["params"]
    assert public["params"]["source"] == "coinapi"
    assert public["params"]["coin"] == "BTC"

    # 斷言 B（誠實殘餘 characterization）：summary scrub 今日「不覆蓋」=form / base64 /
    # <32hex —— 這些值仍在公開 summary 中（記錄現狀，非期望終態）。追蹤於 summary-hardening
    # follow-up issue；在該 follow-up 補上 scrub 覆蓋前，勿把這些翻成 `not in`。
    for residual in (
        "key=sk_live_abc123",
        "pw=hunter2",
        "blob=$(base64-ish)",
        "short=abc1def2",
    ):
        assert residual in public["summary"], (
            f"殘餘 summary 形樣意外被 scrub —— 請更新此 characterization: {residual!r}"
        )
