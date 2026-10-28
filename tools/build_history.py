# -*- coding: utf-8 -*-
"""
Generator bazy wiedzy historycznej Sonic/FTM -> sonic_history.py

Uruchom lokalnie (nie w GitHub Actions), gdy chcesz odświeżyć analizę na nowszych danych:
    python tools/build_history.py
Wymaga: data/SONIC_FTM_S_1d.csv (format Binance klines, FTMUSDT do 2025-01-13, potem SUSDT).
"""
import os, sys, csv, math, pprint
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sonic_core import (indicator_table, zigzag, legs_from_swings, find_ranges, turning_point_flags,
                        backtest_setup, BOTTOM_CONDITIONS, TOP_CONDITIONS, ts_date, nearest_fib, SETUPS)

DEGREES = {"cykl": 0.80, "glowny": 0.60, "posredni": 0.40, "drobny": 0.25}


def load_csv(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(dict(t=int(r["open_time"]) // 1000, o=float(r["open"]), h=float(r["high"]), l=float(r["low"]),
                            c=float(r["close"]), v=float(r["volume"]), qv=float(r["quote_asset_volume"]),
                            sym=r.get("source_symbol", "")))
    return out


def r6(x):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    if isinstance(x, (float, np.floating)):
        x = float(x)
        return float(f"{x:.6g}")
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return r6(o)


def main():
    C = load_csv(os.path.join(ROOT, "data", "SONIC_FTM_S_1d.csv"))
    T = indicator_table(C)
    n = len(C)

    # --- ekstrema roczne (weryfikacja tabeli Gemini)
    yearly = []
    for y in sorted({ts_date(x["t"])[:4] for x in C}):
        ys = [x for x in C if ts_date(x["t"]).startswith(y)]
        hi = max(ys, key=lambda x: x["h"]); lo = min(ys, key=lambda x: x["l"])
        yearly.append(dict(year=int(y), high=hi["h"], high_date=ts_date(hi["t"]), low=lo["l"], low_date=ts_date(lo["t"])))

    # --- swingi na 4 poziomach + odczyty wskaźników
    swings = {}
    for name, pct in DEGREES.items():
        sw = zigzag(C, pct)
        rows = []
        for s in sw:
            i = s["i"]; x = C[i]
            wick = ((x["h"] / max(x["c"], x["o"]) - 1) if s["kind"] == "H" else (1 - x["l"] / min(x["c"], x["o"]))) * 100
            rows.append(dict(date=s["date"], price=s["price"], kind=s["kind"], label=s["label"], confirmed=s["confirmed"],
                             close=x["c"], wick_pct=wick, rsi=T["rsi"][i], rsi_w=T["rsi_w"][i],
                             dist_sma50_pct=T["dist_sma50"][i], dist_sma200_pct=T["dist_sma200"][i],
                             pctb=T["pctb"][i], vol_ratio=T["vol_ratio"][i]))
        swings[name] = rows

    # --- fale (odcinki) z relacjami Fibonacciego
    legs = {}
    for name in ("cykl", "glowny", "posredni"):
        sw = [s for s in zigzag(C, DEGREES[name])]
        lg = legs_from_swings(sw)
        for g in lg:
            g["retrace_fib"] = nearest_fib(g.get("retrace_lin"))
        legs[name] = lg

    # --- statystyki Fibonacciego
    fib_stats = {}
    edges = [0, 0.382, 0.5, 0.618, 0.786, 0.886, 1.0, 1.5, 99]
    names = ["<38.2%", "38.2–50%", "50–61.8%", "61.8–78.6%", "78.6–88.6%", "88.6–100%", "100–150%", ">150%"]
    for name in ("glowny", "posredni", "drobny"):
        lg = legs_from_swings([s for s in zigzag(C, DEGREES[name]) if s["confirmed"]])
        up = [g for g in lg if g["direction"] == "wzrost"]; dn = [g for g in lg if g["direction"] == "spadek"]
        corr = [g["retrace_lin"] for g in dn if g.get("retrace_lin")]
        bnc = [g["retrace_lin"] for g in up if g.get("retrace_lin")]
        fib_stats[name] = dict(
            wzrost_mediana_pct=np.median([g["change_pct"] for g in up]), wzrost_mediana_dni=np.median([g["days"] for g in up]),
            spadek_mediana_pct=np.median([g["change_pct"] for g in dn]), spadek_mediana_dni=np.median([g["days"] for g in dn]),
            korekta_po_wzroscie_percentyle_25_50_75=list(np.percentile(corr, [25, 50, 75])),
            odbicie_po_spadku_percentyle_25_50_75=list(np.percentile(bnc, [25, 50, 75])),
            korekty_rozklad=dict(zip(names, map(int, np.histogram(corr, edges)[0]))),
            odbicia_rozklad=dict(zip(names, map(int, np.histogram(bnc, edges)[0]))),
        )

    # --- konsolidacje
    ranges = find_ranges(C)

    # --- profil punktów zwrotnych: częstość warunków przy dołkach/szczytach vs wszystkie dni
    inter = zigzag(C, DEGREES["posredni"]); minor = zigzag(C, DEGREES["drobny"])
    tops = [s["i"] for s in inter if s["kind"] == "H"]; bots = [s["i"] for s in inter if s["kind"] == "L"]
    near_t = np.zeros(n, bool); near_b = np.zeros(n, bool)
    for i in tops: near_t[max(0, i - 5):i + 6] = True
    for i in bots: near_b[max(0, i - 5):i + 6] = True
    valid = [i for i in range(n) if not np.isnan(T["dist_sma50"][i])]
    flags = {i: turning_point_flags(C, T, minor, i) for i in valid}

    def cond_table(conds, which, idx_swings, near):
        out = []
        for key, label in conds:
            all_days = [i for i in valid if flags[i][which][key]]
            at_sw = [i for i in idx_swings if i in flags]
            out.append(dict(warunek=label, klucz=key,
                            przy_punktach_zwrotnych_pct=100 * np.mean([flags[i][which][key] for i in at_sw]),
                            wszystkie_dni_pct=100 * len(all_days) / len(valid),
                            precyzja_pct=100 * np.mean(near[all_days]) if all_days else None, dni=len(all_days)))
        return out

    def fwd(i, d=30):
        seg = C[i + 1:i + 1 + d]
        if len(seg) < d:
            return None
        return (max(x["h"] for x in seg) / C[i]["c"] - 1) * 100, (1 - min(x["l"] for x in seg) / C[i]["c"]) * 100

    def score_table(which, near, conds):
        out = []
        for k in range(1, len(conds) + 1):
            idx = [i for i in valid if sum(flags[i][which].values()) >= k and i < n - 30]
            if len(idx) < 5:
                continue
            f = [fwd(i) for i in idx]
            out.append(dict(min_warunkow=k, dni=len(idx), precyzja_pct=100 * np.mean(near[idx]),
                            mediana_max_wzrost_30d_pct=np.median([x[0] for x in f]),
                            mediana_max_spadek_30d_pct=np.median([x[1] for x in f])))
        base = [i for i in valid if i < n - 30]
        out.append(dict(min_warunkow=0, dni=len(base), precyzja_pct=100 * np.mean(near[base]),
                        mediana_max_wzrost_30d_pct=np.median([fwd(i)[0] for i in base]),
                        mediana_max_spadek_30d_pct=np.median([fwd(i)[1] for i in base])))
        return out

    profile = dict(
        dolki=dict(warunki=cond_table(BOTTOM_CONDITIONS, 0, bots, near_b), wynik=score_table(0, near_b, BOTTOM_CONDITIONS),
                   mediany=dict(rsi=np.nanmedian([T["rsi"][i] for i in bots]), dist_sma50=np.nanmedian([T["dist_sma50"][i] for i in bots]),
                                pctb=np.nanmedian([T["pctb"][i] for i in bots]), rsi_w=np.nanmedian([T["rsi_w"][i] for i in bots]))),
        szczyty=dict(warunki=cond_table(TOP_CONDITIONS, 1, tops, near_t), wynik=score_table(1, near_t, TOP_CONDITIONS),
                     mediany=dict(rsi=np.nanmedian([T["rsi"][i] for i in tops]), dist_sma50=np.nanmedian([T["dist_sma50"][i] for i in tops]),
                                  pctb=np.nanmedian([T["pctb"][i] for i in tops]), rsi_w=np.nanmedian([T["rsi_w"][i] for i in tops]))),
        uwaga="Precyzja = jaki odsetek dni spełniających warunek leżał ±5 dni od punktu zwrotnego poziomu pośredniego (swing 40%).",
    )

    # --- backtest wybranych setupów
    setups = {}
    for side in ("LONG", "SHORT"):
        summ, trades = backtest_setup(C, side)
        setups[side] = dict(parametry=SETUPS[side], podsumowanie=summ, transakcje=trades)

    data = dict(
        META=dict(zrodlo="Binance FTMUSDT (2019-06-11 – 2025-01-13) + SUSDT (od 2025-01-16), świece 1D",
                  od=ts_date(C[0]["t"]), do=ts_date(C[-1]["t"]), swiec=n,
                  wygenerowano=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                  uwaga="FTM→S 1:1 (migracja styczeń 2025); 14–15.01.2025 brak notowań w trakcie zamiany."),
        YEARLY_EXTREMES=yearly, SWINGS=swings, LEGS=legs, FIB_STATS=fib_stats, RANGES=ranges,
        TURNING_POINT_PROFILE=profile, SETUP_BACKTEST=setups,
    )
    data = clean(data)

    out = os.path.join(ROOT, "sonic_history.py")
    with open(out, "w", encoding="utf-8") as f:
        f.write('# -*- coding: utf-8 -*-\n"""\nBaza wiedzy historycznej Sonic (S) / Fantom (FTM).\n'
                'PLIK GENEROWANY przez tools/build_history.py – nie edytuj ręcznie sekcji danych.\n'
                'Ręcznie pisane są tylko: ELLIOTT_INTERPRETATION, CONCLUSIONS, SETUP_RESEARCH (na końcu pliku).\n"""\n\n')
        for k, v in data.items():
            f.write(f"{k} = " + pprint.pformat(v, width=150, sort_dicts=False) + "\n\n")
        f.write(open(os.path.join(ROOT, "tools", "history_notes.py.txt"), encoding="utf-8").read())
    print("Zapisano", out)


if __name__ == "__main__":
    main()
