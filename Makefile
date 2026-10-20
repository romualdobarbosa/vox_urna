.PHONY: all bronze silver gold test lint

all: gold

bronze:
	uv run vox-urna bronze

silver: bronze
	uv run vox-urna silver

gold: silver
	uv run vox-urna gold

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
