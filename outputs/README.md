# 商業化提案產物

三份提案輸出都由同一份 DOCX 內容重建。先安裝專用、非 runtime 的提案工具：

```bash
uv sync --extra proposal
uv run python tools/build_commercial_proposal.py
uv run python tools/docx_to_html.py
```

DOCX 是內容來源；HTML 由 `tools/docx_to_html.py` 轉換。PDF 的版面品質目前以 Microsoft Word 原生「另存為 PDF」產生，輸入必須是前一步重建的 DOCX；這是明確的人工發布步驟，不是無人值守的 repository build。匯出後應確認三份產物都包含相同章節與 ModelHub／SageMaker 平行 TrainingBackend 邊界，並對 HTML 執行結構驗證、對 PDF 執行文字擷取與頁數 smoke check。

PR 首版 PDF 由 Microsoft Word 原生匯出。沒有 Word 的環境可以完整重建 DOCX 與 HTML，但不得宣稱已重建同步 PDF。
