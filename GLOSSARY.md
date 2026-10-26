# Vox Urna

Votos para Presidente nas eleições de 2022 e 2026, a partir dos boletins de urna do TSE, e um modelo de transferência de votos entre o 1º e o 2º turno.

## Geografia eleitoral

**Seção**:
A menor unidade em que os votos são apurados, identificada por (UF, município, zona, seção). É estável entre os turnos de uma mesma eleição, mas não entre eleições diferentes.
_Avoid_: sessão, urna, local de votação

**Zona**:
Uma área administrativa da Justiça Eleitoral dentro de uma UF, numerada por UF. Pode cobrir vários municípios pequenos ou só uma parte de um município grande, então zona e município não se encaixam um no outro. É mais estável entre eleições do que a seção, mas muda com o rezoneamento. Todo o exterior forma uma única zona.
_Avoid_: zona eleitoral (quando "zona" basta), distrito

## Eleição

**Turno**:
Uma das duas votações de uma eleição presidencial. O 2º turno só acontece se nenhum candidato fizer a maioria dos votos válidos no 1º.
_Avoid_: rodada, fase

**Finalista**:
Um candidato que passou para o 2º turno. É sempre chamado pelo nome e pelo partido reais, nunca por um papel anônimo.
_Avoid_: candidato A/B, vencedor do 1T, papel

**Lado**:
Uma linha de continuidade que liga um finalista de 2026 ao finalista equivalente de cada eleição anterior. Leva o nome do finalista de 2026: o lado Lula e o lado Flávio.
_Avoid_: partido, campo, polo

**Outros**:
Os votos nominais, num turno, em qualquer candidato que não seja finalista daquela eleição.
_Avoid_: terceira via, demais candidatos

**Abstenção**:
Os eleitores aptos de uma seção que não compareceram para votar. Neste projeto ela é uma categoria de escolha como as outras, não um dado faltante.
_Avoid_: ausentes, ausência, faltosos

**Boletim de urna (BU)**:
A contagem oficial de votos de uma seção num turno, publicada pelo TSE.
_Avoid_: resultado da urna, apuração

**Resultado divulgado**:
A totalização oficial que o TSE publica no site de resultados na noite da eleição, por Brasil, UF e município. Não chega à seção nem à zona. É uma fonte independente do BU para conferir os mesmos votos.
_Avoid_: resultado oficial (sozinho), apuração, parcial

## Método

**Taxa de match**:
A fração dos registros de um lado de uma ligação (seções de um turno com o outro, ou zonas de uma eleição com a outra) que encontram um par do outro lado. É uma medida de cobertura que fica visível para o público.
_Avoid_: cobertura (sozinho), taxa de join

**Transferência de votos**:
A fração dos eleitores de cada categoria do 1º turno (um lado, outros, branco, nulo, abstenção) que vai para cada categoria do 2º turno. É estimada por zona e escrita como β.
_Avoid_: migração de votos, fluxo

**Cenário**:
Um conjunto de suposições sobre a transferência de votos que é aplicado ao 1º turno de 2026. O cenário base usa a transferência de 2022. Um cenário de sensibilidade troca só a linha de "outros".
_Avoid_: simulação, hipótese

**Projeção**:
O resultado do 2º turno de 2026 obtido ao aplicar um cenário ao 1º turno de 2026.
_Avoid_: previsão, prognóstico, pesquisa

**Confronto**:
A comparação, depois da eleição, entre a projeção e o resultado real do 2º turno de 2026.
_Avoid_: validação, acerto

**Backtest**:
A aplicação do método a uma eleição passada com resultado conhecido (a transferência de 2018 aplicada ao 1º turno de 2022), para medir o erro do método.
_Avoid_: teste histórico

**Erro do backtest**:
A diferença, em pontos percentuais dos votos válidos do lado Lula, entre o que o backtest projetou e o resultado real do 2º turno de 2022. Vem de um único teste histórico e não é uma garantia estatística.
_Avoid_: margem de erro, intervalo de confiança, erro amostral

**Estabilidade da transferência**:
O quanto a transferência de votos de cada categoria do 1º turno se repete de uma eleição para a outra (2018 e 2022).
_Avoid_: robustez, consistência

**Virada**:
Um município em que o lado na frente no 1º turno de 2026 não é o mesmo que venceu no 2º turno de 2022, contando só os votos dos dois lados. Descreve o 1º turno; não é uma projeção.
_Avoid_: mudança de lado, troca, flip

**Referência externa**:
Uma estimativa da transferência de votos feita por outro método (uma pesquisa amostral publicada), usada só para comparar com o método deste projeto.
_Avoid_: validação, gabarito, confronto
