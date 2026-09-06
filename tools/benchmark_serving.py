"""Matched in-process measurement against the historical uncached handler.

Uses only the predict function from local commit 56c23a8, sharing the current
loaded model/data and module globals. No checkout, server or network changes.
Run after generating data and training: python tools/benchmark_serving.py
"""

import ast
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))
from src import serve  # noqa: E402 - this checkout script locates src above

source = subprocess.check_output(
    ["git", "show", "56c23a8:src/serve.py"], cwd=repo, text=True
)
tree = ast.parse(source)
old = next(
    n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "predict"
)
old.decorator_list = []
namespace = dict(vars(serve))
exec(
    compile(
        ast.Module(body=[old], type_ignores=[]), "<baseline predict at 56c23a8>", "exec"
    ),
    namespace,
)
baseline = namespace["predict"]


async def main():
    async with serve.lifespan(serve.app):
        request = serve.PredictRequest(month="2025-01")
        samples = {"before_ms": [], "after_ms": []}
        for _ in range(10):
            assert baseline(request) == serve.predict(request)
        for batch in range(7):
            order = [("before_ms", baseline), ("after_ms", serve.predict)]
            if batch % 2:
                order.reverse()
            for name, function in order:
                start = time.perf_counter()
                for _ in range(100):
                    function(request)
                samples[name].append((time.perf_counter() - start) * 10)
        result = {
            "python": sys.version,
            "month": request.month,
            "history_rows": len(serve.app.state.history),
            "calls_per_batch": 100,
            "batches": 7,
            "scope": "in-process; excludes HTTP; same loaded model and data",
            "samples": samples,
            "medians_ms": {k: statistics.median(v) for k, v in samples.items()},
            "response": serve.predict(request),
        }
        print(json.dumps(result, indent=2))
        # Redirect stdout to preserve a new run without replacing recorded results.


if __name__ == "__main__":
    asyncio.run(main())
