.PHONY: wheel develop clean test lint format

wheel:
	uv build --wheel

develop: wheel
	uv pip install -e . --force-reinstall

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/

format:
	clang-format -i src/*.cpp src/*.h
	uv run ruff format src/ tests/