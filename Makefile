.PHONY: help install test \
        setup investigate app charts notebook \
        dataset diagnose monitor report workspace benchmark publish \
        clean clean-db clean-datasets

# Two systems live in this repository and the targets are grouped accordingly.
# The v2 targets take DATASET=<path to fab.db>; there is no default, because
# `fabops.config.DB_PATH` names the *legacy v1* database and a v2 command that
# fell back to it would answer about a different fab with no error anywhere.
DATASET ?=
BENCH_ROOT ?= .bench

help:
	@echo "Fab Ops Intelligence — make targets"
	@echo ""
	@echo "  install        pip install -e . with app/notebook/dev extras"
	@echo "  test           run the pytest suite"
	@echo ""
	@echo " the engine (schema v2; pass DATASET=path/to/fab.db)"
	@echo "  dataset        build one scenario dataset (SCENARIO=, SEED=, ROOT=)"
	@echo "  diagnose       run the answer-blind engine on DATASET"
	@echo "  monitor        run the four monitor families on DATASET"
	@echo "  report         emit the full decision artifact for DATASET"
	@echo "  workspace      launch the investigation workspace on DATASET"
	@echo "  benchmark      build a population and score the engine (BENCH_ROOT=)"
	@echo "  publish        regenerate the README benchmark section from it"
	@echo ""
	@echo " the legacy v1 demo (ADR-010: kept until something is better on its surface)"
	@echo "  setup          build data/fab.db + star model + views"
	@echo "  investigate    run the narrated ETCH-02 demo (conclusion is a constant)"
	@echo "  app            launch the legacy dashboard"
	@echo "  charts         (re)render all figures into reports/figures/"
	@echo "  notebook       rebuild and execute notebooks/investigation.ipynb"
	@echo ""
	@echo "  clean          remove caches (leaves the committed database alone)"
	@echo "  clean-db       remove data/fab.db + fab_database.sql"
	@echo "  clean-datasets remove generated schema v2 datasets under data/scenarios/"

install:
	pip install -e ".[app,notebook,dev]"

test: setup
	pytest -q

# ------------------------------------------------------------------ schema v2

SCENARIO ?= chamber_edge_uniformity
SEED ?= 42
ROOT ?= data/scenarios

dataset:
	python -c "from pathlib import Path; from fabsim.emit import build_dataset; \
	from fabsim.scenario import load_scenario; from fabsim.world import load_world; \
	d = build_dataset(load_scenario(Path('scenarios/$(SCENARIO).json')), $(SEED), \
	world=load_world('baseline_fab_v1'), root=Path('$(ROOT)')); print(d.db_path)"

require-dataset:
	@test -n "$(DATASET)" || (echo "set DATASET=path/to/fab.db (make dataset builds one)"; exit 1)

diagnose: require-dataset
	fabops-diagnose "$(DATASET)"

monitor: require-dataset
	fabops-monitor "$(DATASET)"

report: require-dataset
	fabops-report "$(DATASET)" --summary

workspace: require-dataset
	streamlit run app/investigation_workspace.py -- --dataset "$(DATASET)"

benchmark:
	fabops-benchmark --root "$(BENCH_ROOT)" --population both \
	                 --emit-json > docs/benchmark_results.json

publish:
	python publish_readme.py

# ------------------------------------------------------- the legacy v1 demo

setup:
	python -m fabops.build_db

investigate: setup
	python -m fabops.investigation

app: setup
	streamlit run app/ops_dashboard.py

charts: setup
	python -m fabops.charts

notebook: setup
	python build_notebook.py
	jupyter nbconvert --to notebook --execute --inplace notebooks/investigation.ipynb

# ------------------------------------------------------------------ cleaning

clean:
	rm -rf .pytest_cache **/__pycache__ src/fabops/__pycache__ tests/__pycache__
	rm -rf notebooks/.ipynb_checkpoints *.egg-info src/*.egg-info
	@echo "cleaned caches (database untouched; use 'make clean-db' to force regeneration)."

clean-db:
	rm -f data/fab.db data/fab_database.sql
	@echo "removed generated database artifacts (run 'make setup' to rebuild)."

clean-datasets:
	rm -rf data/scenarios
	@echo "removed generated schema v2 datasets (they are reproducible from config, world, seed and version)."
