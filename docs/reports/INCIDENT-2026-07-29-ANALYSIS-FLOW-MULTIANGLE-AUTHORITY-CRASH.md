# Incident Report — analysis-flow worker 循環崩潰導致報告無法產出

- 日期：2026-07-29
- 嚴重度：**P0**（使用者提交分析後報告無法產出）
- 受影響環境：`trustforge-demo` EC2 (`<EC2_INSTANCE_ID>`, ap-southeast-2)
- 狀態：**根因已定位，修復方案已備（未部署）**
- 診斷者：CEO (HurricaneSoft)

---

## 一、問題描述

使用者提交分析請求（選幣種 + 問題）後，**報告無法產出**。web 服務本身回應正常，但分析工作從未完成。

## 二、根因

`trustforge-analysis-flow.service`（處理分析工作的 daemon）在**每次 `reconcile_runtime()` 循環都崩潰**，分析工作無法推進。

崩潰發生在 multi-angle synthesis 的 atomic terminal recovery 路徑：

```
reconcile_runtime()                          # analysis_flow.py:1876
└─ _recover_atomic_terminals()              # analysis_flow.py:1917
     └─ authority = self._atomic_store()    # analysis_flow.py:1928  ← 在 try/except 之外
          └─ raise MultiAngleAuthorityError( # analysis_flow.py:909
                 "production atomic batch authority/
                  exclusive shared projection storage is not configured")
```

systemd 重啟 worker → worker 再崩 → 循環。分析工作在 reconcile 崩潰後無法排程與推進 → 報告卡住。

## 三、為什麼崩

`_atomic_store()` (`analysis_flow.py:891`) 在存取前要求一組 **production 配置**，任一缺失即 raise：

| 條件 | 來源 | demo 環境 |
|------|------|----------|
| atomic batch DynamoDB table | env / config | ❌ 未配 |
| `AWS_REGION` | env | ❌ 空 |
| config_version | config | ❌ 空 |
| `budget_guard.atomic_batch_exclusive_enabled()` | runtime flag | ❌ 關 |
| `TRUSTFORGE_SHARED_ANALYSIS_DB_PATH` | env | ❌ 空 |

demo EC2 上述全未配置 → `_atomic_store()` 必 raise `MultiAngleAuthorityError`。

**關鍵 code 缺陷**：`_recover_atomic_terminals()` 在 line 1928 呼叫 `_atomic_store()`，**該呼叫位於 try/except 之外**。raise 因此直接傳播：

- `_recover_atomic_terminals()` 的 try/except 只包覆**迴圈內**的 `authority.record_job_terminal(...)` 等呼叫（`analysis_flow.py:1931`）。
- `_atomic_store()` 的呼叫（`:1928`）在迴圈**之前**、try **之外**，無任何 catch。
- `reconcile_runtime()`（`:1876`）直接呼叫 `_recover_atomic_terminals()` 亦無 try/except。

更關鍵：**即使 `rows` 為空**（沒有任何 completed atomic job 需要恢復），`_atomic_store()` 仍會被呼叫（它在 `for row in rows` 迴圈之前）。也就是說，只要 reconcile 跑到這一行，未配置環境必崩——無論有沒有實際的 atomic terminal 要恢復。

## 四、引入來源

**非 #748 系列**（asset-intrinsic 工作）。源自更早的 **#808-811 multi-angle synthesis 整合（PR #822）**：

- `feat: #809 multi-angle 後端入口、synthesis 觸發與 API endpoint`（commit `6f88c2b5`）
- `fix: complete multi-angle integration for develop`（commit `4413524f`）
- `fix: release terminal multi-angle reservations`（commit `69b480b9`）

該整合在 `analysis_flow.reconcile_runtime()` 路徑加入了 atomic terminal recovery（DynamoDB-backed exclusive batch authority），但**未處理「未配置環境」的 fail-soft 邊界**。production 配置的 hard requirement 被放在了一個每次 reconcile 必經、且無 try/except 保護的路徑上。

## 五、影響範圍

| 服務 | 狀態 | 影響 |
|------|------|------|
| `trustforge.service` (web) | active running | web 本身回應，但只能把請求入 queue |
| `trustforge-analysis-flow.service` (worker) | active running **但循環崩潰** | 分析工作無法推進 → **報告產不出** |
| `tf-snapshot.service` | **failed** | 快照寫入失敗 |
| `hermes-cycle.service` | activating（卡住） | 自動循環無法啟動 |

附帶觀察：EC2 console output 顯示 `16:22:59 UTC` port 8080 出現 `TCP SYN flooding`（公開流量 / 健康檢查風暴），可能加劇資源壓力，但非本 incident 根因。

## 六、證據

### 6.1 systemd status（analysis-flow）
```
● trustforge-analysis-flow.service - TrustForge durable manual analysis-flow worker
     Active: active (running) since Wed 2026-07-29 16:23:41 UTC
   Main PID: 1598 (python3.11)

Jul 29 16:25:43  File ".../analysis_flow.py", line 1880, in reconcile_runtime
                  repaired["atomic_terminals"] = self._recover_atomic_terminals()
Jul 29 16:25:43  File ".../analysis_flow.py", line 1928, in _recover_atomic_terminals
                  authority = self._atomic_store()
Jul 29 16:25:43  File ".../analysis_flow.py", line 917, in _atomic_store
                  raise MultiAngleAuthorityError(
Jul 29 16:25:43  trustforge.analysis_flow.MultiAngleAuthorityError:
                  production atomic batch authority/exclusive shared projection
                  storage is not configured
```

### 6.2 code 缺陷定位
- `analysis_flow.py:1917` `_recover_atomic_terminals()` — `authority = self._atomic_store()` 位於 try **之外**
- `analysis_flow.py:891` `_atomic_store()` — line 905-909 多條件 fail-closed，未配置即 raise
- `analysis_flow.py:1876` `reconcile_runtime()` — 直接呼叫，無 try/except

### 6.3 排除項
- `load_asset_intrinsic_records`（#869 forbidden-inference gate）：本地實測 develop fixture 載入正常（3 records，無 raise），且 `_public_intrinsic_assessment` 有 `except (OSError, TypeError, ValueError)` fail-soft。**非本 incident 根因**。
- `_public_report_dict` 的 intrinsic 注入：report 投影路徑有 fail-soft catch。**非根因**。
- `/aws/lambda/trustforge-demo` CloudWatch log：近 4 小時無 ERROR/Traceback（報告產出走 EC2 web + worker，非 Lambda）。

## 七、修復方案（未部署，備用）

### 方案 A — fail-soft guard（推薦，立即止血）

修改 `_recover_atomic_terminals()`，讓 atomic store 未配置時跳過 recovery 而非崩潰：

```python
def _recover_atomic_terminals(self) -> int:
    """Replay only locally durable completed results into batch authority."""
    rows = self._conn().execute(...).fetchall()
    if not rows:
        return 0                          # 無 terminal 要恢復 → 不碰 store
    try:
        authority = self._atomic_store()
    except MultiAngleAuthorityError:
        logging.getLogger(__name__).warning(
            "atomic batch authority not configured; "
            "skipping atomic terminal recovery"
        )
        return 0
    for row in rows:
        try:
            ...  # 既有迴圈邏輯不變
```

理由：
1. demo / 未配置環境不應因 atomic recovery 而整個 worker 崩潰。
2. atomic terminal recovery 是 multi-angle synthesis 的**最佳努力修復**，不是分析流程的硬前置——沒有它，基本分析仍可產出報告。
3. 把 production hard requirement 收進「有 rows 才碰 store + 未配置則 warning skip」的邊界內。

### 方案 B — 配置 production atomic store（若 demo 需 multi-angle）

為 demo 配上：DynamoDB atomic batch table、`AWS_REGION`、`TRUSTFORGE_SHARED_ANALYSIS_DB_PATH`、`budget_guard.atomic_batch_exclusive_enabled()`。但 demo 為展示環境，未必需要 production-grade exclusive batch authority。

### 建議

**方案 A 優先**。multi-angle 的 atomic exclusive 是 production 強一致性的進階能力；demo 與未配置環境應能以「跳過 recovery」的降級模式正常產出基本報告，而非整個 worker 崩潰。

## 八、時間線

| 時間 (UTC) | 事件 |
|-----------|------|
| 15:58:21 | EC2 instance 啟動（LaunchTime） |
| 15:58:35 | cloud-init 完成；SSM Agent v3.3.4624.0 running |
| ~16:18:34 | **user initiated stop**（手動停止） |
| 16:22:59 | console: port 8080 TCP SYN flooding |
| 16:23:40 | trustforge.service 重啟 running |
| 16:23:41 | analysis-flow.service 重啟 running（隨即循環崩潰） |
| ~16:25:43 | reconcile_runtime traceback 噴出（取樣） |
| 診斷時 | CEO 透過 SSM 取得 systemd status + console output + code 定位 |

## 九、後續追蹤

- [ ] 部署方案 A（fail-soft guard）至 demo，驗證 worker 不再循環崩潰且報告可產出
- [ ] 排查 `tf-snapshot.service` failed 的獨立根因
- [ ] 排查 `hermes-cycle.service` 卡 activating 的根因
- [ ] 評估 production 環境是否已正確配置 atomic batch authority（避免同樣崩潰）
- [ ] 補測試：`_recover_atomic_terminals` 在 `_atomic_store` raise 時 fail-soft（目前測試只覆蓋迴圈內 exception）
