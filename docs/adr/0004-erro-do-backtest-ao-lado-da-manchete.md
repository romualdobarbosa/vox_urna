# Erro do backtest ao lado da manchete, com sinal e sem corrigir a projeção

O backtest (a transferência de 2018 aplicada ao 1º turno de 2022) mede o erro do método base num caso análogo ao de 2026: um dos lados trocou de candidato (Haddad → Lula), como agora (Jair → Flávio). A manchete continua sendo a faixa entre os cenários. O erro do backtest aparece logo abaixo dela, com sinal ("em 2022, o mesmo método subestimou o lado Lula em X pp"), e a projeção de 2026 não recebe correção de viés. A regra foi fixada antes de olharmos o resultado do backtest, para não ser escolhida a dedo.

## Considered Options

- **Somar o erro à faixa dos cenários:** foi rejeitada porque um único ponto histórico não sustenta uma faixa com cara de intervalo estatístico.
- **Corrigir a projeção pelo viés de 2022:** foi rejeitada porque apostaria que o viés de uma única eleição se repete.
- **Deixar o erro só na seção de metodologia:** foi rejeitada porque esconde do leitor da manchete a única medida empírica de erro que temos.

O número se chama **erro do backtest**, nunca "margem de erro", porque esse termo é de pesquisa amostral e o projeto não é pesquisa (veja `GLOSSARY.md`).
