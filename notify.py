# -*- coding: utf-8 -*-
"""
Powiadomienia ntfy (zastępują Telegram).

Wysyłane są tylko kluczowe sygnały planu, potwierdzone zamkniętą świecą 1h:
  BUY / BUY_BREAKOUT – zakup (strefa akumulacji, odkup albo kupno na wybiciu),
  SELL              – cena w widełkach sprzedaży kroku,
  PREP_SELL/PREP_BUY – cena 2% od widełek: czas wystawić zlecenie z limitem,
  STEP_FAIL         – odkup zawiódł (2 zamknięcia 1h pod widełkami),
  INVALID           – plan unieważniony (zamknięcie dnia poniżej 0,0300).

Sekrety: NTFY_TOPIC (wymagany), NTFY_URL (domyślnie https://ntfy.sh), NTFY_TOKEN (opcjonalny, gdy temat jest chroniony).
"""
import os, json, urllib.request
from datetime import datetime, timezone, timedelta

from plan import fmt, pct

NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")

PRIORITY = {"INVALID": 5, "SELL": 5, "BUY": 5, "BUY_BREAKOUT": 5, "STEP_FAIL": 4, "PREP_SELL": 4, "PREP_BUY": 4}
TAGS = {"INVALID": ["no_entry"], "SELL": ["red_circle"], "BUY": ["green_circle"], "BUY_BREAKOUT": ["rocket"],
        "STEP_FAIL": ["warning"], "PREP_SELL": ["hourglass"], "PREP_BUY": ["hourglass"]}
STRENGTH = {"PREP_SELL": 1, "PREP_BUY": 1, "SELL": 2, "BUY": 2, "BUY_BREAKOUT": 2, "STEP_FAIL": 2, "INVALID": 3}


def pl_time(ts):
    """Godzina polska (CET/CEST) bez zależności od tzdata."""
    d = datetime.fromtimestamp(ts, timezone.utc)
    y = d.year
    last_sun = lambda m: max(datetime(y, m, day, tzinfo=timezone.utc) for day in range(25, 32)
                             if datetime(y, m, day).weekday() == 6)
    summer = last_sun(3).replace(hour=1) <= d < last_sun(10).replace(hour=1)
    return (d + timedelta(hours=2 if summer else 1)).strftime("%d.%m %H:%M")


def fp(x, sign=False):
    return (f"{x:+.1f}%" if sign else f"{x:.1f}%").replace(".", ",")


def btc_line(btc):
    if not btc or not btc.get("price"):
        return ""
    p = btc["price"]
    parts = [f"BTC {fmt(p)}"]
    if btc.get("below"):
        parts.append(f"wsparcie {fmt(btc['below']['price'])} ({fp(pct(btc['below']['price'], p), True)})")
    if btc.get("above"):
        parts.append(f"opór {fmt(btc['above']['price'])} ({fp(pct(btc['above']['price'], p), True)})")
    if btc.get("inside"):
        parts.append("w strefie: " + btc["inside"][0]["label"])
    return " | ".join(parts)


def message(e, plan, btc=None):
    """(tytuł, treść) dla zdarzenia z plan.replay()."""
    k = e["kind"]; t = e.get("target"); z = e.get("zone")
    when = pl_time(e["t"])
    inv = plan["invalidation"]["daily_close_below"]
    lines = []
    if k == "BUY":
        if z["step"] == "start":
            title = "KUP Sonic – strefa akumulacji"
            lines.append(f"Świeca 1h zamknięta {when}: cena zeszła do {fmt(z['trigger'])} (strefa {fmt(z['lo'])}–{fmt(z['hi'])}).")
        else:
            title = f"ODKUP Sonic – po kroku {z['step']}"
            lines.append(f"Świeca 1h zamknięta {when}: cena w strefie odkupu {fmt(z['lo'])}–{fmt(z['hi'])}. Limit ok. {fmt(z['main'])}.")
        if t:
            lines.append(f"Od razu ustaw sprzedaż z limitem: {t['label']} {fmt(t['main'])} (widełki {fmt(t['lo'])}–{fmt(t['hi'])}).")
        lines.append(f"Unieważnienie planu: zamknięcie dnia poniżej {fmt(inv)}.")
    elif k == "BUY_BREAKOUT":
        title = "KUPNO NA WYBICIU – Sonic"
        lines.append(f"Odkup się nie wykonał, a 2 kolejne świece 1h zamknęły się powyżej {fmt(e['level'])} (ostatnia {when}, {fmt(e['price'])}).")
        lines.append("Plan: kupić na wybiciu (stop-buy/limit tuż nad ceną), żeby nie zostać bez pozycji.")
        if t:
            lines.append(f"Następna sprzedaż: {t['label']} {fmt(t['main'])} (widełki {fmt(t['lo'])}–{fmt(t['hi'])}).")
    elif k == "SELL":
        title = f"SPRZEDAJ Sonic – {t['label']}"
        lines.append(f"Świeca 1h zamknięta {when} weszła w widełki sprzedaży {fmt(t['lo'])}–{fmt(t['hi'])} (główny poziom {fmt(t['main'])}).")
        lines.append("Sprzedaj w dolnej części widełek; jeśli cena w widełkach zawraca – nie czekaj na górną granicę.")
        if z:
            lines.append(f"Potem odkup: {fmt(z['lo'])}–{fmt(z['hi'])}, limit ok. {fmt(z['main'])} "
                         f"(plan zakłada cofnięcie ok. {pct(z['main'], t['main']):.0f}%).")
            if z.get("breakout"):
                lines.append(f"Jeśli nie zejdzie: kupno na wybiciu po 2 zamknięciach 1h powyżej {fmt(z['breakout'])}.")
            if z.get("note"):
                lines.append(z["note"])
        if t.get("final"):
            lines.append("To finał hossy według planu – zostań w gotówce.")
    elif k == "PREP_SELL":
        title = f"Przygotuj sprzedaż – {t['label']}"
        lines.append(f"Świeca 1h zamknięta {when}: szczyt {fmt(e.get('high') or e['price'])}, cena {fmt(e['price'])} – "
                     f"{fp(abs(pct(t['lo'], e['price'])))} pod widełkami {fmt(t['lo'])}–{fmt(t['hi'])}.")
        lines.append(f"Ustaw zlecenie sprzedaży z limitem {fmt(t['main'])} (albo w dolnej części widełek). Coinbase: limit stojący w arkuszu, nie rynkowe.")
    elif k == "PREP_BUY":
        title = "Przygotuj zakup Sonic" if z["step"] == "start" else f"Przygotuj odkup – po kroku {z['step']}"
        lim = z['trigger'] if z['step'] == 'start' else z['main']
        lines.append(f"Świeca 1h zamknięta {when}: dołek {fmt(e.get('low') or e['price'])}, cena {fmt(e['price'])} – "
                     f"{fp(pct(e['price'], lim))} nad limitem {fmt(lim)} (strefa {fmt(z['lo'])}–{fmt(z['hi'])}).")
        lines.append(f"Ustaw zlecenie kupna z limitem ok. {fmt(lim)} (Coinbase: limit stojący w arkuszu).")
        if z.get("breakout"):
            lines.append(f"Zapas: stop-buy na wybiciu powyżej {fmt(z['breakout'])}.")
    elif k == "STEP_FAIL":
        title = "Uwaga: odkup zawiódł – Sonic"
        s = e.get("support") or {}
        lines.append(f"Dwa zamknięcia 1h poniżej {fmt(z['lo'])} (ostatnie {when}, {fmt(e['price'])}).")
        lines.append(f"Nie dokupuj. Następne wsparcie z mapy: {fmt(s.get('lo'))}–{fmt(s.get('hi'))}. "
                     f"Plan obowiązuje, dopóki zamknięcie dnia jest powyżej {fmt(inv)}.")
    elif k == "INVALID":
        title = "PLAN UNIEWAŻNIONY – Sonic"
        nx = plan["invalidation"]["next_supports"]
        lines.append(f"Zamknięcie dnia {fmt(e['close'])} poniżej {fmt(inv)}. Scenariusz hossy wstrzymany.")
        lines.append(f"Kolejne wsparcia: {', '.join(fmt(x) for x in nx)}. Nowy plan po analizie.")
    else:
        title, lines = k, [str(e)]
    bl = btc_line(btc)
    if bl:
        lines.append(bl)
    return title, "\n".join(lines)


def select_new(events, sent_keys, now_ts, window_h):
    """Zdarzenia z ostatnich window_h godzin, jeszcze niewysłane; przypomnienie pomijane, gdy jest mocniejszy sygnał tego kroku."""
    fresh = [e for e in events if now_ts - window_h * 3600 <= e["t"] <= now_ts + 60 and key(e) not in sent_keys]
    strong_steps = {e["step"] for e in fresh if STRENGTH.get(e["kind"], 0) >= 2}
    return [e for e in fresh if not (STRENGTH.get(e["kind"], 0) == 1 and e["step"] in strong_steps)]


def key(e):
    return f"{e['kind']}:{e.get('step')}:{e['t']}"


def send(title, body, priority=4, tags=None, click=None, dry_run=False):
    if dry_run:
        print(f"[DRY_RUN] ntfy (p{priority}) {title}\n{body}\n")
        return True
    if not NTFY_TOPIC:
        print("[ntfy] brak NTFY_TOPIC – pomijam")
        return False
    payload = dict(topic=NTFY_TOPIC, title=title, message=body, priority=priority, tags=tags or [])
    if click:
        payload["click"] = click
    headers = {"Content-Type": "application/json"}
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    req = urllib.request.Request(NTFY_URL, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = 200 <= r.status < 300
            print(f"[ntfy] {'wysłano' if ok else 'błąd HTTP ' + str(r.status)}: {title}")
            return ok
    except Exception as ex:
        print(f"[ntfy] błąd: {type(ex).__name__}: {ex}")
        return False


def send_event(e, plan, btc=None, click=None, dry_run=False):
    title, body = message(e, plan, btc)
    return send(title, body, PRIORITY.get(e["kind"], 3), TAGS.get(e["kind"]), click, dry_run)
