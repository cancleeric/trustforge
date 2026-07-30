# #810 實作與驗收任務

- [ ] 1. 更新 endpoint TypeScript contract，加入 provenance、三種獨立比較結果和 independence facts。
- [ ] 2. 重構總覽 header/table/card，呈現每路 mode/question、completeness 與真實 drilldown target。
- [ ] 3. 實作方向分歧、完整度差距、證據重疊三個獨立 presentation panels。
- [ ] 4. 移除所有把 shared source / overlap count 視為 direction divergence 的 UI mapping。
- [ ] 5. 加入 0% independence 固定警示與禁止獨立佐證文案的 regression test。
- [ ] 6. 建立由 `snap-btc-eca5b069d33ea8ac` API export 驗證的 component/integration tests。
- [ ] 7. 以真實 payload 完成 desktop/mobile eye scan，涵蓋 pending/error/overflow/drilldown，保存證據。
- [ ] 8. 執行前端 tests、typecheck/build 與 repository-local gate；僅在 task 6、7 通過後，才能提報 #810 可關閉。
