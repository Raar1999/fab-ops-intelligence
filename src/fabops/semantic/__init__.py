"""
fabops.semantic — the analytical vocabulary over schema v2.

    with open_layer(db_path) as connection:
        rows = read(connection, "v_product_attainment", order_by="product_name")

One database path in, a connection with the layer installed out. Three
properties are the whole of the design and each one answers a defect the audit
or a later gate found.

**The layer is read-only, and that is enforced by the connection rather than by
intention.** The dataset is opened with SQLite's `mode=ro` URI and the views
are `TEMP` views, which live in the connection's own schema. Installing the
layer therefore cannot change a byte of `fab.db` — which matters because the
dataset's manifest records a SHA-256 of that file and the benchmark scores it
afterwards. A layer that wrote its views into the artifact would invalidate the
provenance of every result computed from it, and would do so silently.

**The logic is SQL, in a file, not strings scattered through Python** (ADR-002).
`layer.sql` is reviewable by someone who knows SQL and nothing about this
repository, which is the point of keeping the analytical core in SQL at all.

**Nothing here has a default database.** `fabops.config.DB_PATH` names the
*legacy v1* database, and a v2 surface that defaulted to it would answer
questions about a different fab with no error anywhere. The path is always the
caller's, and `fabops.config` is not imported.

What this layer is *not*: it is not the diagnosis engine's input. The engine
computes its own residuals from the base tables (`fabops.diagnosis.channels`)
because its queries are version-bound to a report — a view definition changing
underneath it would change reports without changing its version. The two agree
where they overlap and a test measures that; they are deliberately not one
piece of code.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Sequence

__all__ = [
    "EDGE_RADIUS_FRACTION",
    "CENTER_RADIUS_FRACTION",
    "LAYER_VERSION",
    "VIEWS",
    "layer_sql",
    "open_layer",
    "read",
    "rows",
    "schema_version_of",
]

#: Moves whenever a view's *meaning* could change, on the same rule the
#: generator and the engine hold themselves to: a consumer that pinned a number
#: to this layer must be able to tell which layer produced it.
LAYER_VERSION = "1.0.0"

#: Where "the edge" starts, as a fraction of the wafer's own radius. 0.80
#: because that is where the simulated fab's ring geometry starts; the v1
#: layer's 110 mm cut-off was calibrated against one wafer size and would mean
#: something different on another. Declared here and asserted equal to the
#: evaluator's independently declared constant by a test, so the two cannot
#: drift into two definitions of the same word.
EDGE_RADIUS_FRACTION = 0.80

#: …and where "the centre" stops. The inner third by radius, which is the v1
#: layer's 50 mm on a 150 mm radius, expressed in the unit that travels.
CENTER_RADIUS_FRACTION = 0.33

#: The schema this layer is written against. A v1 database has no `chambers`
#: table and would fail with a confusing error several joins later.
REQUIRED_SCHEMA_VERSION = "2.0"

_LAYER_PATH = Path(__file__).with_name("layer.sql")
_VIEW_NAME = re.compile(r"CREATE\s+TEMP\s+VIEW\s+(\w+)\s+AS", re.IGNORECASE)


def layer_sql() -> str:
    """The layer's SQL, exactly as it ships."""
    return _LAYER_PATH.read_text(encoding="utf-8")


#: Every view the layer declares, in declaration order (which is also
#: dependency order — a view may only reference views above it).
VIEWS: tuple[str, ...] = tuple(_VIEW_NAME.findall(layer_sql()))


def schema_version_of(connection: sqlite3.Connection) -> str:
    """The declared schema version, or `""` for a database that has none.

    A v1 database has no `dataset_meta` at all, so the missing table is one of
    the answers rather than an error: "this is not a v2 dataset" is exactly
    what the caller needs to be told.
    """
    try:
        row = connection.execute(
            "SELECT schema_version FROM dataset_meta").fetchone()
    except sqlite3.Error:
        return ""
    return "" if row is None else str(row[0])


def open_layer(db_path: Path | str) -> sqlite3.Connection:
    """Open one observable dataset read-only with the layer installed.

    Raises `FileNotFoundError` if the database is not there and `ValueError` if
    it is not schema v2 — both before the first join, because a schema mismatch
    diagnosed four joins deep reads as a bug in the query.
    """
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. `fabops.semantic` reads a schema v2 dataset "
            f"emitted by the simulator; it has no default database.")

    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        version = schema_version_of(connection)
        if version != REQUIRED_SCHEMA_VERSION:
            raise ValueError(
                f"{path} declares schema version {version!r}; "
                f"`fabops.semantic` is written against "
                f"{REQUIRED_SCHEMA_VERSION!r}. The legacy v1 database is read "
                f"by `fabops.db` and its own view layer in `sql/`.")
        connection.executescript(layer_sql())
    except Exception:
        connection.close()
        raise
    return connection


def rows(connection: sqlite3.Connection, sql: str,
         params: Sequence[Any] = ()) -> list[tuple]:
    """Run one statement and return its rows.

    A thin wrapper that exists so every caller in this package reaches the
    database the same way, and so the row-order lint has one call name to look
    for.
    """
    return connection.execute(sql, tuple(params)).fetchall()


def read(connection: sqlite3.Connection, view: str, *, order_by: str,
         where: str | None = None,
         params: Sequence[Any] = ()) -> list[tuple]:
    """`SELECT * FROM <view>` with a mandatory total order.

    `order_by` has no default on purpose. SQL lets a database return rows in
    any order unless the query states one, every consumer of this layer folds
    rows into floats, and the decision surfaces above it turn floats into
    ranks — so an unordered read is a result that depends on the query planner.
    That defect was real and latent in this repository once (ADR-031 §1); the
    cheapest place to make it unrepeatable is the signature.
    """
    if view not in VIEWS:
        raise KeyError(f"unknown view {view!r}; the layer declares "
                       f"{', '.join(VIEWS)}")
    if not order_by.strip():
        raise ValueError("order_by must name at least one column")
    clause = f" WHERE {where}" if where else ""
    return rows(connection, f"SELECT * FROM {view}{clause} "
                            f"ORDER BY {order_by}", params)


def columns(connection: sqlite3.Connection, view: str) -> tuple[str, ...]:
    """The column names of one view, in declaration order."""
    cursor = connection.execute(f"SELECT * FROM {view} LIMIT 0")
    return tuple(description[0] for description in cursor.description)


def iter_dicts(connection: sqlite3.Connection, view: str, *, order_by: str,
               where: str | None = None,
               params: Sequence[Any] = ()) -> Iterator[dict[str, Any]]:
    """`read`, as dictionaries. For presentation code, which wants names."""
    names = columns(connection, view)
    for row in read(connection, view, order_by=order_by, where=where,
                    params=params):
        yield dict(zip(names, row))
