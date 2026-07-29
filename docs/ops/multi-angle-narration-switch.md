# 五方向分析 LLM 綜合敘述開關

五方向分析完成後，系統預設會把確定性 synthesis 交給 LLM 改寫一次；每份
五方向報告最多一次 narration 呼叫。LLM 失敗時仍回傳確定性摘要。

管理員可在 Admin 控制台的「Bedrock 真呼叫開關」旁即時開關
`multi_angle_narration_enabled`，不需重啟。實際呼叫必須同時滿足：

1. Admin narration 開關（未設定時預設 `true`）。
2. `TRUSTFORGE_MULTI_ANGLE_NARRATION` 未設定或精確為 `1`。
3. 全域 Bedrock 真呼叫開關、model pricing 與每日成本閘全部放行。

環境變數是 Admin 無法覆蓋的緊急阻斷層。`0`、明確空字串及任何非 `1`
值都 fail-closed；其中空字串與非法值會寫 warning log，避免設定錯誤造成
靜默關閉，`0` 則是合法的明確 OFF。修正環境變數後才可能恢復。Admin API
的 `effective` 與 `source` 會誠實顯示 env 或全域閘是否正在阻擋。

本 repository 目前沒有通用 `.env.example`／env sample；部署時請依本文件
設定，不要把空值當成「未設定」。若要沿用預設開啟，應完全移除該環境變數，
而不是留下 `TRUSTFORGE_MULTI_ANGLE_NARRATION=`。

## Shared budget reservation reconcile

使用 `TRUSTFORGE_BUDGET_GUARD_BACKEND=dynamodb` 時，provider timeout 或
ledger 寫入失敗若沒有 durable shared accounting receipt，系統會刻意保留
DynamoDB `reserved_total`，即使 process-local unledgered counter 已記錄也
不會釋放。這是 fail-closed：其他 instance 與重啟後的 instance 都必須繼續
看見該 reservation，避免重新 admission 造成超支。

此狀態會記 critical log，訊息包含 `等待 reconcile/manual`。值班人員必須先
核對 provider usage 與 durable ledger；確認實際成本已補入共享 ledger，或
確認 provider 未接受呼叫後，才可依 budget counter 的既有維運程序人工
reconcile `trustforge-budget-guard` 當日 item。不可只因服務重啟、或看到
process-local unledgered 紀錄就直接清除 shared reservation。

若 configured DynamoDB authority 在預留階段不可用，該次 narration 直接
fail-closed 離線，不會 fallback process-local。預留成功後 authority
provenance 會綁定到該次 attempt；即使運行中 env 從 `dynamodb` 改成
`local`（或反向切換），release 仍只作用於原本的 authority。
