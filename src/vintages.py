"""Read retained real time vintages so an origin sees only what it could have seen."""

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "data/public/tsifrght-alfred-vintages.csv"
MANIFEST = ROOT / "data/public/tsifrght-alfred-vintages.json"


def load_vintages(
    table_path: Path = TABLE, manifest_path: Path = MANIFEST
) -> pd.DataFrame:
    """Check the table against its manifest hash, then return it with real dates."""
    raw = table_path.read_bytes()
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("table_sha256") != hashlib.sha256(raw).hexdigest():
        raise ValueError("vintage table SHA256 does not match its manifest")
    table = pd.read_csv(table_path, parse_dates=["vintage_date", "obs_date"])
    if list(table.columns) != ["vintage_date", "obs_date", "value"]:
        raise ValueError("vintage table must hold vintage_date, obs_date and value")
    return table


def first_vintage(table: pd.DataFrame, month: pd.Timestamp) -> pd.Timestamp:
    """The first vintage date on which the month has a value."""
    rows = table.loc[table["obs_date"] == month, "vintage_date"]
    if rows.empty:
        raise ValueError(f"{month:%Y-%m} never appears in the vintages")
    return rows.min()


def origin_history(table: pd.DataFrame, target: pd.Timestamp) -> pd.DataFrame:
    """The history for a target month, as published on the origin vintage.

    The origin is the first vintage that holds the month before the target. No
    later vintage is read, and no month from the target on is returned.
    """
    origin = first_vintage(table, target - pd.DateOffset(months=1))
    snapshot = table.loc[
        (table["vintage_date"] == origin) & (table["obs_date"] < target)
    ]
    return snapshot.sort_values("obs_date").reset_index(drop=True)


def lookahead_rows(
    history: pd.DataFrame, target: pd.Timestamp, origin: pd.Timestamp
) -> pd.DataFrame:
    """Rows a forecaster at the origin must not have seen."""
    late = (history["vintage_date"] > origin) | (history["obs_date"] >= target)
    return history.loc[late]
