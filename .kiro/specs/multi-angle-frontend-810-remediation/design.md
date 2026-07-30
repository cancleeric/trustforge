# #810 設計：以三種比較訊號重構總覽

## TypeScript contract

將 API type 拆為 `direction_divergences`、`completeness_gaps`、`evidence_overlaps`、`evidence_independence`；保留 `conflicts?` 僅作 backward-compatible fallback，且先用 `kind` 分類。元件不自行計算分歧數，所有數字直接取 backend typed field。

## Component layout

1. Header：snapshot、consensus、independence 與不可投資建議限制。
2. Angle summary：table/card，行點擊轉交真實 job/report id。
3. Direction divergence panel：pair-level items；空集合明說無方向分歧。
4. Completeness panel：per-angle coverage、missing fields、gaps。
5. Evidence overlap panel：pair-level sources與 ratio，不使用「分歧」一詞。
6. Limits/pending/error：依 API status 誠實顯示。

`evidence_independence === 0` 時由共同 presenter 產生固定 warning，避免各元件文案漂移。

## Real payload test fixture

測試資料只能由 production acceptance export 取得，包含 snapshot ID、payload digest 與擷取指令。component test mock 的是 transport boundary，不是手寫 domain payload；e2e/eye scan 直接用 export/受控 API endpoint。測試須 assert 三個面板的資料來源與文本彼此不混用。
