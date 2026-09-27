"""Sequential residual intervals with explicit issue then observe ordering."""

import re
from dataclasses import asdict, dataclass
from fractions import Fraction
from math import ceil, isfinite
from statistics import mean


def month_number(month: str) -> int:
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("month must be YYYY-MM")
    year, number = map(int, month.split("-"))
    return year * 12 + number - 1


def number(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not isfinite(value)
    ):
        raise ValueError(name + " must be finite")
    return float(value)


def radius(errors: list[float], alpha: Fraction) -> tuple[float | None, int]:
    """Use a conservative rank and retain an unbounded upper order statistic."""
    if not errors:
        raise ValueError("calibration needs prior errors")
    if any(number(value, "error") < 0 for value in errors):
        raise ValueError("errors must be nonnegative")
    rank = ceil((len(errors) + 1) * (1 - alpha))
    if rank > len(errors):
        return None, rank
    if rank < 1:
        return 0.0, rank
    return sorted(errors)[rank - 1], rank


@dataclass(frozen=True)
class Interval:
    month: str
    point: float
    lower: float | None
    upper: float | None
    status: str
    alpha: float
    rank: int
    calibration_count: int
    calibration_first: str
    calibration_last: str


class ResidualCalibrator:
    """One pending forecast keeps its actual out of its own interval."""

    def __init__(
        self, calibration, *, method="rolling", window=60, alpha="0.1", gamma="0.005"
    ):
        if method not in {"fixed", "rolling", "adaptive"}:
            raise ValueError("unsupported interval method")
        if isinstance(window, bool) or not isinstance(window, int) or window < 1:
            raise ValueError("window must be a positive integer")
        self.target = Fraction(alpha)
        self.gamma = Fraction(gamma)
        if not 0 < self.target < 1 or not 0 < self.gamma < 1:
            raise ValueError("alpha and gamma must be between zero and one")
        self.method, self.window = method, window
        self.alpha = self.target
        self.history = tuple(
            (month, number(error, "error")) for month, error in calibration
        )
        if not self.history or len(self.history) != window:
            raise ValueError("initial calibration must fill the declared window")
        months = [month_number(month) for month, _ in self.history]
        if any(b != a + 1 for a, b in zip(months, months[1:], strict=False)):
            raise ValueError("calibration months must be contiguous and ordered")
        if any(error < 0 for _, error in self.history):
            raise ValueError("errors must be nonnegative")
        self.observed = months[-1]
        self.pending = None

    def issue(self, month: str, point: float) -> Interval:
        if self.pending is not None:
            raise ValueError("observe the pending forecast first")
        if month_number(month) != self.observed + 1:
            raise ValueError("forecast must follow the last observed month")
        point = number(point, "point")
        half, rank = radius([error for _, error in self.history], self.alpha)
        interval = Interval(
            month,
            point,
            None if half is None else point - half,
            None if half is None else point + half,
            "unbounded" if half is None else "finite",
            float(self.alpha),
            rank,
            len(self.history),
            self.history[0][0],
            self.history[-1][0],
        )
        self.pending = interval
        return interval

    def observe(self, month: str, actual: float) -> dict:
        if self.pending is None or self.pending.month != month:
            raise ValueError("observation must match the pending forecast")
        actual = number(actual, "actual")
        interval = self.pending
        unbounded = interval.status == "unbounded"
        covered = unbounded or interval.lower <= actual <= interval.upper
        width = None if unbounded else interval.upper - interval.lower
        score = (
            None
            if unbounded
            else width
            + 2
            / float(self.target)
            * (max(interval.lower - actual, 0) + max(actual - interval.upper, 0))
        )
        if self.method != "fixed":
            self.history = (*self.history, (month, abs(actual - interval.point)))[
                -self.window :
            ]
        if self.method == "adaptive":
            self.alpha += self.gamma * (self.target - int(not covered))
        self.observed = month_number(month)
        self.pending = None
        return {
            **asdict(interval),
            "actual": actual,
            "covered": covered,
            "width": width,
            "interval_score": score,
            "score_status": "infinite" if unbounded else "finite",
        }


def summarize(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("summary needs observations")
    unbounded = sum(row["status"] == "unbounded" for row in rows)
    finite = [row for row in rows if row["status"] == "finite"]
    return {
        "months": len(rows),
        "covered": sum(row["covered"] for row in rows),
        "coverage": mean(row["covered"] for row in rows),
        "unbounded": unbounded,
        "missing": 0,
        "mean_width": None if unbounded else mean(row["width"] for row in rows),
        "mean_interval_score": None
        if unbounded
        else mean(row["interval_score"] for row in rows),
        "aggregate_status": "infinite" if unbounded else "finite",
        "finite_only_months": len(finite),
        "finite_only_mean_width": mean(row["width"] for row in finite)
        if finite
        else None,
        "finite_only_mean_interval_score": mean(row["interval_score"] for row in finite)
        if finite
        else None,
    }
