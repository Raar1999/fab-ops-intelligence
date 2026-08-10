"""
fabops.actions — from evidence to "what should the engineer do next".

    recommend(families, distinguishable=..., knowledge=...) -> list[Action]

The last question `PROJECT_VISION` asks, and the one the audited v1 answered
with a paragraph of static text. What replaces it is a **versioned local
knowledge table** (`knowledge.json`, schema `fabops.knowledge/v1`) keyed by the
*evidence signature* — which families of evidence led — and yielding checks an
engineer performs and containment actions they can take.

Three boundaries define what this is allowed to be, and all three come from
documents that predate it.

**It maps observations onto checks, never onto a mechanism.** No observable
channel in schema v2 identifies which mechanism acted — `classified_type` is a
draw over a hidden origin and a bin is a symptom drawn over a hidden cause — so
an engine or a report that named one would be matching a catalogue
(`DIAGNOSIS_CONTRACT.md` §5.2, §6.4). "The metrology family led, so go and
compare the subject's zonal readings against its own pre-onset window" is a
check. "This is a chamber uniformity fault" is a claim this project's data
cannot support, and it is not made here.

**It is a data file, not code** (`FABOPS_VS_FABKG_BOUNDARY.md` §3). That is the
whole of the FabKG import contract: if that project ever has a richer table, it
supplies a file of the same schema and this package loads it. No shared code,
no runtime dependency, no import in either direction — and this repository
works identically with FabKG absent, which is the only test of the boundary
that matters.

**It never invents a subject.** When the investigation abstains there is no
subject, and the recommendation says so instead of nominating the least
innocent candidate. That is the audited failure this whole project exists to
remove, and it would be easiest to reintroduce here, at the end, where a report
feels incomplete without a name.
"""
from __future__ import annotations

from fabops.actions.recommend import (ACTIONS_VERSION, DEFAULT_KNOWLEDGE_PATH,
                                      KNOWLEDGE_SCHEMA, Action, KnowledgeTable,
                                      load_knowledge, recommend)

__all__ = [
    "ACTIONS_VERSION",
    "Action",
    "DEFAULT_KNOWLEDGE_PATH",
    "KNOWLEDGE_SCHEMA",
    "KnowledgeTable",
    "load_knowledge",
    "recommend",
]
