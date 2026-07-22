# Spec：References 狀態 truth audit (#384)

> Issue: #384
> Size: S, good first issue

---

## Requirements

對照 devlog references.html，逐項確認實際程式碼狀態。

## Tasks
- [x] 讀取 references.html 所有方法論
- [x] 比對 src/ 實際實作狀態
- [x] 產出 docs/audit/REFERENCES-TRUTH-AUDIT.md
- [x] PR #387
- [x] 補上可重跑 checker：`scripts/check_references_truth_audit.py`
- [x] 補上 focused regression：`tests/test_references_truth_audit.py`

## 驗收
- [x] 每項標明：已實作/研究中/未實作/已排除
- [x] 含程式碼位置引用
- [x] HOYA BIT live、AgentCore routing、RAG、manipulation detection、台灣監管來源、Production Deploy `.disabled` 狀態皆有自動檢查
