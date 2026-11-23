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

from .alertas import enviar_alertas
from .busca import buscar_calendario
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


def gerar_site(cfg: dict, rotas, precos: dict, dados: Path) -> None:
    """Grava data/site.json, que a página lê."""
    hoje = date.today().isoformat()
    validas = {r.chave for r in rotas}
    atuais = [
        v["atual"]
        for k, v in precos.items()
        if k in validas and v.get("atual") and v["atual"]["ida"] >= hoje
    ]
    atuais.sort(key=lambda r: (not r["promocao"], -r["desconto"]))
    gravar_json(
        dados / "site.json",
        {
            "gerado": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "origens": cfg["origens"],
            "classes": {k: v["nome"] for k, v in cfg["classes"].items()},
            "regioes": {k: v["nome"] for k, v in cfg["regioes"].items()},
            "total_rotas": len(rotas),
            "rotas": atuais,
        },
    )


def executar(args, dados: Path = DADOS, buscar=buscar_calendario, alertar=enviar_alertas) -> dict:
    cfg = carregar_config()
    rotas = montar_rotas(cfg)
    precos = ler_json(dados / "precos.json", {})
    alertados = ler_json(dados / "alertas.json", {})
    estado = ler_json(dados / "estado.json", {"cursor": 0})

    if args.rota:
        escolhidas = [r for r in rotas if r.chave.lower() == args.rota.lower()]
        if not escolhidas:
            raise SystemExit(f"Rota {args.rota} não existe no config.yaml")
        novo_cursor = estado["cursor"]
    else:
        qtd = args.quantidade or int(cfg["varredura"]["combinacoes_por_execucao"])
        escolhidas, novo_cursor = escolher_rotas(rotas, estado.get("cursor", 0), qtd)

    pausa = float(cfg["varredura"]["pausa_entre_buscas_segundos"])
    limite_falhas = int(cfg["varredura"]["parar_apos_falhas_seguidas"])
    falhas_seguidas = 0
    pesquisadas = 0
    novas_promocoes: list[dict] = []

    print(f"Radar: {len(rotas)} rotas no total; pesquisando {len(escolhidas)} nesta rodada.")
    for i, rota in enumerate(escolhidas):
        try:
            calendario = buscar(rota, cfg)
            falhas_seguidas = 0
        except Exception as erro:
            falhas_seguidas += 1
            print(f"  ! {rota.chave}: erro na busca ({type(erro).__name__}: {erro})")
            if falhas_seguidas >= limite_falhas:
                print("  ! Muitas falhas seguidas (possível bloqueio). Parando esta rodada.")
                # Retoma desta rota na próxima execução.
                if not args.rota:
                    novo_cursor = (estado.get("cursor", 0) + i - falhas_seguidas + 1) % len(rotas)
                break
            continue
        pesquisadas += 1

        registro = precos.setdefault(rota.chave, {"historico": [], "atual": None})
        resultado = avaliar(rota, calendario, registro["historico"], cfg)
        if resultado is None:
            print(f"  - {rota.chave}: sem preços")
            registro["atual"] = None
        else:
            registro["atual"] = resultado
            registro["historico"].append([resultado["atualizado"], resultado["preco"]])
            registro["historico"] = registro["historico"][-MAX_HISTORICO:]
            marca = "PROMOÇÃO" if resultado["promocao"] else ""
            print(f"  · {rota.chave}: R$ {resultado['preco']:.0f} em {resultado['ida']} {marca}")
            if deve_alertar(resultado, alertados.get(rota.chave), cfg):
                novas_promocoes.append(resultado)
        if i < len(escolhidas) - 1:
            time.sleep(pausa)

    # Alertas: os maiores descontos primeiro, com limite por rodada.
    novas_promocoes.sort(key=lambda r: -r["desconto"])
    novas_promocoes = novas_promocoes[: int(cfg["promocao"]["maximo_alertas_por_execucao"])]
    canais: list[str] = []
    if novas_promocoes and not args.sem_alertas:
        canais = alertar(novas_promocoes, os.environ.get("SITE_URL"))
        agora = datetime.now(timezone.utc).isoformat(timespec="minutes")
        for r in novas_promocoes:
            alertados[r["chave"]] = {"preco": r["preco"], "quando": agora}

    estado.update(
        cursor=novo_cursor,
        ultima_execucao=datetime.now(timezone.utc).isoformat(timespec="minutes"),
        ultima_pesquisadas=pesquisadas,
    )
    gravar_json(dados / "precos.json", precos)
    gravar_json(dados / "alertas.json", alertados)
    gravar_json(dados / "estado.json", estado)
    gerar_site(cfg, rotas, precos, dados)

    print(
        f"Fim: {pesquisadas} rotas pesquisadas, {len(novas_promocoes)} promoções novas"
        + (f", alertas por {', '.join(canais)}" if canais else "")
    )
    return {"pesquisadas": pesquisadas, "promocoes": novas_promocoes, "canais": canais}


def main() -> None:
    p = argparse.ArgumentParser(description="Radar de promoções de passagens aéreas")
    p.add_argument("--quantidade", type=int, help="quantas rotas pesquisar nesta rodada")
    p.add_argument("--rota", help="pesquisar só uma rota, ex.: GRU-LIS-executiva")
    p.add_argument("--sem-alertas", action="store_true", help="não envia Telegram/e-mail")
    resultado = executar(p.parse_args())
    if resultado["pesquisadas"] == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
