"""Executa uma rodada do radar.

    python -m radar                       # rodada normal (rodízio da lista)
    python -m radar --quantidade 5        # pesquisa só 5 rotas
    python -m radar --rota GRU-LIS-executiva --sem-alertas
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .alertas import enviar_alertas, testar_alertas
from . import upgrades
from .busca import buscar_calendario, buscar_viagem
from .config import RAIZ, carregar_config, montar_rotas
from .promocoes import avaliar, deve_alertar

DADOS = RAIZ / "data"
MAX_HISTORICO = 60


def ler_json(caminho: Path, padrao):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return padrao


def gravar_json(caminho: Path, dados) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(caminho)


def escolher_rotas(rotas, cursor: int, quantidade: int):
    """Rodízio: pega ``quantidade`` rotas a partir do cursor, dando a volta na lista."""
    if not rotas:
        return [], 0
    quantidade = min(quantidade, len(rotas))
    inicio = cursor % len(rotas)
    escolhidas = [rotas[(inicio + i) % len(rotas)] for i in range(quantidade)]
    return escolhidas, (inicio + quantidade) % len(rotas)


def gerar_site(cfg: dict, rotas, precos: dict, dados: Path, viagens: dict | None = None) -> None:
    """Grava data/site.json, que a página lê."""
    hoje = date.today().isoformat()
    validas = {r.chave for r in rotas}
    atuais = [
        v["atual"]
        for k, v in precos.items()
        if k in validas and v.get("atual") and v["atual"]["ida"] >= hoje
    ]
    # Atualiza a marca de prioridade (o usuário pode ter mudado a lista).
    prioridade = {r.chave: r.prioridade for r in rotas}
    for r in atuais:
        r["prioridade"] = prioridade.get(r["chave"], "")
    atuais.sort(key=lambda r: (not r["promocao"], not r["prioridade"], -r["desconto"]))
    gravar_json(
        dados / "site.json",
        {
            "gerado": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "origens": cfg["origens"],
            "classes": {k: v["nome"] for k, v in cfg["classes"].items()},
            "regioes": {k: v["nome"] for k, v in cfg["regioes"].items()},
            "total_rotas": len(rotas),
            "prioridades": sorted({f"{r.cidade} ({r.destino})" for r in rotas
                                   if r.prioridade == "escolhido por você"}),
            "rotas": atuais,
            "viagens": [
                {"nome": nome, **v} for nome, v in (viagens or {}).items() if v.get("verificado")
            ],
        },
    )


ROTA_SONDA = "GRU-MIA-economica"  # rota que sempre tem voos: serve de teste de bloqueio


def rota_sem_voos(registro: dict | None, hoje: date) -> bool:
    """Rotas que voltaram vazias várias vezes (ex.: primeira classe para um
    destino que não tem) só são tentadas de novo depois de uma semana."""
    if not registro or registro.get("vazias", 0) < 2:
        return False
    ultima = registro.get("ultima_vazia")
    return bool(ultima) and (hoje - date.fromisoformat(ultima)).days < 7


def escolher_uteis(fila, cursor: int, quantidade: int, pular):
    """Como ``escolher_rotas``, mas atravessa rotas que devem ser puladas sem
    contá-las. Retorna a lista percorrida como pares (rota, pular?)."""
    if not fila or quantidade <= 0:
        return []
    percorridas, uteis = [], 0
    for i in range(len(fila)):
        rota = fila[(cursor + i) % len(fila)]
        pula = pular(rota)
        percorridas.append((rota, pula))
        uteis += not pula
        if uteis >= quantidade:
            break
    return percorridas


def executar(args, dados: Path = DADOS, buscar=buscar_calendario, alertar=enviar_alertas,
             buscar_viagem_=None) -> dict:
    cfg = carregar_config()
    rotas = montar_rotas(cfg)
    precos = ler_json(dados / "precos.json", {})
    alertados = ler_json(dados / "alertas.json", {})
    estado = ler_json(dados / "estado.json", {"cursor": 0})
    viagens = ler_json(dados / "viagens.json", {})
    hoje = date.today()
    pausa = float(cfg["varredura"]["pausa_entre_buscas_segundos"])

    # 1) Viagens já compradas (poucas consultas, por isso vêm antes de tudo).
    avisos_viagens: list[dict] = []
    if not args.rota:
        avisos_viagens, consultas = upgrades.verificar(
            cfg, viagens, alertados, buscar_viagem_ or buscar_viagem)
        if consultas:
            time.sleep(pausa)

    # 2) Duas filas em rodízio: destinos prioritários e o resto. Os prioritários
    # vêm primeiro em cada rodada, então são pesquisados mesmo se o Google
    # limitar as consultas no meio da rodada.
    filas = {
        "prioridade": [r for r in rotas if r.prioridade],
        "normal": [r for r in rotas if not r.prioridade],
    }
    cursores = {
        "prioridade": estado.get("cursor_prioridade", 0),
        "normal": estado.get("cursor", 0),
    }
    pular = lambda r: rota_sem_voos(precos.get(r.chave), hoje)  # noqa: E731
    if args.rota:
        escolhidas = [(r, None, False) for r in rotas if r.chave.lower() == args.rota.lower()]
        if not escolhidas:
            raise SystemExit(f"Rota {args.rota} não existe no config.yaml")
    else:
        qtd = args.quantidade or int(cfg["varredura"]["combinacoes_por_execucao"])
        pct = float((cfg.get("prioridades") or {}).get("parte_da_rodada_pct") or 0)
        uteis_pri = sum(not pular(r) for r in filas["prioridade"])
        qtd_pri = min(uteis_pri, round(qtd * pct / 100))
        pri = escolher_uteis(filas["prioridade"], cursores["prioridade"], qtd_pri, pular)
        nor = escolher_uteis(filas["normal"], cursores["normal"], qtd - qtd_pri, pular)
        escolhidas = [(r, "prioridade", p) for r, p in pri] + [(r, "normal", p) for r, p in nor]
    consumidas = [fila for _, fila, _ in escolhidas]  # o que avança o rodízio

    limite_falhas = int(cfg["varredura"]["parar_apos_falhas_seguidas"])
    sonda = next((r for r in rotas if r.chave == ROTA_SONDA), None)
    sondas_restantes = 2
    falhas_seguidas = 0
    inicio_falhas = 0
    vazias_pendentes: list[str] = []  # só viram "rota sem voos" se o Google estiver respondendo
    pesquisadas = 0
    puladas = sum(p for _, _, p in escolhidas)
    novas_promocoes: list[dict] = []

    def confirmar_vazias():
        for chave in vazias_pendentes:
            reg = precos.setdefault(chave, {"historico": [], "atual": None})
            reg["vazias"] = reg.get("vazias", 0) + 1
            reg["ultima_vazia"] = hoje.isoformat()
        vazias_pendentes.clear()

    print(f"Radar: {len(rotas)} rotas no total ({len(filas['prioridade'])} prioritárias); "
          f"pesquisando {len(escolhidas) - puladas} nesta rodada"
          + (f" ({puladas} puladas por não terem voos)" if puladas else "") + ".")
    for i, (rota, _fila, pula) in enumerate(escolhidas):
        if pula:
            continue
        try:
            calendario = buscar(rota, cfg)
            erro = None if calendario else "resposta vazia"
        except Exception as e:
            calendario, erro = [], f"{type(e).__name__}: {e}"

        if erro:
            # Quando o Google limita as consultas, ele devolve resposta vazia em
            # vez de erro; mas algumas rotas simplesmente não têm voos naquela
            # classe. Depois de várias vazias seguidas, uma rota de teste decide.
            if falhas_seguidas == 0:
                inicio_falhas = i
            falhas_seguidas += 1
            vazias_pendentes.append(rota.chave)
            print(f"  ! {rota.chave}: sem resultado ({erro})")
            if falhas_seguidas >= limite_falhas:
                bloqueado = True
                if sonda and sondas_restantes and not args.rota:
                    sondas_restantes -= 1
                    time.sleep(pausa)
                    try:
                        bloqueado = not buscar(sonda, cfg)
                    except Exception:
                        bloqueado = True
                if not bloqueado:
                    print("  ✓ A rota de teste respondeu: essas rotas não têm voos. Continuando.")
                    confirmar_vazias()
                    falhas_seguidas = 0
                    continue
                print("  ! O Google limitou as consultas (a rota de teste também veio vazia). "
                      "Parando; a próxima rodada continua daqui.")
                vazias_pendentes.clear()
                # Se a rodada já começou falhando, pula esse trecho para não
                # ficar preso nele.
                consumidas = consumidas[: inicio_falhas if inicio_falhas > 0 else i + 1]
                break
            continue
        confirmar_vazias()  # o Google está respondendo: as vazias anteriores eram reais
        falhas_seguidas = 0
        pesquisadas += 1

        registro = precos.setdefault(rota.chave, {"historico": [], "atual": None})
        registro.pop("vazias", None)
        registro.pop("ultima_vazia", None)
        resultado = avaliar(rota, calendario, registro["historico"], cfg)
        if resultado is None:
            print(f"  - {rota.chave}: sem preços")
        else:
            registro["atual"] = resultado
            registro["historico"].append([resultado["atualizado"], resultado["preco"]])
            registro["historico"] = registro["historico"][-MAX_HISTORICO:]
            marca = ("PROMOÇÃO" if resultado["promocao"] else "") + (" ⭐" if rota.prioridade else "")
            print(f"  · {rota.chave}: R$ {resultado['preco']:.0f} em {resultado['ida']} {marca}")
            if deve_alertar(resultado, alertados.get(rota.chave), cfg):
                novas_promocoes.append(resultado)
        if i < len(escolhidas) - 1:
            time.sleep(pausa)

    # Alertas: avisos das viagens compradas primeiro; depois as promoções
    # (prioritárias e maiores descontos primeiro), com limite por rodada.
    novas_promocoes.sort(key=lambda r: (not r.get("prioridade"), -r["desconto"]))
    novas_promocoes = novas_promocoes[: int(cfg["promocao"]["maximo_alertas_por_execucao"])]
    avisos = avisos_viagens + novas_promocoes
    canais: list[str] = []
    if avisos and not args.sem_alertas:
        canais = alertar(avisos, os.environ.get("SITE_URL"))
        # Só marca como avisada se algum canal (Telegram/e-mail) realmente enviou.
        if canais:
            agora = datetime.now(timezone.utc).isoformat(timespec="minutes")
            for r in avisos:
                alertados[r["chave"]] = {"preco": r["preco"], "quando": agora}

    if not args.rota:
        for nome, fila in filas.items():
            if fila:
                cursores[nome] = (cursores[nome] + consumidas.count(nome)) % len(fila)
    estado.update(
        cursor=cursores["normal"],
        cursor_prioridade=cursores["prioridade"],
        ultima_execucao=datetime.now(timezone.utc).isoformat(timespec="minutes"),
        ultima_pesquisadas=pesquisadas,
    )
    gravar_json(dados / "precos.json", precos)
    gravar_json(dados / "alertas.json", alertados)
    gravar_json(dados / "estado.json", estado)
    gravar_json(dados / "viagens.json", viagens)
    gerar_site(cfg, rotas, precos, dados, viagens)

    print(
        f"Fim: {pesquisadas} rotas pesquisadas, {len(novas_promocoes)} promoções novas"
        + (f", {len(avisos_viagens)} avisos de viagens compradas" if avisos_viagens else "")
        + (f", alertas por {', '.join(canais)}" if canais else "")
    )
    return {"pesquisadas": pesquisadas, "promocoes": novas_promocoes,
            "avisos_viagens": avisos_viagens, "canais": canais}


def main() -> None:
    p = argparse.ArgumentParser(description="Radar de promoções de passagens aéreas")
    p.add_argument("--quantidade", type=int, help="quantas rotas pesquisar nesta rodada")
    p.add_argument("--rota", help="pesquisar só uma rota, ex.: GRU-LIS-executiva")
    p.add_argument("--sem-alertas", action="store_true", help="não envia Telegram/e-mail")
    p.add_argument("--testar-alerta", action="store_true", help="só manda uma mensagem de teste")
    args = p.parse_args()
    if args.testar_alerta:
        site = ler_json(DADOS / "site.json", {})
        promos = [r for r in site.get("rotas", []) if r.get("promocao")]
        canais = testar_alertas(os.environ.get("SITE_URL"), promos[0] if promos else None)
        print(f"Mensagem de teste enviada por: {', '.join(canais)}")
        return
    resultado = executar(args)
    if resultado["pesquisadas"] == 0 and not resultado["avisos_viagens"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
