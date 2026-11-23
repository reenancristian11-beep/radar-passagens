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
CFG["prioridades"]["destinos"] = []  # testes gerais usam a config sem prioridades
ROTAS = montar_rotas(CFG)


@pytest.fixture(autouse=True)
def config_sem_prioridades(monkeypatch):
    monkeypatch.setattr(principal, "carregar_config", lambda: CFG)


def test_prioridades_do_config_real_sao_todas_reconhecidas(capsys):
    pytest.importorskip("airportsdata")
    from radar.prioridades import resolver

    real = carregar_config()
    motivos, _ = resolver(real)
    escolhidos = [c for c, m in motivos.items() if m == "escolhido por você"]
    assert len(escolhidos) == len(real["prioridades"]["destinos"])
    assert "Não reconheci" not in capsys.readouterr().out


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
    assert len(chamadas) == 11  # 6 com preço + 4 vazias + rota de teste (vazia) → parou
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


def cfg_com_prioridades(destinos, raio=600):
    import copy

    cfg = copy.deepcopy(CFG)
    cfg["prioridades"] = {"destinos": destinos, "raio_proximos_km": raio,
                          "parte_da_rodada_pct": 50, "margem_preco_alvo_pct": 15}
    return cfg


def test_prioridades_por_nome_codigo_e_proximos():
    pytest.importorskip("airportsdata")
    cfg = cfg_com_prioridades(["Lisboa", "nap", "Atlantis"])
    rotas = montar_rotas(cfg)
    pri = {r.destino: r.prioridade for r in rotas if r.prioridade}
    assert pri["LIS"] == "escolhido por você"
    assert pri["NAP"] == "escolhido por você"          # fora da lista: adicionado
    assert pri["OPO"].startswith("perto de Lisboa")    # ~275 km
    assert pri["MAD"].startswith("perto de Lisboa")    # ~500 km
    assert pri["FCO"].startswith("perto de")           # Roma, perto de Nápoles
    assert "BCN" not in pri and "MIA" not in pri       # longe
    nap = next(r for r in rotas if r.destino == "NAP" and r.classe == "executiva")
    assert nap.regiao == "europa" and nap.limite == 9000
    assert len(rotas) == len(ROTAS) + 4 * 4            # NAP: 4 origens × 4 classes


def test_prioridade_aceita_preco_um_pouco_acima_do_alvo():
    pytest.importorskip("airportsdata")
    cfg = cfg_com_prioridades(["LIS"], raio=0)
    lis = next(r for r in montar_rotas(cfg) if r.chave == "GRU-LIS-economica")
    r = avaliar(lis, calendario([3100, 3600, 3700]), [], cfg)   # alvo 2800 + 15% = 3220
    assert r["promocao"] and r["prioridade"]
    comum = avaliar(rota("GRU-LIS-economica"), calendario([3100, 3600, 3700]), [], CFG)
    assert not comum["promocao"]


def test_rodada_reserva_metade_para_prioridades(tmp_path, monkeypatch):
    pytest.importorskip("airportsdata")
    cfg = cfg_com_prioridades(["Orlando"], raio=0)
    monkeypatch.setattr(principal, "carregar_config", lambda: cfg)
    monkeypatch.setattr(principal.time, "sleep", lambda s: None)
    vistas = []

    def busca(r, c):
        vistas.append(r)
        return calendario([2000, 2900])

    args = argparse.Namespace(quantidade=20, rota=None, sem_alertas=True)
    principal.executar(args, tmp_path, busca, lambda p, s: [])
    assert [r.destino for r in vistas[:10]] == ["MCO"] * 10   # prioritárias primeiro
    assert all(r.destino != "MCO" for r in vistas[10:])
    estado = principal.ler_json(tmp_path / "estado.json", {})
    assert estado["cursor_prioridade"] == 10 and estado["cursor"] == 10
    site = principal.ler_json(tmp_path / "site.json", {})
    assert site["prioridades"] == ["Orlando (MCO)"]
    assert site["rotas"][0]["destino"] == "MCO"


def test_rota_de_teste_distingue_sem_voos_de_bloqueio(tmp_path, monkeypatch):
    monkeypatch.setattr(principal.time, "sleep", lambda s: None)
    sem_voos = {"SJP-BPS-economica", "SJP-FEN-economica", "SJP-GIG-economica", "SJP-BSB-economica"}

    def busca(r, cfg):  # Google respondendo; 4 rotas realmente sem voos
        return [] if r.chave in sem_voos else calendario([900, 950])

    args = argparse.Namespace(quantidade=20, rota=None, sem_alertas=True)
    res = principal.executar(args, tmp_path, busca, lambda p, s: [])
    assert res["pesquisadas"] == 16  # não parou
    precos = principal.ler_json(tmp_path / "precos.json", {})
    assert all(precos[c]["vazias"] == 1 for c in sem_voos)

    # Na 2ª vez vazias, passam a ser puladas por uma semana.
    (tmp_path / "estado.json").write_text('{"cursor": 0}')
    principal.executar(args, tmp_path, busca, lambda p, s: [])
    vistas = []
    (tmp_path / "estado.json").write_text('{"cursor": 0}')
    principal.executar(args, tmp_path, lambda r, c: vistas.append(r.chave) or busca(r, c), lambda p, s: [])
    assert not (set(vistas) & sem_voos)
    assert len(vistas) == 20  # as puladas não ocupam lugar na rodada


def viagem_cfg(**extra):
    import copy

    cfg = copy.deepcopy(CFG)
    v = {"nome": "Férias Orlando", "origem": "GRU", "destino": "MCO",
         "ida": (date.today() + timedelta(days=60)).isoformat(),
         "volta": (date.today() + timedelta(days=70)).isoformat(),
         "voos_ida": ["LA 8190"], "classe_comprada": "economica", "preco_pago": 4000,
         "avisar_upgrade_ate": 3000}
    v.update(extra)
    cfg["viagens_compradas"] = [v]
    cfg["upgrades"] = {"verificar_a_cada_horas": 6, "avisar_se_minha_passagem_cair_pct": 10}
    return cfg


def busca_viagem_falsa(precos):
    def busca(v, classe_google, cfg):
        p = precos.get(classe_google)
        return p and {"preco": p, "moeda": "BRL", "voos_ida": ["LA8190"], "voos_volta": ["LA8191"],
                      "mesmos_voos": True}
    return busca


def test_upgrade_e_queda_de_preco_da_viagem_comprada():
    from radar import upgrades

    cfg = viagem_cfg()
    situacao, alertados = {}, {}
    busca = busca_viagem_falsa({"ECONOMY": 3500, "PREMIUM_ECONOMY": 5800,
                                "BUSINESS": 6900, "FIRST": 15000})
    avisos, consultas = upgrades.verificar(cfg, situacao, alertados, busca)
    assert consultas == 4
    tipos = {(a["tipo"], a["classe"]) for a in avisos}
    # Queda de 12,5% na própria passagem; premium (+1.800) e executiva (+2.900) até R$ 3.000.
    assert tipos == {("queda", "Econômica"), ("upgrade", "Premium Economy"), ("upgrade", "Executiva")}
    assert situacao["Férias Orlando"]["classes"]["executiva"]["diferenca"] == 2900

    # Logo em seguida não consulta de novo (intervalo de 6 h).
    assert upgrades.verificar(cfg, situacao, alertados, busca) == ([], 0)


def test_viagem_passada_ou_incompleta_e_ignorada():
    from radar import upgrades

    passada = viagem_cfg(ida="2020-01-01", volta="2020-01-10")
    assert upgrades.viagens_validas(passada) == []
    sem_preco = viagem_cfg(preco_pago=None)
    assert upgrades.viagens_validas(sem_preco) == []


def test_aviso_de_upgrade_chega_no_alerta(tmp_path, monkeypatch):
    from radar.alertas import texto_upgrade

    cfg = viagem_cfg()
    monkeypatch.setattr(principal, "carregar_config", lambda: cfg)
    monkeypatch.setattr(principal.time, "sleep", lambda s: None)
    enviados = []
    args = argparse.Namespace(quantidade=2, rota=None, sem_alertas=False)
    res = principal.executar(args, tmp_path, lambda r, c: calendario([99999]),
                             lambda p, s: enviados.extend(p) or ["telegram"],
                             busca_viagem_falsa({"ECONOMY": 4100, "BUSINESS": 6500}))
    assert [a["classe"] for a in res["avisos_viagens"]] == ["Executiva"]
    texto = texto_upgrade(enviados[0])
    assert "UPGRADE: Executiva por +R$ 2.500" in texto and "mesmos voos" in texto
    site = principal.ler_json(tmp_path / "site.json", {})
    assert site["viagens"][0]["nome"] == "Férias Orlando"


def test_escolhe_o_voo_informado():
    from types import SimpleNamespace as N

    from radar.busca import _escolher

    def op(preco, *numeros):
        return N(price=preco, legs=[N(flight_number=n) for n in numeros])

    opcoes = [op(3000, "3456"), op(4200, "8190", "0510"), op(3900, "8190")]
    assert _escolher(opcoes, ["LA8190", "LA 510"]) == (opcoes[1], True)
    assert _escolher(opcoes, ["G3 1111"]) == (opcoes[0], False)  # não achou: mais barato
    assert _escolher(opcoes, []) == (opcoes[0], False)
