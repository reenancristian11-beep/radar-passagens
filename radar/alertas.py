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
import urllib.request
from email.message import EmailMessage


def _reais(valor: float) -> str:
    return f"R$ {valor:,.0f}".replace(",", ".")


def _data_br(iso: str) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def texto_alerta(r: dict, site: str | None = None) -> str:
    """Mensagem em HTML simples (formato aceito pelo Telegram)."""
    e = html.escape
    linhas = [
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
    textos = [texto_alerta(r, site) for r in resultados]

    try:
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            for t in textos:
                enviar_telegram(t)
            canais.append("telegram")
    except Exception as erro:  # um canal com problema não derruba o outro
        print(f"[alerta] Falha no Telegram: {erro}")

    try:
        melhor = min(resultados, key=lambda r: r["preco"])
        assunto = (
            f"✈️ {len(resultados)} promoção(ões) de passagem — a partir de "
            f"{_reais(melhor['preco'])} ({melhor['origem']}→{melhor['cidade']})"
        )
        if enviar_email(assunto, "\n\n<hr>\n\n".join(textos)):
            canais.append("email")
    except Exception as erro:
        print(f"[alerta] Falha no e-mail: {erro}")

    return canais
