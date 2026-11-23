"""Decide se o melhor preço encontrado para uma rota é uma promoção."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from urllib.parse import quote

from .busca import PrecoData
from .config import Rota

SKYSCANNER_CLASSE = {
    "economica": "economy",
    "premium": "premiumeconomy",
    "executiva": "business",
    "primeira": "first",
}
GOOGLE_CLASSE = {
    "economica": "economy",
    "premium": "premium economy",
    "executiva": "business class",
    "primeira": "first class",
}


def link_google(rota: Rota, ida: str, volta: str, moeda: str = "BRL") -> str:
    consulta = (
        f"Flights to {rota.destino} from {rota.origem} on {ida} "
        f"through {volta} {GOOGLE_CLASSE[rota.classe]}"
    )
    return (
        "https://www.google.com/travel/flights?q="
        + quote(consulta)
        + f"&curr={moeda}&hl=pt-BR&gl=BR"
    )


def link_skyscanner(rota: Rota, ida: str, volta: str) -> str:
    def curta(d: str) -> str:  # 2026-12-10 -> 261210
        return d[2:4] + d[5:7] + d[8:10]

    return (
        "https://www.skyscanner.com.br/transporte/passagens-aereas/"
        f"{rota.origem.lower()}/{rota.destino.lower()}/{curta(ida)}/{curta(volta)}/"
        f"?adultsv2=1&cabinclass={SKYSCANNER_CLASSE[rota.classe]}&rtn=1"
    )


def avaliar(rota: Rota, calendario: list[PrecoData], historico: list[list], cfg: dict) -> dict | None:
    """Analisa o calendário de preços de uma rota.

    ``historico`` é a lista [[data_iso, menor_preco], ...] das varreduras
    anteriores (sem a atual). Retorna um resumo com o melhor preço e os
    motivos pelos quais ele é (ou não) promoção. ``None`` se não há preços.
    """
    if not calendario:
        return None
    regras = cfg["promocao"]
    melhor = min(calendario, key=lambda p: p.preco)
    precos = [p.preco for p in calendario]
    mediana_calendario = median(precos)

    precos_hist = [h[1] for h in historico]
    mediana_hist = (
        median(precos_hist)
        if len(precos_hist) >= int(regras["amostras_minimas_historico"])
        else None
    )

    motivos: list[str] = []
    if regras.get("usar_limite_fixo", True) and rota.limite and melhor.preco <= rota.limite:
        motivos.append(f"abaixo do preço-alvo de R$ {rota.limite:,.0f}".replace(",", "."))

    desconto_hist = None
    if mediana_hist:
        desconto_hist = 1 - melhor.preco / mediana_hist
        if desconto_hist * 100 >= float(regras["queda_vs_historico_pct"]):
            motivos.append(f"{desconto_hist:.0%} abaixo do preço habitual desta rota")

    desconto_cal = 1 - melhor.preco / mediana_calendario if mediana_calendario else 0
    if desconto_cal * 100 >= float(regras["desconto_vs_calendario_pct"]):
        motivos.append(f"{desconto_cal:.0%} abaixo da média das outras datas")

    referencia = mediana_hist or mediana_calendario
    return {
        "chave": rota.chave,
        "origem": rota.origem,
        "destino": rota.destino,
        "cidade": rota.cidade,
        "regiao": rota.regiao,
        "regiao_nome": rota.regiao_nome,
        "classe": rota.classe,
        "classe_nome": rota.classe_nome,
        "preco": round(melhor.preco, 2),
        "moeda": melhor.moeda,
        "ida": melhor.ida,
        "volta": melhor.volta,
        "referencia": round(referencia, 2),
        "desconto": round(1 - melhor.preco / referencia, 4) if referencia else 0,
        "limite": rota.limite,
        "datas_baratas": [
            {"ida": p.ida, "volta": p.volta, "preco": round(p.preco, 2)}
            for p in sorted(calendario, key=lambda p: p.preco)[:5]
        ],
        "promocao": bool(motivos),
        "motivos": motivos,
        "google": link_google(rota, melhor.ida, melhor.volta, melhor.moeda),
        "skyscanner": link_skyscanner(rota, melhor.ida, melhor.volta),
        "atualizado": datetime.now(timezone.utc).isoformat(timespec="minutes"),
    }


def deve_alertar(resultado: dict, ja_alertado: dict | None, cfg: dict, agora: datetime | None = None) -> bool:
    """Evita mandar o mesmo alerta várias vezes."""
    if not resultado or not resultado["promocao"]:
        return False
    if not ja_alertado:
        return True
    regras = cfg["promocao"]
    agora = agora or datetime.now(timezone.utc)
    quando = datetime.fromisoformat(ja_alertado["quando"])
    if agora - quando >= timedelta(days=int(regras["realertar_apos_dias"])):
        return True
    queda = 1 - resultado["preco"] / ja_alertado["preco"]
    return queda * 100 >= float(regras["realertar_se_cair_mais_pct"])
