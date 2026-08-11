"""
Dataset discovery, and the four ways a dataset can fail to be one.

The registry is where the product's honesty about its own inputs lives. Every
case below is one somebody will actually hit — a half-written build, a path
typed with a typo, the legacy database picked out of a file browser because it
is the one called `fab.db` that a search finds first — and each has to produce
a sentence rather than a stack trace.

The legacy case is the load-bearing one. Schema v1 and v2 share table names
where the intent carries over, so a product that opened a v1 database would
answer about a different fab with no error anywhere.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from fabapp import registry

REPO = Path(__file__).resolve().parents[2]


def test_discovery_finds_the_datasets_and_reads_their_provenance(
        populated_root):
    records = registry.discover(populated_root)
    assert len(records) == 2
    assert all(record.usable for record in records)
    for record in records:
        assert record.dataset_id.startswith("scn-")
        assert record.schema_version == "2.0"
        assert record.seed == 42
        assert record.horizon_days == 84
        assert record.has_manifest
        assert record.fabsim_version
        assert len(record.content_sha256) == 64
        assert len(record.build_fingerprint) == 64
        assert record.size_bytes > 0
        assert record.row_counts["wafers"] > 0


def test_the_scenario_is_recovered_without_anything_having_been_written(
        populated_root, faulted, null):
    """The browser's scenario column, and the reason there is no state file."""
    by_id = {record.dataset_id: record
             for record in registry.discover(populated_root)}
    assert by_id[faulted.record.dataset_id].scenario == "chamber_edge_uniformity"
    assert by_id[null.record.dataset_id].scenario == "null_baseline"

    catalogue = list(populated_root.glob("*.json"))
    assert not catalogue, (
        f"the product wrote state into the dataset root: {catalogue}")


def test_a_dataset_from_a_library_this_installation_lacks_is_honest(
        populated_root, faulted, tmp_path):
    """`None`, not a guess. A wrong provenance line is worse than an absent
    one, and this is the case that produces one: a dataset built somewhere
    else, from a configuration that is not here."""
    empty = tmp_path / "no-scenarios"
    empty.mkdir()
    record = registry.inspect(faulted.record.db_path)
    assert record.scenario == "chamber_edge_uniformity"

    from fabapp import scenarios

    assert scenarios.slug_for_scenario_id(record.scenario_id, empty) is None


def test_the_legacy_v1_database_is_refused_and_named(monkeypatch):
    """The hazard the whole registry exists to prevent."""
    legacy = REPO / "data" / "fab.db"
    if not legacy.is_file():
        pytest.skip("the legacy v1 database has not been built here")
    record = registry.inspect(legacy)
    assert record.status == registry.INVALID
    assert not record.usable
    assert "not a schema v2 dataset" in record.detail
    assert "legacy schema v1" in record.detail


def test_a_v1_shaped_database_is_refused_even_where_it_is_not_the_legacy_one(
        tmp_path):
    """Validation is by declared schema, never by path.

    Renaming the legacy database, or pointing the product at some other v1
    one, must change nothing — otherwise the check is a filename comparison
    wearing a schema check's clothes.
    """
    impostor = tmp_path / "elsewhere" / "fab.db"
    impostor.parent.mkdir(parents=True)
    connection = sqlite3.connect(str(impostor))
    connection.execute("CREATE TABLE wafers (wafer_id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    record = registry.inspect(impostor)
    assert record.status == registry.INVALID
    assert "no schema version" in record.detail
    assert "legacy schema v1" not in record.detail, (
        "a database that is not the legacy one was described as though it were")


def test_a_missing_dataset_says_so(tmp_path):
    record = registry.inspect(tmp_path / "nothing" / "fab.db")
    assert record.status == registry.MISSING
    assert not record.usable
    assert "no database at" in record.detail


def test_a_file_that_is_not_a_database_is_refused(tmp_path):
    path = tmp_path / "fab.db"
    path.write_text("this is not a database", encoding="utf-8")
    record = registry.inspect(path)
    assert record.status == registry.INVALID
    assert not record.usable


def test_a_dataset_with_no_material_in_it_is_empty_rather_than_ready(
        faulted, tmp_path):
    """A build that was interrupted leaves a well-formed database with nothing
    in it. Every screen downstream would render blank panels and say nothing."""
    directory = tmp_path / "cut-short"
    directory.mkdir()
    shutil.copy2(faulted.record.db_path, directory / "fab.db")

    connection = sqlite3.connect(str(directory / "fab.db"))
    connection.execute("PRAGMA foreign_keys = OFF")
    for table in ("wafer_yield", "die_bins", "defects", "inspections",
                  "metrology", "run_measurements", "runs", "wafers"):
        connection.execute(f"DELETE FROM {table}")
    connection.commit()
    connection.close()

    record = registry.inspect(directory / "fab.db")
    assert record.status == registry.EMPTY
    assert not record.usable
    assert "did not finish" in record.detail


def test_discovery_reports_a_broken_dataset_rather_than_skipping_it(
        populated_root, tmp_path):
    """A directory holding a broken database is the case somebody needs to
    see; a list that quietly omitted it would leave them wondering."""
    root = tmp_path / "mixed"
    root.mkdir()
    shutil.copytree(sorted(populated_root.iterdir())[0], root / "good")
    (root / "broken").mkdir()
    (root / "broken" / "fab.db").write_text("nope", encoding="utf-8")
    (root / "not-a-dataset").mkdir()

    records = registry.discover(root)
    assert len(records) == 2
    assert [record.usable for record in records] == [True, False]


def test_discovery_of_a_root_that_does_not_exist_is_empty(tmp_path):
    assert registry.discover(tmp_path / "never-created") == ()


def test_a_reference_resolves_from_an_id_a_directory_or_a_file(
        populated_root, faulted):
    directory = faulted.record.db_path.parent
    for reference in (faulted.record.dataset_id, str(directory),
                      str(faulted.record.db_path),
                      f'  "{faulted.record.db_path}"  '):
        record = registry.resolve(reference, populated_root)
        assert record.usable, reference
        assert record.dataset_id == faulted.record.dataset_id


def test_an_unresolvable_reference_says_what_was_searched(populated_root):
    record = registry.resolve("scn-not-a-real-dataset", populated_root)
    assert record.status == registry.MISSING
    assert str(populated_root) in record.detail
    assert registry.resolve("   ", populated_root).status == registry.MISSING


def test_the_record_carries_no_field_naming_the_hidden_plane(faulted):
    """The manifest inventories both planes; the record projects onto an
    allowlist, so the field that names the other one has no route to a
    screen."""
    payload = registry.inspect(faulted.record.db_path).to_dict()
    assert "files" not in payload
    assert "truth" not in str(payload).lower()
    assert "files" not in registry.MANIFEST_FIELDS
