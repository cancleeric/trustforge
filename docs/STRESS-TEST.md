# TrustForge P0-5 壓測結果

> 模式：**offline（樣本資料）** | 測試時間：2026-06-30T18:54:31Z
> 總執行時間：0.13s

> **offline 模式注意**：elapsed 為 pipeline 內部計時（含 regex fallback），**不含真實 Bedrock 網路延遲**。真實時間（含 LLM）請用 `--online` 旗標量測。

## 矩陣結果（5 幣 × 3 題型）

| 幣種/配對 | 題型 | elapsed(s) | evidence | 交付件 OK | PASS |
|-----------|------|-----------|---------|----------|------|
| BTC          | multi_source |      0.010 |        8 | OK | PASS |
| BTC          | hypothesis   |      0.005 |        8 | OK | PASS |
| ETH          | multi_source |      0.005 |        8 | OK | PASS |
| ETH          | hypothesis   |      0.005 |        8 | OK | PASS |
| SOL          | multi_source |      0.005 |        8 | OK | PASS |
| SOL          | hypothesis   |      0.005 |        8 | OK | PASS |
| BNB          | multi_source |      0.006 |        8 | OK | PASS |
| BNB          | hypothesis   |      0.006 |        8 | OK | PASS |
| XRP          | multi_source |      0.006 |        8 | OK | PASS |
| XRP          | hypothesis   |      0.005 |        8 | OK | PASS |
| BTC/ETH      | comparison   |      0.011 |       16 | OK | PASS |
| ETH/SOL      | comparison   |      0.010 |       16 | OK | PASS |
| SOL/BNB      | comparison   |      0.010 |       16 | OK | PASS |
| BNB/XRP      | comparison   |      0.010 |       16 | OK | PASS |
| XRP/BTC      | comparison   |      0.010 |       16 | OK | PASS |

**通過：15/15**

### 結論：全部 < 15 分鐘
（offline 模式：時間僅供正確性驗證，真實 Bedrock 時間需 `--online` 確認）

## 失敗降級驗證

| 案例 | PASS | 說明 |
|------|------|------|
| source_timeout_degradation | PASS | pipeline 正常完成；working docs=9；limits 含失敗來源=True |
| bedrock_offline_degradation | PASS | 降級模式成功；market_judgment 非空=True；bedrock 事件數=2（offline=regex fallback） |

**降級通過：2/2**

## 整體結論
- 所有矩陣案例通過：YES
- 所有降級驗證通過：YES
- 全部 < 15 分鐘
- 真實 Bedrock 時間：**需 --online 旗標 + BEDROCK_MODEL_ID/AWS_REGION env 量測**

---
*由 `scripts/stress_test.py` 自動產生。*