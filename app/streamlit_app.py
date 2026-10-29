import importlib
from pathlib import Path
from types import ModuleType

import plotly.graph_objects as go
import polars as pl
import streamlit as st

import dados
import mapa


def recarregar(modulo: ModuleType) -> None:
    """Recarrega o módulo se o arquivo dele mudou desde que foi carregado.

    O Streamlit Cloud reexecuta este script a cada push, mas mantém os módulos importados
    na versão antiga. Recarregar sempre zeraria o cache da malha em `mapa`, então só
    recarrega quando o arquivo muda. Cada módulo guarda `VERSAO_ARQUIVO` ao ser
    carregado; um módulo sem ela é de antes desta regra e também é recarregado.
    """
    if getattr(modulo, "VERSAO_ARQUIVO", None) != Path(modulo.__file__).stat().st_mtime_ns:
        importlib.reload(modulo)


recarregar(dados)
recarregar(mapa)
from mapa import COR  # noqa: E402  (depois da recarga, para pegar a versão nova)

st.set_page_config(page_title="Vox Urna: a virada do 2º turno de 2026", layout="wide")

POR_EXTENSO = {2: "dois", 3: "três"}
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
    """A versão de `dados.py`, os campos de `Gold` e o tamanho e a data de cada arquivo da
    gold."""
    arquivos = tuple(
        (p.name, p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(dados.DIR_GOLD.glob("*.parquet"))
    )
    return dados.VERSAO_ARQUIVO, tuple(dados.Gold.__dataclass_fields__), arquivos


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


def pontos(valor: float) -> str:
    """Uma diferença em pontos percentuais, com sinal: -7.25 → "-7,3 p.p."."""
    return f"{valor:+.1f}".replace(".", ",") + " p.p."


def decimal(valor: float) -> str:
    """Um número com uma casa e vírgula: 6.89 → "6,9"."""
    return f"{valor:.1f}".replace(".", ",")


def pct_pequeno(fracao: float) -> str:
    """Como `dados.pct`, com duas casas abaixo de 0,1% para um caso só não virar "0,0%"."""
    return dados.pct(fracao, 2 if 0 < fracao < 0.001 else 1)


def texto_backtest() -> str:
    """O aviso de incerteza sob a manchete, com o erro do backtest e o sinal dele (ADR 0004)."""
    sentido = "a mais" if backtest.erro_pp > 0 else "a menos"
    erradas = ", ".join(backtest.ufs_erradas)
    acerto_ufs = f"{backtest.ufs - len(backtest.ufs_erradas)} das {backtest.ufs} UFs"
    if erradas:
        acerto_ufs += f" (o erro foi no {erradas})"
    if backtest.acertou_exterior:
        acerto_ufs += " e no exterior"
    vencedor = "Lula" if backtest.projetado > 0.5 else "Jair Bolsonaro"
    if (backtest.projetado > 0.5) == (backtest.real > 0.5):
        vencedor += " na frente, como aconteceu,"
    else:
        vencedor += " na frente, o que não aconteceu,"
    pontos = f"{abs(backtest.erro_pp):.1f}".replace(".", ",")
    return (
        "Isso é uma projeção, não uma certeza. O modelo supõe que cada grupo de eleitores "
        "repete o que fez nas outras eleições, e um fato novo na campanha ou alguma coisa que "
        "os boletins não mostram pode mudar o resultado na urna. Testei o mesmo método em 2022, "
        f"usando 2018 pra prever o 2º turno, e ele deu {vencedor} "
        f"acertando o vencedor em {acerto_ufs}. Mas ele também deu **{pontos} ponto "
        f"{sentido} pro lado Lula** do que o resultado real ({dados.pct(backtest.projetado)} "
        f"projetado, {dados.pct(backtest.real)} real). Deixo esse erro aqui, do lado do "
        "resultado, porque acho mais honesto mostrar do que esconder."
    )


def texto_match() -> str:
    return (
        f"{dados.pct(match.get('municipio_zona', 0.0))} dos eleitores de 2026 foram ligados à "
        "mesma zona e município de 2022 (a taxa de match)."
    )


def cartoes_viradas(resumo: dict[str, dict], unidade: str, detalhes: bool = True) -> None:
    """Um cartão por sentido da virada, na cor do lado que passou a ficar na frente, com a
    maior, a menor e a média das variações embaixo."""
    cores = {"lula_para_flavio": COR["flavio"], "flavio_para_lula": COR["lula"]}
    for coluna, (sentido, rotulo) in zip(st.columns(2), dados.SENTIDOS.items(), strict=True):
        r = resumo[sentido]
        coluna.markdown(
            f"<div style='border-left: 6px solid {cores[sentido]}; padding-left: 12px; "
            "margin-bottom: 12px'>"
            f"<div style='font-size: 1.1rem'>{rotulo}</div>"
            f"<div style='font-size: 2.4rem; font-weight: 700; color: {cores[sentido]}'>"
            f"{dados.inteiro(r['viradas'])} {unidade}{'s' if r['viradas'] != 1 else ''}</div>"
            f"<div style='opacity: 0.7'>{pct_pequeno(r['pct_municipios'])} de "
            f"{dados.inteiro(r['municipios_pareados'])} {unidade}s comparáveis · "
            f"{pct_pequeno(r['pct_aptos_2026'])} dos eleitores aptos de 2026</div></div>",
            unsafe_allow_html=True,
        )
        if not detalhes or not r["viradas"]:
            continue
        maior, menor, media = coluna.columns(3)
        for metrica, rotulo_metrica, chave in (
            (maior, "Maior variação", "maior"),
            (menor, "Menor variação", "menor"),
        ):
            metrica.metric(rotulo_metrica, pontos(r[f"{chave}_variacao_pp"]))
            metrica.caption(local(r, chave))
        media.metric(
            "Média",
            pontos(r["media_variacao_pp"]),
            help=f"Média simples entre os {unidade}s que viraram.",
        )


def caminho_contrario(r: dict) -> str:
    """O trecho da capa sobre os municípios que viraram de Flávio para Lula."""
    if not r["viradas"]:
        return "e nenhum fez o caminho contrário"
    if r["viradas"] == 1:
        return f"e só 1 fez o caminho contrário, {local(r, 'maior')}"
    return f"e só {dados.inteiro(r['viradas'])} fizeram o caminho contrário"


def local(r: dict, chave: str) -> str:
    """O município da maior ou da menor variação, com o nome da UF; no exterior, só a cidade."""
    nome = dados.nome_municipio(r[f"{chave}_nm_municipio"])
    if r[f"{chave}_uf"] != dados.UF_EXTERIOR:
        nome += f" ({dados.NOMES_UF[r[f'{chave}_uf']]})"
    return nome


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


# Tudo desta aba é do território brasileiro, sem o exterior.
br = dados.linha_resumo(g.resumo, "brasil", "brasil")
validos_2t = br["lula_2t"] + br["flavio_2t"]
st.title("Lula virou a eleição, e isso nunca tinha acontecido")
st.markdown(
    "#### Desde a redemocratização, quem terminou o 1º turno na frente sempre ganhou o 2º "
    "turno pra presidente. Em 2026, não."
)
st.markdown(ASSINATURA)

virada_aba, aba_dados, metodo, engenharia = st.tabs(
    ["A virada", "Dados", "Método", "Como foi feito"]
)


def milhoes(votos: float) -> str:
    """Votos em milhões com uma casa: 2238452 → "2,2 milhões", 1843449 → "1,8 milhão"."""
    valor = abs(votos) / 1e6
    return f"{decimal(valor)} {'milhão' if round(valor, 1) < 2 else 'milhões'}"


def cartao(cor: str, titulo: str, valor: str, detalhe: str, coluna=st) -> None:
    coluna.markdown(
        f"<div style='border-left: 6px solid {cor}; padding-left: 12px; margin-bottom: 12px'>"
        f"<div style='font-size: 1.1rem'>{titulo}</div>"
        f"<div style='font-size: 2.4rem; font-weight: 700; color: {cor}'>{valor}</div>"
        f"<div style='opacity: 0.7'>{detalhe}</div></div>",
        unsafe_allow_html=True,
    )


def escolher_uf(chave: str):
    ufs = sorted(set(g.votos_municipio["uf"]))
    return st.selectbox(
        "Recorte",
        [None, *ufs],
        format_func=lambda uf: "Brasil inteiro" if uf is None else dados.NOMES_UF[uf],
        key=chave,
    )


def mostrar_mapa(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", config={"scrollZoom": False, "displayModeBar": False})


# ---------------------------------------------------------------- A virada
with virada_aba:
    st.markdown(
        f"O Flávio Bolsonaro saiu do 1º turno com {milhoes(br['margem_1t'])} de votos a mais "
        f"que o Lula. No 2º turno, o Lula venceu por {milhoes(br['margem_real'])}. Baixei do "
        "TSE o boletim de todas as urnas do Brasil, que ficam abertos pra qualquer pessoa, "
        "pra entender de onde vieram esses votos."
    )
    for coluna, lado in zip(st.columns(2), dados.LADOS, strict=True):
        cartao(
            COR[lado],
            nomes[lado],
            dados.pct(br[f"{lado}_2t"] / validos_2t),
            f"{dados.inteiro(br[f'{lado}_2t'])} votos no 2º turno · "
            f"{dados.inteiro(br[f'{lado}_1t'])} no 1º",
            coluna,
        )
    st.caption("Votos válidos no território brasileiro. O exterior fica de fora de toda esta aba.")

    # ------------------------------------------------ O contexto
    st.subheader("Quase ninguém viu isso chegar")
    pesquisas = ", ".join(
        f"[{p.instituto}]({p.fonte}) {dados.pct(p.flavio)} a {dados.pct(p.lula)}"
        for p in dados.PESQUISAS_2T
    )
    st.markdown(
        "Na véspera do 2º turno, as principais pesquisas davam o Flávio na frente, dentro da "
        f"margem de erro ({pesquisas}). Das pesquisas do 2º turno, "
        f"[só duas]({dados.FONTE_PESQUISAS_LULA_NA_FRENTE}) mostraram o Lula na frente."
    )

    st.markdown(
        "A minha projeção também não viu. Depois do 1º turno, comparei cada município com o "
        "2º turno de 2022, só com os votos dos dois lados."
    )
    viradas_br = dados.resumo_viradas(g.viradas_resumo)
    para_flavio, para_lula = viradas_br["lula_para_flavio"], viradas_br["flavio_para_lula"]
    cartoes_viradas(viradas_br, "município", detalhes=False)
    faixa = dados.faixa_brasil(g.projecao)
    base = g.confronto.filter((pl.col("nivel") == "brasil") & (pl.col("cenario") == "base"))
    acerto = {
        r["nivel"]: r
        for r in g.confronto_acerto.filter(pl.col("cenario") == "base").iter_rows(named=True)
    }
    situacao = {
        r["grupo"]: r
        for r in g.resumo.filter(pl.col("dimensao") == "situacao").iter_rows(named=True)
    }
    erro_sem_virar = sum(
        situacao[s]["pct_erro_margem_base"] for s in ("lula_manteve", "flavio_manteve")
    )
    st.markdown(
        f"**{dados.inteiro(para_flavio['viradas'])} municípios** que deram vitória ao Lula em "
        f"2022 passaram a dar a frente ao Flávio, {caminho_contrario(para_lula)}. Daí projetei "
        "o 2º turno supondo que cada grupo de eleitores faria o que fez em 2018 e 2022 (o "
        "método está na aba Método). Deu Flávio na frente nos dois cenários, com o Lula entre "
        f"{dados.pct(faixa['lula'][0])} e {dados.pct(faixa['lula'][1])}."
    )
    st.markdown(
        f"A projeção acertou o vencedor em {acerto['uf']['acertos']} das "
        f"{acerto['uf']['unidades']} UFs e em {dados.pct(acerto['municipio']['pct_acerto'])} "
        f"dos municípios, mas errou a diferença nacional em "
        f"{milhoes(br['erro_margem_base'])} de votos. Quase todo esse erro "
        f"({dados.pct(erro_sem_virar)}) está em cidades onde o vencedor não mudou. O Lula "
        "cresceu um pouco mais do que o previsto em quase todo lugar, e somado isso virou "
        "a eleição."
    )
    st.caption(
        f"{dados.AVISO} Erro da projeção no cenário base: "
        f"{pontos(base['erro_pp'][0])} no Lula, em votos válidos."
    )
    with st.expander("Ver a projeção feita antes do 2º turno"):
        cenario = st.radio(
            "Cenário", ids_cenario, format_func=titulo_cenario, horizontal=True, key="cenario"
        )
        if cenario in dados.CENARIOS:
            st.caption(dados.CENARIOS[cenario].suposicoes)
        recorte = escolher_uf("uf_projecao")
        mostrar_mapa(
            mapa.figura(
                dados.tabela_municipio(g.projecao, g.municipio_ibge, cenario, recorte),
                malha_municipio(recorte),
                chave="cd_ibge",
                rotulo="nome",
                nomes=nomes,
            )
        )
        st.markdown(texto_backtest())

    # ------------------------------------------------ A virada
    st.subheader("De onde veio a virada")
    st.markdown(
        f"Do 1º pro 2º turno, o Lula ganhou {milhoes(br['delta_lula'])} de votos e o Flávio, "
        f"{milhoes(br['delta_flavio'])}. Os dois cresceram, porque mais de "
        f"{decimal(br['outros_1t'] / 1e6)} milhões de pessoas tinham votado em outros "
        "candidatos e precisaram escolher, e porque muita gente que tinha votado em branco, "
        "nulo ou nem foi votar apareceu no 2º turno. Mas o Lula levou bem mais desses votos, "
        f"e a diferença entre os dois andou {milhoes(br['virada'])} de votos pro lado dele."
    )
    transf = dados.transferencia_comparada(g.comparacao_transferencia)
    abst = transf.filter(pl.col("origem") == "Abstenção").row(0, named=True)
    st.markdown(
        "Refiz pra 2026 a mesma conta de transferência que fiz com 2022, agora com as urnas "
        "do 2º turno. Quem votou no Lula ou no Flávio no 1º turno quase sempre repetiu o "
        "voto, como em 2022. A mudança veio de quem não tinha escolhido ninguém. De cada 100 "
        f"pessoas que não foram votar no 1º turno, {decimal(abst['lula_2026'] * 100)} votaram "
        f"no Lula no 2º. Em 2022 tinham sido {decimal(abst['lula_2022'] * 100)}."
    )
    st.dataframe(
        transf.with_columns(pl.exclude("origem") * 100),
        hide_index=True,
        width="stretch",
        column_config={
            "origem": "No 1º turno votou em",
            **{
                f"{lado}_{ano}": st.column_config.ProgressColumn(
                    f"{nomes[lado].split(' (')[0]}, {ano}",
                    format="%.1f%%",
                    min_value=0,
                    max_value=100,
                    color=COR[lado],
                )
                for lado in dados.LADOS
                for ano in (2022, 2026)
            },
        },
    )
    st.caption(
        "De cada 100 eleitores do grupo no 1º turno, quantos votaram em cada lado no 2º. É "
        "uma estimativa a partir do saldo das seções de cada zona, não o voto de cada pessoa. "
        "O resto de cada linha foi pra branco, nulo ou abstenção. Em 2022 os lados são Lula "
        "e Jair Bolsonaro."
    )

    # ------------------------------------------------ O Brasil todo
    st.subheader("Não foi só o Nordeste")
    vm = g.votos_municipio
    cresceu = vm.filter(pl.col("delta_lula") > 0).height
    cresceu_mais = vm.filter(pl.col("virada") > 0).height
    flavio_venceu = dados.linha_resumo(g.resumo, "situacao", "flavio_manteve")
    st.markdown(
        "No mapa por estado, o Nordeste fica vermelho e quase todo o resto azul, e parece que "
        "o Nordeste elegeu o Lula sozinho. Os boletins contam outra história. O Lula ganhou "
        f"votos em {dados.inteiro(cresceu)} dos {dados.inteiro(vm.height)} municípios e "
        f"cresceu mais que o Flávio em {dados.inteiro(cresceu_mais)}."
    )
    st.caption(
        "Quanto o Lula tirou da diferença entre os dois do 1º pro 2º turno, a cada 100 "
        "eleitores do município. Vermelho onde o Lula cresceu mais que o Flávio, azul onde "
        "foi o contrário."
    )
    coluna_uf, coluna_filtro = st.columns(2)
    with coluna_uf:
        recorte = escolher_uf("uf_virada")
    so_flavio = coluna_filtro.toggle(
        "Só os municípios que o Flávio venceu no 2º turno", key="so_flavio"
    )
    mostrar_mapa(
        mapa.figura_virada(
            dados.mapa_virada(vm, recorte), malha_municipio(recorte), so_flavio=so_flavio
        )
    )
    st.markdown(
        f"Nas {dados.inteiro(flavio_venceu['municipios'])} cidades em que o Flávio venceu os "
        "dois turnos, o Lula perdeu, mas encurtou a distância em "
        f"{milhoes(flavio_venceu['virada'])} de votos. São "
        f"{dados.pct(flavio_venceu['pct_virada'], 0)} da virada inteira, vinda de lugares "
        "onde ele continuou perdendo."
    )
    regioes = dados.tabela_virada(g.resumo, "regiao", dados.NOMES_REGIAO)
    ne = dados.linha_resumo(g.resumo, "regiao", "NE")
    sudeste = dados.linha_resumo(g.resumo, "regiao", "SE")
    norte = dados.linha_resumo(g.resumo, "regiao", "N")
    sem_ne = br["margem_real"] - ne["virada"]
    st.markdown(
        f"O Nordeste pesou mais que o tamanho dele. Tem {dados.pct(ne['pct_aptos_2t'], 0)} "
        f"dos eleitores e deu {dados.pct(ne['pct_virada'], 0)} da virada. Mas, se a "
        "diferença entre os dois no Nordeste tivesse ficado igual à do 1º turno, o Lula "
        f"ainda venceria, por {milhoes(sem_ne)} de votos. O Sudeste virou "
        f"{milhoes(sudeste['virada'])}, mais que o Nordeste ({milhoes(ne['virada'])}). E no "
        f"Norte o Lula saiu de {dados.inteiro(abs(norte['margem_1t']) / 1e3)} mil votos atrás pra "
        f"uma vitória por {dados.inteiro(norte['margem_real'])} votos."
    )
    config_virada = {
        "municipios": st.column_config.NumberColumn("Municípios", format="localized"),
        "pct_aptos": st.column_config.NumberColumn("Eleitores (% do Brasil)", format="%.0f%%"),
        "virada": st.column_config.NumberColumn("Virada (votos)", format="localized"),
        "pct_virada": st.column_config.ProgressColumn(
            "Parte da virada", format="%.0f%%", min_value=0, max_value=100, color=COR["lula"]
        ),
        "virada_100": st.column_config.NumberColumn("Virada a cada 100 eleitores", format="%.1f"),
    }
    st.dataframe(
        regioes.select("nome", "pct_aptos", "pct_virada", "virada", "virada_100").with_columns(
            pl.col("pct_aptos", "pct_virada") * 100
        ),
        hide_index=True,
        width="stretch",
        column_config={"nome": "Região", **config_virada},
    )
    st.caption("A virada é quanto a diferença Lula − Flávio andou do 1º pro 2º turno, em votos.")

    # ------------------------------------------------ Capitais e interior
    st.subheader("As capitais e o entorno delas viraram mais")
    recortes = dados.tabela_virada(g.resumo, "recorte_faixa", dados.NOMES_RECORTE)
    por_recorte = {
        r["grupo"]: r
        for r in g.resumo.filter(pl.col("dimensao") == "recorte").iter_rows(named=True)
    }
    pequenas = dados.linha_resumo(g.resumo, "recorte_faixa", "interior/ate_10mil")

    def por_100(r: dict) -> str:
        return decimal(r["virada"] / r["aptos_2t"] * 100)

    st.markdown(
        "Dizem que a virada veio das capitais e das periferias delas, mais do que das "
        "cidades pequenas do interior. Os números apontam pra isso. A cada 100 eleitores, o "
        f"Lula tirou {por_100(por_recorte['capital'])} votos da diferença nas capitais e "
        f"{por_100(por_recorte['entorno_capital'])} nas outras cidades da região "
        f"metropolitana delas. No interior foram {por_100(por_recorte['interior'])}, e nas "
        f"cidades com até 10 mil eleitores, {por_100(pequenas)}. Como o interior tem "
        f"{dados.pct(por_recorte['interior']['pct_aptos_2t'], 0)} dos eleitores, ele ainda "
        f"respondeu por {dados.pct(por_recorte['interior']['pct_virada'], 0)} da virada."
    )
    st.dataframe(
        recortes.select("nome", "municipios", "pct_aptos", "pct_virada", "virada_100").with_columns(
            pl.col("pct_aptos", "pct_virada") * 100
        ),
        hide_index=True,
        width="stretch",
        column_config={"nome": "Tipo de cidade", **config_virada},
    )
    st.caption(
        "As regiões metropolitanas são as da lista oficial do IBGE (2025) que contêm uma "
        "capital, com a RIDE no caso de Brasília e Teresina. Campo Grande e Rio Branco não "
        "têm região metropolitana. O tamanho é pelos eleitores aptos do 2º turno. A periferia "
        "dentro de cada capital precisa de uma análise por zona eleitoral, que ainda não fiz."
    )

    # ------------------------------------------------ Os mapas
    st.subheader("A virada no mapa")
    st.markdown(
        "O fundo é o resultado do 2º turno, vermelho onde o Lula venceu e azul onde o Flávio "
        "venceu. Por cima, três cores marcam os municípios que trocaram de lado desde 2022."
    )
    contagem = {
        r["grupo"]: r["municipios"]
        for r in g.resumo.filter(pl.col("dimensao") == "cor_mapa").iter_rows(named=True)
    }
    for coluna, (cor, descricao) in zip(st.columns(3), dados.TRAJETORIAS_MAPA.items(), strict=True):
        cartao(
            mapa.COR_TRAJETORIA[cor],
            descricao,
            f"{dados.inteiro(contagem.get(cor, 0))} municípios",
            "",
            coluna,
        )
    recorte = escolher_uf("uf_trajetoria")
    mostrar_mapa(
        mapa.figura_trajetoria(
            dados.mapa_2o_turno(vm, recorte),
            malha_municipio(recorte),
            nomes=nomes,
            trajetorias=dados.TRAJETORIAS_MAPA,
        )
    )

    st.subheader("Mais gente foi votar no 2º turno")
    nao_voto = {
        t: (br[f"abstencao_{t}"] + br[f"branco_{t}"] + br[f"nulo_{t}"]) / br[f"aptos_{t}"]
        for t in ("1t", "2t")
    }
    caiu = vm.filter(pl.col("var_nao_voto") < 0).height
    subiu_norte = vm.filter((pl.col("var_nao_voto") >= 0) & (pl.col("regiao") == "N")).height
    st.markdown(
        "Somando abstenção, brancos e nulos, o não-voto caiu de "
        f"{dados.pct(nao_voto['1t'])} pra {dados.pct(nao_voto['2t'])} dos eleitores, e caiu "
        f"em {dados.inteiro(caiu)} municípios. Quanto mais escuro o amarelo, mais ele caiu. "
        f"Em cinza ficam as {dados.inteiro(vm.height - caiu)} cidades onde ele subiu ou "
        f"ficou igual, {dados.inteiro(subiu_norte)} delas no Norte."
    )
    recorte = escolher_uf("uf_nao_voto")
    mostrar_mapa(mapa.figura_nao_voto(dados.mapa_nao_voto(vm, recorte), malha_municipio(recorte)))
    abstencao, branco_nulo = st.columns(2)
    for coluna, titulo, chaves in (
        (abstencao, "Abstenção", ("abstencao",)),
        (branco_nulo, "Brancos e nulos", ("branco", "nulo")),
    ):
        antes = sum(br[f"{c}_1t"] for c in chaves)
        depois = sum(br[f"{c}_2t"] for c in chaves)
        coluna.metric(
            titulo,
            dados.pct(depois / br["aptos_2t"]),
            pontos((depois / br["aptos_2t"] - antes / br["aptos_1t"]) * 100),
            delta_color="inverse",
            help=f"{dados.pct(antes / br['aptos_1t'])} no 1º turno. "
            f"{dados.inteiro(antes - depois)} pessoas a menos. Em % dos eleitores aptos.",
        )
    st.markdown(
        "O comparecimento ajudou, mas não explica tudo sozinho. As cidades onde mais gente "
        "voltou a votar não são exatamente as que mais viraram, e a relação entre os dois "
        "mapas existe, mas é fraca."
    )
    rodape()

# ---------------------------------------------------------------- Dados
with aba_dados:
    st.markdown(
        "Aqui estão todas as tabelas, pra quem quer ir além do mapa. Os números da projeção "
        "mudam com o cenário escolhido logo abaixo."
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
        "quanto a regressão de 2022 erra, em média, nas seções da zona. Quanto menor, melhor."
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
            "UFs, porque cada UF é uma regressão própria sobre todas as suas seções, e as duas "
            "estimativas podem divergir (em 2022, por exemplo, a fração de eleitores de Lula "
            "que foi para Jair Bolsonaro fica perto de zero em todas as UFs, mas em 1,1% no "
            "Brasil)."
        )
    else:
        st.caption(
            "A estimativa da UF inteira, numa regressão própria; a projeção usa a de cada zona."
        )
    mostrar_matriz(ano_ref, uf_ref)

    st.subheader("Viradas por município (2º turno de 2022 × 1º turno de 2026)")
    st.markdown(
        "Os municípios em que o lado na frente mudou, comparando quem liderava entre Lula e "
        "Jair Bolsonaro no 2º turno de 2022 com quem lidera entre Lula e Flávio Bolsonaro no "
        "1º turno de 2026. O Brasil não inclui o exterior, que fica à parte no seletor (lá, "
        "cada cidade com consulado conta como um município). Não depende do cenário."
    )
    st.info(dados.RESSALVA_VIRADAS)
    uf_virada = st.selectbox(
        "Abrangência das viradas",
        # O exterior por último, como nas outras tabelas por UF.
        [
            None,
            *sorted(
                set(g.viradas_resumo["uf"].drop_nulls()),
                key=lambda uf: (uf == dados.UF_EXTERIOR, uf),
            ),
        ],
        format_func=lambda uf: "Brasil (sem o exterior)" if uf is None else dados.NOMES_UF[uf],
    )
    resumo_virada = dados.resumo_viradas(g.viradas_resumo, uf_virada)
    unidade = "cidade" if uf_virada == dados.UF_EXTERIOR else "município"
    cartoes_viradas(resumo_virada, unidade)
    sem_par = resumo_virada["lula_para_flavio"]
    exemplo = (
        " (como Boa Esperança do Norte, MT, instalado em 2025)" if uf_virada in (None, "MT") else ""
    )
    st.caption(
        "A variação é a mudança, em pontos percentuais, da fração de Lula entre os votos dos "
        f"dois lados; a maior e a menor são em módulo. Ficam de fora os {unidade}s que existem "
        f"só em um dos anos, {sem_par['municipios_so_2022']} só em 2022 e "
        f"{sem_par['municipios_so_2026']} só em 2026{exemplo}."
    )
    st.dataframe(
        dados.tabela_viradas(g.viradas, uf_virada).with_columns(pl.col("d22", "d26") * 100),
        hide_index=True,
        width="stretch",
        height=360,
        column_config={
            "nome": unidade.capitalize(),
            "sentido": "Virada",
            "aptos_2026": st.column_config.NumberColumn(
                "Eleitores aptos (2026)", format="localized"
            ),
            "d22": st.column_config.ProgressColumn(
                "Lula, 2º turno de 2022 (% dos dois lados)",
                format="%.1f%%",
                min_value=0,
                max_value=100,
                color=COR["lula"],
            ),
            "d26": st.column_config.ProgressColumn(
                "Lula, 1º turno de 2026 (% dos dois lados)",
                format="%.1f%%",
                min_value=0,
                max_value=100,
                color=COR["lula"],
            ),
            "variacao_pp": st.column_config.NumberColumn("Variação (p.p.)", format="%+.1f"),
        },
    )
    rodape()

# ---------------------------------------------------------------- Método
with metodo:
    st.subheader("Como a projeção é feita")
    st.markdown(
        f"""
O voto de cada pessoa é secreto, e ninguém sabe se quem votou em Ciro em 2022 foi de Lula ou
de Bolsonaro no 2º turno. Mas cada **seção** publica o seu boletim de urna, e o saldo de
centenas de seções de uma mesma zona deixa recuperar, por regressão, a **transferência de
votos**, ou seja, que fração de cada grupo do 1º turno foi para cada opção do 2º turno.

1. Estimação. Em cada zona eleitoral, uma regressão de mínimos quadrados restrita
   (o modelo de Goodman, com cada fração entre 0 e 1 e cada linha somando 1) usa as seções
   como observações, tudo em fração dos eleitores aptos e ponderado pelos aptos de cada
   seção. As categorias do 1º turno são os dois lados, outros candidatos, branco, nulo e
   abstenção; as do 2º turno são os dois lados, branco, nulo e abstenção. Uma zona com menos
   de 10 seções usa a estimativa da UF.
2. Ligação a 2026. Cada seção de 2026 recebe a transferência da zona de 2022 com o mesmo
   município e número de zona ({dados.pct(match.get("municipio_zona", 0.0), 2)} do
   eleitorado). Se o par não existia, recebe a média das zonas que continham o município
   em 2022 ({dados.pct(match.get("municipio", 0.0), 2)}), e em último caso a da UF
   ({dados.pct(match.get("uf", 0.0), 2)}). Essas frações são a **taxa de match**.
3. Projeção. As frações são aplicadas aos votos reais do 1º turno de 2026, seção por
   seção, e somadas por zona, município, UF e Brasil.

Cada lado leva o nome do finalista de 2026 e liga os finalistas equivalentes
de cada eleição. O lado Lula é Haddad (PT) em 2018 e Lula (PT) em 2022 e 2026, e o lado Flávio
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
        "backtest é a única medida empírica do erro do método, e vem de um único teste "
        "histórico e não é margem de erro. A projeção de 2026 não é corrigida por ele, porque "
        "isso seria apostar que o viés de uma única eleição se repete."
    )

    st.subheader("O método aplicado a 2022 (backtest)")
    st.markdown(
        "A transferência de 2018 aplicada ao 1º turno de 2022, pela mesma ligação em 3 "
        "passos, e comparada com o 2º turno real de 2022. É um caso parecido com o de 2026, "
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

    st.subheader("Estabilidade da transferência (2018 × 2022)")
    st.markdown(
        "Se a transferência de cada grupo se repete de uma eleição para a outra, faz sentido "
        "usar a de 2022 em 2026. Os eleitores dos dois lados, de nulo e a abstenção quase não "
        f"mudam. Os de outros candidatos mudam muito. Dos que escolheram um lado, "
        f"**{dados.pct(outros_2018, 0)} foram para o lado Lula em 2018 e "
        f"{dados.pct(outros_2022, 0)} em 2022**. Por isso existe o cenário de sensibilidade. "
        "A medida abaixo é a distância de variação total (TVD) entre as duas linhas, onde 0 é "
        "igual e 1 é totalmente diferente."
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

    st.subheader("As pesquisas de 2022 como referência externa")
    st.markdown(
        "Duas pesquisas registradas no TSE perguntaram, logo depois do 1º turno de 2022, em quem "
        "os eleitores de Ciro e de Tebet votariam no 2º turno. A comparação usa a mesma "
        "medida, a fração dos eleitores de outros candidatos que escolheram um lado e foi "
        "para Lula. Elas aparecem lado a lado, sem média, porque as duas divergem muito entre si e "
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
- Inferência ecológica. O voto individual não é observável, só o saldo de cada seção.
  As frações descrevem zonas, não pessoas.
- Suposição central. Os eleitores de cada lado, de outros candidatos, brancos, nulos e
  abstenção se comportam como na eleição de referência. Em 2026 o lado Flávio troca de
  candidato, e isso pode mudar o comportamento.
- Taxa de match. Quase todo o eleitorado de 2026 é ligado à mesma zona de 2022, mas o
  rezoneamento deixa uma parte pequena com uma transferência aproximada.
- Não é pesquisa. Ninguém foi entrevistado, e não há intervalo estatístico.
"""
    )
    rodape()

# ---------------------------------------------------------------- Como foi feito
with engenharia:
    st.markdown(
        f"""
Este dashboard é a ponta de um projeto de engenharia de dados, um pipeline medallion
(bronze → silver → gold) sobre os boletins de urna do TSE para Presidente em 2018, 2022 e
2026. O código está no [GitHub]({REPO}).

#### Bronze (ingestão)
- As URLs vêm da API CKAN do Portal de Dados Abertos do TSE, porque o nome do arquivo muda
  a cada republicação.
- Cada zip (um por UF e turno) é conferido contra o `.sha512` oficial.
- Um manifest registra a URL, o hash, o tamanho e o status de cada arquivo (`success`,
  `corrupted`, `failed` ou `superseded`).
- O re-run pula o que já está íntegro, sem consultar o servidor, e tem retry com backoff.

#### Silver (normalização)
- Lê cada zip em streaming (cerca de 32 GB descomprimidos por 1º turno, com todos os
  cargos), fica só com Presidente e normaliza as diferenças de cada ano (nomes de coluna,
  encoding, aspas e a forma do exterior).
- O resultado vira Parquet tipado, montado pelo DuckDB, em cerca de 2 minutos.
- Cada seção precisa respeitar três invariantes (votos = comparecimento, aptos =
  comparecimento + abstenção e nenhuma duplicata). A seção que viola uma delas vai para
  quarentena com o motivo, e o build falha se a quarentena passar de 0,1%.
- Os totais nacionais batem voto a voto com o resultado oficial (2022, 2º turno, Lula
  60.345.999 × Jair Bolsonaro 58.206.354). Só caíram em quarentena urnas anuladas pelo TSE.

#### Gold (o modelo)
- A regressão restrita por zona (SLSQP, cerca de 10 ms por zona), a ligação em 3 passos, os
  cenários, o backtest, a estabilidade e a referência externa.
- O build confere os lados contra o nome e o partido do boletim e falha se um 2º turno
  tiver voto fora deles.
- As tabelas pequenas ficam versionadas no repositório em Parquet. Este dashboard lê só
  essas tabelas.

#### Qualidade
- Testes com recortes reais pequenos de cada ano para o parsing.
- Testes das invariantes.
- Um teste de recuperação do modelo, com seções sintéticas geradas de uma transferência
  conhecida, que o estimador precisa recuperar.
- As decisões de arquitetura estão registradas em ADRs no repositório.

#### Stack

Python 3.12 (uv), DuckDB, Polars, Parquet, SciPy, Streamlit, Plotly, ruff e
pre-commit. Uma CLI com um comando por camada, encadeados por `make all`.
"""
    )
    rodape()
