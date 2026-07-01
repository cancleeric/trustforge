"""W1.5（#15）：Bedrock 語意 stance 子分類器測試。

⚠️ 本檔全部用注入的 fake stance_fn／fake client（純 dict 對照或計數 stub），
不呼叫真實 Bedrock/AWS（見 bedrock.py 的 classify_stance 才需要 boto3；本檔測試
的是 `trust.scoring._corroboration` / `trust.scoring.score` / `trust.stance_cache`
的介接邏輯，與真實模型輸出解耦，符合本 PR 範圍限制）。

案例來源：
- issue15：Issue #15 復現案例（英文『監管明朗+採用』vs『監管收緊+審慎』，token overlap
  高但語意對立）。
- review1/2/3：前三輪 code review 對舊詞表 heuristic 的打臉案例（despite caution /
  precautionary framework / scrutiny will not materialize 後置否定），這些案例語意上
  都「不是矛盾」，W1.5 的語意分類器不該把它們錯殺成 contradiction。
"""
from __future__ import annotations

from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, _corroboration, score
from trustforge.trust.stance_cache import StanceCache, cache_key, cached_stance_fn


def _doc(id: str, kind: str, source: str, ts: float = 1000.0) -> Document:
    return Document(id=id, kind=kind, source=source, text="", ts=ts)


def _dict_stance_fn(table: dict[str, str]):
    """純 dict 對照的 fake stance_fn（不打真 AWS）。

    key 用 `stance_cache.cache_key`（排序後正規化），與生產程式碼的快取邏輯一致，
    確保無論 `_corroboration` 用哪個順序呼叫 (a, b) 都能命中同一筆假資料。
    """
    def _fn(a: str, b: str) -> str:
        return table.get(cache_key(a, b), "neutral")
    return _fn


# --- Issue #15 案例：fake 給 contradiction → corr 不含該來源（矛盾閘生效） ------

def test_issue15_fake_contradiction_excludes_from_corroboration():
    text_a = ("Market analysts expect regulatory clarity to boost institutional "
              "adoption significantly.")
    text_b = ("Market observers expect regulatory scrutiny to boost investor "
              "caution significantly.")
    c_a = Claim(id="i15a", text=text_a, doc=_doc("da", "news", "coindesk"))
    c_b = Claim(id="i15b", text=text_b, doc=_doc("db", "news", "reuters"))

    stance_fn = _dict_stance_fn({cache_key(text_a, text_b): "contradiction"})
    corr = _corroboration(c_a, [c_a, c_b], stance_fn=stance_fn)

    assert corr == 0.0, f"#15：fake stance_fn 判 contradiction，corr 應 = 0.0，實際: {corr}"


# --- 前 3 輪 review 打臉案例：fake 給 entailment/neutral → 不可錯殺合法佐證 -------

def test_review1_despite_caution_not_falsely_blocked():
    """review#1：『despite short-term caution』仍是同向支撐，fake 給 entailment。"""
    text_a = "Institutional adoption continues rising despite short-term regulatory caution"
    text_b = "Institutional adoption continues rising steadily this quarter"
    c_a = Claim(id="rev1a", text=text_a, doc=_doc("da", "news", "coindesk"))
    c_b = Claim(id="rev1b", text=text_b, doc=_doc("db", "news", "reuters"))

    stance_fn = _dict_stance_fn({cache_key(text_a, text_b): "entailment"})
    corr = _corroboration(c_a, [c_a, c_b], stance_fn=stance_fn)

    assert corr > 0.0, f"despite caution 不應被錯殺，corr 應 > 0，實際: {corr}"


def test_review2_precautionary_framework_not_falsely_blocked():
    """review#2：『precautionary regulatory framework』不等於反對採用，fake 給 entailment。"""
    text_a = "Institutional adoption continues rising under a precautionary regulatory framework"
    text_b = "Institutional adoption continues rising steadily this quarter"
    c_a = Claim(id="rev2a", text=text_a, doc=_doc("da", "news", "coindesk"))
    c_b = Claim(id="rev2b", text=text_b, doc=_doc("db", "news", "reuters"))

    stance_fn = _dict_stance_fn({cache_key(text_a, text_b): "entailment"})
    corr = _corroboration(c_a, [c_a, c_b], stance_fn=stance_fn)

    assert corr > 0.0, f"precautionary framework 不應被錯殺，corr 應 > 0，實際: {corr}"


def test_review3_scrutiny_will_not_materialize_not_falsely_blocked():
    """review#3：『scrutiny will not materialize』是後置否定，實際不構成矛盾，fake 給 neutral。"""
    text_a = "Analysts say regulatory clarity is improving across major markets"
    text_b = "Analysts say regulatory scrutiny will not materialize across major markets"
    c_a = Claim(id="rev3a", text=text_a, doc=_doc("da", "news", "coindesk"))
    c_b = Claim(id="rev3b", text=text_b, doc=_doc("db", "news", "reuters"))

    stance_fn = _dict_stance_fn({cache_key(text_a, text_b): "neutral"})
    corr = _corroboration(c_a, [c_a, c_b], stance_fn=stance_fn)

    assert corr > 0.0, f"scrutiny will not materialize 不應被錯殺，corr 應 > 0，實際: {corr}"


# --- stance_fn=None 回歸鎖：向後相容，逐字比對舊行為 -----------------------------

def test_stance_fn_none_keeps_two_independent_sources_formula():
    """回歸鎖：stance_fn=None（未傳參數的預設值）時，兩個獨立來源仍是
    1 - 0.5**2 = 0.75，跟加入 W1.5 前的公式逐字相同（純加參數不改變既有數值行為）。
    """
    doc_a = _doc("da", "onchain", "glassnode")
    doc_b = _doc("db", "news", "coindesk")
    doc_c = _doc("dc", "news", "reuters")
    c_a = Claim(id="ga", text="清算 瀑布 觸發 ETF 審批 加速", doc=doc_a)
    c_b = Claim(id="gb", text="清算 瀑布 影響 ETF 申請 結果", doc=doc_b)
    c_c = Claim(id="gc", text="清算 瀑布 導致 ETF 審批 延後", doc=doc_c)

    corr_explicit_none = _corroboration(c_a, [c_a, c_b, c_c], stance_fn=None)
    corr_default = _corroboration(c_a, [c_a, c_b, c_c])  # 不傳參數，走預設值

    assert corr_explicit_none == corr_default == 1.0 - 0.5 ** 2, (
        f"回歸鎖：兩獨立來源 corr 應 = 0.75，實際: explicit={corr_explicit_none} "
        f"default={corr_default}"
    )


def test_stance_fn_none_review_cases_keep_pre_w15_corroboration():
    """回歸鎖：review1/2/3 案例在 stance_fn=None 時，corr 數值必須跟 W1.5 加入前
    （純 overlap + 方向閘判斷）一致——這些案例本就通過方向閘，corr 應 > 0，
    加入 stance_fn 參數本身不應改變任何既有數值。
    """
    cases = [
        ("review1", "Institutional adoption continues rising despite short-term regulatory caution",
         "Institutional adoption continues rising steadily this quarter"),
        ("review2", "Institutional adoption continues rising under a precautionary regulatory framework",
         "Institutional adoption continues rising steadily this quarter"),
        ("review3", "Analysts say regulatory clarity is improving across major markets",
         "Analysts say regulatory scrutiny will not materialize across major markets"),
    ]
    for name, text_a, text_b in cases:
        c_a = Claim(id=f"{name}a", text=text_a, doc=_doc(f"{name}da", "news", "coindesk"))
        c_b = Claim(id=f"{name}b", text=text_b, doc=_doc(f"{name}db", "news", "reuters"))
        corr = _corroboration(c_a, [c_a, c_b], stance_fn=None)
        assert corr > 0.0, f"回歸鎖 {name}：stance_fn=None 應維持 corr > 0，實際: {corr}"


# --- 快取層：命中同輸入回同結果（確定性），且底層 classify_stance 只呼叫一次 -------

def test_cache_hit_is_deterministic_and_avoids_duplicate_calls():
    calls: list[tuple[str, str]] = []

    class _CountingClient:
        def classify_stance(self, a: str, b: str) -> str:
            calls.append((a, b))
            return "contradiction"

    cache = StanceCache()  # 無持久化路徑，純記憶體、乾淨環境
    stance_fn = cached_stance_fn(_CountingClient(), cache=cache)

    text_a = ("Market analysts expect regulatory clarity to boost institutional "
              "adoption significantly.")
    text_b = ("Market observers expect regulatory scrutiny to boost investor "
              "caution significantly.")

    r1 = stance_fn(text_a, text_b)
    r2 = stance_fn(text_b, text_a)  # 反轉順序，仍應命中同一筆快取（key 排序後正規化）
    r3 = stance_fn(text_a, text_b)

    assert r1 == r2 == r3 == "contradiction", f"快取命中應回同結果，實際: {r1}, {r2}, {r3}"
    assert len(calls) == 1, f"快取應讓底層 classify_stance 只被呼叫一次，實際呼叫 {len(calls)} 次"


def test_cache_version_mismatch_is_treated_as_miss():
    """version 不符（prompt/model 版本變更）視為 miss，不可誤用舊版本快取結果。"""
    cache = StanceCache()
    key = cache_key("A", "B")
    cache._mem[key] = {"label": "contradiction", "version": "stale-version"}
    assert cache.get("A", "B") is None, "version 不符應視為 miss，不可回傳過期快取值"


# --- score() 貫穿參數 stance_client：端到端驗證 ---------------------------------

def test_score_stance_client_wiring_reduces_corroboration():
    """score() 的 stance_client 貫穿參數：確實透過 cached_stance_fn 呼叫
    client.classify_stance，並反映在 ScoredClaim.components['corroboration']。
    """
    class _FakeContradictClient:
        def classify_stance(self, a: str, b: str) -> str:
            return "contradiction"

    text_a = ("Market analysts expect regulatory clarity to boost institutional "
              "adoption significantly.")
    text_b = ("Market observers expect regulatory scrutiny to boost investor "
              "caution significantly.")
    c_a = Claim(id="wa", text=text_a, doc=_doc("da", "news", "coindesk"))
    c_b = Claim(id="wb", text=text_b, doc=_doc("db", "news", "reuters"))

    scored_with_stance = score([c_a, c_b], now=1000.0, stance_client=_FakeContradictClient())
    sc_a = next(sc for sc in scored_with_stance if sc.claim.id == "wa")
    assert sc_a.components["corroboration"] == 0.0, (
        "score() 應把 stance_client 貫穿到 _corroboration，矛盾閘生效"
    )

    # CEO+codex 對抗審修正後：stance_client=None 不等於「矛盾閘關閉」，而是
    # 「沒有線上 client、只讀持久化快取，快取 miss 時 fail-safe 回 neutral」。
    # 用一組確定不在 demo/sample_data/stance_cache.json 裡的文字，驗證 miss 情境
    # 下不會誤判成 contradiction、不會錯殺合法佐證。
    text_c = "Traders note steady exchange inflows this week amid low volatility"
    text_d = "Traders note steady exchange outflows this week amid low volatility"
    c_c = Claim(id="wc", text=text_c, doc=_doc("dc", "news", "coindesk"))
    c_d = Claim(id="wd", text=text_d, doc=_doc("dd", "news", "reuters"))
    scored_without_stance = score([c_c, c_d], now=1000.0, stance_client=None)
    sc_c = next(sc for sc in scored_without_stance if sc.claim.id == "wc")
    assert sc_c.components["corroboration"] > 0.0, (
        "stance_client=None + 持久化快取 miss 時應 fail-safe 回 neutral，不可錯殺合法佐證"
    )


def test_score_stance_client_without_classify_stance_does_not_crash():
    """向後相容防禦：stance_client 若沒有 classify_stance 方法（如舊版測試 stub），
    score() 應安全降級為不啟用矛盾閘，不 crash。
    """
    class _NoStanceClient:
        pass

    c_a = Claim(id="na", text="清算 瀑布 觸發 ETF 審批", doc=_doc("da", "onchain", "glassnode"))
    c_b = Claim(id="nb", text="清算 瀑布 影響 ETF 申請", doc=_doc("db", "news", "coindesk"))

    scored = score([c_a, c_b], now=1000.0, stance_client=_NoStanceClient())
    assert len(scored) == 2


# --- CEO+codex 對抗審修正回歸測試（PR #26 續修）----------------------------------
# [HIGH] 離線路徑必須也走持久化快取；[MEDIUM] cache_key 不可用裸分隔符串接。


def test_offline_persistent_cache_hit_excludes_contradiction():
    """[HIGH 驗收] 離線（無真實 Bedrock client）+ demo/sample_data/stance_cache.json
    已預先算好 #15 這對是 contradiction 時，score(stance_client=None)（orchestrator.py
    離線路徑實際傳的值）應該仍能透過 cached_stance_fn(None) 讀到持久化快取、正確把
    這對主張排除在獨立佐證之外——不是完全不啟用矛盾閘。這是 orchestrator.py:341
    `stance_client=None if client.offline else client` 離線分支的等價重現。
    """
    text_a = ("Market analysts expect regulatory clarity to boost institutional "
              "adoption significantly.")
    text_b = ("Market observers expect regulatory scrutiny to boost investor "
              "caution significantly.")
    c_a = Claim(id="off15a", text=text_a, doc=_doc("da", "news", "coindesk"))
    c_b = Claim(id="off15b", text=text_b, doc=_doc("db", "news", "reuters"))

    # 完全不提供 client（等同 orchestrator 離線時傳的 None），只靠預設持久化快取路徑
    # （demo/sample_data/stance_cache.json）。
    scored = score([c_a, c_b], now=1000.0, stance_client=None)
    sc_a = next(sc for sc in scored if sc.claim.id == "off15a")
    assert sc_a.components["corroboration"] == 0.0, (
        "離線模式應能讀到持久化快取的 contradiction 判斷並排除該對佐證，"
        f"實際 corr={sc_a.components['corroboration']}"
    )


def test_offline_cache_miss_fails_safe_to_neutral_without_client():
    """[HIGH 驗收] 離線 + 快取 miss（demo/sample_data/stance_cache.json 沒有這對的
    預算結果）→ fail-safe 回 "neutral"，不 crash（不會因為 client=None 去呼叫
    client.classify_stance）、也不可錯殺原本合法的佐證。
    """
    text_a = "Institutional adoption continues rising despite short-term regulatory caution"
    text_b = "Institutional adoption continues rising steadily this quarter"
    c_a = Claim(id="offmiss_a", text=text_a, doc=_doc("da", "news", "coindesk"))
    c_b = Claim(id="offmiss_b", text=text_b, doc=_doc("db", "news", "reuters"))

    scored = score([c_a, c_b], now=1000.0, stance_client=None)
    sc_a = next(sc for sc in scored if sc.claim.id == "offmiss_a")
    assert sc_a.components["corroboration"] > 0.0, (
        "快取 miss 時應 fail-safe 回 neutral、不可錯殺合法佐證，"
        f"實際 corr={sc_a.components['corroboration']}"
    )


def test_cache_key_no_collision_via_raw_separator_concat():
    """[MEDIUM 驗收] cache_key 不可用裸分隔符字串接：('a<SEP>b', 'c') 與
    ('a', 'b<SEP>c') 這類「使用者可控文字恰好包含分隔符」的輸入，正規化排序後
    若用裸串接會產生同一把 key（讓惡意 claim 挪用他對快取結果）。改用 canonical
    JSON 陣列序列化後，兩者必須是不同的 key。
    """
    # 用舊版分隔符 "␟" 本身當作使用者輸入的一部分，模擬對抗場景。
    sep = "␟"
    key1 = cache_key(f"a{sep}b", "c")
    key2 = cache_key("a", f"b{sep}c")
    assert key1 != key2, f"cache_key 發生碰撞：{key1!r} == {key2!r}"

    # 額外驗證：兩把 key 各自仍能正確命中/不命中自己對應的快取，不會互相干擾。
    cache = StanceCache()
    cache.set(f"a{sep}b", "c", "contradiction")
    cache.set("a", f"b{sep}c", "entailment")
    assert cache.get(f"a{sep}b", "c") == "contradiction"
    assert cache.get("a", f"b{sep}c") == "entailment"
