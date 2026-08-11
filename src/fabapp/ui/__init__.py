"""
fabapp.ui — the screens, and nothing behind them.

Every module in this package draws. None of them computes: the numbers arrive
from `fabops.report.workspace`, the pictures from `fabops.report.figures`, the
verdict from `fabops.diagnosis` through `fabops.report`, and the criteria
checklists from `fabapp.explain`, which is itself a restatement of the engine's
own artifact.

Three properties are checked by `tests/fabapp/test_ui_guards.py` over every
module here rather than asserted in this docstring: no module imports the
simulator or the evaluator, no module opens a database or writes SQL, and no
module names a tool, a chamber, a product or a mechanism. The last is the
audited defect this whole line of work exists to prevent — a dashboard that
highlighted a row because a constant said so.
"""
from __future__ import annotations

__all__: list[str] = []
