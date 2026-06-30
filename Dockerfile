# TrustForge Live Demo — 純 stdlib，無第三方執行期依賴（boto3 僅實際呼叫 Bedrock 時用）
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY data ./data
COPY demo ./demo
RUN pip install --no-cache-dir -e .

ENV PORT=8080
EXPOSE 8080
# 健康檢查走 /healthz
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/healthz').read()" || exit 1

CMD ["python", "-m", "trustforge.web"]
