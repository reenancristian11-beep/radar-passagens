"""Viagens já compradas: avisa quando vale a pena fazer upgrade de classe
(a classe de cima ficou perto do que você pagou) ou quando a sua própria
passagem ficou mais barata (dá para remarcar ou pedir crédito)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

ORDEM = ["economica", "premium", "executiva", "primeira"]
GOOGLE_CLASSE = {"economica": "economy", "premium": "premium economy",
                 "executiva": "business class", "primeira": "first class"}


def _data(valor) -> str:
    return valor.isoformat() if isinstance(valor, date) else str(valor)


def viagens_validas(cfg: dict) -> list[dict]:
    """Lê ``viagens_compradas`` do config, ignorando viagens já passadas."""
    hoje = date.today().isoformat()
    saida = []
    for i, v in enumerate(cfg.get("viagens_compradas") or []):
        faltando = [c for c in ("origem", "destino", "ida", "classe_comprada", "preco_pago") if not v.get(c)]
        if faltando:
            print(f"[upgrades] Viagem {i + 1} ignorada: falta {', '.join(faltando)}.")
            continue
        if v["classe_comprada"] not in ORDEM:
            print(f"[upgrades] Viagem {i + 1}: classe '{v['classe_comprada']}' inválida "
                  f"(use {', '.join(ORDEM)}).")
            continue
        viagem = dict(v)
        viagem["origem"] = str(v["origem"]).upper()
        viagem["destino"] = str(v["destino"]).upper()
        viagem["ida"] = _data(v["ida"])
        viagem["volta"] = _data(v["volta"]) if v.get("volta") else None
        viagem["nome"] = v.get("nome") or f"{viagem['origem']}→{viagem['destino']} {viagem['ida']}"
        if viagem["ida"] < hoje:
            continue
        saida.append(viagem)
    return saida


def link_google(viagem: dict, classe: str, moeda: str = "BRL") -> str:
    consulta = f"Flights to {viagem['destino']} from {viagem['origem']} on {viagem['ida']}"
    if viagem.get("volta"):
        consulta += f" through {viagem['volta']}"
    consulta += f" {GOOGLE_CLASSE[classe]}"
    return f"https://www.google.com/travel/flights?q={quote(consulta)}&curr={moeda}&hl=pt-BR&gl=BR"


def verificar(cfg: dict, situacao: dict, alertados: dict, buscar, agora: datetime | None = None,
              forcar: bool = False) -> tuple[list[dict], int]:
    """Confere as viagens compradas que estão na hora de conferir.

    ``situacao`` (data/viagens.json) é atualizado no lugar. Retorna os avisos
    novos e quantas consultas foram feitas.
    """
    agora = agora or datetime.now(timezone.utc)
    regras = cfg.get("upgrades") or {}
    intervalo = timedelta(hours=float(regras.get("verificar_a_cada_horas", 6)))
    queda_pct = float(regras.get("avisar_se_minha_passagem_cair_pct", 10))
    classes_cfg = cfg["classes"]
    avisos: list[dict] = []
    consultas = 0

    viagens = viagens_validas(cfg)
    nomes = {v["nome"] for v in viagens}
    for nome in list(situacao):  # some com viagens removidas/passadas
        if nome not in nomes:
            del situacao[nome]

    for v in viagens:
        estado = situacao.setdefault(v["nome"], {"classes": {}})
        ultimo = estado.get("verificado")
        if not forcar and ultimo and agora - datetime.fromisoformat(ultimo) < intervalo:
            continue
        pago = float(v["preco_pago"])
        limite_upgrade = float(v.get("avisar_upgrade_ate") or regras.get("avisar_upgrade_ate_padrao") or 0)
        minha = ORDEM.index(v["classe_comprada"])
        algum_preco = False
        print(f"  ✈ Viagem comprada '{v['nome']}' ({v['origem']}→{v['destino']}, "
              f"{classes_cfg[v['classe_comprada']]['nome']}, pagou R$ {pago:.0f})")
        for classe in ORDEM[minha:]:
            consultas += 1
            try:
                r = buscar(v, classes_cfg[classe]["google"], cfg)
            except Exception as erro:
                print(f"    ! {classe}: erro ({type(erro).__name__}: {erro})")
                continue
            if not r:
                print(f"    - {classes_cfg[classe]['nome']}: sem preço")
                estado["classes"].pop(classe, None)
                continue
            algum_preco = True
            diferenca = r["preco"] - pago
            info = {
                "preco": round(r["preco"], 2),
                "diferenca": round(diferenca, 2),
                "mesmos_voos": r["mesmos_voos"],
                "voos_ida": r["voos_ida"],
                "voos_volta": r["voos_volta"],
                "google": link_google(v, classe, r["moeda"]),
            }
            estado["classes"][classe] = info
            print(f"    · {classes_cfg[classe]['nome']}: R$ {r['preco']:.0f} ({diferenca:+.0f})")

            aviso = None
            if classe == v["classe_comprada"]:
                if diferenca < 0 and -diferenca / pago * 100 >= queda_pct:
                    aviso = "queda"
            elif limite_upgrade and diferenca <= limite_upgrade:
                aviso = "upgrade"
            if aviso:
                chave = f"viagem:{v['nome']}:{classe}"
                anterior = alertados.get(chave)
                repetido = anterior and anterior["preco"] <= r["preco"] * 1.03 and \
                    agora - datetime.fromisoformat(anterior["quando"]) < timedelta(days=7)
                if not repetido:
                    avisos.append({
                        "tipo": aviso, "chave": chave, "preco": info["preco"],
                        "viagem": v["nome"], "origem": v["origem"], "destino": v["destino"],
                        "ida": v["ida"], "volta": v["volta"], "pago": pago,
                        "classe_comprada": classes_cfg[v["classe_comprada"]]["nome"],
                        "classe": classes_cfg[classe]["nome"], **info,
                    })
        if algum_preco:  # se o Google não respondeu, tenta de novo na próxima rodada
            estado["verificado"] = agora.isoformat(timespec="minutes")
        estado["pago"] = pago
        estado["classe_comprada"] = v["classe_comprada"]
        estado.update(origem=v["origem"], destino=v["destino"], ida=v["ida"], volta=v["volta"])
    return avisos, consultas
