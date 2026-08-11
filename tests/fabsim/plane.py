"""Where the hidden plane is allowed to be, and everywhere it is not.

Six slice tests used to end with two assertions about the *working tree*:

    assert not list(repository.glob("**/truth.json"))
    assert not (repository / "data" / "scenarios").exists()

They were true when they were written and they are not a property of the code
they follow. Each of those tests is a **static scan** of one module's source —
none of them runs the slice — so the tree assertions never measured anything
that test could have caused. What they encoded was a *phase* condition: at Step
3A the emitter did not exist yet, so `PHASE_1_ACCEPTANCE` could record that no
truth file and no dataset directory existed anywhere, and the scans asserted it
in passing.

The emitter exists now, and `GROUND_TRUTH_CONTRACT.md` §2 declares
`data/scenarios/<dataset_id>/truth/truth.json` to be exactly where a dataset's
hidden plane belongs — which `.gitignore` excludes for that reason. So the old
assertion says "nobody may ever have built a dataset", which contradicts the
architecture it is meant to defend, and it fires the moment anybody uses the
product for the thing the product is for.

What replaces it keeps the teeth and drops the phase condition: **a hidden-plane
artifact may exist only inside the declared dataset root.** One written into
`src/`, `app/`, `reports/`, `notebooks/`, `data/` itself or anywhere else is
still a failure, and now it is the only thing that is one.
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["DATASET_ROOT", "REPOSITORY", "hidden_plane_files_outside_the_root"]

REPOSITORY = Path(__file__).resolve().parents[2]

#: The one place a hidden plane may live (`fabsim.emit.DATASET_ROOT`).
DATASET_ROOT = REPOSITORY / "data" / "scenarios"

#: Directories that are not this repository's code and would only slow the walk
#: down. A virtual environment holding a package with a `truth.json` fixture in
#: it is not this project writing one.
_SKIP = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}


def hidden_plane_files_outside_the_root() -> list[Path]:
    """Every `truth.json` in the repository that is not in a dataset."""
    found: list[Path] = []
    for path in REPOSITORY.rglob("truth.json"):
        if _SKIP & set(path.parts):
            continue
        if DATASET_ROOT in path.parents:
            continue
        found.append(path)
    return sorted(found)
