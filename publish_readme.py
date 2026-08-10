"""
publish_readme.py — write the README's benchmark section from the measurement.

    python publish_readme.py                 # regenerate the section
    python publish_readme.py --check         # fail if it is out of date

A maintainer script at the repository root, next to `build_notebook.py`, and it
lives here for a reason rather than for convenience.

**`fabeval` writes nothing.** ADR-024 §1 makes that an absolute, lexically
checkable rule — the evaluation plane calls no `write_text`, no `mkdir`, no
`commit` — because a grader that can write is a grader that can contaminate the
thing it grades. "Writes nothing except a README" is not a rule anybody can
enforce by reading, and the first exception is what turns a guard into a
convention. So `fabeval.publish` renders and this file writes.

The rendering, the results document and the drift check all belong to
`fabeval.publish`; the only thing here is the file handling and an exit code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from fabeval.publish import apply_block, extract_block, render_markdown

REPO = Path(__file__).resolve().parent
RESULTS = REPO / "docs" / "benchmark_results.json"
README = REPO / "README.md"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="publish_readme.py",
        description=("Render the README's benchmark section from a committed "
                     "fabeval.results/v1 document. The numbers come from "
                     "`fabops-benchmark --emit-json`; nothing here measures "
                     "anything."))
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--readme", type=Path, default=README)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the README is out of date")
    arguments = parser.parse_args(argv)

    document = json.loads(arguments.results.read_text(encoding="utf-8"))
    block = render_markdown(document)
    text = arguments.readme.read_text(encoding="utf-8")

    if arguments.check:
        if extract_block(text) != block:
            print(f"{arguments.readme.name} is out of date against "
                  f"{arguments.results.name}; run this without --check",
                  file=sys.stderr)
            return 1
        print(f"{arguments.readme.name} matches {arguments.results.name}")
        return 0

    updated = apply_block(text, block)
    if updated == text:
        print(f"{arguments.readme.name} already matches "
              f"{arguments.results.name}")
        return 0
    arguments.readme.write_text(updated, encoding="utf-8")
    print(f"wrote the benchmark section into {arguments.readme.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
