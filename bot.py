# -*- coding: utf-8 -*-
"""
handel-bot – monitor Sonic (S) + kontekst Bitcoina
==================================================
Uruchamiany co godzinę przez GitHub Actions (.github/workflows/bot.yml).

Co robi przy każdym uruchomieniu:
  1. Pobiera świece dzienne Sonica z Coinbase i dokleja je do historii z data/SONIC_FTM_S_1d.csv,
     oraz świece 1h Sonica od początku planu (data/sonic_plan.json).
  2. Odtwarza plan etapowy na zamkniętych świecach 1h (plan.py): zakup, sprzedaż, odkup, kupno na wybiciu,
     nieudany odkup, unieważnienie planu.
  3. Nowe kluczowe sygnały (z ostatnich godzin, jeszcze niewysłane) -> ntfy (notify.py).
  4. Raz dziennie (pierwsze uruchomienie po REPORT_HOUR_UTC) buduje pełny raport:
     dane BTC (on-chain, kontrakty, nastroje), księga zleceń Coinbase, wyszukiwanie w sieci i komentarz AI (Groq),
     a potem wysyła index.html na FTP (plus kopię w archiwum).
Stan (wysłane sygnały, data raportu) jest trzymany na FTP poza public_html, bo runner GitHub Actions jest za każdym razem czysty.
"""
import os, io, sys, json, html, ftplib, asyncio, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

import aiohttp

import market_data as M
import analysis as A
import ai as AI
import plan as P
import notify as N
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
    c1h = await M.safe(M.coinbase_candles(session, "S-USD", 3600, start_ts=from_ts - 6 * 3600), "Coinbase S-USD świece 1h", errors)
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


# ----------------------------------------------------------------- główna pętla
def plan_for_ai(pc):
    st = pc["state"]
    return dict(faza=st["phase"], co_robic_teraz=pc["action"],
                cel_sprzedazy=(st.get("target") or {}).get("main"), strefa_zakupu=[(st.get("zone") or {}).get("lo"), (st.get("zone") or {}).get("hi")],
                uniewaznienie_zamkniecie_dnia_ponizej=pc["plan"]["invalidation"]["daily_close_below"],
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
    for e in new:
        if N.send_event(e, plan, btc_ctx, click=REPORT_URL, dry_run=DRY_RUN):
            sent.add(N.key(e))
            state["signals"] = (state.get("signals") or [])[-99:] + [dict(kind=e["kind"], step=e.get("step"), t=e["t"],
                                                                         price=e["price"], sent_at=now.isoformat())]
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
            state["last_report_date"] = today

    state["last_run"] = now.isoformat()
    state["last_errors"] = errors
    ok = publish(state, html_doc, archive)
    if html_doc and not ok:
        state["last_report_date"] = None   # spróbuj ponownie przy następnym uruchomieniu
        sys.exit(1)
    print("Koniec cyklu.")


if __name__ == "__main__":
    asyncio.run(main())
