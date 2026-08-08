.PHONY: help install setup investigate app test charts notebook clean clean-db

help:
	@echo "Fab Operations Analytics — make targets:"
	@echo "  install      pip install -e . with app/notebook/dev extras"
	@echo "  setup        build data/fab.db + star model + views"
	@echo "  investigate  run the end-to-end RCA (prints story, renders charts)"
	@echo "  app          launch the Streamlit dashboard"
	@echo "  test         run the pytest suite"
	@echo "  charts       (re)render all figures into reports/figures/"
	@echo "  notebook     rebuild and execute notebooks/investigation.ipynb"
	@echo "  clean        remove caches (leaves the committed database alone)"
	@echo "  clean-db     remove data/fab.db + fab_database.sql to force regeneration"

install:
	pip install -e ".[app,notebook,dev]"

setup:
	python -m fabops.build_db

investigate: setup
	python -m fabops.investigation

app: setup
	streamlit run app/ops_dashboard.py

test: setup
	pytest -q

charts: setup
	python -m fabops.charts

notebook: setup
	python build_notebook.py
	jupyter nbconvert --to notebook --execute --inplace notebooks/investigation.ipynb

clean:
	rm -rf .pytest_cache **/__pycache__ src/fabops/__pycache__ tests/__pycache__
	rm -rf notebooks/.ipynb_checkpoints *.egg-info src/*.egg-info
	@echo "cleaned caches (database untouched; use 'make clean-db' to force regeneration)."

clean-db:
	rm -f data/fab.db data/fab_database.sql
	@echo "removed generated database artifacts (run 'make setup' to rebuild)."
