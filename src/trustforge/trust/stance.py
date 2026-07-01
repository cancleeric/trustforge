"""語意立場偵測：反義/否定感知的 stance 判斷（純函式，離線/regex，不呼叫任何 LLM）。

用於 `scoring._corroboration` 的矛盾閘：token overlap 高不代表方向一致——
例如 "regulatory clarity"（監管明朗）與 "regulatory scrutiny"（監管收緊）共享
大量虛詞（market/expect/boost/significantly...），token overlap 可能 ≥ 0.4，
但語意其實對立，不該被算作獨立佐證。

反作弊：純 regex/集合運算，不呼叫 Bedrock 或任何 LLM，全確定性、可審查。

保守原則（CEO 指示）：判不準時一律回 neutral。
- 誤判 contradict 會錯殺合法佐證（信任被錯壓低）；
- 誤判 support 會虛抬信任（讓矛盾主張被當佐證）；
- 兩者都傷產品信任核心，因此**寧可漏抓，不可誤判**——本模組任何不確定情境一律退回
  「不計入此次命中」，讓上層 fallback 到 neutral/既有 token overlap 邏輯。

已知限制（誠實列出，非窮舉）：
- `ANTONYM_PAIRS` 為人工維護的有限詞表，刻意收斂在**金融/監管語境**、低領域漂移風險的
  詞對；未收錄的反義詞對仍可能被誤判為佐證（漏判，符合保守原則）。
- 只偵測「單一反義詞對」層級的對立，不做完整語意/否定邏輯推理（隱喻、反諷不處理）。
- 雙重否定（如 "not without scrutiny"／「並非沒有收緊」）採「否定標記奇偶性」判斷：
  窗口內偶數個否定標記視為 ambiguous（ 不嘗試還原成肯定語意去二次判斷方向），
  直接跳過此命中，不用於構成 contradict 或 support 證據。
- 英文否定守門用「前 4 個英文詞」窗口近似中文的「前 4 字」窗口，非精確依存句法，
  跨句/跨子句的否定範圍可能誤判（漏抓或誤攔皆有可能）。
"""
from __future__ import annotations

import re

# --- 反義/對立詞群 ---------------------------------------------------------
# 每個 pair 為 (group_x, group_y)：若一段文字命中 group_x、另一段命中 group_y（或反之），
# 視為方向對立的證據。中英雙語混合，同時涵蓋中英文主張。
#
# 領域漂移守則（CEO 指示）：詞表刻意收斂在「明確金融/監管語境」的詞，
# 排除 growth/decline、trust/doubt 這類多領域通用詞（例如 "trust" 在資安/科技語境
# 常表示完全不同的意思，與市場信心無關）——對不確定的詞寧可不收，避免誤殺合法佐證。
ANTONYM_PAIRS: list[tuple[frozenset[str], frozenset[str]]] = [
    # 監管態度：明朗/清晰 vs 收緊/嚴查（#15 bug 案例核心）
    (
        frozenset({"clarity", "clarify", "clarified", "明朗", "明確", "清晰"}),
        frozenset({
            "scrutiny", "crackdown", "clampdown",
            "收緊", "收紧", "打壓", "打压", "嚴查", "严查", "嚴管", "严管",
        }),
    ),
    # 市場行為：機構採用/接納 vs 觀望/規避（#15 bug 案例核心）
    (
        frozenset({"adoption", "adopt", "採用", "采用", "普及", "接納", "接纳"}),
        frozenset({
            "caution", "avoidance",
            "觀望", "观望", "謹慎", "谨慎", "規避", "规避",
        }),
    ),
    # 情緒/立場：看多 vs 看空（純金融術語，領域漂移風險極低）
    (
        frozenset({"bullish", "看多", "看漲", "看涨"}),
        frozenset({"bearish", "看空", "看跌"}),
    ),
]

# --- 否定偵測 ---------------------------------------------------------------
# 明確否定結構（比照 scoring._NEG_RX 精神：只吃明確否定詞，不吃「不僅/不斷/不只」
# 這類肯定語境）。獨立定義於本模組，避免與 scoring.py 互相 import 造成耦合。
_NEG_FALSE_POSITIVE_EN = re.compile(r"\bnot\s+only\b|\bnot\s+just\b", re.IGNORECASE)
_NEG_RX_EN = re.compile(
    r"\bnot\b|\bno\b|\bnever\b|\blacks?\b|\bwithout\b|\bfail(?:s|ed)?\s+to\b|"
    r"\bdoesn'?t\b|\bdon'?t\b|\bisn'?t\b|\baren'?t\b|\bcan'?t\b|\bwon'?t\b",
    re.IGNORECASE,
)
_NEG_RX_ZH = re.compile(r"不會|不太|不致|不至|不再|沒有|沒|尚未|未|無法|別|勿|非")


def _neg_count_zh(text: str, start: int, window_chars: int = 4) -> int:
    """中文否定標記計數：命中詞前 window_chars 字內出現幾個否定詞（供奇偶判斷）。"""
    window = text[max(0, start - window_chars):start]
    return len(_NEG_RX_ZH.findall(window))


def _neg_count_en(text: str, start: int, look_words: int = 4) -> int:
    """英文否定標記計數：命中詞前 look_words 個英文詞內出現幾個否定詞（供奇偶判斷）。

    先濾掉 "not only / not just" 這類肯定語境片語，再計數剩餘否定詞。
    """
    before = text[:start]
    words = re.findall(r"[a-zA-Z']+", before)
    window = " ".join(words[-look_words:])
    cleaned = _NEG_FALSE_POSITIVE_EN.sub(" ", window)
    return len(_NEG_RX_EN.findall(cleaned))


def _neg_state(text_lower: str, start: int) -> str:
    """回傳否定狀態："none"（無否定）/ "negated"（單一否定，取消命中）/
    "ambiguous"（偶數個否定、雙重否定，語意不確定，保守跳過）。

    奇偶性（parity）判斷：
    - 0 個否定標記 → none（正常計入命中）。
    - 奇數個（通常 1 個）→ negated → 該命中被取消，不計入 support/contradict 證據
      （沿用既有 codebase 對否定的處理精神：取消而非嘗試反向推論語意）。
    - 偶數個（≥2，例如 "not without scrutiny" / 「並非沒有收緊」）→ ambiguous →
      不嘗試還原成肯定語意二次判斷，直接視為不確定，同樣不計入命中。
      （保守原則：判不準時寧可漏抓，不可誤判成 support 或 contradict。）
    """
    n = _neg_count_zh(text_lower, start) + _neg_count_en(text_lower, start)
    if n == 0:
        return "none"
    if n % 2 == 0:
        return "ambiguous"
    return "negated"


def _find_hit(text_lower: str, group: frozenset[str]) -> str | None:
    """在文字中尋找 group 內任一詞的命中位置，套用否定守門，回傳命中詞或 None。

    詞長降序比對，避免短詞先命中蓋掉更精確的長詞。
    命中詞若處於 negated 或 ambiguous 否定狀態，視為不計入命中，繼續嘗試同組其他詞。
    """
    for word in sorted(group, key=len, reverse=True):
        m = re.search(re.escape(word), text_lower)
        if not m:
            continue
        if _neg_state(text_lower, m.start()) != "none":
            continue
        return word
    return None


def semantic_stance(
    text_a: str, text_b: str, tokens_a: set[str], tokens_b: set[str]
) -> tuple[str, list[str]]:
    """比較兩段文字的語意立場，回傳 ("support" | "contradict" | "neutral", evidence)。

    判斷順序：
    1. 反義詞對：text_a 命中 group_x 且 text_b 命中對應 group_y（或反之），
       且雙方命中詞皆未被否定守門取消（見 `_neg_state`）→ contradict，
       evidence 記錄命中的反義詞對（如 "clarity↔scrutiny"）。
    2. 無反義命中，但兩段有共享具體詞（tokens 交集，呼叫端應已過濾停用詞）
       → support，evidence 為共享詞列表。
    3. 其餘（無反義命中也無共享詞，或否定狀態不確定被跳過）→ neutral，evidence 為空。

    純函式、確定性、不呼叫任何 LLM。
    """
    ta_low = text_a.lower()
    tb_low = text_b.lower()
    evidence: list[str] = []

    for group_x, group_y in ANTONYM_PAIRS:
        hit_a_x = _find_hit(ta_low, group_x)
        hit_b_y = _find_hit(tb_low, group_y)
        if hit_a_x and hit_b_y:
            evidence.append(f"{hit_a_x}↔{hit_b_y}")
            continue
        hit_a_y = _find_hit(ta_low, group_y)
        hit_b_x = _find_hit(tb_low, group_x)
        if hit_a_y and hit_b_x:
            evidence.append(f"{hit_a_y}↔{hit_b_x}")

    if evidence:
        return "contradict", evidence

    shared = tokens_a & tokens_b
    if shared:
        return "support", sorted(shared)
    return "neutral", []
