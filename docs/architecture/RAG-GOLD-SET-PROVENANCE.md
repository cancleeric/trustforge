# RAG gold set 與 provenance 契約

`rag_gold_set.py` 是離線 pure builder/evaluator，不讀寫資料庫、不接
AnalysisFlow，也沒有 approval、activation 或 Evidence promotion API。

## 信任邊界

- 唯一具名 reviewer 是 `gray-cpo`；資格只能由 caller 傳入、具
  tenant/version/effective-window/registry checksum 的 trusted registry
  fixture 授權。有效期採 `[valid_from, valid_until)`；事件自行宣稱的角色、
  到期日或 hash 不構成權限。
- label、retrieval 與 manifest 都採 exact schema、canonical checksum、
  tenant 與 point-in-time cutoff。外租戶及 cutoff 後輸入在計數、quota 與
  hash 前即不可見。
- gold label 只接受 `approved_answer`／`must_abstain` exact enum，並保存
  reviewer reason、review time、source provenance。每個 query 必須 exactly
  one root、每個 predecessor 最多一個 successor、exactly one head。
- gold label 是 `human_reviewed_non_evidentiary_gold`，永遠不是 Evidence。
  feedback、高票、答案重複、prompt injection 文字均沒有升格路徑。
- revision 必須形成同 query、無環、無斷鏈的 supersession 鏈；重播及回滾
  至舊輸入集合會產生 byte-identical manifest。

## 評估語意

retrieval result、feedback、manual review 都是 exact LearningEvent 子契約，
各自納入 manifest input root/count。feedback 的 `eligible_as_gold` 與
`eligible_as_evidence` 必須為 false，其文字只視為 opaque data；feedback
必須指向 supplied retrieval identity 且 query lineage 相同。

retrieval 回答必須綁 query cutoff、snapshot/job/checksum 並附 evidence
identity citation；snapshot 由 supplied scoped Evidence LearningEvents
重建 current revision lineage，caller 不能自簽 identity list。只有 query-time
current identity 同時綁定於 gold label 才算 citation aligned，文字相同不能
洗白錯誤來源。retrieval top-level `snapshot_id`／`job_id`／`snapshot_sha256`
皆須直接等於由 tenant、canonical cutoff、Evidence event root 與 lineage
version deterministic 導出的 trusted snapshot/run binding；此驗證先於
citations，所以 empty-current Evidence 的 explicit abstention 仍可驗真。
citation claim 亦須與 current Evidence canonical claim exact
一致，不能只拿合法 identity 搭配捏造 claim。build 與 evaluate 共用同一個
retrieval semantic validator；相同 query cutoff 共用一次 Evidence snapshot
重建，且 frozen policy 最多允許 16 個 unique canonical cutoffs；第 17 個會在
任何 snapshot build 前 fail closed，避免 `O(retrieval × evidence)` 重掃。
offset timestamp 先 canonicalize 為 UTC cache key。Evidence iterable 僅
bounded collect 一次，因此 one-shot generator 與 list 輸入結果一致。證據不足必須
abstain；缺少 retrieval 記為 `missing`，不是 abstain，也不納入 decision
accuracy。資料量不足回傳 `insufficient_data`，
不假裝有效評估。

輸入在 materialize 前套用 event/count、canonical UTF-8 bytes、field、
node 與 depth 限制，拒絕 NaN/Infinity 與未知 schema。輸出只供 regression
與診斷，不修改 Trust Kernel。gold-set 版本以
`gold_set_revision + previous_manifest_sha256` 明確成鏈，無 mutable current
pointer；chain 不允許 dataset cutoff 倒退，也不允許在同一 chain 中無授權
更換 reviewer registry version/checksum。以舊 policy/registry/events 回放
就是 deterministic rollback。evaluation report 會保存 query-time Evidence
snapshot/root/count/current lineage，以及每列 retrieval identity、
snapshot/job、claim/provenance checksum。即使某 gold query 沒有 retrieval，
report 與 row 仍綁 evaluation `query_as_of` 的 Evidence snapshot/root/count；
當時 Evidence 改變就必然改變 report hash。

Evaluation row 明確區分 `missing`、`explicit_abstention`、`answered`。
missing 沒有模型決策，不納入 decision accuracy；explicit abstention 保留
retrieval identity、snapshot/job 與 query-time Evidence state。
`decision_correct` 依 gold enum 判定：`must_abstain` 只接受明確 abstain，
`approved_answer` 只接受答案內容正確的 answered。`answer_exact` 只評有
答案語意的 `approved_answer`，其他為 `null`。`decision_accuracy` 與
`exact_answer_rate` 均輸出實際 evaluated count，空分母回傳 `null`。
