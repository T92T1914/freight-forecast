"""Fetch the real time vintages of one ALFRED series once, by hand.

ALFRED needs no account and no key for this. The download page lists every
vintage date of a series. The public graph CSV endpoint returns the series as it
stood on one vintage date. This script reads the list, asks for each date once,
keeps the long table of vintage date, observation month and value, and writes a
manifest with the source, the retrieval clock and the hashes. It does not poll
and no workflow or test runs it.
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

FORM_URL = "https://alfred.stlouisfed.org/series/downloaddata?seid={seid}"
GRAPH_URL = (
    "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={seid}&vintage_date={date}"
)
AGENT = "freight-forecast-vintage-fetch"


def _get(url: str, attempts: int = 3) -> bytes:
    for attempt in range(attempts):
        try:
            with urlopen(
                Request(url, headers={"User-Agent": AGENT}), timeout=60
            ) as response:
                return response.read()
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(5)
    raise AssertionError("unreachable")


def vintage_dates(seid: str) -> list[str]:
    page = _get(FORM_URL.format(seid=seid)).decode("utf-8")
    start = page.index('id="form_selected_vintage_dates"')
    block = page[start : page.index("</select>", start)]
    dates = sorted(set(re.findall(r'<option value="(\d{4}-\d{2}-\d{2})"', block)))
    if not dates:
        raise SystemExit("the page listed no vintage dates")
    return dates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", default="TSIFRGHT")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--pause", type=float, default=1.0)
    args = parser.parse_args()
    dates = vintage_dates(args.series)
    rows = ["vintage_date,obs_date,value"]
    digest = hashlib.sha256()
    for date in dates:
        body = _get(GRAPH_URL.format(seid=args.series, date=date))
        digest.update(date.encode("ascii") + b"\n" + body)
        lines = body.decode("utf-8").splitlines()
        if not lines or not lines[0].lower().startswith(("observation_date", "date")):
            raise SystemExit(f"unexpected response for vintage {date}")
        for line in lines[1:]:
            obs, _, value = line.partition(",")
            if value and value != ".":
                rows.append(f"{date},{obs},{value}")
        time.sleep(args.pause)
    table = ("\n".join(rows) + "\n").encode("utf-8")
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    stem = args.series.lower()
    (out / f"{stem}-alfred-vintages.csv").write_bytes(table)
    manifest = {
        "schema_version": 1,
        "series": args.series,
        "source": GRAPH_URL.format(seid=args.series, date="VINTAGE_DATE"),
        "vintage_list_source": FORM_URL.format(seid=args.series),
        "retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "vintage_dates": len(dates),
        "first_vintage": dates[0],
        "last_vintage": dates[-1],
        "rows": len(rows) - 1,
        "raw_responses_sha256": digest.hexdigest(),
        "table_sha256": hashlib.sha256(table).hexdigest(),
        "account_or_key_used": False,
        "note": (
            "ALFRED vintage dates are when FRED took the data in. "
            "They trail the BTS release."
        ),
    }
    (out / f"{stem}-alfred-vintages.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
