.PHONY: help setup investigate app test charts notebook clean

help:
	@echo "Fab Operations Analytics — make targets:"
	@echo "  setup        build data/fab.db + star model + views"
	@echo "  investigate  run the end-to-end RCA (prints story, renders charts)"
	@echo "  app          launch the Streamlit dashboard"
	@echo "  test         run the pytest suite"
	@echo "  charts       (re)render all figures into reports/figures/"
	@echo "  notebook     rebuild and execute notebooks/investigation.ipynb"
	@echo "  clean        remove caches and the generated database"

setup:
	python -m src.build_db

investigate: setup
	python -m src.investigation

app: setup
	streamlit run app/ops_dashboard.py

test: setup
	pytest -q

charts: setup
	python -m src.charts

notebook: setup
	python build_notebook.py
	jupyter nbconvert --to notebook --execute --inplace notebooks/investigation.ipynb

clean:
	rm -rf data/fab.db data/fab_database.sql
	rm -rf .pytest_cache **/__pycache__ src/__pycache__ tests/__pycache__
	rm -rf notebooks/.ipynb_checkpoints
	@echo "cleaned (run 'make setup' to rebuild the database)."
