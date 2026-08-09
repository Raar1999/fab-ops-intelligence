"""
cli.py — `fabops-diagnose <path-to-fab.db>`.

A thin wrapper and deliberately nothing more: it parses one argument, calls
`diagnose`, and prints the artifact as JSON on standard output. It opens no
file - the report goes to stdout and the caller decides where that lands - so
the "exactly one database is opened" guarantee survives having a command line.

It exists because `fabops-investigate` already points at the *legacy narrated
demo*, whose conclusion is a constant in `fabops.config` (ADR-003 grandfathers
it, ADR-010 keeps it until its replacement is strictly better on that surface).
A reader who typed the obvious command would otherwise get the hard-coded
story and reasonably believe they had run the engine. The two are now
separately reachable and separately named.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from fabops.diagnosis import ENGINE, diagnose

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fabops-diagnose",
        description=("Investigate one observable dataset and print a "
                     "fabops.investigation/v1 report. Takes a path to a "
                     "schema v2 fab.db and nothing else."))
    parser.add_argument("database", help="path to fab.db")
    parser.add_argument("--indent", type=int, default=2,
                        help="JSON indentation (0 for one line)")
    arguments = parser.parse_args(argv)

    report = diagnose(arguments.database)
    print(json.dumps(report.to_dict(),
                     indent=arguments.indent or None, sort_keys=False))
    verdict = ("insufficient evidence" if report.insufficient_evidence
               else f"leading candidate {report.top.entity.key}")
    print(f"# {ENGINE}: {verdict} "
          f"(family-wise p = {report.abstention['p_familywise']})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
