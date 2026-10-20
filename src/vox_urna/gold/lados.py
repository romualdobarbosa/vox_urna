from dataclasses import dataclass


@dataclass(frozen=True)
class Finalista:
    nr_votavel: int
    nm_votavel: str
    sg_partido: str


# Quem ocupa cada lado em cada eleição (ADR 0003). Os nomes são os do BU, para conferência.
LADOS: dict[int, dict[str, Finalista]] = {
    2018: {
        "lula": Finalista(13, "FERNANDO HADDAD", "PT"),
        "flavio": Finalista(17, "JAIR BOLSONARO", "PSL"),
    },
    2022: {
        "lula": Finalista(13, "LULA", "PT"),
        "flavio": Finalista(22, "JAIR BOLSONARO", "PL"),
    },
    2026: {
        "lula": Finalista(13, "LULA", "PT"),
        "flavio": Finalista(22, "FLAVIO BOLSONARO", "PL"),
    },
}
