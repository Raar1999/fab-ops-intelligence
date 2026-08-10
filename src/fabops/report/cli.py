"""
cli.py — `fabops-report <path-to-fab.db> [--subject TOOL/CHAMBER]`.

Prints the full `fabops.report/v1` artifact as JSON, or a readable summary with
`--summary`. Like the other two commands it opens no file of its own: the
artifact goes to standard output and the caller decides where it lands.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from fabops.report import REPORT, build_report

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fabops-report",
        description=("Investigate one observable dataset and emit the full "
                     "decision-support artifact: conclusion, impact, "
                     "containment ranking and recommended checks."))
    parser.add_argument("database", help="path to fab.db")
    parser.add_argument("--subject", default=None,
                        help=("quantify impact for this entity instead of the "
                              "engine's leading candidate — 'TOOL/CHAMBER' or "
                              "'TOOL'. The artifact records that the subject "
                              "was supplied rather than concluded."))
    parser.add_argument("--step", default=None,
                        help="restrict impact to one step name")
    parser.add_argument("--knowledge", default=None,
                        help=("an optional replacement knowledge table "
                              "(fabops.knowledge/v1). Absent or invalid falls "
                              "back to the built-in one."))
    parser.add_argument("--summary", action="store_true",
                        help="print a readable summary instead of JSON")
    arguments = parser.parse_args(argv)

    report = build_report(arguments.database, subject=arguments.subject,
                          step_name=arguments.step,
                          knowledge_path=arguments.knowledge)

    if not arguments.summary:
        print(report.to_json())
        return 0

    investigation = report.investigation
    print(f"{REPORT}  dataset {report.dataset_id}")
    abstention = investigation.get("abstention", {})
    print(f"\nconclusion: "
          f"{'insufficient evidence' if investigation.get('insufficient_evidence') else 'candidate offered'}"
          f"  (family-wise p = {abstention.get('p_familywise')}, "
          f"alpha = {abstention.get('alpha')})")

    if report.subject:
        print(f"subject: {report.subject['id']} "
              f"({report.subject['kind']}, {report.subject['source']}-chosen, "
              f"step {report.subject.get('step_name')})")
    else:
        print("subject: none — the evidence names nobody")

    if report.impact:
        impact = report.impact
        print(f"\nimpact: {impact['exposed_wafers']} exposed wafers, "
              f"within-product deficit "
              f"{impact['within_product_deficit_pts']:+.3f} "
              f"± {impact['standard_error_pts']:.3f} pts")
        print(f"        die delta {impact['estimated_die_delta']:+.0f} of "
              f"{impact['exposed_die']} exposed die "
              f"(negative = shortfall against within-product peers)")
        print(f"        standing among peers z = "
              f"{impact['standing_z_among_peers']:+.2f}  "
              f"distinguishable from benign variation: "
              f"{impact['distinguishable_from_benign_variation']}")

    if report.containment:
        print(f"\ncontainment — most exposed lots at "
              f"{report.containment['step_name']}:")
        for row in report.containment["lots_ranked_by_exposure"][:5]:
            print(f"   lot {row['lot_id']:<4} {row['product_name']:<12} "
                  f"{row['exposed_wafers']}/{row['lot_wafers']} wafers "
                  f"({row['share']:.0%})")

    print("\nrecommended:")
    for action in report.actions:
        print(f"  [{action.kind}] {action.text}")

    print(f"\n# knowledge table: {report.provenance['knowledge']}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(main())
