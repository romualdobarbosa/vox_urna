# β estimado por zona e ligado a 2026 em três passos

Zona e município não se encaixam um no outro: uma zona pode cobrir vários municípios, e um município pode estar em várias zonas. Por isso a estimação e a ligação entre eleições são tratadas separadamente.

**Estimação:** o β é estimado por zona inteira (UF, zona), usando todas as suas seções como observações. Uma zona com menos seções que um mínimo configurável (10, de início) usa o β da UF. Estimar por (município, zona) deixaria pequenos municípios com poucas seções e β instável.

**Ligação a 2026:** cada seção de 2026 recebe o β assim: (1) o da zona de 2022 com o mesmo par (município, zona); se o par não existia, (2) a média, ponderada pelo eleitorado, dos β das zonas que continham o município em 2022; e se ainda não houver, (3) o da UF. Ligar só por (UF, zona) erraria em silêncio quando o rezoneamento redistribui municípios e mantém o número da zona. A fração do eleitorado de 2026 ligada em cada passo é reportada como taxa de match. O exterior é uma zona como outra qualquer.
