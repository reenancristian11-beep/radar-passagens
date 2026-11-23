"""Envio de alertas gratuitos: Telegram e/ou e-mail (SMTP, ex.: Gmail).

Configuração por variáveis de ambiente (no GitHub: Settings → Secrets):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  SMTP_USUARIO, SMTP_SENHA, EMAIL_PARA  (opcional: SMTP_SERVIDOR, SMTP_PORTA)
Sem nenhuma variável, os alertas só aparecem no log e no site.
"""

from __future__ import annotations

import html
import json
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage


def _reais(valor: float) -> str:
    return f"R$ {valor:,.0f}".replace(",", ".")


def _data_br(iso: str) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def texto_alerta(r: dict, site: str | None = None) -> str:
    """Mensagem em HTML simples (formato aceito pelo Telegram)."""
    e = html.escape
    linhas = []
    if r.get("prioridade"):
        linhas.append(f"⭐ <b>DESTINO PRIORITÁRIO</b> ({e(r['prioridade'])})")
    linhas += [
        f"✈️ <b>{e(r['origem'])} → {e(r['cidade'])} ({e(r['destino'])})</b>",
        f"💺 {e(r['classe_nome'])} · ida e volta",
        f"💰 <b>{_reais(r['preco'])}</b> (normal ≈ {_reais(r['referencia'])})",
        f"📅 {_data_br(r['ida'])} → {_data_br(r['volta'])}",
        "🔎 " + e("; ".join(r["motivos"])),
    ]
    outras = [d for d in r["datas_baratas"][1:4]]
    if outras:
        linhas.append(
            "Outras datas: "
            + ", ".join(f"{_data_br(d['ida'])} ({_reais(d['preco'])})" for d in outras)
        )
    linhas.append(f'<a href="{e(r["google"])}">Ver no Google Flights</a> · '
                  f'<a href="{e(r["skyscanner"])}">Skyscanner</a>')
    if site:
        linhas.append(f'<a href="{e(site)}">Todas as promoções</a>')
    return "\n".join(linhas)


def texto_upgrade(a: dict, site: str | None = None) -> str:
    """Aviso sobre uma viagem já comprada (upgrade ou queda de preço)."""
    e = html.escape
    voos = " + ".join(a["voos_ida"] + a["voos_volta"])
    mesmos = "nos mesmos voos" if a["mesmos_voos"] else "nas mesmas datas (voos podem ser outros)"
    if a["tipo"] == "upgrade":
        linhas = [
            f"⬆️ <b>UPGRADE: {e(a['classe'])} por +{_reais(a['diferenca'])}</b>",
            f"🧳 Sua viagem: <b>{e(a['viagem'])}</b> ({e(a['origem'])} → {e(a['destino'])})",
            f"Você pagou {_reais(a['pago'])} em {e(a['classe_comprada'])}; "
            f"{e(a['classe'])} hoje custa {_reais(a['preco'])} {mesmos}.",
            "👉 Peça o upgrade pago à companhia ou, se sua tarifa permite remarcar, "
            "troque de classe pagando a diferença.",
        ]
    else:
        linhas = [
            f"💸 <b>SUA PASSAGEM BAIXOU {_reais(-a['diferenca'])}</b>",
            f"🧳 Sua viagem: <b>{e(a['viagem'])}</b> ({e(a['origem'])} → {e(a['destino'])})",
            f"Você pagou {_reais(a['pago'])}; hoje a mesma classe ({e(a['classe'])}) "
            f"custa {_reais(a['preco'])} {mesmos}.",
            "👉 Se a sua tarifa permite remarcação ou crédito, vale consultar a companhia.",
        ]
    linhas.append(f"📅 {_data_br(a['ida'])}" + (f" → {_data_br(a['volta'])}" if a.get("volta") else ""))
    if voos:
        linhas.append(f"Voos: {e(voos)}")
    linhas.append(f'<a href="{e(a["google"])}">Ver no Google Flights</a>')
    if site:
        linhas.append(f'<a href="{e(site)}">Abrir o radar</a>')
    return "\n".join(linhas)


def enviar_telegram(mensagem: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    dados = json.dumps(
        {"chat_id": chat, "text": mensagem, "parse_mode": "HTML", "disable_web_page_preview": True}
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=dados,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status == 200


def enviar_email(assunto: str, corpo_html: str) -> bool:
    usuario = os.environ.get("SMTP_USUARIO")
    senha = os.environ.get("SMTP_SENHA")
    para = os.environ.get("EMAIL_PARA") or usuario
    if not usuario or not senha:
        return False
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = usuario
    msg["To"] = para
    msg.set_content("Seu leitor de e-mail não mostra HTML. Abra o site do radar para ver as promoções.")
    msg.add_alternative(corpo_html.replace("\n", "<br>\n"), subtype="html")
    servidor = os.environ.get("SMTP_SERVIDOR", "smtp.gmail.com")
    porta = int(os.environ.get("SMTP_PORTA", "465"))
    with smtplib.SMTP_SSL(servidor, porta, timeout=30) as s:
        s.login(usuario, senha)
        s.send_message(msg)
    return True


def enviar_alertas(resultados: list[dict], site: str | None = None) -> list[str]:
    """Envia os alertas e devolve a lista de canais usados."""
    if not resultados:
        return []
    canais: list[str] = []
    textos = [texto_upgrade(r, site) if r.get("tipo") else texto_alerta(r, site) for r in resultados]

    try:
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            for t in textos:
                enviar_telegram(t)
            canais.append("telegram")
    except Exception as erro:  # um canal com problema não derruba o outro
        print(f"[alerta] Falha no Telegram: {erro}")

    try:
        ups = [r for r in resultados if r.get("tipo")]
        promos = [r for r in resultados if not r.get("tipo")]
        if promos:
            melhor = min(promos, key=lambda r: r["preco"])
            assunto = (
                f"✈️ {len(promos)} promoção(ões) de passagem — a partir de "
                f"{_reais(melhor['preco'])} ({melhor['origem']}→{melhor['cidade']})"
            )
            if ups:
                assunto = f"⬆️ {len(ups)} aviso(s) da sua viagem + " + assunto
        else:
            assunto = f"⬆️ {len(ups)} aviso(s) de upgrade/preço da sua viagem comprada"
        if enviar_email(assunto, "\n\n<hr>\n\n".join(textos)):
            canais.append("email")
    except Exception as erro:
        print(f"[alerta] Falha no e-mail: {erro}")

    return canais


def testar_alertas(site: str | None = None, promocao: dict | None = None) -> list[str]:
    """Manda uma mensagem de teste em cada canal configurado.

    Diferente do envio normal, aqui um erro interrompe com a mensagem do
    problema, para ficar fácil descobrir o que está errado no log.
    """
    partes = ["✅ <b>Radar de Passagens conectado!</b>",
              "Os alertas de promoção vão chegar aqui."]
    if site:
        partes.append(f'<a href="{html.escape(site)}">Abrir o site do radar</a>')
    if promocao:
        partes += ["", "Exemplo com uma promoção atual:", "", texto_alerta(promocao, None)]
    mensagem = "\n".join(partes)

    canais: list[str] = []
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        if not os.environ.get("TELEGRAM_CHAT_ID"):
            raise SystemExit("Falta o secret TELEGRAM_CHAT_ID.")
        try:
            enviar_telegram(mensagem)
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode(errors="replace")
            raise SystemExit(f"Telegram recusou ({erro.code}): {detalhe}") from None
        canais.append("telegram")
    if os.environ.get("SMTP_USUARIO") and enviar_email("✅ Radar de Passagens conectado", mensagem):
        canais.append("email")
    if not canais:
        raise SystemExit("Nenhum canal configurado (TELEGRAM_BOT_TOKEN / SMTP_USUARIO).")
    return canais
