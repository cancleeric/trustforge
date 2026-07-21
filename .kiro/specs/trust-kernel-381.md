# Spec：Trust Kernel 實體切割 (#381) — v2

> Issue: #381 (re-opened)
> 前版（facade）被否定：re-export 不等於邊界切割

---

## Requirements

### R1: 版本化 Kernel input/output contract
- 定義 `KernelInput`（Evidence list + PIT timestamp）
- 定義 `KernelOutput`（score, confidence, abstain, reason_codes）
- contract 版本化（KERNEL_CONTRACT_VERSION）

### R2: Kernel 純記憶體執行
- 不得 import: os, boto3, requests, web, skills, upgrade, deploy
- 可用純 fixture 驗證，無需網路/AWS/filesystem
- import-boundary AST test

### R3: 邊界測試
- 現有 scoring/regression/adversarial tests 全通過
- 新增 kernel-boundary contract test

---

## Design

```
KernelInput → [trust/kernel.py] → KernelOutput
              ↑ 只接受已標準化的 Evidence/Claim
              ↑ 不依賴 IO/LLM/cache/env
```

## Tasks
- [ ] 定義 KernelInput + KernelOutput dataclass
- [ ] 實作純計算 kernel 函式
- [ ] import-boundary AST test
- [ ] 合約測試
