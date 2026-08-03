# EU AI Act／EN 18286 Overlay

> Status: Draft — unapproved — non-effective
>
> Parent tracking issue: #1264
>
> This PR / document slice: #1265 (PR-A only)

本目錄在既有 ISO/IEC 42001 AIMS 上建立 EU AI Act／EN 18286 overlay。它不是第二套獨立管理系統，也不構成 EU AI Act conformity、CE marking、EN certification 或 presumption of conformity。

## Current artifacts

- [Applicability and classification](applicability-and-classification.md)：intended purpose、經濟營運者角色、風險分類及重新分類觸發條件。
- [Four-way crosswalk](crosswalk.md)：EN 18286、EU AI Act、ISO/IEC 42001 與 TrustForge evidence 的映射骨架。
- [Gap analysis](../../plans/ANALYSIS-EN-18286-EU-AI-ACT-GAP-2026-08-01.md)：初步差距、限制及改善工作包。

## Decision gates

1. EN 18286 的 bibliographic、publication、official status 均為 `authoritative source pending`；條款級工作需要公司合法授權的正式全文，且目前沒有 Official Journal citation 或 presumption of conformity 主張。
2. Intended purpose、operator role 與 risk classification 需要 Compliance Counsel 核准。
3. 沒有核准前，狀態只能是 `pending`、`draft` 或 `awaiting licensed text`。
4. 任何產品用途、EU 上市方式、供應鏈角色或受管制決策用途變更，都必須重新分類；受影響用途、release、contract 立即 blocked，必要時停止／隔離，並升級 Compliance Counsel、CISO、CPO、CEO，直到 exact-version 核准才可解除。
5. 本目錄不授權 DB、migration、secret、IAM、部署或應用程式碼異動。

## Owners and approval

| Responsibility | Role | Current state |
|---|---|---|
| Business intended purpose | CPO | Pending assignment and approval |
| Legal applicability and classification | Compliance Counsel | Pending assignment and approval |
| Security and resilience controls | CISO | Pending assignment and approval |
| AIMS document control | AIMS Manager | Proposed; not appointed |
| Final conformity decision | Authorized management and applicable conformity authority | Not authorized |

## Overlay documents

- [適用性與分類紀錄](applicability-and-classification.md)：intended purpose、operator role 與初步分類問題。
- [四向 crosswalk](crosswalk.md)：EN 18286、EU AI Act、ISO/IEC 42001/AIMS 與 TrustForge evidence 骨架。
- [EN 18286 / EU AI Act QMS overlay](en-18286-qms-overlay.md)：Phase 0-1 來源限制、角色評估、觸發條件與工作項。

本目錄只保存 preliminary applicability evidence，不保存 EN 18286 標準全文，
不宣稱 presumption of conformity、CE 標示、EU AI Act conformity 或任何認證。

## Issue boundary

#1265／PR-A 只交付本目錄與分析文件的 preliminary framework。它不完成、關閉或代表父 issue #1264 的其餘 Phase 0–4 工作；#1264 只能依其自身驗收條件逐項 disposition。
