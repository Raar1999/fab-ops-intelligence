"""The replacement guards, shown failing on what they exist to catch.

Six slice tests had a working-tree assertion swapped for a narrower one at the
productization gate (`tests/fabsim/plane.py` says why). Narrowing a guard is
the move that quietly turns a check into decoration, so both halves of the new
rule are exercised here against a planted violation — and both halves are shown
*not* to fire on the arrangement the architecture actually declares.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import (DATASET_ROOT, datasets_added_during_the_session,
                            pytest_sessionstart)
from tests.fabsim.plane import (REPOSITORY,
                                hidden_plane_files_outside_the_root)


def test_the_repository_currently_holds_no_stray_hidden_plane():
    """The real assertion, on the real tree."""
    assert hidden_plane_files_outside_the_root() == []


def test_the_declared_dataset_root_is_where_the_hidden_plane_belongs():
    """`GROUND_TRUTH_CONTRACT` §2 and `fabsim.emit.DATASET_ROOT` agree, and the
    guard is written against that location rather than against a second copy of
    it."""
    from fabsim.emit import DATASET_ROOT as EMITTER_ROOT

    from tests.fabsim.plane import DATASET_ROOT as GUARD_ROOT

    assert EMITTER_ROOT == GUARD_ROOT == REPOSITORY / "data" / "scenarios"


def test_the_guard_fires_on_a_hidden_plane_written_outside_a_dataset(tmp_path,
                                                                     monkeypatch):
    """The mutation: a slice that wrote an answer key into the source tree.

    Planted under a temporary repository root rather than the real one, so the
    check that proves the guard works cannot itself leave one behind.
    """
    import tests.fabsim.plane as plane

    fake = tmp_path / "repo"
    (fake / "src" / "fabsim").mkdir(parents=True)
    (fake / "data" / "scenarios" / "scn-x" / "truth").mkdir(parents=True)

    legitimate = fake / "data" / "scenarios" / "scn-x" / "truth" / "truth.json"
    legitimate.write_text(json.dumps({"events": []}), encoding="utf-8")

    monkeypatch.setattr(plane, "REPOSITORY", fake)
    monkeypatch.setattr(plane, "DATASET_ROOT", fake / "data" / "scenarios")

    # A dataset's own hidden plane is where it belongs, and does not fire.
    assert plane.hidden_plane_files_outside_the_root() == []

    # One written into the source tree is the defect, and does.
    stray = fake / "src" / "fabsim" / "truth.json"
    stray.write_text("{}", encoding="utf-8")
    assert plane.hidden_plane_files_outside_the_root() == [stray]


def test_the_session_snapshot_notices_a_dataset_the_suite_added(monkeypatch,
                                                                tmp_path):
    """The other half: the suite must not write into the repository's root.

    The snapshot is what makes "the suite added nothing" measurable on a
    machine that already holds datasets, so it has to be able to see one
    appear.
    """
    import tests.conftest as shared

    root = tmp_path / "scenarios"
    root.mkdir()
    (root / "scn-already-here").mkdir()

    monkeypatch.setattr(shared, "DATASET_ROOT", root)
    # `pytest_sessionstart` rebinds this module global, so it is handed to
    # monkeypatch as well — otherwise this test would leave the *session's*
    # snapshot pointing at a temporary directory, and the check that the suite
    # wrote nothing into the repository would start accusing it of everything.
    monkeypatch.setattr(shared, "DATASETS_AT_SESSION_START", frozenset())
    shared.pytest_sessionstart(None)
    assert shared.datasets_added_during_the_session() == []

    (root / "scn-written-by-a-test").mkdir()
    assert shared.datasets_added_during_the_session() == [
        "scn-written-by-a-test"]


def test_the_snapshot_survives_a_root_that_does_not_exist(monkeypatch,
                                                          tmp_path):
    """A clean clone has no dataset root at all, and that is not a failure."""
    import tests.conftest as shared

    monkeypatch.setattr(shared, "DATASET_ROOT", tmp_path / "never-created")
    monkeypatch.setattr(shared, "DATASETS_AT_SESSION_START", frozenset())
    shared.pytest_sessionstart(None)
    assert shared.datasets_added_during_the_session() == []


def test_the_real_session_snapshot_was_taken_before_any_test_ran():
    """`pytest_sessionstart` runs before collection, so a dataset present in
    the tree when the suite began is baseline rather than an accusation."""
    assert callable(pytest_sessionstart)
    assert isinstance(DATASET_ROOT, Path)
    assert datasets_added_during_the_session() == []
