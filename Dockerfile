FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    SCHEMESAATHI_DATA=/app/runtime SCHEMESAATHI_HEADLESS=1 PORT=10000
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium --only-shell \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 saathi \
    && mkdir -p /app/runtime && chown saathi:saathi /app/runtime
COPY --chown=saathi:saathi app/ ./app/
COPY --chown=saathi:saathi run.py serve.py ./
COPY --from=frontend --chown=saathi:saathi /build/app/static ./app/static
USER saathi
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=20s --start-period=60s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','10000')+'/healthz',timeout=15)"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "serve.py"]
