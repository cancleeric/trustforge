# Competitive Whitespace — TrustForge 差異化論述

> 誠信紅線：競品功能敘述基於官方公開資訊，實事求是，不貶低競品「不存在」的功能。

## 核心差異化（一句話）

現有競品**給你答案、給你分數**；TrustForge 給你**答案背後每一條可點回原始來源的
證據，以及多源背離偵測**——一個可溯源、防 cross-source conflation 的信任驗證層。

## 現有競品事實基礎（官方公開功能）

| 競品 | 公開定位 | 給分數？ | 可溯源證據鏈？ | cross-source conflation 防禦？ |
|------|----------|----------|----------------|-------------------------------|
| Nansen | Smart Money / Token God Mode（鏈上地址標籤＋組合風險） | 是（標籤/風險分） | 否（標籤結論，無逐條來源追溯） | 否 |
| LunarCrush | Galaxy Score / AltRank（社群情緒統一分數） | **是（Galaxy Score）** | 否 | 否 |
| Arkham | 實體去匿名化（Intel Exchange） | 是（實體評分） | 部分（鏈上實體歸屬） | 否 |
| Messari | AI 全源引註研究 | 是 | 部分（研究報告級） | 否 |
| Glassnode | 可回測 point-in-time 鏈上指標 | 是（指標） | 否（指標非多源結論追溯） | 否 |

> ⚠️ 曾被寫成「Nansen/LunarCrush/Arkham 都**沒有**統一信任分數」——**此說法錯誤**
> （LunarCrush 明確有 Galaxy Score），已刪除。正確論述鎖定在「**無可溯源證據鏈**」。

## 空白地帶論述（對評審）

- 所有 incumbent 都把多源資訊**壓縮成一個結論或一個分數**，使用者無法審計
  「這條結論來自哪、有無來源互相矛盾」。
- TrustForge 反其道：保留並視覺化**證據鏈本身**——來源 badge、同意／矛盾計數、
  分層資訊完整度，且評分由本 pipeline（AWS Bedrock）產生，反作弊。
- 這不與競品「搶分數」，而是補上他們都沒做的「**可審計信任層**」。

## 誠信邊界

- 不宣稱 TrustForge 預測價格或勝率（CONFORMAL-FINDING 已證 AUC≈0.49）。
- 定位嚴守「資訊完整度＋可溯源」，對應評分權重的主題契合／創意／商業三項。
