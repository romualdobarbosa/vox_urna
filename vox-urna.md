# Vox Urna

> Projeto de portfólio de **engenharia de dados**. É um pipeline medallion sobre os boletins de urna do TSE (Presidente, 2018, 2022 e 2026). Ele estima a transferência de votos entre turnos e publica uma projeção do 2º turno de 2026.

Vocabulário: [GLOSSARY.md](GLOSSARY.md). Decisões de arquitetura: [docs/adr/](docs/adr/).

## Objetivo

- **Produto principal (o que vai para o portfólio):** a ingestão e a arquitetura medallion (bronze → silver → gold), robustas, idempotentes e testadas.
- **Fator viralização:** publicar a projeção do 2º turno **até sexta, 23/10/26**, e o confronto entre projeção e resultado real depois de 25/10.
- **Não é futurologia nem opinião.** O projeto é uma projeção metodológica a partir de dados oficiais, com as suposições explícitas. Não há compromisso de acertar o resultado.

## Escopo

**Dentro:**
- Cargo **Presidente** apenas.
- 2018: 1º e 2º turno, por seção. Servem para calibrar o cenário de sensibilidade de "outros" e para o backtest.
- 2022: 1º e 2º turno, por seção. Fonte do β do cenário base.
- 2026: 1º turno por seção. O 2º turno é ingerido depois de 25/10, para o confronto.
- Exterior incluído no modelo e nos totais, como uma zona a mais.
- Backtest 2018 → 2022 como item **opcional** (é o primeiro corte se atrasar).
- MVP publicado até 23/10/26.

**Fora (por enquanto):**
- Governador e outros cargos.
- EI bayesiano (`PyEI`).
- Orquestrador (Dagster/Prefect). A CLI é escrita para encaixar em assets depois.
- Intervalos estatísticos (bootstrap). Na v1 a incerteza é a faixa entre cenários.

## Fontes de dados

**Portal de Dados Abertos do TSE** (CKAN, `dadosabertos.tse.jus.br`). Datasets `resultados-{ano}-boletim-de-urna` (2018, 2022 e 2026 confirmados), com um zip por UF e turno, cada um acompanhado de um `.sha512` oficial, servidos de `cdn.tse.jus.br`. O nome do arquivo tem um timestamp variável, então **a URL sempre vem da API** (`package_show`).

Formato confirmado:
- CSV em **latin-1**, separador `;`, num zip que também traz um PDF de leiame.
- **Formato longo**: uma linha por (seção, cargo, votável).
- Colunas-chave: `SG_UF, CD_MUNICIPIO, NM_MUNICIPIO, NR_ZONA, NR_SECAO, DS_CARGO_PERGUNTA, SG_PARTIDO, DS_TIPO_VOTAVEL, NM_VOTAVEL, QT_VOTOS, QT_APTOS, QT_COMPARECIMENTO, QT_ABSTENCOES, DS_SECOES_AGREGADAS`.

Diferenças por ano, que o silver normaliza:
- 2018: a UF vem como **`SG_ UF`** (com espaço), e `CD_MUNICIPIO` tem zero à esquerda (`"01007"` em 2018, `"1007"` depois).
- Algumas colunas mudam de nome: `DS_AGREGADAS` → `DS_SECOES_AGREGADAS`, `CD_FLASCARD_…` → `CD_FLASHCARD_…`.
- O uso de aspas, o fim de linha (CRLF/LF) e a forma do partido nulo (`#NULO#` em 2018, `-1` em 2022) variam.
- O exterior é `ZZ` em 2022 e 2026 e "Exterior" em 2018. O "município" é a cidade do consulado (cerca de 145), e há uma única zona.

Plano B (não usado, mas documentado): os JSONs de `resultados.tse.jus.br`. Referências: https://github.com/doccaz/urnas-br e a especificação EA17 do TSE.

## Arquitetura (medallion)

Execução por CLI, um comando por camada (`vox-urna bronze --ano 2026 --turno 1`, `vox-urna silver`, `vox-urna gold`), encadeados por `make all`.

### Bronze: ingestão (a vitrine do projeto)
- Resolve as URLs pela API CKAN.
- Manifest (`data/bronze/manifest.csv`): `url, ano, turno, uf, caminho, sha512, bytes, downloaded_at, status`, com status `success`, `corrupted`, `failed` ou `superseded`. Quando o TSE republica um arquivo, ele aparece com outro timestamp no nome, e a versão anterior fica `superseded`.
- Arquivos em `data/bronze/ano=AAAA/turno=N/`, com o nome original.
- **Integridade:** baixa o `.sha512` oficial e confere o zip contra ele. Se não bater, o status vira `corrupted` e o arquivo é baixado de novo no próximo run.
- **Idempotência:** no re-run, pula o que já está com `status=success` no manifest, sem consultar o servidor. `--force` baixa tudo de novo.
- Retry com backoff.
- O arquivo bruto é guardado como veio.

### Silver
- Lê cada zip **em streaming** (o 1º turno tem cerca de 32 GB descomprimido por eleição, com todos os cargos), mantém só as linhas de Presidente (`CD_CARGO_PERGUNTA = 1`; os textos mudam de capitalização entre anos), normaliza o cabeçalho por ano e converte para UTF-8. O DuckDB monta as tabelas. Tudo roda em cerca de 2 min.
- Grava Parquet tipado em `data/silver/{tabela}/ano=AAAA/turno=N/`:
  - `secao`: uma linha por seção, como o BU reporta (a seção principal já inclui as agregadas), com `uf, cd_municipio, nm_municipio, zona, secao, aptos, comparecimento, abstencao, branco, nulo, nominais, secoes_agregadas`.
  - `votos`: votos nominais por seção e candidato (`nr_votavel, nm_votavel, sg_partido, votos`), com nome e partido reais.
  - `rejeitadas`: seções em quarentena e o motivo.
- **Invariantes:** urna apurada (o TSE anula algumas urnas, cujos votos não entram na totalização); soma dos votos = comparecimento; aptos = comparecimento + abstenção; nenhuma seção duplicada; aptos e comparecimento constantes dentro da seção. Uma seção que viola uma invariante vai para quarentena, e o build só falha se as rejeitadas passarem de 0,1% ([ADR 0001](docs/adr/0001-quarentena-de-secoes-invalidas.md)).
- **Idempotência:** `data/silver/_construcao.json` guarda os sha512 do bronze usados em cada turno. O turno só é reconstruído se o bronze mudar ou com `--force`.
- **Conferência com o resultado oficial:** os totais nacionais batem voto a voto (ex: 2022 2T, Lula 60.345.999 × Jair 58.206.354). Nos dados reais só caíram em quarentena urnas anuladas: 4 + 1 seções em 2018, 4 + 1 em 2022 e nenhuma em 2026.

### Gold
Arquivos em `data/gold/{tabela}.parquet`, reconstruídos inteiros a cada `vox-urna gold` (cerca de 2 min). As tabelas por seção (`categorias_secao`, `painel_secao_*`, cerca de 100 MB) ficam fora do git; as demais são versionadas para o dashboard.
- `lados`: a config de lados ([ADR 0003](docs/adr/0003-lados-nomeados-pelo-finalista-de-2026.md)), conferida contra o nome e o partido do BU. O build falha se divergir, ou se um 2º turno tiver voto fora dos lados.
- `categorias_secao`: cada seção nas categorias do modelo (lados, outros, branco, nulo, abstenção), em fração dos aptos.
- `painel_secao_{2018,2022}`: o 1º turno juntado ao 2º por `(uf, municipio, zona, secao)`.
- `transferencia_zona_{2018,2022}`: o β por zona (`nivel = 'zona'`, com `fonte` dizendo se é o próprio ou o da UF) e por UF (`nivel = 'uf'`).
- `ligacao_2026`: o β de cada (UF, município, zona) de 2026 por eleição de referência (`ano_ref`) e o passo da ligação.
- `projecao_2026`: a projeção por cenário (`base` e `outros_2018`), agregada por zona, município (no exterior, a cidade), UF (`ZZ` é o exterior) e Brasil.
- `taxa_match`: a taxa de match ponderada pelos aptos de cada painel e de cada passo da ligação.
- `backtest_2022` (opcional): o β de 2018 aplicado ao 1º turno de 2022, comparado com o 2º turno real de 2022.
- Depois de 25/10: `confronto_2026` (projeção contra resultado real) e `transferencia_zona_2026` (o β real de 2026, comparado com o de 2022).

## Modelo: transferência de votos (Goodman restrito)

O voto individual mudando de lado não é observável; só o saldo por seção. A regressão sobre as várias seções de uma zona recupera os fluxos que o saldo de uma seção isolada esconde.

Por zona, usando as seções como observações, com tudo em **fração dos aptos**:

```
2T_j = Σ_k β_kj · 1T_k
k ∈ {lula, flavio, outros, branco, nulo, abstenção}   (1º turno)
j ∈ {lula, flavio, branco, nulo, abstenção}           (2º turno)

s.a.  0 ≤ β_kj ≤ 1,   Σ_j β_kj = 1
```

- Mínimos quadrados ponderados pelos aptos de cada seção. Solver: `scipy.optimize.minimize` (SLSQP), cerca de 10 ms por zona.
- **Lados** ([ADR 0003](docs/adr/0003-lados-nomeados-pelo-finalista-de-2026.md)): cada categoria de finalista leva o nome do finalista de 2026. Uma config explícita diz quem ocupa cada lado em cada eleição:

  | Lado | 2018 | 2022 | 2026 |
  |---|---|---|---|
  | `lula` | Haddad (PT) | Lula (PT) | Lula (PT) |
  | `flavio` | Jair Bolsonaro (PSL) | Jair Bolsonaro (PL) | Flávio Bolsonaro (PL) |

- **Estimação e ligação** ([ADR 0002](docs/adr/0002-fallback-hierarquico-do-beta.md)): o β é estimado por zona inteira (o da UF se a zona tiver menos de 10 seções). Cada seção de 2026 é ligada assim: (município, zona) de 2022 → zona(s) do município em 2022, ponderadas pelo eleitorado → UF.
- **Cenários:** o **base** usa o β de 2022 inteiro. O de **sensibilidade** usa o β de 2022 com a linha "outros" trocada pela de 2018. Se 2018 for cortado, a sensibilidade vira uma divisão igual de "outros" entre os lados.
- **Incerteza:** a faixa entre os cenários.
- **Limitações (vão no README):** inferência ecológica (falácia ecológica); suposição de que o eleitorado de cada lado e de "outros" se comporta como na eleição de referência; taxa de match.

## Entregáveis

- Repo `vox-urna` com pipeline reproduzível, testes e README com a metodologia e as limitações.
- Dashboard Streamlit no **Streamlit Community Cloud**, lendo as tabelas gold versionadas no repo em Parquet. Bronze e silver ficam fora do git.
  - Mapa por UF e município (malhas do IBGE, com uma tabela de correspondência entre os códigos de município do TSE e do IBGE). As zonas aparecem só em tabelas e rankings.
  - Exterior fora do mapa, nas listas por **país** (tabela cidade → país versionada) e por **cidade**.
  - Seletor de cenários, com as suposições escritas por extenso.
  - Nomes e partidos reais. Vermelho para o lado do PT, azul para o outro.
  - Taxa de match ponderada pelo eleitorado, sempre visível.
- Post de lançamento (até 23/10) e post de confronto (depois da divulgação oficial do 2º turno).

## Testes

- Fixtures reais pequenas (recortes do AC de 2018, 2022 e 2026) para o parsing e a normalização por ano.
- Testes das invariantes do silver.
- **Teste de recuperação do modelo:** seções sintéticas geradas a partir de um β conhecido, e o estimador precisa recuperá-lo dentro de uma tolerância.

## Stack

Python 3.12 (uv), DuckDB, Polars, Parquet, cvxpy/scipy, Streamlit, ruff, pre-commit.

## Cronograma

| Data | Entrega |
|---|---|
| Ter 20/10 | Esqueleto do repo e bronze completo (2018, 2022 e 2026, com manifest e sha512) |
| Qua 21/10 | Silver com normalização por ano e testes |
| Qui 22/10 | Gold, modelo (β, ligação em 3 passos, cenários) e backtest |
| Sex 23/10 | Dashboard, deploy e post de lançamento |
| Após 25/10 | Ingestão do 2º turno de 2026, confronto, β real de 2026 e post |

As datas são **limites**, não metas. A ordem de construção é fixa, mas cada etapa começa assim que a anterior termina, e a publicação sai assim que a projeção base e o dashboard estiverem prontos.

**Ordem de corte se atrasar:** (1) backtest; (2) tabela cidade → país (mostra só a cidade); (3) 2018 (a sensibilidade vira uma divisão igual). A projeção base nunca é cortada.

## Publicação e comunicação

- Enquadramento: **projeção metodológica a partir de dados oficiais**, não opinião nem previsão. O tom é neutro: o foco fica nos dados, no método e nas limitações.
- Aviso fixo no post, no topo do dashboard e no README: *"Não é pesquisa eleitoral. Não houve coleta de opinião com eleitores: é uma projeção com metodologia própria, a partir dos dados oficiais das urnas, abertos a qualquer cidadão."*
- Vocabulário: "projeção" e "cenário"; nunca "pesquisa", "intenção de voto" ou "previsão".
- A manchete mostra a faixa entre os cenários, não um número único.
- Nada é publicado no dia 25/10.
