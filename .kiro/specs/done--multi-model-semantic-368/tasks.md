# Tasks：Multi-model Semantic Direction #368

- [x] PR #370 新增 `semantic_direction.py`
- [x] 依 price/news/onchain/sentiment 提供來源專屬 prompt
- [x] Bedrock 結構化 direction/confidence/reasoning 解析
- [x] 多來源 confidence-weighted aggregation 與 graceful degradation
- [x] `semantic_direction` 接入 orchestrator 與 direction resolution
- [x] Dawid-Skene canonical implementation 保持可用
- [x] semantic direction、pre-score resolution 與 degradation tests
- [x] Issue #368 closed

原 tasks 中的 `semantic_analyzer.py`、`multi_model_voter.py` 是早期檔名草案；
最終等價能力收斂於 `semantic_direction.py` 與 `direction_resolution.py`。
