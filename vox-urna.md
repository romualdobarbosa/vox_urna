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
- 2026: 1º turno por seção. O 2º turno é ingerido depois de 25/10, para o confronto, pelos arquivos de urna do site de resultados, porque o CKAN demorou a publicar.
- 2026, os dois turnos, também pelos resultados divulgados (JSON por Brasil, UF e município). É uma segunda fonte, que sai na noite da eleição, dias antes dos BUs do CKAN.
- Exterior incluído no modelo e nos totais, como uma zona a mais.
- Backtest 2018 → 2022, estabilidade da transferência entre 2018 e 2022 e referência externa (pesquisas de 2022), descritos em "Avaliação do método".
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

**Resultados divulgados** (`resultados.tse.jus.br`), a fonte do site de resultados do TSE. Um JSON por abrangência, publicado na noite da eleição:
- `comum/config/ele-c.json`: as eleições do ciclo e os seus códigos (Presidente 2026: `6257` no 1º turno, `6258` no 2º). A API só mantém o ciclo corrente; 2018 e 2022 já saíram dela.
- `ele2026/{eleicao}/config/mun-e{eleicao}-cm.json`: a lista de municípios de cada UF (5.571 no Brasil e 186 cidades no exterior), com o código do TSE, o do IBGE e as zonas.
- `ele2026/{eleicao}/dados/{uf}/{uf}{municipio}-c0001-e{eleicao}-u.json`, e os equivalentes de UF (`{uf}-c0001-…`) e Brasil (`br-c0001-…`): eleitorado, aptos das seções apuradas, comparecimento, abstenção, válidos, brancos, nulos, nulos técnicos e os votos de cada candidato. Não há arquivo por zona nem por seção.
- Diferenças em relação ao BU: o JSON separa os **nulos técnicos** (votos em candidato com registro indeferido, que o TSE soma aos nulos e o BU conta como nominais), e o eleitorado inclui as seções não instaladas.

**Arquivos de urna** (`resultados.tse.jus.br/oficial/ele{ano}/arquivo-urna/{pleito}/`), o BU de cada seção como saiu da urna, publicado na noite da eleição. É a mesma granularidade do bweb do CKAN. Usado em 2026/2 (pleito `3221`), porque três dias depois da eleição o CKAN ainda não tinha o turno:
- `config/{uf}/{uf}-p{pleito}-cs.json`: as seções de cada UF por município e zona (517.179 no Brasil em 2026/2). Uma seção agregada traz `nsp` (a principal) e não tem arquivo próprio; a principal lista as agregadas em `nsa`.
- `dados/{uf}/{municipio}/{zona}/{secao}/p{pleito}-{uf}-m{municipio}-z{zona}-s{secao}-aux.json`: a situação da seção (`st`) e os hashes das urnas que geraram resultado ali, cada um com a sua situação e os seus arquivos (`bu`, `rdv`, `log`, `vota`, `imgbu`).
- `{hash}/{nome}-bu.dat`: o BU binário, em ASN.1 (BER) dentro de um envelope genérico. A especificação é a do TSE (formato dos arquivos de BU, RDV e assinatura digital, v2), copiada em `src/vox_urna/bronze/asn1/bu.asn1`, e o site do TSE bloqueia o download automatizado dela. No exterior, onde se vota em cédula, algumas seções têm só o `busa` (BU do Sistema de Apuração), no mesmo formato.
- O BU traz só os números de candidato e partido, sem nome e sem sigla, e não traz as seções agregadas.
- O servidor anuncia limite de 5.000 req/s, mas com 32 conexões ele devolveu 429 em massa por ~10 min; com 16 não houve erro.

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

### Bronze: resultados divulgados
`vox-urna bronze-resultados` (`make bronze-resultados`), a segunda ingestão, com os mesmos princípios e outra fonte.
- Resolve o código da eleição de Presidente de cada turno na config do TSE, baixa a lista de municípios e monta, a partir dela, a URL do Brasil, de cada UF e de cada município (cerca de 5.800 arquivos por turno).
- Um pool de 4 threads baixa os arquivos. Os workers só baixam e validam; quem grava o manifest é a thread principal, a cada 500 arquivos e no fim.
- Manifest próprio (`data/bronze/resultados/manifest.csv`): `url, ano, turno, abrangencia, uf, cd_municipio, caminho, sha512, bytes, gerado_em, downloaded_at, status`. O `gerado_em` é a data e a hora em que o TSE gerou o arquivo.
- Arquivos em `data/bronze/resultados/ano=AAAA/turno=N/`, com o nome original.
- **Integridade:** não há `.sha512` oficial. O sha512 é calculado na chegada, e o JSON precisa ser da eleição, do turno e da abrangência pedidos e ter o cargo de Presidente; se não, fica `corrupted`. Se a totalização daquele arquivo ainda não for final (`tf`), fica `incomplete`. Nos dois casos o arquivo não vai para o disco e é baixado de novo no próximo run.
- Idempotência, retry e `--force` como no bronze do CKAN. `--uf` baixa só as UFs pedidas, sem o Brasil.

### Bronze: arquivos de urna
`vox-urna bronze-urna --ano 2026 --turno 2` (`make bronze-urna`), a terceira ingestão. Fica fora do `make all`: são ~1 milhão de requisições, e o comando sai com erro enquanto houver seção sem BU (as não instaladas do exterior ficam assim para sempre).
- Baixa a config de seções de cada UF, e para cada seção com BU próprio, o aux.json e o BU do hash escolhido. Pool de 16 threads; a thread principal grava o manifest a cada 2.000 seções e no fim.
- **Hash escolhido:** o "Totalizado" mais recente; sem ele, o "Recebido" mais recente. Seções "Recebida" entram: o BU delas é o único publicado, chegou ao TSE na noite da eleição ou nos dias seguintes e soma, junto com as "Totalizada", exatamente o resultado divulgado (em 2026/2, 10.844 seções em 67 zonas e 135 municípios). A situação vai para o silver em `st_secao`. Do arquivo, o `bu`, ou o `busa` se não houver `bu`.
- Um manifest por UF (`data/bronze/urna/ano=AAAA/turno=N/{UF}/manifest.csv`): `url` (a do aux.json), a seção, `hash`, `hashes` (quantos o aux.json lista; mais de um é urna trocada), `caminho, sha512, bytes, downloaded_at, status, tipo_bu, st_secao`.
- Arquivos em `data/bronze/urna/ano=AAAA/turno=N/{UF}/{municipio}/` (aux.json e BU, com o nome original) e as configs em `.../config/`. 2026/2 ocupa 4,0 GB.
- **Integridade:** não há `.sha512` oficial; o sha512 é calculado na chegada. O BU precisa decodificar e ser do pleito, do município, da zona e da seção pedidos; se não, fica `corrupted`. Seção sem BU válido (situação "Nao instalada", ou nenhum hash aceito) fica `incomplete`. Nos dois casos o BU não vai para o disco e é baixado de novo no próximo run.
- Idempotência e retry como nos outros bronzes; `--uf` e `--workers` escolhem as UFs e o pool.
- Em 2026/2: 499.207 seções com BU (488.334 Totalizada, 10.844 Recebida, 29 busa) e 41 não instaladas no exterior. ~5h de parede com o bloqueio no meio, ~1.050 s de CPU, até 564 MB de RAM.

### Silver
- Lê cada zip **em streaming** (o 1º turno tem cerca de 32 GB descomprimido por eleição, com todos os cargos), mantém só as linhas de Presidente (`CD_CARGO_PERGUNTA = 1`; os textos mudam de capitalização entre anos), normaliza o cabeçalho por ano e converte para UTF-8. O DuckDB monta as tabelas. Tudo roda em cerca de 2 min.
- Grava Parquet tipado em `data/silver/{tabela}/ano=AAAA/turno=N/`:
  - `secao`: uma linha por seção, como o BU reporta (a seção principal já inclui as agregadas), com `uf, cd_municipio, nm_municipio, zona, secao, aptos, comparecimento, abstencao, branco, nulo, nominais, secoes_agregadas, st_secao` (`st_secao` é a situação no site de resultados quando a fonte é o arquivo de urna, e nula no CKAN).
  - `votos`: votos nominais por seção e candidato (`nr_votavel, nm_votavel, sg_partido, votos`), com nome e partido reais.
  - `rejeitadas`: seções em quarentena e o motivo.
- **Invariantes:** urna apurada (o TSE anula algumas urnas, cujos votos não entram na totalização); soma dos votos = comparecimento; aptos = comparecimento + abstenção; nenhuma seção duplicada; aptos e comparecimento constantes dentro da seção. Uma seção que viola uma invariante vai para quarentena, e o build só falha se as rejeitadas passarem de 0,1% ([ADR 0001](docs/adr/0001-quarentena-de-secoes-invalidas.md)).
- **Fonte de cada turno:** o bweb do CKAN quando o manifest do CKAN tem o turno; se não, os arquivos de urna. O BU binário vira, por UF, um CSV no formato do bweb (`silver/urna.py`) e segue o mesmo caminho de quarentena e gravação. O nome e o partido dos candidatos vêm do silver `resultado_votos`, por isso o silver dos resultados divulgados roda antes. Um BU sem votável de Presidente (duas cidades do exterior em 2026/2, só com abstenção) não gera linha, como no bweb. Quando o CKAN publicar um turno, o silver passa a usá-lo sozinho; o CKAN não publica por partes, mas se publicasse, o turno passaria a ser o recorte do CKAN.
- **Conferência entre fontes:** `construir_fonte` constrói um turno de uma fonte escolhida num silver à parte, e `silver/conferencia.py` compara dois silvers seção a seção. É para o BU binário × bweb de 2026/2 quando o CKAN publicar.
- **Idempotência:** `data/silver/_construcao.json` guarda os sha512 do bronze usados em cada turno e a `fonte`. Nos arquivos de urna, ~500 mil BUs, guarda só o sha512 da lista ordenada. O turno só é reconstruído se o bronze mudar ou com `--force`. Uma coluna nova no `secao` pede `--force`, porque o gold lê as partições com o schema do primeiro arquivo.
- **Conferência com o resultado oficial:** os totais nacionais batem voto a voto (ex: 2022 2T, Lula 60.345.999 × Jair 58.206.354). Nos dados reais só caíram em quarentena urnas anuladas: 4 + 1 seções em 2018, 4 + 1 em 2022 e nenhuma em 2026. Em 2026/2 (arquivos de urna), os 5.757 municípios batem com o resultado divulgado em aptos, comparecimento, brancos, nulos e votos de cada candidato, menos Ciudad Guayana (72 aptos) e Trípoli (30 aptos), que no divulgado só têm abstenção, o mesmo caso das 41 cidades do exterior no 1º turno. Território nacional: Lula 64.056.078 × Flávio 59.654.544; com o exterior, 64.256.232 × 59.826.041. O arquivo de urna não diz se a urna foi anulada, então essa invariante não se aplica a ele; em 2026/2 o divulgado tem 0 votos anulados.

### Silver: resultados divulgados
O mesmo `vox-urna silver` constrói, a partir do bronze dos resultados, em `data/silver/{tabela}/ano=AAAA/turno=N/`:
- `resultado`: uma linha por Brasil, UF e município (`nivel`), com `uf, cd_municipio, nm_municipio, cd_ibge, eleitorado, aptos, comparecimento, abstencao, validos, branco, nulo, nulo_tecnico, anulados, gerado_em`. O Brasil tem `uf` nula; o exterior é a UF `ZZ`, sem código do IBGE.
- `resultado_votos`: os votos de cada candidato em cada linha de `resultado` (`nr_votavel, nm_votavel, sg_partido, votos`), com o nome de urna, como no BU.
- **Sem quarentena:** são totais já consolidados pelo TSE, então o build falha se faltar algum arquivo da lista de municípios, se uma conta não fechar dentro do arquivo (aptos = comparecimento + abstenção; comparecimento = válidos + brancos + nulos + nulos técnicos + anulados; válidos = soma dos candidatos) ou se os municípios não somarem a UF e as UFs não somarem o Brasil, em cada total e em cada candidato.
- Idempotência pelo mesmo `_construcao.json`, com a chave `resultados-AAAA-N`.

### Gold
Arquivos em `data/gold/{tabela}.parquet`, reconstruídos inteiros a cada `vox-urna gold` (cerca de 2 min). As tabelas por seção (`categorias_secao`, `painel_secao_*`, cerca de 100 MB) ficam fora do git; as demais são versionadas para o dashboard.
- `lados`: a config de lados ([ADR 0003](docs/adr/0003-lados-nomeados-pelo-finalista-de-2026.md)), conferida contra o nome e o partido do BU. O build falha se divergir, ou se um 2º turno tiver voto fora dos lados.
- `categorias_secao`: cada seção nas categorias do modelo (lados, outros, branco, nulo, abstenção), em fração dos aptos.
- `painel_secao_{2018,2022}`: o 1º turno juntado ao 2º por `(uf, municipio, zona, secao)`.
- `transferencia_zona_{2018,2022}`: o β por zona (`nivel = 'zona'`, com `fonte` dizendo se é o próprio ou o da UF), por UF (`nivel = 'uf'`) e Brasil (`nivel = 'brasil'`, `fonte = 'media_zonas'`). O β Brasil é, para cada linha k, a média dos β das zonas ponderada pelos eleitores da categoria k em cada zona. Ele serve só para leitura; a projeção usa os β de zona e de UF. A coluna `rmse_pp` é a qualidade do ajuste: a raiz da média dos quadrados dos resíduos (2º turno real − 1º turno × β), em pontos percentuais da fração dos aptos, com as seções ponderadas pelos aptos e a média tomada sobre as 5 categorias do 2º turno. Na zona, é o β que ela usa (o próprio ou o da UF) aplicado às suas seções; na UF, o β da UF aplicado a todas as seções dela; no Brasil fica nulo, porque esse β é uma média e não é ajustado a seções. O valor se repete nas 6 linhas k da unidade.
- `ligacao_2026`: o β de cada (UF, município, zona) de 2026 por eleição de referência (`ano_ref`) e o passo da ligação.
- `projecao_2026`: a projeção por cenário (`base` e `outros_2018`), agregada por zona, município (no exterior, a cidade), UF (`ZZ` é o exterior) e Brasil.
- `taxa_match`: a taxa de match ponderada pelos aptos de cada painel e de cada passo da ligação.
- `municipio_ibge`: o código IBGE (7 dígitos) de cada município brasileiro de 2026, para o mapa. A fonte está anotada em `gold/construcao.py`, e o build falha se algum município ficar sem par ou com o código fora da UF.
- `backtest_2022`: o β de 2018 inteiro (só o cenário base) aplicado ao 1º turno de 2022 pela mesma ligação em 3 passos (`ligacao_2022`, com a taxa de match em `ligacao_2022_ref_2018`) e comparado com o 2º turno real de 2022, nos níveis Brasil, UF e município. Por linha: a % de votos válidos do lado Lula projetada e a real, o `erro_pp` com sinal (projetado − real, em pontos percentuais), o lado vencedor de cada um e se bateu (`acertou`), e o erro por categoria em fração dos aptos (`erro_aptos_{lula,flavio,branco,nulo,abstencao}`, a fração projetada sobre os aptos do 1º turno menos a real sobre os aptos do 2º). Só entram os municípios que votaram nos dois turnos: em 2022, Caracas só votou no 2º (1.242 aptos).
- `backtest_2022_acerto`: por nível (UF e município), a % das unidades em que o lado vencedor bateu, sem ponderar (`pct_acerto`) e ponderada pelos aptos do 1º turno (`pct_acerto_aptos`). O exterior (`ZZ`) conta como uma UF a mais.
- `estabilidade_transferencia`: a TVD (`0,5 · Σ_j |p − q|`, de 0 a 1) de cada linha k entre o β de 2018 e o de 2022, nos níveis Brasil (o β Brasil, média das zonas) e UF (o β da UF), com os β já existentes e sem bootstrap. A linha `outros` junta candidatos diferentes em cada ano, então a sua TVD compara eleitorados de composição diferente.
- `referencia_externa`: as pesquisas de 2022 com cruzamento 1º × 2º turno, vindas de `gold/referencia_externa.csv`, ao lado do β Brasil de `outros` em 2022, uma linha por pesquisa (`fonte = 'pesquisa'`) e uma do método (`fonte = 'vox_urna'`), sem média. A métrica, `divisao_lula`, é Lula ÷ (Lula + Bolsonaro) entre os eleitores de `outros`. Na pesquisa, os percentuais de Ciro e de Tebet são ponderados pelos votos de cada um no 1º turno de 2022 nos BUs (`peso_ciro`, `peso_tebet`) antes da divisão, que vale tanto na base `total` quanto na de votos válidos; `divisao_lula_{ciro,tebet}` traz a divisão de cada um. No método, é `β[outros→lula] ÷ (β[outros→lula] + β[outros→flavio])`. O `outros` do método inclui os demais candidatos (Soraya, D'Avila e outros, cerca de 14% de `outros`), que as pesquisas não cruzam. O build falha se uma pesquisa não tiver as duas linhas ou se um candidato não tiver voto no BU.
- `viradas_municipio`: uma linha por município (no exterior, a cidade do consulado) que votou no 2º turno de 2022 e no 1º turno de 2026, com `d22` e `d26` = Lula ÷ (Lula + Flávio), dos votos do município somados (em 2022, Flávio é o lado de Jair Bolsonaro), `variacao_pp` = 100 · (`d26` − `d22`) e a `virada` (`lula_para_flavio` quando `d22` > 0,5 e `d26` < 0,5, `flavio_para_lula` no contrário, nula se o lado na frente não mudou; o empate exato não é virada). Compara o 1º turno de 2026 só entre os dois lados: descreve o 1º turno, não é uma projeção.
- `viradas_municipio_resumo`: por nível e sentido, uma linha mesmo sem viradas. O Brasil deixa o exterior de fora, porque as cidades de consulado, com poucos votos, dominariam os extremos; o exterior (`ZZ`) tem só a sua linha de UF. Traz os municípios pareados, os que ficaram fora por existir só em 2022 ou só em 2026 (`municipios_so_{2022,2026}`), o número de viradas, a % sobre os pareados (`pct_municipios`) e a % dos aptos de 2026 dos pareados (`pct_aptos_2026`), a média da `variacao_pp` das viradas (sem ponderar) e o município de maior e o de menor variação em módulo (`maior_*`, `menor_*`, com o sinal mantido).
- `primeiro_turno_2026`: os votos do 1º turno de 2026 nas categorias do modelo (aptos, Lula, Flávio, outros, branco, nulo e abstenção), uma linha por UF e uma do Brasil (`nivel = brasil`, `uf` nula). O Brasil inclui o exterior, como no resultado oficial. É o que a capa do dashboard usa para o resultado do 1º turno, porque `categorias_secao` não vai para o deploy.
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
- **Incerteza:** a faixa entre os cenários, com o erro do backtest logo abaixo ([ADR 0004](docs/adr/0004-erro-do-backtest-ao-lado-da-manchete.md)).
- **Qualidade do ajuste:** o RMSE dos resíduos de cada zona, em pontos percentuais, vai numa coluna de `transferencia_zona_*`.

## Avaliação do método

- **Backtest:** replica só o cenário base. A métrica principal é o erro, com sinal, na % de votos válidos do lado Lula, nos níveis Brasil, UF e município. Também sai a % de UFs e de municípios em que o lado vencedor bateu, com e sem ponderação pelos aptos, e o erro por categoria (em fração dos aptos) como diagnóstico. A projeção de 2026 não é corrigida pelo viés. A refatoração da ligação não pode mudar `projecao_2026`, `ligacao_2026` nem as linhas de 2026 da `taxa_match`, e um teste garante isso.
- **Estabilidade da transferência:** a TVD (`0,5 · Σ|p − q|`) de cada uma das 6 linhas do β, entre 2018 e 2022, nos níveis Brasil e UF. Não usa bootstrap. Uma nota avisa que a composição de `outros` mudou entre os anos.
- **Referência externa:** só entram pesquisas com relatório original e registro no TSE, da 1ª rodada do 2º turno de 2022, mostradas lado a lado e sem média. A comparação usa a divisão entre os lados, Lula ÷ (Lula + Bolsonaro), dos eleitores de `outros`. Os números por candidato são ponderados pelos votos no 1º turno de 2022, contados nos nossos BUs. O comparador é o β Brasil de `outros` em 2022.
- **Limitações (vão no README):** inferência ecológica (falácia ecológica); suposição de que o eleitorado de cada lado e de "outros" se comporta como na eleição de referência; taxa de match.

## Entregáveis

- Repo `vox-urna` com pipeline reproduzível, testes e README com a metodologia e as limitações.
- Dashboard Streamlit no **Streamlit Community Cloud**, lendo as tabelas gold versionadas no repo em Parquet. Bronze e silver ficam fora do git.
  - Mapa por UF e município (malhas do IBGE, com uma tabela de correspondência entre os códigos de município do TSE e do IBGE). As zonas aparecem só em tabelas e rankings.
  - Exterior fora do mapa, numa lista por **cidade** do consulado.
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

**Ordem de corte se atrasar:** (1) referência externa; (2) estabilidade da transferência; (3) backtest. A projeção base nunca é cortada. A tabela cidade → país foi descartada, porque a lista por cidade basta.

## Publicação e comunicação

- Enquadramento: **projeção metodológica a partir de dados oficiais**, não opinião nem previsão. O tom é neutro: o foco fica nos dados, no método e nas limitações.
- Aviso fixo no post, no topo do dashboard e no README: *"Não é pesquisa eleitoral. Não houve coleta de opinião com eleitores: é uma projeção com metodologia própria, a partir dos dados oficiais das urnas, abertos a qualquer cidadão."*
- Vocabulário: "projeção" e "cenário"; nunca "pesquisa", "intenção de voto" ou "previsão".
- A manchete mostra a faixa entre os cenários, não um número único.
- Nada é publicado no dia 25/10.
