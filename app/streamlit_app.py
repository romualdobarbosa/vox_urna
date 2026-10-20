import polars as pl
import streamlit as st

import dados

COR = {"lula": "#C8102E", "flavio": "#1F4E9C"}

st.set_page_config(page_title="Vox Urna: projeção do 2º turno de 2026", layout="wide")


@st.cache_data
def gold() -> dados.Gold:
    return dados.carregar()


g = gold()
nomes = dados.finalistas(g.lados)

st.warning(dados.AVISO, icon="⚠️")
st.title("Projeção do 2º turno presidencial de 2026")
st.caption(
    "Os votos do 1º turno de 2026, seção por seção, redistribuídos segundo a transferência "
    "de votos entre turnos observada em eleições anteriores."
)

# Manchete: a faixa entre os cenários, nunca um número único.
faixa = dados.faixa_brasil(g.projecao)
colunas = st.columns(2)
for coluna, lado in zip(colunas, dados.LADOS, strict=True):
    baixo, alto = faixa[lado]
    coluna.markdown(
        f"<div style='border-left: 6px solid {COR[lado]}; padding-left: 12px'>"
        f"<div style='font-size: 1.1rem'>{nomes[lado]}</div>"
        f"<div style='font-size: 2.4rem; font-weight: 700; color: {COR[lado]}'>"
        f"{dados.pct(baixo)} a {dados.pct(alto)}</div>"
        f"<div style='opacity: 0.7'>dos votos válidos, conforme o cenário</div></div>",
        unsafe_allow_html=True,
    )

match = dados.taxa_match_ligacao(g.taxa_match, 2022)
st.caption(
    f"Taxa de match: {dados.pct(match.get('municipio_zona', 0.0), 2)} do eleitorado de 2026 "
    f"ligado à mesma zona e município de 2022, {dados.pct(match.get('municipio', 0.0), 2)} "
    f"pelo município e {dados.pct(match.get('uf', 0.0), 2)} pela UF."
)

st.divider()
st.subheader("Cenários")
ids = dados.cenarios(g.projecao)
cenario = st.radio(
    "Escolha o cenário",
    ids,
    format_func=lambda c: dados.CENARIOS[c].titulo if c in dados.CENARIOS else c,
    horizontal=True,
)
if cenario in dados.CENARIOS:
    st.markdown(f"**Suposições:** {dados.CENARIOS[cenario].suposicoes}")

brasil = g.projecao.filter((pl.col("nivel") == "brasil") & (pl.col("cenario") == cenario)).row(
    0, named=True
)
colunas = st.columns(4)
for coluna, lado in zip(colunas[:2], dados.LADOS, strict=True):
    coluna.metric(
        nomes[lado],
        dados.pct(brasil[f"pct_{lado}_validos"]),
        f"{dados.inteiro(brasil[lado])} votos",
        delta_color="off",
    )
colunas[2].metric("Brancos e nulos", dados.inteiro(brasil["branco"] + brasil["nulo"]))
colunas[3].metric("Abstenção", dados.pct(brasil["abstencao"] / brasil["aptos"]))

st.subheader("Por UF")
uf = dados.tabela_uf(g.projecao, cenario).with_columns(
    pl.col("pct_lula_validos", "pct_flavio_validos", "margem_lula") * 100
)
st.dataframe(
    uf.drop("uf"),
    hide_index=True,
    width="stretch",
    column_config={
        "nome": "UF",
        "aptos": st.column_config.NumberColumn("Eleitores aptos", format="localized"),
        "pct_lula_validos": st.column_config.ProgressColumn(
            f"{nomes['lula']} (% válidos)", format="%.1f%%", min_value=0, max_value=100
        ),
        "pct_flavio_validos": st.column_config.ProgressColumn(
            f"{nomes['flavio']} (% válidos)", format="%.1f%%", min_value=0, max_value=100
        ),
        "margem_lula": st.column_config.NumberColumn(
            "Diferença (p.p., Lula − Flávio)", format="%+.1f"
        ),
    },
)

st.divider()
with st.expander("Método e limitações"):
    st.markdown(
        """
- **Transferência de votos.** Em cada zona eleitoral, uma regressão restrita (Goodman) sobre
  as seções estima que fração dos eleitores de cada categoria do 1º turno foi para cada
  categoria do 2º turno na eleição de referência. A projeção aplica essas frações ao
  1º turno de 2026.
- **Inferência ecológica.** O voto individual não é observável, só o saldo de cada seção.
  As frações descrevem zonas, não pessoas.
- **Suposição central.** Os eleitores de cada lado, de outros candidatos, brancos, nulos e
  abstenção se comportam como na eleição de referência. A faixa entre os cenários é a única
  medida de incerteza; não há intervalo estatístico.
- **Fonte.** Boletins de urna do TSE (Portal de Dados Abertos), cargo Presidente.
"""
    )
