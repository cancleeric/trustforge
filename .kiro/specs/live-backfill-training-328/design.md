# Design

## 架構變更

```
BackfillWorker
  ├── mode=offline（現有，用 BedrockClient(offline=True)）
  └── mode=live（新增，用 BedrockClient(offline=False)）
        ├── _build_day_snapshot()（同現有）
        ├── replay_snapshot() 改用 run_agent_pipeline(client=BedrockClient(offline=False))
        ├── _persist_to_trust_history()（同現有）
        └── _persist_to_training_data()（新增，append JSONL）

run_analysis_flow.py daemon loop
  └── refresh_once() 後新增：_write_snapshots()
```

## CLI 擴充

```bash
# live 模式回填（抽樣 200 天/幣）
python -m trustforge.cli backfill start --mode live --sample 200 --coin BTC,ETH,SOL,BNB,XRP

# 匯出模型 artifacts
python -m trustforge.cli export-model --out out/model-artifacts/

# 匯入模型 artifacts（新環境）
python -m trustforge.cli import-model --from out/model-artifacts/
```
