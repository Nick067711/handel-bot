# -*- coding: utf-8 -*-
"""
Powiadomienia ntfy (zastępują Telegram).

Wysyłane są tylko kluczowe sygnały planu, potwierdzone zamkniętą świecą 1h:
  BUY / BUY_BREAKOUT – zakup (strefa akumulacji, odkup albo kupno na wybiciu),
  SELL              – cena w widełkach sprzedaży kroku,
  PREP_SELL/PREP_BUY – cena 2% od widełek: czas wystawić zlecenie z limitem,
  STEP_FAIL         – odkup zawiódł (2 zamknięcia 1h pod widełkami),
  DEEP_TOUCH/DEEP_LOST – korekta rozszerzona: cena w strefie wsparcia poniżej planu / strefa przebita
                       (unieważnienie planu wyłączone 18.11.2026).

Sekrety: NTFY_TOPIC (wymagany), NTFY_URL (domyślnie https://ntfy.sh), NTFY_TOKEN (opcjonalny, gdy temat jest chroniony).
"""
import os, json, urllib.request
from datetime import datetime, timezone, timedelta

from plan import fmt, pct

NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")

PRIORITY = {"CHANNEL_BREAKOUT": 5, "PRE_BREAKOUT": 3, "UPDATE_2D": 4, "INVALID": 5, "SELL": 5, "BUY": 5, "BUY_BREAKOUT": 5, "STEP_FAIL": 4, "PREP_SELL": 4, "PREP_BUY": 4,
            "PREP_MID": 3, "MID_SELL": 4, "MID_REBUY": 4, "MID_BREAKOUT": 4, "CONFIRM": 3,
            "DEEP_TOUCH": 4, "DEEP_LOST": 4}
TAGS = {"CHANNEL_BREAKOUT": ["rocket"], "PRE_BREAKOUT": ["eyes"], "UPDATE_2D": ["memo"], "INVALID": ["no_entry"], "SELL": ["red_circle"], "BUY": ["green_circle"], "BUY_BREAKOUT": ["rocket"],
        "STEP_FAIL": ["warning"], "PREP_SELL": ["hourglass"], "PREP_BUY": ["hourglass"], "PREP_MID": ["eyes"],
        "MID_SELL": ["large_orange_diamond"], "MID_REBUY": ["large_blue_diamond"], "MID_BREAKOUT": ["large_blue_diamond"],
        "CONFIRM": ["white_check_mark"], "DEEP_TOUCH": ["anchor"], "DEEP_LOST": ["arrow_down"]}
STRENGTH = {"CHANNEL_BREAKOUT": 2, "PRE_BREAKOUT": 1, "UPDATE_2D": 2, "PREP_SELL": 1, "PREP_BUY": 1, "PREP_MID": 1, "SELL": 2, "BUY": 2, "BUY_BREAKOUT": 2, "STEP_FAIL": 2, "INVALID": 3,
            "MID_SELL": 2, "MID_REBUY": 2, "MID_BREAKOUT": 2, "DEEP_TOUCH": 2, "DEEP_LOST": 2}


def pl_time(ts):
    """Godzina polska (CET/CEST) bez zależności od tzdata."""
    d = datetime.fromtimestamp(ts, timezone.utc)
    y = d.year
    last_sun = lambda m: max(datetime(y, m, day, tzinfo=timezone.utc) for day in range(25, 32)
                             if datetime(y, m, day).weekday() == 6)
    summer = last_sun(3).replace(hour=1) <= d < last_sun(10).replace(hour=1)
    return (d + timedelta(hours=2 if summer else 1)).strftime("%d.%m %H:%M")


def r4(x):
    return round(x, 4) if x < 0.1 else round(x, 3)


def fp(x, sign=False):
    return (f"{x:+.1f}%" if sign else f"{x:.1f}%").replace(".", ",")


def fu(x):
    return f"{x:,.0f}".replace(",", " ") + " USD"


def fsn(x):
    return f"{x:,.0f}".replace(",", " ") + " S"


def amounts_text(plan, side, step, mid=False):
    """Kwoty wg prognozy planu (wariant ostrożny, prowizja z data/sonic_plan.json)."""
    try:
        from plan import amounts_for, start_amounts, fmt as F
        if side == "buy" and step == "start":
            a = start_amounts(plan)
            return f"Wg planu: {fsn(a['S'])} przy limicie {F(a['limit'])} = ok. {fu(a['usd'])} z prowizją."
        r = amounts_for(plan, step, mid)
        if not r:
            return ""
        if side == "sell":
            return (f"Wg planu masz ok. {fsn(r['S_in'])} → przy limicie {F(r['sell'])}: {fu(r['usd'])} "
                    f"(w widełkach {F(r['sell_lo'])}–{F(r['sell_hi'])}: {fu(r['usd_lo'])} – {fu(r['usd_hi'])}).")
        if r.get("rebuy"):
            return (f"Wg planu masz ok. {fu(r['usd'])} → przy limicie {F(r['rebuy'])}: {fsn(r['S'])} "
                    f"(gdyby zeszło do {F(r['rebuy_lo'])}: do {fsn(r['S_hi'])}).")
    except Exception as ex:
        print(f"[ntfy] brak kwot: {ex}")
    return ""


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


def _message_long(e, plan, btc=None):
    """(tytuł, treść) dla zdarzenia z plan.replay() – wersja opisowa (kanał, unieważnienie)."""
    k = e["kind"]; t = e.get("target"); z = e.get("zone")
    when = pl_time(e["t"])
    lines = []
    if k == "BUY":
        if z["step"] == "start":
            title = "KUP Sonic – strefa akumulacji"
            lines.append(f"Świeca 1h zamknięta {when}: cena zeszła do {fmt(z['trigger'])} (strefa {fmt(z['lo'])}–{fmt(z['hi'])}).")
        else:
            title = f"ODKUP Sonic – po kroku {z['step']}"
            lines.append(f"Świeca 1h zamknięta {when}: cena w strefie odkupu {fmt(z['lo'])}–{fmt(z['hi'])}. Limit {fmt(z['limit'])} (górna granica – wariant ostrożny).")
        if t:
            lines.append(f"Od razu ustaw sprzedaż z limitem: {t['label']} {fmt(t['limit'])} (widełki {fmt(t['lo'])}–{fmt(t['hi'])}, główny poziom {fmt(t['main'])}).")
    elif k == "BUY_BREAKOUT":
        title = "KUPNO NA WYBICIU – Sonic"
        lines.append(f"Odkup się nie wykonał, a 2 kolejne świece 1h zamknęły się powyżej {fmt(e['level'])} (ostatnia {when}, {fmt(e['price'])}).")
        lines.append("Plan: kupić na wybiciu (stop-buy/limit tuż nad ceną), żeby nie zostać bez pozycji.")
        if t:
            lines.append(f"Następna sprzedaż: {t['label']} – limit {fmt(t['limit'])} (widełki {fmt(t['lo'])}–{fmt(t['hi'])}).")
    elif k == "SELL":
        title = f"SPRZEDAJ Sonic – {t['label']}"
        lines.append(f"Świeca 1h zamknięta {when} weszła w widełki sprzedaży {fmt(t['lo'])}–{fmt(t['hi'])} (główny poziom {fmt(t['main'])}).")
        lines.append(f"Sprzedaj – limit {fmt(t['limit'])} (wariant ostrożny); jeśli cena w widełkach zawraca – nie czekaj, sprzedaj od razu.")
        if z:
            lines.append(f"Potem odkup: {fmt(z['lo'])}–{fmt(z['hi'])}, limit {fmt(z['limit'])} (górna granica) "
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
        lines.append(f"Ustaw zlecenie sprzedaży z limitem {fmt(t['limit'])} (wariant ostrożny; widełki {fmt(t['lo'])}–{fmt(t['hi'])}). Coinbase: limit stojący w arkuszu, nie rynkowe.")
    elif k == "PREP_BUY":
        title = "Przygotuj zakup Sonic" if z["step"] == "start" else f"Przygotuj odkup – po kroku {z['step']}"
        lim = z['limit']
        lines.append(f"Świeca 1h zamknięta {when}: dołek {fmt(e.get('low') or e['price'])}, cena {fmt(e['price'])} – "
                     f"{fp(pct(e['price'], lim))} nad limitem {fmt(lim)} (strefa {fmt(z['lo'])}–{fmt(z['hi'])}).")
        lines.append(f"Ustaw zlecenie kupna z limitem ok. {fmt(lim)} (Coinbase: limit stojący w arkuszu).")
        if z.get("breakout"):
            lines.append(f"Zapas: stop-buy na wybiciu powyżej {fmt(z['breakout'])}.")
    elif k == "STEP_FAIL":
        title = "Uwaga: odkup zawiódł – Sonic"
        s = e.get("support") or {}
        lines.append(f"Dwa zamknięcia 1h poniżej {fmt(z['lo'])} (ostatnie {when}, {fmt(e['price'])}).")
        lines.append(f"Nie dokupuj. Następne wsparcie z mapy: {fmt(s.get('lo'))}–{fmt(s.get('hi'))}. Plan obowiązuje dalej.")
    elif k in ("PREP_MID", "MID_SELL"):
        m = e["mid"]
        title = (f"Krok pośredni: Sonic blisko {fmt(m['main'])}" if k == "PREP_MID" else f"KROK POŚREDNI – Sonic w strefie {fmt(m['lo'])}–{fmt(m['hi'])}")
        lines.append(f"Świeca 1h zamknięta {when}: szczyt {fmt(e.get('high'))}, cena {fmt(e['price'])}. {m['label']}: widełki {fmt(m['lo'])}–{fmt(m['hi'])}.")
        lines += mid_advice(m, t)
    elif k == "MID_REBUY":
        m = e["mid"]
        title = f"Odkup po kroku pośrednim {m['id']}"
        lines.append(f"Jeśli sprzedałeś na kroku pośrednim ({fmt(m['main'])}): świeca 1h zamknięta {when} zeszła do strefy odkupu "
                     f"{fmt(m['rebuy_lo'])}–{fmt(m['rebuy_hi'])} (dołek {fmt(e.get('low'))}). Kup z limitem {fmt(m['rebuy_limit'])}.")
        if t:
            lines.append(f"Główny cel bez zmian: {t['label']} – limit {fmt(t['limit'])} (widełki {fmt(t['lo'])}–{fmt(t['hi'])}).")
        lines.append("Jeśli nie sprzedawałeś – nic nie rób, plan trzyma pozycję.")
    elif k == "MID_BREAKOUT":
        m = e["mid"]
        title = f"Wybicie nad krokiem pośrednim {m['id']}"
        lines.append(f"Jeśli sprzedałeś na {fmt(m['main'])}: 2 zamknięcia 1h powyżej {fmt(m['breakout'])} (ostatnie {when}, {fmt(e['price'])}) – "
                     f"odkup na wybiciu, żeby nie zostać bez pozycji.")
        if t:
            lines.append(f"Główny cel: {t['label']} – limit {fmt(t['limit'])} (widełki {fmt(t['lo'])}–{fmt(t['hi'])}).")
        lines.append("Jeśli nie sprzedawałeś – nic nie rób.")
    elif k in ("CHANNEL_BREAKOUT", "PRE_BREAKOUT", "UPDATE_2D"):
        ch = e["channel"]; af = ch.get("after") or {}
        sell, reb = af.get("sell"), af.get("rebuy")
        mx = af.get("max") or []
        nc = af.get("next_channel") or []
        if k == "PRE_BREAKOUT":
            title = f"Obserwuj wykres – Sonic pod sufitem kanału {fmt(ch['ceiling'])}"
            lines.append(f"Świeca 1h zamknięta {when}: {fmt(e['price'])}, {fp(abs(pct(ch['ceiling'], e['price'])))} pod sufitem, RSI 1h {e['rsi']}, "
                         f"wolumen do {str(e['vol_x']).replace('.', ',')}× średniej. Tak wyglądały godziny przed wybiciami z VIII–X 2026 (alert bywa fałszywy).")
            lines.append(f"Wybicie = zamknięcie 1h powyżej {fmt(ch['ceiling'] * (1 + ch.get('break_pct', 1) / 100))} przy dużym wolumenie.")
        elif k == "CHANNEL_BREAKOUT":
            title = f"WYBICIE z kanału {fmt(ch['floor'])}–{fmt(ch['ceiling'])}"
            lines.append(f"Świeca 1h zamknięta {when}: {fmt(e['price'])} ({fp(pct(e['price'], ch['ceiling']), True)} nad sufitem), "
                         f"wolumen do {str(e['vol_x']).replace('.', ',')}× średniej.")
        else:
            title = "Aktualizacja: 2 dni po wybiciu, sprzedaż jeszcze nie weszła"
            pk = e["peak"]
            lines.append(f"Najwyżej od wybicia: {fmt(pk)} ({fp(pct(pk, sell), True)} wobec celu {fmt(sell)}), teraz {fmt(e['price'])}.")
            if pk < sell:
                lines.append(f"Jeśli cena wyraźnie hamuje pod celem, rozważ obniżenie limitu do ok. {fmt(r4(pk * 0.99))} (1% pod szczytem). Inaczej zostaw {fmt(sell)}.")
        if sell:
            lines.append(f"Bezpieczna sprzedaż: {fmt(sell)} ({fp(pct(sell, e['price']), True)} od ceny). {af.get('note', '')}".strip())
        if reb and sell:
            lines.append(f"Bezpieczny odkup: {fmt(reb)} ({fp(pct(reb, sell), True)} pod sprzedażą).")
        if mx:
            lines.append(f"Możliwe maksimum, gdy opór pęknie bez cofnięcia: {' / '.join(fmt(x) for x in mx)}.")
        if nc:
            lines.append(f"Kanał po pierwszym szczycie: podłoga {fmt(nc[0])}, sufit {fmt(nc[1])}, wahanie ok. {af.get('swing_pct')}% "
                         f"(we wrześniu cofnięcia rosły 6% → 8% → 12% → 18%).")
        amt = amounts_text(plan, "sell", next((x["id"] for x in plan["steps"] if x["id"] != "start" and not x.get("intermediate")), "1")) if sell else ""
    elif k == "INVALID":
        title = "PLAN UNIEWAŻNIONY – Sonic"
        lines.append(f"Zamknięcie dnia {fmt(e['close'])} poniżej poziomu unieważnienia. Scenariusz hossy wstrzymany.")
    elif k in ("DEEP_TOUCH", "DEEP_LOST"):
        z, nz = e["support"], e.get("next_support")
        nxt = f"{nz['id']} {fmt(nz['lo'])}–{fmt(nz['hi'])} ({nz['label']}; {nz['btc']})" if nz else "brak – dno cyklu"
        if k == "DEEP_TOUCH":
            title = f"Sonic w strefie wsparcia {z['id']} {fmt(z['lo'])}–{fmt(z['hi'])}"
            lines.append(f"Świeca 1h zamknięta {when}: dołek {fmt(e.get('low'))}, cena {fmt(e['price'])} – strefa: {z['label']} ({z['why']}).")
            lines.append(f"Odpowiednik: {z['btc']} Plan obowiązuje (bez unieważnienia), poziomy wyżej bez zmian." if z['btc'].endswith('.') else
                         f"Odpowiednik: {z['btc']}. Plan obowiązuje (bez unieważnienia), poziomy wyżej bez zmian.")
            lines.append("Jeśli jesteś w gotówce – to strefa do zakupu; odbicie od wsparcia typowo do starych poziomów: "
                         "0,0315 → 0,0327 → 0,0342/0,0346 → 0,0362.")
            lines.append(f"Przebicie strefy (2 zamknięcia 1h pod {fmt(z['lo'])}) → następne wsparcie: {nxt}.")
        else:
            title = f"Wsparcie {z['id']} przebite – Sonic"
            lines.append(f"Dwa zamknięcia 1h pod {fmt(z['lo'])} (ostatnie {when}, {fmt(e['price'])}). Strefa {fmt(z['lo'])}–{fmt(z['hi'])} nie utrzymała ceny.")
            lines.append(f"Następne wsparcie: {nxt}. Nie kupuj w spadającym nożu – czekaj na wejście w kolejną strefę.")
    else:
        title, lines = k, [str(e)]
    if k not in ("CHANNEL_BREAKOUT", "PRE_BREAKOUT", "UPDATE_2D"):
        amt = ""
    if k in ("SELL", "PREP_SELL"):
        amt = amounts_text(plan, "sell", t["step"])
    elif k in ("BUY", "PREP_BUY"):
        amt = amounts_text(plan, "buy", z["step"])
    elif k in ("MID_SELL", "PREP_MID"):
        amt = amounts_text(plan, "sell", e["mid"]["id"], mid=True)
    elif k == "MID_REBUY":
        amt = amounts_text(plan, "buy", e["mid"]["id"], mid=True)
    if amt:
        lines.append(amt)
    bl = btc_line(btc)
    if bl:
        lines.append(bl)
    return title, "\n".join(lines)


def mid_advice(m, t):
    out = ["To krok POŚREDNI – decyzja na gorąco: spójrz na wykres 15m/1h. Sprzedaj tylko, jeśli cena wyraźnie zawraca "
           f"(długi górny knot, kolejne niższe świece) – limit {fmt(m['limit'])}. Jeśli rośnie z impetem – trzymaj do głównego celu."]
    out.append(f"Jeśli sprzedasz: odkup {fmt(m['rebuy_lo'])}–{fmt(m['rebuy_hi'])} (limit {fmt(m['rebuy_limit'])}) "
               f"albo kupno na wybiciu po 2 zamknięciach 1h powyżej {fmt(round(m['breakout'] * 1.01, 4))}.")
    if m.get("note"):
        out.append(m["note"])
    if t:
        out.append(f"Główny cel planu: {t['label']} – limit {fmt(t['limit'])} (widełki {fmt(t['lo'])}–{fmt(t['hi'])}).")
    return out


def zone_txt(z):
    return f"{fmt(z['lo'])}–{fmt(z['hi'])}"


def _live_message_long(w, level, price, plan, btc=None, now_ts=None):
    """Alert na żywo – wersja opisowa (nieużywana od 19.11.2026)."""
    import time
    when = pl_time(now_ts or int(time.time()))
    k, t = w["kind"], w.get("target")
    L = [f"Teraz {fmt(price)} ({when}, świeca 1h w trakcie)."]
    if k == "SELL":
        if level == "near":
            title, pr, tg = f"Sonic zbliża się do sprzedaży – {t['label']}", 4, ["hourglass"]
            L[0] = L[0][:-1] + f" – {fp(abs(pct(t['lo'], price)))} pod widełkami {zone_txt(t)}."
            L.append(f"Obserwuj. Jeśli nie masz jeszcze zlecenia: sprzedaż z limitem {fmt(t['limit'])} (wariant ostrożny).")
        else:
            title, pr, tg = f"SONIC W WIDEŁKACH SPRZEDAŻY – {t['label']}", 5, ["red_circle"]
            L.append(f"Widełki {zone_txt(t)}, główny poziom {fmt(t['main'])}. Sprzedaj – limit {fmt(t['limit'])}; jeśli cena zawraca – nie czekaj.")
            nz = None
            try:
                from plan import buy_zone
                nz = buy_zone(plan, t["step"])
            except Exception:
                pass
            if nz:
                L.append(f"Potem odkup {zone_txt(nz)} (limit {fmt(nz['limit'])}) albo kupno na wybiciu po 2 zamknięciach 1h powyżej {fmt(round(nz['breakout'] * 1.01, 4))}.")
            L.append("Po zamknięciu świecy 1h przyjdzie krótkie potwierdzenie.")
    elif k == "BUY":
        z = w["zone"]
        lim = z["trigger"]
        if level == "near":
            title, pr, tg = ("Sonic zbliża się do strefy zakupu" if z["step"] == "start" else f"Sonic zbliża się do odkupu – po kroku {z['step']}"), 4, ["hourglass"]
            L[0] = L[0][:-1] + f" – {fp(pct(price, lim))} nad poziomem {fmt(lim)} (strefa {zone_txt(z)})."
            L.append(f"Obserwuj. Zlecenie kupna z limitem {fmt(z['limit'])}.")
        else:
            title, pr, tg = ("SONIC W STREFIE ZAKUPU – kup" if z["step"] == "start" else f"SONIC W STREFIE ODKUPU – po kroku {z['step']}"), 5, ["green_circle"]
            L.append(f"Strefa {zone_txt(z)}. Kup z limitem {fmt(z['limit'])}.")
            if t:
                L.append(f"Od razu potem: sprzedaż z limitem {t['label']} {fmt(t['limit'])} (widełki {zone_txt(t)}).")
            L.append("Po zamknięciu świecy 1h przyjdzie krótkie potwierdzenie.")
    elif k == "BO_WATCH":
        z = w["zone"]
        title, pr, tg = f"Sonic przebija {fmt(z['breakout'])} – możliwe kupno na wybiciu", 4, ["eyes"]
        L.append(f"Zakup w strefie {zone_txt(z)} się nie wykonał. Plan: kupno na wybiciu po 2 zamknięciach 1h powyżej {fmt(round(z['breakout'] * 1.01, 4))} "
                 f"– przyjdzie osobny sygnał. Obserwuj; możesz wcześniej ustawić stop-buy tuż nad ceną.")
    elif k == "MID":
        m = w["mid"]
        if level == "near":
            title, pr, tg = f"Krok pośredni: Sonic zbliża się do {fmt(m['main'])}", 3, ["eyes"]
            L[0] = L[0][:-1] + f" – {fp(abs(pct(m['lo'], price)))} pod strefą {fmt(m['lo'])}–{fmt(m['hi'])}."
        else:
            title, pr, tg = f"KROK POŚREDNI – Sonic w strefie {fmt(m['lo'])}–{fmt(m['hi'])}", 4, ["large_orange_diamond"]
        L += mid_advice(m, t)
    elif k == "MID_REBUY":
        m = w["mid"]
        title = f"Odkup po kroku pośrednim {m['id']}" + (" – zbliża się" if level == "near" else "")
        pr, tg = (3, ["hourglass"]) if level == "near" else (4, ["large_blue_diamond"])
        L.append(f"Dotyczy tylko sytuacji, gdy sprzedałeś na kroku pośrednim ({fmt(m['main'])}). Strefa odkupu {fmt(m['rebuy_lo'])}–{fmt(m['rebuy_hi'])}, "
                 f"limit {fmt(m['rebuy_limit'])}.")
    else:
        title, pr, tg = k, 3, []
    amt = {"SELL": lambda: amounts_text(plan, "sell", w["step"]), "BUY": lambda: amounts_text(plan, "buy", w["step"]),
           "MID": lambda: amounts_text(plan, "sell", w["step"], mid=True),
           "MID_REBUY": lambda: amounts_text(plan, "buy", w["step"], mid=True)}.get(k, lambda: "")()
    if amt:
        L.append(amt)
    bl = btc_line(btc)
    if bl:
        L.append(bl)
    return title, "\n".join(L), pr, tg


def confirm_message(e, plan, btc=None):
    """Krótkie potwierdzenie po zamknięciu świecy 1h, gdy alert na żywo już poszedł."""
    k = e["kind"]; when = pl_time(e["t"]); c = e["price"]
    if k in ("SELL", "MID_SELL"):
        z = e["target"] if k == "SELL" else e["mid"]
        name = z["label"]
        if c < z["lo"]:
            verdict = f"dotknęła widełek (szczyt {fmt(e.get('high'))}) i zamknęła się POD nimi ({fmt(c)}) – odrzucenie, sprzedaż potwierdzona."
        elif c <= z["hi"]:
            verdict = f"zamknęła się W widełkach ({fmt(c)}). Jeśli jeszcze trzymasz – sprzedawaj w widełkach."
        else:
            verdict = f"zamknęła się NAD widełkami ({fmt(c)}) – silne wybicie. Jeśli sprzedałeś: odkup albo kupno na wybiciu wg planu."
        title = f"Potwierdzenie 1h – {name}"
    elif k in ("BUY", "MID_REBUY"):
        z = e["zone"] if k == "BUY" else dict(lo=e["mid"]["rebuy_lo"], hi=e["mid"]["rebuy_hi"])
        if c < z["lo"]:
            verdict = f"zamknęła się POD strefą ({fmt(c)}) – presja podaży; nie dokładaj, kolejne wsparcia w alertach korekty rozszerzonej."
        elif c <= z["hi"]:
            verdict = f"zamknęła się w strefie ({fmt(c)}, dołek {fmt(e.get('low'))})."
        else:
            verdict = f"zamknęła się nad strefą ({fmt(c)}, dołek {fmt(e.get('low'))}) – popyt odbił cenę."
        title = "Potwierdzenie 1h – zakup" if k == "BUY" else f"Potwierdzenie 1h – odkup po {e['mid']['id']}"
    else:
        return message(e, plan, btc)
    lines = [f"Świeca 1h zamknięta {when} " + verdict]
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


def send_event(e, plan, btc=None, click=None, dry_run=False, confirm=False, state=None):
    if confirm:
        title, body = confirm_message(e, plan, btc)
        return send(title, body, PRIORITY["CONFIRM"], TAGS["CONFIRM"], click, dry_run), title, body
    title, body = message(e, plan, btc, state)
    return send(title, body, PRIORITY.get(e["kind"], 3), TAGS.get(e["kind"]), click, dry_run), title, body


# ----------------------------------------------------------------- zwięzłe komunikaty (od 19.11.2026)
# Każdy sygnał: co ustawić (sprzedaż / odkup z widełkami), tunel bieżącego stopnia (od–do, %), kwoty, BTC.

def _step_card_data(plan, sid):
    """Dane stopnia: sprzedaż i odkup (limity ostrożne + widełki), wybicie."""
    from plan import step_by_id, mid_info, sell_zone, buy_zone
    s = step_by_id(plan, sid)
    if s.get("intermediate"):
        m = mid_info(s)
        return dict(id=sid, mid=True, name=f"stopień {sid} (pośredni)", s_lo=m["lo"], s_hi=m["hi"], s_lim=m["limit"],
                    b_lo=m["rebuy_lo"], b_hi=m["rebuy_hi"], b_lim=m["rebuy_limit"], bo=m["breakout"])
    t, z = sell_zone(plan, sid), buy_zone(plan, sid)
    d = dict(id=sid, mid=False, name=f"stopień {sid}", s_lo=t["lo"], s_hi=t["hi"], s_lim=t["limit"],
             b_lo=None, b_hi=None, b_lim=None, bo=None, final=t.get("final"))
    if z:
        d.update(b_lo=z["lo"], b_hi=z["hi"], b_lim=z["limit"], bo=z.get("breakout"))
    return d


def _amounts_row(plan, sid):
    try:
        from plan import capital_path
        return next((r for r in capital_path(plan, True) if r["step"] == sid), None)
    except Exception:
        return None


def card_lines(plan, sid, sell_first=True):
    """Zwięzła karta stopnia: SPRZEDAŻ / ODKUP / tunel / wybicie / kwota."""
    d = _step_card_data(plan, sid)
    L = [f"SPRZEDAŻ {fmt(d['s_lim'])}  (widełki {fmt(d['s_lo'])}–{fmt(d['s_hi'])})"]
    if d["b_lim"]:
        L.append(f"ODKUP {fmt(d['b_lim'])}  (widełki {fmt(d['b_lo'])}–{fmt(d['b_hi'])})")
        L.append(f"Tunel {d['name']}: {fmt(d['b_lim'])} – {fmt(d['s_lim'])} ({fp(pct(d['s_lim'], d['b_lim']), True)})")
        if d["bo"]:
            L.append(f"Bez korekty: odkup na wybiciu po 2 zamkn. 1h > {fmt(round(d['bo'] * 1.01, 4))}")
    elif d.get("final"):
        L.append("To finał planu – potem gotówka.")
    r = _amounts_row(plan, sid)
    if r:
        L.append(f"Kwota wg planu: {fsn(r['S_in'])} → {fu(r['usd'])}" + (f" → {fsn(r['S'])}" if r.get("S") else ""))
    if not sell_first:
        L = L[1:2] + L[:1] + L[2:]
    return L


def next_step_id(state):
    """Najbliższy stopień do zagrania w bieżącym stanie planu."""
    for m in state.get("mids") or []:
        if m["status"] == "pending":
            return m["id"]
    t = state.get("target")
    return t["step"] if t else None


def state_card(plan, state):
    """Karta dla sygnałów informacyjnych (wsparcia, StochRSI): co ustawić teraz wg planu."""
    if not state:
        return []
    if state["phase"] == "BUY" and state.get("zone"):
        z = state["zone"]
        L = [f"KUP {fmt(z['limit'])}  (strefa {fmt(z['lo'])}–{fmt(z['hi'])})"]
        t = state.get("target")
        if t:
            L.append(f"Potem SPRZEDAŻ {fmt(t['limit'])}  (widełki {fmt(t['lo'])}–{fmt(t['hi'])})")
            L.append(f"Tunel: {fmt(z['limit'])} – {fmt(t['limit'])} ({fp(pct(t['limit'], z['limit']), True)})")
        return L
    sid = next_step_id(state)
    if not sid:
        return []
    touched = [m for m in state.get("mids") or [] if m["status"] == "touched"]
    L = []
    for m in touched:
        L.append(f"Po kroku {m['id']} (jeśli sprzedałeś): ODKUP {fmt(m['rebuy_limit'])} (widełki {fmt(m['rebuy_lo'])}–{fmt(m['rebuy_hi'])})")
    return L + ["Następny stopień:"] + card_lines(plan, sid)


def _btc_short(btc):
    if not btc or not btc.get("price"):
        return ""
    p = btc["price"]
    out = f"BTC {fmt(p)}"
    if btc.get("below"):
        out += f" | wsp. {fmt(btc['below']['price'])}"
    if btc.get("above"):
        out += f" | opór {fmt(btc['above']['price'])}"
    return out


def _after_mid_id(plan, state, mid_id):
    """Stopień po kroku pośrednim mid_id: kolejny pośredni (jeśli jest i czeka) albo cel główny."""
    ids = [m["id"] for m in (state or {}).get("mids") or []]
    if mid_id in ids:
        for m in (state.get("mids") or [])[ids.index(mid_id) + 1:]:
            if m["status"] != "done":
                return m["id"]
    t = (state or {}).get("target")
    return t["step"] if t else None


def message(e, plan, btc=None, state=None):
    """Zwięzły komunikat dla zdarzenia z plan.replay(): ceny do ustawienia + tunel stopnia."""
    k = e["kind"]; t = e.get("target"); z = e.get("zone")
    when = pl_time(e["t"]); c = e["price"]
    head = f"Sonic {fmt(c)} (świeca 1h {when})"
    L = []
    if k in ("PREP_MID", "MID_SELL"):
        m = e["mid"]
        title = (f"Ustaw SPRZEDAŻ {fmt(m['limit'])} – krok {m['id']}" if k == "PREP_MID" else f"SPRZEDAJ {fmt(m['limit'])} – krok {m['id']}")
        L = [head] + card_lines(plan, m["id"]) + ["Pośredni: sprzedaj, gdy cena zawraca; przy impecie trzymaj."]
    elif k == "MID_REBUY":
        m = e["mid"]
        title = f"ODKUP {fmt(m['rebuy_limit'])} – po kroku {m['id']}"
        L = [head, f"Jeśli sprzedałeś na {fmt(m['limit'])}: ODKUP {fmt(m['rebuy_limit'])} (widełki {fmt(m['rebuy_lo'])}–{fmt(m['rebuy_hi'])})."]
        nx = _after_mid_id(plan, state, m["id"]) or (t or {}).get("step")
        if nx:
            L += ["Następny stopień:"] + card_lines(plan, nx)
    elif k == "MID_BREAKOUT":
        m = e["mid"]
        title = f"KUP NA WYBICIU ok. {fmt(c)} – po kroku {m['id']}"
        L = [head, f"Jeśli sprzedałeś na {fmt(m['limit'])}: 2 zamkn. 1h > {fmt(m['breakout'])} – odkup teraz, odkup na dołku się nie wykonał."]
        nx = _after_mid_id(plan, state, m["id"]) or (t or {}).get("step")
        if nx:
            L += ["Następny stopień:"] + card_lines(plan, nx)
    elif k in ("SELL", "PREP_SELL"):
        title = (f"SPRZEDAJ {fmt(t['limit'])} – {t['label']}" if k == "SELL" else f"Ustaw SPRZEDAŻ {fmt(t['limit'])} – {t['label']}")
        L = [head] + card_lines(plan, t["step"])
    elif k in ("BUY", "PREP_BUY", "BUY_BREAKOUT"):
        lim = z["limit"] if k != "BUY_BREAKOUT" else c
        title = {"BUY": f"KUP {fmt(lim)}", "PREP_BUY": f"Ustaw KUPNO {fmt(lim)}", "BUY_BREAKOUT": f"KUP NA WYBICIU ok. {fmt(c)}"}[k]
        title += " – " + ("strefa akumulacji" if z["step"] == "start" else f"odkup po kroku {z['step']}")
        L = [head]
        if k == "BUY_BREAKOUT":
            L.append(f"Odkup na dołku się nie wykonał: 2 zamkn. 1h > {fmt(e['level'])}.")
        else:
            L.append(f"KUP {fmt(z['limit'])}  (strefa {fmt(z['lo'])}–{fmt(z['hi'])})")
        if t:
            L += ["Następny stopień:"] + card_lines(plan, next_step_id(state) if state and state.get("phase") == "HOLD" else t["step"])
    elif k in ("DEEP_TOUCH", "DEEP_LOST"):
        sp, nz = e["support"], e.get("next_support")
        if k == "DEEP_TOUCH":
            title = f"Sonic {fmt(c)} w strefie wsparcia {sp['id']} {fmt(sp['lo'])}–{fmt(sp['hi'])}"
        else:
            title = f"Wsparcie {sp['id']} {fmt(sp['lo'])}–{fmt(sp['hi'])} przebite – Sonic {fmt(c)}"
        L = [head, f"{sp['label']} ({sp['btc']})."]
        if nz:
            L.append(f"Niżej: {nz['id']} {fmt(nz['lo'])}–{fmt(nz['hi'])} ({nz['btc']})" +
                     (f"; przebicie {sp['id']} = 2 zamkn. 1h < {fmt(sp['lo'])}" if k == "DEEP_TOUCH" else ""))
        L += state_card(plan, state)
    elif k == "STEP_FAIL":
        sup = e.get("support") or {}
        title = f"Odkup zawiódł – Sonic {fmt(c)}"
        L = [head, f"2 zamkn. 1h < {fmt(z['lo'])}. Nie dokupuj. Następne wsparcie: {fmt(sup.get('lo'))}–{fmt(sup.get('hi'))}."]
        L += state_card(plan, state)
    else:
        return _message_long(e, plan, btc)
    b = _btc_short(btc)
    if b:
        L.append(b)
    return title, "\n".join(L)


def live_message(w, level, price, plan, btc=None, now_ts=None, state=None):
    """Alert na żywo (świeca 1h w trakcie) – zwięźle. Zwraca (tytuł, treść, priorytet, tagi)."""
    import time
    when = pl_time(now_ts or int(time.time()))
    k, t = w["kind"], w.get("target")
    head = f"Sonic {fmt(price)} (teraz, {when})"
    near = level == "near"
    if k == "SELL":
        title = (f"Blisko SPRZEDAŻY {fmt(t['limit'])} – {t['label']}" if near else f"SPRZEDAJ {fmt(t['limit'])} – cena w widełkach")
        pr, tg = (4, ["hourglass"]) if near else (5, ["red_circle"])
        L = [head] + card_lines(plan, t["step"])
    elif k == "MID":
        m = w["mid"]
        title = (f"Blisko SPRZEDAŻY {fmt(m['limit'])} – krok {m['id']}" if near else f"SPRZEDAJ {fmt(m['limit'])} – krok {m['id']}")
        pr, tg = (3, ["eyes"]) if near else (4, ["large_orange_diamond"])
        L = [head] + card_lines(plan, m["id"]) + ["Pośredni: sprzedaj, gdy cena zawraca; przy impecie trzymaj."]
    elif k == "MID_REBUY":
        m = w["mid"]
        title = (f"Blisko ODKUPU {fmt(m['rebuy_limit'])} – po kroku {m['id']}" if near else f"ODKUP {fmt(m['rebuy_limit'])} – po kroku {m['id']}")
        pr, tg = (3, ["hourglass"]) if near else (4, ["large_blue_diamond"])
        L = [head, f"Jeśli sprzedałeś na {fmt(m['limit'])}: ODKUP {fmt(m['rebuy_limit'])} (widełki {fmt(m['rebuy_lo'])}–{fmt(m['rebuy_hi'])})."]
        nx = _after_mid_id(plan, state, m["id"]) or (t or {}).get("step")
        if nx:
            L += ["Następny stopień:"] + card_lines(plan, nx)
    elif k == "BUY":
        z = w["zone"]
        title = (f"Blisko KUPNA {fmt(z['limit'])}" if near else f"KUP {fmt(z['limit'])} – cena w strefie")
        pr, tg = (4, ["hourglass"]) if near else (5, ["green_circle"])
        L = [head, f"KUP {fmt(z['limit'])}  (strefa {fmt(z['lo'])}–{fmt(z['hi'])})"]
        if t:
            L += ["Następny stopień:"] + card_lines(plan, t["step"])
    elif k == "BO_WATCH":
        z = w["zone"]
        title, pr, tg = f"Sonic przebija {fmt(z['breakout'])} – możliwe kupno na wybiciu", 4, ["eyes"]
        L = [head, f"Kupno na wybiciu po 2 zamkn. 1h > {fmt(round(z['breakout'] * 1.01, 4))} – przyjdzie osobny sygnał."]
    else:
        return _live_message_long(w, level, price, plan, btc, now_ts)
    b = _btc_short(btc)
    if b:
        L.append(b)
    return title, "\n".join(L), pr, tg
