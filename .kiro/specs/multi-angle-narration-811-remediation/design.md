# #811 設計：事實受限的 Bedrock 敘事

## Narration facts contract

在 multi-angle module 產生 immutable `NarrationFacts`，只包含：snapshot ID、per-angle mode/question、consensus、direction divergences、completeness gaps、evidence overlaps、independence 與 required limits。其 `independent_cross_validation` 為 derived boolean，僅當 ratio 大於零且有 unique source IDs 才為 true。

## Prompt and fallback

Prompt 以固定段落約束模型：

- 只能改寫 supplied facts，不得判斷或補充市場事實。
- 分別描述三種量測，禁止用 overlap 代替 divergence。
- `independent_cross_validation=false` 時必須保留「沒有獨立交叉佐證」限制，禁止任何相反語意。
- 不提供投資建議。

fallback 由同一 `NarrationFacts` template 產生，確保 Bedrock 不可用時仍符合同一語意規則。

## Runtime

保留 env flag 和 fail-soft；Bedrock 呼叫只可走既有 wrapper 與預算 gate。保存 `narration_source`（bedrock/fallback）、facts digest、snapshot ID 和 execution-log correlation，讓真實 payload 輸出可稽核。

## Acceptance

從 #809 acceptance export 載入 production report，產生 facts digest，檢查 fallback 一定通過。若可用且 budget 允許，以相同 facts 做一次 Bedrock verification；無法 live 呼叫需記為未完成 live verification，而非以 mock 成功替代。
