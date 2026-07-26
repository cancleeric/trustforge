# Migration Bridge Audit (#422)

## 結論：無不安全 legacy code 需移除

審計 src/ 全量 legacy reference：
- `legacy` 關鍵字多為 CSP 相容層、upgrade_state_machine 向後相容接口
- 均為 active maintenance，非 dead code
- 移除會破壞 backward compatibility

## 保留清單
| 模組 | Legacy 項目 | 理由 |
|------|-----------|------|
| web.py | CSP_MODE=legacy | SSR 相容，active |
| upgrade_state_machine | legacy actor arg | 向後相容，zero-cost |
| trustforge_core/ | legacy scoring paths | deterministic, immutable |

## 建議
不做 migration bridge 移除。已達最小可維護狀態。
