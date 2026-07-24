# 本機排程硬編碼路徑事故報告

- **日期**：2026-07-23
- **嚴重度**：High
- **狀態**：程式根治完成；真機啟用驗收待另行授權
- **Issue**：[#518](https://github.com/cancleeric/trustforge/issues/518)
- **修復 PR**：[#536](https://github.com/cancleeric/trustforge/pull/536)
- **合併版本**：`ade1e08`

## 1. 事故摘要

本機排程與相關腳本曾把 repository 根目錄寫死為
`/Users/apple/HurricaneSoft/trustforge`。在其他使用者或機器上，launchd
無法找到程式，自動收集與分析因此未啟動；web 仍可由人工方式啟動，使
health 表面正常，但 freshness 持續變舊。

事故調查時觀察到：

- `/api/status`：fresh=0、stale=99、missing=16；
- 最後成功抓取時間停在 2026-07-21 18:25；
- 手動以正確路徑執行 fetch 後，freshness 恢復為 fresh=84、stale=15。

結論：資料收集管線本身可運作，故障點是不可攜的排程設定與缺少一致的安裝流程。

## 2. 根因

1. 五份 tracked launchd plist 內含固定 repository、Python、Node、SQLite
   與 log 絕對路徑。
2. 五個 shell script 使用固定路徑或錯誤的固定預設值。
3. macOS 使用靜態 plist；Linux 本機沒有與其語意等價的 user-level 安裝流程。
4. 排程未載入時沒有直接錯誤回饋，只有 freshness 逐步惡化。

## 3. 已完成根治

PR #536 已完成並合併：

- shell scripts 預設由自身位置解析 canonical repository root，仍允許明確
  `TRUSTFORGE_HOME` 覆寫；symlink／非 canonical 路徑 fail closed；
- 移除五份 host-specific 靜態 plist；
- macOS plist 改由 `plistlib` 結構化產生，不使用 `sed` 模板，避免 XML
  escaping 與路徑注入問題；
- 新增 macOS launchd／Linux `systemd --user` 的本機安裝與解除安裝入口；
- Linux local units 與 macOS local plist 對齊 SQLite、web、analysis 環境；
- 支援 `--render-only`、`--no-enable` 與 UI opt-in；
- 加入 ownership marker、原子寫入、unmanaged collision 拒絕、service-manager
  三態查詢、transaction rollback 與 rollback-incomplete 回報；
- production `/opt`／DynamoDB installers 保持不變；
- 未涉及 DB schema、migration、secret rotation 或 ModelHub。

安全與對抗審查曾攔截並修正：

- 覆寫同名但非本工具管理的 unit／plist；
- enable/bootstrap 中途失敗造成部分安裝；
- rollback 未精確恢復 enabled／active／loaded 狀態；
- service manager query error 被誤判為 benign absent；
- render-only 意外呼叫 launchctl；
- vacuous rollback tests。

最終 reviewer 與 harper（CISO）均對合併 commit 給予 PASS。

## 4. 已驗證項目

- [x] `scripts/`、`deploy/`、`tests/` 中舊的 `/Users/apple/...` runtime 路徑為零
- [x] macOS plist 與 Linux user units 可在隔離目錄 render
- [x] render-only 不呼叫 `launchctl`／`systemctl`
- [x] 路徑含空白、`%` 與特殊字元時可正確產生設定
- [x] unmanaged 同名檔案不會被覆寫或解除安裝
- [x] install／rollback／uninstall 失敗路徑使用 fake service manager 驗證
- [x] eye：critical=0、warning=0
- [x] 合併後聚焦回歸通過

## 5. 尚未執行的真機驗收

本次修復與合併**沒有啟用本機常駐排程**。安裝會啟動 ingestion、analysis
及 local web；analysis 可能連動外部模型或成本，因此必須另外取得明確授權，
不能因文件結案自動執行。

- [ ] macOS 執行 installer，確認 `launchctl print gui/$UID/<label>`
- [ ] 等一個 900 秒 interval，確認 refresh log 有新輸出
- [ ] 驗證 `/api/status` 的 freshness `fresh > 0`
- [ ] Linux 真機驗證 `systemctl --user` timer／services

安全的純產生檢查：

```bash
./deploy/install_local_scheduler.sh --render-only --output-dir /tmp/trustforge-scheduler
```

## 6. 剩餘風險

「freshness 全 stale 時主動告警／health degraded」尚未在 #518 實作。目前已解決
排程不可攜根因，但若排程日後因 credential、網路或 service manager 故障停止，
仍需要獨立追蹤。已建立
[#537](https://github.com/cancleeric/trustforge/issues/537)，範圍限制為
freshness degraded 判定與 API contract，不自動重啟、不新增外部通知或 secret。

## 7. 結案判定

- **程式缺陷**：已修復並合併。
- **Issue #518**：已關閉。
- **部署狀態**：未啟用，不能宣稱真機排程已運作。
- **完整營運閉環**：需完成第 5 節真機驗收，並完成 #537。
