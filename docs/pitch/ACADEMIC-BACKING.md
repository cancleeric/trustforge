# Academic Backing — TrustForge Pitch 素材

> 誠信紅線：下表每一條宣稱都對應**經獨立 webfetch 實查證的真實文獻**（arXiv 原始頁）。
> 任何查不到 / 主題不符的素材一律剔除，絕不編造。

## 模組 A｜痛點數據：LLM 引用幻覺率

- **數據**：10 個商業部署 LLM、橫跨 4 個學術領域、共 69,557 條引用實測，
  引用幻覺率（phantom citation）介於 **11.4% – 56.8%**（五倍差距，受模型／領域／
  prompt 框架強烈影響）。
- **來源（真實，已查證）**：Naser, M.Z. (2026). *How LLMs Cite and Why It Matters:
  A Cross-Model Audit of Reference Fabrication in AI-Assisted Academic Writing and
  Methods to Detect Phantom Citations*. **arXiv:2603.03299**.
  https://arxiv.org/abs/2603.03299
- **pitch 用法**：開場痛點頁——「當 LLM 替你整理資訊，超過一成到近六成的引用
  可能是不存在的。加密市場決策不能建立在不實來源上，這就是 TrustForge 信任提煉層
  存在的根本原因。」

## 模組 B｜失效模式：cross-source conflation（我們的防禦對象）

- **概念**：tool-using LLM agent 用 MCP 從異質來源（搜尋／API／資料庫）回答時，
  標準事實性指標只檢查「答案是否被（某處）證據支持」，卻漏掉一種 provenance 失效：
  **一條 claim 在某處被支持，卻被歸因到錯誤來源**——作者稱之 *cross-source conflation*。
- **來源（真實，已查證）**：Alvarez, A., Rajan, S., Mugel, S., Orús, R. (2026).
  *ProvenanceGuard: Source-Aware Factuality Verification for MCP-Based LLM Agents*.
  **arXiv:2606.18037**. https://arxiv.org/abs/2606.18037
- **pitch 用法**：中段技術深度頁——主動點名「我們知道 cross-source conflation 這個
  失效模式，我們的 Evidence Trail 設計就是對每一條結論追溯到原始來源、並標記矛盾
  來源，防禦這個模式」，展現技術深度而非功能列表。

## ⛔ 明列「不可用」清單（曾險些誤用，已剔除）

| 素材 | 狀態 | 處置 |
|------|------|------|
| "Cited but Not Verified" | ❌ 不存在此標題論文 | 禁止引用；幻覺率數字請改用 arXiv:2603.03299 |
| Pub-Guard-LLM（arXiv:2502.15429） | ⚠️ 真實，但主題是「偵測被撤銷的生物醫學論文」 | 與加密信任敘事無關，**禁用於分層評分背書** |
| 「競品無統一分數」 | ❌ 事實錯誤（LunarCrush 有 Galaxy Score） | 改為「無可溯源證據鏈」，見 COMPETITIVE-WHITESPACE.md |

## 驗收標準

- [ ] 每個數字都有可點擊真實來源（上方 arXiv 連結）。
- [ ] 無 "Cited but Not Verified"、無 Pub-Guard-LLM 分層評分背書、無「競品無統一分數」。
- [ ] 可直接剪進 pitch 逐字稿與 1 頁 deck。
