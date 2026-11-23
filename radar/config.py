"""Leitura do config.yaml e montagem da lista de rotas a pesquisar."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_CONFIG = RAIZ / "config.yaml"


@dataclass(frozen=True)
class Rota:
    """Uma combinação origem → destino → classe que o radar acompanha."""

    origem: str
    destino: str
    cidade: str
    regiao: str
    regiao_nome: str
    classe: str          # chave interna: economica, premium, executiva, primeira
    classe_nome: str     # nome exibido: "Executiva"
    classe_google: str   # nome no Google Flights: BUSINESS
    duracao_dias: int
    limite: float | None  # preço de referência fixo (R$ ida e volta)

    @property
    def chave(self) -> str:
        return f"{self.origem}-{self.destino}-{self.classe}"


def carregar_config(caminho: Path = ARQUIVO_CONFIG) -> dict:
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f)


def montar_rotas(cfg: dict) -> list[Rota]:
    """Gera todas as combinações origem × destino × classe definidas no config."""
    classes = cfg["classes"]
    rotas: list[Rota] = []
    for origem in cfg["origens"]:
        for regiao, destinos in cfg["destinos"].items():
            reg = cfg["regioes"][regiao]
            for destino, cidade in destinos.items():
                if destino == origem:
                    continue
                for classe, limite in reg["classes"].items():
                    rotas.append(
                        Rota(
                            origem=origem,
                            destino=destino,
                            cidade=cidade,
                            regiao=regiao,
                            regiao_nome=reg["nome"],
                            classe=classe,
                            classe_nome=classes[classe]["nome"],
                            classe_google=classes[classe]["google"],
                            duracao_dias=int(reg.get("duracao_dias", 7)),
                            limite=float(limite) if limite else None,
                        )
                    )
    return rotas
