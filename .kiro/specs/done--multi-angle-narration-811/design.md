# Multi-angle Narration 設計文件

## 流程

```
synthesize_angles() → MultiAngleReport (確定性)
       │
       ▼
narrate_synthesis(report, client, log)
       │
       ├── 離線 / env flag off → return report.synthesis_summary
       │
       ├── _bedrock_live_attempt(log) → live?
       │     ├── not live → return report.synthesis_summary
       │     └── live → 組裝 prompt → client.complete()
       │
       ├── Bedrock 成功 → return narration text
       └── Bedrock 失敗 → return report.synthesis_summary (降級)
```

## Prompt 設計

```python
SYSTEM = """你是 TrustForge 的分析敘事助手。
你的唯一工作是把已經算好的結構化分析結果用流暢的中文摘要描述。
你不可以自行發明任何交叉訊號、方向判斷或結論。
你只能敘述以下 JSON 資料中已存在的結論和數值。"""

PROMPT_TEMPLATE = """
以下是 {coin} 的五角度綜合分析結構化結果：

共識：{consensus}
加權信心：{consensus_confidence}
證據獨立性：{evidence_independence}

角度結果：
{angles_summary}

衝突清單：
{conflicts_summary}

限制：
{limits}

請用 2-3 句話摘要上述結果，語氣中性專業。不可添加任何原始資料中沒有的判斷。
"""
```

## 整合到 _maybe_trigger_synthesis

```python
# 在 synthesize_angles() 之後，存入 DB 之前
narration = None
if os.environ.get("TRUSTFORGE_MULTI_ANGLE_NARRATION") == "1":
    try:
        narration = narrate_synthesis(report, client, log)
    except Exception:
        pass  # fail-soft

# 存入 payload 時加上 narration 欄位
payload = report.to_dict()
if narration:
    payload["narration"] = narration
```

## 不做什麼

- 不改動 synthesize_angles() 的任何邏輯
- 不讓 LLM 影響 consensus / conflicts / agreement_matrix
- 不預設開啟（需顯式 env var）
