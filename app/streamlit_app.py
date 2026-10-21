import polars as pl
import streamlit as st

import dados
import mapa
from mapa import COR

st.set_page_config(page_title="Vox Urna: projeção do 2º turno de 2026", layout="wide")


@st.cache_data
def gold() -> dados.Gold:
    return dados.carregar()


@st.cache_data
def malha_municipio(uf: str | None) -> dict:
    """A malha municipal inteira ou só a de uma UF, para não mandar o Brasil todo à toa."""
    malha = mapa.malha("municipio")
    if uf is None:
        return malha
    return {**malha, "features": [f for f in malha["features"] if f["properties"]["uf"] == uf]}


def em_pct(tabela: pl.DataFrame) -> pl.DataFrame:
    """As frações das tabelas em pontos percentuais, como as colunas mostram."""
    return tabela.with_columns(
        pl.col("pct_lula_validos", "pct_flavio_validos", "margem_lula") * 100
    )


g = gold()
nomes = dados.finalistas(g.lados)


def config_colunas(rotulo: str) -> dict:
    """A configuração das colunas das tabelas de UF e de município."""
    return {
        "nome": rotulo,
        "aptos": st.column_config.NumberColumn("Eleitores aptos", format="localized"),
        "pct_lula_validos": st.column_config.ProgressColumn(
            f"{nomes['lula']} (% válidos)",
            format="%.1f%%",
            min_value=0,
            max_value=100,
            color=COR["lula"],
        ),
        "pct_flavio_validos": st.column_config.ProgressColumn(
            f"{nomes['flavio']} (% válidos)",
            format="%.1f%%",
            min_value=0,
            max_value=100,
            color=COR["flavio"],
        ),
        "margem_lula": st.column_config.NumberColumn(
            "Diferença (p.p., Lula − Flávio)", format="%+.1f"
        ),
    }


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
        help=f"{dados.inteiro(brasil[lado])} votos projetados",
    )
colunas[2].metric("Brancos e nulos", dados.inteiro(brasil["branco"] + brasil["nulo"]))
colunas[3].metric("Abstenção", dados.pct(brasil["abstencao"] / brasil["aptos"]))

st.subheader("Mapa")
st.caption(
    f"Diferença em pontos percentuais dos votos válidos: vermelho onde {nomes['lula']} fica "
    f"à frente, azul onde {nomes['flavio']} fica. O exterior não aparece no mapa."
)
nivel = st.radio("Nível do mapa", ["UF", "Município"], horizontal=True)
if nivel == "UF":
    tabela_mapa = dados.tabela_uf(g.projecao, cenario).filter(pl.col("uf") != dados.UF_EXTERIOR)
    geojson, chave = mapa.malha("uf"), "uf"
else:
    ufs = sorted(set(g.projecao["uf"].drop_nulls()) - {dados.UF_EXTERIOR})
    recorte = st.selectbox(
        "Recorte",
        [None, *ufs],
        format_func=lambda uf: "Brasil inteiro" if uf is None else dados.NOMES_UF[uf],
    )
    tabela_mapa = dados.tabela_municipio(g.projecao, g.municipio_ibge, cenario, recorte)
    geojson, chave = malha_municipio(recorte), "cd_ibge"

st.plotly_chart(
    mapa.figura(tabela_mapa, geojson, chave=chave, rotulo="nome", nomes=nomes),
    width="stretch",
    config={"scrollZoom": False},
)
if nivel == "Município":
    if recorte in (None, "MT"):
        st.caption(
            "Boa Esperança do Norte (MT), instalado em 2025, ainda não tem polígono na malha do "
            "IBGE: o território dele aparece dentro de Sorriso e de Nova Ubiratã. A projeção do "
            "município está na tabela abaixo."
        )
    st.dataframe(
        em_pct(tabela_mapa.drop("uf", "cd_ibge")),
        hide_index=True,
        width="stretch",
        height=360,
        column_config=config_colunas("Município"),
    )

st.subheader("Por UF")
uf = em_pct(dados.tabela_uf(g.projecao, cenario))
st.dataframe(uf.drop("uf"), hide_index=True, width="stretch", column_config=config_colunas("UF"))


st.subheader("Exterior, por cidade")
st.caption("Cada cidade com consulado brasileiro, da maior para a menor em eleitores aptos.")
st.dataframe(
    em_pct(dados.tabela_exterior(g.projecao, cenario)),
    hide_index=True,
    width="stretch",
    height=360,
    column_config=config_colunas("Cidade"),
)

st.subheader("Zonas eleitorais")
st.caption(
    "Da zona mais favorável a Lula à mais favorável a Flávio. A zona é a unidade em que a "
    "transferência de votos é estimada; ela não tem polígono no mapa."
)
uf_zona = st.selectbox(
    "UF das zonas",
    [None, *sorted(set(g.projecao["uf"].drop_nulls()))],
    format_func=lambda uf: "Todas" if uf is None else dados.NOMES_UF[uf],
)
st.dataframe(
    em_pct(dados.tabela_zona(g.projecao, cenario, uf_zona)).drop("uf"),
    hide_index=True,
    width="stretch",
    height=360,
    column_config=config_colunas("Zona"),
)

st.subheader("Para onde foram os votos")
st.caption(
    "A transferência estimada numa eleição de referência: de cada 100 eleitores de uma "
    "categoria no 1º turno (linha), quantos foram para cada categoria no 2º turno (coluna)."
)
anos = sorted(set(g.transferencia_uf["ano"]), reverse=True)
ufs_ref = [dados.UF_BRASIL, *sorted(set(g.transferencia_uf["uf"]) - {dados.UF_BRASIL})]
coluna_ano, coluna_uf = st.columns(2)
ano_ref = coluna_ano.selectbox("Eleição de referência", anos)
uf_ref = coluna_uf.selectbox(
    "Abrangência",
    ufs_ref,
    format_func=lambda uf: (
        "Brasil (média das zonas, ponderada pelos eleitores)"
        if uf == dados.UF_BRASIL
        else dados.NOMES_UF[uf]
    ),
)
if uf_ref == dados.UF_BRASIL:
    st.caption(
        "O Brasil é a média das zonas, ponderada pelos eleitores de cada categoria do "
        "1º turno. As zonas são a base da projeção. Por isso o Brasil não é a média das UFs: "
        "cada UF é uma regressão própria sobre todas as suas seções, e as duas estimativas "
        "podem divergir (em 2022, por exemplo, a fração de eleitores de Lula que foi para "
        "Jair Bolsonaro fica perto de zero em todas as UFs, mas em 1,1% no Brasil)."
    )
else:
    st.caption("A estimativa da UF inteira, numa regressão própria; a projeção usa a de cada zona.")
matriz = dados.matriz_transferencia(g.transferencia_uf, g.lados, ano_ref, uf_ref)
st.dataframe(
    matriz.with_columns(pl.exclude("origem") * 100),
    hide_index=True,
    width="stretch",
    column_config={
        "origem": f"1º turno de {ano_ref} ↓ · 2º turno →",
        **{
            c: st.column_config.NumberColumn(c, format="%.1f%%")
            for c in matriz.columns
            if c != "origem"
        },
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
