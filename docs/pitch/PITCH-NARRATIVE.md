# Pitch Narrative — 決賽逐字稿骨架

> 素材全部來自 docs/pitch/ACADEMIC-BACKING.md 與 COMPETITIVE-WHITESPACE.md，
> 每個數字皆可在該二檔查到真實來源。開場痛點 → 中段技術深度 → 結尾誠實定位。

## 開場（30s）— 痛點

「當你讓 LLM 替你整理加密市場資訊，你以為它在幫你做功課——但一項對 10 個商業
LLM、69,557 條引用的實測顯示，引用幻覺率高達 **11.4% 到 56.8%**（來源：arXiv:2603.03299）。
也就是說，超過一成、甚至近六成的引用，可能根本不存在。在加密市場做決策，這是不能接受的。」

## 中段（90s）— 我們做什麼 + 技術深度

「TrustForge 是一個多源資訊信任提煉 Agent。它不只給你一個分數，而是給你**每一條
結論背後可點回原始來源的證據鏈**。

我們知道一個業界長期忽略的失效模式——*cross-source conflation*：一條 claim 在某處
被支持，卻被歸因到錯誤來源（來源：ProvenanceGuard, arXiv:2606.18037）。我們的
Evidence Trail 設計就是針對這個模式：每條結論追溯原始來源、標記矛盾來源、並以分層
『資訊完整度』呈現，而非單一黑箱分數。

而且——評分由我們自己的 pipeline（AWS Bedrock 基礎模型）產生，不套用外部黑箱結論，
這本身就是反作弊設計。」

（demo：展示 Evidence Trail Cards + 分層資訊完整度 UI，對應已 merge develop 的 #171-1 / #171-2）

## 結尾（30s）— 誠實定位

「我們誠實說：TrustForge 不預測價格、不保證勝率——我們的驗證數據顯示純預測 AUC 約 0.49，
接近隨機。但我們解決的是更根本的問題：**讓你看到資訊從哪來、有多完整、有無來源互相
矛盾**。在 HOYA BIT 這樣的多源加密資訊場景，可溯源的信任層，是現有 Nansen / LunarCrush /
Arkham 都沒補上的空白。

這就是 TrustForge：incumbent 給你答案，我們給你答案背後的證據。」

## 預期追問應答（誠信版）

- **「你的 trust score 準嗎？」** → 定位是「資訊完整度＋可溯源」，非預測；AUC≈0.49 已誠實自曝。
- **「憑什麼說競品沒做？」** → 他們有分數（LunarCrush Galaxy Score），但無可溯源證據鏈／
  cross-source conflation 防禦。
- **「模型合規？」** → 僅用 AWS Bedrock 基礎模型；評分本 pipeline 產生，反作弊。
