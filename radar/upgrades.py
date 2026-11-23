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
        faltando = [c for c in ("origem", "destino", "ida", "classe_comprada") if not v.get(c)]
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
              forcar: bool = False, esperar=lambda: None) -> tuple[list[dict], int]:
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
        pago = float(v["preco_pago"]) if v.get("preco_pago") else None
        limite_upgrade = float(v.get("avisar_upgrade_ate") or regras.get("avisar_upgrade_ate_padrao") or 0)
        minha = ORDEM.index(v["classe_comprada"])
        nome_minha = classes_cfg[v["classe_comprada"]]["nome"]
        print(f"  ✈ Viagem comprada '{v['nome']}' ({v['origem']}→{v['destino']}, {nome_minha}"
              + (f", pagou R$ {pago:.0f})" if pago else ", valor pago não informado)"))

        # 1) Preço de hoje da sua classe e das classes acima, na mesma viagem.
        achados: dict[str, dict] = {}
        for classe in ORDEM[minha:]:
            r = None
            for tentativa in range(2):  # resposta vazia às vezes é o Google limitando: tenta 2x
                if consultas:
                    esperar()
                consultas += 1
                try:
                    r = buscar(v, classes_cfg[classe]["google"], cfg)
                except Exception as erro:
                    print(f"    ! {classe}: erro ({type(erro).__name__}: {erro})")
                if r:
                    break
            if r:
                achados[classe] = r
            else:
                print(f"    - {classes_cfg[classe]['nome']}: sem preço")
        if v["classe_comprada"] not in achados:
            # Sem o preço da sua classe não há referência: tenta de novo na
            # próxima rodada (sem esperar o intervalo de horas).
            print("    ! sem o preço da sua classe; confiro de novo na próxima rodada")
            continue

        # 2) Referência: o que você pagou; sem isso, o preço de hoje da sua classe
        # (o upgrade estimado é a diferença de tarifa entre as classes hoje).
        hoje_minha = achados.get(v["classe_comprada"], {}).get("preco")
        referencia = pago or hoje_minha
        estado["classes"] = {}
        menores = estado.setdefault("menor_diferenca", {})
        for classe, r in achados.items():
            diferenca = r["preco"] - referencia if referencia else None
            info = {
                "preco": round(r["preco"], 2),
                "diferenca": round(diferenca, 2) if diferenca is not None else None,
                "mesmos_voos": r["mesmos_voos"],
                "voos_ida": r["voos_ida"],
                "voos_volta": r["voos_volta"],
                "google": link_google(v, classe, r["moeda"]),
            }
            estado["classes"][classe] = info
            print(f"    · {classes_cfg[classe]['nome']}: R$ {r['preco']:.0f}"
                  + (f" ({diferenca:+.0f})" if diferenca is not None else ""))

            aviso = None
            if classe == v["classe_comprada"]:
                if pago and diferenca < 0 and -diferenca / pago * 100 >= queda_pct:
                    aviso = "queda"
            elif diferenca is not None:
                anterior_menor = menores.get(classe)
                if pago and limite_upgrade:
                    aviso = "upgrade" if diferenca <= limite_upgrade else None
                elif anterior_menor is not None and diferenca <= anterior_menor * 0.9:
                    aviso = "upgrade"  # sem limite: avisa quando fica 10% mais barato que o menor já visto
                if anterior_menor is None or diferenca < anterior_menor:
                    menores[classe] = round(diferenca, 2)
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
                        "referencia": referencia, "tarifa": v.get("tarifa"),
                        "classe_comprada": nome_minha,
                        "classe": classes_cfg[classe]["nome"], **info,
                    })

        # 3) Na primeira conferência, manda um resumo com os valores de todas as classes.
        chave_resumo = f"viagem:{v['nome']}:resumo"
        if chave_resumo not in alertados and len(achados) > 1:
            avisos.append({
                "tipo": "resumo", "chave": chave_resumo, "preco": referencia or 0,
                "viagem": v["nome"], "origem": v["origem"], "destino": v["destino"],
                "ida": v["ida"], "volta": v["volta"], "pago": pago, "referencia": referencia,
                "tarifa": v.get("tarifa"), "classe_comprada": nome_minha,
                "classes": [
                    {"classe": classes_cfg[c]["nome"], "minha": c == v["classe_comprada"], **estado["classes"][c]}
                    for c in ORDEM if c in estado["classes"]
                ],
            })

        estado["verificado"] = agora.isoformat(timespec="minutes")
        estado["pago"] = pago
        estado["referencia"] = referencia
        estado["tarifa"] = v.get("tarifa")
        estado["classe_comprada"] = v["classe_comprada"]
        estado.update(origem=v["origem"], destino=v["destino"], ida=v["ida"], volta=v["volta"])
    return avisos, consultas
