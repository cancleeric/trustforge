# Spec：Provider ports/adapters 接入 runtime (#386) — v2

> Issue: #386 (re-opened)
> 前版問題：Protocol 定義了但 production pipeline 仍直接 import Bedrock

---

## Requirements

### R1: 每個 registry key 有 builtin adapter
LLMProvider / MemoryProvider / Evaluator / Gateway / ObservabilitySink / UpgradeStore

### R2: get_provider() 真正影響執行路徑
切換 provider → 實際 invoked adapter 改變（spy/fake 測試）

### R3: AgentCore adapter 有真實契約測試
宣稱 agentcore enabled → 真的走 agentcore bridge，不是靜默 fallback

### R4: 失敗不靜默
初始化失敗 → 明確錯誤，不靜默顯示 agentcore enabled
