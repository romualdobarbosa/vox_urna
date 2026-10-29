.PHONY: all bronze bronze-resultados bronze-urna silver gold test lint dashboard figuras

all: gold

bronze:
	uv run vox-urna bronze

bronze-resultados:
	uv run vox-urna bronze-resultados

# Fora do `all`: ~1 milhão de requisições, e sai com erro enquanto houver seção sem BU.
bronze-urna:
	uv run vox-urna bronze-urna --ano 2026 --turno 2

silver: bronze bronze-resultados
	uv run vox-urna silver

gold: silver
	uv run vox-urna gold

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

dashboard:
	uv run --group dashboard streamlit run app/streamlit_app.py

figuras:
	uv run --with kaleido python app/figuras.py
