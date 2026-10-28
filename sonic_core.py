# -*- coding: utf-8 -*-
"""
Wspólne funkcje analizy rynku: wskaźniki, swingi (ZigZag), konsolidacje, Fibonacci.
Używane przez bot.py (na żywo) i tools/build_history.py (analiza historyczna).
Czysty numpy/pandas-free – działa z listami świec w formacie:
    {"t": unix_sec_początku, "o":..., "h":..., "l":..., "c":..., "v": wolumen_bazowy, "qv": wolumen_USD}
"""
from datetime import datetime, timezone
import numpy as np

DAY = 86400

# Poziomy Fibonacciego używane w całym bocie
FIB_RETRACE = [0.236, 0.382, 0.5, 0.618, 0.786, 0.886]
FIB_EXT = [1.0, 1.272, 1.618, 2.0, 2.618]


def ts_date(t):
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d")


# ----------------------------------------------------------------- wskaźniki
def ema(values, period):
    v = np.asarray(values, dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) < period:
        return out
    out[period - 1] = v[:period].mean()
    a = 2.0 / (period + 1)
    for i in range(period, len(v)):
        out[i] = v[i] * a + out[i - 1] * (1 - a)
    return out


def sma(values, period):
    v = np.asarray(values, dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) < period:
        return out
    cs = np.cumsum(np.insert(v, 0, 0.0))
    out[period - 1:] = (cs[period:] - cs[:-period]) / period
    return out


def rsi(values, period=14):
    c = np.asarray(values, dtype=float)
    out = np.full(len(c), np.nan)
    if len(c) < period + 1:
        return out
    d = np.diff(c)
    g, l = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = g[:period].mean(), l[:period].mean()

    def val(a, b):
        if b == 0:
            return 50.0 if a == 0 else 100.0
        return 100.0 - 100.0 / (1.0 + a / b)

    out[period] = val(ag, al)
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        out[i + 1] = val(ag, al)
    return out


def macd(values, fast=12, slow=26, signal=9):
    c = np.asarray(values, dtype=float)
    m = ema(c, fast) - ema(c, slow)
    s = np.full(len(c), np.nan)
    idx = np.where(~np.isnan(m))[0]
    if len(idx) >= signal:
        s[idx] = ema(m[idx], signal)
    return m, s, m - s


def bollinger_pctb(values, period=20, k=2.0):
    c = np.asarray(values, dtype=float)
    out = np.full(len(c), np.nan)
    for i in range(period - 1, len(c)):
        w = c[i - period + 1:i + 1]
        m, sd = w.mean(), w.std()
        if sd > 0:
            out[i] = (c[i] - (m - k * sd)) / (2 * k * sd)
    return out


def atr(candles, period=14):
    h = np.array([x["h"] for x in candles]); l = np.array([x["l"] for x in candles]); c = np.array([x["c"] for x in candles])
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    out = np.full(len(candles), np.nan)
    if len(tr) >= period:
        a = tr[:period].mean(); out[period] = a
        for i in range(period, len(tr)):
            a = (a * (period - 1) + tr[i]) / period
            out[i + 1] = a
    return out


def resample_weekly(candles):
    """Świece tygodniowe (poniedziałek 00:00 UTC) z dziennych."""
    weeks = {}
    for x in candles:
        wd = datetime.fromtimestamp(x["t"], timezone.utc).weekday()
        wk = x["t"] - wd * DAY
        w = weeks.get(wk)
        if w is None:
            weeks[wk] = dict(t=wk, o=x["o"], h=x["h"], l=x["l"], c=x["c"], v=x.get("v", 0), qv=x.get("qv", 0))
        else:
            w["h"] = max(w["h"], x["h"]); w["l"] = min(w["l"], x["l"]); w["c"] = x["c"]
            w["v"] += x.get("v", 0); w["qv"] += x.get("qv", 0)
    return [weeks[k] for k in sorted(weeks)]


def indicator_table(candles):
    """Zwraca słownik tablic wskaźników dziennych (indeks = indeks świecy)."""
    c = [x["c"] for x in candles]
    qv = np.array([x.get("qv", 0.0) for x in candles], dtype=float)
    m, s, h = macd(c)
    vol20 = sma(qv, 20)
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_ratio = np.where(vol20 > 0, qv / vol20, np.nan)
    s50, s200 = sma(c, 50), sma(c, 200)
    carr = np.asarray(c, dtype=float)
    # tygodniowy RSI przypisany do dni (wartość z bieżącego tygodnia na zamknięciu dnia – bez zaglądania w przyszłość)
    rsi_w = np.full(len(candles), np.nan)
    weekly_closes, cur_week = [], None
    for i, x in enumerate(candles):
        wk = x["t"] - datetime.fromtimestamp(x["t"], timezone.utc).weekday() * DAY
        if wk != cur_week:
            weekly_closes.append(x["c"]); cur_week = wk
        else:
            weekly_closes[-1] = x["c"]
        if len(weekly_closes) >= 15:
            rsi_w[i] = rsi(weekly_closes[-200:])[-1]
    return {
        "rsi": rsi(c), "rsi_w": rsi_w, "macd": m, "macd_sig": s, "macd_hist": h,
        "sma20": sma(c, 20), "sma50": s50, "sma200": s200,
        "dist_sma50": (carr / s50 - 1) * 100, "dist_sma200": (carr / s200 - 1) * 100,
        "pctb": bollinger_pctb(c), "vol_ratio": vol_ratio, "atr": atr(candles),
    }


# ----------------------------------------------------------------- swingi
def zigzag(candles, pct):
    """
    ZigZag na high/low. Punkt zwrotny jest potwierdzony, gdy cena odejdzie od ekstremum o >= pct.
    Zwraca listę: {i, t, date, price, kind('H'/'L'), confirmed, confirm_i}
    confirm_i = indeks świecy, na której swing stał się znany (ważne dla testów bez zaglądania w przyszłość).
    """
    hi = [x["h"] for x in candles]; lo = [x["l"] for x in candles]
    n = len(candles); piv = []
    if n < 2:
        return []
    trend = None
    ch_i, ch = 0, hi[0]; cl_i, cl = 0, lo[0]
    ext_i = ext_p = None
    for i in range(1, n):
        if trend is None:
            if hi[i] > ch: ch_i, ch = i, hi[i]
            if lo[i] < cl: cl_i, cl = i, lo[i]
            if ch / cl - 1 >= pct:
                if cl_i < ch_i:
                    piv.append([cl_i, cl, "L", i]); trend = "up"; ext_i, ext_p = ch_i, ch
                else:
                    piv.append([ch_i, ch, "H", i]); trend = "down"; ext_i, ext_p = cl_i, cl
            continue
        if trend == "up":
            if hi[i] >= ext_p:
                ext_i, ext_p = i, hi[i]
            elif 1 - lo[i] / ext_p >= pct:
                piv.append([ext_i, ext_p, "H", i]); trend = "down"; ext_i, ext_p = i, lo[i]
        else:
            if lo[i] <= ext_p:
                ext_i, ext_p = i, lo[i]
            elif hi[i] / ext_p - 1 >= pct:
                piv.append([ext_i, ext_p, "L", i]); trend = "up"; ext_i, ext_p = i, hi[i]
    out = [dict(i=i, t=candles[i]["t"], date=ts_date(candles[i]["t"]), price=p, kind=k, confirmed=True, confirm_i=ci)
           for i, p, k, ci in piv]
    if trend is not None:
        out.append(dict(i=ext_i, t=candles[ext_i]["t"], date=ts_date(candles[ext_i]["t"]), price=ext_p,
                        kind="H" if trend == "up" else "L", confirmed=False, confirm_i=None))
    return label_swings(out)


def label_swings(sw):
    last = {"H": None, "L": None}
    for s in sw:
        ref = last[s["kind"]]
        if ref is None:
            s["label"] = s["kind"]
        elif s["kind"] == "H":
            s["label"] = "HH" if s["price"] > ref else "LH"
        else:
            s["label"] = "HL" if s["price"] > ref else "LL"
        last[s["kind"]] = s["price"]
    return sw


def legs_from_swings(sw):
    """Odcinki (fale) między kolejnymi swingami + relacje Fibonacciego do poprzednich odcinków."""
    legs = []
    for a, b in zip(sw[:-1], sw[1:]):
        legs.append(dict(start_date=a["date"], start=a["price"], end_date=b["date"], end=b["price"],
                         direction="wzrost" if b["price"] > a["price"] else "spadek",
                         change_pct=(b["price"] / a["price"] - 1) * 100,
                         days=int(round((b["t"] - a["t"]) / DAY)),
                         end_label=b.get("label"), confirmed=b["confirmed"]))
    for k, lg in enumerate(legs):
        if k >= 1:
            prev = legs[k - 1]
            size, psize = abs(lg["end"] - lg["start"]), abs(prev["end"] - prev["start"])
            lsize, lpsize = abs(np.log(lg["end"] / lg["start"])), abs(np.log(prev["end"] / prev["start"]))
            lg["retrace_lin"] = size / psize if psize else None          # zniesienie poprzedniej fali (liniowo)
            lg["retrace_log"] = lsize / lpsize if lpsize else None       # zniesienie w skali logarytmicznej
            lg["time_ratio"] = lg["days"] / prev["days"] if prev["days"] else None
        if k >= 2:
            same = legs[k - 2]
            s1, s0 = abs(lg["end"] - lg["start"]), abs(same["end"] - same["start"])
            lg["ext_vs_prev_same"] = s1 / s0 if s0 else None             # np. fala 3 / fala 1
    return legs


def nearest_fib(ratio, levels=(0.236, 0.382, 0.5, 0.618, 0.786, 0.886, 1.0, 1.272, 1.618, 2.0, 2.618)):
    if ratio is None:
        return None
    return min(levels, key=lambda f: abs(f - ratio))


def fib_levels(low, high, direction="up"):
    """Poziomy zniesień dla ruchu low->high (direction='up') lub high->low ('down')."""
    rng = high - low
    out = {}
    for f in FIB_RETRACE:
        out[f] = high - rng * f if direction == "up" else low + rng * f
    for f in FIB_EXT[1:]:
        out[f] = low + rng * f if direction == "up" else high - rng * f
    return out


# ----------------------------------------------------------------- konsolidacje
def find_ranges(candles, width=0.35, min_days=21, merge_gap=5, merge_overlap=0.6):
    """
    Konsolidacja = okres >= min_days, w którym zamknięcia mieszczą się w paśmie max/min <= 1+width.
    Sąsiednie, nakładające się pasma są łączone. Wsparcie/opór = 5. i 95. percentyl low/high (odporne na knoty).
    """
    c = [x["c"] for x in candles]; n = len(c); raw = []; i = 0
    while i < n - min_days:
        hi = lo = c[i]; j = i
        while j + 1 < n:
            h2, l2 = max(hi, c[j + 1]), min(lo, c[j + 1])
            if h2 / l2 - 1 > width:
                break
            hi, lo, j = h2, l2, j + 1
        if j - i + 1 >= min_days:
            raw.append([i, j]); i = j + 1
        else:
            i += 1

    def band(a, b):
        seg = candles[a:b + 1]
        return float(np.percentile([x["l"] for x in seg], 5)), float(np.percentile([x["h"] for x in seg], 95))

    merged = []
    for a, b in raw:
        if merged:
            pa, pb = merged[-1]
            s1, r1 = band(pa, pb); s2, r2 = band(a, b)
            ov = max(0.0, min(r1, r2) - max(s1, s2)) / min(r1 - s1, r2 - s2)
            if a - pb <= merge_gap and ov >= merge_overlap:
                merged[-1] = [pa, b]; continue
        merged.append([a, b])

    out = []
    for a, b in merged:
        seg = candles[a:b + 1]
        sup, res = band(a, b)
        closes = [x["c"] for x in seg]
        rec = dict(start=ts_date(seg[0]["t"]), end=ts_date(seg[-1]["t"]), days=len(seg),
                   support=sup, resistance=res, abs_low=min(x["l"] for x in seg), abs_high=max(x["h"] for x in seg),
                   width_pct=(res / sup - 1) * 100,
                   support_touches=sum(1 for x in seg if x["l"] <= sup * 1.03),
                   resistance_touches=sum(1 for x in seg if x["h"] >= res * 0.97),
                   ongoing=(b == n - 1))
        prior = candles[max(0, a - 60):a]
        rec["trend_before"] = None if len(prior) < 10 else ("wzrost" if seg[0]["c"] > prior[0]["c"] else "spadek")
        rec["move_before_pct"] = None if len(prior) < 10 else (seg[0]["c"] / prior[0]["c"] - 1) * 100
        if b + 1 < n:
            brk = candles[b + 1]["c"]
            rec["breakout"] = "góra" if brk > max(closes) else "dół"
            nxt = candles[b + 1:b + 31]
            ref = seg[-1]["c"]
            rec["max_up_30d_pct"] = (max(x["h"] for x in nxt) / ref - 1) * 100
            rec["max_down_30d_pct"] = (1 - min(x["l"] for x in nxt) / ref) * 100
            # fałszywe wybicie: w ciągu 10 dni zamknięcie wróciło do środka pasma
            back = [x["c"] for x in candles[b + 2:b + 12]]
            rec["false_breakout"] = any(sup < v < res for v in back) if back else None
        else:
            rec.update(breakout=None, max_up_30d_pct=None, max_down_30d_pct=None, false_breakout=None)
        out.append(rec)
    return out


# ----------------------------------------------------------------- profil punktów zwrotnych
# Warunki wyznaczone w analizie historii (tools/build_history.py). Liczone na zamknięciu dnia.
BOTTOM_CONDITIONS = [
    ("rsi_low", "RSI dzienny ≤ 35"),
    ("sma50_far_below", "Cena ≥ 25% pod SMA50"),
    ("pctb_low", "Cena przy/pod dolną wstęgą Bollingera (%B ≤ 0.1)"),
    ("vol_spike", "Wolumen ≥ 1.5× średniej 20 dni (ostatnie 3 dni)"),
    ("lower_wick", "Długi dolny knot ≥ 8% (ostatnie 3 dni)"),
    ("bull_div", "Dywergencja byków RSI"),
    ("rsi_w_low", "RSI tygodniowy ≤ 40"),
]
TOP_CONDITIONS = [
    ("rsi_high", "RSI dzienny ≥ 65"),
    ("sma50_far_above", "Cena ≥ 20% nad SMA50"),
    ("pctb_high", "Cena przy/nad górną wstęgą Bollingera (%B ≥ 0.95)"),
    ("vol_spike2", "Wolumen ≥ 2× średniej 20 dni (ostatnie 3 dni)"),
    ("upper_wick", "Długi górny knot ≥ 8% (ostatnie 3 dni)"),
    ("bear_div", "Dywergencja niedźwiedzi RSI"),
    ("rsi_w_high", "RSI tygodniowy ≥ 65"),
]


def turning_point_flags(candles, T, minor_swings, i):
    """Flagi warunków dołka/szczytu w dniu i (bez zaglądania w przyszłość)."""
    def g(key):
        v = T[key][i]
        return None if v is None or np.isnan(v) else float(v)

    lo3 = range(max(0, i - 2), i + 1)
    vr = [T["vol_ratio"][k] for k in lo3 if not np.isnan(T["vol_ratio"][k])]
    lw = max((1 - candles[k]["l"] / min(candles[k]["c"], candles[k]["o"])) * 100 for k in lo3)
    uw = max((candles[k]["h"] / max(candles[k]["c"], candles[k]["o"]) - 1) * 100 for k in lo3)
    rsi_now = g("rsi")

    def div(kind):
        prev = [s for s in minor_swings if s["kind"] == kind and s["confirmed"] and s["confirm_i"] is not None
                and s["confirm_i"] <= i and 10 <= i - s["i"] <= 120]
        if not prev or rsi_now is None:
            return False
        p = prev[-1]
        rp = T["rsi"][p["i"]]
        if np.isnan(rp):
            return False
        if kind == "L":
            return candles[i]["l"] < p["price"] and rsi_now > rp + 3
        return candles[i]["h"] > p["price"] and rsi_now < rp - 3

    d50, pb, rw = g("dist_sma50"), g("pctb"), g("rsi_w")
    vmax = max(vr) if vr else None
    bottom = {
        "rsi_low": rsi_now is not None and rsi_now <= 35,
        "sma50_far_below": d50 is not None and d50 <= -25,
        "pctb_low": pb is not None and pb <= 0.1,
        "vol_spike": vmax is not None and vmax >= 1.5,
        "lower_wick": lw >= 8,
        "bull_div": div("L"),
        "rsi_w_low": rw is not None and rw <= 40,
    }
    top = {
        "rsi_high": rsi_now is not None and rsi_now >= 65,
        "sma50_far_above": d50 is not None and d50 >= 20,
        "pctb_high": pb is not None and pb >= 0.95,
        "vol_spike2": vmax is not None and vmax >= 2.0,
        "upper_wick": uw >= 8,
        "bear_div": div("H"),
        "rsi_w_high": rw is not None and rw >= 65,
    }
    return bottom, top


# ----------------------------------------------------------------- setupy sygnałów
# Parametry wybrane w backteście historii FTM/S 2019–2026 (szczegóły w sonic_history.SETUP_RESEARCH).
SETUPS = {
    "LONG": dict(degree=0.25, lines=(0.618, 0.786), tol=0.035, invalid=0.886),
    "SHORT": dict(degree=0.40, lines=(0.618, 0.786), tol=0.035, invalid=0.886),
}


def known_swings(swings, i):
    return [s for s in swings if s["confirmed"] and s["confirm_i"] is not None and s["confirm_i"] <= i]


def evaluate_setup(candles, i, swings, side, hist_lookback=1100):
    """
    Sprawdza setup na świecy i (zamkniętej). Zwraca słownik z warunkami i poziomami (zawsze),
    pole 'triggered' mówi, czy wszystkie warunki są spełnione.
      LONG : ostatni potwierdzony ruch to wzrost L->H; korekta dotyka linii Fibo 0.618/0.786 (±tol)
             lub historycznego poziomu leżącego w strefie 0.5–0.786; korekta nie przebiła 0.886;
             świeca dzienna wzrostowa zamknięta w górnej połowie.
      SHORT: lustrzanie dla spadku H->L i odbicia w strefę 0.618/0.786.
    """
    cfg = SETUPS[side]
    k = known_swings(swings, i)
    out = dict(side=side, triggered=False, conditions={}, levels={}, leg=None)
    if len(k) < 2:
        out["conditions"]["Wystarczająca historia swingów"] = False
        return out
    a, b = k[-2], k[-1]
    x = candles[i]
    hist = [s["price"] for s in k[:-2] if i - s["i"] <= hist_lookback]
    tol = cfg["tol"]
    if side == "LONG":
        if not (a["kind"] == "L" and b["kind"] == "H"):
            out["conditions"]["Ostatni potwierdzony ruch to wzrost (L→H)"] = False
            return out
        rng = b["price"] - a["price"]
        lines = {f: b["price"] - rng * f for f in cfg["lines"]}
        inv = b["price"] - rng * cfg["invalid"]
        z_lo, z_hi = b["price"] - rng * 0.786 * (1 + tol), b["price"] - rng * 0.5
        seg = [c["l"] for c in candles[b["i"] + 1:i + 1]]
        touch = x["l"]
        not_broken = (min(seg) if seg else x["l"]) >= inv
        candle_ok = x["c"] > x["o"] and x["c"] > (x["h"] + x["l"]) / 2
        stop = min(inv, x["l"] * 0.97); target = b["price"]
        rr_ok = stop < x["c"] < target
    else:
        if not (a["kind"] == "H" and b["kind"] == "L"):
            out["conditions"]["Ostatni potwierdzony ruch to spadek (H→L)"] = False
            return out
        rng = a["price"] - b["price"]
        lines = {f: b["price"] + rng * f for f in cfg["lines"]}
        inv = b["price"] + rng * cfg["invalid"]
        z_lo, z_hi = b["price"] + rng * 0.5, b["price"] + rng * 0.786 * (1 + tol)
        seg = [c["h"] for c in candles[b["i"] + 1:i + 1]]
        touch = x["h"]
        not_broken = (max(seg) if seg else x["h"]) <= inv
        candle_ok = x["c"] < x["o"] and x["c"] < (x["h"] + x["l"]) / 2
        stop = max(inv, x["h"] * 1.03); target = b["price"]
        rr_ok = target < x["c"] < stop
    fib_hits = [f for f, p in lines.items() if abs(touch / p - 1) <= tol]
    hist_hits = [p for p in hist if z_lo <= p <= z_hi and abs(touch / p - 1) <= tol]
    in_zone = bool(fib_hits or hist_hits)
    word = "Cena w korekcie" if side == "LONG" else "Cena w odbiciu"
    out["leg"] = dict(start=a["price"], start_date=a["date"], end=b["price"], end_date=b["date"])
    out["levels"] = dict(fib_0618=lines[0.618], fib_0786=lines[0.786], invalidation=inv,
                         zone_low=min(lines.values()) * (1 - tol), zone_high=max(lines.values()) * (1 + tol),
                         hist_levels_in_zone=sorted(set(round(p, 6) for p in hist if z_lo <= p <= z_hi)),
                         stop=stop, target=target,
                         rr=((target - x["c"]) / (x["c"] - stop)) if side == "LONG" and x["c"] > stop
                         else ((x["c"] - target) / (stop - x["c"]) if side == "SHORT" and stop > x["c"] else None))
    out["conditions"] = {
        f"{word} dotknęła linii Fibo 0.618/0.786 lub poziomu historycznego (±{tol * 100:.1f}%)": in_zone,
        f"{word} nie przekroczyła poziomu unieważnienia (Fibo 0.886)": not_broken,
        ("Świeca dzienna wzrostowa, zamknięcie w górnej połowie" if side == "LONG"
         else "Świeca dzienna spadkowa, zamknięcie w dolnej połowie"): candle_ok,
        "Stop i cel po właściwych stronach ceny": rr_ok,
    }
    out["fib_hits"] = fib_hits; out["hist_hits"] = hist_hits
    out["triggered"] = all(out["conditions"].values())
    return out


def trade_outcome(candles, i, side, entry, stop, target, horizon=90):
    risk = (entry - stop) if side == "LONG" else (stop - entry)
    if risk <= 0:
        return None
    R = ((target - entry) if side == "LONG" else (entry - target)) / risk
    for j in range(i + 1, min(len(candles), i + horizon + 1)):
        c = candles[j]
        if side == "LONG":
            if c["l"] <= stop: return dict(result_R=-1.0, target_R=R, days=j - i, exit="stop")
            if c["h"] >= target: return dict(result_R=R, target_R=R, days=j - i, exit="cel")
        else:
            if c["h"] >= stop: return dict(result_R=-1.0, target_R=R, days=j - i, exit="stop")
            if c["l"] <= target: return dict(result_R=R, target_R=R, days=j - i, exit="cel")
    last = min(len(candles) - 1, i + horizon)
    if last == len(candles) - 1 and last - i < horizon:
        return dict(result_R=None, target_R=R, days=last - i, exit="otwarta")
    ex = candles[last]["c"]
    return dict(result_R=((ex - entry) if side == "LONG" else (entry - ex)) / risk, target_R=R, days=horizon, exit="czas")


def backtest_setup(candles, side, start_i=220, cooldown=7):
    swings = zigzag(candles, SETUPS[side]["degree"])
    trades, last = [], -99
    for i in range(start_i, len(candles)):
        if i - last < cooldown:
            continue
        ev = evaluate_setup(candles, i, swings, side)
        if not ev["triggered"]:
            continue
        lv = ev["levels"]
        oc = trade_outcome(candles, i, side, candles[i]["c"], lv["stop"], lv["target"])
        if oc is None:
            continue
        trades.append(dict(date=ts_date(candles[i]["t"]), entry=candles[i]["c"], stop=lv["stop"], target=lv["target"],
                           risk_pct=abs(candles[i]["c"] - lv["stop"]) / candles[i]["c"] * 100, **oc))
        last = i
    closed = [t for t in trades if t["result_R"] is not None]
    r = np.array([t["result_R"] for t in closed]) if closed else np.array([])
    summary = dict(n=len(closed), win_rate=float(np.mean(r > 0) * 100) if len(r) else None,
                   avg_R=float(r.mean()) if len(r) else None,
                   median_target_R=float(np.median([t["target_R"] for t in closed])) if closed else None,
                   median_risk_pct=float(np.median([t["risk_pct"] for t in closed])) if closed else None,
                   median_days=float(np.median([t["days"] for t in closed])) if closed else None)
    return summary, trades


# ----------------------------------------------------------------- poziomy S/R
def cluster_levels(prices, tol=0.04):
    """Grupuje poziomy cenowe leżące w odległości <= tol (względnie). Zwraca [(poziom, liczność)]."""
    ps = sorted(p for p in prices if p and p > 0)
    groups = []
    for p in ps:
        if groups and p / groups[-1][-1] - 1 <= tol:
            groups[-1].append(p)
        else:
            groups.append([p])
    return [(float(np.exp(np.mean(np.log(g)))), len(g)) for g in groups]
