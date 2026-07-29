# 實作任務：Agent OS Admin UI Rails

> Issue: #924 | Epic: #914

## Task 1: 建立 TypeScript types 與 API hooks

- [x] 建立 `frontend/src/lib/agosTypes.ts`
  - MemoryItem, SkillItem, ToolItem, ContextManifest interfaces
  - Badge variant types
  - Query params interfaces
- [x] Superseded: no separate `useAdminAgos.ts`; `AdminAgosPage.tsx` owns the
  same admin fetch/state behavior
  - useAdminMemories(params)
  - useAdminSkills(params)
  - useAdminTools(params)
  - useAdminContext(runId)
  - Authorization header handling

## Task 2: 建立共用 Admin 元件

- [x] 建立 `frontend/src/components/admin/AgosBadge.tsx`
  - Variants: historical, candidate, trusted, proposal, risk-*
  - aria-label support
- [x] 建立 `frontend/src/components/admin/AgosTokenBudgetBar.tsx`
  - Progress bar with color thresholds
  - Accessible role=progressbar
- [x] Superseded: loading/error/empty/unauthorized states are implemented in
  the page/rails without a separate `RailWrapper`

## Task 3: 實作 Memory Rail

- [x] 建立 `frontend/src/components/admin/AgosMemoryRail.tsx`
- [x] Desktop: table with kind, provider, eligibility, lineage, timestamps
- [x] Mobile: card layout
- [x] Evidence eligibility badge（gray/yellow/green）
- [x] Lineage info（rank, reason）

## Task 4: 實作 Skill Rail

- [x] 建立 `frontend/src/components/admin/AgosSkillRail.tsx`
- [x] Revision hash（truncated with copy button）
- [x] Family badge
- [x] Risk classification badge
- [x] Lifecycle status
- [x] Dependencies list
- [x] Frozen state indicator

## Task 5: 實作 Tool Rail

- [x] 建立 `frontend/src/components/admin/AgosToolRail.tsx`
- [x] Side-effect class badge
- [x] Approval indicator
- [x] Status badge（success=green, failed=red, timeout=orange, rejected=gray）
- [x] Input/output hash（truncated）
- [x] Evidence class

## Task 6: 實作 Context Rail

- [x] 建立 `frontend/src/components/admin/AgosContextRail.tsx`
- [x] Token budget bar
- [x] Included refs count by type
- [x] Excluded refs list with reason badges
- [x] Content hash display

## Task 7: 組裝 Admin Page

- [x] 建立 `frontend/src/pages/AdminAgosPage.tsx`
- [x] Tab navigation（Memory / Skill / Tool / Context）
- [x] Run ID selector or input
- [x] 路由註冊（`/admin/agos`，不加入 public nav）

## Task 8: 測試

- [x] 建立 `frontend/src/pages/AdminAgosPage.test.tsx`
- [x] 測試 4 tabs 渲染
- [x] 測試各 rail 正確顯示 badges
- [x] 測試 loading/error/empty/unauthorized states
- [x] 建立 `frontend/src/components/admin/AgosBadge.test.tsx`
- [x] 測試 badge variants 與 aria-label
- [x] 確認 lint / build 通過
- [x] 確認 pre-push 通過

## Review gate

- [x] Browser Eye：desktop `1440x1000`、mobile `390x844`；四個 rails
  populated/empty、長內容與 scrolling、token/401、backend empty/404、
  horizontal overflow 均驗證通過
- [x] Eye CLI：exact PR range breaking changes `0 critical / 0 warning`

### HEAD evidence

Types, rails, token bar, page route, tab/run selection, badges, states, and
tests exist under `frontend/src/`. Browser Eye、Eye CLI、Codex adversarial
review 與 CPO exact-HEAD review 均完成。
