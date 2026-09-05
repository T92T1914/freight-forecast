"""Generate a synthetic monthly household goods shipment volume dataset.

The shape mirrors the DoD PCS cycle seen at a JPPSO: roughly 40,000 moves
land in the May-August peak, winter months run at about a third of peak,
and total volume drifts up slightly year over year. A fixed seed makes the
dataset fully reproducible.
"""

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 2017
START_MONTH = "2017-01"
N_MONTHS = 96  # 2017-2024
BASE_LEVEL = 5200  # baseline moves per month before seasonality
ANNUAL_TREND = 0.018  # slight year-over-year growth
NOISE_SD = 0.04

# Seasonal multipliers. May-Aug factors sum to 7.45, so the four peak
# months total ~38,700 moves at base level -- the "nearly 40,000" peak.
# Kept as a grid (one row per third of the year) so the seasonal shape is
# visible at a glance; the formatter would otherwise stack twelve lines.
# fmt: off
MONTH_FACTORS = {
    1: 0.55, 2: 0.55, 3: 0.70, 4: 0.95,
    5: 1.65, 6: 2.00, 7: 2.05, 8: 1.75,
    9: 1.05, 10: 0.85, 11: 0.65, 12: 0.55,
}
# fmt: on

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "shipments.csv"


def generate(seed: int = SEED) -> pd.DataFrame:
    """Return the (date, volume) series for `seed`.

    The structure is multiplicative -- base level x trend x month factor x
    noise -- so the summer peak scales with the overall level instead of
    adding a fixed number of moves. That is the same assumption the model
    makes by fitting log(volume), which is deliberate: the data is meant to
    look like the cycle, not to hand the model an easy win by construction.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(START_MONTH, periods=N_MONTHS, freq="MS")
    years_elapsed = (dates.year - dates.year[0]).to_numpy()
    factors = np.array([MONTH_FACTORS[m] for m in dates.month])
    noise = rng.normal(1.0, NOISE_SD, size=N_MONTHS)

    volume = BASE_LEVEL * (1 + ANNUAL_TREND) ** years_elapsed * factors * noise
    return pd.DataFrame({"date": dates, "volume": volume.round().astype(int)})


def main() -> None:
    df = generate()
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(DATA_PATH, index=False)
    print(f"Wrote {len(df)} months to {DATA_PATH}")


if __name__ == "__main__":
    main()
