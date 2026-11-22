# -*- coding: utf-8 -*-
"""
handel-bot – monitor Sonic (S) + kontekst Bitcoina
==================================================
Uruchamiany co godzinę przez GitHub Actions (.github/workflows/bot.yml).

Co robi przy każdym uruchomieniu:
  1. Pobiera świece dzienne Sonica z Coinbase i dokleja je do historii z data/SONIC_FTM_S_1d.csv,
     oraz świece 1h Sonica od początku planu (data/sonic_plan.json).
  2. Odtwarza plan etapowy na zamkniętych świecach 1h (plan.py): zakup, sprzedaż, odkup, kupno na wybiciu,
     nieudany odkup, strefy wsparcia przy głębszej korekcie (unieważnienie wyłączone).
  3. Nowe kluczowe sygnały (z ostatnich godzin, jeszcze niewysłane) -> ntfy (notify.py).
  4. Raz dziennie (pierwsze uruchomienie po REPORT_HOUR_UTC) buduje pełny raport:
     dane BTC (on-chain, kontrakty, nastroje), księga zleceń Coinbase, wyszukiwanie w sieci i komentarz AI (Groq),
     a potem wysyła index.html na FTP (plus kopię w archiwum).
Stan (wysłane sygnały, data raportu) jest trzymany na FTP poza public_html, bo runner GitHub Actions jest za każdym razem czysty.
"""
import os, io, sys, json, html, time, ftplib, asyncio, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

import aiohttp

import market_data as M
import analysis as A
import ai as AI
import plan as P
import monitor as MON
import notify as N
import swing as SW
import report_html as R
import sonic_history as H
from sonic_core import DAY

# --- KONFIGURACJA (sekrety GitHub Actions) ---
FTP_HOST = os.getenv("FTP_HOST", "piwecki.pl")
FTP_USER = os.getenv("FTP_USER", "")
FTP_PASS = os.getenv("FTP_PASS", "")
FTP_DIR = os.getenv("FTP_DIR", "domains/piwecki.pl/public_html/analiza")
FTP_STATE_DIR = os.getenv("FTP_STATE_DIR", "domains/piwecki.pl/bot_state")   # poza public_html
FTP_USE_TLS = os.getenv("FTP_USE_TLS", "1") == "1"
REPORT_URL = os.getenv("REPORT_URL", "https://piwecki.pl/analiza/")
REPORT_HOUR_UTC = int(os.getenv("REPORT_HOUR_UTC", "5"))       # raport dzienny po 05:00 UTC (06/07 w Polsce)
FORCE_REPORT = os.getenv("FORCE_REPORT", "0") == "1"           # ręczne uruchomienie z GitHuba wymusza raport
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"                     # test lokalny: bez FTP i ntfy, raport do pliku
STATE_FILE = "sonic_bot_state.json"
LIVE_UNTIL_MINUTE = int(os.getenv("LIVE_UNTIL_MINUTE", "0"))   # >0: po części godzinowej czuwaj na żywo do tej minuty godziny
REPO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raport")   # kopia wyników w repozytorium (commit robi workflow)


# ----------------------------------------------------------------- FTP + stan
def ftp_configured():
    return bool(FTP_HOST and FTP_USER and FTP_PASS) and not DRY_RUN


def ftp_connect():
    if FTP_USE_TLS:
        try:
            ftp = ftplib.FTP_TLS(FTP_HOST, timeout=30)
            ftp.login(FTP_USER, FTP_PASS)
            ftp.prot_p()
            return ftp
        except Exception as e:
            print(f"[FTP] FTPS niedostępne ({type(e).__name__}: {e}) – łączę zwykłym FTP")
    ftp = ftplib.FTP(FTP_HOST, timeout=30)
    ftp.login(FTP_USER, FTP_PASS)
    return ftp


def ftp_cd(ftp, home, path, create=True):
    ftp.cwd(home)
    for d in path.strip("/").split("/"):
        if not d:
            continue
        try:
            ftp.cwd(d)
        except ftplib.error_perm:
            if not create:
                raise
            ftp.mkd(d); ftp.cwd(d)


def ftp_put(ftp, name, data: bytes):
    """Zapis przez plik tymczasowy i zmianę nazwy – odwiedzający nigdy nie zobaczy pół pliku."""
    tmp = name + ".tmp"
    ftp.storbinary(f"STOR {tmp}", io.BytesIO(data))
    try:
        ftp.rename(tmp, name)
    except ftplib.error_perm:
        try:
            ftp.delete(name)
        except ftplib.error_perm:
            pass
        ftp.rename(tmp, name)


def load_state():
    default = {"last_report_date": None, "ntfy_sent": [], "signals": []}
    if ftp_configured():
        try:
            with ftp_connect() as ftp:
                home = ftp.pwd()
                ftp_cd(ftp, home, FTP_STATE_DIR, create=False)
                buf = io.BytesIO()
                ftp.retrbinary(f"RETR {STATE_FILE}", buf.write)
                return {**default, **json.loads(buf.getvalue().decode("utf-8"))}
        except Exception as e:
            print(f"[stan] brak stanu na FTP ({type(e).__name__}) – start od zera")
    if os.path.exists(STATE_FILE):
        try:
            return {**default, **json.load(open(STATE_FILE, encoding="utf-8"))}
        except Exception:
            pass
    return default


def publish(state, html_doc=None, archive_name=None):
    data = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
    with open(STATE_FILE, "wb") as f:
        f.write(data)
    if DRY_RUN:
        if html_doc:
            with open("index.html", "w", encoding="utf-8") as f:
                f.write(html_doc)
            print("[DRY_RUN] raport zapisany lokalnie: index.html")
        return True
    if not ftp_configured():
        print("[FTP] brak danych logowania – pomijam wysyłkę")
        return False
    try:
        with ftp_connect() as ftp:
            home = ftp.pwd()
            if html_doc:
                ftp_cd(ftp, home, FTP_DIR)
                ftp_put(ftp, "index.html", html_doc.encode("utf-8"))
                if archive_name:
                    ftp_cd(ftp, home, FTP_DIR.rstrip("/") + "/archiwum")
                    ftp_put(ftp, archive_name, html_doc.encode("utf-8"))
                print(f"[FTP] raport opublikowany: {REPORT_URL}")
            ftp_cd(ftp, home, FTP_STATE_DIR)
            ftp_put(ftp, STATE_FILE, data)
        return True
    except Exception as e:
        print(f"[FTP] błąd: {type(e).__name__}: {e}")
        return False


# ----------------------------------------------------------------- dane
async def fetch_sonic_1h(session, errors, from_ts):
    """Świece 1h Sonica od początku planu (Coinbase, zapas: Kraken – ostatnie 30 dni)."""
    c1h = await M.safe(M.coinbase_candles(session, "S-USD", 3600, start_ts=from_ts - 80 * 3600), "Coinbase S-USD świece 1h", errors)
    src = "Coinbase"
    if not c1h:
        c1h = await M.safe(M.kraken_ohlc(session, "SUSD", 60), "Kraken S/USD świece 1h", errors)
        src = "Kraken"
    c1h = A.closed_only(c1h or [], period=3600)
    print(f"[Sonic 1h] świec: {len(c1h)} ({src}), ostatnia {datetime.fromtimestamp(c1h[-1]['t'], timezone.utc):%Y-%m-%d %H:%M} UTC" if c1h else "[Sonic 1h] brak świec")
    return c1h, src


async def fetch_btc_price(session, errors):
    for label, fn in (("Coinbase BTC ticker", lambda: M.coinbase_ticker(session, "BTC-USD")),
                      ("Kraken BTC ticker", lambda: M.kraken_ticker(session, "XBTUSD"))):
        t = await M.safe(fn(), label, errors)
        if t:
            return t["price"]
    return None


async def fetch_sonic(session, errors, full):
    hist, path = A.load_history_csv()
    if not hist:
        print("[Sonic] UWAGA: brak data/SONIC_FTM_S_1d.csv – analiza tylko na danych z Coinbase")
    start = (hist[-1]["t"] - 90 * DAY) if hist else int((datetime.now(timezone.utc) - timedelta(days=700)).timestamp())
    live = await M.safe(M.coinbase_candles(session, "S-USD", 86400, start_ts=start), "Coinbase S-USD świece 1D", errors)
    tasks = [M.safe(M.coinbase_ticker(session, "S-USD"), "Coinbase S-USD ticker", errors),
             M.safe(M.coinbase_stats(session, "S-USD"), "Coinbase S-USD statystyki", errors),
             M.safe(M.coinbase_candles(session, "S-USD", 21600), "Coinbase S-USD świece 6h", errors)]
    if full:
        tasks += [M.safe(M.coinbase_book(session, "S-USD"), "Coinbase S-USD księga zleceń", errors),
                  M.safe(M.coingecko_sonic(session), "CoinGecko Sonic", errors)]
    res = await asyncio.gather(*tasks)
    ticker, stats, c6h = res[0], res[1], res[2]
    book, cg = (res[3], res[4]) if full else (None, None)
    if ticker is None:   # zapasowe źródła ceny
        for label, coro in (("Kraken S/USD", M.kraken_ticker(session, "SUSD")), ("OKX S-USDT", M.okx_ticker(session, "S-USDT"))):
            ticker = await M.safe(coro, label, errors)
            if ticker:
                break
    candles, scale = A.merge_history(hist, live or [])
    candles = A.closed_only(candles)
    c6h = A.closed_only(c6h or [], period=21600)
    fresh = bool(live) and candles and candles[-1]["t"] >= int(datetime.now(timezone.utc).timestamp()) - 2 * DAY
    print(f"[Sonic] świec dziennych: {len(candles)} (ostatnia {A.ts_date(candles[-1]['t']) if candles else 'brak'}), "
          f"skala wolumenu Coinbase→Binance ×{scale:.2f}, dane świeże: {fresh}")
    return dict(candles=candles, ticker=ticker, stats=stats, c6h=c6h, book=book, cg=cg, fresh=fresh)


async def fetch_btc(session, errors, full):
    daily = await M.safe(M.coinbase_candles(session, "BTC-USD", 86400), "Coinbase BTC-USD świece", errors)
    if not daily:
        daily = await M.safe(M.kraken_ohlc(session, "XBTUSD", 1440), "Kraken BTC świece", errors)
    if not daily:
        return None
    daily = A.closed_only(daily)
    tick = await asyncio.gather(M.safe(M.coinbase_ticker(session, "BTC-USD"), "Coinbase BTC ticker", errors),
                                M.safe(M.kraken_ticker(session, "XBTUSD"), "Kraken BTC ticker", errors))
    srcs, prices = [], []
    for name, t in zip(("Coinbase", "Kraken"), tick):
        if t:
            srcs.append(name); prices.append(t["price"])
    price = sum(prices) / len(prices) if prices else daily[-1]["c"]
    weekly = onchain = oi_hist = fng = glob = None
    derivs = []
    if full:
        weekly, oi_hist, fng, glob, *derivs = await asyncio.gather(
            M.safe(M.kraken_ohlc(session, "XBTUSD", 10080), "Kraken BTC świece tygodniowe", errors),
            M.safe(M.okx_oi_history(session), "OKX historia OI", errors),
            M.safe(M.fear_greed(session), "Fear & Greed", errors),
            M.safe(M.coingecko_global(session), "CoinGecko global", errors),
            M.safe(M.hyperliquid_btc(session), "Hyperliquid", errors),
            M.safe(M.okx_btc_derivs(session), "OKX kontrakty", errors),
            M.safe(M.deribit_btc(session), "Deribit", errors))
        # realized price i MVRV: najpierw BGeometrics (darmowe, 8 zapytań/h), potem Coin Metrics jako zapas
        rp = await M.safe(M.bitcoin_data_last(session, "realized-price", ("realizedPrice", "realized_price")), "bitcoin-data realized price", errors)
        mv = await M.safe(M.bitcoin_data_last(session, "mvrv", ("mvrv",)), "bitcoin-data MVRV", errors)
        if rp and not (0.1 * price < rp < 2 * price):
            print(f"[BTC] odrzucam podejrzaną wartość realized price: {rp}")
            rp = None
        if rp:
            onchain = dict(source="BGeometrics (bitcoin-data.com)", realized_price=rp, mvrv=mv or (price / rp))
            print(f"[BTC] realized price {rp:,.0f} USD, MVRV {onchain['mvrv']:.2f} (BGeometrics)")
        else:
            onchain = await M.safe(M.coinmetrics_btc(session), "Coin Metrics", errors)
    return A.analyze_btc(daily, weekly or [], price, onchain, derivs, oi_hist, fng, glob, srcs or ["ostatnie zamknięcie Coinbase"])


def _r(x, n=4):
    """Zaokrąglenie do n cyfr znaczących (krótsze dane = mniej tokenów)."""
    if isinstance(x, float):
        return float(f"{x:.{n}g}")
    if isinstance(x, dict):
        return {k: _r(v, n) for k, v in x.items() if v is not None}
    if isinstance(x, list):
        return [_r(v, n) for v in x]
    return x


def ai_btc_data(btc):
    oc = btc.get("onchain") or {}
    fr = [d["funding_8h_pct"] for d in btc["derivatives"] if d.get("funding_8h_pct") is not None]
    return _r(dict(
        cena=btc["price"], zmiana_7d_pct=btc["change_7d_pct"], zmiana_30d_pct=btc["change_30d_pct"],
        vs_sma50_pct=A.pct(btc["price"], btc["sma50"]), vs_sma200_pct=A.pct(btc["price"], btc["sma200"]),
        vs_sma200tyg_pct=A.pct(btc["price"], btc["sma200w"]), mayer=btc["mayer"], rsi_d=btc["rsi"], rsi_tyg=btc["rsi_w"],
        macd_hist=btc["macd_hist"], realized_price=oc.get("realized_price"), mvrv=oc.get("mvrv"),
        open_interest_usd=sum(d.get("oi_usd") or 0 for d in btc["derivatives"]) or None, oi_zmiana_7d_pct=btc.get("oi_7d_change_pct"),
        funding_8h_pct=(sum(fr) / len(fr)) if fr else None,
        strach_chciwosc=(btc.get("fng") or {}).get("value"), dominacja_btc_pct=(btc.get("global_") or {}).get("btc_dominance"),
        ocena_tla_dla_altcoinow=btc["context_label"]))


def ai_sonic_data(s, plan_ai=None):
    lv, ob, tp = s["levels"], s.get("orderbook") or {}, s["turning"]
    lvl = lambda l: dict(cena=l["price"], odl_pct=l["dist_pct"], zrodla=l["sources"][:2])
    setups = {}
    for side, st in s["setups"].items():
        L = st.get("levels") or {}
        setups[side] = dict(aktywny=st["triggered"], warunki=st["conditions"], strefa=[L.get("zone_low"), L.get("zone_high")],
                            uniewaznienie=L.get("invalidation"), cel=L.get("target"), odl_do_strefy_pct=st.get("dist_to_zone_pct"),
                            historia=dict(n=st["backtest"].get("n"), trafnosc_pct=st["backtest"].get("win_rate"),
                                          sredni_R=st["backtest"].get("avg_R")))
    cg = s.get("coingecko") or {}
    h = s["history"]
    return _r(dict(
        cena=s["price"], swieca=s["candle_date"], zmiana_7d_pct=s["change_7d_pct"], zmiana_30d_pct=s["change_30d_pct"],
        zmiana_90d_pct=s["change_90d_pct"], od_ATH_pct=h["from_ath_pct"], ATH=h["ath"], razy_od_ATL=h["from_atl_x"],
        pozycja_w_zakresie_12m_pct=h["year_position_pct"], kierunek=s["direction"] and {k: v for k, v in s["direction"].items() if k != "short_term_detail"},
        struktura={d: dict(trend=v["trend"], ostatnie=[f"{x['label']} {x['price']:.4g} {x['date']}" for x in v["swings"][-3:]])
                   for d, v in s["structure"].items()},
        fibo=[dict(ruch=f"{f['start']:.4g}->{f['end']:.4g} ({f['start_date']}–{f['end_date']})", zniesione_pct=f["current_retrace_pct"],
                   poziomy={str(k): v for k, v in f["levels"].items() if k in (0.382, 0.5, 0.618, 0.786)}) for f in s["fibs"][:2]],
        wsparcia=[lvl(l) for l in lv["supports"][:3]], opory=[lvl(l) for l in lv["resistances"][:3]],
        wskazniki={k: s["indicators"][k] for k in ("rsi", "rsi_w", "macd_hist", "dist_sma50_pct", "dist_sma200_pct", "pctb", "atr_pct")},
        podobienstwo_do_dolka=f"{tp['bottom_score']}/7", podobienstwo_do_szczytu=f"{tp['top_score']}/7",
        ksiega_coinbase=dict(popyt_5pct_usd=ob.get("bid_depth_5pct_usd"), podaz_5pct_usd=ob.get("ask_depth_5pct_usd"),
                             przewaga_kupna_pct=ob.get("imbalance_5pct"),
                             sciana_kupna=(ob.get("bid_walls") or [{}])[0].get("price"), sciana_sprzedazy=(ob.get("ask_walls") or [{}])[0].get("price"))
        if ob else None,
        coingecko=dict(ranking=cg.get("rank"), sentyment_pozytywny_pct=cg.get("sentiment_up_pct"),
                       gieldy=[f"{e['exchange']} {e['share_pct']:.0f}%" for e in (cg.get("exchanges_volume") or [])[:3]]) if cg else None,
        setupy_pomocnicze=setups,
        plan_etapowy=plan_ai,
        wnioski_z_historii=H.CONCLUSIONS[1:3] + H.CONCLUSIONS[7:9],
        elliott_obecnie=H.ELLIOTT_INTERPRETATION[-1]["opis"]))


# ----------------------------------------------------------------- StochRSI 1h (alerty na życzenie użytkownika)
def _rsi(c, n):
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


def stoch_rsi(closes, rl=14, sl=14, k=3, d=3):
    r = _rsi(closes, rl)
    raw = [None] * len(closes)
    for i in range(len(closes)):
        w = [x for x in r[max(0, i - sl + 1):i + 1] if x is not None]
        if len(w) == sl:
            lo, hi = min(w), max(w)
            raw[i] = (r[i] - lo) / (hi - lo) * 100 if hi > lo else 0.0
    def sma(a, n, i):
        w = a[i - n + 1:i + 1] if i >= n - 1 else []
        return sum(w) / n if w and all(x is not None for x in w) else None
    K = [sma(raw, k, i) for i in range(len(closes))]
    D = [sma(K, d, i) for i in range(len(closes))]
    return K, D


IV_SEC = {"1h": 3600, "2h": 7200, "4h": 14400}


async def fetch_binance_1h(session, errors, symbol, interval="1h"):
    url = "https://data-api.binance.vision/api/v3/klines"
    d = await M.safe(M.get_json(session, url, {"symbol": symbol, "interval": interval, "limit": 1000}), f"Binance {symbol} {interval}", errors)
    if not d:
        return None
    return A.closed_only([dict(t=int(k[0]) // 1000, o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), v=float(k[5])) for k in d],
                         period=IV_SEC.get(interval, 3600))


def stoch_events(cfg, candles, src):
    """Sygnały naprzemienne: po „szczycie” (K w górę przez top_level) czekamy na „dołek” (K w dół do bottom_level) i odwrotnie.
    Odtwarzane od alternate_from z oczekiwaniem alternate_expect."""
    if not candles or len(candles) < 60:
        return []
    sec = IV_SEC.get(cfg.get("interval", "1h"), 3600)
    K, D = stoch_rsi([c["c"] for c in candles], cfg["rsi_len"], cfg["stoch_len"], cfg["k_smooth"], cfg["d_smooth"])
    R14 = _rsi([c["c"] for c in candles], 14)
    top, bot = cfg["top_level"], cfg["bottom_level"]
    t_from = P.iso_ts(cfg["alternate_from"]) if cfg.get("alternate_from") else candles[-3]["t"]
    expect = cfg.get("alternate_expect", "TOP")
    ev = []
    for i in range(1, len(candles)):
        if candles[i]["t"] + sec <= t_from or K[i] is None or K[i - 1] is None:
            continue
        c = candles[i]
        if expect == "BOTTOM" and K[i] <= bot < K[i - 1]:
            expect = "TOP"
            # poprzedni dołek K (<= 20) przed bieżącym spadkiem – do oceny dywergencji
            j = i - 1
            while j > 0 and K[j] is not None and K[j] > bot:
                j -= 1
            prev = None
            for m in range(j, max(1, j - 72), -1):
                if K[m] is not None and K[m] <= 20 and (K[m - 1] is None or K[m] <= K[m - 1]) and (m + 1 >= len(K) or K[m + 1] is None or K[m] <= K[m + 1]):
                    prev = m
                    break
            ev.append(dict(kind="STOCH_BOTTOM", t=c["t"] + sec, k=K[i], d=D[i], price=c["c"], low=c["l"], rsi=R14[i],
                           prev=(dict(t=candles[prev]["t"] + sec, k=K[prev], rsi=R14[prev],
                                      price=min(x["l"] for x in candles[max(0, prev - 1):prev + 2])) if prev is not None else None),
                           src=src, sec=sec))
            continue
        if expect == "TOP" and K[i] >= top > K[i - 1]:
            expect = "BOTTOM"
            # poprzedni szczyt K (>= 80) przed bieżącym wzrostem – do oceny dywergencji
            j = i - 1
            while j > 0 and K[j] is not None and K[j] < top:
                j -= 1
            prev = None
            for m in range(j, max(0, j - 72), -1):
                if K[m] is not None and K[m] >= 80 and (K[m - 1] is None or K[m] >= K[m - 1]) and (K[m + 1] is None or K[m] >= K[m + 1]):
                    prev = m
                    break
            ev.append(dict(kind="STOCH_TOP", t=c["t"] + sec, k=K[i], d=D[i], price=c["c"], high=c["h"],
                           rsi=R14[i],
                           prev=(dict(t=candles[prev]["t"] + sec, k=K[prev], rsi=R14[prev], price=max(x["h"] for x in candles[max(0, prev - 1):prev + 2])) if prev is not None else None),
                           src=src, sec=sec))
    return ev


def _r14(x):
    return f"{x:.0f}" if x is not None else "–"


def stoch_message(e, plan, btc_ctx=None, pst=None):
    """Krótki alert: StochRSI / RSI(14), bieżąca cena i cena przy poprzednim szczycie/dołku wskaźnika. Bez poziomów planu."""
    when = N.pl_time(e["t"])
    iv = plan['stochrsi'].get('interval', '1h')
    span = f"{N.pl_time(e['t'] - e.get('sec', 3600))}–{when[-5:]}"
    pv = e.get("prev")
    if e["kind"] == "STOCH_TOP":
        title = f"Sonic: StochRSI {iv} ≥ {plan['stochrsi']['top_level']} – możliwy szczyt"
        lines = [f"Teraz: StochRSI {e['k']:.0f} | RSI(14) {_r14(e.get('rsi'))} | cena {P.fmt(e['price'])} (szczyt świecy {P.fmt(e['high'])})",
                 f"Świeca {iv} {span}"]
        if pv:
            lines.append(f"Poprzedni szczyt: StochRSI {pv['k']:.0f} | RSI(14) {_r14(pv.get('rsi'))} | cena {P.fmt(pv['price'])} ({N.pl_time(pv['t'])})")
            if e["high"] > pv["price"] and e["k"] < pv["k"]:
                lines.append("Cena wyżej, wskaźnik niżej – DYWERGENCJA spadkowa.")
            elif e["high"] <= pv["price"]:
                lines.append("Cena niżej niż przy poprzednim szczycie – niższy szczyt.")
    else:
        title = f"Sonic: StochRSI {iv} ≤ {plan['stochrsi']['bottom_level']} – możliwy dołek"
        lines = [f"Teraz: StochRSI {e['k']:.0f} | RSI(14) {_r14(e.get('rsi'))} | cena {P.fmt(e['price'])} (dołek świecy {P.fmt(e['low'])})",
                 f"Świeca {iv} {span}"]
        if pv:
            lines.append(f"Poprzedni dołek: StochRSI {pv['k']:.0f} | RSI(14) {_r14(pv.get('rsi'))} | cena {P.fmt(pv['price'])} ({N.pl_time(pv['t'])})")
            r_now, r_prev = e.get("rsi"), pv.get("rsi")
            if e["low"] < pv["price"] and r_now is not None and r_prev is not None and r_now >= r_prev + 2:
                lines.append("Cena niżej, RSI wyżej – dywergencja wzrostowa.")
            elif e["low"] < pv["price"]:
                lines.append("Cena niżej niż przy poprzednim dołku – niższy dołek, bez dywergencji.")
            else:
                lines.append("Cena wyżej niż przy poprzednim dołku – wyższy dołek.")
    return title, "\n".join(lines)


# ----------------------------------------------------------------- kopia wyników w repozytorium
def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, float):
        return None if x != x else round(x, 8)
    if isinstance(x, (str, int, bool)) or x is None:
        return x
    return str(x)


def write_repo_file(rel, data):
    if DRY_RUN and os.getenv("REPO_COPY", "0") != "1":
        print(f"[DRY_RUN] pomijam zapis raport/{rel} (REPO_COPY=1, żeby zapisać)")
        return
    path = os.path.join(REPO_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(_jsonable(data), f, ensure_ascii=False, indent=1)
    print(f"[repo] zapisano raport/{rel}")


def repo_snapshot(ctx, pc, btc, sonic, plan_ai):
    """Te same dane co w index.html, w postaci do czytania (JSON)."""
    st = pc["state"]
    return dict(
        wygenerowano=ctx["generated_at"],
        uwaga="Kopia danych z raportu dziennego bota (piwecki.pl/analiza). Plan etapowy: data/sonic_plan.json.",
        plan=dict(faza=st["phase"], co_robic_teraz=pc["action"], cel_sprzedazy=st.get("target"), strefa_zakupu=st.get("zone"),
                  ostatni_zakup=st.get("last_fill"), polozenie=pc["position"],
                  potwierdzenia=[dict(warunek=c["name"], spelniony=c["ok"]) for c in pc["confirm"]["items"]],
                  sygnaly_od_startu=[dict(czas_pl=N.pl_time(e["t"]), typ=e["kind"], krok=e.get("step"), cena=e["price"]) for e in pc["events"]],
                  start_planu=pc["plan"]["start"], wersja_planu=pc["plan"]["meta"]["version"],
                  prognoza_kapitalu=dict(tylko_glowne=P.capital_path(pc["plan"], False), z_posrednimi=P.capital_path(pc["plan"], True),
                                         start=pc["plan"].get("capital"))),
        btc=dict(dane=ai_btc_data(btc), cena=btc["price"], zmiana_24h_pct=btc.get("change_24h_pct"),
                 poziomy=[dict(cena=l["price"], opis=l["label"], odl_pct=l["dist_pct"], w_strefie=l["inside"]) for l in pc["btc"]["levels"]],
                 scenariusze=pc["btc"]["scenarios"],
                 wskazniki=[dict(nazwa=m["name"], wartosc=m["value"], ocena=m["status"]) for m in btc.get("metrics", [])],
                 kontrakty=btc.get("derivatives")),
        sonic=dict(ai_sonic_data(sonic, plan_ai), zmiana_24h_pct=sonic.get("change_24h_pct"),
                   wsparcia_techniczne=[dict(cena=l["price"], zrodla=l["sources"][:3]) for l in sonic["levels"]["supports"][:5]],
                   opory_techniczne=[dict(cena=l["price"], zrodla=l["sources"][:3]) for l in sonic["levels"]["resistances"][:5]]),
        ai=dict(model=ctx["model"], komentarz=ctx.get("ai"), research_makro=ctx.get("research_macro"), research_sonic=ctx.get("research_sonic")),
        niedostepne_zrodla=sorted((ctx.get("errors") or {}).keys()),
    )


def append_signals_log(entries):
    path = os.path.join(REPO_DIR, "sygnaly.json")
    try:
        old = json.load(open(path, encoding="utf-8"))
    except Exception:
        old = []
    write_repo_file("sygnaly.json", (old + entries)[-500:])


# ----------------------------------------------------------------- główna pętla
def plan_for_ai(pc):
    st = pc["state"]
    return dict(faza=st["phase"], co_robic_teraz=pc["action"],
                cel_sprzedazy=(st.get("target") or {}).get("main"), strefa_zakupu=[(st.get("zone") or {}).get("lo"), (st.get("zone") or {}).get("hi")],
                uniewaznienie="wyłączone (18.11.2026) – przy głębszej korekcie plan obowiązuje",
                wsparcia_ponizej=[f"{z['id']} {z['lo']}–{z['hi']} ({z['label']}; {z['btc']})" for z in P.deep_zones(pc["plan"])],
                ostatnie_sygnaly=[f"{N.pl_time(e['t'])} {e['kind']} {e.get('step')}" for e in pc["events"][-4:]],
                uwaga="Plan etapowy jest nadrzędny. Komentarz ma się do niego odnosić, nie proponować innych poziomów wejścia/wyjścia.")


async def main():
    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())
    today = now.strftime("%Y-%m-%d")
    state = load_state()
    plan = P.load_plan()
    report_due = FORCE_REPORT or DRY_RUN or (state.get("last_report_date") != today and now.hour >= REPORT_HOUR_UTC)
    print(f"Start {now:%Y-%m-%d %H:%M} UTC | raport dzienny: {'TAK' if report_due else 'nie'} | "
          f"ostatni raport {state.get('last_report_date')} | plan {plan['meta']['version']}")
    errors = {}

    async with aiohttp.ClientSession() as session:
        sd = await fetch_sonic(session, errors, full=report_due)
        c1h, src1h = await fetch_sonic_1h(session, errors, P.iso_ts(plan["start"]["from"]))
        st_candles, st_src = None, None
        scfg = plan.get("stochrsi") or {}
        if scfg.get("enabled"):
            for sym in (scfg.get("symbol", "SUSDC"), scfg.get("fallback_symbol", "SUSDT")):
                st_candles = await fetch_binance_1h(session, errors, sym, scfg.get("interval", "1h"))
                if st_candles:
                    st_src = sym.replace("S", "S/", 1)
                    break
            if not st_candles:
                st_candles, st_src = c1h, "Coinbase S-USD"
        if len(sd["candles"]) < 250:
            print("KRYTYCZNE: za mało świec Sonica do analizy – przerywam bez publikacji")
            publish(state)
            sys.exit(1)

        # --- plan etapowy na świecach 1h
        rp = P.replay(plan, c1h, sd["candles"])
        pst = rp["state"]
        fresh_1h = bool(c1h) and c1h[-1]["t"] >= now_ts - 3 * 3600
        sent = set(state.get("ntfy_sent") or [])
        new = N.select_new(rp["events"], sent, now_ts, plan["signal_rules"]["send_window_hours"]) if fresh_1h else []
        print(f"[plan] faza {pst['phase']}, zdarzeń od startu planu: {len(rp['events'])}, nowych do wysłania: {len(new)}"
              + ("" if fresh_1h else " (świece 1h nieświeże – nie wysyłam)"))

        btc = None
        btc_now = await fetch_btc_price(session, errors) if (new or report_due) else None
        if report_due:
            btc = await fetch_btc(session, errors, full=True)
            if btc and not btc_now:
                btc_now = btc["price"]

    # --- ntfy: kluczowe sygnały planu
    btc_ctx = P.btc_position(plan, btc_now, (btc or {}).get("sma200")) if btc_now else None
    if btc_ctx:
        btc_ctx["price"] = btc_now
    log = []
    sent_cls = set(state.get("sent_cls") or [])
    plan_alerts = (plan.get("alerts") or {}).get("plan_levels", True)
    if not plan_alerts:
        for e in new:
            sent.add(N.key(e))      # alerty poziomów planu wyłączone – oznacz jako obsłużone, bez wysyłki
        if new:
            print(f"[plan] alerty poziomów wyłączone – pominięto {len(new)}")
        new = []
    for e in new:
        ck, lvl = P.event_class_key(plan, e)
        confirm = False
        if ck and lvl == "near" and (ck + ":near" in sent_cls or ck + ":hit" in sent_cls):
            sent.add(N.key(e))            # ostrzeżenie już poszło na żywo
            continue
        if ck and lvl == "hit" and ck + ":hit" in sent_cls:
            confirm = True                # alert na żywo już był – teraz tylko potwierdzenie z zamkniętej świecy
        ok, title, body = N.send_event(e, plan, btc_ctx, click=REPORT_URL, dry_run=DRY_RUN, confirm=confirm, state=pst)
        if ok:
            sent.add(N.key(e))
            if ck:
                sent_cls.add(f"{ck}:{lvl}")
            log.append(dict(wyslano=now.isoformat(), czas_swiecy_pl=N.pl_time(e["t"]), typ="CONFIRM" if confirm else e["kind"],
                            krok=e.get("step"), cena=e["price"], tytul=title, tresc=body))
            state["signals"] = (state.get("signals") or [])[-99:] + [dict(kind=e["kind"], step=e.get("step"), t=e["t"],
                                                                         price=e["price"], sent_at=now.isoformat())]
    state["sent_cls"] = sorted(sent_cls)[-400:]

    # --- StochRSI 1h (alerty informacyjne)
    scfg = plan.get("stochrsi") or {}
    if scfg.get("enabled") and st_candles:
        for e in stoch_events(scfg, st_candles, st_src):
            k = f"{e['kind']}:{e['t']}"
            if k in sent or e["t"] < now_ts - 3 * 3600:
                continue
            title, body = stoch_message(e, plan, btc_ctx, pst)
            if N.send(title, body, 4, ["chart_with_upwards_trend" if e["kind"] == "STOCH_TOP" else "chart_with_downwards_trend"], REPORT_URL, DRY_RUN):
                sent.add(k)
                log.append(dict(wyslano=now.isoformat(), czas_swiecy_pl=N.pl_time(e["t"]), typ=e["kind"], krok=None, cena=e["price"], tytul=title, tresc=body))
    # --- sygnały wahadłowe 2h (sprzedaż na szczycie odbicia / odkup na niższym dołku)
    swcfg = plan.get("swing") or {}
    if swcfg.get("enabled"):
        await asyncio.to_thread(run_swing, swcfg, sent, log, now_ts)
    state["ntfy_sent"] = sorted(sent, key=lambda k: int(k.rsplit(":", 1)[-1]))[-300:]
    state["plan_phase"] = pst["phase"]

    # --- raport dzienny
    html_doc = archive = None
    if report_due:
        if btc is None:
            print("[BTC] brak danych BTC – raport bez sekcji BTC nie zostanie opublikowany")
        else:
            sonic = A.analyze_sonic(sd["candles"], sd["ticker"], sd["stats"], sd["book"], sd["cg"], sd["c6h"])
            price = sonic["price"]
            pc = dict(plan=plan, state=pst, events=rp["events"], action=P.next_action(plan, pst),
                      position=P.position(plan, price, pst), confirm=P.confirmations(plan, sd["candles"], btc.get("chart")),
                      btc=P.btc_position(plan, btc["price"], btc.get("sma200")), path=P.projection_path(plan),
                      c1h_source=src1h, price=price)
            date_pl = now.strftime("%d.%m.%Y, %H:%M UTC")
            macro = await asyncio.to_thread(AI.research_macro, date_pl)
            senti = await asyncio.to_thread(AI.research_sonic_sentiment, date_pl)
            comment = await asyncio.to_thread(AI.commentary, ai_btc_data(btc), ai_sonic_data(sonic, plan_for_ai(pc)), macro, senti)
            ctx = dict(sonic=sonic, btc=btc, ai=comment, research_macro=macro, research_sonic=senti, plan=pc,
                       model=AI.GROQ_MODEL, generated_at=date_pl, errors=errors,
                       elliott=H.ELLIOTT_INTERPRETATION, conclusions=H.CONCLUSIONS)
            html_doc = R.render(ctx)
            archive = f"{today}.html"
            try:
                snap = repo_snapshot(ctx, pc, btc, sonic, plan_for_ai(pc))
                write_repo_file("stan.json", snap)
                write_repo_file(f"archiwum/{today}.json", snap)
                write_repo_file("index.html", html_doc)
            except Exception as ex:
                print(f"[repo] nie zapisano kopii raportu: {type(ex).__name__}: {ex}")
            state["last_report_date"] = today

    state["last_run"] = now.isoformat()
    state["last_errors"] = errors
    ok = publish(state, html_doc, archive)
    if html_doc and not ok:
        state["last_report_date"] = None   # spróbuj ponownie przy następnym uruchomieniu
        if log:
            append_signals_log(log)
        sys.exit(1)

    await run_monitor(plan, state, log)

    # --- czuwanie na żywo do końca godziny (sygnały w trakcie budowania świecy 1h)
    if plan_alerts and LIVE_UNTIL_MINUTE and fresh_1h and pst["phase"] in ("BUY", "HOLD"):
        hour0 = now_ts - now_ts % 3600
        deadline = hour0 + LIVE_UNTIL_MINUTE * 60
        if time.time() < deadline - 60:
            await live_loop(plan, pst, state, sent_cls, deadline, log, btc_ctx)
    if log:
        append_signals_log(log)
    print("Koniec cyklu.")


def run_swing(cfg, sent, log, now_ts):
    c = None
    for sym in (cfg.get("symbol", "SUSDC"), cfg.get("fallback_symbol", "SUSDT")):
        try:
            c = SW.fetch_1h(sym)
            if c and c[-1]["t"] >= now_ts - 3 * 3600:
                break
        except Exception as ex:
            print(f"[swing] {sym}: {type(ex).__name__}: {ex}")
            c = None
    if not c:
        print("[swing] brak świec – pomijam")
        return
    ev, st = SW.replay(cfg, c, P.iso_ts(cfg["start"]["from"]))
    print(f"[swing] stan {st['phase']}, odniesienie {P.fmt(st['ref'])}, trend {st['trend']}, tymczasowe dno "
          f"{P.fmt(st['tmp_bottom']) if st['tmp_bottom'] else '–'}, zdarzeń od startu: {len(ev)}")
    for e in ev:
        k = f"{e['kind']}:{e['t']}"
        if k in sent or e["t"] < now_ts - 3 * 3600:
            continue
        title, body = SW.message(e, cfg, N.pl_time)
        pr, tg = {"SWING_SELL": (5, ["moneybag"]), "SWING_BUY": (5, ["shopping_cart"])}.get(e["kind"], (3, ["rocket"]))
        if N.send(title, body, pr, tg, None, DRY_RUN):
            sent.add(k)
            log.append(dict(wyslano=datetime.now(timezone.utc).isoformat(), czas_swiecy_pl=N.pl_time(e["t"]), typ=e["kind"],
                            krok=None, cena=e["price"], tytul=title, tresc=body))


async def run_monitor(plan, state, log):
    """Monitoring BTC (prawdziwe/fałszywe odbicie) i NEAR – alerty ntfy, każdy tylko raz."""
    cfg = plan.get("monitor") or {}
    if not cfg.get("enabled"):
        return
    sent = set(state.get("mon_sent") or [])
    try:
        alerts = await asyncio.to_thread(MON.check, cfg, sent)
    except Exception as ex:
        print(f"[monitor] błąd: {type(ex).__name__}: {ex}")
        return
    for key, title, body, pr, tg in alerts:
        if N.send(title, body, pr, tg, REPORT_URL, DRY_RUN):
            sent.add(key)
            log.append(dict(wyslano=datetime.now(timezone.utc).isoformat(), czas_swiecy_pl="monitor", typ=key.split(":")[0],
                            krok=None, cena=None, tytul=title, tresc=body))
    if alerts:
        state["mon_sent"] = sorted(sent)[-300:]
        publish(state)


async def live_loop(plan, pst, state, sent_cls, deadline, log, btc_ctx):
    """Co ok. minutę sprawdza cenę Sonica (środek bid/ask z Coinbase) względem poziomów bieżącego kroku planu."""
    poll = plan["signal_rules"].get("live_poll_seconds", 60)
    watches = P.live_watches(plan, pst)
    print(f"[live] czuwam do {datetime.fromtimestamp(deadline, timezone.utc):%H:%M} UTC, poziomy: "
          + ", ".join(f"{w['kind']} {w['step']} @{P.fmt(w['level'])}" for w in watches))
    errs, last_btc, n = {}, 0.0, 0
    last_mon = 0.0
    async with aiohttp.ClientSession() as session:
        while time.time() < deadline:
            if time.time() - last_mon >= 900 and int(time.time()) % 900 >= 60:      # co 15 min, minutę po zamknięciu świecy 15m
                last_mon = time.time() - (int(time.time()) % 900)
                await run_monitor(plan, state, log)
            t = await M.safe(M.coinbase_ticker(session, "S-USD"), "Coinbase S-USD ticker (live)", errs)
            if t:
                price = (t["bid"] + t["ask"]) / 2 if t.get("bid") and t.get("ask") else t["price"]
                n += 1
                alerts = P.live_check(plan, pst, price, sent_cls)
                if alerts:
                    if time.time() - last_btc > 300:
                        bp = await fetch_btc_price(session, errs)
                        if bp:
                            btc_ctx = P.btc_position(plan, bp, (btc_ctx or {}).get("sma200"))
                            btc_ctx["price"] = bp
                            last_btc = time.time()
                    for key, lvl, w in alerts:
                        title, body, pr, tg = N.live_message(w, lvl, price, plan, btc_ctx, state=pst)
                        if N.send(title, body, pr, tg, REPORT_URL, DRY_RUN):
                            sent_cls.add(key)
                            log.append(dict(wyslano=datetime.now(timezone.utc).isoformat(), czas_swiecy_pl="na żywo",
                                            typ=f"LIVE_{w['kind']}_{lvl.upper()}", krok=w["step"], cena=price, tytul=title, tresc=body))
                    state["sent_cls"] = sorted(sent_cls)[-400:]
                    publish(state)        # zapisz od razu, żeby kolejne uruchomienie nie powtórzyło alertu
            await asyncio.sleep(poll)
    print(f"[live] koniec czuwania, sprawdzeń ceny: {n}")


RUN_MINUTES = int(os.getenv("RUN_MINUTES", "0"))   # >0: tryb ciągły – cykl co godzinę przez tyle minut (GitHub Actions max ok. 6 h)
REPO_PUSH = os.getenv("REPO_PUSH", "0") == "1"      # po każdym cyklu wypchnij folder raport/ do repozytorium


def push_repo():
    """Zapisuje zmiany w raport/ w repozytorium (tryb ciągły – żeby kopia była aktualna co godzinę)."""
    import subprocess
    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True)
    branch = os.getenv("GITHUB_REF_NAME", "main")
    git("add", "raport/")
    if git("diff", "--cached", "--quiet").returncode == 0:
        git("pull", "-q", "--rebase", "origin", branch)    # pobierz zmiany planu (data/sonic_plan.json) na następny cykl
        return
    git("-c", "user.name=handel-bot", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-q", "-m", f"Raport bota {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC [skip ci]")
    for _ in range(3):
        if git("pull", "-q", "--rebase", "origin", branch).returncode == 0 and \
                git("push", "-q", "origin", f"HEAD:{branch}").returncode == 0:
            print("[repo] raport/ wypchnięty")
            return
        time.sleep(5)
    print("[repo] nie udało się wypchnąć raport/ – spróbuję w następnym cyklu")


def run_forever():
    end = time.time() + RUN_MINUTES * 60
    n = 0
    while True:
        n += 1
        print(f"===== cykl {n} =====", flush=True)
        try:
            asyncio.run(main())
        except SystemExit as ex:
            print(f"[cykl] zakończony kodem {ex.code}")
        except Exception as ex:
            import traceback
            traceback.print_exc()
            print(f"[cykl] błąd: {type(ex).__name__}: {ex}")
        if REPO_PUSH:
            push_repo()
        nxt = (int(time.time()) // 3600 + 1) * 3600 + 60      # minuta po zamknięciu kolejnej świecy 1h
        if nxt + 600 > end:
            print(f"[tryb ciągły] koniec po {n} cyklach – następne uruchomienie przejmie czuwanie")
            break
        time.sleep(max(0, nxt - time.time()))


if __name__ == "__main__":
    if RUN_MINUTES:
        run_forever()
    else:
        asyncio.run(main())
