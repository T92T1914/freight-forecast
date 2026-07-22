# Self-contained serving image: the build regenerates the dataset and trains
# the model from scratch, so the artifact inside the image is exactly what
# the pinned code produces — no "which model file was this?" ambiguity.
# Training takes ~2s, so build-time training costs nothing here; a heavier
# model would move to a multi-stage build or an artifact registry.
FROM python:3.11-slim

# no build tools: every pinned wheel ships prebuilt for this base
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY data/ data/

# train at build time; fails the build if the model can't beat the naive
RUN python -m src.train

EXPOSE 8000

# container-level self-check (Kubernetes probes come in later via manifests)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "src.serve:app", "--host", "0.0.0.0", "--port", "8000"]
