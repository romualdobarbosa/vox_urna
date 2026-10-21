import plotly.graph_objects as go
import polars as pl
import streamlit as st

import dados
import mapa
from mapa import COR

st.set_page_config(page_title="Vox Urna: projeção do 2º turno de 2026", layout="wide")

DESTAQUE = "#E8A33D"  # neutro entre os lados: o achado não é de um candidato
REPO = "https://github.com/romualdobarbosa/vox_urna"
LINKEDIN = "https://www.linkedin.com/in/romualdo-barbosa-b43935244/"
ASSINATURA = (
    f"Feito por [Romualdo Barbosa]({LINKEDIN}) · dados oficiais do TSE · "
    f"código aberto no [GitHub]({REPO})"
)


@st.cache_data
def gold(versao: tuple) -> dados.Gold:
    """`versao` só serve de chave: o Streamlit Cloud recarrega o app a cada push sem
    reiniciar o processo, e sem ela o cache devolveria a gold e o `Gold` antigos."""
    return dados.carregar()


def versao_gold() -> tuple:
    """Os campos de `Gold` e o tamanho e a data de cada arquivo da gold."""
    arquivos = tuple(
        (p.name, p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(dados.DIR_GOLD.glob("*.parquet"))
    )
    return tuple(dados.Gold.__dataclass_fields__), arquivos


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


g = gold(versao_gold())
nomes = dados.finalistas(g.lados)
ids_cenario = dados.cenarios(g.projecao)
match = dados.taxa_match_ligacao(g.taxa_match, 2022)
backtest = dados.resumo_backtest(g.backtest, g.backtest_acerto)
outros_2018 = dados.divisao_outros(g.transferencia_uf, 2018)
outros_2022 = dados.divisao_outros(g.transferencia_uf, 2022)


def titulo_cenario(c: str) -> str:
    return dados.CENARIOS[c].titulo if c in dados.CENARIOS else c


def config_colunas(rotulo: str) -> dict:
    """A configuração das colunas das tabelas de UF, município, cidade e zona."""
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
        "rmse_pp": st.column_config.NumberColumn("Ajuste (RMSE, p.p.)", format="%.2f"),
    }


def texto_backtest() -> str:
    """A linha do erro do backtest sob a manchete, com sinal (ADR 0004)."""
    sentido = "a mais" if backtest.erro_pp > 0 else "a menos"
    erradas = ", ".join(backtest.ufs_erradas)
    acerto_ufs = f"{backtest.ufs - len(backtest.ufs_erradas)} das {backtest.ufs} UFs"
    if erradas:
        acerto_ufs += f" (exceção: {erradas})"
    if backtest.acertou_exterior:
        acerto_ufs += " e no exterior"
    pontos = f"{abs(backtest.erro_pp):.1f}".replace(".", ",")
    return (
        f"**Testamos o mesmo método em 2022** (a transferência de 2018 aplicada ao 1º turno "
        f"de 2022): ele deu **{pontos} ponto percentual {sentido} para o lado Lula** do que "
        f"o resultado real ({dados.pct(backtest.projetado)} "
        f"projetado, {dados.pct(backtest.real)} real) e acertou quem venceu em {acerto_ufs}."
    )


def texto_match() -> str:
    return (
        f"Taxa de match: {dados.pct(match.get('municipio_zona', 0.0))} dos eleitores de 2026 "
        "foram ligados à mesma zona e município de 2022."
    )


def rodape() -> None:
    st.divider()
    st.caption(f"{texto_match()} Detalhes na aba Método.")
    st.caption(ASSINATURA)


def estilo_matriz(matriz: pl.DataFrame, lados: dict[str, str]):
    """A matriz de transferência como mapa de calor: a coluna de cada lado na cor dele e as
    demais em cinza, com a intensidade dada pela fração."""
    rgb = {
        lados["lula"]: (200, 16, 46),
        lados["flavio"]: (31, 78, 156),
    }

    def celula(valor: float, coluna: str) -> str:
        r, g_, b = rgb.get(coluna, (128, 128, 128))
        alfa = valor / 100 * (1.0 if coluna in rgb else 0.6)
        texto = "color: white; " if alfa > 0.55 else ""
        return f"{texto}background-color: rgba({r}, {g_}, {b}, {alfa:.2f})"

    tabela = matriz.with_columns(pl.exclude("origem") * 100).to_pandas()
    estilo = tabela.style.format(
        lambda v: f"{v:.1f}%".replace(".", ","), subset=tabela.columns[1:]
    ).hide(axis="index")
    for coluna in tabela.columns[1:]:
        estilo = estilo.map(lambda v, c=coluna: celula(v, c), subset=[coluna])
    # A linha de outros é o achado que separa os cenários.
    destaque = tabela.index[tabela["origem"] == dados.CATEGORIAS["outros"]]
    return estilo.set_properties(subset=(destaque, tabela.columns), **{"font-weight": "bold"})


def mostrar_matriz(ano: int, uf: str) -> None:
    matriz = dados.matriz_transferencia(g.transferencia_uf, g.lados, ano, uf)
    st.dataframe(
        estilo_matriz(matriz, dados.finalistas(g.lados, ano)),
        hide_index=True,
        width="stretch",
        column_config={"origem": f"1º turno de {ano} ↓ · 2º turno →"},
    )


st.warning(dados.AVISO, icon="⚠️")
st.title("Projeção do 2º turno de 2026")
st.markdown(ASSINATURA)

resultado, aba_dados, metodo, engenharia = st.tabs(
    ["Resultado", "Dados", "Método", "Como foi feito"]
)

# ---------------------------------------------------------------- Resultado
with resultado:
    st.markdown(
        "A gente pegou os votos do 1º turno de 2026, seção por seção, e fez uma pergunta: "
        "**se cada grupo de eleitores fizer no 2º turno o que grupos parecidos fizeram em "
        "2022, como fica o resultado?** Num segundo cenário, quem votou em outros "
        "candidatos se comporta como em 2018. Ninguém foi entrevistado: são só as contas, "
        "feitas com os dados oficiais das urnas, que qualquer pessoa pode baixar no TSE."
    )

    # Manchete: a faixa entre os cenários, nunca um número único.
    faixa = dados.faixa_brasil(g.projecao)
    colunas = st.columns(2)
    for coluna, lado in zip(colunas, dados.LADOS, strict=True):
        baixo, alto = faixa[lado]
        coluna.markdown(
            f"<div style='border-left: 6px solid {COR[lado]}; padding-left: 12px; "
            "margin-bottom: 12px'>"
            f"<div style='font-size: 1.1rem'>{nomes[lado]}</div>"
            f"<div style='font-size: 2.4rem; font-weight: 700; color: {COR[lado]}'>"
            f"{dados.pct(baixo)} a {dados.pct(alto)}</div>"
            f"<div style='opacity: 0.7'>dos votos válidos, conforme o cenário</div></div>",
            unsafe_allow_html=True,
        )
    st.write("")
    st.markdown(texto_backtest())
    st.caption(texto_match())

    st.divider()
    _, meio, _ = st.columns([1, 2, 1])
    cenario = meio.radio(
        "Cenário", ids_cenario, format_func=titulo_cenario, horizontal=True, key="cenario"
    )
    if cenario in dados.CENARIOS:
        meio.caption(dados.CENARIOS[cenario].suposicoes)

    brasil = g.projecao.filter((pl.col("nivel") == "brasil") & (pl.col("cenario") == cenario)).row(
        0, named=True
    )
    brancos_nulos, abstencao = dados.brancos_nulos_abstencao(brasil)
    colunas = st.columns(4)
    for coluna, lado in zip(colunas[:2], dados.LADOS, strict=True):
        coluna.metric(
            nomes[lado],
            dados.pct(brasil[f"pct_{lado}_validos"]),
            help=f"{dados.inteiro(brasil[lado])} votos projetados",
        )
    colunas[2].metric(
        "Brancos e nulos",
        dados.pct(brancos_nulos),
        help=f"{dados.inteiro(brasil['branco'] + brasil['nulo'])} votos projetados, em % de "
        "quem comparece",
    )
    colunas[3].metric(
        "Abstenção",
        dados.pct(abstencao),
        help=f"{dados.inteiro(brasil['abstencao'])} eleitores, em % dos aptos",
    )

    st.subheader("Mapa")
    st.caption(
        f"Diferença em pontos percentuais dos votos válidos: vermelho onde {nomes['lula']} "
        f"fica à frente, azul onde {nomes['flavio']} fica. O exterior não aparece no mapa "
        "(está na aba Dados)."
    )
    coluna_nivel, coluna_recorte = st.columns(2)
    nivel = coluna_nivel.radio("Nível do mapa", ["Município", "UF"], horizontal=True)
    if nivel == "UF":
        tabela_mapa = dados.tabela_uf(g.projecao, cenario).filter(pl.col("uf") != dados.UF_EXTERIOR)
        geojson, chave = mapa.malha("uf"), "uf"
        recorte = None
    else:
        ufs = sorted(set(g.projecao["uf"].drop_nulls()) - {dados.UF_EXTERIOR})
        recorte = coluna_recorte.selectbox(
            "Recorte",
            [None, *ufs],
            format_func=lambda uf: "Brasil inteiro" if uf is None else dados.NOMES_UF[uf],
        )
        tabela_mapa = dados.tabela_municipio(g.projecao, g.municipio_ibge, cenario, recorte)
        geojson, chave = malha_municipio(recorte), "cd_ibge"

    st.plotly_chart(
        mapa.figura(tabela_mapa, geojson, chave=chave, rotulo="nome", nomes=nomes),
        width="stretch",
        config={"scrollZoom": False, "displayModeBar": False},
    )
    if nivel == "Município" and recorte in (None, "MT"):
        st.caption(
            "Boa Esperança do Norte (MT), instalado em 2025, ainda não tem polígono na malha do "
            "IBGE: o território dele aparece dentro de Sorriso e de Nova Ubiratã. A projeção do "
            "município está na aba Dados."
        )

    st.subheader("Para onde foram os votos")
    st.markdown(
        f"<div style='border-left: 6px solid {DESTAQUE}; background: rgba(232, 163, 61, 0.12);"
        " padding: 12px 16px; border-radius: 4px; margin-bottom: 12px'>"
        "<b>O achado que separa os dois cenários:</b> dos eleitores de outros candidatos que "
        f"escolheram um lado no 2º turno, <b>{dados.pct(outros_2022, 0)} foram para Lula em "
        f"2022</b>, mas <b>{dados.pct(outros_2018, 0)} foram para Haddad em 2018</b>. Quem "
        "votou num dos lados no 1º turno quase sempre "
        "fica com ele; quem votou em outros candidatos muda muito de uma eleição para a "
        "outra.</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Brasil, 2022: de cada 100 eleitores de uma categoria no 1º turno (linha), quantos "
        "foram para cada categoria no 2º turno (coluna). É a média das zonas, ponderada pelos "
        "eleitores. Outras UFs e 2018 estão na aba Dados."
    )
    mostrar_matriz(2022, dados.UF_BRASIL)
    rodape()

# ---------------------------------------------------------------- Dados
with aba_dados:
    st.markdown(
        "Todas as tabelas da projeção, para quem quer ir além do mapa. Os números mudam com "
        "o cenário escolhido aqui."
    )
    cenario_dados = st.radio(
        "Cenário das tabelas",
        ids_cenario,
        format_func=titulo_cenario,
        horizontal=True,
        key="cenario_dados",
    )

    st.subheader("Por UF")
    uf = em_pct(dados.tabela_uf(g.projecao, cenario_dados))
    st.dataframe(
        uf.drop("uf"), hide_index=True, width="stretch", column_config=config_colunas("UF")
    )

    st.subheader("Por município")
    ufs = sorted(set(g.projecao["uf"].drop_nulls()) - {dados.UF_EXTERIOR})
    uf_municipio = st.selectbox(
        "UF dos municípios",
        [None, *ufs],
        format_func=lambda uf: "Todas" if uf is None else dados.NOMES_UF[uf],
    )
    st.dataframe(
        em_pct(
            dados.tabela_municipio(g.projecao, g.municipio_ibge, cenario_dados, uf_municipio)
        ).drop("uf", "cd_ibge"),
        hide_index=True,
        width="stretch",
        height=360,
        column_config=config_colunas("Município"),
    )

    st.subheader("Exterior, por cidade")
    st.caption("Cada cidade com consulado brasileiro, da maior para a menor em eleitores aptos.")
    st.dataframe(
        em_pct(dados.tabela_exterior(g.projecao, cenario_dados)),
        hide_index=True,
        width="stretch",
        height=360,
        column_config=config_colunas("Cidade"),
    )

    st.subheader("Zonas eleitorais")
    st.caption(
        "Da zona mais favorável a Lula à mais favorável a Flávio. A zona é a unidade em que a "
        "transferência de votos é estimada; ela não tem polígono no mapa. O ajuste (RMSE) diz "
        "quanto a regressão de 2022 erra, em média, nas seções da zona: quanto menor, melhor."
    )
    uf_zona = st.selectbox(
        "UF das zonas",
        [None, *sorted(set(g.projecao["uf"].drop_nulls()))],
        format_func=lambda uf: "Todas" if uf is None else dados.NOMES_UF[uf],
    )
    st.dataframe(
        em_pct(dados.tabela_zona(g.projecao, cenario_dados, uf_zona, g.rmse_zona)).drop("uf"),
        hide_index=True,
        width="stretch",
        height=360,
        column_config=config_colunas("Zona"),
    )

    st.subheader("Transferência de votos por eleição e UF")
    st.caption(
        "De cada 100 eleitores de uma categoria no 1º turno (linha), quantos foram para cada "
        "categoria no 2º turno (coluna), na eleição de referência."
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
            "1º turno. As zonas são a base da projeção. Por isso o Brasil não é a média das "
            "UFs: cada UF é uma regressão própria sobre todas as suas seções, e as duas "
            "estimativas podem divergir (em 2022, por exemplo, a fração de eleitores de Lula "
            "que foi para Jair Bolsonaro fica perto de zero em todas as UFs, mas em 1,1% no "
            "Brasil)."
        )
    else:
        st.caption(
            "A estimativa da UF inteira, numa regressão própria; a projeção usa a de cada zona."
        )
    mostrar_matriz(ano_ref, uf_ref)
    rodape()

# ---------------------------------------------------------------- Método
with metodo:
    st.subheader("Como a projeção é feita")
    st.markdown(
        f"""
O voto de cada pessoa é secreto: ninguém sabe se quem votou em Ciro em 2022 foi de Lula ou
de Bolsonaro no 2º turno. Mas cada **seção** publica o seu boletim de urna, e o saldo de
centenas de seções de uma mesma zona deixa recuperar, por regressão, a **transferência de
votos**: que fração de cada grupo do 1º turno foi para cada opção do 2º turno.

1. **Estimação.** Em cada zona eleitoral, uma regressão de mínimos quadrados restrita
   (o modelo de Goodman, com cada fração entre 0 e 1 e cada linha somando 1) usa as seções
   como observações, tudo em fração dos eleitores aptos e ponderado pelos aptos de cada
   seção. As categorias do 1º turno são os dois lados, outros candidatos, branco, nulo e
   abstenção; as do 2º turno são os dois lados, branco, nulo e abstenção. Uma zona com menos
   de 10 seções usa a estimativa da UF.
2. **Ligação a 2026.** Cada seção de 2026 recebe a transferência da zona de 2022 com o mesmo
   município e número de zona ({dados.pct(match.get("municipio_zona", 0.0), 2)} do
   eleitorado). Se o par não existia, recebe a média das zonas que continham o município
   em 2022 ({dados.pct(match.get("municipio", 0.0), 2)}), e em último caso a da UF
   ({dados.pct(match.get("uf", 0.0), 2)}). Essas frações são a **taxa de match**.
3. **Projeção.** As frações são aplicadas aos votos reais do 1º turno de 2026, seção por
   seção, e somadas por zona, município, UF e Brasil.

**Os lados.** Cada lado leva o nome do finalista de 2026 e liga os finalistas equivalentes
de cada eleição: o lado Lula é Haddad (PT) em 2018 e Lula (PT) em 2022 e 2026; o lado Flávio
é Jair Bolsonaro em 2018 e 2022 e Flávio Bolsonaro (PL) em 2026.
"""
    )
    st.subheader("Cenários")
    for c in ids_cenario:
        if c in dados.CENARIOS:
            st.markdown(f"**{dados.CENARIOS[c].titulo}.** {dados.CENARIOS[c].suposicoes}")

    st.subheader("Incerteza")
    st.markdown(
        "A faixa entre os cenários mostra quanto a projeção depende das suposições. O erro do "
        "backtest é a única medida empírica do erro do método: vem de um único teste "
        "histórico e não é margem de erro. A projeção de 2026 não é corrigida por ele, porque "
        "isso seria apostar que o viés de uma única eleição se repete."
    )

    st.subheader("Backtest: o método aplicado a 2022")
    st.markdown(
        "A transferência de 2018 aplicada ao 1º turno de 2022, pela mesma ligação em 3 "
        "passos, e comparada com o 2º turno real de 2022. É um caso parecido com o de 2026: "
        "um dos lados trocou de candidato (Haddad → Lula), como agora (Jair → Flávio). Só o "
        "cenário base é replicado."
    )
    acerto = {r["nivel"]: r for r in g.backtest_acerto.iter_rows(named=True)}
    colunas = st.columns(3)
    colunas[0].metric(
        "Erro no Brasil (lado Lula)",
        f"{backtest.erro_pp:+.1f}".replace(".", ",") + " p.p.",
        help="Projetado menos real, em pontos percentuais dos votos válidos.",
    )
    colunas[1].metric(
        "Vencedor certo nas UFs",
        f"{acerto['uf']['acertos']} de {acerto['uf']['unidades']}",
        help=f"{dados.pct(acerto['uf']['pct_acerto_aptos'])} dos eleitores aptos. O exterior "
        "conta como uma UF.",
    )
    colunas[2].metric(
        "Vencedor certo nos municípios",
        dados.pct(acerto["municipio"]["pct_acerto"]),
        help=f"{dados.pct(acerto['municipio']['pct_acerto_aptos'])} dos eleitores aptos.",
    )
    tabela_bt = dados.tabela_backtest_uf(g.backtest).with_columns(pl.col("projetado", "real") * 100)
    st.dataframe(
        tabela_bt,
        hide_index=True,
        width="stretch",
        column_config={
            "nome": "UF",
            "projetado": st.column_config.NumberColumn(
                "Lado Lula projetado (% válidos)", format="%.1f%%"
            ),
            "real": st.column_config.NumberColumn("Lado Lula real (% válidos)", format="%.1f%%"),
            "erro_pp": st.column_config.NumberColumn("Erro (p.p.)", format="%+.1f"),
            "acertou": st.column_config.CheckboxColumn("Acertou o vencedor"),
        },
    )

    st.subheader("Estabilidade da transferência: 2018 × 2022")
    st.markdown(
        "Se a transferência de cada grupo se repete de uma eleição para a outra, faz sentido "
        "usar a de 2022 em 2026. Os eleitores dos dois lados, de nulo e a abstenção quase não "
        f"mudam. Os de outros candidatos mudam muito: dos que escolheram um lado, "
        f"**{dados.pct(outros_2018, 0)} foram para o lado Lula em 2018 e "
        f"{dados.pct(outros_2022, 0)} em 2022**. Por isso existe o cenário de sensibilidade. "
        "A medida abaixo é a distância de variação total (TVD) entre as duas linhas: 0 é igual, "
        "1 é totalmente diferente."
    )
    st.dataframe(
        dados.tabela_estabilidade(g.estabilidade),
        hide_index=True,
        column_config={
            "categoria": "Categoria do 1º turno",
            "tvd": st.column_config.NumberColumn("TVD 2018 × 2022", format="%.3f"),
        },
    )
    st.caption(
        "A composição de outros mudou entre os anos (Ciro, Alckmin e outros em 2018; Ciro, "
        "Tebet e outros em 2022), então parte da diferença vem de serem eleitores diferentes. "
        "Branco também muda bastante, mas é pouca gente."
    )

    st.subheader("Referência externa: as pesquisas de 2022")
    st.markdown(
        "Duas pesquisas registradas no TSE perguntaram, logo depois do 1º turno de 2022, em quem "
        "os eleitores de Ciro e de Tebet votariam no 2º turno. A comparação usa a mesma "
        "medida: dos eleitores de outros candidatos que escolheram um lado, a fração que foi "
        "para Lula. Elas aparecem lado a lado, sem média: as duas divergem muito entre si, e "
        "nenhuma é o gabarito."
    )
    ref = dados.tabela_referencia(g.referencia_externa)
    cores = ["#9AA0A6" if f == "pesquisa" else DESTAQUE for f in ref["fonte"]]
    fig = go.Figure(
        go.Bar(
            x=(ref["divisao_lula"] * 100).to_list(),
            y=ref["rotulo"].to_list(),
            orientation="h",
            marker_color=cores,
            text=[dados.pct(v) for v in ref["divisao_lula"]],
            textposition="outside",
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        height=220,
        margin={"l": 0, "r": 40, "t": 10, "b": 10},
        xaxis={"range": [0, 100], "ticksuffix": "%", "title": None},
        yaxis={"autorange": "reversed"},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    for r in ref.filter(pl.col("fonte") == "pesquisa").iter_rows(named=True):
        st.caption(
            f"**{r['instituto']}** · registro {r['registro_tse']} · campo de "
            f"{r['campo_inicio']:%d/%m} a {r['campo_fim']:%d/%m/%Y} · {r['modo']} · "
            f"{dados.inteiro(r['n_total'])} entrevistas · [relatório]({r['url']})"
        )
    st.caption(
        "A fração de cada pesquisa junta Ciro e Tebet ponderados pelos votos de cada um no "
        "1º turno de 2022, contados nos boletins de urna. A deste método é a transferência "
        "Brasil de outros em 2022."
    )

    st.subheader("Limitações")
    st.markdown(
        """
- **Inferência ecológica.** O voto individual não é observável, só o saldo de cada seção.
  As frações descrevem zonas, não pessoas.
- **Suposição central.** Os eleitores de cada lado, de outros candidatos, brancos, nulos e
  abstenção se comportam como na eleição de referência. Em 2026 o lado Flávio troca de
  candidato, e isso pode mudar o comportamento.
- **Taxa de match.** Quase todo o eleitorado de 2026 é ligado à mesma zona de 2022, mas o
  rezoneamento deixa uma parte pequena com uma transferência aproximada.
- **Não é pesquisa.** Ninguém foi entrevistado, e não há intervalo estatístico.
"""
    )
    rodape()

# ---------------------------------------------------------------- Como foi feito
with engenharia:
    st.markdown(
        f"""
Este dashboard é a ponta de um projeto de **engenharia de dados**: um pipeline medallion
(bronze → silver → gold) sobre os boletins de urna do TSE para Presidente em 2018, 2022 e
2026. O código está no [GitHub]({REPO}).

**Bronze: ingestão.**
- As URLs vêm da API CKAN do Portal de Dados Abertos do TSE, porque o nome do arquivo muda
  a cada republicação.
- Cada zip (um por UF e turno) é conferido contra o `.sha512` oficial.
- Um manifest registra a URL, o hash, o tamanho e o status de cada arquivo (`success`,
  `corrupted`, `failed` ou `superseded`).
- O re-run pula o que já está íntegro, sem consultar o servidor, e tem retry com backoff.

**Silver: normalização.**
- Lê cada zip em streaming (cerca de 32 GB descomprimidos por 1º turno, com todos os
  cargos), fica só com Presidente e normaliza as diferenças de cada ano: nomes de coluna,
  encoding, aspas e a forma do exterior.
- O resultado vira Parquet tipado, montado pelo DuckDB, em cerca de 2 minutos.
- Invariantes por seção: votos = comparecimento, aptos = comparecimento + abstenção, sem
  duplicatas. A seção que viola uma delas vai para quarentena com o motivo, e o build falha
  se a quarentena passar de 0,1%.
- Os totais nacionais batem voto a voto com o resultado oficial (2022, 2º turno: Lula
  60.345.999 × Jair Bolsonaro 58.206.354). Só caíram em quarentena urnas anuladas pelo TSE.

**Gold: o modelo.**
- A regressão restrita por zona (SLSQP, cerca de 10 ms por zona), a ligação em 3 passos, os
  cenários, o backtest, a estabilidade e a referência externa.
- O build confere os lados contra o nome e o partido do boletim e falha se um 2º turno
  tiver voto fora deles.
- As tabelas pequenas ficam versionadas no repositório em Parquet. Este dashboard lê só
  essas tabelas.

**Qualidade.**
- Testes com recortes reais pequenos de cada ano para o parsing.
- Testes das invariantes.
- Um teste de recuperação do modelo: seções sintéticas geradas de uma transferência
  conhecida, que o estimador precisa recuperar.
- As decisões de arquitetura estão registradas em ADRs no repositório.

**Stack.** Python 3.12 (uv), DuckDB, Polars, Parquet, SciPy, Streamlit, Plotly, ruff e
pre-commit. Uma CLI com um comando por camada, encadeados por `make all`.
"""
    )
    rodape()
