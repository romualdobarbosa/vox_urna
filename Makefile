.PHONY: all bronze silver test lint

all: silver

bronze:
	uv run vox-urna bronze

silver: bronze
	uv run vox-urna silver

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
