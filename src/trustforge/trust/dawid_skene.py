"""Dawid-Skene EM 估計器 — W2 動態信譽的離線 fallback（TrustForge #180/#182）。

#182 把本模組接進 `trust.scoring._iterate_source_reputation`：當生產路徑是
「離線 / 語意未驗證」（沒有任何一筆真 `entailment` 佐證流進 W2）時，線上
truth-discovery 動態信譽無法靠語意驗證計分，改由本模組對「多源方向標籤的
統計共識」估算每來源可靠度 `r(source)`，直接餵給 Step B 混合公式的
`agreement_score`。

⚠️ 誠實紅線（與 #167 / AUC 無關，絕不宣稱）：
- 本模組產出的是「多源方向標籤的統計共識信心」——也就是「在已知真標籤下，
  這個來源有多常跟多數一致」的**事後一致性**估計。它不是預測力（predictive
  power），沒有解決 #167 的多源方向預測 AUC≈0.49 問題；DS 收斂只代表標籤
  之間內部一致，不代表標籤對真實市場方向有任何外在校準。
- 因此 DS 路徑只用來「在離線時給一個比先驗略好的共識排序」，**不偽造**任何
  agree/contra 聯集、不聲稱已做語意驗證。文字/UI 一律標註「DS 共識收斂」，
  不與 entailment 驗證的「互證」混為一談。

演算法：標準 Dawid-Skene EM（Dawid & Skene 1979）。
- `prior` π[label]：每個 item 取多數票（平手取 `LABELS` 順序第一），跨 item
  計票後歸一化。
- 混淆矩陣 CM[source][true][obs]：初始化採**多數票 warm start**（每來源 CM 對角
  = 其與所屬 item 多數票的一致率，其餘均分 off-diag），**禁用 random、完全確定性**。
  註：`naive 均勻 1/3` 對均衡多類別資料是 EM 退化固定點（見函式內初始化區註解），
  會讓可靠度塌成 0.5 / 崩潰，故不採用。
- E-step：用當前 π + CM 算每 item 的 true-label 後驗（Bayes，normalization
  用 `math.fsum`）。
- M-step：用後驗重估 π 與 CM（全部 `sorted()` + `math.fsum`，確定性、抗
  `PYTHONHASHSEED`）。
- 收斂：`|L[t] - L[t-1]| < tol` 或達 `n_iter` 停止；每輪 data log-likelihood
  記入 `meta["likelihoods"]`，並斷言**單調不減**（容差 `tol`），違反即 raise。

可靠度 → 單一分數 `r(source)`（對稱、決定性、r∈[0,1]、r=0.5 對應先驗等價）：
- `accuracy = mean(CM[L][L])`（對角平均，label 對稱）
- `confusion_err = max_{i≠j} CM[i][j]`（最大非對角，越大表示越愛亂給錯標）
- `skill = accuracy - confusion_err`
- `r = 0.5 + 0.5*(skill - 0.5)*2`（飽和映射、clamp 到 [0,1]）

退化（保守小樣本 guard，不與線上 `MIN_INDEPENDENT_EVIDENCE` 混用）：
- 來源數 < 3（`標籤數`）→ 所有來源 `r = 0.5`（無法估混淆矩陣），記入
  `meta["fallback_sources"]`。
- 某 item 的 rater 數 < `min_raters_per_item` → 只參與此類 item 的來源
  `r = 0.5`，記入 `meta["fallback_sources"]`（這類 item 無法估計該來源與多數
  的偏離）。
- r=0.5 對應「先驗等價」：呼叫端應把此類來源的信譽視為純先驗、不動它（見
  `scoring._iterate_source_reputation` 的 DS 分支把 fallback 來源強制 α=1）。

確定性保證：本模組**禁用 random**（不設種子也確定）。所有會受 dict/set 迭代
順序影響的加總都先 `sorted()` 再 `math.fsum`，因此同輸入在不同 process /
`PYTHONHASHSEED` 下得到逐位元相同的結果。
"""
from __future__ import annotations

import math
from collections import defaultdict

LABELS = ("bullish", "bearish", "neutral")
_IDX = {lab: i for i, lab in enumerate(LABELS)}
N_LABELS = len(LABELS)

# log(0) 的數值地板：EM 在 M-step 可能把某格 CM 估到 0（某來源從未給過某標籤），
# 直接 math.log(0) 會炸成 -inf 毀掉整條 likelihood。用遠小於任何真機率的地板值
# 取代，對收斂後的 CM 與 likelihood（及單調性，容差 1e-9 內）無實質影響。
_LOG_FLOOR = math.log(1e-300)


def _safe_log(x: float) -> float:
    return math.log(x) if x > 0.0 else _LOG_FLOOR


def em_source_reliability(
    votes: dict[tuple, dict[str, str]],
    n_iter: int = 50,
    tol: float = 1e-9,
    min_raters_per_item: int = 2,
) -> tuple[dict[str, float], dict, dict, dict]:
    """Dawid-Skene EM 估算每來源可靠度。

    參數
    ----
    votes: dict[(coin, window), dict[source, label]]
        每個 item（(coin, window)）下，各來源投出的方向標籤（必須 ∈ `LABELS`）。
        同 (coin, window) 內每來源只應有一票（重複投以最後一筆為準，由呼叫端保證）。

    回傳
    ----
    (reliability, confusion, posterior, meta)
    - reliability: {source: r}，每來源可靠度單分數（退化來源為 0.5）。
    - confusion: {source: CM}（CM 為 [[..]×N_LABELS] 機率矩陣，列加總=1）。
    - posterior: {item_key: [P(true=lab) for lab in LABELS]}（最後一輪 E-step）。
    - meta: {"likelihoods", "fallback_sources", "n_items", "n_sources", "converged"}。

    確定性：同輸入必回相同浮點結果（見模組 docstring）。
    """
    # 確定性：item 與 source 一律先 sorted 再迭代，避免 dict/set 迭代順序受
    # PYTHONHASHSEED 影響（(coin, window) 含字串，其 hash 會被隨機化）。
    items = sorted(votes.items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))
    sources: list[str] = sorted({s for _key, sv in items for s in sv})
    n_items = len(items)
    n_sources = len(sources)

    likelihoods: list[float] = []
    fallback_sources: set[str] = set()

    if n_items == 0 or n_sources == 0:
        return (
            {s: 0.5 for s in sources},
            {},
            {},
            {
                "likelihoods": likelihoods,
                "fallback_sources": sorted(fallback_sources),
                "n_items": n_items,
                "n_sources": n_sources,
                "converged": True,
            },
        )

    # 退化守門 1：來源數 < 標籤數（3）→ 無法估混淆矩陣，全部 0.5。
    if n_sources < N_LABELS:
        for s in sources:
            fallback_sources.add(s)
        return (
            {s: 0.5 for s in sources},
            {},
            {},
            {
                "likelihoods": likelihoods,
                "fallback_sources": sorted(fallback_sources),
                "n_items": n_items,
                "n_sources": n_sources,
                "converged": True,
            },
        )

    # 退化守門 2：只參與「rater 數 < min_raters_per_item」item 的來源 → 0.5。
    well_rated_items_of: dict[str, int] = defaultdict(int)
    for _key, sv in items:
        if len(sv) >= min_raters_per_item:
            for s in sv:
                well_rated_items_of[s] += 1
    for s in sources:
        if well_rated_items_of[s] == 0:
            fallback_sources.add(s)

    # ---- 初始化（確定性、禁用 random）----
    # 每 item 的多數票方向（平手取 LABELS 順序第一）——作為 DS 的「共識偽真值」
    # 起點，並據此初始化 π 與 CM。注意：naive 均勻 CM（1/N）對「均衡多類別」資料
    # 是 EM 的**退化固定點**（uniform CM 下後驗=π、M-step 又把 CM 拉回邊際分布，
    # 永遠逃不出），導致可靠度全數塌成 0.5 / 崩潰。因此改用確定性的「多數票 warm
    # start」：每來源 CM 對角 = 其與所屬 item 多數票的**一致率**（其餘均分 off-diag）。
    # 這仍完全確定性（sorted + fsum，無 random），且能正確啟動 EM 收斂到真混淆矩陣。
    def _majority(sv: dict[str, str]) -> int:
        counts = [0] * N_LABELS
        for lab in sv.values():
            if lab in _IDX:
                counts[_IDX[lab]] += 1
        return max(range(N_LABELS), key=lambda i: (counts[i], -i))

    prior_counts = [0.0] * N_LABELS
    src_agree: dict[str, list[float]] = {s: [0.0, 0.0] for s in sources}
    for _key, sv in items:
        best = _majority(sv)
        prior_counts[best] += 1.0
        for s, lab in sv.items():
            src_agree[s][1] += 1.0
            if lab in _IDX and _IDX[lab] == best:
                src_agree[s][0] += 1.0
    denom = math.fsum(prior_counts)
    pi = [c / denom if denom > 0 else 1.0 / N_LABELS for c in prior_counts]

    cm: dict[str, list[list[float]]] = {}
    for s in sources:
        agreed = src_agree[s][0]
        total = src_agree[s][1]
        a = (agreed / total) if total > 0 else 1.0 / N_LABELS
        a = max(min(a, 1.0 - 1e-9), 1e-9)
        off = (1.0 - a) / (N_LABELS - 1)
        cm[s] = [[a if t == k else off for k in range(N_LABELS)] for t in range(N_LABELS)]

    log_pi = [_safe_log(p) for p in pi]
    prev_L: float | None = None
    posterior: dict[tuple, list[float]] = {}
    converged = False

    for _it in range(max(1, int(n_iter))):
        # E-step：對每 item 算 true-label 後驗，並累積 M-step 的分子。
        new_pi_num = [0.0] * N_LABELS
        new_cm_num: dict[str, list[list[float]]] = {
            s: [[0.0] * N_LABELS for _ in range(N_LABELS)] for s in sources
        }
        item_loglik = 0.0

        for key, sv in items:
            logp = [log_pi[t] for t in range(N_LABELS)]
            for s, lab in sv.items():
                k = _IDX.get(lab)
                if k is None:
                    continue
                for t in range(N_LABELS):
                    logp[t] += _safe_log(cm[s][t][k])
            m = max(logp)
            exps = [math.exp(lp - m) for lp in logp]
            Z = math.fsum(exps)
            post = [e / Z for e in exps]
            posterior[key] = post
            item_loglik += math.log(Z) + m
            for t in range(N_LABELS):
                new_pi_num[t] += post[t]
            for s, lab in sv.items():
                k = _IDX.get(lab)
                if k is None:
                    continue
                for t in range(N_LABELS):
                    new_cm_num[s][t][k] += post[t]

        # M-step：用後驗重估 π 與 CM（全部 fsum 歸一化，確定性）。
        total = math.fsum(new_pi_num)
        pi = [x / total if total > 0 else 1.0 / N_LABELS for x in new_pi_num]
        for s in sources:
            new_cm = []
            for t in range(N_LABELS):
                row = new_cm_num[s][t]
                rdenom = math.fsum(row)
                new_cm.append(
                    [row[k] / rdenom if rdenom > 0 else 1.0 / N_LABELS for k in range(N_LABELS)]
                )
            cm[s] = new_cm
        log_pi = [_safe_log(p) for p in pi]

        likelihoods.append(item_loglik)
        # 單調不減斷言（容差 tol）：EM 數學上保證 likelihood 不降；若因數值
        # 地板/浮點誤差跌破容差，這是實作 bug，直接 raise 而非靜默吞掉。
        if prev_L is not None and item_loglik < prev_L - tol:
            raise AssertionError(
                f"DS EM likelihood 非單調不減：{prev_L!r} -> {item_loglik!r}"
            )
        if prev_L is not None and abs(item_loglik - prev_L) < tol:
            converged = True
            break
        prev_L = item_loglik

    # ---- 可靠度單分數 ----
    reliability: dict[str, float] = {}
    for s in sources:
        if s in fallback_sources:
            reliability[s] = 0.5
            continue
        diag = [cm[s][t][t] for t in range(N_LABELS)]
        accuracy = math.fsum(diag) / N_LABELS
        off = [cm[s][t][k] for t in range(N_LABELS) for k in range(N_LABELS) if t != k]
        confusion_err = max(off) if off else 0.0
        skill = accuracy - confusion_err
        # r = 0.5 + 0.5*(skill - 0.5)*2，飽和映射到 [0, 1]。
        r = 0.5 + 0.5 * (skill - 0.5) * 2
        reliability[s] = max(0.0, min(1.0, r))

    return (
        reliability,
        cm,
        posterior,
        {
            "likelihoods": likelihoods,
            "fallback_sources": sorted(fallback_sources),
            "n_items": n_items,
            "n_sources": n_sources,
            "converged": converged,
        },
    )
