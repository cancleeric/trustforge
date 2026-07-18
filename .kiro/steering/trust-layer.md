---
inclusion: fileMatch
fileMatchPattern: "src/trustforge/trust/**"
---

# Trust Layer 開發規範

本文件在修改 `src/trustforge/trust/` 下任何檔案時自動載入。

## 信任評分公式

```
TrustScore = w_src  · SourceReputation        (0.50)
           + w_corr · CrossSourceCorroboration (0.25)
           + w_rec  · RecencyDecay             (0.15)
           − w_manip · ManipulationPenalty     (0.40)
```

權重定義在 `scoring.py::DEFAULT_WEIGHTS`，修改前須經團隊討論。

## 來源基礎信譽

| kind | 信譽 | 說明 |
|------|------|------|
| price / onchain | 0.95 | 客觀事實 |
| regulatory | 0.90 | 官方公告 |
| hoyabit | 0.85 | 交易所一手行情 |
| price_live | 0.90 | CoinGecko 現價 |
| news | 0.65 | 新聞媒體 |
| sentiment / dev_activity | 0.50 | 輔助訊號 |
| social | 0.35 | 匿名社群（最低） |

## 開發鐵則

1. **可解釋性優先**：評分核心保持可解釋、可審查，不全交 LLM 黑箱。claim 抽取可用 LLM，但最終分數必須由確定性公式算出。
2. **不造假**：信任分缺失 = 未評估，絕不填 0 假裝安全。`None`/省略鍵 = 沒算過。
3. **fail-safe 保守**：判不準一律回 neutral，寧可漏抓不可誤判。操縱偵測也是——誤扣比漏扣嚴重。
4. **跨源獨立性**：佐證只計「獨立」來源。同一 publisher 不同大小寫/空白變體要正規化為同一源。
5. **stance 分類的 budget 限制**：O(n²) 配對有硬上限（DEFAULT_STANCE_PAIR_BUDGET=40），超出降級 neutral 不呼叫。
6. **時間預算意識**：任何新增的 Bedrock 呼叫都要考慮 15 分鐘執行窗口。`STANCE_TIME_RESERVE_SEC` 必須 >= 單次呼叫最壞耗時。

## 測試要求

- 修改評分邏輯後必須確認 `tests/test_trust_scoring.py` 通過
- 新增評分維度須附帶測試案例
- 操縱偵測的測試要涵蓋「正常文字不被誤判」的負面案例

## Dawid-Skene 動態信譽

`trust/dawid_skene.py` 實作 EM 迭代的來源信譽動態學習。修改時注意：
- 迭代有 clamp 下限（`_reputation_floor`），防止信譽蒸發到 0
- 收斂門檻與最大迭代次數要保持合理（過多迭代吃時間預算）
