# Forecast feature reuse

The history and model are loaded once per application lifespan. The old handler rebuilt the same lag and rolling features for every request. The repaired handler builds a date-indexed feature frame at startup, including the one forecastable future month, and selects the requested row. Reloading the application rebuilds that frame from the newly loaded history.

Regression checks compare all 85 supported months against the previous feature construction, prohibit request-time feature rebuilding, and verify that restarting with changed history does not reuse stale features.

## Local measurement — 2026-09-06

On Python 3.11.8, Windows 11, Ryzen 7 7800X3D, with 96 history rows and the same loaded model, a request for 2025-01 took a median **4.534 ms before / 1.278 ms after**, measured in process. This is about 3.55 times faster for this handler measurement. It excludes HTTP, networking and startup. Other offline audits ran concurrently; this is not a production throughput or tail-latency result.

Ten paired warmups checked identical responses. Seven batches of 100 calls alternated before/after order; the medians are over those seven batch means. See [raw samples](serving-measurement.json). The predicted volume was 3297, seasonal naive 3477, trained through 2022-12-01.

```sh
python -m src.generate_data
python -m src.train
python tools/benchmark_serving.py
```

The script reads the historical handler from local commit `56c23a8`; retain repository history when reproducing. It does not switch branches. Regenerating data and retraining intentionally replace the local generated data/model artifacts. Dependency versions, machine load and model retraining can change timings; the printed response makes changed model results visible. The data is seeded synthetic data, not operational shipment records.
