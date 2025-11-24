.PHONY: build clean test lint format

build:
	uv build

clean:
	rm -rf _build _deps dist *.egg-info

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/
