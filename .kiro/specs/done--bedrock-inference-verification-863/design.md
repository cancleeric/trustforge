# 設計：Bedrock 推理服務開啟與 claim_id 溯源行文驗證

> Issue: #863

## 架構決策

### AD-1: 驗證腳本為獨立新增檔案，不動核心

所有驗證邏輯放在 `scripts/` 目錄，只讀取既有模組的公開介面：

```
scripts/
├── verify_bedrock.py       # 環境/IAM 驗證
├── smoke_test_bedrock.py   # 最小推理 smoke test
└── verify_traceability.py  # claim_id 溯源完整性驗證
```

理由：
- 核心邏輯（`bedrock.py` / `orchestrator.py` / `scoring.py`）已穩定，不應為了驗證而修改
- 驗證腳本可獨立跑、獨立刪，不影響 pipeline 行為
- 證據產出到 `out/` 目錄（已在 `.gitignore`），不污染版控

### AD-2: 證據格式——結構化 JSON

所有驗證結果輸出為 JSON，便於程式化重現與 CI 檢查：

```json
// out/bedrock_smoke_test.json
{
  "timestamp": "2026-07-29T12:00:00Z",
  "region": "ap-southeast-2",
  "model_id": "au.anthropic.claude-...",
  "stance_model_id": "au.anthropic.claude-haiku-...",
  "tests": [
    {
      "name": "complete",
      "status": "success",
      "elapsed_sec": 2.3,
      "input_tokens": 42,
      "output_tokens": 128,
      "response_length": 256
    },
    {
      "name": "classify_stance",
      "status": "success",
      "elapsed_sec": 0.8,
      "result": "entailment"
    }
  ],
  "overall": "pass"
}
```

```json
// out/bedrock_traceability.json
{
  "timestamp": "2026-07-29T12:05:00Z",
  "coin": "BTC",
  "model_id": "...",
  "claim_ids_in_narrative": ["price_btc_001#llm0", "news_btc_002#llm1", ...],
  "claim_ids_count": 7,
  "min_required": 5,
  "all_traceable": true,
  "narrative_has_layers": true,
  "offline_markers_absent": true,
  "overall": "pass"
}
```

### AD-3: Fixture 資料策略

驗證用的 fixture 使用 `data/` 目錄既有的 HOYA BIT OHLCV 資料搭配合成 news/onchain Document：

```python
# verify_traceability.py 中的 fixture 建構
def _build_fixture_docs(coin: str = "BTC") -> list[Document]:
    """建構最小可驗證 fixture（≥5 筆，涵蓋 3 種 kind）。
    
    - price: 取 data/ 目錄最近 5 日 OHLCV
    - news: 合成 2 筆具代表性的新聞主張
    - onchain: 合成 1 筆鏈上指標
    """
```

理由：
- 不依賴外部 API 可達性（news/social 連接器可能暫時不可用）
- 使用真實 HOYA BIT 價格資料確保 claim 抽取有意義
- 合成文本控制明確方向，便於驗證行文層次

### AD-4: 降級驗證——故意觸發失敗路徑

```python
# 在 verify_traceability.py 中
def _test_degraded_mode():
    """故意設錯 model_id，驗證降級行為。"""
    bad_config = BedrockConfig()
    bad_config.model_id = "nonexistent-model-id-for-testing"
    client = BedrockClient(config=bad_config)
    # 執行 pipeline → 預期不中斷、報告含降級標記
```

### AD-5: 護欄驗證——讀取 ledger 與 log

不重新實作護欄邏輯，而是在 pipeline 跑完後檢查其副作用：

```python
def _verify_guardrails(log: ExecutionLog):
    """驗證護欄在線上模式的生效證據。"""
    # 1. execution_log 至少 2 筆 bedrock.complete 事件
    bedrock_events = [e for e in log.events if e["tool"] == "bedrock.complete"]
    assert len(bedrock_events) >= 2
    
    # 2. llm.cost 事件有 token 數與估算成本
    cost_events = [e for e in log.events if e["tool"] == "llm.cost"]
    assert all(e["params"]["cost_usd"] > 0 for e in cost_events)
    
    # 3. ledger 有新增 run 記錄
    from trustforge.ledger import daily_cost_usd
    assert daily_cost_usd() > 0  # 今日已有花費
```

### AD-6: claim_id 溯源驗證邏輯

```python
import re

_CLAIM_ID_RE = re.compile(r"[\w\-]+#(?:llm)?\d+")

def _verify_claim_id_traceability(report: Report, evidence: list[Evidence]):
    """驗證 narrative 中引用的 claim_id 可追溯到 evidence。"""
    # 1. 從 narrative 文本中提取所有 claim_id 引用
    narrative_text = "\n".join(report.inferences)
    cited_ids = set(_CLAIM_ID_RE.findall(narrative_text))
    
    # 2. 建立 evidence 中所有 claim_id 的全集
    evidence_claims = set()
    for ev in evidence:
        if ev.related_claim:
            evidence_claims.add(ev.related_claim)
    # 也檢查 cross_source_signal 的 supporting_claim_ids
    if report.cross_source_signal:
        for cid in report.cross_source_signal.get("supporting_claim_ids", []):
            evidence_claims.add(cid)
    
    # 3. 驗證：cited ⊆ traceable
    # 4. 驗證：|cited| >= 5
```

## 安全考量

- 所有輸出不含 credential/token/secret（驗證腳本只輸出模型 ID、區域、token 計數等非敏感欄位）
- `verify_bedrock.py` 的 IAM 權限檢查使用 `get-caller-identity`（STS），不記錄 ARN 中的帳號 ID
- 即使在 `out/` 目錄（gitignored），也不寫入 session token 或 secret key

## 測試策略

本 issue 本身是驗證性質——「測試的測試」。具體做法：

1. **單元測試**（`tests/test_verify_scripts.py`）：
   - 測 `_CLAIM_ID_RE` 正則能正確匹配各種 claim_id 格式
   - 測 fixture 建構函式產出正確數量/kind 的 Document
   - 測降級偵測邏輯能辨識離線字樣

2. **整合驗證**（手動執行）：
   - `python scripts/verify_bedrock.py` — 環境檢查
   - `python scripts/smoke_test_bedrock.py` — 推理 smoke test
   - `python scripts/verify_traceability.py` — 完整 pipeline + 溯源

3. **CI 可選閘門**：
   - 環境變數未設定時 gracefully skip（不阻擋離線 CI）
   - 設定 `TRUSTFORGE_ONLINE_VERIFY=1` 時才執行線上驗證

## 成本估算

| 步驟 | 預估 token 用量 | 預估成本 |
|------|----------------|---------|
| Smoke test complete() | ~200 in / ~200 out | < $0.002 |
| Smoke test classify_stance() | ~300 in / ~32 out | < $0.001 |
| Full pipeline (5 docs) | ~2000 in / ~1024 out | < $0.008 |
| **合計** | | **< $0.011** |

遠低於 `$3/day` 每日預算上限。
