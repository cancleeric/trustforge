# TrustForge 決賽投稿封裝檢查表 (#313)

> 競賽投稿截止：2026-08-01
> 版本：v0.23.0

## Part A — 轉 Public 清理
- [ ] 確認無 production secret 留在 repo（`grep -r "sk-*" src/ --include="*.py"`）
- [ ] 確認無 private IP / 內網 reference（`grep -r "192.168\|10\.0\|\.internal"`）
- [ ] GCP Cloud Run credentials 不進版控（已在 .gitignore）
- [ ] 前端 public Demo URL 可存取：由部署環境提供

## Part B — 交付件打包
- [ ] `scripts/submission-pack.sh` 產出 `finale-submission.zip`
- [ ] 簡報 PDF（參見 `docs/competition/slide-deck/`）
- [ ] Live Demo URL + demo evidence 截圖（`out/demo-evidence/`）
- [ ] 技術文件（`docs/competition/`）

## Part C — 提交前最終檢查
- [ ] `git diff --check` 無空白錯誤
- [ ] pre-push gate 全綠
- [ ] `finale-submission.zip` < 50MB
- [ ] 所有決賽相關 issue 已 close

## 執行
```bash
# 自動化打包
bash scripts/submission-pack.sh

# 手動驗證
unzip -l out/finale-submission.zip
```
