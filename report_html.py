# -*- coding: utf-8 -*-
"""Generowanie pliku index.html (raport dzienny) – czysty HTML + CSS + wykresy SVG, bez zewnętrznych skryptów."""
import html, math, re
from datetime import datetime, timezone

E = html.escape


# ----------------------------------------------------------------- formatowanie liczb (styl polski)
def fnum(x, dec=2):
    if x is None:
        return "brak"
    s = f"{x:,.{dec}f}"
    return s.replace(",", " ").replace(".", ",")


def fprice(x):
    if x is None:
        return "brak"
    if x >= 1000:
        return fnum(x, 0)
    if x >= 1:
        return fnum(x, 3)
    dec = max(4, -int(math.floor(math.log10(abs(x)))) + 3)
    return fnum(x, dec)


def fpct(x, dec=1, sign=True):
    if x is None:
        return "brak"
    s = f"{x:+.{dec}f}" if sign else f"{x:.{dec}f}"
    return s.replace(".", ",") + "%"


def fusd_big(x):
    if x is None:
        return "brak"
    for div, suf in ((1e12, "bln"), (1e9, "mld"), (1e6, "mln"), (1e3, "tys.")):
        if abs(x) >= div:
            return f"{fnum(x / div, 2)} {suf} USD"
    return f"{fnum(x, 0)} USD"


def fdate(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return d or "brak"


def md_lite(text):
    """Bezpieczna zamiana prostego markdownu (akapity, listy '- ', **pogrubienia**, linki) na HTML."""
    if not text:
        return ""
    out, ul = [], False
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        if all(re.match(r"^\s*[-•*]\s+", l) for l in lines):
            items = []
            for l in lines:
                items.append(f"<li>{_inline(re.sub(r'^\s*[-•*]\s+', '', l))}</li>")
            out.append("<ul>" + "".join(items) + "</ul>")
        else:
            buf, items = [], []
            for l in lines:
                if re.match(r"^\s*[-•*]\s+", l):
                    items.append(f"<li>{_inline(re.sub(r'^\s*[-•*]\s+', '', l))}</li>")
                else:
                    if items:
                        buf.append("<ul>" + "".join(items) + "</ul>"); items = []
                    buf.append(f"<p>{_inline(l)}</p>")
            if items:
                buf.append("<ul>" + "".join(items) + "</ul>")
            out.append("".join(buf))
    return "".join(out)


def _inline(s):
    s = E(s.strip())
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(https?://[^\s<)\]]+)", lambda m: f'<a href="{m.group(1)}" rel="noopener" target="_blank">{_short(m.group(1))}</a>', s)
    return s


def _short(url):
    u = re.sub(r"^https?://(www\.)?", "", url)
    return u if len(u) <= 48 else u[:45] + "…"


# ----------------------------------------------------------------- wykresy SVG
def _xticks(ts_list, x_of, every=1):
    ticks, last_m = [], None
    for t in ts_list:
        d = datetime.fromtimestamp(t, timezone.utc)
        key = (d.year, d.month)
        if key != last_m and d.day <= 3 and d.month % every == 0:
            ticks.append((x_of(t), d.strftime("%m.%y")))
        last_m = key
    return ticks


def sonic_chart_svg(s, mobile=False):
    data = s["chart"][-365:]
    if len(data) < 10:
        return ""
    W, Hh, L, R, Tp, B = (600, 420, 6, 112, 20, 34) if mobile else (1000, 380, 10, 92, 14, 26)
    gap = 26 if mobile else 15
    t0, t1 = data[0]["t"], data[-1]["t"]
    lv = s["levels"]
    show_levels = (lv["supports"][:3] + lv["resistances"][:3])
    vals = [d["l"] for d in data] + [d["h"] for d in data] + [l["price"] for l in show_levels]
    lo, hi = min(vals) * 0.92, max(vals) * 1.06
    ly = lambda v: Tp + (math.log(hi) - math.log(v)) / (math.log(hi) - math.log(lo)) * (Hh - Tp - B)
    lx = lambda t: L + (t - t0) / max(1, (t1 - t0)) * (W - L - R)
    parts = [f'<svg viewBox="0 0 {W} {Hh}" role="img" aria-label="Wykres ceny Sonic z ostatnich 12 miesięcy w skali logarytmicznej" class="chart{' m' if mobile else ''}">']
    # strefy setupów
    for side, cls in (("LONG", "zone-long"), ("SHORT", "zone-short")):
        st = s["setups"].get(side) or {}
        z = st.get("levels") or {}
        not_broken = any("unieważnienia" in k and v for k, v in (st.get("conditions") or {}).items())
        if z.get("zone_low") and not_broken and lo < z["zone_high"] and z["zone_low"] < hi:
            y1, y2 = ly(min(hi, z["zone_high"])), ly(max(lo, z["zone_low"]))
            parts.append(f'<rect x="{L}" y="{y1:.1f}" width="{W - L - R}" height="{max(2, y2 - y1):.1f}" class="{cls}"/>')
            parts.append(f'<text x="{L + 6}" y="{y1 + 13:.1f}" class="zone-label {cls}-t">strefa obserwacji {side}</text>')
    # poziomy (etykiety rozsunięte, żeby się nie nakładały)
    labels = []
    for l in show_levels:
        y = ly(l["price"]); cls = "lvl-s" if l["price"] < s["price"] else "lvl-r"
        parts.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="{cls}"/>')
        labels.append([y, fprice(l["price"]), f"axis {cls}-t"])
    labels.append([ly(s["price"]), fprice(s["price"]), "axis now-t"])
    labels.sort(key=lambda x: x[0])
    for k in range(1, len(labels)):
        if labels[k][0] - labels[k - 1][0] < gap:
            labels[k][0] = labels[k - 1][0] + gap
    for y, txt, cls in labels:
        parts.append(f'<text x="{W - R + 6}" y="{y + 4:.1f}" class="{cls}">{txt}</text>')
    # linia ceny
    pts = " ".join(f"{lx(d['t']):.1f},{ly(d['c']):.1f}" for d in data)
    parts.append(f'<polyline points="{pts}" class="price-line"/>')
    # swingi
    for sw in (s.get("swings_chart", []) if not mobile else []):
        if sw["t"] >= t0:
            up = sw["label"] in ("HH", "LH", "H")
            y = ly(sw["price"]) + (-8 if up else 16)
            parts.append(f'<text x="{lx(sw["t"]):.1f}" y="{y:.1f}" class="swing-t" text-anchor="middle">{E(sw["label"])}</text>')
    # bieżąca cena
    cy = ly(s["price"])
    parts.append(f'<circle cx="{lx(t1):.1f}" cy="{cy:.1f}" r="4.5" class="now-dot"/>')
    for x, lab in _xticks([d["t"] for d in data], lx, 3 if mobile else 1):
        parts.append(f'<text x="{x:.1f}" y="{Hh - 8}" class="axis" text-anchor="middle">{lab}</text>')
    parts.append("</svg>")
    return "".join(parts)


def btc_chart_svg(b, mobile=False):
    data = b["chart"]
    if len(data) < 10:
        return ""
    W, Hh, L, R, Tp, B = (600, 380, 6, 118, 20, 34) if mobile else (1000, 300, 10, 92, 14, 26)
    sma = b.get("sma200_series") or []
    rp = (b.get("onchain") or {}).get("realized_price")
    vals = [d["c"] for d in data] + [v for v in sma if v] + ([rp] if rp else [])
    lo, hi = min(vals) * 0.95, max(vals) * 1.04
    t0, t1 = data[0]["t"], data[-1]["t"]
    ly = lambda v: Tp + (hi - v) / (hi - lo) * (Hh - Tp - B)
    lx = lambda t: L + (t - t0) / max(1, (t1 - t0)) * (W - L - R)
    parts = [f'<svg viewBox="0 0 {W} {Hh}" role="img" aria-label="Wykres ceny Bitcoina z ostatnich 12 miesięcy ze średnią 200-dniową" class="chart{' m' if mobile else ''}">']
    if rp and lo < rp < hi:
        y = ly(rp)
        parts.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="lvl-rp"/>')
        parts.append(f'<text x="{W - R + 6}" y="{y + 4:.1f}" class="axis rp-t">realized</text>')
    if sma:
        pts = " ".join(f"{lx(d['t']):.1f},{ly(v):.1f}" for d, v in zip(data, sma[-len(data):]) if v)
        parts.append(f'<polyline points="{pts}" class="sma-line"/>')
        last = next((v for v in reversed(sma) if v), None)
        if last:
            parts.append(f'<text x="{W - R + 6}" y="{ly(last) + 4:.1f}" class="axis sma-t">SMA200</text>')
    pts = " ".join(f"{lx(d['t']):.1f},{ly(d['c']):.1f}" for d in data)
    parts.append(f'<polyline points="{pts}" class="btc-line"/>')
    cy = ly(b["price"])
    parts.append(f'<circle cx="{lx(t1):.1f}" cy="{cy:.1f}" r="4.5" class="btc-dot"/>')
    parts.append(f'<text x="{W - R + 6}" y="{cy + 4:.1f}" class="axis btc-t">{fnum(b["price"], 0)}</text>')
    for x, lab in _xticks([d["t"] for d in data], lx, 3 if mobile else 1):
        parts.append(f'<text x="{x:.1f}" y="{Hh - 8}" class="axis" text-anchor="middle">{lab}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ----------------------------------------------------------------- bloki
STATUS_WORD = {"pozytywny": "sprzyja", "negatywny": "nie sprzyja", "neutralny": "neutralnie", "ostrzeżenie": "uwaga", "brak": "brak danych"}


def metric_value(m):
    v, f = m["value"], m["fmt"]
    if v is None:
        return "brak"
    return {"pct": fpct(v), "pct3": fpct(v, 4), "pct_plain": fpct(v, 1, sign=False), "x": fnum(v, 2) + "×",
            "num": fnum(v, 1) if abs(v) >= 1 else fnum(v, 4), "int": str(int(v)),
            "usd": fnum(v, 0) + " USD", "usd_big": fusd_big(v)}.get(f, str(v))


def ladder_html(s):
    lv = s["levels"]
    rows = []
    for l in reversed(lv["resistances"][:5]):
        rows.append(("r", l))
    rows.append(("now", None))
    for l in lv["supports"][:5]:
        rows.append(("s", l))
    out = ['<ol class="ladder" aria-label="Poziomy oporu nad ceną i wsparcia pod ceną">']
    for kind, l in rows:
        if kind == "now":
            out.append(f'<li class="rung now"><span class="rp">{fprice(s["price"])}</span>'
                       f'<span class="rd">cena teraz</span><span class="rs"></span>'
                       f'<span class="rsrc">ostatnie zamknięcie dnia {fdate(s["candle_date"])}; kierunek krótkoterminowy: {E(s["direction"]["short_term"] or "brak")}</span></li>')
            continue
        strength = min(5, max(1, round(l["strength"] / 3)))
        dots = "".join('<i class="on"></i>' if k < strength else "<i></i>" for k in range(5))
        key = (l is lv.get("key_support")) or (l is lv.get("key_resistance"))
        label = ("opór" if kind == "r" else "wsparcie") + (" kluczowy" if key and kind == "r" else " kluczowe" if key else "")
        out.append(f'<li class="rung {kind}{" key" if key else ""}"><span class="rp">{fprice(l["price"])}</span>'
                   f'<span class="rd"><span class="rk">{label}</span> {fpct(l["dist_pct"])} od ceny</span>'
                   f'<span class="rs" title="siła poziomu {strength}/5">{dots}</span>'
                   f'<span class="rsrc">{E("; ".join(l["sources"][:4]))}</span></li>')
    out.append("</ol>")
    return "".join(out)


def checklist(conds):
    return "<ul class='check'>" + "".join(
        f"<li class='{'ok' if v else 'no'}'><span class='mk' aria-hidden='true'>{'✓' if v else '✗'}</span>"
        f"<span>{E(k)}</span><span class='sr'>{'spełniony' if v else 'niespełniony'}</span></li>" for k, v in conds.items()) + "</ul>"


def setup_block(side, st, price):
    lv = st.get("levels") or {}
    bt = st.get("backtest") or {}
    name = "LONG – odbicie od strefy Fibonacciego" if side == "LONG" else "SHORT – odrzucenie strefy Fibonacciego"
    if st.get("triggered"):
        state = f'<p class="state on-{side.lower()}">Sygnał aktywny na świecy {fdate(st.get("date") or "")}</p>'
    elif lv.get("zone_low"):
        d = st.get("dist_to_zone_pct")
        broken = any("unieważnienia" in k and not v for k, v in (st.get("conditions") or {}).items())
        if broken:
            where = "setup unieważniony – cena przekroczyła poziom Fibo 0,886 tego ruchu"
        elif d == 0:
            where = "cena jest w strefie, potrzebna świeca potwierdzająca"
        else:
            where = f"strefa {fpct(abs(d), sign=False)} {'poniżej' if d < 0 else 'powyżej'} ceny"
        state = f'<p class="state">Brak sygnału: {where}</p>'
    else:
        state = '<p class="state">Brak sygnału: układ swingów nie tworzy dziś tego setupu</p>'
    leg = st.get("leg")
    legtxt = (f"Ruch odniesienia: {fprice(leg['start'])} ({fdate(leg['start_date'])}) → {fprice(leg['end'])} ({fdate(leg['end_date'])})." if leg else "")
    lvtxt = ""
    if lv.get("zone_low"):
        lvtxt = (f"<dl class='kv'><dt>Strefa</dt><dd>{fprice(lv['zone_low'])} – {fprice(lv['zone_high'])}</dd>"
                 f"<dt>Fibo 0,618 / 0,786</dt><dd>{fprice(lv['fib_0618'])} / {fprice(lv['fib_0786'])}</dd>"
                 f"<dt>Unieważnienie</dt><dd>{fprice(lv['invalidation'])}</dd>"
                 f"<dt>Cel (koniec ruchu)</dt><dd>{fprice(lv['target'])}</dd>"
                 + (f"<dt>Poziomy historyczne w strefie</dt><dd>{', '.join(fprice(p) for p in lv['hist_levels_in_zone'][:4])}</dd>" if lv.get("hist_levels_in_zone") else "")
                 + "</dl>")
    btxt = ""
    if bt.get("n"):
        btxt = (f"<p class='bt'>Historycznie (FTM/S 2019–dziś): <strong>{bt['n']}</strong> sygnałów, trafność "
                f"<strong>{fnum(bt['win_rate'], 0)}%</strong>, średni wynik <strong>{fnum(bt['avg_R'], 2)}R</strong>, "
                f"mediana ryzyka {fnum(bt['median_risk_pct'], 0)}% ceny, mediana czasu {fnum(bt['median_days'], 0)} dni."
                + (" Setup ma słabszą, niestabilną przewagę – traktuj jako sygnał niższej pewności." if side == "SHORT" else "")
                + "</p>")
    return (f"<article class='setup {side.lower()}'><h4>{name}</h4>{state}<p class='muted'>{E(legtxt)}</p>"
            f"{checklist(st.get('conditions') or {})}{lvtxt}{btxt}</article>")


def turning_block(s):
    tp = s["turning"]
    def bar(score, total, prec, which):
        w = score / total * 100
        ptxt = (f"Historycznie przy ≥{prec['min_warunkow']} warunkach {fnum(prec['precyzja_pct'], 0)}% dni leżało do 5 dni od {which}"
                if prec else f"Za mało warunków, by mówić o podobieństwie do {which}")
        return (f"<div class='meter'><div class='mtrack'><div class='mfill' style='width:{w:.0f}%'></div></div>"
                f"<p><strong>{score} z {total}</strong> warunków. {E(ptxt)}.</p></div>")
    bl = "".join(f"<li class='{'ok' if v else 'no'}'>{E(tp['bottom_labels'][k])}</li>" for k, v in tp["bottom_flags"].items())
    tl = "".join(f"<li class='{'ok' if v else 'no'}'>{E(tp['top_labels'][k])}</li>" for k, v in tp["top_flags"].items())
    return (f"<div class='two'><div><h4>Podobieństwo do historycznych dołków</h4>{bar(tp['bottom_score'], 7, tp['bottom_precision'], 'dołka')}"
            f"<ul class='flags'>{bl}</ul></div><div><h4>Podobieństwo do historycznych szczytów</h4>"
            f"{bar(tp['top_score'], 7, tp['top_precision'], 'szczytu')}<ul class='flags'>{tl}</ul></div></div>")


CSS = """
:root{--ink:#0E1626;--panel:#152035;--panel2:#1B2942;--line:#26365A;--text:#E6E9EF;--muted:#93A0B8;
--btc:#E8A93B;--sonic:#5CC8E8;--pos:#43C59E;--neg:#E5635B;--warn:#E8C25C}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--ink);color:var(--text);font:16px/1.6 Manrope,system-ui,sans-serif;font-variant-numeric:tabular-nums}
a{color:var(--sonic)}a:focus-visible,summary:focus-visible{outline:2px solid var(--sonic);outline-offset:2px}
.wrap{max-width:1100px;margin:0 auto;padding:40px 16px 64px}
header.top{display:grid;grid-template-columns:1fr auto;gap:16px 32px;align-items:end;padding-bottom:28px;border-bottom:1px solid var(--line)}
header.top h1{font-size:clamp(1.7rem,3.4vw,2.5rem);line-height:1.15;margin:0;font-weight:700;letter-spacing:-.01em}
header.top .date{color:var(--muted);margin:6px 0 0}
.tickers{display:flex;gap:28px}.tk{text-align:right}.tk b{display:block;font-size:1.35rem;font-weight:700}
.tk span{color:var(--muted);font-size:.85rem}.tk.btc b{color:var(--btc)}.tk.sonic b{color:var(--sonic)}
.verdict{margin:28px 0 8px;font-size:1.15rem;max-width:72ch}
.verdict strong{font-weight:700}
section{margin-top:56px}
section>h2{font-size:1.6rem;margin:0 0 6px;font-weight:700;padding-left:14px;border-left:4px solid var(--btc)}
section.sonic>h2{border-left-color:var(--sonic)}
.lede{color:var(--muted);margin:0 0 24px;max-width:75ch}
h3{font-size:1.15rem;margin:36px 0 12px}h4{font-size:1rem;margin:0 0 8px}
.hero{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 24px;margin:18px 0 8px}
.hero .big{font-size:clamp(2.2rem,6vw,3.4rem);font-weight:800;letter-spacing:-.02em;line-height:1}
section.btc .hero .big{color:var(--btc)}section.sonic .hero .big{color:var(--sonic)}
.chg{color:var(--muted)}.chg b{font-weight:700;color:var(--text)}
.metrics{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.metric{padding:16px 18px;box-shadow:1px 0 0 var(--line),0 1px 0 var(--line)}
.metric .mn{color:var(--muted);font-size:.88rem;margin:0}
.metric .mv{font-size:1.4rem;font-weight:700;margin:2px 0 4px}
.metric .ms{font-size:.85rem;font-weight:700}
.metric .me{font-size:.86rem;color:var(--muted);margin:6px 0 0;line-height:1.5}
.st-pozytywny{color:var(--pos)}.st-negatywny{color:var(--neg)}.st-ostrzeżenie{color:var(--warn)}.st-neutralny,.st-brak{color:var(--muted)}
.chart{width:100%;height:auto;display:block;background:var(--panel);border-radius:10px;margin:8px 0}
.axis{fill:var(--muted);font-size:13px}.chart.m .axis{font-size:21px}.chart.m .zone-label{font-size:19px}.ch-m{display:none}.price-line{fill:none;stroke:var(--sonic);stroke-width:2}
.btc-line{fill:none;stroke:var(--btc);stroke-width:2}.sma-line{fill:none;stroke:var(--muted);stroke-width:1.5;stroke-dasharray:5 4}
.lvl-s{stroke:var(--pos);stroke-width:1;stroke-dasharray:3 4;opacity:.8}.lvl-r{stroke:var(--neg);stroke-width:1;stroke-dasharray:3 4;opacity:.8}
.lvl-s-t{fill:var(--pos)}.lvl-r-t{fill:var(--neg)}.lvl-rp{stroke:var(--pos);stroke-width:1.5;stroke-dasharray:2 3}.rp-t{fill:var(--pos)}
.zone-long{fill:var(--pos);opacity:.12}.zone-short{fill:var(--neg);opacity:.12}.zone-label{font-size:12px}.zone-long-t{fill:var(--pos)}.zone-short-t{fill:var(--neg)}
.swing-t{fill:var(--muted);font-size:11px}.now-dot{fill:var(--sonic)}.now-t{fill:var(--sonic);font-weight:700}
.btc-dot{fill:var(--btc)}.btc-t{fill:var(--btc);font-weight:700}.sma-t{fill:var(--muted)}
.legend{color:var(--muted);font-size:.85rem;margin:0}
.ai{background:var(--panel);border-radius:10px;padding:22px 26px;margin-top:20px;font-family:Newsreader,Georgia,serif;font-size:1.08rem;line-height:1.7}
.ai p{margin:0 0 .9em;max-width:72ch}.ai ul{margin:0 0 .9em;padding-left:1.2em;max-width:72ch}.ai li{margin:.2em 0}
.ai .who{font-family:Manrope,system-ui,sans-serif;font-size:.85rem;color:var(--muted);margin:0 0 12px}
.ai.research{background:var(--panel2);font-size:1rem}
.ladder{list-style:none;margin:8px 0 0;padding:0;position:relative}
.ladder:before{content:"";position:absolute;left:118px;top:6px;bottom:6px;width:2px;background:var(--line)}
.rung{display:grid;grid-template-columns:104px 1fr auto;gap:2px 30px;padding:10px 0 10px 0;position:relative;border-bottom:1px solid rgba(38,54,90,.5)}
.rung:before{content:"";position:absolute;left:114px;top:17px;width:10px;height:10px;border-radius:50%;background:var(--ink);border:2px solid var(--muted)}
.rung.r:before{border-color:var(--neg)}.rung.s:before{border-color:var(--pos)}.rung.key:before{background:currentColor}
.rung.r.key{color:var(--neg)}.rung.s.key{color:var(--pos)}
.rung .rp{font-weight:700;color:var(--text);text-align:right}.rung .rd{color:var(--muted)}.rung .rk{color:var(--text);font-weight:600;margin-right:6px}.rung.r.key .rk{color:var(--neg)}.rung.s.key .rk{color:var(--pos)}.rung .rs{white-space:nowrap}
.rung .rsrc{grid-column:2/4;color:var(--muted);font-size:.84rem}
.rung.now{background:var(--panel2);border-radius:8px;padding:12px 0}.rung.now:before{background:var(--sonic);border-color:var(--sonic);width:14px;height:14px;left:112px;top:16px}
.rung.now .rp{color:var(--sonic);font-size:1.15rem}
.rs i{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--line);margin-left:3px}.rs i.on{background:var(--muted)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:28px}
.panel{background:var(--panel);border-radius:10px;padding:20px 22px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 18px;margin:10px 0}.kv.inline{max-width:560px}.kv dt{color:var(--muted)}.kv dd{margin:0;font-weight:600}
.check,.flags{list-style:none;padding:0;margin:10px 0}.check li{display:flex;gap:10px;padding:3px 0}
.check .mk{width:1.2em;font-weight:800}.check li.ok .mk{color:var(--pos)}.check li.no .mk{color:var(--neg)}.check li.no{color:var(--muted)}
.flags li{padding:2px 0 2px 18px;position:relative;font-size:.92rem}.flags li:before{content:"";position:absolute;left:0;top:.6em;width:8px;height:8px;border-radius:2px;background:var(--line)}
.flags li.ok:before{background:var(--sonic)}.flags li.no{color:var(--muted)}
.sr{position:absolute;left:-9999px}
.meter .mtrack{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin:6px 0}.meter .mfill{height:100%;background:var(--sonic)}
.meter p{margin:0;font-size:.92rem;color:var(--muted)}.meter strong{color:var(--text)}
.setups{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.setup{background:var(--panel);border-radius:10px;padding:20px 22px;border-top:3px solid var(--line)}
.setup.long{border-top-color:var(--pos)}.setup.short{border-top-color:var(--neg)}
.state{font-weight:700;margin:4px 0 6px}.state.on-long{color:var(--pos)}.state.on-short{color:var(--neg)}
.muted{color:var(--muted);font-size:.92rem}.bt{font-size:.9rem;color:var(--muted)}.bt strong{color:var(--text)}
table{width:100%;border-collapse:collapse;font-size:.92rem}th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600}td.n{text-align:right}
.tablewrap{overflow-x:auto}
details{margin-top:14px}summary{cursor:pointer;color:var(--sonic);font-weight:600}
footer{margin-top:64px;padding-top:22px;border-top:1px solid var(--line);color:var(--muted);font-size:.88rem}
footer .sig{color:var(--text);font-size:1rem;margin-bottom:10px}
@media (max-width:760px){.ch-d{display:none}.ch-m{display:block}header.top{grid-template-columns:1fr}.tickers{justify-content:flex-start}.tk{text-align:left}
.two,.setups{grid-template-columns:1fr}.ladder:before{left:86px}.rung{grid-template-columns:76px 1fr;gap:2px 26px}.rung:before{left:82px}
.rung.now:before{left:80px}.rung .rs{grid-column:2}.rung .rsrc{grid-column:2}.ai{padding:18px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def render(ctx):
    s, b = ctx["sonic"], ctx["btc"]
    ai = ctx.get("ai") or {}
    date_txt = ctx["generated_at"]
    sig_l, sig_s = s["setups"]["LONG"], s["setups"]["SHORT"]
    if sig_l.get("triggered") or sig_s.get("triggered"):
        side = "LONG" if sig_l.get("triggered") else "SHORT"
        verdict = f"Sonic: <strong>aktywny sygnał {side}</strong> na zamknięciu dnia {fdate(s['candle_date'])}. Tło BTC dla altcoinów: <strong>{E(b['context_label'])}</strong>."
    else:
        lz = (sig_l.get("levels") or {})
        dz = sig_l.get("dist_to_zone_pct")
        broken_l = any("unieważnienia" in k and not v for k, v in (sig_l.get("conditions") or {}).items())
        watch = ""
        if lz.get("zone_low") and dz is not None and not broken_l:
            where = "cena jest w strefie" if dz == 0 else f"{fpct(abs(dz), sign=False)} {'poniżej' if dz < 0 else 'powyżej'} ceny"
            watch = f" Strefa obserwacji LONG: {fprice(lz['zone_low'])}–{fprice(lz['zone_high'])} ({where})."
        verdict = f"Sonic: <strong>brak sygnału</strong> na zamknięciu dnia {fdate(s['candle_date'])}.{watch} Tło BTC dla altcoinów: <strong>{E(b['context_label'])}</strong>."

    # BTC metryki
    mt = "".join(f"<div class='metric'><p class='mn'>{E(m['name'])}</p><p class='mv'>{metric_value(m)}</p>"
                 f"<p class='ms st-{m['status']}'>{STATUS_WORD.get(m['status'], m['status'])}</p><p class='me'>{E(m['explain'])}</p></div>"
                 for m in b["metrics"])
    derivs = ", ".join(f"{d['source']}: OI {fusd_big(d.get('oi_usd'))}, funding {fpct(d.get('funding_8h_pct'), 4)}" for d in b["derivatives"])

    # Sonic
    ind = s["indicators"]; hist = s["history"]; d = s["direction"]
    struct_rows = "".join(
        f"<tr><td>{E(name)}</td><td>{E(st['trend'])}</td><td>"
        + " → ".join(f"{E(x['label'])} {fprice(x['price'])} <span class='muted'>({fdate(x['date'])}{'' if x['confirmed'] else ', niepotw.'})</span>" for x in st["swings"][-4:])
        + "</td></tr>" for name, st in (("Cykl (≥80%)", s["structure"]["cykl"]), ("Główny (≥60%)", s["structure"]["glowny"]),
                                         ("Pośredni (≥40%)", s["structure"]["posredni"]), ("Drobny (≥25%)", s["structure"]["drobny"])))
    fib_rows = ""
    for f in s["fibs"][:2]:
        cells = "".join(f"<td class='n'>{fprice(f['levels'][k])}</td>" if k in f["levels"] else "<td class='n'>–</td>" for k in (0.382, 0.5, 0.618, 0.786, 0.886))
        fib_rows += (f"<tr><td>{fprice(f['start'])} → {fprice(f['end'])}<br><span class='muted'>{fdate(f['start_date'])} – {fdate(f['end_date'])}, "
                     f"{E(f['direction'])}</span></td>{cells}<td class='n'>{fnum(f['current_retrace_pct'], 0)}%</td></tr>")
    ob = s.get("orderbook")
    ob_html = "<p class='muted'>Księga zleceń Coinbase niedostępna w tym cyklu.</p>"
    if ob:
        walls = lambda ws: ", ".join(f"{fprice(w['price'])} ({fpct(w['dist_pct'])}, ~{fusd_big(w['usd'])})" for w in ws) or "brak wyraźnych"
        ob_html = (f"<dl class='kv'><dt>Popyt do −2% / −5% / −10%</dt><dd>{fusd_big(ob['bid_depth_2pct_usd'])} / {fusd_big(ob['bid_depth_5pct_usd'])} / {fusd_big(ob['bid_depth_10pct_usd'])}</dd>"
                   f"<dt>Podaż do +2% / +5% / +10%</dt><dd>{fusd_big(ob['ask_depth_2pct_usd'])} / {fusd_big(ob['ask_depth_5pct_usd'])} / {fusd_big(ob['ask_depth_10pct_usd'])}</dd>"
                   f"<dt>Przewaga kupujących (±5%)</dt><dd>{fnum(ob['imbalance_5pct'], 0)}% zleceń to kupno</dd>"
                   f"<dt>Ściany kupna</dt><dd>{walls(ob['bid_walls'])}</dd><dt>Ściany sprzedaży</dt><dd>{walls(ob['ask_walls'])}</dd></dl>"
                   "<p class='muted'>Ściana = przedział 1% ceny z co najmniej 3× większą wartością zleceń niż mediana. Zlecenia mogą zniknąć w każdej chwili.</p>")
    cg = s.get("coingecko") or {}
    exch = ""
    if cg.get("exchanges_volume"):
        exch = "<table><thead><tr><th>Giełda</th><th class='n'>Wolumen 24h</th><th class='n'>Udział</th></tr></thead><tbody>" + "".join(
            f"<tr><td>{E(e['exchange'])}</td><td class='n'>{fusd_big(e['volume_usd'])}</td><td class='n'>{fnum(e['share_pct'], 1)}%</td></tr>" for e in cg["exchanges_volume"][:6]) + "</tbody></table>"
    rng = hist.get("current_range") or hist.get("last_range")
    rng_txt = ""
    if rng:
        rng_txt = (f"{'Trwająca' if rng.get('ongoing') else 'Ostatnia'} konsolidacja: {fdate(rng['start'])} – {fdate(rng['end'])} ({rng['days']} dni), "
                   f"wsparcie {fprice(rng['support'])}, opór {fprice(rng['resistance'])} (szerokość {fnum(rng['width_pct'], 0)}%)"
                   + (f", wybicie {E(rng['breakout'])}" if rng.get("breakout") else "") + ". "
                   f"Typowa konsolidacja Sonica/FTM trwała {fnum(hist['typical_range_days']['median'], 0)} dni (co czwarta ponad {fnum(hist['typical_range_days']['p75'], 0)}).")
    past = "".join(f"<li>{fdate(r['start'])} – {fdate(r['end'])}: {fprice(r['support'])}–{fprice(r['resistance'])}, {r['days']} dni, wybicie {E(r['breakout'] or 'brak')}</li>"
                   for r in hist["ranges_at_this_price"])
    research = ""
    if ctx.get("research_sonic"):
        research = f"<div class='ai research'><p class='who'>Nastroje i wiadomości o Sonic – wyszukiwanie w sieci przez model {E(ctx['model'])}</p>{md_lite(ctx['research_sonic'])}</div>"
    research_macro = ""
    if ctx.get("research_macro"):
        research_macro = f"<details><summary>Notatki makro z wyszukiwania w sieci</summary><div class='ai research'>{md_lite(ctx['research_macro'])}</div></details>"

    ai_btc = f"<div class='ai'><p class='who'>Komentarz modelu {E(ctx['model'])}</p>{md_lite(ai.get('BTC'))}</div>" if ai.get("BTC") else "<p class='muted'>Komentarz AI niedostępny w tym cyklu.</p>"
    ai_sonic = f"<div class='ai'><p class='who'>Komentarz modelu {E(ctx['model'])}</p>{md_lite(ai.get('SONIC'))}{md_lite(ai.get('SYGNAL'))}" \
               + (f"<h4>Ryzyka</h4>{md_lite(ai.get('RYZYKA'))}" if ai.get("RYZYKA") else "") + "</div>" if ai.get("SONIC") else "<p class='muted'>Komentarz AI niedostępny w tym cyklu.</p>"

    elliott = "".join(f"<tr><td>{E(e['okres'])}</td><td><strong>{E(e['nazwa'])}</strong><br><span class='muted'>{E(e['opis'])}</span></td></tr>" for e in ctx["elliott"])
    concl = "".join(f"<li>{E(c)}</li>" for c in ctx["conclusions"])

    return f"""<!DOCTYPE html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Raport dzienny: Bitcoin i Sonic – {E(date_txt)}</title>
<meta name="description" content="Dzienny raport rynkowy BTC i Sonic (S): wskaźniki, poziomy wsparcia i oporu, struktura fal, sygnały.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Newsreader:opsz,wght@6..72,400;6..72,600&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="wrap">
<header class="top"><div><h1>Bitcoin i Sonic – raport dzienny</h1><p class="date">{E(date_txt)}</p></div>
<div class="tickers"><div class="tk btc"><b>{fnum(b['price'], 0)} USD</b><span>BTC {fpct(b['change_24h_pct'])} / 24h</span></div>
<div class="tk sonic"><b>{fprice(s['price'])} USD</b><span>S {fpct(s['change_24h_pct'])} / 24h</span></div></div></header>
<p class="verdict">{verdict}</p>

<section class="btc" aria-labelledby="h-btc"><h2 id="h-btc">Bitcoin</h2>
<p class="lede">Lider rynku i tło dla altcoinów. Ceny: {E(', '.join(b['sources']))}.</p>
<div class="hero"><span class="big">{fnum(b['price'], 0)} USD</span>
<span class="chg">7 dni <b>{fpct(b['change_7d_pct'])}</b>, 30 dni <b>{fpct(b['change_30d_pct'])}</b></span></div>
<div class='ch-d'>{btc_chart_svg(b)}</div><div class='ch-m'>{btc_chart_svg(b, True)}</div>
<p class="legend">Linia przerywana: średnia 200 dni. Zielona linia: realized price (średni koszt nabycia BTC w sieci).</p>
<h3>Wskaźniki – co oznaczają dziś</h3><div class="metrics">{mt}</div>
<p class="muted">Rynek kontraktów: {E(derivs) or 'brak danych'}.</p>
<h3>Ocena modelu AI na tle makroekonomii</h3>{ai_btc}{research_macro}
</section>

<section class="sonic" aria-labelledby="h-s"><h2 id="h-s">Sonic (S)</h2>
<p class="lede">Dawniej Fantom (FTM), zamiana 1:1 w styczniu 2025. Historia od 2019 r. z Binance, bieżące dane z Coinbase.</p>
<div class="hero"><span class="big">{fprice(s['price'])} USD</span>
<span class="chg">7 dni <b>{fpct(s['change_7d_pct'])}</b>, 30 dni <b>{fpct(s['change_30d_pct'])}</b>, 90 dni <b>{fpct(s['change_90d_pct'])}</b></span></div>
<p>Kierunek ruchu: krótkoterminowo (świece 6h) <strong>{E(d['short_term'] or 'brak danych')}</strong>, średnioterminowo (dni) <strong>{E(d['medium_term'])}</strong>,
długoterminowo (SMA200) <strong>{E(d['long_term'])}</strong>.</p>
<div class='ch-d'>{sonic_chart_svg(s)}</div><div class='ch-m'>{sonic_chart_svg(s, True)}</div>
<p class="legend">Skala logarytmiczna, 12 miesięcy. Linie zielone: wsparcia, czerwone: opory. Etykiety HH/HL/LH/LL: punkty zwrotne poziomu pośredniego.</p>

<h3>Gdzie jest cena: wsparcia i opory</h3>
<p class="muted">Poziomy łączą swingi z całej historii, granice konsolidacji, linie Fibonacciego bieżących ruchów, poziomy użytkownika i ściany w księdze zleceń Coinbase. Kropki = siła (liczba i waga źródeł).</p>
{ladder_html(s)}

<h3>Kontekst historyczny</h3>
<div class="two"><div class="panel"><dl class="kv">
<dt>Od szczytu wszech czasów</dt><dd>{fpct(hist['from_ath_pct'])} (ATH {fprice(hist['ath'])}, {fdate(hist['ath_date'])})</dd>
<dt>Od dołka wszech czasów</dt><dd>{fnum(hist['from_atl_x'], 1)}× (ATL {fprice(hist['atl'])}, {fdate(hist['atl_date'])})</dd>
<dt>Zakres 12 miesięcy</dt><dd>{fprice(hist['year_low'])} – {fprice(hist['year_high'])} (cena na {fnum(hist['year_position_pct'], 0)}% zakresu)</dd>
<dt>Dni z niższym zamknięciem</dt><dd>{fnum(hist['pct_days_below'], 0)}% całej historii</dd>
<dt>Ostatnio na tym poziomie</dt><dd>{fdate(hist['last_seen_before']) if hist['last_seen_before'] else 'nigdy wcześniej'}</dd></dl>
<p class="muted">{E(rng_txt)}</p></div>
<div class="panel"><h4>Gdy cena była tu wcześniej (konsolidacje)</h4><ul class="flags">{past or '<li>brak konsolidacji na tym poziomie</li>'}</ul></div></div>

<h3>Struktura fal (HH / HL / LH / LL)</h3>
<div class="tablewrap"><table><thead><tr><th>Poziom</th><th>Struktura</th><th>Ostatnie punkty zwrotne</th></tr></thead><tbody>{struct_rows}</tbody></table></div>
<h3>Zniesienia Fibonacciego bieżących ruchów</h3>
<div class="tablewrap"><table><thead><tr><th>Ruch</th><th class="n">38,2%</th><th class="n">50%</th><th class="n">61,8%</th><th class="n">78,6%</th><th class="n">88,6%</th><th class="n">Zniesione</th></tr></thead><tbody>{fib_rows}</tbody></table></div>
<p class="muted">Na tym aktywie korekty kończyły się najczęściej w strefie 61,8–78,6%, a odbicia po spadkach w strefie 50–78,6%.</p>

<h3>Wskaźniki na tle historycznych punktów zwrotnych</h3>
<dl class="kv inline"><dt>RSI dzienny / tygodniowy</dt><dd>{fnum(ind['rsi'], 1)} / {fnum(ind['rsi_w'], 1)}</dd>
<dt>Odległość od SMA50 / SMA200</dt><dd>{fpct(ind['dist_sma50_pct'])} / {fpct(ind['dist_sma200_pct'])}</dd>
<dt>%B Bollingera</dt><dd>{fnum(ind['pctb'], 2)}</dd><dt>MACD (histogram)</dt><dd>{fnum(ind['macd_hist'], 6)}</dd>
<dt>Zmienność dzienna (ATR)</dt><dd>{fnum(ind['atr_pct'], 1)}%</dd></dl>
{turning_block(s)}

<h3>Księga zleceń i wolumen</h3>
<div class="two"><div class="panel"><h4>Coinbase S-USD</h4>{ob_html}</div>
<div class="panel"><h4>Gdzie handluje się Sonikiem (CoinGecko, 24h)</h4>{exch or "<p class='muted'>Brak danych CoinGecko.</p>"}
<p class="muted">Kapitalizacja {fusd_big(cg.get('market_cap_usd'))}, miejsce {cg.get('rank') or 'brak'} w rankingu. Głosy sentymentu na CoinGecko: {fnum(cg.get('sentiment_up_pct'), 0) if cg.get('sentiment_up_pct') is not None else 'brak'}% pozytywnych.</p></div></div>

<h3>Sygnały LONG / SHORT</h3>
<p class="muted">Sygnał pojawia się tylko w wąskiej strefie (±3,5% od linii Fibonacciego 0,618/0,786 lub poziomu historycznego) i dopiero po potwierdzeniu świecą dzienną. Powiadomienie na Telegram: najwyżej raz dziennie.</p>
<div class="setups">{setup_block('LONG', sig_l, s['price'])}{setup_block('SHORT', sig_s, s['price'])}</div>

<h3>Ocena modelu AI</h3>{research}{ai_sonic}

<details><summary>Interpretacja falowa całej historii (Elliott)</summary><div class="tablewrap"><table><tbody>{elliott}</tbody></table></div></details>
<details><summary>Wnioski z analizy historii 2019–2026</summary><ul class="flags">{concl}</ul></details>
</section>

<footer><p class="sig">Raport wygenerowany autonomicznie przez model AI: Groq ({E(ctx['model'])})</p>
<p>Dane: Coinbase, Kraken, Binance (historia), Coin Metrics / BGeometrics, Hyperliquid, OKX, Deribit, alternative.me, CoinGecko. Obliczenia wskaźników, poziomów i sygnałów wykonuje kod bota; model AI pisze komentarz i przeszukuje sieć.</p>
<p>Raport ma charakter informacyjny i nie jest poradą inwestycyjną. Statystyki historyczne nie gwarantują przyszłych wyników.</p>
<p>Wygenerowano: {E(date_txt)}.{(' Niedostępne źródła: ' + E(', '.join(ctx['errors'].keys())) + '.') if ctx.get('errors') else ''}</p></footer>
</div></body></html>"""
