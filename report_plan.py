# -*- coding: utf-8 -*-
"""Sekcje raportu oparte na planie etapowym: mapa prognozy (SVG), tabela kroków, „gdzie jesteśmy”, poziomy BTC."""
import html, math
from datetime import datetime, timezone

from plan import fmt, pct, date_ts, step_by_id, sell_limit, capital_path

E = html.escape
DAY = 86400


def d_short(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m")


def d_full(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m.%Y")


def fpc(x, sign=True):
    if x is None:
        return "brak"
    return (f"{x:+.1f}%" if sign else f"{x:.1f}%").replace(".", ",")


MONTHS = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"]


# ----------------------------------------------------------------- status kroków z odtworzonych zdarzeń
def show_mids(pc):
    return pc["plan"]["signal_rules"].get("intermediate_alerts", True)


def step_status(pc):
    """id kroku -> 'sprzedano' / 'odkupiono' / 'teraz-sprzedaż' / 'teraz-odkup' / 'przyszły'."""
    st = pc["state"]
    sold = {e["step"] for e in pc["events"] if e["kind"] == "SELL"}
    bought = {e["step"] for e in pc["events"] if e["kind"] in ("BUY", "BUY_BREAKOUT")}
    cur_sell = (st.get("target") or {}).get("step") if st["phase"] == "HOLD" else None
    cur_buy = (st.get("zone") or {}).get("step") if st["phase"] == "BUY" else None
    out = {}
    mid_ev = {}
    for e in pc["events"]:
        if e["kind"] in ("MID_SELL", "MID_REBUY", "MID_BREAKOUT"):
            mid_ev[e["step"]] = e["kind"]
    active_mids = {m["id"]: m["status"] for m in (st.get("mids") or [])}
    for s in pc["plan"]["steps"]:
        i = s["id"]
        if s.get("intermediate"):
            if active_mids.get(i) == "pending":
                out[i] = "pośredni-czeka"
            elif active_mids.get(i) == "touched":
                out[i] = "pośredni-dotknięty"
            elif i in mid_ev:
                out[i] = "pośredni-minięty"
            else:
                out[i] = "przyszły"
            continue
        if i == cur_sell:
            out[i] = "teraz-sprzedaż"
        elif i == cur_buy:
            out[i] = "teraz-odkup" if i != "start" else "teraz-zakup"
        elif i in sold and i in bought:
            out[i] = "zrealizowany"
        elif i in sold:
            out[i] = "sprzedano"
        elif i in bought:
            out[i] = "kupiono"
        else:
            out[i] = "przyszły"
    return out


# ----------------------------------------------------------------- mapa prognozy (SVG)
class Labeler:
    """Prosty układacz etykiet: przesuwa etykietę w pionie, dopóki nie przestanie nachodzić na już postawione."""

    def __init__(self, fs, x_min, x_max, y_min, y_max):
        self.boxes, self.fs, self.x_min, self.x_max, self.y_min, self.y_max = [], fs, x_min, x_max, y_min, y_max

    def width(self, txt, fs):
        return len(txt) * fs * 0.56

    def block(self, x1, y1, x2, y2):
        self.boxes.append((x1, y1, x2, y2))

    def place(self, x, y, lines, anchor="middle", direction=-1, fs=None, step=None):
        """lines: [(tekst, klasa, rozmiar)], y = linia bazowa pierwszej linii. direction -1 = szukaj wyżej, +1 = niżej."""
        fs = fs or self.fs
        h = sum(l[2] for l in lines) + 2 * (len(lines) - 1)
        w = max(self.width(t, s) for t, _, s in lines)
        if anchor == "middle":
            x = min(max(x, self.x_min + w / 2), self.x_max - w / 2)
            x1 = x - w / 2
        elif anchor == "end":
            x = max(x, self.x_min + w)
            x1 = x - w
        else:
            x = min(x, self.x_max - w)
            x1 = x
        step = step or (fs * 0.6)
        y0 = y
        for _ in range(40):
            top = y - lines[0][2] * 0.85
            box = (x1 - 2, top - 1, x1 + w + 2, top + h + 1)
            if box[1] >= self.y_min and box[3] <= self.y_max and not any(
                    not (box[2] <= b[0] or box[0] >= b[2] or box[3] <= b[1] or box[1] >= b[3]) for b in self.boxes):
                break
            y += direction * step
        self.boxes.append(box)
        out, yy = [], y
        for t, cls, size in lines:
            out.append(f'<text x="{x:.1f}" y="{yy:.1f}" text-anchor="{anchor}" class="{cls}" style="font-size:{size}px">{E(t)}</text>')
            yy += size + 2
        return "".join(out), abs(y - y0) > 0.5, (x, y)


def projection_chart_svg(pc, sonic, mobile=False):
    plan = pc["plan"]
    meta = plan["meta"]
    price = pc["price"]
    t_start = date_ts(meta["chart_from"])
    hist = [d for d in sonic["chart"] if d["t"] >= t_start]
    if len(hist) < 5:
        return ""
    t_now = hist[-1]["t"] + DAY
    t_mid = date_ts("2027-03-12")
    t_end = date_ts("2027-11-05")
    path = [p for p in pc["path"] if show_mids(pc) or not p.get("mid")]
    status = step_status(pc)

    W, Hh = (640, 900) if mobile else (1040, 660)
    L, R, Tp, B = (8, 64, 70, 60) if mobile else (12, 66, 56, 44)
    fs = 17 if mobile else 12
    sm = fs - 2
    pw = W - L - R
    # oś czasu odcinkami: historia | etapy 1–2 (rozciągnięte) | dalsza przyszłość (zagęszczona)
    segs = [(t_start, t_now, 0.0, 0.22), (t_now, t_mid, 0.22, 0.86), (t_mid, t_end, 0.86, 1.0)] if not mobile else \
           [(t_start, t_now, 0.0, 0.18), (t_now, t_mid, 0.18, 0.86), (t_mid, t_end, 0.86, 1.0)]

    def lx(t):
        for a, b, fa, fb in segs:
            if t <= b:
                t = max(t, a)
                return L + (fa + (t - a) / max(1, b - a) * (fb - fa)) * pw
        return L + pw

    lo, hi = 0.0175, 0.245
    ly = lambda v: Tp + (math.log(hi) - math.log(v)) / (math.log(hi) - math.log(lo)) * (Hh - Tp - B)
    lab = Labeler(fs, L + 2, L + pw - 2, Tp - 8, Hh - B - 2)
    P = [f'<svg viewBox="0 0 {W} {Hh}" role="img" class="chart pmap{" m" if mobile else ""}" '
         f'aria-label="Mapa planu Sonica: historia od sierpnia 2026 i prognoza etapów 1–3 ze szczytami, dołkami i datami">']
    T = []   # teksty dokładane na końcu (nad liniami)

    # tła faz
    phases = [(t_start, t_now, "historia"), (t_now, date_ts("2027-01-16"), "etap 1"), (date_ts("2027-01-16"), date_ts("2027-01-30"), "korekta"),
              (date_ts("2027-01-30"), date_ts("2027-02-28"), "etap 2"), (date_ts("2027-02-28"), t_end, "korekta · etap 3")]
    for k, (a, b, name) in enumerate(phases):
        x1, x2 = lx(a), lx(b)
        if k % 2 == 1:
            P.append(f'<rect x="{x1:.1f}" y="{Tp - 34}" width="{x2 - x1:.1f}" height="{Hh - B - Tp + 34}" class="ph-bg"/>')
        if x2 - x1 > lab.width(name, sm):
            P.append(f'<text x="{(x1 + x2) / 2:.1f}" y="{Tp - 18}" text-anchor="middle" class="ph-t" style="font-size:{sm}px">{E(name)}</text>')
    xm = lx(t_mid)
    P.append(f'<line x1="{xm:.1f}" x2="{xm:.1f}" y1="{Tp - 34}" y2="{Hh - B}" class="brk"/>')

    # oś Y (log)
    for v in (0.02, 0.03, 0.04, 0.05, 0.07, 0.10, 0.15, 0.20):
        y = ly(v)
        P.append(f'<line x1="{L}" x2="{L + pw:.1f}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        P.append(f'<text x="{L + pw + 6:.1f}" y="{y + fs / 3:.1f}" class="axis" style="font-size:{fs}px">{("0," + f"{v:.2f}"[2:]).rstrip("0")}</text>')

    # linie poziome: unieważnienie, SMA100, potwierdzenie etapu 1 (etykiety w części historycznej)
    inv = plan["invalidation"]["daily_close_below"]
    conf = plan["confirmations"]["stage1_confirm_daily_close_above"]
    sma100 = (pc.get("confirm") or {}).get("sonic_sma100")
    hl = [(inv, "neg", f"unieważnienie planu: zamknięcie dnia < {fmt(inv)}", +1, "end"),
          (conf, "mut", f"potwierdzenie etapu 1: {fmt(conf)}", -1, "start")]
    if sma100:
        hl.append((sma100, "mut2", f"SMA100 {fmt(sma100)}", -1, "end"))
    for v, cls, txt, d, anc in hl:
        y = ly(v)
        P.append(f'<line x1="{L}" x2="{L + pw:.1f}" y1="{y:.1f}" y2="{y:.1f}" class="hl-{cls}"/>')
        T.append((L + 4 if anc == "start" else L + pw - 4, y + (sm + 3 if d > 0 else -5), [(txt, f"hl-{cls}-t", sm)], anc, d))

    # strefy kroków
    for p in path:
        if p["kind"] == "check":
            continue
        cur = (p["kind"] == "top" and status.get(p["step"]) == "teraz-sprzedaż") or \
              (p["kind"] == "bottom" and status.get(p["step"]) == "teraz-odkup")
        cls = {"top": "z-sell", "bottom": "z-buy"}[p["kind"]] + (" z-cur" if cur else "") + (" z-mid" if p.get("mid") else "")
        half = (2.5 * DAY) if p["t"] < t_mid else 14 * DAY
        x1, x2 = lx(p["t"] - half), lx(p["t"] + half)
        y1, y2 = ly(p["hi"]), ly(p["lo"])
        P.append(f'<rect x="{x1:.1f}" y="{y1:.1f}" width="{max(3, x2 - x1):.1f}" height="{max(2, y2 - y1):.1f}" rx="2" class="{cls}">'
                 f'<title>{E(p["label"])}: {"sprzedaż" if p["kind"] == "top" else "odkup"} {fmt(p["price"])} '
                 f'(widełki {fmt(p["lo"])}–{fmt(p["hi"])}), ok. {d_full(p["t"])}</title></rect>')
    stz = step_by_id(plan, "start")
    if status.get("start") == "teraz-zakup":
        x1, x2 = lx(t_now - 3 * DAY), lx(t_now + 3 * DAY)
        P.append(f'<rect x="{x1:.1f}" y="{ly(stz["buy_range"][1]):.1f}" width="{x2 - x1:.1f}" '
                 f'height="{ly(stz["buy_range"][0]) - ly(stz["buy_range"][1]):.1f}" rx="2" class="z-buy z-cur">'
                 f'<title>Strefa akumulacji {fmt(stz["buy_range"][0])}–{fmt(stz["buy_range"][1])}, limit {fmt(stz["buy"])}</title></rect>')
    s3 = step_by_id(plan, "3f")
    if s3.get("optimistic"):
        a, b = s3["optimistic"]
        x1, x2 = lx(date_ts(s3["eta"]) - 14 * DAY), lx(date_ts(s3["eta"]) + 14 * DAY)
        P.append(f'<rect x="{x1:.1f}" y="{ly(b):.1f}" width="{x2 - x1:.1f}" height="{ly(a) - ly(b):.1f}" rx="2" class="z-opt">'
                 f'<title>Etap 3, wariant optymistyczny {fmt(a)}–{fmt(b)}</title></rect>')

    # historia (zamknięcia dzienne) + bieżąca cena
    P.append("".join(f'<line x1="{lx(d["t"] + DAY // 2):.1f}" x2="{lx(d["t"] + DAY // 2):.1f}" y1="{ly(d["h"]):.1f}" y2="{ly(d["l"]):.1f}" class="wick"/>'
                     for d in hist if d.get("h") and d.get("l")))
    pts = [(lx(d["t"] + DAY // 2), ly(d["c"])) for d in hist] + [(lx(t_now), ly(price))]
    P.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + '" class="hist-line"/>')
    fut = [p for p in path if p["t"] > t_now]
    ppts = [(lx(t_now), ly(price))] + [(lx(p["t"]), ly(p["price"])) for p in fut]
    P.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in ppts) + '" class="proj-line"/>')

    # „teraz” – stawiane jako pierwsze, żeby było zawsze czytelne
    xn, yn = lx(t_now), ly(price)
    P.append(f'<line x1="{xn:.1f}" x2="{xn:.1f}" y1="{Tp - 34}" y2="{Hh - B}" class="now-v"/>')
    lab.block(xn - 9, yn - 9, xn + 9, yn + 9)
    txt, _, _ = lab.place(xn - 10, yn + fs + 16, [(f"teraz {fmt(price)}", "now-t lg", fs + 1),
                                                  (datetime.now(timezone.utc).strftime("%d.%m.%Y"), "lbl-m", sm)], anchor="end", direction=1)
    T.append(txt)

    # punkty historyczne
    hk = [(meta["launch_low"], "dołek", "b"), (meta["first_impulse_top"], "szczyt", "t"), (meta["correction_low"], "dołek korekty", "b")]
    for k, name, kind in hk:
        t = date_ts(k["date"]) + DAY // 2
        if t < t_start:
            continue
        x, y = lx(t), ly(k["price"])
        lab.block(x - 5, y - 5, x + 5, y + 5)
        P.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{5 if mobile else 4}" class="hk-dot"><title>{E(name)} {fmt(k["price"])} ({d_full(t)})</title></circle>')
        lines = [(fmt(k["price"]), "hk-t", fs), (f"{name} {d_short(t)}", "lbl-m", sm)]
        y0 = y - fs - sm - 4 if kind == "t" else y + fs + 6
        if mobile and k is meta["correction_low"]:
            continue
        T.append((x, y0, lines, "middle", -1 if kind == "t" else 1, (x, y)))

    # punkty prognozy
    for p in fut:
        x, y = lx(p["t"]), ly(p["price"])
        if p["kind"] == "check":
            P.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{4 if mobile else 3}" class="chk-dot"><title>Przystanek SMA100 {fmt(p["price"])}</title></circle>')
            continue
        top = p["kind"] == "top"
        if p.get("mid"):
            P.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{4 if mobile else 3}" class="{"pt-mtop" if top else "pt-mbot"}">'
                     f'<title>Krok pośredni {E(p["label"])} – {"sprzedaż (decyzja na gorąco)" if top else "odkup"} {fmt(p["price"])} '
                     f'(widełki {fmt(p["lo"])}–{fmt(p["hi"])}), ok. {d_full(p["t"])}</title></circle>')
            if top and not mobile:
                T.append((x, y - sm - 6, [(f"pośr. {fmt(p['price'])}", "lbl-mid", sm)], "middle", -1, (x, y)))
            continue
        lab.block(x - 4, y - 4, x + 4, y + 4)
        P.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{5 if mobile else 4}" class="{"pt-top" if top else "pt-bot"}">'
                 f'<title>{E(p["label"])} – {"sprzedaż" if top else "odkup"} {fmt(p["price"])} (widełki {fmt(p["lo"])}–{fmt(p["hi"])}), ok. {d_full(p["t"])}</title></circle>')
        far = p["t"] > t_mid
        name = ("etap 3 · " if top else "korekta · ") if far else ""
        lines = [(name + fmt(p["price"]), "lbl-top" if top else "lbl-bot", fs), (d_short(p["t"]) if not far else d_full(p["t"])[3:].replace(".", "/"), "lbl-m", sm)]
        if mobile and not top and not far:
            lines = lines[:1]
        if mobile and p["step"] in ("2a", "2b", "2c") or (mobile and not top and not far and p["step"] != "5"):
            continue          # na telefonie te punkty opisuje tabela kroków
        y0 = y - sm - 9 if top else y + fs + 7
        T.append((x, y0, lines, "middle", -1 if top else 1, (x, y)))

    # teksty: najpierw te już złożone, potem reszta przez układacz (z linią prowadzącą, gdy przesunięte)
    out = []
    for item in T:
        if isinstance(item, str):
            out.append(item)
            continue
        x, y, lines, anchor, d = item[:5]
        txt, moved, (tx, ty) = lab.place(x, y, lines, anchor=anchor, direction=d)
        if len(item) > 5 and abs(ty - y) > fs * 0.9:
            px, py = item[5]
            yb = ty + 3 if d < 0 else ty - lines[0][2] * 0.9
            yb = yb + sum(l[2] + 2 for l in lines[1:]) if d < 0 else yb
            out.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{tx:.1f}" y2="{yb:.1f}" class="leader"/>')
        out.append(txt)
    P += out

    # oś X
    seen = []
    for t in range(t_start, t_end, DAY):
        d = datetime.fromtimestamp(t, timezone.utc)
        if d.day != 1 or (t > t_mid and d.month % 3 != 0):
            continue
        x = lx(t)
        if (seen and x - seen[-1] < (46 if mobile else 30)) or x < L + 18:
            continue
        seen.append(x)
        lab_x = MONTHS[d.month - 1] + (f" {d.year % 100}" if d.month == 1 or len(seen) == 1 else "")
        P.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{Hh - B}" y2="{Hh - B + 5}" class="tick"/>')
        P.append(f'<text x="{x:.1f}" y="{Hh - B + fs + 8}" text-anchor="middle" class="axis" style="font-size:{fs}px">{lab_x}</text>')
    P.append(f'<text x="{(xm + L + pw) / 2:.1f}" y="{Tp - 40}" text-anchor="middle" class="lbl-m" style="font-size:{sm - 1}px">czas zagęszczony</text>')
    P.append("</svg>")
    return "".join(P)


# ----------------------------------------------------------------- tabela kroków
STATUS_LABEL = {"pośredni-czeka": ("pośredni – obserwujemy", "mid"), "pośredni-dotknięty": ("pośredni – dotknięty, czeka odkup/wybicie", "mid"),
                "pośredni-minięty": ("pośredni – minięty", "done"), "teraz-sprzedaż": ("teraz: czekamy na sprzedaż", "cur"), "teraz-odkup": ("teraz: czekamy na odkup", "cur"),
                "teraz-zakup": ("teraz: akumulacja", "cur"), "zrealizowany": ("zrealizowany", "done"),
                "sprzedano": ("sprzedano", "done"), "kupiono": ("kupiono", "done"), "przyszły": ("", "fut")}


def steps_table(pc):
    st = step_status(pc)
    rows = []
    for s in pc["plan"]["steps"]:
        if s.get("intermediate") and not show_mids(pc):
            continue
        lab, cls = STATUS_LABEL[st[s["id"]]]
        if s["id"] == "start":
            sell = f"strefa zakupu <b>{fmt(s['buy_range'][0])}–{fmt(s['buy_range'][1])}</b>"
            reb = f"limit {fmt(s['buy'])}, wybicie {fmt(s['breakout'])}"
            eta, reta = d_full(date_ts(s["eta"])), ""
        elif s.get("checkpoint"):
            sell = f"<span class='muted'>przystanek</span> {fmt(s['level'])} <span class='muted'>({fmt(s['level_range'][0])}–{fmt(s['level_range'][1])})</span>"
            reb, eta, reta = "<span class='muted'>bez transakcji</span>", d_full(date_ts(s["eta"])), ""
        else:
            sell = (f"limit <b>{fmt(sell_limit(s['sell_range'][0], s['sell']))}</b> <span class='muted'>(widełki {fmt(s['sell_range'][0])}–"
                    f"{fmt(s['sell_range'][1])}, główny {fmt(s['sell'])})</span>")
            reb = (f"limit <b>{fmt(s['rebuy_range'][1])}</b> <span class='muted'>(widełki {fmt(s['rebuy_range'][0])}–{fmt(s['rebuy_range'][1])}, główny {fmt(s['rebuy'])})</span>"
                   if s.get("rebuy") else "<span class='muted'>koniec planu</span>")
            eta = d_full(date_ts(s["eta"]))
            reta = d_full(date_ts(s["rebuy_eta"])) if s.get("rebuy_eta") else ""
            if s.get("optimistic"):
                reb = f"<span class='muted'>optymistycznie {fmt(s['optimistic'][0])}–{fmt(s['optimistic'][1])}</span>"
        if s.get("intermediate"):
            sell = (f"<span class='midtag'>pośredni</span> limit {fmt(sell_limit(s['sell_range'][0], s['sell']))} "
                    f"<span class='muted'>({fmt(s['sell_range'][0])}–{fmt(s['sell_range'][1])})</span>")
            reb = f"limit {fmt(s['rebuy_range'][1])} <span class='muted'>({fmt(s['rebuy_range'][0])}–{fmt(s['rebuy_range'][1])}), wybicie {fmt(s['breakout'])}</span>"
        note = s.get("rebuy_note") or s.get("note") or s.get("condition") or ""
        rows.append(f"<tr class='st-{cls}{' midrow' if s.get('intermediate') else ''}'><td><b>{E(s['label'])}</b>{f'<br><span class=muted>{E(note)}</span>' if note else ''}</td>"
                    f"<td>{sell}</td><td class='n'>{eta}</td><td>{reb}</td><td class='n'>{reta}</td>"
                    f"<td>{f'<span class=badge-{cls}>{E(lab)}</span>' if lab else ''}</td></tr>")
    return ("<div class='tablewrap'><table class='steps'><thead><tr><th>Krok</th><th>Sprzedaż (widełki)</th><th class='n'>ok. data</th>"
            "<th>Odkup (widełki)</th><th class='n'>ok. data</th><th>Status</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


# ----------------------------------------------------------------- prognoza kapitału
def fusd(x):
    return f"{x:,.0f}".replace(",", " ") + " USD"


def fs(x):
    return f"{x:,.0f}".replace(",", " ") + " S"


def capital_table(pc):
    plan = pc["plan"]
    C = plan.get("capital") or {}
    base = {r["step"]: r for r in capital_path(plan, False)}
    two = show_mids(pc)
    full = {r["step"]: r for r in capital_path(plan, True)} if two else base
    st = step_status(pc)
    start = step_by_id(plan, "start")
    rows = []
    for s in plan["steps"]:
        i = s["id"]
        if i not in full:
            continue
        a, b = base.get(i), full[i]
        cur = st.get(i, "").startswith("teraz") or st.get(i, "").startswith("pośredni-czeka")
        cls = ("midrow " if s.get("intermediate") else "") + ("st-cur" if cur else "")
        name = (f"<span class='midtag'>pośredni</span> " + E(s["label"].replace("Pośredni ", ""))) if s.get("intermediate") else E(s["label"])
        cell = lambda r, k, f: f(r[k]) if r and r.get(k) is not None else "<span class='muted'>–</span>"
        rng_usd = lambda r: (f"<br><span class='rng'>{fusd(r['usd_lo'])} – {fusd(r['usd_hi'])}</span>" if r else "")
        rng_s = lambda r: (f"<br><span class='rng'>do {fs(r['S_hi'])}</span>" if r and r.get("S_hi") else "")
        rows.append(f"<tr class='{cls}'><td>{name}</td><td class='n'>{fmt(b['sell'])}</td><td class='n'>{fmt(b['rebuy']) if b['rebuy'] else 'gotówka'}</td>"
                    f"<td class='n'><b>{cell(a, 'usd', fusd)}</b>{rng_usd(a)}</td><td class='n'>{cell(a, 'S', fs)}{rng_s(a)}</td>"
                    + (f"<td class='n'>{cell(b, 'usd', fusd)}{rng_usd(b)}</td><td class='n'>{cell(b, 'S', fs)}{rng_s(b)}</td>" if two else "") + "</tr>")
    s0 = float(C.get("start_S", 100000))
    hold = s0 * step_by_id(plan, "2d")["sell"]
    last_a, last_b = list(base.values())[-1], list(full.values())[-1]
    foot = (f"<tr class='tot'><td><b>Wynik na szczycie etapu 2</b></td><td></td><td></td><td class='n'><b>{fusd(last_a['usd'])}</b></td><td></td>"
            + (f"<td class='n'><b>{fusd(last_b['usd'])}</b></td><td></td>" if two else "") + "</tr>")
    head = ("<tr><th rowspan='2'>Krok</th><th class='n' rowspan='2'>Sprzedaż</th><th class='n' rowspan='2'>Odkup</th>"
            "<th class='n' colspan='2'>Tylko kroki główne</th>" + ("<th class='n' colspan='2'>Z krokami pośrednimi</th>" if two else "") + "</tr>"
            "<tr><th class='n'>USD po sprzedaży</th><th class='n'>S po odkupie</th>"
            + ("<th class='n'>USD po sprzedaży</th><th class='n'>S po odkupie</th>" if two else "") + "</tr>")
    return (f"<h3>Prognoza kapitału na kolejnych krokach</h3>"
            f"<p class='muted'>Start: {fs(s0)} kupione po {fmt(start['buy'])} (ok. {fusd(s0 * start['buy'])}). Wariant ostrożny – limity jak w sygnałach "
            f"(sprzedaż w połowie dolnej części widełek, odkup na górnej granicy), prowizja {str(C.get('fee_pct', 0.4)).replace('.', ',')}% od każdej transakcji. "
            f"Kwota w USD to gotówka po sprzedaży na danym kroku (pod spodem: zakres przy sprzedaży na dolnej i górnej granicy widełek), "
            f"liczba S – stan po odkupie na limicie (pod spodem: ile, gdyby cena zeszła do dolnej granicy widełek odkupu). Dla porównania: trzymanie {fs(s0)} do 0,120 = {fusd(hold)}.</p>"
            f"<div class='tablewrap'><table class='steps cap'><thead>{head}</thead>"
            f"<tbody>{''.join(rows)}{foot}</tbody></table></div>"
            "<p class='muted'>To prognoza przy założeniu, że rynek przejdzie ścieżkę planu i każdy odkup się wykona. Każde kupno na wybiciu zamiast odkupu "
            "kosztuje ok. 2–4% na danym kroku. Porównuj swój stan Soniców i gotówki z tą tabelą – pokazuje, czego się trzymać.</p>")


# ----------------------------------------------------------------- „gdzie jesteśmy”
KIND_PL = {"MID_SELL": "krok pośredni – strefa", "MID_REBUY": "odkup po pośrednim", "MID_BREAKOUT": "wybicie nad pośrednim", "PREP_MID": "zbliża się do pośredniego", "BUY": "zakup", "BUY_BREAKOUT": "kupno na wybiciu", "SELL": "sprzedaż", "PREP_SELL": "przygotuj sprzedaż",
           "PREP_BUY": "przygotuj zakup", "STEP_FAIL": "odkup zawiódł", "INVALID": "plan unieważniony"}


def where_block(pc):
    from notify import pl_time
    st, pos, price = pc["state"], pc["position"], pc["price"]
    inv = pc["plan"]["invalidation"]["daily_close_below"]
    ph = {"BUY": "czekamy na zakup", "HOLD": "w pozycji – czekamy na sprzedaż", "INVALID": "plan unieważniony",
          "DONE": "plan zakończony"}[st["phase"]]
    t = st.get("target") or {}
    z = st.get("zone") or {}
    kv = [("Faza planu", f"<b>{E(ph)}</b>")]
    if st["phase"] == "BUY":
        inside = z["lo"] <= price <= z["hi"]
        kv.append(("Strefa zakupu", f"{fmt(z['lo'])}–{fmt(z['hi'])} " + ("(<b>cena w strefie</b>)" if inside else f"({fpc(pct(z['hi'], price))} od ceny)")))
        kv.append(("Limit zakupu", f"{fmt(z['limit'])} ({fpc(pct(z['limit'], price))} od ceny)"))
        if z.get("breakout"):
            kv.append(("Kupno na wybiciu", f"2 zamknięcia 1h powyżej {fmt(z['breakout'])} ({fpc(pct(z['breakout'], price))})"))
    if t:
        kv.append(("Następna sprzedaż", f"{E(t['label'])}: limit {fmt(t['limit'])} (widełki {fmt(t['lo'])}–{fmt(t['hi'])}, {fpc(pct(t['lo'], price))} do widełek)"))
    for m in st.get("mids") or []:
        if m["status"] == "pending":
            kv.append((f"Krok pośredni {E(m['id'])}", f"limit {fmt(m['limit'])} (widełki {fmt(m['lo'])}–{fmt(m['hi'])}, {fpc(pct(m['lo'], price))}) – decyzja na gorąco"))
        elif m["status"] == "touched":
            kv.append((f"Po pośrednim {E(m['id'])}", f"jeśli sprzedałeś: odkup {fmt(m['rebuy_lo'])}–{fmt(m['rebuy_hi'])} albo wybicie nad {fmt(m['breakout'])}"))
    kv.append(("Do szczytu etapu 1 / 2", f"{fpc(pos['to_stage1_top_pct'])} / {fpc(pos['to_stage2_top_pct'])}"))
    kv.append(("Do unieważnienia planu", f"{fpc(pct(inv, price))} (zamknięcie dnia poniżej {fmt(inv)})"))
    bars = "".join(
        f"<div class='meter'><p>{E(name)}: <strong>{v:.0f}%</strong> drogi (skala log.)</p><div class='mtrack'><div class='mfill' style='width:{v:.0f}%'></div></div></div>"
        for name, v in (("Etap 1: od strefy zakupu do szczytu 0,071", pos["progress_stage1"]),
                        ("Etap 2: od strefy zakupu do szczytu 0,120", pos["progress_stage2"])))
    ev = [e for e in pc["events"] if not e["kind"].startswith("PREP")][-6:][::-1]
    evh = "".join(f"<li><b>{E(KIND_PL.get(e['kind'], e['kind']))}</b> {E(str(e.get('step') or ''))} – {pl_time(e['t'])}, cena {fmt(e['price'])}</li>" for e in ev)
    conf = "".join(f"<li class='{'ok' if c['ok'] else 'no'}'><span class='mk' aria-hidden='true'>{'✓' if c['ok'] else '✗'}</span>"
                   f"<span>{E(c['name'])}</span></li>" for c in (pc.get("confirm") or {}).get("items", []))
    return (f"<div class='two'><div class='panel now-panel'><h4>Gdzie jesteśmy</h4><p class='act'>{E(pc['action'])}</p>"
            f"<dl class='kv'>{''.join(f'<dt>{a}</dt><dd>{b}</dd>' for a, b in kv)}</dl>{bars}</div>"
            f"<div class='panel'><h4>Potwierdzenia trendu (dzienne)</h4><ul class='check'>{conf}</ul>"
            f"<h4>Ostatnie sygnały planu (świece 1h)</h4><ul class='flags'>{evh or '<li>brak sygnałów od startu planu</li>'}</ul>"
            f"<p class='muted'>Start odtwarzania planu: {E(pc['plan']['start']['from'][:16].replace('T', ' '))} UTC, świece 1h: {E(pc.get('c1h_source') or '')}.</p></div></div>")


# ----------------------------------------------------------------- BTC: drabinka poziomów
def btc_ladder(pc, btc_price):
    bp = pc["btc"]
    rows, placed = [], False
    for l in bp["levels"]:
        if not placed and l["price"] < btc_price and not l["inside"]:
            rows.append(f"<li class='rung now'><span class='rp'>{fmt(btc_price)}</span><span class='rd'>cena teraz</span><span class='rs'></span><span class='rsrc'></span></li>")
            placed = True
        kind = {"r": "r", "s": "s", "p": "r"}[l["kind"]] if not l["inside"] else "now"
        lab = "w strefie" if l["inside"] else ("opór" if l["price"] > btc_price else "wsparcie")
        rows.append(f"<li class='rung {kind}{' key' if 'kluczowe' in l['label'] or 'SMA200' in l['label'] else ''}'><span class='rp'>{fmt(l['price'])}</span>"
                    f"<span class='rd'><span class='rk'>{lab}</span> {fpc(l['dist_pct'])} od ceny</span><span class='rs'></span>"
                    f"<span class='rsrc'>{E(l['label'])} ({fmt(l['range'][0])}–{fmt(l['range'][1])})</span></li>")
    if not placed:
        rows.append(f"<li class='rung now'><span class='rp'>{fmt(btc_price)}</span><span class='rd'>cena teraz</span><span class='rs'></span><span class='rsrc'></span></li>")
    sc = "".join(f"<tr><td>{E(s['name'])}</td><td class='n'>{s['pct']}%</td></tr>" for s in bp["scenarios"])
    beta = bp.get("beta")
    return (f"<div class='two'><div><ol class='ladder' aria-label='Poziomy BTC'>{''.join(rows)}</ol></div>"
            f"<div class='panel'><h4>Scenariusze dna korekty BTC</h4><table><tbody>{sc}</tbody></table>"
            f"<p class='muted'>Dołek korekty: {fmt(bp['low']['price'])} ({E(bp['low']['date'])}); szczyt przed korektą: {fmt(bp['high']['price'])} ({E(bp['high']['date'])}). "
            + (f"Sonic porusza się średnio ok. {str(beta).replace('.', ',')}× mocniej niż BTC (beta). " if beta else "")
            + "Poziomy i szanse z analizy 15–17.11.2026; SMA200 liczona na bieżąco.</p></div></div>")


def btc_level_lines(pc, ly, L, W, R, lo, hi):
    out = []
    for l in pc["btc"]["levels"]:
        if lo < l["price"] < hi:
            y = ly(l["price"])
            cls = "lvl-r" if l["kind"] in ("r", "p") else "lvl-s"
            out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="{cls}"><title>{E(l["label"])}</title></line>')
    return "".join(out)


PLAN_CSS = """
.pmap .ph-bg{fill:#ffffff;opacity:.03}.pmap .ph-t{fill:var(--muted);letter-spacing:.08em;text-transform:uppercase}
.pmap .wick{stroke:var(--sonic);stroke-width:1;opacity:.28}.pmap .leader{stroke:var(--muted);stroke-width:.8;opacity:.6}.pmap .grid{stroke:var(--line);stroke-width:1;opacity:.45}.pmap .lg{font-weight:700}
.pmap .brk{stroke:var(--line);stroke-width:1.5;stroke-dasharray:2 5}
.pmap .hl-neg{stroke:var(--neg);stroke-width:1.2;stroke-dasharray:6 4}.pmap .hl-neg-t{fill:var(--neg)}
.pmap .hl-mut{stroke:var(--muted);stroke-width:1;stroke-dasharray:6 4;opacity:.7}.pmap .hl-mut-t{fill:var(--muted)}
.pmap .hl-mut2{stroke:var(--muted);stroke-width:1;stroke-dasharray:2 4;opacity:.6}.pmap .hl-mut2-t{fill:var(--muted)}
.pmap .z-sell{fill:var(--neg);opacity:.16}.pmap .z-buy{fill:var(--pos);opacity:.16}.pmap .z-chk{fill:var(--muted);opacity:.12}
.pmap .z-cur{opacity:.42;stroke:currentColor}.pmap .z-opt{fill:var(--neg);opacity:.07}
.pmap .hist-line{fill:none;stroke:var(--sonic);stroke-width:2;stroke-linejoin:round}
.pmap .proj-line{fill:none;stroke:var(--sonic);stroke-width:2;stroke-dasharray:7 5;opacity:.75;stroke-linejoin:round}
.pmap .hk-dot{fill:var(--sonic);stroke:var(--panel);stroke-width:2}.pmap text{font-variant-numeric:tabular-nums}.pmap .hk-t{fill:var(--text);font-weight:600}
.pmap .pt-top{fill:var(--neg);stroke:var(--panel);stroke-width:2}.pmap .pt-bot{fill:var(--pos);stroke:var(--panel);stroke-width:2}
.pmap .chk-dot{fill:var(--muted)}.pmap .z-mid{opacity:.10;stroke:var(--warn);stroke-dasharray:3 3;stroke-width:1}
.pmap .pt-mtop,.pmap .pt-mbot{fill:var(--panel);stroke:var(--warn);stroke-width:1.5}.pmap .lbl-mid{fill:var(--warn)}
.midtag{color:var(--warn);font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
table.steps tr.midrow td{font-size:.9em}.badge-mid{color:var(--warn);font-weight:600;font-size:.86rem}.pmap .lbl-top{fill:#F2A29C;font-weight:600}.pmap .lbl-bot{fill:#8FDDC4;font-weight:600}.pmap .lbl-m{fill:var(--muted)}
.pmap .now-v{stroke:var(--sonic);stroke-width:1;opacity:.5}.pmap .now-ring{fill:none;stroke:var(--sonic);stroke-width:2}
.pmap .now-dot{fill:var(--sonic)}.pmap .now-t{fill:var(--sonic)}.pmap .tick{stroke:var(--muted)}
.pmap rect:hover,.pmap circle:hover{opacity:.9}
.act{font-size:1.08rem;font-weight:600;margin:4px 0 12px;line-height:1.5}
.now-panel{border-left:3px solid var(--sonic)}
table.steps td{vertical-align:top}table.cap tr.tot td{border-top:2px solid var(--line)}table.cap th{white-space:nowrap}table.cap .rng{color:var(--muted);font-size:.78rem;font-weight:400;white-space:nowrap}table.steps tr.st-cur td{background:rgba(92,200,232,.10)}table.steps tr.st-done td{color:var(--muted)}
.badge-cur{background:var(--sonic);color:var(--ink);border-radius:4px;padding:1px 7px;font-weight:700;font-size:.82rem;white-space:nowrap}
.badge-done{color:var(--pos);font-weight:600;font-size:.86rem}
.plan-legend{display:flex;flex-wrap:wrap;gap:6px 18px;color:var(--muted);font-size:.85rem;margin:4px 0 0;padding:0;list-style:none}
.plan-legend i{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.plan-legend .l-h{height:3px;background:var(--sonic)}.plan-legend .l-p{height:0;border-top:2px dashed var(--sonic)}
.plan-legend .l-m{border:1.5px dashed var(--warn);background:none}.plan-legend .l-s{background:var(--neg);opacity:.5}.plan-legend .l-b{background:var(--pos);opacity:.5}.plan-legend .l-i{height:0;border-top:2px dashed var(--neg)}
"""


def plan_section(pc, sonic):
    leg = ("<ul class='plan-legend'><li><i class='l-h'></i>cena (zamknięcia dzienne)</li><li><i class='l-p'></i>prognoza wg planu</li>"
           "<li><i class='l-s'></i>widełki sprzedaży</li><li><i class='l-b'></i>widełki zakupu/odkupu</li>"
           + 
           ("<li><i class='l-m'></i>krok pośredni (decyzja na gorąco)</li>" if show_mids(pc) else "") + "<li><i class='l-i'></i>unieważnienie planu</li><li>mocniej podświetlona strefa = bieżący krok</li></ul>")
    meta = pc["plan"]["meta"]
    return (f"<h3>Mapa planu: historia od sierpnia 2026 i prognoza</h3>"
            f"<p class='muted'>{E(meta['basis'])} {E(meta['tempo_note'])}</p>"
            f"<div class='ch-d'>{projection_chart_svg(pc, sonic)}</div><div class='ch-m'>{projection_chart_svg(pc, sonic, True)}</div>{leg}"
            f"{where_block(pc)}"
            f"<h3>Kroki planu</h3>{steps_table(pc)}{capital_table(pc)}"
            "<p class='muted'><b>Wariant ostrożny:</b> sprzedaż z limitem w połowie między dolną granicą widełek a głównym poziomem, "
            "a gdy cena w widełkach zawraca – od razu. Odkup z limitem na górnej granicy widełek, żeby nie zostać bez pozycji. Gdy odkup się nie wykona: kupno na wybiciu po 2 zamknięciach 1h powyżej poprzedniego celu. "
            + ("Kroki pośrednie (żółte) to opory po drodze: bot ostrzega, a decyzję o sprzedaży podejmujesz na gorąco, patrząc, czy cena zawraca. "
               if show_mids(pc) else "Kroki pośrednie ocenia codzienna analiza w czacie Claude (nie bot). ") +
            "Na ntfy: ostrzeżenie, gdy cena jest 2% od poziomu, i alert w chwili wejścia w widełki (czuwanie co minutę w trakcie świecy), "
            "potem krótkie potwierdzenie po zamknięciu świecy 1h.</p>")
