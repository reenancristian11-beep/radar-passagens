"""Consulta ao Google Flights através da biblioteca fli (pacote "flights").

Usa a visão de calendário do Google Flights: uma única consulta devolve o
preço mais barato de ida e volta para cada data de partida da janela.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .config import Rota


@dataclass
class PrecoData:
    ida: str      # AAAA-MM-DD
    volta: str    # AAAA-MM-DD
    preco: float
    moeda: str


def buscar_calendario(rota: Rota, cfg: dict) -> list[PrecoData]:
    """Retorna o preço mais barato por data de partida para a rota."""
    # Importação tardia: os testes rodam sem a biblioteca instalada.
    from fli.core import build_date_search_segments
    from fli.models import Airport, DateSearchFilters, MaxStops, PassengerInfo, SeatType
    from fli.search import SearchDates

    janela = cfg["datas"]
    inicio = date.today() + timedelta(days=int(janela["comecar_em_dias"]))
    # O Google aceita no máximo ~305 dias à frente, contando a volta.
    horizonte = min(int(janela["horizonte_dias"]), 300 - rota.duracao_dias)
    fim = inicio + timedelta(days=horizonte)

    segmentos, tipo = build_date_search_segments(
        origin=Airport[rota.origem],
        destination=Airport[rota.destino],
        start_date=inicio.isoformat(),
        trip_duration=rota.duracao_dias,
        is_round_trip=True,
    )
    filtros = DateSearchFilters(
        trip_type=tipo,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=segmentos,
        stops=MaxStops.ANY,
        seat_type=SeatType[rota.classe_google],
        from_date=inicio.isoformat(),
        to_date=fim.isoformat(),
        duration=rota.duracao_dias,
    )
    resultados = SearchDates().search(
        filtros,
        currency=cfg.get("moeda", "BRL"),
        language=cfg.get("idioma", "pt-BR"),
        country=cfg.get("pais", "BR"),
    )
    saida: list[PrecoData] = []
    for r in resultados or []:
        if not r.price or r.price <= 0:
            continue
        ida = r.date[0].date()
        volta = r.date[1].date() if len(r.date) > 1 else ida + timedelta(days=rota.duracao_dias)
        saida.append(
            PrecoData(
                ida=ida.isoformat(),
                volta=volta.isoformat(),
                preco=float(r.price),
                moeda=r.currency or cfg.get("moeda", "BRL"),
            )
        )
    return saida
