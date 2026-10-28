# -*- coding: utf-8 -*-
"""
Pobieranie danych rynkowych (wszystko darmowe, bez kluczy API).
Każda funkcja zwraca dane albo rzuca wyjątek – o tym, co się nie udało, decyduje wywołujący (log + pominięcie).
"""
import asyncio
from datetime import datetime, timezone, timedelta

import aiohttp

HEADERS = {"User-Agent": "Mozilla/5.0 (handel-bot; +https://piwecki.pl/analiza/)"}
TIMEOUT = aiohttp.ClientTimeout(total=20)
DAY = 86400


async def get_json(session, url, params=None):
    async with session.get(url, params=params, headers=HEADERS, timeout=TIMEOUT) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}: {(await r.text())[:120]}")
        return await r.json(content_type=None)


async def post_json(session, url, payload):
    async with session.post(url, json=payload, headers=HEADERS, timeout=TIMEOUT) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}: {(await r.text())[:120]}")
        return await r.json(content_type=None)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------- Coinbase (Sonic i BTC)
CB = "https://api.exchange.coinbase.com"


async def coinbase_candles(session, product, granularity=86400, start_ts=None, end_ts=None):
    """Świece Coinbase rosnąco. Bez start_ts: ostatnie 300. Ze start_ts: paginacja po 300 świec."""
    if start_ts is None:
        d = await get_json(session, f"{CB}/products/{product}/candles", {"granularity": granularity})
        rows = d
    else:
        end_ts = end_ts or int(datetime.now(timezone.utc).timestamp())
        rows, cur = [], start_ts
        while cur < end_ts:
            chunk_end = min(end_ts, cur + granularity * 299)
            d = await get_json(session, f"{CB}/products/{product}/candles",
                               {"granularity": granularity, "start": _iso(cur), "end": _iso(chunk_end)})
            rows.extend(d)
            cur = chunk_end + granularity
            await asyncio.sleep(0.25)
    seen, out = set(), []
    for x in rows:  # [time, low, high, open, close, volume]
        t = int(x[0])
        if t in seen:
            continue
        seen.add(t)
        out.append(dict(t=t, o=float(x[3]), h=float(x[2]), l=float(x[1]), c=float(x[4]), v=float(x[5]),
                        qv=float(x[5]) * float(x[4])))
    return sorted(out, key=lambda c: c["t"])


async def coinbase_ticker(session, product):
    d = await get_json(session, f"{CB}/products/{product}/ticker")
    return dict(price=float(d["price"]), volume_24h_base=float(d["volume"]), bid=float(d.get("bid") or 0), ask=float(d.get("ask") or 0))


async def coinbase_stats(session, product):
    d = await get_json(session, f"{CB}/products/{product}/stats")
    return {k: float(v) for k, v in d.items() if v not in (None, "")}


async def coinbase_book(session, product):
    """Pełna zagregowana księga zleceń (level 2): bids/asks = [[cena, ilość, liczba_zleceń], ...]."""
    d = await get_json(session, f"{CB}/products/{product}/book", {"level": 2})
    bids = [(float(p), float(s)) for p, s, *_ in d.get("bids", [])]
    asks = [(float(p), float(s)) for p, s, *_ in d.get("asks", [])]
    return dict(bids=bids, asks=asks, time=d.get("time"))


# ----------------------------------------------------------------- Ceny zapasowe
async def kraken_ticker(session, pair):
    d = await get_json(session, "https://api.kraken.com/0/public/Ticker", {"pair": pair})
    if d.get("error"):
        raise RuntimeError(d["error"])
    x = next(iter(d["result"].values()))
    return dict(price=float(x["c"][0]), volume_24h_base=float(x["v"][1]))


async def kraken_ohlc(session, pair, interval):
    d = await get_json(session, "https://api.kraken.com/0/public/OHLC", {"pair": pair, "interval": interval})
    if d.get("error"):
        raise RuntimeError(d["error"])
    key = next(k for k in d["result"] if k != "last")
    return [dict(t=int(x[0]), o=float(x[1]), h=float(x[2]), l=float(x[3]), c=float(x[4]), v=float(x[6]),
                 qv=float(x[6]) * float(x[5])) for x in d["result"][key]]


async def okx_ticker(session, inst):
    d = await get_json(session, "https://www.okx.com/api/v5/market/ticker", {"instId": inst})
    x = d["data"][0]
    return dict(price=float(x["last"]), volume_24h_base=float(x["vol24h"]))


# ----------------------------------------------------------------- BTC: dane on-chain i pochodne
async def coinmetrics_btc(session):
    """Zapas: MVRV z darmowego API Coin Metrics. Realized price = cena / MVRV
    (CapRealUSD nie jest dostępny w wersji darmowej)."""
    d = await get_json(session, "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
                       {"assets": "btc", "metrics": "PriceUSD,CapMVRVCur", "frequency": "1d",
                        "page_size": 8, "paging_from": "end"})
    rows = [r for r in (d.get("data") or []) if r.get("CapMVRVCur") and r.get("PriceUSD")]
    if not rows:
        raise RuntimeError("brak danych")
    last = rows[-1]
    mvrv = float(last["CapMVRVCur"])
    return dict(source="Coin Metrics (community)", date=last["time"][:10],
                realized_price=float(last["PriceUSD"]) / mvrv, mvrv=mvrv)


def _first_number(obj, prefer=()):
    """bitcoin-data.com zwraca słownik z datą i jedną wartością – wybierz liczbę (najpierw z preferowanych kluczy)."""
    if isinstance(obj, list):
        obj = obj[-1] if obj else {}
    for k in prefer:
        if k in obj:
            try:
                return float(obj[k])
            except (TypeError, ValueError):
                pass
    for k, v in obj.items():
        if any(s in k.lower() for s in ("date", "time", "ts", "d")) and len(k) <= 8:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    raise RuntimeError(f"nie rozpoznano formatu: {str(obj)[:80]}")


async def bitcoin_data_last(session, metric, prefer=()):
    """bitcoin-data.com (BGeometrics), darmowe: 8 zapytań/h, 15/dzień – używać tylko przy raporcie dziennym."""
    d = await get_json(session, f"https://bitcoin-data.com/v1/{metric}/last")
    return _first_number(d, prefer)


async def hyperliquid_btc(session):
    d = await post_json(session, "https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    meta, ctxs = d[0], d[1]
    idx = next(i for i, a in enumerate(meta["universe"]) if a["name"] == "BTC")
    x = ctxs[idx]
    mark = float(x["markPx"])
    return dict(source="Hyperliquid", oi_usd=float(x["openInterest"]) * mark,
                funding_8h_pct=float(x["funding"]) * 8 * 100,  # stawka godzinowa -> 8h, w %
                volume_24h_usd=float(x.get("dayNtlVlm") or 0))


async def okx_btc_derivs(session):
    oi = await get_json(session, "https://www.okx.com/api/v5/public/open-interest",
                        {"instType": "SWAP", "instId": "BTC-USDT-SWAP"})
    fr = await get_json(session, "https://www.okx.com/api/v5/public/funding-rate", {"instId": "BTC-USDT-SWAP"})
    o = oi["data"][0]
    oi_usd = float(o["oiUsd"]) if o.get("oiUsd") else None
    return dict(source="OKX", oi_usd=oi_usd, funding_8h_pct=float(fr["data"][0]["fundingRate"]) * 100)


async def okx_oi_history(session):
    """Historia open interest + wolumen (USD) wszystkich kontraktów BTC na OKX, interwał dzienny."""
    d = await get_json(session, "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume",
                       {"ccy": "BTC", "period": "1D"})
    rows = sorted(d["data"], key=lambda x: int(x[0]))
    return [(int(r[0]) // 1000, float(r[1]), float(r[2])) for r in rows]


async def deribit_btc(session):
    d = await get_json(session, "https://www.deribit.com/api/v2/public/get_book_summary_by_instrument",
                       {"instrument_name": "BTC-PERPETUAL"})
    x = d["result"][0]
    return dict(source="Deribit", oi_usd=float(x["open_interest"]),
                funding_8h_pct=float(x["funding_8h"]) * 100 if x.get("funding_8h") is not None else None)


async def fear_greed(session):
    d = await get_json(session, "https://api.alternative.me/fng/", {"limit": 8})
    rows = d["data"]
    return dict(value=int(rows[0]["value"]), label=rows[0]["value_classification"],
                week_ago=int(rows[-1]["value"]) if len(rows) >= 8 else None)


async def coingecko_global(session):
    d = await get_json(session, "https://api.coingecko.com/api/v3/global")
    g = d["data"]
    return dict(btc_dominance=float(g["market_cap_percentage"]["btc"]),
                total_mcap_usd=float(g["total_market_cap"]["usd"]),
                mcap_change_24h_pct=float(g.get("market_cap_change_percentage_24h_usd") or 0))


async def coingecko_sonic(session):
    d = await get_json(session, "https://api.coingecko.com/api/v3/coins/sonic-3",
                       {"localization": "false", "tickers": "true", "market_data": "true",
                        "community_data": "true", "developer_data": "false", "sparkline": "false"})
    md = d.get("market_data", {})
    vols = {}
    for t in d.get("tickers", []):
        name = (t.get("market") or {}).get("name") or "?"
        v = (t.get("converted_volume") or {}).get("usd") or 0
        vols[name] = vols.get(name, 0) + float(v)
    top = sorted(vols.items(), key=lambda kv: -kv[1])[:8]
    tot = sum(vols.values()) or 1
    return dict(
        rank=d.get("market_cap_rank"),
        market_cap_usd=(md.get("market_cap") or {}).get("usd"),
        fdv_usd=(md.get("fully_diluted_valuation") or {}).get("usd"),
        volume_24h_usd=(md.get("total_volume") or {}).get("usd"),
        change_7d_pct=md.get("price_change_percentage_7d"),
        change_30d_pct=md.get("price_change_percentage_30d"),
        circulating=md.get("circulating_supply"), total_supply=md.get("total_supply"),
        sentiment_up_pct=d.get("sentiment_votes_up_percentage"),
        watchlist_users=d.get("watchlist_portfolio_users"),
        exchanges_volume=[dict(exchange=k, volume_usd=v, share_pct=v / tot * 100) for k, v in top],
    )


async def safe(coro, label, errors):
    try:
        return await coro
    except Exception as e:
        errors[label] = f"{type(e).__name__}: {str(e)[:140]}"
        print(f"[dane] {label}: {errors[label]}")
        return None
