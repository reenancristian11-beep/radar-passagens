"""Destinos prioritários: os escolhidos pelo usuário e os que ficam perto deles."""

from __future__ import annotations

import math
import unicodedata
from functools import lru_cache


@lru_cache(maxsize=1)
def _aeroportos() -> dict:
    """Base gratuita de aeroportos (código IATA → cidade, país, coordenadas)."""
    try:
        import airportsdata

        return airportsdata.load("IATA")
    except ImportError:  # sem a base, só não calcula "próximos"
        return {}


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return sem_acento.strip().lower()


def distancia_km(a: str, b: str) -> float | None:
    base = _aeroportos()
    if a not in base or b not in base:
        return None
    lat1, lon1 = math.radians(base[a]["lat"]), math.radians(base[a]["lon"])
    lat2, lon2 = math.radians(base[b]["lat"]), math.radians(base[b]["lon"])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(h))


def _aeroporto_existe_no_google(codigo: str) -> bool:
    try:
        from fli.models import Airport
    except ImportError:
        return True
    return codigo in Airport.__members__


def resolver(cfg: dict) -> tuple[dict[str, str], dict[str, dict]]:
    """Lê ``prioridades`` do config.

    Retorna:
      - ``motivos``: {código do destino: motivo da prioridade}
      - ``extras``: destinos prioritários que não estavam na lista,
        {código: {"cidade": ..., "regiao": ...}}
    """
    pri = cfg.get("prioridades") or {}
    pedidos = pri.get("destinos") or []
    if not pedidos:
        return {}, {}

    # Destinos já configurados: código → (cidade, região)
    lista = {
        cod: (cidade, regiao)
        for regiao, destinos in cfg["destinos"].items()
        for cod, cidade in destinos.items()
    }
    por_nome = {_normalizar(cidade): cod for cod, (cidade, _) in lista.items()}

    motivos: dict[str, str] = {}
    extras: dict[str, dict] = {}
    for pedido in pedidos:
        texto = str(pedido).strip()
        codigo = texto.upper() if len(texto) == 3 and texto.isalpha() else None
        if codigo not in lista and _normalizar(texto) in por_nome:
            codigo = por_nome[_normalizar(texto)]
        if not codigo:
            print(f"[prioridades] Não reconheci '{texto}'. Use o código do aeroporto "
                  f"(ex.: LIS) ou o nome da cidade como está na lista de destinos.")
            continue
        if codigo not in lista:
            if not _aeroporto_existe_no_google(codigo) or codigo not in _aeroportos():
                print(f"[prioridades] Aeroporto '{codigo}' desconhecido; ignorado.")
                continue
            # Fica na região do destino configurado mais próximo (para usar os
            # mesmos preços-alvo, classes e duração de viagem).
            perto = min(
                (d for d in lista if distancia_km(codigo, d) is not None),
                key=lambda d: distancia_km(codigo, d),
                default=None,
            )
            if perto is None:
                print(f"[prioridades] Sem coordenadas para '{codigo}'; ignorado.")
                continue
            cidade = _aeroportos()[codigo]["city"] or codigo
            extras[codigo] = {"cidade": cidade, "regiao": lista[perto][1]}
            lista[codigo] = (cidade, lista[perto][1])
        motivos[codigo] = "escolhido por você"

    raio = float(pri.get("raio_proximos_km") or 0)
    if raio > 0:
        for escolhido in list(motivos):
            for cod, (cidade, _) in lista.items():
                if cod in motivos:
                    continue
                d = distancia_km(escolhido, cod)
                if d is not None and d <= raio:
                    motivos[cod] = f"perto de {lista[escolhido][0]} ({d:.0f} km)"
    return motivos, extras
