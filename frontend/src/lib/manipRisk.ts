// #86：跨幣信任×操縱風險排行——操縱風險徽章的純分級邏輯，抽成獨立、
// 不依賴 React 的模組，方便 vitest 純單元測試（本專案 frontend 測試慣例
// 只測 lib/ 純函式，元件本身不另外裝 RTL/jsdom）。

export type ManipRiskTier = 'high' | 'medium' | 'low' | 'unscored'

export interface ManipRiskDisplay {
  tier: ManipRiskTier
  label: string
  color: string
  /**
   * true 表示這個 `unscored` 是因為偵測到「legacy payload」（見下方
   * legacy 相容策略），不是本輪真的沒有 evidence。呼叫端可以用這個旗標
   * 給 tooltip 不同的說明文案。
   */
  legacy?: boolean
}

/** 沿用 issue #86 定案數字：≥0.3 高風險／≥0.1 中風險／其餘低風險。 */
export const MANIP_RISK_HIGH_THRESHOLD = 0.3
export const MANIP_RISK_MEDIUM_THRESHOLD = 0.1

const NEUTRAL_COLOR = 'var(--color-tf-muted)'
export const MANIP_RISK_UNSCORED_LABEL = '操縱風險未評分'

/**
 * codex 複審 HIGH 修復（風險 invariant 定案）：這裡吃的 `manipScore` 語意
 * 是後端 `_calc_manip_signal()` 算出的 **worst-case（max，any-hit）**，
 * 不是算術平均——平均會被 evidence 筆數稀釋（15 筆裡 1 筆已確認操縱
 * `manipulation=1.0`，平均只剩 0.067，會被門檻誤判低風險），只有
 * worst-case 能保證「只要出現一筆已確認操縱，就不可能顯示低風險」這個
 * 不變量，見 `scripts/fetch_scheduler.py::_calc_manip_signal()` docstring
 * 與 `manipRisk.test.ts::單筆確認操縱`。`manip_score_mean` 只當輔助資訊
 * （呼叫端另外顯示於 tooltip），不參與這裡的分級判斷。
 *
 * codex 複審 MEDIUM 修復（缺分數不可悄悄消失）：`manipScore` 為
 * `undefined`（本輪無 evidence、或舊格式快照本欄位新增前寫入）時回傳
 * 明確的 `unscored` 中性態，顏色用中性灰（跟 `low` 的綠色分開，不會被
 * 誤讀成「已評估、風險低」）——「沒評分」跟「評分後風險低」在 UI 上必須
 * 可區分。
 *
 * codex 複審 delta HIGH 修復（legacy payload 相容策略，不開新欄位遷移）：
 * `manip_score` 這個「欄位名」在這次 PR 期間**原地**從舊語意（算術平均）
 * 換成新語意（worst-case/max）。部署切換窗口、或任何殘留的舊版
 * writer 寫出的快照，`manip_score` 實際上仍是舊的平均值（例如
 * 15 筆裡 1 筆確認操縱、平均只剩 0.067），若直接套新門檻判讀，會把
 * 「已確認操縱」誤判成「低風險」——正是這輪要修的稀釋漏洞用另一條路
 * 復發。新版 `_snapshot_dict()`（`scripts/fetch_scheduler.py`）保證只要
 * 寫了 `manip_score` 就一定同時寫 `manip_score_mean`（兩欄位成對出現，
 * 見 `_calc_manip_signal()`）；因此「有 `manipScore` 但沒有
 * `manipScoreMean`」是新版 writer 不可能產生的形狀，可以拿來當「這包資料
 * 是舊 writer 寫的、`manipScore` 語意不可信」的判別信號，不必為此再開一個
 * schema 版本欄位或做遷移。判定為 legacy payload 時一律降級顯示
 * `unscored`（`legacy: true`），直到（依快照歷史 TTL
 * `TRUST_SNAPSHOT_HISTORY_TTL_SECONDS = 90 天`，見
 * `src/trustforge/ingestion/cache.py:271`）所有舊快照過期、新 writer
 * 全面覆寫後，才能安全移除這段 legacy 判斷分支。
 *
 * codex 窮舉終審 MEDIUM 修復（越界值穿透成假低風險，第二道防線）：主要
 * 防線在 `validators.ts::isManipScoreValue`（越界/非有限值在那裡就會讓
 * 整筆 `OverviewCoin` 判定不合法），這裡額外對 `manipScore`／
 * `manipScoreMean` 做 `Number.isFinite` + 0..1 範圍檢查、fail-closed 成
 * `unscored`——防止有任何未來繞過 validator 的呼叫路徑（例如測試、或
 * 其他直接呼叫這個純函式的呼叫端）把 `NaN`/`Infinity`/負值/大於 1 的值
 * 餵進門檻比較（負值必然小於任何正門檻，會被誤判成「低操縱風險」，把
 * 畸形資料偽裝成安全結論）。
 */
function isValidManipScore(value: number | undefined): value is number {
  return value !== undefined && Number.isFinite(value) && value >= 0 && value <= 1
}

export function manipRiskDisplay(
  manipScore: number | undefined,
  manipScoreMean: number | undefined,
): ManipRiskDisplay {
  if (manipScore === undefined) {
    return { tier: 'unscored', label: MANIP_RISK_UNSCORED_LABEL, color: NEUTRAL_COLOR }
  }
  if (!isValidManipScore(manipScore)) {
    // fail-closed：越界／非有限值視同沒有可信分數，不可套門檻分級。
    return { tier: 'unscored', label: MANIP_RISK_UNSCORED_LABEL, color: NEUTRAL_COLOR }
  }
  if (manipScoreMean !== undefined && !isValidManipScore(manipScoreMean)) {
    // manipScoreMean 本身越界／非有限，同樣視為不可信資料，fail-closed。
    return { tier: 'unscored', label: MANIP_RISK_UNSCORED_LABEL, color: NEUTRAL_COLOR }
  }
  if (manipScoreMean === undefined) {
    // legacy payload：新版 writer 一定成對寫入，只有舊 writer 才會只有
    // manip_score、沒有 manip_score_mean——此時 manipScore 語意不可信
    // （可能仍是舊的平均值），一律降級成 unscored，不可套新門檻分級。
    return { tier: 'unscored', label: MANIP_RISK_UNSCORED_LABEL, color: NEUTRAL_COLOR, legacy: true }
  }
  if (manipScore >= MANIP_RISK_HIGH_THRESHOLD) {
    return { tier: 'high', label: '⚠ 高操縱風險', color: 'var(--color-tf-bad)' }
  }
  if (manipScore >= MANIP_RISK_MEDIUM_THRESHOLD) {
    return { tier: 'medium', label: '⚡ 中操縱風險', color: 'var(--color-tf-warn)' }
  }
  return { tier: 'low', label: '✓ 低操縱風險', color: 'var(--color-tf-good)' }
}
