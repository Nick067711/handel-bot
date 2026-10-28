# -*- coding: utf-8 -*-
"""Analiza bieżąca Sonic i BTC na podstawie danych z market_data i bazy wiedzy sonic_history."""
import os, csv, math, statistics
from datetime import datetime, timezone

import numpy as np

import sonic_history as H
from sonic_core import (DAY, indicator_table, zigzag, legs_from_swings, find_ranges, turning_point_flags,
                        evaluate_setup, backtest_setup, cluster_levels, ema, ts_date, SETUPS,
                        BOTTOM_CONDITIONS, TOP_CONDITIONS)

ROOT = os.path.dirname(os.path.abspath(__file__))
DEGREES = {"cykl": 0.80, "glowny": 0.60, "posredni": 0.40, "drobny": 0.25}
DEGREE_NAMES = {"cykl": "cykl (ruchy ≥80%)", "glowny": "główny (≥60%)", "posredni": "pośredni (≥40%)", "drobny": "drobny (≥25%)"}
DEGREE_WEIGHT = {"cykl": 4, "glowny": 3, "posredni": 2, "drobny": 1}

# Poziomy użytkownika z poprzedniej wersji bota – zachowane jako dodatkowe źródło poziomów (można zmienić)
USER_LEVELS = [
    dict(price=0.045, name="Dolna granica bazy (użytkownik)"),
    dict(price=0.054, name="Górna granica bazy (użytkownik)"),
    dict(price=0.070, name="Short Anchor (użytkownik)"),
]


def pct(a, b):
    return (a / b - 1) * 100 if a and b else None


def num(x):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


# ----------------------------------------------------------------- historia Sonica
def load_history_csv():
    for p in (os.path.join(ROOT, "data", "SONIC_FTM_S_1d.csv"), os.path.join(ROOT, "SONIC_FTM_S_1d.csv")):
        if os.path.exists(p):
            out = []
            with open(p, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    out.append(dict(t=int(r["open_time"]) // 1000, o=float(r["open"]), h=float(r["high"]),
                                    l=float(r["low"]), c=float(r["close"]), v=float(r["volume"]),
                                    qv=float(r.get("quote_asset_volume") or float(r["volume"]) * float(r["close"]))))
            return out, p
    return [], None


def closed_only(candles, period=DAY, now_ts=None):
    now_ts = now_ts or int(datetime.now(timezone.utc).timestamp())
    return [c for c in sorted(candles, key=lambda x: x["t"]) if c["t"] + period <= now_ts]


def merge_history(hist, live):
    """Dokleja świece z Coinbase po ostatniej świecy z CSV. Wolumen Coinbase skalowany do poziomu Binance (z części wspólnej)."""
    if not hist:
        return list(live), 1.0
    last_t = hist[-1]["t"]
    by_t = {c["t"]: c for c in hist}
    ratios = [by_t[c["t"]]["qv"] / c["qv"] for c in live if c["t"] in by_t and c["qv"] > 0 and by_t[c["t"]]["qv"] > 0]
    scale = statistics.median(ratios[-60:]) if len(ratios) >= 5 else 1.0
    tail = []
    for c in live:
        if c["t"] > last_t:
            x = dict(c); x["qv"] = c["qv"] * scale; x["v"] = c["v"] * scale
            tail.append(x)
    return hist + tail, scale


# ----------------------------------------------------------------- księga zleceń
def analyze_book(book, mid):
    if not book or not mid:
        return None
    bids = [(p, s * p) for p, s in book["bids"] if p >= mid * 0.75]
    asks = [(p, s * p) for p, s in book["asks"] if p <= mid * 1.25]

    def depth(side, frac):
        if side == "bid":
            return sum(u for p, u in bids if p >= mid * (1 - frac))
        return sum(u for p, u in asks if p <= mid * (1 + frac))

    def walls(rows, side):
        bins = {}
        for p, u in rows:
            k = math.floor(math.log(p / mid) / math.log(1.01))  # przedziały co 1%
            bins[k] = bins.get(k, 0) + u
        if not bins:
            return []
        med = statistics.median(bins.values())
        cand = []
        for k, u in bins.items():
            lo, hi = mid * 1.01 ** k, mid * 1.01 ** (k + 1)
            if u >= max(3 * med, 1):
                cand.append(dict(price=(lo + hi) / 2, usd=u, dist_pct=pct((lo + hi) / 2, mid), x_median=u / med if med else None))
        cand.sort(key=lambda w: -w["usd"])
        return sorted(cand[:4], key=lambda w: abs(w["dist_pct"]))

    out = dict(mid=mid, spread_pct=pct(book["asks"][0][0], book["bids"][0][0]) if book["bids"] and book["asks"] else None)
    for f in (0.02, 0.05, 0.10):
        out[f"bid_depth_{int(f * 100)}pct_usd"] = depth("bid", f)
        out[f"ask_depth_{int(f * 100)}pct_usd"] = depth("ask", f)
    b5, a5 = out["bid_depth_5pct_usd"], out["ask_depth_5pct_usd"]
    out["imbalance_5pct"] = b5 / (b5 + a5) * 100 if (b5 + a5) else None
    out["bid_walls"] = walls(bids, "bid")
    out["ask_walls"] = walls(asks, "ask")
    return out


# ----------------------------------------------------------------- poziomy wsparcia / oporu
def dpl(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return d


def build_levels(price, swings, ranges, fibs, book_info):
    cand = []
    seen = {}
    for deg, sw in swings.items():
        for s in sw:
            if 0.4 * price <= s["price"] <= 2.0 * price:
                key = (s["date"], round(s["price"], 8))
                if key in seen:          # ten sam punkt zwrotny na kilku poziomach: +1 do wagi, jeden opis
                    seen[key]["w"] += 1
                    seen[key]["degs"].append(DEGREE_NAMES[deg].split(" ")[0])
                    continue
                seen[key] = dict(price=s["price"], w=DEGREE_WEIGHT[deg], label=s["label"], date=s["date"],
                                 degs=[DEGREE_NAMES[deg].split(" ")[0]])
    for v in seen.values():
        cand.append(dict(price=v["price"], w=v["w"], src=f"{v['label']} z {dpl(v['date'])} ({', '.join(v['degs'])})"))
    for r in ranges[-12:]:
        for key, nm in (("support", "wsparcie"), ("resistance", "opór")):
            p = r[key]
            if 0.4 * price <= p <= 2.0 * price:
                cand.append(dict(price=p, w=2, src=f"{nm} konsolidacji {dpl(r['start'])}–{dpl(r['end'])} ({r['days']} dni)"))
    for f in fibs:
        for lvl, p in f["levels"].items():
            if 0.4 * price <= p <= 2.0 * price:
                w = 3 if lvl in (0.618, 0.786) else 2 if lvl in (0.5, 0.382, 1.272, 1.618) else 1
                cand.append(dict(price=p, w=w, src=f"Fibo {f'{lvl * 100:.1f}'.replace('.', ',')}% {f['label']}"))
    for u in USER_LEVELS:
        cand.append(dict(price=u["price"], w=2, src=u["name"]))
    if book_info:
        for wall in book_info["bid_walls"] + book_info["ask_walls"]:
            cand.append(dict(price=wall["price"], w=2, src=f"ściana w księdze Coinbase ~{wall['usd']:,.0f} USD"))
    cand.sort(key=lambda c: c["price"])
    groups = []
    for c in cand:
        if groups and c["price"] / groups[-1][-1]["price"] - 1 <= 0.03:
            groups[-1].append(c)
        else:
            groups.append([c])
    levels = []
    for g in groups:
        wsum = sum(c["w"] for c in g)
        p = math.exp(sum(math.log(c["price"]) * c["w"] for c in g) / wsum)
        levels.append(dict(price=p, strength=wsum, sources=[c["src"] for c in sorted(g, key=lambda c: -c["w"])][:6],
                           dist_pct=pct(p, price)))
    supports = sorted([l for l in levels if l["price"] <= price], key=lambda l: -l["price"])
    resists = sorted([l for l in levels if l["price"] > price], key=lambda l: l["price"])
    # najbliższe "istotne" (siła >= 4) i po 4 najbliższe w każdą stronę
    key_sup = next((l for l in supports if l["strength"] >= 4), supports[0] if supports else None)
    key_res = next((l for l in resists if l["strength"] >= 4), resists[0] if resists else None)
    return dict(supports=supports[:5], resistances=resists[:5], key_support=key_sup, key_resistance=key_res)


# ----------------------------------------------------------------- Sonic
def analyze_sonic(candles, ticker=None, stats=None, book=None, cg=None, c6h=None):
    T = indicator_table(candles)
    n = len(candles); i = n - 1; last = candles[i]
    price = (ticker or {}).get("price") or last["c"]
    g = lambda k, j=i: num(T[k][j])

    swings = {d: zigzag(candles, p) for d, p in DEGREES.items()}
    ranges = find_ranges(candles)

    # struktura: ostatnie swingi na każdym poziomie
    structure = {}
    for d, sw in swings.items():
        conf = [s for s in sw if s["confirmed"]]
        last_h = next((s for s in reversed(conf) if s["kind"] == "H"), None)
        last_l = next((s for s in reversed(conf) if s["kind"] == "L"), None)
        tail = sw[-5:]
        if last_h and last_l:
            if last_h["label"] in ("HH",) and last_l["label"] in ("HL", "L"):
                trend = "wzrostowa (HH + HL)"
            elif last_h["label"] in ("LH", "H") and last_l["label"] == "LL":
                trend = "spadkowa (LH + LL)"
            elif last_h["label"] == "HH" and last_l["label"] == "LL":
                trend = "zmiana na wzrostową (pierwszy HH po LL)" if last_h["i"] > last_l["i"] else "mieszana (rozszerzająca się)"
            elif last_h["label"] == "LH" and last_l["label"] == "HL":
                trend = "zawężająca się (konsolidacja)"
            else:
                trend = "mieszana"
        else:
            trend = "brak danych"
        unconf = sw[-1] if sw and not sw[-1]["confirmed"] else None
        structure[d] = dict(trend=trend, swings=[dict(date=s["date"], price=s["price"], label=s["label"], confirmed=s["confirmed"]) for s in tail],
                            current_extreme=dict(date=unconf["date"], price=unconf["price"], kind=unconf["kind"], label=unconf["label"]) if unconf else None)

    # fibo bieżących ruchów (drobny i pośredni) – ostatni potwierdzony swing -> bieżące ekstremum
    fibs = []
    for d in ("drobny", "posredni", "glowny"):
        sw = swings[d]
        if len(sw) >= 2:
            a, b = sw[-2], sw[-1]
            lo, hi = min(a["price"], b["price"]), max(a["price"], b["price"])
            up = b["price"] > a["price"]
            levels = {}
            for f in (0.236, 0.382, 0.5, 0.618, 0.786, 0.886):
                levels[f] = hi - (hi - lo) * f if up else lo + (hi - lo) * f
            for f in (1.272, 1.618):
                levels[f] = lo - (hi - lo) * (f - 1) if up else hi + (hi - lo) * (f - 1)
            levels = {k: v for k, v in levels.items() if v > 0}
            retr = (hi - price) / (hi - lo) if up else (price - lo) / (hi - lo)
            fibs.append(dict(name=f"{a['date']}→{b['date']} ({DEGREE_NAMES[d].split(' ')[0]})", degree=d,
                             label=f"{'wzrostu' if up else 'spadku'} {dpl(a['date'])}–{dpl(b['date'])}", start=a["price"], end=b["price"],
                             start_date=a["date"], end_date=b["date"], direction="wzrost" if up else "spadek",
                             end_confirmed=b["confirmed"], levels=levels, current_retrace_pct=retr * 100))

    book_info = analyze_book(book, price) if book else None
    levels = build_levels(price, {d: [s for s in sw if s["confirmed"]] for d, sw in swings.items()}, ranges, fibs[:2], book_info)

    # kierunek ruchu
    s20, s50, s200 = g("sma20"), g("sma50"), g("sma200")
    s50_prev = num(T["sma50"][i - 10]) if i >= 10 else None
    s200_prev = num(T["sma200"][i - 20]) if i >= 20 else None
    hist_now, hist_prev = g("macd_hist"), g("macd_hist", i - 1)
    d_score = sum([(price > s20) if s20 else 0, (price > s50) if s50 else 0, (hist_now or 0) > 0, (hist_now or 0) > (hist_prev or 0)])
    medium = "w górę" if d_score >= 3 else "w dół" if d_score <= 1 else "bokiem / niezdecydowany"
    long_term = ("w górę" if s200 and price > s200 and s200_prev and s200 > s200_prev else
                 "w dół" if s200 and price < s200 and s200_prev and s200 < s200_prev else "przejściowy")
    short_term, st_detail = None, None
    if c6h and len(c6h) >= 60:
        cl = [c["c"] for c in c6h]
        e20, e50 = ema(cl, 20)[-1], ema(cl, 50)[-1]
        ch2d = pct(cl[-1], cl[-9]) if len(cl) > 9 else None
        short_term = "w górę" if (cl[-1] > e20 > e50) else "w dół" if (cl[-1] < e20 < e50) else "bokiem"
        st_detail = dict(ema20_6h=e20, ema50_6h=e50, change_48h_pct=ch2d)

    # kontekst historyczny
    all_lows = [c["l"] for c in candles]; all_highs = [c["h"] for c in candles]
    ath = max(candles, key=lambda c: c["h"]); atl = min(candles, key=lambda c: c["l"])
    pct_days_below = 100 * sum(1 for c in candles if c["c"] < price) / n
    last_seen = next((ts_date(c["t"]) for c in reversed(candles[:-30]) if c["l"] <= price <= c["h"]), None)
    yr = candles[-365:]
    y_hi, y_lo = max(c["h"] for c in yr), min(c["l"] for c in yr)
    hist_ranges_here = [r for r in ranges if r["abs_low"] <= price <= r["abs_high"] and not r["ongoing"]]
    cur_range = next((r for r in reversed(ranges) if r["ongoing"]), None)
    rng_days = [r["days"] for r in H.RANGES]

    # wskaźniki i profil punktów zwrotnych
    minor = swings["drobny"]
    bflags, tflags = turning_point_flags(candles, T, minor, i)
    bscore, tscore = sum(bflags.values()), sum(tflags.values())

    def precision(which, score):
        rows = H.TURNING_POINT_PROFILE[which]["wynik"]
        best = None
        for r in rows:
            if r["min_warunkow"] and score >= r["min_warunkow"]:
                best = r
        return best

    # setupy + historyczna skuteczność (liczona na bieżąco tymi samymi regułami)
    setups = {}
    for side in ("LONG", "SHORT"):
        sw = swings["drobny"] if SETUPS[side]["degree"] == 0.25 else swings["posredni"]
        ev = evaluate_setup(candles, i, sw, side)
        summ, trades = backtest_setup(candles, side)
        lv = ev.get("levels") or {}
        dist_zone = None
        if lv.get("zone_low"):
            if side == "LONG":
                dist_zone = 0.0 if lv["zone_low"] <= price <= lv["zone_high"] else pct(lv["zone_high"], price)
            else:
                dist_zone = 0.0 if lv["zone_low"] <= price <= lv["zone_high"] else pct(lv["zone_low"], price)
        ev["dist_to_zone_pct"] = dist_zone
        ev["backtest"] = summ
        ev["recent_trades"] = trades[-6:]
        regime_bt = None
        if side == "LONG":
            regime_bt = "pod SMA200 (jak obecnie)" if s200 and price < s200 else "nad SMA200 (jak obecnie)"
        ev["regime_note"] = regime_bt
        setups[side] = ev

    chg = lambda k: pct(price, candles[-1 - k]["c"]) if n > k else None
    return dict(
        price=price, candle_date=ts_date(last["t"]), price_source="Coinbase S-USD (live)" if ticker else "ostatnie zamknięcie",
        change_24h_pct=pct(price, (stats or {}).get("open")) if stats and stats.get("open") else chg(1),
        change_7d_pct=chg(7), change_30d_pct=chg(30), change_90d_pct=chg(90),
        volume_24h_coinbase_usd=(ticker or {}).get("volume_24h_base", 0) * price if ticker else None,
        volume_30d_coinbase_usd=(stats or {}).get("volume_30day", 0) * price if stats and stats.get("volume_30day") else None,
        indicators=dict(rsi=g("rsi"), rsi_w=g("rsi_w"), macd=g("macd"), macd_signal=g("macd_sig"), macd_hist=hist_now,
                        macd_hist_prev=hist_prev, sma20=s20, sma50=s50, sma200=s200,
                        dist_sma50_pct=g("dist_sma50"), dist_sma200_pct=g("dist_sma200"), pctb=g("pctb"),
                        atr_pct=(g("atr") / last["c"] * 100) if g("atr") else None, vol_ratio=g("vol_ratio")),
        direction=dict(short_term=short_term, short_term_detail=st_detail, medium_term=medium, long_term=long_term),
        structure=structure, fibs=fibs, levels=levels, orderbook=book_info,
        history=dict(ath=ath["h"], ath_date=ts_date(ath["t"]), atl=atl["l"], atl_date=ts_date(atl["t"]),
                     from_ath_pct=pct(price, ath["h"]), from_atl_x=price / atl["l"],
                     pct_days_below=pct_days_below, last_seen_before=last_seen,
                     year_high=y_hi, year_low=y_lo, year_position_pct=(price - y_lo) / (y_hi - y_lo) * 100 if y_hi > y_lo else None,
                     ranges_at_this_price=hist_ranges_here[-4:], current_range=cur_range,
                     typical_range_days=dict(median=statistics.median(rng_days), p75=float(np.percentile(rng_days, 75))),
                     last_range=ranges[-1] if ranges else None),
        turning=dict(bottom_flags=bflags, top_flags=tflags, bottom_score=bscore, top_score=tscore,
                     bottom_precision=precision("dolki", bscore), top_precision=precision("szczyty", tscore),
                     bottom_labels=dict(BOTTOM_CONDITIONS), top_labels=dict(TOP_CONDITIONS)),
        setups=setups, coingecko=cg,
        chart=[dict(t=c["t"], c=c["c"], h=c["h"], l=c["l"]) for c in candles[-400:]],
        swings_chart=[dict(t=s["t"], price=s["price"], label=s["label"]) for s in swings["posredni"] if s["t"] >= candles[-400]["t"]],
    )


# ----------------------------------------------------------------- BTC
def _status(v, good, bad, higher_is_good=True):
    if v is None:
        return "brak"
    if higher_is_good:
        return "pozytywny" if v >= good else "negatywny" if v <= bad else "neutralny"
    return "pozytywny" if v <= good else "negatywny" if v >= bad else "neutralny"


def analyze_btc(daily, weekly, price, onchain, derivs, oi_hist, fng, glob, sources):
    closes = [c["c"] for c in daily]
    T = indicator_table(daily)
    i = len(daily) - 1
    g = lambda k, j=i: num(T[k][j])
    s50, s200 = g("sma50"), g("sma200")
    wk = [c["c"] for c in weekly] if weekly else []
    sma200w = float(np.mean(wk[-200:])) if len(wk) >= 200 else None
    rsi_w = None
    if len(wk) >= 20:
        from sonic_core import rsi as _rsi
        rsi_w = num(_rsi(wk)[-1])
    chg = lambda k: pct(price, daily[-1 - k]["c"]) if len(daily) > k else None
    mayer = price / s200 if s200 else None
    metrics = []

    def add(key, name, value, fmt, status, explain):
        metrics.append(dict(key=key, name=name, value=value, fmt=fmt, status=status, explain=explain))

    add("trend200", "Cena vs średnia 200 dni", pct(price, s200), "pct",
        _status(pct(price, s200), 0.0001, -0.0001),
        "Nad średnią 200-dniową rynek jest w trendzie wzrostowym, pod nią – w spadkowym. To najprostszy filtr hossy/bessy.")
    add("trend50", "Cena vs średnia 50 dni", pct(price, s50), "pct", _status(pct(price, s50), 0.0001, -0.0001),
        "Średnioterminowy kierunek: nad SMA50 kupujący mają przewagę w ostatnich tygodniach.")
    add("sma200w", "Cena vs średnia 200 tygodni", pct(price, sma200w), "pct", "pozytywny" if sma200w and price > sma200w * 1.5 else "neutralny" if sma200w and price > sma200w else ("negatywny" if sma200w else "brak"),
        "Średnia 200-tygodniowa to historyczne 'dno cyklu' – BTC rzadko schodził poniżej niej.")
    add("mayer", "Mnożnik Mayera (cena / SMA200)", mayer, "x",
        "ostrzeżenie" if mayer and mayer > 2.4 else "pozytywny" if mayer and mayer < 0.8 else "neutralny" if mayer else "brak",
        "Powyżej 2.4 historycznie oznaczało przegrzanie, poniżej 0.8 – okazje w dołkach.")
    add("rsi", "RSI dzienny (14)", g("rsi"), "num",
        "ostrzeżenie" if (g("rsi") or 50) >= 70 else "pozytywny" if (g("rsi") or 50) <= 30 else "neutralny",
        "Siła ruchu: >70 rynek wykupiony (ryzyko korekty), <30 wyprzedany (szansa na odbicie).")
    add("rsi_w", "RSI tygodniowy (14)", rsi_w, "num",
        "ostrzeżenie" if (rsi_w or 50) >= 75 else "pozytywny" if (rsi_w or 50) <= 35 else "neutralny",
        "To samo w skali tygodni – mniej szumu, lepiej pokazuje fazę cyklu.")
    h_now, h_prev = g("macd_hist"), g("macd_hist", i - 1)
    add("macd", "MACD (histogram)", h_now, "num",
        "pozytywny" if (h_now or 0) > 0 and (h_now or 0) >= (h_prev or 0) else "negatywny" if (h_now or 0) < 0 and (h_now or 0) <= (h_prev or 0) else "neutralny",
        "Dodatni i rosnący = przyspieszanie wzrostów; ujemny i malejący = przyspieszanie spadków.")
    if onchain and onchain.get("realized_price"):
        rp = onchain["realized_price"]
        add("realized", "Realized Price (średni koszt zakupu wszystkich BTC)", rp, "usd",
            "pozytywny" if price > rp * 1.2 else "neutralny" if price > rp else "negatywny",
            "Średnia cena, po której każdy BTC ostatnio zmienił właściciela. Cena pod nią = przeciętny posiadacz jest na stracie (historycznie strefa dna).")
    if onchain and onchain.get("mvrv"):
        mv = onchain["mvrv"]
        add("mvrv", "MVRV (wycena rynkowa / realizowana)", mv, "x",
            "ostrzeżenie" if mv >= 3.2 else "pozytywny" if mv <= 1.0 else "neutralny",
            "Ile razy rynek jest 'nad' średnim kosztem. Powyżej 3.2 szczyty cykli, poniżej 1 dołki cykli.")
    oi_vals = [d for d in derivs if d and d.get("oi_usd")]
    if oi_vals:
        tot = sum(d["oi_usd"] for d in oi_vals)
        ch7 = None
        if oi_hist and len(oi_hist) >= 8:
            ch7 = pct(oi_hist[-1][1], oi_hist[-8][1])
        add("oi", "Open Interest (otwarte kontrakty)", tot, "usd_big",
            "ostrzeżenie" if ch7 and ch7 > 15 else "neutralny",
            f"Suma otwartych pozycji na kontraktach ({', '.join(d['source'] for d in oi_vals)}). Szybki wzrost OI przy rosnącej cenie = dużo dźwigni i ryzyko gwałtownych likwidacji."
            + (f" Zmiana OI na OKX w 7 dni: {ch7:+.1f}%." if ch7 is not None else ""))
    fr = [d["funding_8h_pct"] for d in derivs if d and d.get("funding_8h_pct") is not None]
    if fr:
        f_avg = statistics.mean(fr)
        add("funding", "Funding (opłata long↔short, 8h)", f_avg, "pct3",
            "ostrzeżenie" if f_avg > 0.03 else "pozytywny" if f_avg < 0 else "neutralny",
            "Dodatni: longi płacą shortom (przewaga optymistów). Wysoki (>0.03%/8h) = przegrzanie; ujemny = pesymizm, często paliwo do odbicia.")
    if fng:
        add("fng", "Indeks strachu i chciwości", fng["value"], "int",
            "pozytywny" if fng["value"] <= 25 else "ostrzeżenie" if fng["value"] >= 75 else "neutralny",
            f"Nastrój rynku 0–100 ({fng['label']}). Skrajny strach bywał okazją, skrajna chciwość – ostrzeżeniem."
            + (f" Tydzień temu: {fng['week_ago']}." if fng.get("week_ago") is not None else ""))
    if glob:
        add("dominance", "Dominacja BTC", glob["btc_dominance"], "pct_plain", "neutralny",
            "Udział BTC w kapitalizacji całego rynku. Rosnąca dominacja = kapitał ucieka z altcoinów (zła wiadomość dla Sonica), spadająca = sezon altów.")

    # kontekst dla altcoinów (argument przy sygnale Sonic)
    score = 0.0
    score += 1 if s200 and price > s200 else -1
    score += 0.5 if (h_now or 0) > 0 else -0.5
    if onchain and onchain.get("mvrv"):
        score += 0.5 if onchain["mvrv"] < 1.5 else -0.5 if onchain["mvrv"] > 3 else 0
    if fng:
        score += 0.5 if fng["value"] < 25 else -0.5 if fng["value"] > 80 else 0
    if fr:
        score += -0.5 if statistics.mean(fr) > 0.03 else 0.25 if statistics.mean(fr) < 0 else 0
    label = "sprzyjające" if score >= 1 else "niesprzyjające" if score <= -1 else "neutralne"
    return dict(price=price, sources=sources, change_24h_pct=chg(1), change_7d_pct=chg(7), change_30d_pct=chg(30),
                sma50=s50, sma200=s200, sma200w=sma200w, mayer=mayer, rsi=g("rsi"), rsi_w=rsi_w, macd_hist=h_now,
                onchain=onchain, derivatives=[d for d in derivs if d], oi_7d_change_pct=(pct(oi_hist[-1][1], oi_hist[-8][1]) if oi_hist and len(oi_hist) >= 8 else None),
                fng=fng, global_=glob, metrics=metrics, context_score=score, context_label=label,
                chart=[dict(t=c["t"], c=c["c"]) for c in daily[-365:]],
                sma200_series=[num(x) for x in T["sma200"][-365:]])
