# Agent OS Admin UI Rails

> Issue: #924 | Epic: #914
> Depends on: #923
> Labels: agent-os, frontend, P1

## 背景

Admin Summary API (#923) 提供了 memory、skill、tool、context 的 read-only 查詢。
本 issue 建立 Admin-only UI rails 呈現這些資料。

## 範圍

新增 Admin-only 頁面（不修改 public demo navigation）：
- Memory rail
- Skill rail
- Tool rail
- Context rail

**不包含**：activation/deployment UI、public-facing changes。

## 功能需求

### FR-1: Memory Rail

顯示：
- Kind（episodic/semantic/procedural/dialogue）badge
- Evidence eligibility status badge
- Provider
- Lineage info（rank, reason）
- Selection reason
- Created/retrieved timestamps

Badge 視覺區分：
- `historical` = gray
- `candidate` = yellow
- `trusted_evidence` = green
- `proposal` = blue outline

### FR-2: Skill Rail

顯示：
- Skill name + family badge
- Revision hash（truncated）
- Dependencies list
- Risk classification badge
- Lifecycle status
- Frozen state indicator

### FR-3: Tool Rail

顯示：
- Tool name + version
- Side-effect class badge
- Approval requirement indicator
- Input/output hashes（truncated）
- Evidence class
- Status（success/failed/timeout/rejected）

### FR-4: Context Rail

顯示：
- Included refs summary（count by type）
- Excluded refs list with reasons
- Token budget bar（used/total）
- Deterministic hash（for verification）

### FR-5: Badge 視覺系統

| Badge | Color | 用途 |
|-------|-------|------|
| historical context | gray bg | evidence_eligible=false |
| candidate evidence | yellow bg | candidate_evidence class |
| trusted evidence | green bg | trusted_evidence + verified |
| proposal | blue outline | pending approval |
| read_only | green text | low risk |
| local_write | yellow text | medium risk |
| external_write | orange text | high risk |
| deploy_or_release | red text | critical risk |

### FR-6: States

每個 rail 需處理：
- Loading state（skeleton / spinner）
- Empty state（no data message）
- Error state（API error message）
- Unauthorized state（redirect to login or show message）

### FR-7: Responsive

- Desktop: table layout
- Mobile: card layout（堆疊）

## 非功能需求

- **NFR-1: Admin-only** — 不出現在 public demo navigation
- **NFR-2: 無 mock data** — 只從真實 Admin API 取資料
- **NFR-3: Accessibility** — badge 有 aria-label，table 有 proper headers

## 驗收條件

1. Memory rail 顯示 kind, eligibility, lineage, selection reason
2. Skill rail 顯示 revision, dependencies, risk, frozen state
3. Tool rail 顯示 side effect, approval, hashes, evidence class
4. Context rail 顯示 included/excluded refs, token budget
5. Historical/candidate/trusted/proposal badges 視覺區分
6. Loading, empty, error, unauthorized states 完整
7. Desktop/mobile Eye, frontend tests, lint/build, pre-push 通過
