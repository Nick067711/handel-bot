# -*- coding: utf-8 -*-
"""
Plan etapowy Sonica (data/sonic_plan.json) i sygnały na świecach 1h.

Bot odtwarza plan na zamkniętych świecach 1h od chwili zapisanej w plan["start"]:
  BUY  – czekamy na zakup w strefie (start, odkup po sprzedaży albo kupno na wybiciu),
  HOLD – trzymamy Sonica i czekamy na strefę sprzedaży kolejnego kroku.
Każde przejście daje zdarzenie. Na ntfy idą tylko zdarzenia kluczowe (zakup, sprzedaż, wybicie,
nieudany odkup, unieważnienie planu) oraz przypomnienia „przygotuj zlecenie”, gdy cena jest blisko poziomu.
Odtwarzanie jest deterministyczne – ten sam zestaw świec zawsze daje te same zdarzenia, więc stan
na FTP przechowuje tylko listę już wysłanych sygnałów.
"""
import os, json, math
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
PLAN_PATH = os.path.join(ROOT, "data", "sonic_plan.json")
HOUR = 3600


def load_plan(path=PLAN_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def iso_ts(s):
    s = s.replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def date_ts(s):
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def pct(a, b):
    return (a / b - 1) * 100 if a and b else None


# ----------------------------------------------------------------- struktura planu
def trade_steps(plan):
    """Główne kroki planu (bez pośrednich). Krok 'start' ma tylko strefę zakupu."""
    return [s for s in plan["steps"] if not s.get("checkpoint") and not s.get("intermediate")]


def mids_before(plan, sid):
    """Kroki pośrednie leżące bezpośrednio przed głównym krokiem sid (między nim a poprzednim krokiem głównym)."""
    out = []
    for s in plan["steps"]:
        if s["id"] == sid:
            return out
        if s.get("intermediate"):
            out.append(s)
        elif not s.get("checkpoint"):
            out = []
    return []


def r4(x):
    return round(x, 4) if x < 0.1 else round(x, 3)


def sell_limit(lo, main):
    """Wariant ostrożny: zlecenie sprzedaży w połowie między dolną granicą widełek a głównym poziomem."""
    return r4((lo + main) / 2)


def mid_info(s):
    return dict(id=s["id"], label=s["label"], lo=s["sell_range"][0], hi=s["sell_range"][1], main=s["sell"],
                limit=sell_limit(s["sell_range"][0], s["sell"]),
                rebuy_lo=s["rebuy_range"][0], rebuy_hi=s["rebuy_range"][1], rebuy=s["rebuy"], rebuy_limit=s["rebuy_range"][1],
                breakout=s.get("breakout", s["sell"]), note=s.get("note"), status="pending")


def mids_for(plan, target):
    if not target or not plan["signal_rules"].get("intermediate_alerts", True):
        return []
    return [mid_info(m) for m in mids_before(plan, target["step"])]


def step_by_id(plan, sid):
    return next(s for s in plan["steps"] if s["id"] == sid)


def next_trade_step(plan, sid):
    ts = trade_steps(plan)
    i = [s["id"] for s in ts].index(sid)
    return ts[i + 1] if i + 1 < len(ts) else None


def buy_zone(plan, sid):
    """Strefa zakupu „po” kroku sid: dla 'start' strefa akumulacji, dla innych – odkup po sprzedaży."""
    s = step_by_id(plan, sid)
    if sid == "start":
        lo, hi = s["buy_range"]
        return dict(step=sid, lo=lo, hi=hi, main=s["buy"], trigger=s["buy"], limit=s["buy"], breakout=s.get("breakout"),
                    label="strefa akumulacji", note=s.get("note"))
    if not s.get("rebuy_range"):
        return None
    lo, hi = s["rebuy_range"]
    return dict(step=sid, lo=lo, hi=hi, main=s["rebuy"], trigger=hi, limit=hi, breakout=s.get("breakout", s["sell"]),
                label="odkup po kroku " + s["label"].lower().replace("krok ", ""), note=s.get("rebuy_note"))


def sell_zone(plan, sid):
    s = step_by_id(plan, sid)
    lo, hi = s["sell_range"]
    return dict(step=sid, lo=lo, hi=hi, main=s["sell"], limit=s.get("sell_limit") or sell_limit(lo, s["sell"]), label=s["label"], final=s.get("final", False),
                condition=s.get("condition"))


def all_levels(plan):
    """Płaska lista poziomów planu (do drabinki i do analysis.build_levels)."""
    out = []
    for s in plan["steps"]:
        if s.get("checkpoint"):
            out.append(dict(price=s["level"], lo=s["level_range"][0], hi=s["level_range"][1], kind="check",
                            step=s["id"], name=f"{s['label']} {fmt(s['level'])}"))
            continue
        if s.get("intermediate"):
            out.append(dict(price=s["sell"], lo=s["sell_range"][0], hi=s["sell_range"][1], kind="mid", step=s["id"],
                            name=f"{s['label']} (pośredni) {fmt(s['sell'])}"))
            out.append(dict(price=s["rebuy"], lo=s["rebuy_range"][0], hi=s["rebuy_range"][1], kind="midbuy", step=s["id"],
                            name=f"Odkup po pośrednim {s['id']} {fmt(s['rebuy'])}"))
            continue
        if s["id"] == "start":
            out.append(dict(price=s["buy"], lo=s["buy_range"][0], hi=s["buy_range"][1], kind="buy", step="start",
                            name=f"Strefa zakupu (start) {fmt(s['buy_range'][0])}–{fmt(s['buy_range'][1])}"))
            continue
        out.append(dict(price=s["sell"], lo=s["sell_range"][0], hi=s["sell_range"][1], kind="sell", step=s["id"],
                        name=f"Sprzedaż – {s['label']} {fmt(s['sell'])}"))
        if s.get("rebuy"):
            out.append(dict(price=s["rebuy"], lo=s["rebuy_range"][0], hi=s["rebuy_range"][1], kind="rebuy", step=s["id"],
                            name=f"Odkup po: {s['label']} {fmt(s['rebuy'])}"))
    for z in deep_zones(plan):
        out.append(dict(price=round((z["lo"] + z["hi"]) / 2, 5), lo=z["lo"], hi=z["hi"], kind="support", step=z["id"],
                        name=f"Wsparcie {z['id']} {fmt(z['lo'])}–{fmt(z['hi'])} ({z['label']})"))
    return out


def deep_zones(plan):
    """Strefy wsparcia poniżej planu (korekta rozszerzona), od najwyższej."""
    return sorted((plan.get("deep_supports") or {}).get("zones") or [], key=lambda z: -z["hi"])


def invalidation_level(plan):
    """Poziom unieważnienia albo None, gdy wyłączone."""
    inv = plan.get("invalidation") or {}
    if inv.get("enabled") is False:
        return None
    return inv.get("daily_close_below")


def deep_events(plan, candles_1h, t_from):
    """DEEP_TOUCH – pierwsze wejście ceny w strefę wsparcia; DEEP_LOST – 2 zamknięcia 1h pod dolną granicą."""
    ds = plan.get("deep_supports") or {}
    zones = deep_zones(plan)
    if not zones:
        return []
    need = ds.get("lost_closes_1h", 2)
    st = {z["id"]: dict(touched=False, lost=False, below=0) for z in zones}
    ev = []
    first = True
    for c in candles_1h:
        if c["t"] < t_from:
            continue
        if first:      # cena już w strefie w chwili startu planu – bez alertu „wejście w strefę”
            for z in zones:
                if c["l"] <= z["hi"] and c["h"] >= z["lo"]:
                    st[z["id"]]["touched"] = True
            first = False
        for i, z in enumerate(zones):
            s = st[z["id"]]
            below = [x for x in zones[i + 1:]]
            nxt = below[0] if below else None
            if not s["touched"] and c["l"] <= z["hi"]:
                s["touched"] = True
                ev.append(dict(kind="DEEP_TOUCH", t=c["t"] + HOUR, step=z["id"], price=c["c"], high=c["h"], low=c["l"],
                               support=z, next_support=nxt))
            if s["touched"] and not s["lost"]:
                s["below"] = s["below"] + 1 if c["c"] < z["lo"] else 0
                if s["below"] >= need:
                    s["lost"] = True
                    ev.append(dict(kind="DEEP_LOST", t=c["t"] + HOUR, step=z["id"], price=c["c"], high=c["h"], low=c["l"],
                                   support=z, next_support=nxt))
    return ev


def fmt(x):
    """Polski zapis ceny: 0,0400 / 0,03328 / 0,120 / 64 364."""
    if x is None:
        return "brak"
    if x >= 1000:
        return f"{x:,.0f}".replace(",", " ")
    if x >= 1:
        return f"{x:.3f}".replace(".", ",")
    dec = max(4, -int(math.floor(math.log10(abs(x)))) + 3)
    s = f"{x:.{dec}f}"
    keep = 3 if x >= 0.1 else 4
    while s.endswith("0") and len(s.split(".")[1]) > keep:
        s = s[:-1]
    return s.replace(".", ",")


# ----------------------------------------------------------------- odtwarzanie planu na 1h
def rsi_series(closes, n=14):
    out, g, l = [None] * len(closes), 0.0, 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
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


def channel_events(plan, candles_1h, t_from, sold_after=None):
    """Alerty kanału (schodka): PRE_BREAKOUT, CHANNEL_BREAKOUT, UPDATE_2D. Wolumen = obrót w USD (v*c)."""
    ch = plan.get("channel")
    if not ch or not candles_1h:
        return []
    ev = []
    closes = [c["c"] for c in candles_1h]
    rs = rsi_series(closes)
    qv = [c.get("v", 0) * c["c"] for c in candles_1h]
    top = ch["ceiling"]
    brk = top * (1 + ch.get("break_pct", 1.0) / 100)
    last_pre, broke = None, None

    def avg48(i):
        w = qv[max(0, i - 48):i]
        return sum(w) / len(w) if w else 0

    for i, c in enumerate(candles_1h):
        if c["t"] < t_from:
            continue
        a = avg48(i)
        if not broke:
            mx6 = max(qv[max(0, i - 5):i + 1]) / a if a else 0
            mx24 = max(qv[max(0, i - 23):i + 1]) / a if a else 0
            if c["c"] > brk and mx24 >= ch.get("vol_mult", 3.0):
                broke = c
                ev.append(dict(kind="CHANNEL_BREAKOUT", t=c["t"] + HOUR, step=ch["id"], price=c["c"], high=c["h"], low=c["l"],
                               channel=ch, vol_x=round(mx24, 1)))
                continue
            near = top * (1 - ch.get("pre_dist_pct", 5.0) / 100) <= c["c"] <= brk
            if near and rs[i] and rs[i] >= ch.get("pre_rsi", 63) and mx6 >= ch.get("pre_vol_mult", 2.5) and \
                    (last_pre is None or c["t"] - last_pre >= 24 * HOUR):
                last_pre = c["t"]
                ev.append(dict(kind="PRE_BREAKOUT", t=c["t"] + HOUR, step=ch["id"], price=c["c"], high=c["h"], low=c["l"],
                               channel=ch, rsi=round(rs[i]), vol_x=round(mx6, 1)))
        else:
            if c["t"] >= broke["t"] + 48 * HOUR and not any(e["kind"] == "UPDATE_2D" for e in ev):
                if sold_after is None or sold_after > c["t"]:
                    hi = max(x["h"] for x in candles_1h if broke["t"] <= x["t"] <= c["t"])
                    ev.append(dict(kind="UPDATE_2D", t=c["t"] + HOUR, step=ch["id"], price=c["c"], high=c["h"], low=c["l"],
                                   channel=ch, peak=hi))
    return ev


def replay(plan, candles_1h, daily=None, now_ts=None):
    """
    candles_1h: zamknięte świece 1h rosnąco [{t,o,h,l,c}], t = otwarcie świecy (UTC).
    daily: zamknięte świece dzienne (do unieważnienia planu).
    Zwraca dict(state=..., events=[...]).
    """
    R = plan["signal_rules"]
    st0 = plan["start"]
    t_from = iso_ts(st0["from"])
    phase, sid = st0["phase"], st0["step"]
    zone = buy_zone(plan, sid) if phase == "BUY" else None
    target = sell_zone(plan, sid) if phase == "HOLD" else sell_zone(plan, next_trade_step(plan, sid)["id"])
    last_fill = None          # strefa ostatniego zakupu (do reguły „odkup zawiódł”)
    mids = mids_for(plan, target)
    events, prep_sent = [], set()
    above = below = 0
    armed = False
    invalid = False
    inv_level = invalidation_level(plan)
    daily_closes = {c["t"] // 86400: c["c"] for c in (daily or [])}

    def ev(kind, c, **kw):
        events.append(dict(kind=kind, t=c["t"] + HOUR, step=kw.pop("step", None), price=c["c"], high=c["h"], low=c["l"], **kw))

    for c in candles_1h:
        if c["t"] < t_from:
            continue
        # unieważnienie planu: zamknięcie dnia poprzedniego poniżej poziomu
        if inv_level is not None and not invalid and c["t"] % 86400 == 0:
            prev = daily_closes.get(c["t"] // 86400 - 1)
            if prev is not None and prev < inv_level:
                invalid = True
                ev("INVALID", c, close=prev, step=None)
        if invalid:
            continue

        if phase == "BUY":
            # przypomnienie: cena blisko strefy zakupu
            key = ("PREP_BUY", zone["step"], zone["lo"])
            if key not in prep_sent and c["l"] <= zone["trigger"] * (1 + R["prep_buy_pct"] / 100) and c["l"] > zone["trigger"]:
                prep_sent.add(key)
                ev("PREP_BUY", c, step=zone["step"], zone=zone, target=target)
            if c["l"] <= zone["trigger"]:
                ev("BUY", c, step=zone["step"], zone=zone, target=target, fill=min(zone["trigger"], c["o"]))
                last_fill, phase, above, below, armed = zone, "HOLD", 0, 0, False
                mids = mids_for(plan, target)
                continue
            bo = zone.get("breakout")
            if bo and c["c"] < bo:
                armed = True          # wybicie liczy się dopiero po powrocie ceny pod poziom
            if bo and armed:
                above = above + 1 if c["c"] > bo * (1 + R["breakout_buffer_pct"] / 100) else 0
                if above >= R["breakout_closes_1h"]:
                    ev("BUY_BREAKOUT", c, step=zone["step"], zone=zone, target=target, level=bo)
                    last_fill, phase, above, below, armed = dict(zone, lo=bo * 0.97, hi=bo), "HOLD", 0, 0, False
                    mids = mids_for(plan, target)
                    continue
        else:  # HOLD
            # kroki pośrednie: sygnał „obserwuj / decyzja na gorąco” i – jeśli się sprzedało – odkup lub wybicie
            for m in mids:
                if m["status"] == "pending":
                    if ("PREP_MID", m["id"]) not in prep_sent and m["lo"] * (1 - R["prep_sell_pct"] / 100) <= c["h"] < m["lo"]:
                        prep_sent.add(("PREP_MID", m["id"]))
                        ev("PREP_MID", c, step=m["id"], mid=dict(m), target=target)
                    if c["h"] >= m["lo"]:
                        m["status"], m["above"], m["armed"] = "touched", 0, False
                        ev("MID_SELL", c, step=m["id"], mid=dict(m), target=target)
                elif m["status"] == "touched":
                    if c["l"] <= m["rebuy_hi"]:
                        m["status"] = "done"
                        ev("MID_REBUY", c, step=m["id"], mid=dict(m), target=target)
                        continue
                    if c["c"] < m["breakout"]:
                        m["armed"] = True
                    if m["armed"]:
                        m["above"] = m["above"] + 1 if c["c"] > m["breakout"] * (1 + R["breakout_buffer_pct"] / 100) else 0
                        if m["above"] >= R["breakout_closes_1h"]:
                            m["status"] = "done"
                            ev("MID_BREAKOUT", c, step=m["id"], mid=dict(m), target=target, level=m["breakout"])
            key = ("PREP_SELL", target["step"])
            if key not in prep_sent and c["h"] >= target["lo"] * (1 - R["prep_sell_pct"] / 100) and c["h"] < target["lo"]:
                prep_sent.add(key)
                ev("PREP_SELL", c, step=target["step"], target=target)
            if c["h"] >= target["lo"]:
                nz = buy_zone(plan, target["step"])
                ev("SELL", c, step=target["step"], target=target, zone=nz)
                if target.get("final") or nz is None:
                    phase, sid = "DONE", target["step"]
                    break
                phase, zone, mids = "BUY", nz, []
                nxt = next_trade_step(plan, target["step"])
                target = sell_zone(plan, nxt["id"]) if nxt else None
                above = below = 0
                armed = False
                continue
            # odkup zawiódł: dwa zamknięcia 1h pod dolną granicą strefy, w której kupiliśmy
            if last_fill and not last_fill.get("failed"):
                below = below + 1 if c["c"] < last_fill["lo"] else 0
                if below >= R["fail_closes_1h"]:
                    last_fill["failed"] = True
                    sup = next_support(plan, last_fill["lo"])
                    ev("STEP_FAIL", c, step=last_fill["step"], zone=last_fill, support=sup, target=target)

    first_sell = next((e["t"] for e in events if e["kind"] == "SELL"), None)
    events = sorted(events + channel_events(plan, candles_1h, t_from, first_sell) + deep_events(plan, candles_1h, t_from),
                    key=lambda e: e["t"])
    last = candles_1h[-1] if candles_1h else None
    state = dict(phase="INVALID" if invalid else phase, zone=zone if phase == "BUY" else None,
                 target=target, last_fill=last_fill, price=last["c"] if last else None,
                 last_candle=last["t"] if last else None, mids=mids if phase == "HOLD" else [],
                 armed=armed, start_from=st0["from"])
    return dict(state=state, events=events)


def next_support(plan, below_price):
    zones = []
    for s in trade_steps(plan):
        z = buy_zone(plan, s["id"])
        if z and z["hi"] < below_price:
            zones.append(z)
    if zones:
        return max(zones, key=lambda z: z["hi"])
    deep = [z for z in deep_zones(plan) if z["hi"] < below_price] or deep_zones(plan)[-1:]
    if deep:
        z = deep[0]
        return dict(lo=z["lo"], hi=z["hi"], main=round((z["lo"] + z["hi"]) / 2, 5), label=f"wsparcie {z['id']} – {z['label']}", step=None)
    return None


# ----------------------------------------------------------------- położenie ceny na mapie planu
def position(plan, price, state):
    """Gdzie jest cena względem całej mapy: najbliższe poziomy planu nad i pod ceną, postęp etapu."""
    lv = sorted([l for l in all_levels(plan) if l["kind"] != "invalid"], key=lambda l: l["price"])
    below = [l for l in lv if l["price"] <= price]
    above = [l for l in lv if l["price"] > price]
    seq = [s for s in plan["steps"] if s.get("sell")]
    first, top1 = step_by_id(plan, "start")["buy"], step_by_id(plan, "5")["sell"]
    top2 = step_by_id(plan, "2d")["sell"]
    prog = lambda a, b: max(0.0, min(100.0, (math.log(price) - math.log(a)) / (math.log(b) - math.log(a)) * 100))
    return dict(below=below[-3:][::-1], above=above[:3], to_stage1_top_pct=pct(top1, price), to_stage2_top_pct=pct(top2, price),
                progress_stage1=prog(first, top1), progress_stage2=prog(first, top2), n_sell_steps=len(seq))


def next_action(plan, state):
    """Jedno zdanie: co robić teraz według planu."""
    ph = state["phase"]
    if ph == "INVALID":
        return (f"Plan unieważniony (zamknięcie dnia poniżej {fmt(invalidation_level(plan))}). "
                f"Gotówka; kolejne wsparcia {', '.join(fmt(z['lo']) + '–' + fmt(z['hi']) for z in deep_zones(plan)[:2])}.")
    if ph == "DONE":
        return "Plan zakończony – finał hossy zrealizowany. Gotówka."
    t = state.get("target")
    if ph == "BUY":
        z = state["zone"]
        txt = (f"Czekamy na zakup: {z['label']} {fmt(z['lo'])}–{fmt(z['hi'])}, zlecenie z limitem {fmt(z['limit'])}")
        if z.get("breakout"):
            txt += f"; jeśli nie zejdzie – kupno na wybiciu: 2 zamknięcia 1h powyżej {fmt(z['breakout'])}"
        if t:
            txt += f". Następna sprzedaż: {t['label']} – limit {fmt(t['limit'])} (widełki {fmt(t['lo'])}–{fmt(t['hi'])}, główny poziom {fmt(t['main'])})."
        return txt
    return (f"Trzymamy Sonica. Zlecenie sprzedaży z limitem: {t['label']} {fmt(t['limit'])} "
            f"(widełki {fmt(t['lo'])}–{fmt(t['hi'])}, główny poziom {fmt(t['main'])}; wariant ostrożny)."
            + (f" Warunek: {t['condition']}." if t.get("condition") else ""))


# ----------------------------------------------------------------- potwierdzenia trendu (do raportu)
def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def ema_last(vals, n):
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def confirmations(plan, sonic_daily, btc_daily):
    C = plan["confirmations"]
    sc = [c["c"] for c in sonic_daily]
    bc = [c["c"] for c in (btc_daily or [])]
    s_sma = sma(sc, C["sonic_sma100_days"])
    e20, e50 = ema_last(sc, C["sonic_ema_fast"]), ema_last(sc, C["sonic_ema_slow"])
    b_sma = sma(bc, C["btc_sma200_days"]) if bc else None
    out = [
        dict(name=f"Sonic: zamknięcie dnia powyżej SMA100 ({fmt(s_sma)})", ok=bool(s_sma and sc[-1] > s_sma), value=s_sma),
        dict(name=f"Sonic: EMA20 ({fmt(e20)}) powyżej EMA50 ({fmt(e50)})", ok=bool(e20 and e50 and e20 > e50), value=e20),
        dict(name=f"BTC: zamknięcie dnia powyżej SMA200 ({fmt(b_sma)})", ok=bool(b_sma and bc[-1] > b_sma), value=b_sma),
        dict(name=f"Potwierdzenie etapu 1: zamknięcie dnia Sonica powyżej {fmt(C['stage1_confirm_daily_close_above'])}",
             ok=sc[-1] > C["stage1_confirm_daily_close_above"], value=C["stage1_confirm_daily_close_above"]),
    ]
    return dict(items=out, sonic_sma100=s_sma, btc_sma200=b_sma, ema20=e20, ema50=e50)


# ----------------------------------------------------------------- BTC na tle poziomów
def btc_position(plan, price, sma200=None):
    B = plan["btc_levels"]
    lv = [dict(l) for l in B["levels"]]
    if sma200:
        lv.append(dict(price=sma200, kind="r" if sma200 > price else "s", label=f"SMA200 {fmt(sma200)}",
                       range=[sma200 * 0.995, sma200 * 1.005]))
    lv.sort(key=lambda l: -l["price"])
    for l in lv:
        l["dist_pct"] = pct(l["price"], price)
        l["inside"] = l["range"][0] <= price <= l["range"][1]
    above = [l for l in lv if l["price"] > price and not l["inside"]]
    below = [l for l in lv if l["price"] <= price and not l["inside"]]
    inside = [l for l in lv if l["inside"]]
    return dict(levels=lv, above=above[-1] if above else None, below=below[0] if below else None, inside=inside, sma200=sma200,
                beta=B.get("beta_sonic"), low=B.get("correction_low"), high=B.get("correction_high"),
                scenarios=B.get("scenarios", []))


# ----------------------------------------------------------------- ścieżka prognozy (do wykresu)
def projection_path(plan):
    """Punkty prognozy (data, cena, etykieta, typ) wg kolejności kroków planu."""
    pts = []
    for s in plan["steps"]:
        if s["id"] == "start":
            continue
        if s.get("checkpoint"):
            pts.append(dict(t=date_ts(s["eta"]), price=s["level"], kind="check", label=s["label"], step=s["id"],
                            lo=s["level_range"][0], hi=s["level_range"][1]))
            continue
        mid = bool(s.get("intermediate"))
        pts.append(dict(t=date_ts(s["eta"]), price=s["sell"], kind="top", label=s["label"], step=s["id"], mid=mid,
                        lo=s["sell_range"][0], hi=s["sell_range"][1]))
        if s.get("rebuy"):
            pts.append(dict(t=date_ts(s["rebuy_eta"]), price=s["rebuy"], kind="bottom", label="odkup", step=s["id"], mid=mid,
                            lo=s["rebuy_range"][0], hi=s["rebuy_range"][1]))
    return pts


# ----------------------------------------------------------------- czuwanie w trakcie świecy (cena bieżąca)
def live_watches(plan, state):
    """Poziomy do pilnowania na żywo w bieżącym stanie planu. cls = klasa sygnału (wspólna z sygnałami z zamkniętych świec)."""
    W = []
    ph = state["phase"]
    if ph == "HOLD" and state.get("target"):
        t = state["target"]
        W.append(dict(cls=f"sell:{t['step']}", side="up", lo=t["lo"], hi=t["hi"], level=t["lo"], step=t["step"], target=t, kind="SELL"))
        for m in state.get("mids") or []:
            if m["status"] == "pending":
                W.append(dict(cls=f"mid:{m['id']}", side="up", lo=m["lo"], hi=m["hi"], level=m["lo"], step=m["id"], mid=m, target=t, kind="MID"))
            elif m["status"] == "touched":
                W.append(dict(cls=f"midbuy:{m['id']}", side="down", lo=m["rebuy_lo"], hi=m["rebuy_hi"], level=m["rebuy_hi"],
                              step=m["id"], mid=m, target=t, kind="MID_REBUY"))
    elif ph == "BUY" and state.get("zone"):
        z = state["zone"]
        W.append(dict(cls=f"buy:{z['step']}", side="down", lo=z["lo"], hi=z["hi"], level=z["trigger"], step=z["step"], zone=z,
                      target=state.get("target"), kind="BUY"))
        if z.get("breakout") and state.get("armed"):
            W.append(dict(cls=f"bo:{z['step']}", side="up", lo=z["breakout"], hi=z["breakout"] * 1.05, level=z["breakout"],
                          step=z["step"], zone=z, target=state.get("target"), kind="BO_WATCH", near_only=False))
    return W


def live_check(plan, state, price, sent_cls):
    """Zwraca listę alertów na żywo: (klucz, poziom 'near'/'hit', watch). sent_cls – już wysłane klucze klas."""
    R = plan["signal_rules"]
    near = R.get("live_near_pct", 2.0) / 100
    out = []
    pre = state.get("start_from", "")
    for w in live_watches(plan, state):
        base = f"{pre}|{w['cls']}"
        hit_k, near_k = base + ":hit", base + ":near"
        if w["side"] == "up":
            if w["kind"] == "BO_WATCH":
                if price > w["level"] * (1 + R.get("live_breakout_watch_pct", 1.0) / 100) and hit_k not in sent_cls:
                    out.append((hit_k, "hit", w))
                continue
            if price >= w["level"]:
                if hit_k not in sent_cls:
                    out.append((hit_k, "hit", w))
            elif price >= w["level"] * (1 - near) and near_k not in sent_cls and hit_k not in sent_cls:
                out.append((near_k, "near", w))
        else:
            if price <= w["level"]:
                if hit_k not in sent_cls:
                    out.append((hit_k, "hit", w))
            elif price <= w["level"] * (1 + near) and near_k not in sent_cls and hit_k not in sent_cls:
                out.append((near_k, "near", w))
    return out


EVENT_CLASS = {"PREP_SELL": ("sell", "near"), "SELL": ("sell", "hit"), "PREP_BUY": ("buy", "near"), "BUY": ("buy", "hit"),
               "PREP_MID": ("mid", "near"), "MID_SELL": ("mid", "hit"), "MID_REBUY": ("midbuy", "hit")}


def event_class_key(plan, e):
    """Klucz klasy dla zdarzenia z zamkniętej świecy (do łączenia z alertami na żywo)."""
    c = EVENT_CLASS.get(e["kind"])
    if not c:
        return None, None
    return f"{plan['start']['from']}|{c[0]}:{e.get('step')}", c[1]


# ----------------------------------------------------------------- prognoza kapitału (wariant ostrożny)
def capital_path(plan, use_mid):
    """Kolejne transakcje wg limitów wariantu ostrożnego do szczytu etapu 2.
    Każdy wiersz: S_in (Sonic przed sprzedażą), sell (limit), usd (gotówka po sprzedaży) z zakresem usd_lo/usd_hi (sprzedaż na dolnej/górnej
    granicy widełek), rebuy (limit), S (Sonic po odkupie) z zakresem S_lo/S_hi (odkup na górnej/dolnej granicy)."""
    C = plan.get("capital") or {}
    S, fee = float(C.get("start_S", 100000)), float(C.get("fee_pct", 0.2)) / 100
    rows = []
    for s in plan["steps"]:
        if s["id"] == "start" or s.get("checkpoint"):
            continue
        if s.get("intermediate") and not use_mid:
            continue
        lo, hi = s["sell_range"]
        sp = s.get("sell_limit") or sell_limit(lo, s["sell"])
        row = dict(step=s["id"], label=s["label"], mid=bool(s.get("intermediate")), S_in=S, sell=sp, sell_lo=lo, sell_hi=hi,
                   usd=S * sp * (1 - fee), usd_lo=S * lo * (1 - fee), usd_hi=S * hi * (1 - fee), rebuy=None, S=None)
        if s["id"] == "2d" or s.get("final") or not s.get("rebuy_range"):
            rows.append(row)
            break
        rlo, rhi = s["rebuy_range"]
        row.update(rebuy=rhi, rebuy_lo=rlo, rebuy_hi=rhi, S=row["usd"] / rhi * (1 - fee), S_hi=row["usd"] / rlo * (1 - fee))
        S = row["S"]
        rows.append(row)
    return rows


def amounts_for(plan, step_id, mid=False):
    """Kwoty wg planu dla kroku. Kroki główne liczone na ścieżce bez pośrednich (pośrednie są opcjonalne);
    krok pośredni – od stanu Soniców przed najbliższym krokiem głównym."""
    base = capital_path(plan, False)
    if not mid:
        return next((r for r in base if r["step"] == step_id), None)
    s = step_by_id(plan, step_id)
    nxt = None
    seen = False
    for x in plan["steps"]:
        if x["id"] == step_id:
            seen = True
            continue
        if seen and not x.get("intermediate") and not x.get("checkpoint"):
            nxt = x["id"]
            break
    S = next((r["S_in"] for r in base if r["step"] == nxt), float((plan.get("capital") or {}).get("start_S", 100000)))
    fee = float((plan.get("capital") or {}).get("fee_pct", 0.2)) / 100
    lo, hi = s["sell_range"]
    sp = sell_limit(lo, s["sell"])
    usd = S * sp * (1 - fee)
    rlo, rhi = s["rebuy_range"]
    return dict(step=step_id, S_in=S, sell=sp, sell_lo=lo, sell_hi=hi, usd=usd, usd_lo=S * lo * (1 - fee), usd_hi=S * hi * (1 - fee),
                rebuy=rhi, rebuy_lo=rlo, rebuy_hi=rhi, S=usd / rhi * (1 - fee), S_hi=usd / rlo * (1 - fee))


def start_amounts(plan):
    C = plan.get("capital") or {}
    st = step_by_id(plan, "start")
    S0, fee = float(C.get("start_S", 100000)), float(C.get("fee_pct", 0.2)) / 100
    return dict(S=S0, limit=st["buy"], usd=S0 * st["buy"] * (1 + fee), usd_lo=S0 * st["buy_range"][0], usd_hi=S0 * st["buy_range"][1])
