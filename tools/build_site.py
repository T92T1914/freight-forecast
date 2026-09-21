"""Build a public site from an explicit list of public example files."""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_site"
FILES = {
    "site/index.html": "index.html",
    "site/style.css": "style.css",
    "site/app.js": "app.js",
    "site/selection-state.mjs": "selection-state.mjs",
    "site/backtest.js": "backtest.js",
    "docs/backtest-example.json": "backtest.json",
    "docs/visual-example-data.json": "data.json",
    "docs/freight-forecast-example.svg": "example.svg",
}


def main():
    data = json.loads((ROOT / "docs/visual-example-data.json").read_text())
    if not data.get("source_commit"):
        raise ValueError("Example data must retain its source revision.")
    backtest = json.loads((ROOT / "docs/backtest-example.json").read_text())
    if not backtest.get("provenance", {}).get("implementation_sha256"):
        raise ValueError("Backtest data must retain its implementation hashes.")
    OUT.mkdir(exist_ok=True)
    for source, target in FILES.items():
        path = ROOT / source
        if not path.is_file() or path.is_symlink():
            raise ValueError("Expected a regular source file: " + source)
        shutil.copyfile(path, OUT / target)
    unexpected = {p.name for p in OUT.iterdir()} - set(FILES.values())
    if unexpected:
        raise ValueError("Unexpected site output files: " + str(sorted(unexpected)))
    print("Built", len(FILES), "public files in", OUT)


if __name__ == "__main__":
    main()
