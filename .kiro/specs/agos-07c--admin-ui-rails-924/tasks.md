# 實作任務：Agent OS Admin UI Rails

> Issue: #924 | Epic: #914

## Task 1: 建立 TypeScript types 與 API hooks

- [ ] 建立 `frontend/src/lib/agosTypes.ts`
  - MemoryItem, SkillItem, ToolItem, ContextManifest interfaces
  - Badge variant types
  - Query params interfaces
- [ ] 建立 `frontend/src/hooks/useAdminAgos.ts`
  - useAdminMemories(params)
  - useAdminSkills(params)
  - useAdminTools(params)
  - useAdminContext(runId)
  - Authorization header handling

## Task 2: 建立共用 Admin 元件

- [ ] 建立 `frontend/src/components/admin/AgosBadge.tsx`
  - Variants: historical, candidate, trusted, proposal, risk-*
  - aria-label support
- [ ] 建立 `frontend/src/components/admin/AgosTokenBudgetBar.tsx`
  - Progress bar with color thresholds
  - Accessible role=progressbar
- [ ] 建立 RailWrapper（loading/error/empty/unauthorized states）

## Task 3: 實作 Memory Rail

- [ ] 建立 `frontend/src/components/admin/AgosMemoryRail.tsx`
- [ ] Desktop: table with kind, provider, eligibility, lineage, timestamps
- [ ] Mobile: card layout
- [ ] Evidence eligibility badge（gray/yellow/green）
- [ ] Lineage info（rank, reason）

## Task 4: 實作 Skill Rail

- [ ] 建立 `frontend/src/components/admin/AgosSkillRail.tsx`
- [ ] Revision hash（truncated with copy button）
- [ ] Family badge
- [ ] Risk classification badge
- [ ] Lifecycle status
- [ ] Dependencies list
- [ ] Frozen state indicator

## Task 5: 實作 Tool Rail

- [ ] 建立 `frontend/src/components/admin/AgosToolRail.tsx`
- [ ] Side-effect class badge
- [ ] Approval indicator
- [ ] Status badge（success=green, failed=red, timeout=orange, rejected=gray）
- [ ] Input/output hash（truncated）
- [ ] Evidence class

## Task 6: 實作 Context Rail

- [ ] 建立 `frontend/src/components/admin/AgosContextRail.tsx`
- [ ] Token budget bar
- [ ] Included refs count by type
- [ ] Excluded refs list with reason badges
- [ ] Content hash display

## Task 7: 組裝 Admin Page

- [ ] 建立 `frontend/src/pages/AdminAgosPage.tsx`
- [ ] Tab navigation（Memory / Skill / Tool / Context）
- [ ] Run ID selector or input
- [ ] 路由註冊（`/admin/agos`，不加入 public nav）

## Task 8: 測試

- [ ] 建立 `frontend/src/pages/AdminAgosPage.test.tsx`
- [ ] 測試 4 tabs 渲染
- [ ] 測試各 rail 正確顯示 badges
- [ ] 測試 loading/error/empty/unauthorized states
- [ ] 建立 `frontend/src/components/admin/AgosBadge.test.tsx`
- [ ] 測試 badge variants 與 aria-label
- [ ] 確認 lint / build 通過
- [ ] 確認 pre-push 通過
