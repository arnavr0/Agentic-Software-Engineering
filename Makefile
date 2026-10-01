.PHONY: install install-browser dev test lint typecheck

install:
	python -m pip install -e ".[dev]"

install-browser:
	python -m playwright install chromium

dev:
	python -m uvicorn agentic_tester.api.app:app

test:
	python -m pytest tests/unit -v

lint:
	python -m ruff check src tests

typecheck:
	python -m mypy src
