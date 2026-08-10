"""
cli.py — `fabops-monitor <path-to-fab.db>`.

Prints the four families' signals as a table a human reads, or the whole
report as JSON with `--json`. It opens no file of its own: the report goes to
standard output and the caller decides where that lands, which is what keeps
"exactly one database is opened" true of a command line as well as of a
function.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from fabops.monitors import FAMILIES, MONITOR, monitor

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fabops-monitor",
        description=("Watch one observable dataset: process, equipment, yield "
                     "and defect monitors over a schema v2 fab.db. Reports "
                     "signals, never a conclusion — ranking candidates is "
                     "`fabops-diagnose`."))
    parser.add_argument("database", help="path to fab.db")
    parser.add_argument("--family", action="append", choices=FAMILIES,
                        help="run only this family (repeatable)")
    parser.add_argument("--json", action="store_true",
                        help="print the whole report as JSON")
    arguments = parser.parse_args(argv)

    families = tuple(arguments.family) if arguments.family else FAMILIES
    report = monitor(arguments.database, families=families)

    if arguments.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=False))
        return 0

    print(f"{MONITOR}  dataset {report.dataset_id}  "
          f"horizon {report.horizon_days} d")
    grouped = report.by_family()
    for family in families:
        signals = grouped.get(family, [])
        print(f"\n[{family}] {len(signals)} signal(s)")
        for signal in signals[:40]:
            print(f"  day {signal.day_index:>3}  {signal.entity:<14} "
                  f"{signal.channel:<28} {signal.rule:<32} "
                  f"z={signal.z:+.2f}")
        if len(signals) > 40:
            print(f"  … {len(signals) - 40} more")

    print(f"\n# {len(report.signals)} signal(s) across "
          f"{len(families)} famil{'y' if len(families) == 1 else 'ies'}. "
          f"A healthy fab produces signals too — the number that means "
          f"something is the rate against fault-free worlds.", file=sys.stderr)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(main())
