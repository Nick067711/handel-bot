"""Sygnały wahadłowe Sonica (SPRZEDAŻ na szczycie odbicia / ODKUP na niższym dołku) – świece 2h.

Zasady (sprawdzone na historii Sonica 02.2025–11.2026, skrypty w opisie commita):
- Fale krótkoterminowe liczone zygzakiem 8% na świecach 2h (szczyt potwierdzony, gdy cena spadnie 8% od niego; dołek odwrotnie).
- SPRZEDAŻ (gdy trzymasz Sonica):
    * cena ≥ cena odniesienia (ostatni zakup) × (1 + min_gain) – sygnał tylko przy wypracowanym zysku,
    * wykupienie: RSI(14) 2h ≥ rsi_hi w ostatnich 6 świecach, StochRSI(14,14,3,3) było ≥ k_hi i właśnie przecina linię D w dół,
    * opis: „drugi niższy szczyt” (odbicie pod poprzedni szczyt fali), „mocne wykupienie” (RSI ≥ rsi_x) albo „szczyt odbicia”.
- ODKUP (gdy masz gotówkę):
    * cena ≤ cena sprzedaży × (1 − min_drop),
    * niższy dołek: cena zeszła pod ostatni potwierdzony dołek fali,
    * wyprzedanie: RSI(14) 2h ≤ rsi_lo w ostatnich 6 świecach, StochRSI było ≤ k_lo i przecina D w górę.
- Informacyjnie (nie zmienia stanu): przy gotówce zamknięcie 2h ponad ostatni szczyt fali × (1 + breakout) – „wybicie”.

Stan odtwarzany deterministycznie od swing.start (faza, cena odniesienia, liczba S), więc każde uruchomienie
bota daje te same zdarzenia; nowe są wysyłane raz (klucz rodzaj:czas).
"""
import json, urllib.request, urllib.parse

API = "https://data-api.binance.vision/api/v3/klines"


def fetch_1h(symbol, limit=1000):
    q = urllib.parse.urlencode({"symbol": symbol, "interval": "1h", "limit": limit})
    with urllib.request.urlopen(f"{API}?{q}", timeout=30) as r:
        d = json.load(r)
    c = [dict(t=int(k[0]) // 1000, o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), v=float(k[5])) for k in d]
    return c[:-1]          # bez bieżącej, niezamkniętej świecy


def resample(c, hours):
    out, cur = [], None
    for x in c:
        b = x["t"] - x["t"] % (hours * 3600)
        if not cur or cur["t"] != b:
            if cur:
                out.append(cur)
            cur = dict(t=b, o=x["o"], h=x["h"], l=x["l"], c=x["c"], v=x["v"], n=1)
        else:
            cur["h"] = max(cur["h"], x["h"]); cur["l"] = min(cur["l"], x["l"]); cur["c"] = x["c"]; cur["v"] += x["v"]; cur["n"] += 1
    if cur and cur["n"] == hours:
        out.append(cur)
    return [x for x in out if x["n"] == hours]


def rsi(c, n=14):
    out, g, l = [None] * len(c), 0.0, 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        if i <= n:
            g += max(d, 0); l += max(-d, 0)
            if i == n:
                g /= n; l /= n
                out[i] = 100 - 100 / (1 + g / l) if l else 100
        else:
            g = (g * (n - 1) + max(d, 0)) / n
            l = (l * (n - 1) + max(-d, 0)) / n
            out[i] = 100 - 100 / (1 + g / l) if l else 100
    return out


def stoch(c, rl=14, sl=14, k=3, d=3):
    r = rsi(c, rl)
    raw = [None] * len(c)
    for i in range(len(c)):
        w = [x for x in r[max(0, i - sl + 1):i + 1] if x is not None]
        if len(w) == sl:
            lo, hi = min(w), max(w)
            raw[i] = (r[i] - lo) / (hi - lo) * 100 if hi > lo else 50.0

    def sma(a, n, i):
        w = a[i - n + 1:i + 1] if i >= n - 1 else []
        return sum(w) / n if w and all(x is not None for x in w) else None
    K = [sma(raw, k, i) for i in range(len(c))]
    D = [sma(K, d, i) for i in range(len(c))]
    return K, D


def replay(cfg, c, start_ts):
    """Zwraca (zdarzenia, stan końcowy). c – świece interwału cfg['tf_hours'] (zamknięte)."""
    th, lb, fee = cfg["zigzag"], cfg["lookback"], cfg["fee"]
    sec = cfg["tf_hours"] * 3600
    C = [x["c"] for x in c]
    R = rsi(C)
    K, D = stoch(C)
    st = cfg["start"]
    state = st["phase"]                    # HOLD = masz Sonica, CASH = masz gotówkę
    ref = st["ref_price"]
    S = float(st.get("S") or 0)
    cash = float(st.get("cash") or 0)
    mode, ext, ei = "up", c[0]["h"], 0
    tops, bots, ev = [], [], []
    brk_done = set()
    for i in range(1, len(c)):
        x = c[i]
        if mode == "up":
            if x["h"] > ext:
                ext, ei = x["h"], i
            elif x["l"] <= ext * (1 - th):
                tops.append((ei, ext)); mode, ext, ei = "down", x["l"], i
        else:
            if x["l"] < ext:
                ext, ei = x["l"], i
            elif x["h"] >= ext * (1 + th):
                bots.append((ei, ext)); mode, ext, ei = "up", x["h"], i
        t_close = x["t"] + sec
        if t_close <= start_ts or i <= lb:
            continue
        k0, k1, d0, d1 = K[i - 1], K[i], D[i - 1], D[i]
        if None in (k0, k1, d0, d1) or R[i] is None:
            continue
        w = range(i - lb, i + 1)
        kw = [K[j] for j in w if K[j] is not None]
        rw = [R[j] for j in w if R[j] is not None]
        price = C[i]
        base = dict(t=t_close, price=price, rsi=R[i], k=k1, ref=ref,
                    last_top=tops[-1][1] if tops else None, last_bot=bots[-1][1] if bots else None)
        if state == "HOLD":
            if price < ref * (1 + cfg["min_gain"]):
                continue
            if not (k0 >= d0 and k1 < d1 and max(kw) >= cfg["k_hi"] and max(rw) >= cfg["rsi_hi"]):
                continue
            hi = max(c[j]["h"] for j in w)
            why = "szczyt odbicia"
            if tops and bots and bots[-1][0] > tops[-1][0] and mode == "up":
                T1, B1 = tops[-1][1], bots[-1][1]
                if B1 + cfg["retrace"] * (T1 - B1) <= hi <= T1 * 1.02:
                    why = "drugi niższy szczyt"
            if why == "szczyt odbicia" and max(rw) >= cfg["rsi_x"]:
                why = "mocne wykupienie"
            cash = S * price * (1 - fee)
            ev.append(dict(base, kind="SWING_SELL", why=why, S_before=S, cash_after=cash, rsi_max=max(rw), gain=price / ref - 1))
            S, state, ref = 0.0, "CASH", price
        else:
            if price <= ref * (1 - cfg["min_drop"]) and k0 <= d0 and k1 > d1 and min(kw) <= cfg["k_lo"] and min(rw) <= cfg["rsi_lo"]:
                prev_low = bots[-1][1] if bots else None
                lo = min(c[j]["l"] for j in w)
                lower = (prev_low is not None and lo < prev_low) or (mode == "down" and prev_low is not None and ext < prev_low)
                if lower:
                    S = cash / price * (1 - fee)
                    ev.append(dict(base, kind="SWING_BUY", why="niższy dołek", cash_before=cash, S_after=S, rsi_min=min(rw),
                                   prev_low=prev_low, drop=price / ref - 1))
                    cash, state, ref = 0.0, "HOLD", price
                    continue
            if cfg.get("breakout") and tops and price >= tops[-1][1] * (1 + cfg["breakout"]) and tops[-1][0] not in brk_done and price > ref:
                brk_done.add(tops[-1][0])
                ev.append(dict(base, kind="SWING_BREAKOUT", top=tops[-1][1]))
    return ev, dict(phase=state, ref=ref, S=S, cash=cash, last_top=tops[-1][1] if tops else None,
                    last_bot=bots[-1][1] if bots else None)


def _f(x):
    s = f"{x:.5f}".rstrip("0") if x < 0.1 else f"{x:.4f}"
    return s.replace(".", ",")


def _p(x):
    return f"{x * 100:+.1f}%".replace(".", ",")


def _n(x):
    return f"{x:,.0f}".replace(",", " ")


def message(e, cfg, pl_time):
    tf = f"{cfg['tf_hours']}h"
    when = pl_time(e["t"])
    if e["kind"] == "SWING_SELL":
        title = f"Sonic: SPRZEDAŻ – {e['why']} ({_f(e['price'])})"
        lines = [f"Świeca {tf} zamknięta {when}: cena {_f(e['price'])}, RSI {e['rsi']:.0f} (max {e['rsi_max']:.0f}), StochRSI {e['k']:.0f} – zawraca w dół.",
                 f"Zysk względem zakupu {_f(e['ref'])}: {_p(e['gain'])}.",
                 f"{_n(e['S_before'])} S ≈ {_n(e['cash_after'])} $ po prowizji."]
        nxt = e["price"] * (1 - cfg["min_drop"])
        lb = f" i pod ostatni dołek fali {_f(e['last_bot'])}" if e.get("last_bot") else ""
        lines.append(f"Odkup: dopiero przy cenie ≤ {_f(nxt)}{lb}, gdy RSI się odwróci (osobny alert).")
    elif e["kind"] == "SWING_BUY":
        title = f"Sonic: ODKUP – niższy dołek ({_f(e['price'])})"
        lines = [f"Świeca {tf} zamknięta {when}: cena {_f(e['price'])}, RSI {e['rsi']:.0f} (min {e['rsi_min']:.0f}), StochRSI {e['k']:.0f} – zawraca w górę.",
                 f"Pod poprzednim dołkiem fali {_f(e['prev_low'])}; {_p(e['drop'])} względem sprzedaży {_f(e['ref'])}.",
                 f"{_n(e['cash_before'])} $ ≈ {_n(e['S_after'])} S po prowizji."]
        lines.append(f"Kolejna sprzedaż: dopiero od {_f(e['price'] * (1 + cfg['min_gain']))} przy wykupieniu (osobny alert).")
    else:
        title = f"Sonic: wybicie ponad szczyt fali {_f(e['top'])} – uwaga"
        lines = [f"Świeca {tf} zamknięta {when}: cena {_f(e['price'])} ponad ostatnim szczytem fali {_f(e['top'])}.",
                 f"Masz gotówkę ze sprzedaży po {_f(e['ref'])}. Mocne wybicie bywa początkiem większego ruchu – rozważ, czy czekać na odkup niżej."]
    lines.append("Jeśli nie wykonałeś transakcji, napisz – zaktualizuję stan bota.")
    return title, "\n".join(lines)
