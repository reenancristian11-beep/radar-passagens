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


def _numero(voo: str) -> str:
    """'LA 8190', 'la8190' ou '8190' → '8190' (só os dígitos, sem zeros à esquerda)."""
    digitos = "".join(c for c in str(voo) if c.isdigit())
    return digitos.lstrip("0") or digitos


def _codigo_voo(leg) -> str:
    cia = getattr(leg.airline, "name", str(leg.airline)).lstrip("_")
    return f"{cia}{leg.flight_number}"


def _escolher(opcoes, voos_desejados: list[str]):
    """Escolhe o itinerário com os voos informados; sem voos informados (ou se
    não achar), fica com o mais barato. Retorna (opção, achou_os_voos)."""
    if voos_desejados:
        alvo = {_numero(v) for v in voos_desejados}
        for op in opcoes:
            if alvo <= {_numero(leg.flight_number) for leg in op.legs}:
                return op, True
    com_preco = [op for op in opcoes if op.price]
    return (min(com_preco, key=lambda op: op.price) if com_preco else (opcoes[0] if opcoes else None)), False


def buscar_viagem(viagem: dict, classe_google: str, cfg: dict) -> dict | None:
    """Preço atual de uma viagem já comprada (mesmas datas e, se informado,
    mesmos voos) na classe pedida. Ida e volta: 2 consultas; só ida: 1."""
    from copy import deepcopy

    from fli.core import build_flight_segments
    from fli.models import Airport, FlightSearchFilters, MaxStops, PassengerInfo, SeatType
    from fli.search import SearchFlights

    segmentos, tipo = build_flight_segments(
        origin=Airport[viagem["origem"]],
        destination=Airport[viagem["destino"]],
        departure_date=str(viagem["ida"]),
        return_date=str(viagem["volta"]) if viagem.get("volta") else None,
    )
    filtros = FlightSearchFilters(
        trip_type=tipo,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=segmentos,
        stops=MaxStops.ANY,
        seat_type=SeatType[classe_google],
    )
    local = dict(currency=cfg.get("moeda", "BRL"), language=cfg.get("idioma", "pt-BR"),
                 country=cfg.get("pais", "BR"), capture_session=False)
    busca = SearchFlights()
    idas = busca._fetch_flights(filtros, **local)
    if not idas:
        return None
    ida, achou_ida = _escolher(idas, viagem.get("voos_ida") or [])
    if ida is None:
        return None
    volta, achou_volta = None, True
    if viagem.get("volta"):
        # Com a ida escolhida, o Google lista as voltas com o preço TOTAL.
        filtros_volta = deepcopy(filtros)
        filtros_volta.flight_segments[0].selected_flight = ida
        voltas = busca._fetch_flights(filtros_volta, **local)
        if not voltas:
            return None
        volta, achou_volta = _escolher(voltas, viagem.get("voos_volta") or [])
    final = volta or ida
    if not final.price:
        return None
    return {
        "preco": float(final.price),
        "moeda": final.currency or cfg.get("moeda", "BRL"),
        "voos_ida": [_codigo_voo(l) for l in ida.legs],
        "voos_volta": [_codigo_voo(l) for l in volta.legs] if volta else [],
        "mesmos_voos": bool(viagem.get("voos_ida") or viagem.get("voos_volta"))
        and (achou_ida or not viagem.get("voos_ida"))
        and (achou_volta or not viagem.get("voos_volta")),
    }
