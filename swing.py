"""Sygnały wahadłowe Sonica do akumulacji w trendzie spadkowym – świece 1h (szybki sygnał) w kontekście 2h.

Zasady (backtest Sonica od szczytu 0,0739 z 13.10.2026 do 22.11.2026: 12 transakcji, liczba S ×2,04 mimo spadku ceny
o ok. 64%; okna kontrolne: 05–06.2026 ×1,52, 12.2025–04.2026 ×1,85):
- Fale liczone zygzakiem 8% na świecach 2h. Trend: wyższy ostatni dołek fali niż poprzedni = „po tymczasowym dnie”
  (wyższe dołki), niższy = trend spadkowy.
- SPRZEDAŻ (masz Sonica): RSI(14) 2h ≥ rsi_hi w ostatnich 7 świecach 2h oraz zawrócenie StochRSI(14,14,3,3) w dół:
  na 2h (z ≥ k_hi) albo szybciej na 1h (z ≥ 80) przy StochRSI 2h ≥ k2_ctx_hi. Bez wymogu zysku względem zakupu
  (min_gain = null) – zysk liczy się w liczbie Soników przy odkupie niżej.
- ODKUP (masz gotówkę): cena ≤ sprzedaż × (1 − min_drop), RSI(14) 2h ≤ rsi_lo, zawrócenie StochRSI w górę (2h z ≤ k_lo
  albo 1h z ≤ 20 przy StochRSI 2h ≤ k2_ctx_lo) oraz niższy dołek (pod ostatni dołek fali), a po tymczasowym dnie także
  wyższy dołek po cofnięciu ≥ pullback od szczytu fali.
- Informacyjnie: przy gotówce zamknięcie 1h ponad ostatni szczyt fali × (1 + breakout) – „wybicie”.

Stan odtwarzany deterministycznie od swing.start, więc każde uruchomienie bota daje te same zdarzenia;
nowe są wysyłane raz (klucz rodzaj:czas).
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


def _cross(K, D, k, lb, down, lvl):
    if k < lb + 1 or None in (K[k - 1], K[k], D[k - 1], D[k]):
        return False
    kw = [K[q] for q in range(k - lb, k + 1) if K[q] is not None]
    if down:
        return K[k - 1] >= D[k - 1] and K[k] < D[k] and max(kw) >= lvl
    return K[k - 1] <= D[k - 1] and K[k] > D[k] and min(kw) <= lvl


def replay(cfg, c1, start_ts):
    """c1 – zamknięte świece 1h. Zwraca (zdarzenia, stan końcowy)."""
    tfh = cfg["tf_hours"]; sec2 = tfh * 3600
    c2 = resample(c1, tfh)
    C1 = [x["c"] for x in c1]; C2 = [x["c"] for x in c2]
    K1, D1 = stoch(C1); R2 = rsi(C2); K2, D2 = stoch(C2)
    th, fee, lb = cfg["zigzag"], cfg["fee"], cfg["lookback"]
    st = cfg["start"]
    state, ref = st["phase"], st["ref_price"]
    S, cash = float(st.get("S") or 0), float(st.get("cash") or 0)
    mode, ext, ei = "up", (c2[0]["h"] if c2 else 0), 0
    tops, bots, ev, brk_done, trend, j2 = [], [], [], set(), "down", -1
    blow, ntrig = None, 0          # dołek bieżącego odbicia i liczba sygnałów wykupienia od niego
    for i in range(lb + 1, len(c1)):
        t = c1[i]["t"] + 3600
        while j2 + 1 < len(c2) and c2[j2 + 1]["t"] + sec2 <= t:
            j2 += 1; x = c2[j2]
            if mode == "up":
                if x["h"] > ext:
                    ext, ei = x["h"], j2
                elif x["l"] <= ext * (1 - th):
                    tops.append((ei, ext)); mode, ext, ei = "down", x["l"], j2
            else:
                if x["l"] < ext:
                    ext, ei = x["l"], j2
                elif x["h"] >= ext * (1 + th):
                    bots.append((ei, ext)); mode, ext, ei = "up", x["h"], j2
                    if len(bots) >= 2:
                        trend = "up" if bots[-1][1] > bots[-2][1] else "down"
        if j2 < 20 or t <= start_ts:
            continue
        price = C1[i]
        on2 = c2[j2]["t"] + sec2 == t
        x2dn = on2 and _cross(K2, D2, j2, lb, True, cfg["k_hi"])
        x2up = on2 and _cross(K2, D2, j2, lb, False, cfg["k_lo"])
        x1dn = _cross(K1, D1, i, lb, True, 80) and K2[j2] is not None and K2[j2] >= cfg["k2_ctx_hi"]
        x1up = _cross(K1, D1, i, lb, False, 20) and K2[j2] is not None and K2[j2] <= cfg["k2_ctx_lo"]
        rw = [R2[q] for q in range(j2 - lb, j2 + 1) if R2[q] is not None]
        if not rw:
            continue
        lo = min(c1[q]["l"] for q in range(i - 5, i + 1))
        tmp_bottom = min(b[1] for b in bots[-6:]) if bots else None
        base = dict(t=t, price=price, ref=ref, rsi2=R2[j2], k1=K1[i], k2=K2[j2], trend=trend,
                    last_top=tops[-1][1] if tops else None, last_bot=bots[-1][1] if bots else None, tmp_bottom=tmp_bottom)
        if state == "HOLD":
            if blow is None or c1[i]["l"] < blow:
                blow, ntrig = c1[i]["l"], 0          # nowe odbicie zaczyna się od nowego dołka
            if cfg.get("min_gain") is not None and price < ref * (1 + cfg["min_gain"]):
                continue
            if (x2dn or x1dn) and max(rw) >= cfg["rsi_hi"]:
                ntrig += 1
                bounce = max(c1[q]["h"] for q in range(i - 11, i + 1)) / blow - 1
                mode_s = cfg.get("sell_mode", "first")
                if mode_s == "adaptive" and ntrig < 2 and bounce < cfg.get("big_bounce", 0.08):
                    continue                          # pierwszy szczyt małego odbicia – czekamy na drugi
                if ntrig >= 2:
                    why = "drugi szczyt odbicia"
                elif mode_s == "adaptive":
                    why = f"duże odbicie (+{bounce * 100:.0f}% od dołka)".replace(".", ",")
                else:
                    why = "szczyt odbicia"
                cash = S * price * (1 - fee)
                ev.append(dict(base, kind="SWING_SELL", why=why, src="2h" if x2dn else "1h", S_before=S, cash_after=cash,
                               rsi_max=max(rw), gain=price / ref - 1))
                S, state, ref, blow, ntrig = 0.0, "CASH", price, None, 0
        else:
            if price <= ref * (1 - cfg["min_drop"]) and (x2up or x1up) and min(rw) <= cfg["rsi_lo"]:
                prev_low = bots[-1][1] if bots else None
                lower = prev_low is not None and (lo < prev_low or (mode == "down" and ext < prev_low))
                higher = (cfg.get("uptrend") and trend == "up" and prev_low is not None and lo > prev_low and tops
                          and lo <= tops[-1][1] * (1 - cfg["pullback"]))
                if lower or higher:
                    S = cash / price * (1 - fee)
                    ev.append(dict(base, kind="SWING_BUY", why="niższy dołek" if lower else "wyższy dołek po tymczasowym dnie",
                                   src="2h" if x2up else "1h", cash_before=cash, S_after=S, rsi_min=min(rw), prev_low=prev_low,
                                   drop=price / ref - 1))
                    cash, state, ref, blow, ntrig = 0.0, "HOLD", price, None, 0
                    continue
            if cfg.get("breakout") and tops and price >= tops[-1][1] * (1 + cfg["breakout"]) and tops[-1][0] not in brk_done and price > ref:
                brk_done.add(tops[-1][0])
                ev.append(dict(base, kind="SWING_BREAKOUT", top=tops[-1][1]))
    return ev, dict(phase=state, ref=ref, S=S, cash=cash, trend=trend,
                    last_top=tops[-1][1] if tops else None, last_bot=bots[-1][1] if bots else None,
                    tmp_bottom=min(b[1] for b in bots[-6:]) if bots else None)


def _f(x):
    s = f"{x:.5f}".rstrip("0") if x < 0.1 else f"{x:.4f}"
    return s.replace(".", ",")


def _p(x):
    return f"{x * 100:+.1f}%".replace(".", ",")


def _n(x):
    return f"{x:,.0f}".replace(",", " ")


def _trend_txt(e):
    tb = e.get("tmp_bottom")
    if e.get("why") == "niższy dołek":
        return "Struktura: niższy dołek – trend spadkowy trwa, buduje się nowe tymczasowe dno."
    if e.get("trend") == "up" and tb:
        return f"Struktura: po tymczasowym dnie {_f(tb)} wyższe dołki."
    return "Struktura: trend spadkowy (niższe dołki)" + (f", tymczasowe dno {_f(tb)}." if tb else ".")


def message(e, cfg, pl_time):
    when = pl_time(e["t"])
    if e["kind"] == "SWING_SELL":
        title = f"Sonic: SPRZEDAŻ – {e['why']} ({_f(e['price'])})"
        lines = [f"Świeca 1h zamknięta {when}: cena {_f(e['price'])}. Sygnał {e['src']}: StochRSI zawraca w dół "
                 f"(1h {e['k1']:.0f}, 2h {e['k2']:.0f}), RSI 2h max {e['rsi_max']:.0f}.",
                 f"Względem zakupu {_f(e['ref'])}: {_p(e['gain'])}. {_n(e['S_before'])} S ≈ {_n(e['cash_after'])} $ po prowizji.",
                 _trend_txt(e)]
        nxt = e["price"] * (1 - cfg["min_drop"])
        s_back = e["cash_after"] / nxt * (1 - cfg["fee"])
        lines.append(f"Odkup najwcześniej przy ≤ {_f(nxt)} (≈ {_n(s_back)} S), gdy RSI się odwróci – osobny alert.")
    elif e["kind"] == "SWING_BUY":
        title = f"Sonic: ODKUP – {e['why']} ({_f(e['price'])})"
        lines = [f"Świeca 1h zamknięta {when}: cena {_f(e['price'])}. Sygnał {e['src']}: StochRSI zawraca w górę "
                 f"(1h {e['k1']:.0f}, 2h {e['k2']:.0f}), RSI 2h min {e['rsi_min']:.0f}.",
                 f"Poprzedni dołek fali {_f(e['prev_low'])}; {_p(e['drop'])} względem sprzedaży {_f(e['ref'])}.",
                 f"{_n(e['cash_before'])} $ ≈ {_n(e['S_after'])} S po prowizji.",
                 _trend_txt(e)]
    else:
        title = f"Sonic: wybicie ponad szczyt fali {_f(e['top'])} – uwaga"
        lines = [f"Świeca 1h zamknięta {when}: cena {_f(e['price'])} ponad ostatnim szczytem fali {_f(e['top'])}.",
                 f"Masz gotówkę ze sprzedaży po {_f(e['ref'])}. Mocne wybicie bywa początkiem większego ruchu – rozważ, czy czekać na odkup niżej.",
                 _trend_txt(e)]
    lines.append("Jeśli nie wykonałeś transakcji, napisz – zaktualizuję stan bota.")
    return title, "\n".join(lines)
