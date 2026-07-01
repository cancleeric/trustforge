"""語意立場偵測：反義/否定感知的 stance 判斷（純函式，離線/regex，不呼叫任何 LLM）。

用於 `scoring._corroboration` 的矛盾閘：token overlap 高不代表方向一致——
例如 "regulatory clarity"（監管明朗）與 "regulatory scrutiny"（監管收緊）共享
大量虛詞（market/expect/boost/significantly...），token overlap 可能 ≥ 0.4，
但語意其實對立，不該被算作獨立佐證。

反作弊：純 regex/集合運算，不呼叫 Bedrock 或任何 LLM，全確定性、可審查。

保守原則（CEO 指示，最高優先）：判不準時一律回 neutral，把來源計入佐證（別丟）。
- 誤判 contradict 會錯殺合法佐證（信任被錯壓低）；
- 誤判 support 會虛抬信任（讓矛盾主張被當佐證）；
- 兩者都傷產品信任核心，因此**寧可漏抓，不可誤判**。

核心保守機制（code-review 硬化後版本）：
**僅當跨兩段文字命中 ≥2 個不同反義詞對，才判 contradict；命中恰好 1 個反義對
時保守回 neutral（不判 support，也不判 contradict）**，讓上層 `_corroboration`
仍把該來源計入佐證，不誤殺。#15 bug 案例本身命中 2 對（clarity↔scrutiny +
adoption↔caution）仍會被抓；但常見的「單對＋出現在從屬子句」（如 "adoption
rising despite short-term caution"）不再因單一反義詞命中就被錯殺成 contradict。
細膩的單對語意判斷留給後續 W1.5（Bedrock 輔助）處理。

已知限制（誠實列出，非窮舉）：
- `ANTONYM_PAIRS` 為人工維護的有限詞表，刻意收斂在**金融/監管語境**、低領域漂移
  風險的詞對；未收錄的反義詞對仍可能被誤判為佐證（漏判，符合保守原則）。
- 「≥2 對才 contradict」是刻意保守的設計取捨：只命中 1 個反義對時，即使那 1 對
  真的語意對立，也會被判 neutral 而非 contradict（寧可漏抓矛盾，不可錯殺佐證）。
- 中文沒有天然詞界（contiguous CJK 無空格），無法套用 `\b` word-boundary；
  中文子字串誤命中風險靠「否定閘 + ≥2 對門檻」兜底，不是杜絕。
- 雙重否定（如 "not without scrutiny"／「並非沒有收緊」）採「否定標記奇偶性」
  判斷：窗口內偶數個否定標記視為 ambiguous，不嘗試還原成肯定語意二次判斷，
  直接跳過此命中。
- 否定偵測為雙向窗口（詞前 + 詞後皆查），但仍是固定字數/詞數窗口近似，非
  精確依存句法，跨句/跨子句的否定範圍仍可能誤判。
"""
from __future__ import annotations

import re

# --- 反義/對立詞群 ---------------------------------------------------------
# 每個 pair 為 (group_x, group_y)：若一段文字命中 group_x、另一段命中 group_y（或反之），
# 視為方向對立的候選證據。中英雙語混合，同時涵蓋中英文主張。
#
# 領域漂移守則（CEO 指示）：詞表刻意收斂在「明確金融/監管語境」的詞，
# 排除 growth/decline、trust/doubt 這類多領域通用詞（例如 "trust" 在資安/科技
# 語境常表示完全不同的意思，與市場信心無關）——對不確定的詞寧可不收，避免誤殺合法佐證。
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

# --- 詞界判斷（僅英文適用）---------------------------------------------------
# 中文無天然詞界（contiguous CJK 無空格分隔），套用 \b 會讓中文完全比對不到
# （\b 只在 \w/非\w 交界處成立，CJK 字元彼此相鄰時內部沒有交界）。因此只對
# 純 ASCII 英文詞套用 \b + re.ASCII word-boundary，避免「precautionary」誤命中
# 子字串「caution」；中文靠既有否定閘 + 下方「≥2 對才 contradict」門檻兜底
# （W1 案2b review #2）。
_ASCII_WORD_RX = re.compile(r"^[A-Za-z']+$")

# --- 否定偵測 ---------------------------------------------------------------
# 明確否定結構（比照 scoring._NEG_RX 精神：只吃明確否定詞，不吃「不僅/不斷/不只」
# 這類肯定語境）。獨立定義於本模組，避免與 scoring.py 互相 import 造成耦合。
_NEG_FALSE_POSITIVE_EN = re.compile(r"\bnot\s+only\b|\bnot\s+just\b", re.IGNORECASE)
_NEG_RX_EN = re.compile(
    r"\bnot\b|\bno\b|\bnever\b|\blacks?\b|\bwithout\b|\bfail(?:s|ed)?\s+to\b|"
    r"\bdoesn'?t\b|\bdon'?t\b|\bisn'?t\b|\baren'?t\b|\bcan'?t\b|\bwon'?t\b",
    re.IGNORECASE,
)

# 單字「不」/「沒」在中文極常見（不明確/不看多/不採用），但「不僅/不但/不只/不斷/
# 不外乎/不過」是連接詞/程度副詞語境，不是在否定後面那個詞的方向——比照英文
# _NEG_FALSE_POSITIVE_EN 的精神，計數前先濾掉這些片語，避免誤判否定。
_NEG_FALSE_POSITIVE_ZH = re.compile(r"不僅|不但|不只|不斷|不外乎|不過")
_NEG_RX_ZH = re.compile(r"不會|不太|不致|不至|不再|沒有|沒|尚未|未|無法|別|勿|非|不")


def _neg_count_zh(text: str, start: int, end: int, window_chars: int = 4) -> int:
    """中文否定標記計數：命中詞**前後** window_chars 字內各出現幾個否定詞（供奇偶判斷）。

    雙向查詢（W1 案2b review #3 修正）：否定詞不一定出現在被否定詞之前，
    保留雙向以與英文邏輯一致，並涵蓋後置否定句型。
    """
    before = text[max(0, start - window_chars):start]
    after = text[end:end + window_chars]
    cleaned_before = _NEG_FALSE_POSITIVE_ZH.sub(" ", before)
    cleaned_after = _NEG_FALSE_POSITIVE_ZH.sub(" ", after)
    return len(_NEG_RX_ZH.findall(cleaned_before)) + len(_NEG_RX_ZH.findall(cleaned_after))


def _neg_count_en(text: str, start: int, end: int, look_words: int = 4) -> int:
    """英文否定標記計數：命中詞**前後** look_words 個英文詞內各出現幾個否定詞。

    雙向查詢（W1 案2b review #3 修正）：否定詞常出現在被否定詞之後，例如
    "scrutiny will not materialize"——"not" 在 "scrutiny" 後面，只查詞前窗口
    會完全漏掉這個否定，誤判為 scrutiny 被正面主張、進而誤判 contradict。
    """
    before_text = text[:start]
    words_before = re.findall(r"[a-zA-Z']+", before_text)
    window_before = " ".join(words_before[-look_words:])

    after_text = text[end:]
    words_after = re.findall(r"[a-zA-Z']+", after_text)
    window_after = " ".join(words_after[:look_words])

    cleaned_before = _NEG_FALSE_POSITIVE_EN.sub(" ", window_before)
    cleaned_after = _NEG_FALSE_POSITIVE_EN.sub(" ", window_after)
    return len(_NEG_RX_EN.findall(cleaned_before)) + len(_NEG_RX_EN.findall(cleaned_after))


def _neg_state(text_lower: str, start: int, end: int) -> str:
    """回傳否定狀態："none"（無否定）/ "negated"（單一否定，取消命中）/
    "ambiguous"（偶數個否定、雙重否定，語意不確定，保守跳過）。

    奇偶性（parity）判斷：
    - 0 個否定標記 → none（正常計入命中）。
    - 奇數個（通常 1 個）→ negated → 該次 occurrence 被取消，不計入命中
      （沿用既有 codebase 對否定的處理精神：取消而非嘗試反向推論語意）。
    - 偶數個（≥2，例如 "not without scrutiny" / 「並非沒有收緊」）→ ambiguous →
      不嘗試還原成肯定語意二次判斷，直接視為不確定，同樣不計入命中。
      （保守原則：判不準時寧可漏抓，不可誤判成 support 或 contradict。）
    """
    n = (
        _neg_count_zh(text_lower, start, end)
        + _neg_count_en(text_lower, start, end)
    )
    if n == 0:
        return "none"
    if n % 2 == 0:
        return "ambiguous"
    return "negated"


def _find_hit(text_lower: str, group: frozenset[str]) -> str | None:
    r"""在文字中尋找 group 內任一詞的**乾淨**命中，回傳命中詞或 None。

    - 詞長降序比對，避免短詞先命中蓋掉更精確的長詞。
    - 英文詞套用 `\b` word-boundary（+ re.ASCII），避免子字串誤命中
      （例如 "precautionary" 不可命中 "caution"）；中文無詞界，維持子字串搜尋，
      靠否定閘 + 上層 ≥2 對門檻兜底（W1 案2b review #2）。
    - 掃描**所有** occurrence，只要有任一乾淨（非否定/非 ambiguous）的 occurrence
      即視為命中；若該詞所有 occurrence 都被否定，才換下一個候選詞
      （W1 案2b review #4：避免只看第一個 occurrence 就誤判整體未命中/命中）。
    """
    for word in sorted(group, key=len, reverse=True):
        if _ASCII_WORD_RX.match(word):
            pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.ASCII)
        else:
            pattern = re.compile(re.escape(word))
        for m in pattern.finditer(text_lower):
            if _neg_state(text_lower, m.start(), m.end()) == "none":
                return word
    return None


def semantic_stance(
    text_a: str, text_b: str, tokens_a: set[str], tokens_b: set[str]
) -> tuple[str, list[str]]:
    """比較兩段文字的語意立場，回傳 ("support" | "contradict" | "neutral", evidence)。

    判斷順序：
    1. 反義詞對：text_a 命中 group_x 且 text_b 命中對應 group_y（或反之），
       且命中詞皆未被否定守門取消 → 記入 evidence。
       **僅當累積 ≥2 個不同反義詞對命中時才判 contradict**（保守核心機制，
       見模組 docstring）；恰好命中 1 對時，保守回 neutral（不誤殺，也不假裝支撐）。
    2. 無足夠反義證據，但兩段有共享具體詞（tokens 交集，呼叫端應已過濾停用詞）
       → support，evidence 為共享詞列表。
    3. 其餘（無反義命中也無共享詞）→ neutral，evidence 為空。

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

    if len(evidence) >= 2:
        return "contradict", evidence
    if evidence:
        # 恰好命中 1 個反義對：證據不足以斷定矛盾，保守回 neutral，不誤殺、
        # 也不假裝支撐。細膩單對語意判斷留給 W1.5（Bedrock 輔助）。
        return "neutral", []

    shared = tokens_a & tokens_b
    if shared:
        return "support", sorted(shared)
    return "neutral", []
