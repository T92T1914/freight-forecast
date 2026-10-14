# The image trains from its copied dataset and runtime source. The existing
# training gate must beat the seasonal naive baseline before saving a model.
FROM python:3.11-slim

ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# no build tools: every pinned wheel ships prebuilt for this base
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY data/ data/

# train at build time; fails the build if the model can't beat the naive
RUN python -m src.train \
    && chown 10001:10001 /app/models /app/models/model.joblib \
    && chmod 0500 /app/models \
    && chmod 0400 /app/models/model.joblib

USER 10001:10001

EXPOSE 8000

# container-level self-check (Kubernetes probes come in later via manifests)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "src.serve:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "8", "--timeout-graceful-shutdown", "5"]
