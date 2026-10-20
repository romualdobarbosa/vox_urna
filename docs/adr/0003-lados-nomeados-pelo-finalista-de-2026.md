# As categorias dos finalistas são "lados" com o nome do finalista de 2026

O modelo precisa de uma chave estável para ligar os finalistas de eleições diferentes, mas nem o partido nem a pessoa se mantêm. Em 2018 o finalista era Jair Bolsonaro pelo PSL, em 2022 Jair pelo PL e em 2026 Flávio pelo PL. Do outro lado, Haddad em 2018 e Lula depois. Por isso a chave é o **lado**, que leva o nome do finalista de 2026 (`lula`, `flavio`), porque o objeto do estudo é o 2º turno de 2026. Uma config explícita diz qual candidato real ocupa cada lado em cada eleição. Na tela aparecem sempre o nome e o partido reais daquela eleição, com vermelho para o lado do PT e azul para o outro.

Opções descartadas: chave por partido (quebra em PSL → PL) e papel anônimo por ranking no 1º turno (rejeitado; o projeto usa os nomes reais, que são fatos públicos).
