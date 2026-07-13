# TrustForge — Pitch 1-Pager（決賽直接用）

> 誠信紅線：以下每個數字都有可點擊真實來源（arXiv）。無捏造、無「競品無分數」類錯誤。
> 定位嚴守「資訊完整度＋可溯源」，不宣稱市場預測力。

---

## 一句話定位
incumbent（Nansen / LunarCrush / Arkham）給你**答案與分數**；TrustForge 給你**答案背後每一條可點回原始來源的證據鏈，以及多源背離偵測**。

## 痛點（開場 30s）
當你讓 LLM 替你整理加密市場資訊，一項對 **10 個商業 LLM、69,557 條引用**的實測顯示，
引用幻覺率高達 **11.4%–56.8%**（來源：arXiv:2603.03299，Naser 2026）。超過一成、近六成的
引用可能根本不存在——在 HOYA BIT 這樣的多源加密資訊場景，這不能接受。

## 我們做什麼（中段 90s）
多源資訊信任提煉 Agent：
- 每條結論**追溯到原始來源**，標記「支持/矛盾」來源計數（Evidence Trail Cards）。
- **分層「資訊完整度」**呈現（高/中/低/棄權），而非單一黑箱分數。
- 評分由**本 pipeline（AWS Bedrock 基礎模型）**產生，不套用外部黑箱結論 → 反作弊。
- 主動防禦業界忽略的失效模式 **cross-source conflation**（claim 被支持卻歸因錯來源，
  來源：ProvenanceGuard, arXiv:2606.18037, Alvarez 2026）。

## 差異化（競品空白）
現有競品**都有各自的統一分數**（LunarCrush 即 Galaxy Score），但他們都**不提供可溯源、
防 cross-source conflation 的證據鏈**。TrustForge 補上的正是這層「可審計信任層」。

## 誠實收尾（結尾 30s）
我們**不預測價格、不保證勝率**——內部驗證顯示純預測 AUC≈0.49（近隨機）。我們解決更根本的：
**讓你看到資訊從哪來、有多完整、有無來源互相矛盾**。可溯源的信任層，是現有整合型競品都沒做的空白。

## 預期追問（誠信版應答）
- 「trust score 準嗎？」→ 定位是資訊完整度＋可溯源，非預測；AUC≈0.49 已誠實自曝。
- 「憑什麼說競品沒做？」→ 他們有分數，但無可溯源證據鏈／cross-source conflation 防禦。
- 「模型合規？」→ 僅用 AWS Bedrock 基礎模型；評分本 pipeline 產生，反作弊。

## 來源（均可點開）
- arXiv:2603.03299 — https://arxiv.org/abs/2603.03299 （引用幻覺率 11.4%–56.8%）
- arXiv:2606.18037 — https://arxiv.org/abs/2606.18037 （ProvenanceGuard / cross-source conflation）
- arXiv:2502.15429 — https://arxiv.org/abs/2502.15429 （Pub-Guard-LLM，**生醫撤稿偵測，本題禁用背書**）

---
詳細素材見同目錄：ACADEMIC-BACKING.md / COMPETITIVE-WHITESPACE.md / PITCH-NARRATIVE.md
