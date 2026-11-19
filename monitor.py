# -*- coding: utf-8 -*-
"""
Monitoring BTC (prawdziwe / fałszywe odbicie) i NEAR (oderwany wzrost) – alerty ntfy.

Wszystkie statystyki w komunikatach pochodzą z testów na historii Binance (19.11.2026), liczone w % od punktu
odniesienia danego przypadku (dołek, szczyt) i przenoszone na bieżące ceny:
  * BTC 15m: dwa szczyty (drugi równy lub wyższy, do 0,4% niżej) przy RSI(14) niższym o ≥5 pkt, potem zamknięcie 15m
    pod dołkiem między szczytami – w bessie 165 przypadków: nowy szczyt w 12 h 45%, spadek ≥1% 43%, ≥2% 15%;
    NEAR w tych samych 12 h spadał o ≥3% w 34% przypadków.
  * BTC po świeżym 20-dniowym dołku w bessie (54 przypadki od 2017): +4,7% od dołka w 1. dniu → dalej do +9,6% w 56%;
    brak +4,7% w 1. dniu → najpierw nowy dołek w 30 z 35 (86%).
  * NEAR od 12.10.2026: 9 z 9 wzrostów ≥10% oderwanych od BTC wróciło pod start wzrostu (typowo po ok. 25 h).
"""
import json, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

API = "https://data-api.binance.vision/api/v3/klines"


def klines(sym, iv, limit=500):
    url = API + "?" + urllib.parse.urlencode(dict(symbol=sym, interval=iv, limit=limit))
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "handel-bot"}), timeout=20) as r:
        d = json.loads(r.read().decode())
    sec = {"15m": 900, "1h": 3600, "1d": 86400}[iv]
    now = time.time()
    rows = [dict(t=int(k[0]) // 1000, o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4])) for k in d]
    return [x for x in rows if x["t"] + sec <= now]          # tylko zamknięte świece


def rsi(c, n=14):
    g = l = 0.0
    o = [50.0] * len(c)
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        gi, li = max(d, 0), max(-d, 0)
        if i <= n:
            g += gi / n; l += li / n
        else:
            g = (g * (n - 1) + gi) / n; l = (l * (n - 1) + li) / n
        if i >= n:
            o[i] = 100 - 100 / (1 + g / l) if l else 100
    return o


def pl(ts):
    d = datetime.fromtimestamp(ts, timezone.utc)
    summer = 3 < d.month < 10 or (d.month == 3 and d.day >= 25) or (d.month == 10 and d.day < 25)
    return (d + timedelta(hours=2 if summer else 1)).strftime("%d.%m %H:%M")


def f(x):
    return (f"{x:,.0f}".replace(",", " ") if x >= 1000 else (f"{x:.3f}" if x >= 1 else f"{x:.4f}")).replace(".", ",")


def btc_divergence(b15):
    """Ostatni sygnał: 2 szczyty 15m + RSI niżej o ≥5 + zamknięcie pod szyją. Zwraca dict albo None."""
    H = [x["h"] for x in b15]; L = [x["l"] for x in b15]; C = [x["c"] for x in b15]
    R = rsi(C)
    n = len(C)
    piv = [i for i in range(6, n - 2) if H[i] == max(H[i - 6:min(n, i + 7)])]
    for k in range(len(piv) - 1, -1, -1):
        p2 = piv[k]
        if p2 < n - 20:
            break
        for p1 in reversed(piv[:k]):
            if p2 - p1 > 48:
                break
            if p2 - p1 < 4:
                continue
            if H[p2] >= H[p1] * 0.996 and R[p2] <= R[p1] - 5 and R[p1] >= 60:
                neck = min(L[p1:p2 + 1])
                for j in range(p2 + 1, n):
                    if H[j] > max(H[p1], H[p2]):
                        break
                    if C[j] < neck:
                        if j >= n - 2:                       # świeża świeca (ostatnie 30 min)
                            return dict(p1=b15[p1], p2=b15[p2], r1=R[p1], r2=R[p2], neck=neck, at=b15[j])
                        break
                break
    return None


def check(cfg, sent):
    """Zwraca listę (klucz, tytuł, treść, priorytet, tagi) – tylko nowe alerty."""
    out = []
    if not cfg or not cfg.get("enabled"):
        return out
    near_sym = cfg.get("near_symbol", "NEARUSDT")
    b1d = klines("BTCUSDT", "1d", 60)
    b1h = klines("BTCUSDT", "1h", 200)
    price = b1h[-1]["c"]
    lows = [x["l"] for x in b1d]
    # świeży 20-dniowy dołek (dzień, w którym padł)
    # świeży dołek: ostatni zamknięty dzień, którego dołek jest najniższy (z tolerancją 0,5% – retest) z 20 poprzednich dni
    lo_i = max(i for i in range(20, len(b1d)) if lows[i] <= min(lows[i - 20:i]) * 1.005) if len(b1d) > 21 else len(b1d) - 1
    lo, lo_day = lows[lo_i], b1d[lo_i]["t"]
    lvl1, lvl2 = lo * 1.047, lo * 1.096
    ctx = f"BTC {f(price)} | ostatni dołek {f(lo)} ({pl(lo_day)[:5]}) | +4,7%: {f(lvl1)} | +9,6%: {f(lvl2)}"

    # --- BTC: zasada 1. dnia po dołku (sprawdzane po zamknięciu dnia)
    if cfg.get("btc_structure", True) and lo_i == len(b1d) - 2:
        d1 = b1d[-1]                                   # dzień 1 po dołku (zamknięty)
        key = f"BTC_DAY1:{lo_day}"
        if key not in sent:
            if d1["h"] >= lvl1:
                out.append((key, f"BTC: dzień po dołku doszedł do +4,7% ({f(lvl1)})",
                            f"Najwyżej {f(d1['h'])}, zamknięcie {f(d1['c'])}.\nHistoria (54 dołki w bessie): po takim 1. dniu dalej do +9,6% ({f(lvl2)}) w 56%.\n{ctx}",
                            4, ["chart_with_upwards_trend"]))
            else:
                out.append((key, "BTC: dzień po dołku BEZ +4,7% – częściej nowy dołek",
                            f"Najwyżej {f(d1['h'])} (cel {f(lvl1)} nie padł), zamknięcie {f(d1['c'])}.\nHistoria: w 30 z 35 takich przypadków (86%) najpierw padał nowy dołek.\n{ctx}",
                            4, ["warning"]))
    # --- BTC: dno pęka (zamknięcie 1h >1% pod dołkiem 20 dni sprzed tej świecy)
    if cfg.get("btc_structure", True):
        today0 = (int(time.time()) // 86400) * 86400
        prev_lo = min([min(lows[-20:])] + [x["l"] for x in b1h[:-1] if x["t"] >= today0])
        last = b1h[-1]
        if last["c"] < prev_lo * 0.99:
            key = f"BTC_BREAK:{round(prev_lo)}"
            if key not in sent:
                out.append((key, f"BTC: dno {f(prev_lo)} pękło – zamknięcie 1h {f(last['c'])}",
                            f"Świeca {pl(last['t'])}. Historia (18 przypadków od 2025): NEAR w 48 h spadał dalej typowo o 5,5%, w 5 z 18 o ≥10%.\n{ctx}",
                            5, ["rotating_light"]))
    # --- BTC 15m: dywergencja + zamknięcie pod szyją
    if cfg.get("btc_div_15m", True):
        b15 = klines("BTCUSDT", "15m", 300)
        d = btc_divergence(b15)
        if d:
            key = f"BTC_DIV15:{d['p2']['t']}"
            if key not in sent:
                out.append((key, f"BTC 15m: spadek momentum na szczycie {f(d['p2']['h'])}",
                            f"Szczyty {f(d['p1']['h'])} ({pl(d['p1']['t'])[-5:]}, RSI {d['r1']:.0f}) i {f(d['p2']['h'])} ({pl(d['p2']['t'])[-5:]}, RSI {d['r2']:.0f}); "
                            f"zamknięcie 15m {f(d['at']['c'])} pod dołkiem między nimi {f(d['neck'])}.\n"
                            f"Historia (bessa, 165 przypadków): w 12 h nowy szczyt 45%, spadek ≥1% 43%, ≥2% 15%; NEAR ≥3% w dół 34%.\n{ctx}",
                            4, ["eyes"]))
    # --- NEAR: oderwany wzrost (start wzrostu, szczyt, BTC w tym czasie)
    if cfg.get("near_fade", True):
        n1h = klines(near_sym, "1h", 120)
        if n1h:
            seg = n1h[-72:]
            bi = min(range(len(seg)), key=lambda i: seg[i]["l"])
            base = seg[bi]["l"]
            pk_i = max(range(bi, len(seg)), key=lambda i: seg[i]["h"])
            peak = seg[pk_i]["h"]
            nprice = seg[-1]["c"]
            bt = {x["t"]: x["c"] for x in b1h}
            b0, b1 = bt.get(seg[bi]["t"]), bt.get(seg[pk_i]["t"])
            if peak / base - 1 >= 0.10 and b0 and b1 and b1 / b0 - 1 < 0.04 and nprice <= peak * 0.98:
                key = f"NEAR_FADE:{seg[bi]['t']}"
                if key not in sent:
                    out.append((key, f"NEAR: oderwany wzrost +{(peak / base - 1) * 100:.0f}% zawraca ({f(nprice)})",
                                f"Start wzrostu {f(base)} ({pl(seg[bi]['t'])}) → szczyt {f(peak)} ({pl(seg[pk_i]['t'])}); BTC w tym czasie {f"{(b1 / b0 - 1) * 100:+.1f}".replace(".", ",")}%.\n"
                                f"Od 12.10.2026: 9 z 9 takich wzrostów wróciło pod start (typowo po ok. 25 h; spadek od szczytu 21–33%).\n"
                                f"Cele: start wzrostu {f(base)} ({(base / nprice - 1) * 100:+.0f}%), −25% od szczytu {f(peak * 0.75)}.\n{ctx}",
                                4, ["chart_with_downwards_trend"]))
    return out
