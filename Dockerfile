# TrustForge Live Demo — 純 stdlib，無第三方執行期依賴（boto3 僅實際呼叫 Bedrock 時用）
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY data ./data
COPY demo ./demo
# 第三輪 AI 友善：`GET /api/openapi.yaml`／`GET /llms.txt` 是純讀檔回傳（見
# `src/trustforge/web.py::_handle_openapi_spec`/`_handle_llms_txt`），容器內
# 必須帶這兩份檔案，否則兩端點在部署環境會 404。只帶 `docs/api`（實際被
# serve 的部分），不帶整棵 `docs/`（archive/plans/qa 等內部規劃文件不對外）。
COPY docs/api ./docs/api
COPY llms.txt ./llms.txt
RUN pip install --no-cache-dir -e .

ENV PORT=8080
EXPOSE 8080
# 健康檢查走 /healthz
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/healthz').read()" || exit 1

CMD ["python", "-m", "trustforge.web"]
