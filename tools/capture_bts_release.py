"""Capture a bulletin or prepare an outcome for separate read only ledger review."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.bts_release import capture_release, prepare_candidate  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Only a complete candidate.json marked ready_for_ledger_review with its "
            "matching frozen observation is eligible for separate ledger review. "
            "Partial or failed attempts are not ready. "
            "No command appends an observation."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="one fixed official monthly bulletin")
    capture.add_argument("--url", required=True)
    capture.add_argument(
        "--output", type=Path, required=True, help="new attempt directory"
    )
    prepare = commands.add_parser("prepare", help="freeze a candidate, never append it")
    prepare.add_argument("--capture", type=Path, required=True)
    prepare.add_argument("--issuance-event", type=Path, required=True)
    prepare.add_argument(
        "--key", required=True, help="retain this exact key with the payload"
    )
    prepare.add_argument(
        "--review", type=Path, help="explicit publication and value evidence"
    )
    prepare.add_argument(
        "--output", type=Path, required=True, help="new attempt directory"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            result = capture_release(args.url, args.output)
            print(
                f"Captured bulletin for review in {args.output}. No outcome admitted."
            )
        else:
            result = prepare_candidate(
                args.capture,
                args.issuance_event,
                args.key,
                args.output,
                review=args.review,
            )
            print(f"{result['status']}: {args.output}. No observation appended.")
            if result["status"] == "not_admitted":
                for reason in result["reasons"]:
                    print(reason)
                return 2
    except (OSError, ValueError, KeyError) as error:
        parser.error("\n".join([str(error)[:1024], *getattr(error, "__notes__", [])]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
