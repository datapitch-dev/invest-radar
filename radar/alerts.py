"""Alerty: wykrywa zmiany sygnałów między skanami i wysyła powiadomienie (Telegram i/lub e-mail).

Konfiguracja w .env:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID           (bot z @BotFather; chat id np. z @userinfobot)
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_TO   (Gmail: hasło aplikacji, host smtp.gmail.com, port 465)
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from .signals import ACTION_PL, HORIZON_PL

ACTIONABLE = {"BUY", "ACCUMULATE", "REDUCE", "AVOID"}


def diff_signals(prev: list[dict] | None, new: list, min_confidence: float = 0.40,
                 alert_on_first_run: bool = False) -> list[dict]:
    """Zmiana względem poprzedniego skanu. Alertujemy, gdy nowy sygnał jest 'do działania'
    i ma wystarczającą pewność, gdy sygnał do działania wygasł (wróciło TRZYMAJ), albo gdy ta sama akcja
    dopiero teraz przekroczyła próg pewności (wcześniej była za słaba na alert)."""
    if prev is None:
        if not alert_on_first_run:
            return []
        prev = []
    old = {(s["symbol"], s["horizon"]): s for s in prev}
    changes = []
    for s in new:
        p = old.get((s.symbol, s.horizon)) or {}
        before = p.get("action")
        if before is None and prev:
            before = "HOLD"                     # nowy instrument w watchliście
        confident = s.action in ACTIONABLE and s.confidence >= min_confidence
        if before == s.action:
            # brak pewności w starym zapisie = nie wiemy, czy próg był przekroczony -> bez alertu
            old_conf = p.get("confidence")
            if not (confident and old_conf is not None and old_conf < min_confidence):
                continue
        elif not (confident or (before in ACTIONABLE and s.action not in ACTIONABLE)):
            continue
        changes.append({"symbol": s.symbol, "name": s.name, "horizon": s.horizon,
                        "old": before or "-", "new": s.action, "score": s.score,
                        "confidence": s.confidence, "old_confidence": p.get("confidence"),
                        "risks": s.risks[:2]})
    order = {"BUY": 0, "AVOID": 1, "ACCUMULATE": 2, "REDUCE": 3, "HOLD": 4}
    return sorted(changes, key=lambda c: (order.get(c["new"], 9), -c["confidence"]))


def format_message(changes: list[dict], macro_summary: str = "", max_len: int = 3500) -> str:
    lines = ["📡 Invest Radar - zmiany sygnałów", ""]
    for c in changes:
        if c["old"] == c["new"]:
            what = f"{ACTION_PL[c['new']]} - pewność wzrosła z {c['old_confidence']:.0%}"
        else:
            what = f"{ACTION_PL.get(c['old'], c['old'])} → {ACTION_PL[c['new']]}"
        lines.append(f"{c['name']} ({c['symbol']}) [{HORIZON_PL[c['horizon']].split(' ')[0]}]: {what} "
                     f"(score {c['score']:+.2f}, pewność {c['confidence']:.0%})")
        for r in c["risks"]:
            lines.append(f"   ⚠ {r}")
    if macro_summary:
        lines += ["", f"Makro: {macro_summary}"]
    lines += ["", "To nie jest porada inwestycyjna. Zlecenia składasz sam u brokera."]
    text = "\n".join(lines)
    return text if len(text) <= max_len else text[:max_len - 20] + "\n… (skrócone)"


def send_telegram(text: str, token: str, chat_id: str) -> None:
    import requests
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=20)
    r.raise_for_status()


def send_email(text: str, host: str, port: int, user: str, password: str, to: str,
               subject: str = "Invest Radar - zmiany sygnałów") -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(text)
    with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


def notify(text: str, env=None, telegram=send_telegram, email=send_email) -> list[str]:
    """Wysyła na skonfigurowane kanały. Zwraca listę kanałów, które się udały; błąd jednego nie blokuje drugiego."""
    env = os.environ if env is None else env
    sent, errors = [], []
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        try:
            telegram(text, env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"])
            sent.append("telegram")
        except Exception as e:
            errors.append(f"telegram: {e}")
    if all(env.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO")):
        try:
            email(text, env["SMTP_HOST"], int(env.get("SMTP_PORT", 465)), env["SMTP_USER"],
                  env["SMTP_PASSWORD"], env["EMAIL_TO"])
            sent.append("email")
        except Exception as e:
            errors.append(f"email: {e}")
    if errors and not sent:
        raise RuntimeError("; ".join(errors))
    return sent
