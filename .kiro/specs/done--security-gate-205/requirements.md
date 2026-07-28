# Spec：投稿前安全 Gate (#205)

> Issue: #205
> Branch: `feat/issue-205-security-gate`

## 概述

決賽投稿前的安全掃描：確保 repo 無 secret 外洩、無內網 reference、官方資料無公開風險。

---

## 一、需求

### R1: Secret Scan
- 掃描整個 repo 的 `.env`、hardcoded token、API key pattern
- 工具：`grep -rn` + regex pattern matching
- 產出報告 `out/security-gate-report.json`

### R2: 內網 Reference 清理
- 掃描所有 `.py`、`.md`、`.ts` 中的 `localhost`、`192.168.*`、`10.*`、`.local` domain
- 區分：開發用（OK）vs 不該出現在交付物中的（需移除）

### R3: 官方資料公開風險
- 確認 `data/` 目錄的 OHLCV 是否可公開（HOYA BIT 授權範圍）
- 確認 `demo/sample_data/` 是否為合成資料

### R4: CLI 入口
- `python -m trustforge.cli security-gate [--fix]`

---

## 三、驗收
- [x] 掃描完成無 P0 leak
- [x] 報告寫入 `out/security-gate-report.json`
