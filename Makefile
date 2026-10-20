.PHONY: all bronze test lint

all: bronze

bronze:
	uv run vox-urna bronze

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
