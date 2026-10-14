"""Capture one new official BTS source directory without issuing a prediction."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.bts_capture import capture_snapshot  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="new capture directory"
    )
    args = parser.parse_args(argv)
    try:
        receipt = capture_snapshot(args.output)
    except (OSError, ValueError, KeyError) as error:
        parser.error("\n".join([str(error), *getattr(error, "__notes__", [])]))
    print(
        f"Captured BTS Freight TSI through {receipt['last_month']} in {args.output}. "
        "availability bound retained, no issuance created"
    )


if __name__ == "__main__":
    main()
