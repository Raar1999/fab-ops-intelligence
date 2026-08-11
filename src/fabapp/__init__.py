"""
fabapp — the product plane: one application over the three that already exist.

    fabsim      writes both planes                  generation
    fabops      reads the observable plane          analysis
    fabeval     reads both, writes nothing          evaluation
    fabapp      generates, then analyses            product      <- this one

ADR-013 gave three actors three privileges and a direction of travel. A product
is the first thing in this repository that legitimately needs two of them in
one process: a person clicks *create a dataset* and then clicks *investigate
it*. That is a fourth plane, and ADR-037 declares it with its own privilege row
rather than letting it be an exception to somebody else's.

**What this plane may do.** Import `fabsim` to build a dataset and `fabops` to
read one. Read the scenario configurations, because choosing one is the user's
own act. Present what those two packages return.

**What it may not do, and how each is enforced rather than promised.**

* *It may not reach the hidden plane.* Generation goes through
  `fabsim.emit.build_observable`, which returns a handle carrying a database
  path and observable provenance — there is no field the answer key could
  arrive through, and no directory to join a name onto. A lint additionally
  rejects the token anywhere in this package, and a second one rejects a call
  to the two-plane builder.
* *It may not import the evaluator.* `fabeval` is the one component that holds
  the answer key and the report at once. A product that imported it could
  score the dataset it is displaying, and the screen would stop being blind.
* *It may not decide anything.* No statistic, no threshold, no ranking and no
  conclusion is computed here. Every number on every screen is produced by
  `fabops` and rendered by this package, which is the property ADR-035
  established for the workspace and this package inherits wholesale.
* *It may not let the scenario reach the analysis.* The user picks a scenario;
  the engine is handed a path. Between those two facts this package carries the
  slug in a catalog that no analysis function can read, and a test asserts the
  rendered investigation is byte-identical with the catalog present and absent.

**What it deliberately is not.** It is not a second implementation of anything.
The pages render `fabops.report.workspace`; the investigation renders
`fabops.report.build_report`; the figures are `fabops.report.figures`. If a
number needs computing that `fabops` does not compute, it belongs in `fabops`.
"""
from __future__ import annotations

__all__ = ["APP", "APP_VERSION"]

#: Moves whenever this plane's *contract* moves — a new screen, a changed
#: registry format, a changed launcher. It cannot move a number, because this
#: package computes none; what it versions is the presentation and the
#: orchestration, which is what a reader needs to know when a screenshot and a
#: repository disagree.
APP_VERSION = "1.0.0"
APP = f"fabapp/{APP_VERSION}"
