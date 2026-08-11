"""
app.py — the FabOps application shell.

Started by `fabops-app`. Runnable directly for a test:

    python -m fabapp.ui.app --dataset <path to fab.db> --page Investigation

The shell owns three things and delegates everything else: which dataset is
open, which page is showing, and the one pass over the dataset that every page
then reads from.

**There is no default dataset, and there cannot be a useful one.** The legacy
schema v1 database is a different fab, and a v2 surface that fell back to it
would answer the wrong question with no error anywhere. So a fresh start lands
on Datasets, where a user creates or opens one — which is the productization,
rather than a path a reader has to already know.

**One pass, cached on the file's own modification time.** The audited dashboard
cached every query and never invalidated any of them, so a rebuilt database
showed stale numbers until the process was restarted. Keying the cache on the
database's mtime means a regenerated dataset is a different cache entry.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import streamlit as st

from fabapp import APP
from fabapp.config import WorkspaceError, locations
from fabapp.registry import READY, inspect
from fabapp.service import DatasetNotUsable, open_dataset
from fabapp.ui import about, datasets, explore, investigation

#: The pages, in the order the audit's information architecture puts them, with
#: the two the product adds around it: a place to get a dataset at the front,
#: and a place to understand the application at the back.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Datasets", ("Datasets",)),
    ("Explore", ("Fab Today", "Process", "Equipment", "Yield", "Defect",
                 "Wafer explorer")),
    ("Investigate", ("Investigation",)),
    ("About", ("About",)),
)

PAGES = tuple(page for _section, group in SECTIONS for page in group)

#: Pages that need an open dataset. `Datasets` and `About` do not, which is
#: what lets a first-time user reach something useful before anything exists.
NEEDS_DATASET = frozenset(PAGES) - {"Datasets", "About"}

DATASET_STATE = "dataset_path"


def _arguments() -> tuple[str, str]:
    """The opening dataset and page, from the command line or the environment.

    `fabops-app` passes them through the environment because Streamlit re-runs
    this script on every interaction; the command line is honoured too so the
    module can be run directly by a test.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dataset", default=os.environ.get("FABOPS_DATASET"))
    parser.add_argument("--page", default=os.environ.get("FABOPS_PAGE"))
    known, _rest = parser.parse_known_args(sys.argv[1:])
    return (known.dataset or "").strip(), (known.page or "").strip()


@st.cache_data(show_spinner="Running the monitors and the engine…")
def _load(db_path: str, stamp: float):
    """One pass over one dataset. `stamp` is the file's mtime (see module doc)."""
    return open_dataset(db_path)


def _current_record(opening: str):
    """The dataset this session has open, if any."""
    path = st.session_state.get(DATASET_STATE) or opening
    if not path:
        return None
    record = inspect(path)
    return record


def _sidebar(record, opening_page: str) -> str:
    st.sidebar.title("FabOps")
    if record is None:
        st.sidebar.info("No dataset open.")
    elif record.status == READY:
        st.sidebar.success(f"**{record.dataset_id}**")
        st.sidebar.caption(
            f"{record.scenario or 'scenario not in the local library'} · "
            f"seed {record.seed} · {record.horizon_days} d")
    else:
        st.sidebar.error(f"{record.status}: {record.detail}")

    labels: list[str] = []
    lookup: dict[str, str] = {}
    for section, group in SECTIONS:
        for page in group:
            label = f"{section} — {page}" if len(group) > 1 else page
            labels.append(label)
            lookup[label] = page

    default = 0
    for index, label in enumerate(labels):
        if lookup[label] == opening_page:
            default = index
            break

    chosen = st.sidebar.radio("Go to", labels, index=default,
                              label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.caption(
        "All data is synthetic. This application renders what the engine and "
        "the monitors produced and computes nothing of its own; where a "
        "candidate is highlighted, it is highlighted because the engine "
        "ranked it.")
    st.sidebar.caption(APP)
    return lookup[chosen]


def main() -> None:
    st.set_page_config(page_title="FabOps", layout="wide",
                       initial_sidebar_state="expanded")

    opening_dataset, opening_page = _arguments()
    if opening_dataset and DATASET_STATE not in st.session_state:
        st.session_state[DATASET_STATE] = opening_dataset

    try:
        locations()
    except WorkspaceError as exc:
        st.title("FabOps")
        st.error(str(exc))
        return

    record = _current_record(opening_dataset)
    page = _sidebar(record, opening_page if opening_page in PAGES
                    else ("Datasets" if record is None
                          or record.status != READY else "Fab Today"))

    if page == "Datasets":
        datasets.render(record)
        return
    if page == "About":
        about.render(record)
        return

    if record is None:
        st.title("FabOps")
        st.info("Open a dataset first — the **Datasets** page creates one or "
                "loads one that already exists.")
        return
    if record.status != READY:
        st.title("FabOps")
        st.error(f"This dataset cannot be opened — {record.status}: "
                 f"{record.detail}")
        st.caption("Pick another one on the **Datasets** page.")
        return

    st.session_state[DATASET_STATE] = str(record.db_path)
    try:
        payload = _load(str(record.db_path), record.db_path.stat().st_mtime)
    except DatasetNotUsable as exc:
        st.error(str(exc))
        return

    st.title(f"FabOps — {page}")
    st.caption(
        f"dataset {payload['dataset']['dataset_id']} · schema "
        f"{payload['dataset']['schema_version']} · rendered from "
        f"{payload['generated_by']['engine']}, "
        f"{payload['generated_by']['monitors']}")

    if page == "Investigation":
        investigation.render(payload, record)
    else:
        explore.render(page, payload, record)


main()
