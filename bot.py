import os
import ftplib
import asyncio
import aiohttp
import numpy as np
from datetime import datetime, timezone
from google import genai
from google.genai.errors import APIError

# --- KONFIGURACJA ŚRODOWISKOWA / API ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

FTP_HOST = os.getenv("FTP_HOST", "piwecki.pl")
FTP_USER = os.getenv("FTP_USER", "")
FTP_PASS = os.getenv("FTP_PASS", "")


# --- 1. PEŁNY SILNIK ANALIZY TECHNICZNEJ I WZORCÓW HISTORYCZNYCH ---
class HistoricalPatternEngine:
    @staticmethod
    def calculate_rsi(prices, period=14):
        if len(prices) < period + 1:
            return 50.0
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        
        for i in range(period, len(prices) - 1):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(float(100 - (100 / (1 + rs))), 2)

    @staticmethod
    def calculate_ema(prices, period):
        if not prices:
            return 0.0
        if len(prices) < period:
            period = len(prices)
        if period <= 0:
            return float(prices[-1])
        prices = np.array(prices)
        weights = np.exp(np.linspace(-1., 0., period))
        weights /= weights.sum()
        ema = np.convolve(prices, weights, mode='full')[:len(prices)]
        idx = min(period, len(ema) - 1)
        if idx >= 0:
            ema[:period] = ema[idx]
        return round(float(ema[-1]), 5)

    @staticmethod
    def calculate_macd(prices, fast=12, slow=26, signal=9):
        if len(prices) < slow + signal:
            return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
        prices = np.array(prices)
        
        def ema_arr(data, p):
            alpha = 2 / (p + 1)
            res = [data[0]]
            for val in data[1:]:
                res.append(val * alpha + res[-1] * (1 - alpha))
            return np.array(res)

        ema_fast = ema_arr(prices, fast)
        ema_slow = ema_arr(prices, slow)
        macd_line = ema_fast - ema_slow
        signal_line = ema_arr(macd_line, signal)
        histogram = macd_line - signal_line
        
        return {
            "macd": round(float(macd_line[-1]), 5),
            "signal": round(float(signal_line[-1]), 5),
            "hist": round(float(histogram[-1]), 5)
        }

    @staticmethod
    def calculate_stoch_rsi(prices, rsi_period=14, stoch_period=14):
        rsi_series = []
        for i in range(rsi_period, len(prices) + 1):
            sub_prices = prices[:i]
            rsi_val = HistoricalPatternEngine.calculate_rsi(sub_prices, rsi_period)
            rsi_series.append(rsi_val)
            
        if len(rsi_series) < stoch_period:
            return {"k": 50.0}
            
        rsi_slice = rsi_series[-stoch_period:]
        min_rsi = min(rsi_slice)
        max_rsi = max(rsi_slice)
        
        if max_rsi == min_rsi:
            stoch_rsi = 100.0
        else:
            stoch_rsi = ((rsi_series[-1] - min_rsi) / (max_rsi - min_rsi)) * 100
            
        return {"k": round(float(stoch_rsi), 2)}

    @staticmethod
    def detect_multi_timeframe_divergence(prices):
        if len(prices) < 30:
            return "BRAK"
        p = np.array(prices)
        rsi_series = []
        for i in range(14, len(p) + 1):
            rsi_series.append(HistoricalPatternEngine.calculate_rsi(p[:i], 14))
        
        if len(rsi_series) < 15:
            return "BRAK"
            
        recent_prices = p[-15:]
        recent_rsi = np.array(rsi_series[-15:])
        
        p_min1_idx = np.argmin(recent_prices[:7])
        p_min2_idx = 7 + np.argmin(recent_prices[7:])
        if recent_prices[p_min2_idx] < recent_prices[p_min1_idx] and recent_rsi[p_min2_idx] > recent_rsi[p_min1_idx]:
            return "BYCZA_DYWERGENCJA"
            
        p_max1_idx = np.argmax(recent_prices[:7])
        p_max2_idx = 7 + np.argmax(recent_prices[7:])
        if recent_prices[p_max2_idx] > recent_prices[p_max1_idx] and recent_rsi[p_max2_idx] < recent_rsi[p_max1_idx]:
            return "NIEDŹWIEDZIA_DYWERGENCJA"
            
        return "BRAK"

    @classmethod
    def evaluate_asset_patterns(cls, timeframes_data):
        c_1m = timeframes_data.get("1M", [])
        c_1w = timeframes_data.get("1w", [])
        c_1d = timeframes_data.get("1d", [])
        c_4h = timeframes_data.get("4h", [])
        c_1h = timeframes_data.get("1h", [])
        
        if not c_1d or len(c_1d) < 20:
            return {"pattern_signal": "NEUTRAL", "regime": "UNKNOWN", "setup_progress": 0, "notes": "Niewystarczająca historia danych"}
            
        close_price = c_1d[-1]
        ema200_1d = cls.calculate_ema(c_1d, 200) if len(c_1d) >= 200 else cls.calculate_ema(c_1d, len(c_1d))
        regime = "HOSSA (Bullish Regime)" if close_price > ema200_1d else "BESSA (Bearish Regime)"
        
        macd_1m = cls.calculate_macd(c_1m) if len(c_1m) >= 35 else {"macd": 0.0, "signal": 0.0, "hist": 0.0}
        macd_1m_prev = cls.calculate_macd(c_1m[:-1]) if len(c_1m) >= 36 else {"macd": 0.0, "signal": 0.0, "hist": 0.0}
        monthly_macd_turning_up = macd_1m["hist"] > macd_1m_prev["hist"]
        
        div_1w = cls.detect_multi_timeframe_divergence(c_1w)
        div_1d = cls.detect_multi_timeframe_divergence(c_1d)
        div_4h = cls.detect_multi_timeframe_divergence(c_4h)
        
        rsi_1d = cls.calculate_rsi(c_1d)
        rsi_4h = cls.calculate_rsi(c_4h) if c_4h else 50.0
        
        cost_basis_status = "NEUTRAL_ZONE"
        if 80500 <= close_price <= 82500:
            cost_basis_status = "TEST_ETF_COST_BASIS (~81.5k)"
        elif 76500 <= close_price <= 78500:
            cost_basis_status = "TEST_TRUE_MARKET_MEAN (~77.5k)"

        progress = int(min(max((rsi_1d / 100.0) * 100, 0), 100))

        pattern_signal = "NEUTRAL"
        notes = f"Reżim: {regime}. Miesięczny MACD rośnie: {monthly_macd_turning_up}. RSI(1D): {rsi_1d}."
        
        if rsi_1d >= 70 or div_1d == "NIEDŹWIEDZIA_DYWERGENCJA":
            pattern_signal = "POTENTIAL_SHORT_TRAP"
            notes += " UWAGA: Skrajne wykupienie / Niedźwiedzia dywergencja – idealny kandydat pod Short (pułapka byków)."
        elif regime.startswith("HOSSA"):
            if monthly_macd_turning_up and (div_1w == "BYCZA_DYWERGENCJA" or cost_basis_status != "NEUTRAL_ZONE" or rsi_4h < 35):
                pattern_signal = "POTENTIAL_LONG_SETUP"
            else:
                pattern_signal = "POTENTIAL_LONG_SWING"
        else:
            pattern_signal = "POTENTIAL_SHORT_SETUP"

        return {
            "pattern_signal": pattern_signal,
            "regime": regime,
            "monthly_macd_turning_up": monthly_macd_turning_up,
            "cost_basis_status": cost_basis_status,
            "divergences": {"1w": div_1w, "1d": div_1d, "4h": div_4h},
            "setup_progress": progress,
            "notes": notes
        }


# --- 2. POBIERANIE ŚWIEC Z OKX, BINANCE, KRAKEN I BYBIT ---
async def fetch_okx_candles(session, symbol, bar):
    bar_map = {"1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W", "1M": "1M"}
    inst_id = f"{symbol}-USDT-SWAP"
    if symbol == "S":
        inst_id = "S-USDT-SWAP"
    url = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar_map.get(bar, '1H')}&limit=100"
    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("data"):
                    return [float(c[4]) for c in data["data"][::-1]]
    except Exception as e:
        print(f"OKX Fetch error ({symbol} {bar}): {e}")
    return None

async def fetch_binance_candles(session, symbol, bar):
    bar_map = {"1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w", "1M": "1M"}
    bn_symbol = f"{symbol}USDT"
    url = f"https://api.binance.com/api/v3/klines?symbol={bn_symbol}&interval={bar_map.get(bar, '1h')}&limit=100"
    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and len(data) > 0:
                    return [float(c[4]) for c in data]
    except Exception as e:
        print(f"Binance Fetch error ({symbol} {bar}): {e}")
    return None

async def fetch_kraken_candles(session, symbol, bar):
    bar_map = {"1h": 60, "4h": 240, "1d": 1440, "1w": 10080, "1M": 21600}
    pair = "XBTUSDT" if symbol == "BTC" else f"{symbol}USDT"
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={bar_map.get(bar, 60)}"
    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                result = data.get("result", {})
                for k, v in result.items():
                    if k != "last":
                        return [float(c[4]) for c in v[-100:]]
    except Exception as e:
        print(f"Kraken Fetch error ({symbol} {bar}): {e}")
    return None

async def fetch_bybit_candles(session, symbol, bar):
    bar_map = {"1h": "60", "4h": "240", "1d": "D", "1w": "W", "1M": "M"}
    by_symbol = f"{symbol}USDT"
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={by_symbol}&interval={bar_map.get(bar, '60')}&limit=100"
    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                list_data = data.get("result", {}).get("list", [])
                if list_data:
                    return [float(c[4]) for c in list_data[::-1]]
    except Exception as e:
        print(f"Bybit Fetch error ({symbol} {bar}): {e}")
    return None

async def fetch_aggregated_market_data(session, symbol, bar):
    tasks = [
        fetch_okx_candles(session, symbol, bar),
        fetch_binance_candles(session, symbol, bar),
        fetch_kraken_candles(session, symbol, bar),
        fetch_bybit_candles(session, symbol, bar)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    valid_indicators = []
    valid_closes_list = []
    
    for closes in results:
        if isinstance(closes, list) and len(closes) >= 15:
            valid_closes_list.append(closes)
            rsi = HistoricalPatternEngine.calculate_rsi(closes)
            ema20 = HistoricalPatternEngine.calculate_ema(closes, 20)
            ema50 = HistoricalPatternEngine.calculate_ema(closes, 50)
            ema200 = HistoricalPatternEngine.calculate_ema(closes, 200) if len(closes) >= 200 else ema50
            macd = HistoricalPatternEngine.calculate_macd(closes)
            stoch = HistoricalPatternEngine.calculate_stoch_rsi(closes)
            
            valid_indicators.append({
                "close": closes[-1],
                "rsi": rsi,
                "stoch_k": stoch["k"],
                "macd_line": macd["macd"],
                "macd_signal": macd["signal"],
                "macd_hist": macd["hist"],
                "ema20": ema20,
                "ema50": ema50,
                "ema200": ema200
            })

    if not valid_indicators:
        return {
            "close": 0.0, "rsi": 50.0, "stoch_k": 50.0, 
            "macd_line": 0.0, "macd_signal": 0.0, "macd_hist": 0.0,
            "ema20": 0.0, "ema50": 0.0, "ema200": 0.0, "raw_closes": []
        }

    decimals = 4 if symbol in ["STRK", "S", "SUI"] else 2
    best_raw_closes = max(valid_closes_list, key=len) if valid_closes_list else []

    return {
        "close": round(float(np.mean([d["close"] for d in valid_indicators])), decimals),
        "rsi": round(float(np.mean([d["rsi"] for d in valid_indicators])), 2),
        "stoch_k": round(float(np.mean([d["stoch_k"] for d in valid_indicators])), 2),
        "macd_line": round(float(np.mean([d["macd_line"] for d in valid_indicators])), 4),
        "macd_signal": round(float(np.mean([d["macd_signal"] for d in valid_indicators])), 4),
        "macd_hist": round(float(np.mean([d["macd_hist"] for d in valid_indicators])), 4),
        "ema20": round(float(np.mean([d["ema20"] for d in valid_indicators])), decimals),
        "ema50": round(float(np.mean([d["ema50"] for d in valid_indicators])), decimals),
        "ema200": round(float(np.mean([d["ema200"] for d in valid_indicators])), decimals),
        "raw_closes": best_raw_closes
    }


# --- 3. DYNAMICZNY SKAN TOP SELEKCJI OKX (EKSTREMA / SHORT TRAP) ---
async def fetch_okx_top_extremes(session):
    symbols = ["SOL", "SUI", "ETH", "AVAX", "LINK", "ADA", "DOGE", "NEAR", "APT", "STRK"]
    results = []
    for sym in symbols:
        closes = await fetch_okx_candles(session, sym, "1d")
        if closes and len(closes) >= 20:
            rsi = HistoricalPatternEngine.calculate_rsi(closes)
            results.append({
                "symbol": sym,
                "close": closes[-1],
                "rsi": rsi,
                "extreme_score": rsi
            })
    results = sorted(results, key=lambda x: x["extreme_score"], reverse=True)
    return results


# --- HELPER: KOSZYKOWA AGREGACJA PŁYNNOŚCI ZE WSZYSTKICH GIEŁD ---
def aggregate_baskets_to_zones(walls, current_price, is_btc=True):
    bin_size = 500.0 if is_btc else 0.0005
    token_name = "BTC" if is_btc else "STRK"
    
    asks_bins = {}
    bids_bins = {}
    
    for w in walls:
        p = w["price"]
        s = w["size"]
        if w["type"] == "Ask" and current_price <= p <= current_price * 1.10:
            b_key = int(p / bin_size) * bin_size
            if b_key not in asks_bins:
                asks_bins[b_key] = {"token": 0.0, "usd": 0.0}
            asks_bins[b_key]["token"] += s
            asks_bins[b_key]["usd"] += s * p
        elif w["type"] == "Bid" and current_price * 0.90 <= p <= current_price:
            b_key = int(p / bin_size) * bin_size
            if b_key not in bids_bins:
                bids_bins[b_key] = {"token": 0.0, "usd": 0.0}
            bids_bins[b_key]["token"] += s
            bids_bins[b_key]["usd"] += s * p
            
    best_ask_str = "Brak"
    best_bid_str = "Brak"
    
    if asks_bins:
        top_ask_key = max(asks_bins, key=lambda k: asks_bins[k]["token"])
        z_min = top_ask_key
        z_max = top_ask_key + bin_size
        tok_val = asks_bins[top_ask_key]["token"]
        usd_val = asks_bins[top_ask_key]["usd"]
        best_ask_str = f"${z_min:,.2f} - ${z_max:,.2f} | {tok_val:,.2f} {token_name} (~${usd_val/1e6:,.2f} mln USD)"
        
    if bids_bins:
        top_bid_key = max(bids_bins, key=lambda k: bids_bins[k]["token"])
        z_min = top_bid_key
        z_max = top_bid_key + bin_size
        tok_val = bids_bins[top_bid_key]["token"]
        usd_val = bids_bins[top_bid_key]["usd"]
        best_bid_str = f"${z_min:,.2f} - ${z_max:,.2f} | {tok_val:,.2f} {token_name} (~${usd_val/1e6:,.2f} mln USD)"
        
    return best_ask_str, best_bid_str


# --- SPECJALNA FUNKCJA: GŁĘBOKI SKAN PŁYNNOŚCI NEAR DO POZIOMU 1.5 ---
async def fetch_near_deep_liquidation_zones(session):
    """
    Pobiera głęboki arkusz zleceń dla NEAR aż do poziomu 1.5 USD,
    grupując zlecenia w koszyki (np. co 0.1 USD), aby wykryć luki płynnościowe 
    oraz kluczowe ściany / opory / wsparcia po drodze.
    """
    near_walls = []
    current_price = 4.392  # przybliżona cena bazowa
    try:
        async with session.get("https://api.binance.com/api/v3/ticker/price?symbol=NEARUSDT", timeout=5) as resp:
            if resp.status == 200:
                res = await resp.json()
                current_price = float(res.get("price", 4.392))
    except Exception:
        pass

    # Pobieranie orderbooka z Binance (maksymalny limit depth)
    try:
        async with session.get("https://api.binance.com/api/v3/depth?symbol=NEARUSDT&limit=5000", timeout=10) as resp:
            if resp.status == 200:
                res = await resp.json()
                for bid in res.get("bids", []):
                    p = float(bid[0])
                    s = float(bid[1])
                    if 1.5 <= p <= current_price:
                        near_walls.append({"price": p, "size": s})
    except Exception as e:
        print(f"NEAR Deep Depth Binance error: {e}")

    # Pobieranie z OKX
    try:
        async with session.get("https://www.okx.com/api/v5/market/books?instId=NEAR-USDT-SWAP&sz=400", timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                data = res.get("data", [])
                if data:
                    for bid in data[0].get("bids", []):
                        p = float(bid[0])
                        s = float(bid[1])
                        if 1.5 <= p <= current_price:
                            near_walls.append({"price": p, "size": s})
    except Exception as e:
        print(f"NEAR Deep Depth OKX error: {e}")

    # Agregacja do koszykówek co 0.1 USD aż do poziomu 1.5 USD
    bin_size = 0.1
    bins = {}
    for w in near_walls:
        p = w["price"]
        s = w["size"]
        b_key = round(int(p / bin_size) * bin_size, 2)
        if b_key not in bins:
            bins[b_key] = {"token": 0.0, "usd": 0.0}
        bins[b_key]["token"] += s
        bins[b_key]["usd"] += s * p

    # Analiza luk płynnościowych (poziomy, gdzie wolumen tokenów spada blisko zera)
    significant_walls = []
    liquidity_gaps = []
    
    sorted_bins = sorted(bins.keys(), reverse=True)
    if not sorted_bins:
        return "Brak danych głębokich dla NEAR w zakresie do $1.5."

    for b in sorted_bins:
        tok = bins[b]["token"]
        if tok > 10000:  # Próg istotnej ściany płynności / wsparcia
            significant_walls.append(f"${b:,.2f}: {tok:,.2f} NEAR (~${bins[b]['usd']/1e3:,.1f}k)")
        elif tok < 1000:  # Luka płynnościowa (brak zleceń obronnych)
            liquidity_gaps.append(f"${b:,.2f}")

    report_text = f"--- GŁĘBOKA ANALIZA PŁYNNOŚCI NEAR (od obecnej ceny do $1.5) ---\n"
    report_text += f"Znaczące ściany wsparcia po drodze:\n" + "\n".join(significant_walls[:10]) + "\n"
    if liquidity_gaps:
        report_text += f"Potencjalne Luki Płynnościowe (szybki zjazd ceny bez oporu):\n" + ", ".join(liquidity_gaps[:10]) + "\n"
    
    return report_text


# --- 4. OPEN INTEREST, FUNDING RATES & KOSZYKOWY SKAN PŁYNNOŚCI ---
async def fetch_derivatives_and_liquidations(session):
    derivatives_info = {
        "open_interest_okx": "Brak danych",
        "open_interest_bybit": "Brak danych",
        "open_interest_strk": "Brak danych",
        "funding_rate_okx": "0.01%",
        "funding_rate_bybit": "0.01%",
        "funding_rate_strk": "0.01%",
        "liquidations_bybit": "Brak danych (stabilny przepływ)",
        "liquidation_map_btc": "Brak danych w pełnym zakresie",
        "liquidation_map_strk": "Brak danych w pełnym zakresie",
        "near_deep_liquidation": "Brak danych",
        "realized_price": "US Spot ETF Cost Basis: ~81.5k USD | True Market Mean: ~77.5k USD | Short-Term Holder: ~74.6k USD"
    }
    
    try:
        url_oi_okx = "https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP"
        async with session.get(url_oi_okx, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("data"):
                    derivatives_info["open_interest_okx"] = f"{float(res['data'][0].get('oiCcy', 0)):,.2f} BTC"
    except Exception as e:
        print(f"OI OKX error: {e}")

    try:
        url_oi_bybit = "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=4h&limit=1"
        async with session.get(url_oi_bybit, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                list_oi = res.get("result", {}).get("list", [])
                if list_oi:
                    derivatives_info["open_interest_bybit"] = f"{float(list_oi[0].get('openInterest', 0)):,.2f} BTC"
    except Exception as e:
        print(f"OI Bybit error: {e}")

    try:
        url_oi_strk = "https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=STRK-USDT-SWAP"
        async with session.get(url_oi_strk, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("data"):
                    derivatives_info["open_interest_strk"] = f"{float(res['data'][0].get('oiCcy', 0)):,.2f} STRK"
    except Exception as e:
        print(f"OI STRK OKX error: {e}")

    try:
        url_fr_okx = "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP"
        async with session.get(url_fr_okx, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("data"):
                    fr = float(res['data'][0].get('fundingRate', 0)) * 100
                    derivatives_info["funding_rate_okx"] = f"{fr:.4f}%"
    except Exception as e:
        print(f"Funding Rate OKX error: {e}")

    try:
        url_fr_bybit = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"
        async with session.get(url_fr_bybit, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                list_fr = res.get("result", {}).get("list", [])
                if list_fr:
                    fr = float(list_fr[0].get('fundingRate', 0)) * 100
                    derivatives_info["funding_rate_bybit"] = f"{fr:.4f}%"
    except Exception as e:
        print(f"Funding Rate Bybit error: {e}")

    try:
        url_fr_strk = "https://www.okx.com/api/v5/public/funding-rate?instId=STRK-USDT-SWAP"
        async with session.get(url_fr_strk, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("data"):
                    fr = float(res['data'][0].get('fundingRate', 0)) * 100
                    derivatives_info["funding_rate_strk"] = f"{fr:.4f}%"
    except Exception as e:
        print(f"Funding Rate STRK OKX error: {e}")

    try:
        url_liq = "https://api.bybit.com/v5/market/recent-trade?category=linear&symbol=BTCUSDT&limit=50"
        async with session.get(url_liq, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                trades = res.get("result", {}).get("list", [])
                large_sells = sum(1 for t in trades if t.get("side") == "Sell" and float(t.get("size", 0)) > 2.0)
                large_buys = sum(1 for t in trades if t.get("side") == "Buy" and float(t.get("size", 0)) > 2.0)
                derivatives_info["liquidations_bybit"] = f"Presja Buy: {large_buys} | Presja Sell: {large_sells} dużych transakcji"
    except Exception as e:
        print(f"Liquidations Bybit error: {e}")

    btc_current_price = 82800.0
    try:
        async with session.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5) as resp:
            if resp.status == 200:
                res = await resp.json()
                btc_current_price = float(res.get("price", 82800.0))
    except Exception:
        pass

    btc_walls = []
    try:
        async with session.get("https://www.okx.com/api/v5/market/books?instId=BTC-USDT-SWAP&sz=400", timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                data = res.get("data", [])
                if data:
                    for ask in data[0].get("asks", []):
                        btc_walls.append({"type": "Ask", "price": float(ask[0]), "size": float(ask[1]), "ex": "OKX"})
                    for bid in data[0].get("bids", []):
                        btc_walls.append({"type": "Bid", "price": float(bid[0]), "size": float(bid[1]), "ex": "OKX"})
    except Exception as e:
        print(f"BTC OKX Book error: {e}")

    try:
        async with session.get("https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=500", timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                for ask in res.get("asks", []):
                    btc_walls.append({"type": "Ask", "price": float(ask[0]), "size": float(ask[1]), "ex": "Binance"})
                for bid in res.get("bids", []):
                    btc_walls.append({"type": "Bid", "price": float(bid[0]), "size": float(bid[1]), "ex": "Binance"})
    except Exception as e:
        print(f"BTC Binance Book error: {e}")

    try:
        async with session.get("https://api.bybit.com/v5/market/orderbook?category=linear&symbol=BTCUSDT&limit=200", timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                result = res.get("result", {})
                for ask in result.get("a", []):
                    btc_walls.append({"type": "Ask", "price": float(ask[0]), "size": float(ask[1]), "ex": "Bybit"})
                for bid in result.get("b", []):
                    btc_walls.append({"type": "Bid", "price": float(bid[0]), "size": float(bid[1]), "ex": "Bybit"})
    except Exception as e:
        print(f"BTC Bybit Book error: {e}")

    try:
        async with session.get("https://api.kraken.com/0/public/Depth?pair=XBTUSDT&count=100", timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                result = res.get("result", {}).get("XXBTZUSDT", {}) or res.get("result", {}).get("XBTUSDT", {})
                for ask in result.get("asks", []):
                    btc_walls.append({"type": "Ask", "price": float(ask[0]), "size": float(ask[1]), "ex": "Kraken"})
                for bid in result.get("bids", []):
                    btc_walls.append({"type": "Bid", "price": float(bid[0]), "size": float(bid[1]), "ex": "Kraken"})
    except Exception as e:
        print(f"BTC Kraken Book error: {e}")

    if btc_walls:
        ask_zone, bid_zone = aggregate_baskets_to_zones(btc_walls, btc_current_price, is_btc=True)
        derivatives_info["liquidation_map_btc"] = f"Strefa Oporu (Ask): {ask_zone} | Strefa Wsparcia (Bid): {bid_zone}"

    strk_current_price = 0.058
    try:
        async with session.get("https://api.binance.com/api/v3/ticker/price?symbol=STRKUSDT", timeout=5) as resp:
            if resp.status == 200:
                res = await resp.json()
                strk_current_price = float(res.get("price", 0.058))
    except Exception:
        pass

    strk_walls = []
    try:
        async with session.get("https://www.okx.com/api/v5/market/books?instId=STRK-USDT-SWAP&sz=400", timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                data = res.get("data", [])
                if data:
                    for ask in data[0].get("asks", []):
                        strk_walls.append({"type": "Ask", "price": float(ask[0]), "size": float(ask[1]), "ex": "OKX"})
                    for bid in data[0].get("bids", []):
                        strk_walls.append({"type": "Bid", "price": float(bid[0]), "size": float(bid[1]), "ex": "OKX"})
    except Exception as e:
        print(f"OKX Book scan error: {e}")

    try:
        async with session.get("https://api.binance.com/api/v3/depth?symbol=STRKUSDT&limit=500", timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                for ask in res.get("asks", []):
                    strk_walls.append({"type": "Ask", "price": float(ask[0]), "size": float(ask[1]), "ex": "Binance"})
                for bid in res.get("bids", []):
                    strk_walls.append({"type": "Bid", "price": float(bid[0]), "size": float(bid[1]), "ex": "Binance"})
    except Exception as e:
        print(f"Binance Book scan error: {e}")

    if strk_walls:
        ask_zone_s, bid_zone_s = aggregate_baskets_to_zones(strk_walls, strk_current_price, is_btc=False)
        derivatives_info["liquidation_map_strk"] = f"Strefa Ask: {ask_zone_s} | Strefa Bid: {bid_zone_s}"
    else:
        derivatives_info["liquidation_map_strk"] = "Brak danych w pełnym zakresie"

    # Pobranie głębokiego skanu NEAR do 1.5 USD
    derivatives_info["near_deep_liquidation"] = await fetch_near_deep_liquidation_zones(session)

    return derivatives_info


# --- 5. ROZBITE, LEKKIE ZAPYTANIA DO AI (CZĘŚĆ 1 ORAZ CZĘŚĆ 2) ---
async def generate_ai_part1(tech_summary, deriv_data, pattern_summary):
    if not GEMINI_API_KEY:
        return "Brak klucza API Gemini."
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
Jesteś profesjonalnym analitykiem rynkowym. Przygotuj Część 1 raportu (Kondycja Rynku, BTC, STRK, S, oraz Głęboka Płynność NEAR do 1.5 USD) na podstawie poniższych danych.
BEZWZGLĘDNE ZASADY:
1. Brak wstępów.
2. Zakaz podawania dokładnych wartości liczb wskaźników (RSI, MACD) w tekście.
3. Skup się na strukturze, reżimie, strefach płynności oraz sprawdź czy NEAR ma luki płynnościowe w drodze do 1.5.

STAN TECHNICZNY I WZORCE:
{pattern_summary}

ZAGREGOWANE STREFY PŁYNNOŚCI I POCHODNE:
- BTC Płynność: {deriv_data['liquidation_map_btc']}
- STRK Płynność: {deriv_data['liquidation_map_strk']}
- NEAR Płynność i Luki do $1.5: {deriv_data['near_deep_liquidation']}
- OI BTC: {deriv_data['open_interest_okx']} | OI STRK: {deriv_data['open_interest_strk']}
- Funding Rates: BTC {deriv_data['funding_rate_okx']} | STRK {deriv_data['funding_rate_strk']}
- Baza Kosztowa: {deriv_data['realized_price']}
"""
    candidate_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    for model in candidate_models:
        for attempt in range(1, 3):
            try:
                response = client.models.generate_content(model=model, contents=prompt)
                if response and response.text:
                    return response.text.strip()
            except Exception as e:
                print(f"Ostrzeżenie API Część 1 ({model}): {e}")
                await asyncio.sleep(3)
    return "Analiza makro niedostępna (chwilowy limit API)."


async def generate_ai_part2(okx_extremes):
    if not GEMINI_API_KEY:
        return "Brak klucza API Gemini."
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
Przygotuj Część 2 raportu: Top Selekcja Short Trap (wykupione projekty z OKX z potencjałem gwałtownego spadku).
Zasady: Krótko, profesjonalnie, bez podawania dokładnych wartości wskaźników w tekście.

DANE EKSTREMÓW OKX (SHORT TRAP TARGETS):
{okx_extremes}
"""
    candidate_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    for model in candidate_models:
        for attempt in range(1, 3):
            try:
                response = client.models.generate_content(model=model, contents=prompt)
                if response and response.text:
                    return response.text.strip()
            except Exception as e:
                print(f"Ostrzeżenie API Część 2 ({model}): {e}")
                await asyncio.sleep(3)
    return "Top Selekcja Short Trap: Brak danych z API."


# --- 6. WYSYŁKA NA TELEGRAM (Z PODZIAŁEM NA CHUNKI > 4096 ZNAKÓW) ---
async def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Brak danych konfiguracyjnych Telegrama.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    max_len = 4000
    chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    
    async with aiohttp.ClientSession() as session:
        for chunk in chunks:
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk}
            async with session.post(url, json=payload) as resp:
                res_text = await resp.text()
                print(f"Odpowiedź Telegram API: {res_text}")
            await asyncio.sleep(1)


# --- 7. WYSYŁKA HTML NA FTP ---
def upload_html_to_ftp(html_content):
    if not FTP_USER or not FTP_PASS:
        print("Brak danych logowania do FTP.")
        return
    try:
        with ftplib.FTP(FTP_HOST, FTP_USER, FTP_PASS) as ftp:
            try:
                ftp.cwd("domains/piwecki.pl/public_html/analiza")
            except ftplib.error_perm:
                try:
                    ftp.cwd("domains/piwecki.pl/public_html")
                    ftp.mkd("analiza")
                    ftp.cwd("analiza")
                except Exception as ex:
                    print(f"FTP Subdirectory creation error: {ex}")
            
            with open("index.html", "w", encoding="utf-8") as f:
                f.write(html_content)
                
            with open("index.html", "rb") as f:
                ftp.storbinary("STOR index.html", f)
            print("Pomyślnie wysłano czyste repozytorium danych na FTP (piwecki.pl/analiza).")
    except Exception as e:
        print(f"Błąd przesyłania FTP: {e}")


# --- 8. GŁÓWNA PĘTLA PROGRAMU ---
async def main():
    async with aiohttp.ClientSession() as session:
        intervals = ["1h", "4h", "1d", "1w", "1M"]
        symbols = ["BTC", "STRK", "S", "SUI"]
        
        market_data = {}
        pattern_evaluations = {}

        for sym in symbols:
            market_data[sym] = {}
            timeframes_raw = {}
            for bar in intervals:
                d = await fetch_aggregated_market_data(session, sym, bar)
                market_data[sym][bar] = d
                timeframes_raw[bar] = d.get("raw_closes", [])
            
            pattern_evaluations[sym] = HistoricalPatternEngine.evaluate_asset_patterns(timeframes_raw)
            
        deriv_data = await fetch_derivatives_and_liquidations(session)
        okx_extremes = await fetch_okx_top_extremes(session)
        
        tech_summary = ""
        pattern_summary = ""
        
        for sym in symbols:
            tech_summary += f"\n{sym} Wyższe Interwały (OKX/Binance/Kraken/Bybit):\n"
            for bar in ["1h", "4h", "1d", "1w", "1M"]:
                d = market_data[sym][bar]
                tech_summary += f"- {bar}: Cena: ${d['close']}, RSI: {d['rsi']}, StochRSI_K: {d['stoch_k']}, MACD Line: {d['macd_line']}, Signal: {d['macd_signal']}, Hist: {d['macd_hist']}, EMA20: ${d['ema20']}, EMA50: ${d['ema50']}, EMA200: ${d['ema200']}\n"
            
            pat = pattern_evaluations[sym]
            pattern_summary += f"- {sym}: Reżim: {pat.get('regime')}, Sygnał: {pat.get('pattern_signal')}, Wskaźnik Wykupienia: {pat.get('setup_progress')}%, Notatki: {pat.get('notes')}\n"

        okx_summary = "Skanowanie OKX Ekstremów / Wykupień 1D (Short Trap):\n"
        for item in okx_extremes[:4]:
            okx_summary += f"- {item['symbol']}: Cena: ${item['close']}, RSI(1D): {item['rsi']}\n"

        # Generowanie raportu tekstowego do wysyłki na Telegram
        part1 = await generate_ai_part1(tech_summary, deriv_data, pattern_summary)
        part2 = await generate_ai_part2(okx_summary)
        full_report = f"📊 **RAPORT RYNKOWY SHORT TRAP**\n\n{part1}\n\n{part2}\n\n🔍 **Głęboka Płynność NEAR do $1.5:**\n{deriv_data['near_deep_liquidation']}"

        await send_telegram(full_report)

        # Generowanie HTML do publikacji na FTP
        html_boxes = ""
        for sym in symbols:
            rows = ""
            for bar in ["1h", "4h", "1d", "1w", "1M"]:
                d = market_data[sym][bar]
                rows += f"""
                <tr>
                    <td><b>{bar}</b></td>
                    <td>${d['close']}</td>
                    <td>{d['rsi']}</td>
                    <td>{d['stoch_k']}</td>
                    <td>{d['macd_line']} / {d['macd_signal']} ({d['macd_hist']})</td>
                    <td>${d['ema20']}</td>
                    <td>${d['ema50']}</td>
                    <td>${d['ema200']}</td>
                </tr>
                """
            pat = pattern_evaluations[sym]
            html_boxes += f"""
            <div class="tech-card">
                <h3>{sym} — Sygnał: <span class="badge">{pat.get('pattern_signal')}</span> | Wykupienie: <span class="badge" style="color: #ff5252;">{pat.get('setup_progress')}%</span></h3>
                <p class="notes">{pat.get('notes')}</p>
                <table>
                    <thead>
                        <tr>
                            <th>Interwał</th><th>Cena Zamknięcia</th><th>RSI</th><th>StochRSI K</th><th>MACD</th><th>EMA 20</th><th>EMA 50</th><th>EMA 200</th>
                        </tr>
                    </thead>
                    <tbody>{rows}</tbody>
                </table>
            </div>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Market Maker Monitor — Analiza Głęboka Płynności NEAR</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f1115; color: #e1e1e6; margin: 0; padding: 25px; line-height: 1.6; }}
        .container {{ max-width: 1100px; margin: 0 auto; background: #16181d; padding: 30px; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.7); }}
        h1 {{ color: #ff5252; border-bottom: 2px solid #232730; padding-bottom: 12px; margin-top: 0; }}
        h2 {{ color: #40c4ff; margin-top: 35px; border-bottom: 1px solid #232730; padding-bottom: 8px; }}
        .tech-card {{ background: #1a1d24; padding: 20px; border-radius: 8px; margin-top: 20px; border-left: 5px solid #ff5252; }}
        .notes {{ font-size: 0.9em; color: #b0b5c0; margin: 5px 0 15px 0; }}
        .badge {{ background: #232730; color: #ff5252; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.88em; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #2a2e39; }}
        th {{ background-color: #0f1115; color: #8892b0; font-weight: 600; }}
        tr:hover {{ background-color: #232730; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Market Maker Monitor — Głęboka Płynność i Luki NEAR</h1>
        <p>Data aktualizacji: <b>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</b></p>
        
        <h2>1. Analiza Głęboka Płynności NEAR (do $1.5)</h2>
        <div class="tech-card" style="border-left-color: #40c4ff;">
            <pre style="white-space: pre-wrap; font-family: inherit;">{deriv_data['near_deep_liquidation']}</pre>
        </div>

        <h2>2. Wskaźniki Techniczne i Reżim Rynkowy</h2>
        {html_boxes}
    </div>
</body>
</html>"""

        upload_html_to_ftp(html_content)

if __name__ == "__main__":
    asyncio.run(main())
