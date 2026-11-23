"""Testes offline (não consultam o Google Flights)."""

import argparse
from datetime import date, datetime, timedelta, timezone

import pytest

from radar import __main__ as principal
from radar.alertas import texto_alerta
from radar.busca import PrecoData
from radar.config import carregar_config, montar_rotas
from radar.promocoes import avaliar, deve_alertar

CFG = carregar_config()
ROTAS = montar_rotas(CFG)


def rota(chave):
    return next(r for r in ROTAS if r.chave == chave)


def calendario(precos, inicio=None):
    inicio = inicio or date.today() + timedelta(days=10)
    return [
        PrecoData(
            ida=(inicio + timedelta(days=i)).isoformat(),
            volta=(inicio + timedelta(days=i + 10)).isoformat(),
            preco=p,
            moeda="BRL",
        )
        for i, p in enumerate(precos)
    ]


def test_rotas_cobrem_as_quatro_origens_e_todas_as_classes():
    assert {r.origem for r in ROTAS} == {"SJP", "GRU", "VCP", "CGH"}
    assert {r.classe for r in ROTAS} == {"economica", "premium", "executiva", "primeira"}
    assert len({r.chave for r in ROTAS}) == len(ROTAS)


def test_todos_os_aeroportos_existem_no_google_flights():
    fli = pytest.importorskip("fli.models")
    codigos = set(CFG["origens"]) | {d for g in CFG["destinos"].values() for d in g}
    faltando = [c for c in codigos if c not in fli.Airport.__members__]
    assert not faltando, f"Códigos desconhecidos: {faltando}"


def test_regioes_dos_destinos_existem():
    assert set(CFG["destinos"]) <= set(CFG["regioes"])


def test_preco_normal_nao_e_promocao():
    r = avaliar(rota("GRU-LIS-economica"), calendario([4200, 4100, 4300, 4000, 4500]), [], CFG)
    assert r["preco"] == 4000
    assert not r["promocao"]


def test_abaixo_do_limite_fixo_e_promocao():
    r = avaliar(rota("GRU-LIS-economica"), calendario([3500, 2700, 3600, 3400]), [], CFG)
    assert r["promocao"]
    assert any("preço-alvo" in m for m in r["motivos"])


def test_queda_contra_historico():
    hist = [["2026-01-01", 12000], ["2026-01-02", 12500], ["2026-01-03", 11800]]
    r = avaliar(rota("GRU-LIS-executiva"), calendario([9500, 9800, 9900]), hist, CFG)
    assert r["promocao"]
    assert any("habitual" in m for m in r["motivos"])
    assert r["referencia"] == 12000


def test_tarifa_erro_detectada_no_calendario():
    precos = [20000] * 30 + [9500]
    r = avaliar(rota("VCP-DXB-primeira"), calendario(precos), [], CFG)
    assert r["promocao"]
    assert any("outras datas" in m for m in r["motivos"])


def test_links_de_compra():
    r = avaliar(rota("SJP-MIA-executiva"), calendario([5000], date(2027, 3, 5)), [], CFG)
    assert "google.com/travel/flights" in r["google"]
    assert "sjp/mia/270305/270315" in r["skyscanner"]
    assert "cabinclass=business" in r["skyscanner"]


def test_nao_realerta_mesma_promocao():
    r = {"promocao": True, "preco": 2500}
    agora = datetime(2026, 12, 1, tzinfo=timezone.utc)
    ontem = {"preco": 2500, "quando": (agora - timedelta(days=1)).isoformat()}
    assert not deve_alertar(r, ontem, CFG, agora)
    assert deve_alertar({"promocao": True, "preco": 2300}, ontem, CFG, agora)  # caiu mais 8%
    antigo = {"preco": 2500, "quando": (agora - timedelta(days=8)).isoformat()}
    assert deve_alertar(r, antigo, CFG, agora)
    assert deve_alertar(r, None, CFG, agora)


def test_texto_do_alerta():
    r = avaliar(rota("CGH-REC-economica"), calendario([390, 600, 650]), [], CFG)
    t = texto_alerta(r, "https://exemplo.github.io/radar/")
    assert "CGH → Recife (REC)" in t and "R$ 390" in t


def test_rodizio_da_lista():
    escolhidas, cursor = principal.escolher_rotas(list(range(10)), 8, 4)
    assert escolhidas == [8, 9, 0, 1] and cursor == 2


def test_rodada_completa_com_busca_simulada(tmp_path, monkeypatch):
    monkeypatch.setattr(principal.time, "sleep", lambda s: None)
    enviados = []

    def busca_falsa(r, cfg):
        if r.destino == "REC":
            return calendario([300, 700, 800])
        return calendario([99999, 99999])

    args = argparse.Namespace(quantidade=20, rota=None, sem_alertas=False)
    res = principal.executar(args, tmp_path, busca_falsa, lambda p, s: enviados.extend(p) or ["teste"])
    assert res["pesquisadas"] == 20
    assert any(p["destino"] == "REC" for p in enviados)
    assert (tmp_path / "site.json").exists()
    estado = principal.ler_json(tmp_path / "estado.json", {})
    assert estado["cursor"] == 20

    # Segunda rodada nas mesmas rotas: não repete o alerta.
    enviados.clear()
    (tmp_path / "estado.json").write_text('{"cursor": 0}')
    principal.executar(args, tmp_path, busca_falsa, lambda p, s: enviados.extend(p) or ["teste"])
    assert enviados == []


def test_para_se_o_google_bloquear(tmp_path, monkeypatch):
    monkeypatch.setattr(principal.time, "sleep", lambda s: None)

    def busca_bloqueada(r, cfg):
        raise RuntimeError("429 Too Many Requests")

    args = argparse.Namespace(quantidade=20, rota=None, sem_alertas=True)
    res = principal.executar(args, tmp_path, busca_bloqueada, lambda p, s: [])
    assert res["pesquisadas"] == 0
    # Começou falhando: pula o trecho (4 rotas) para não ficar preso nele.
    assert principal.ler_json(tmp_path / "estado.json", {})["cursor"] == 4


def test_respostas_vazias_seguidas_param_e_retomam_dali(tmp_path, monkeypatch):
    monkeypatch.setattr(principal.time, "sleep", lambda s: None)
    chamadas = []

    def busca(r, cfg):
        chamadas.append(r.chave)
        return calendario([900, 950]) if len(chamadas) <= 6 else []

    args = argparse.Namespace(quantidade=20, rota=None, sem_alertas=True)
    res = principal.executar(args, tmp_path, busca, lambda p, s: [])
    assert res["pesquisadas"] == 6
    assert len(chamadas) == 10  # 6 com preço + 4 vazias, depois parou
    assert principal.ler_json(tmp_path / "estado.json", {})["cursor"] == 6


def test_resposta_vazia_nao_apaga_ultimo_preco(tmp_path, monkeypatch):
    monkeypatch.setattr(principal.time, "sleep", lambda s: None)
    args = argparse.Namespace(quantidade=None, rota="GRU-LIS-economica", sem_alertas=True)
    principal.executar(args, tmp_path, lambda r, c: calendario([3900, 4100]), lambda p, s: [])
    principal.executar(args, tmp_path, lambda r, c: [], lambda p, s: [])
    precos = principal.ler_json(tmp_path / "precos.json", {})
    assert precos["GRU-LIS-economica"]["atual"]["preco"] == 3900


def test_sem_canal_de_alerta_nao_marca_como_avisado(tmp_path, monkeypatch):
    monkeypatch.setattr(principal.time, "sleep", lambda s: None)
    args = argparse.Namespace(quantidade=None, rota="CGH-REC-economica", sem_alertas=False)
    res = principal.executar(args, tmp_path, lambda r, c: calendario([300, 700]), lambda p, s: [])
    assert res["promocoes"]
    assert principal.ler_json(tmp_path / "alertas.json", {}) == {}


def test_preco_caro_nao_vira_promocao_so_por_desconto():
    # Caso real da 1ª execução: SJP→Fortaleza a R$ 1.445, 40% abaixo das outras datas.
    r = avaliar(rota("SJP-FOR-economica"), calendario([1445] + [2500] * 20), [], CFG)
    assert not r["promocao"]


def test_mensagem_de_teste(monkeypatch):
    from radar import alertas

    enviadas = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.delenv("SMTP_USUARIO", raising=False)
    monkeypatch.setattr(alertas, "enviar_telegram", lambda m: enviadas.append(m) or True)
    promo = avaliar(rota("SJP-BSB-economica"), calendario([376, 700]), [], CFG)
    assert alertas.testar_alertas("https://site/", promo) == ["telegram"]
    assert "conectado" in enviadas[0] and "Brasília" in enviadas[0]
