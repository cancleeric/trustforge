# 設計：Agent OS Admin UI Rails

> Issue: #924 | Epic: #914

## 架構決策

### AD-1: 獨立 Admin Page

新增 `frontend/src/pages/AdminAgosPage.tsx` — 單一頁面 4 個 tab（Memory / Skill / Tool / Context）。

Route: `/admin/agos`（不加入 public nav，僅 admin 知道路徑）。

### AD-2: 共用 Admin API Hook

```typescript
// frontend/src/hooks/useAdminAgos.ts
export function useAdminMemories(params: MemoryQueryParams) { ... }
export function useAdminSkills(params: SkillQueryParams) { ... }
export function useAdminTools(params: ToolQueryParams) { ... }
export function useAdminContext(runId: string) { ... }
```

Authorization token from localStorage or env：
```typescript
const token = localStorage.getItem('admin_token') || '';
const headers = { Authorization: `Bearer ${token}` };
```

### AD-3: Component Structure

```
frontend/src/
├── pages/
│   └── AdminAgosPage.tsx           # Main page with tabs
├── components/admin/
│   ├── AgosMemoryRail.tsx          # Memory tab content
│   ├── AgosSkillRail.tsx           # Skill tab content
│   ├── AgosToolRail.tsx            # Tool tab content
│   ├── AgosContextRail.tsx         # Context tab content
│   ├── AgosBadge.tsx               # Reusable badge component
│   └── AgosTokenBudgetBar.tsx      # Token budget visualization
├── hooks/
│   └── useAdminAgos.ts             # API hooks
└── lib/
    └── agosTypes.ts                # TypeScript interfaces
```

### AD-4: Badge Component

```tsx
type BadgeVariant =
  | 'historical'       // gray
  | 'candidate'        // yellow
  | 'trusted'          // green
  | 'proposal'         // blue outline
  | 'risk-read'        // green text
  | 'risk-local'       // yellow text
  | 'risk-external'    // orange text
  | 'risk-deploy';     // red text

function AgosBadge({ variant, label }: { variant: BadgeVariant; label: string }) {
  // Tailwind classes mapped to variant
  const classes = BADGE_CLASSES[variant];
  return <span className={classes} aria-label={label}>{label}</span>;
}
```

### AD-5: Token Budget Bar

```tsx
function AgosTokenBudgetBar({ used, total }: { used: number; total: number }) {
  const pct = Math.min(100, (used / total) * 100);
  const color = pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-yellow-500' : 'bg-green-500';
  return (
    <div className="w-full bg-gray-200 rounded h-4" role="progressbar" aria-valuenow={used} aria-valuemax={total}>
      <div className={`${color} h-4 rounded`} style={{ width: `${pct}%` }} />
      <span className="text-xs">{used}/{total} tokens ({pct.toFixed(0)}%)</span>
    </div>
  );
}
```

### AD-6: State Handling

```tsx
function RailWrapper({ loading, error, unauthorized, empty, children }) {
  if (unauthorized) return <UnauthorizedState />;
  if (loading) return <SkeletonLoader />;
  if (error) return <ErrorState message={error} />;
  if (empty) return <EmptyState />;
  return children;
}
```

## 測試策略

`frontend/src/pages/AdminAgosPage.test.tsx`：
- Renders 4 tabs
- Memory rail shows kind + eligibility badges
- Skill rail shows revision + risk badge
- Tool rail shows side-effect + status
- Context rail shows token budget bar
- Loading state renders skeleton
- Error state renders message
- Unauthorized → shows auth message
- Empty state renders appropriate message

`frontend/src/components/admin/AgosBadge.test.tsx`：
- Each variant renders correct color class
- aria-label present

Responsive：
- Desktop: table layout
- Mobile: card layout (via Tailwind responsive classes)
