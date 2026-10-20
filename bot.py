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
            return {"pattern_signal": "NEUTRAL", "regime": "UNKNOWN", "notes": "Niewystarczająca historia danych"}
            
        close_price = c_1d[-1]
        ema200_1d = cls.calculate_ema(c_1d, 200) if len(c_1d) >= 200 else cls.calculate_ema(c_1d, len(c_1d))
        regime = "HOSSA (Bullish Regime)" if close_price > ema200_1d else "BESSA (Bearish Regime)"
        
        macd_1m = cls.calculate_macd(c_1m) if len(c_1m) >= 35 else {"macd": 0.0, "signal": 0.0, "hist": 0.0}
        macd_1m_prev = cls.calculate_macd(c_1m[:-1]) if len(c_1m) >= 36 else {"macd": 0.0, "signal": 0.0, "hist": 0.0}
        monthly_macd_turning_up = macd_1m["hist"] > macd_1m_prev["hist"]
        
        div_1w = cls.detect_multi_timeframe_divergence(c_1w)
        div_1d = cls.detect_multi_timeframe_divergence(c_1d)
        div_4h = cls.detect_multi_timeframe_divergence(c_4h)
        
        rsi_4h = cls.calculate_rsi(c_4h) if c_4h else 50.0
        
        cost_basis_status = "NEUTRAL_ZONE"
        if 80500 <= close_price <= 82500:
            cost_basis_status = "TEST_ETF_COST_BASIS (~81.5k)"
        elif 76500 <= close_price <= 78500:
            cost_basis_status = "TEST_TRUE_MARKET_MEAN (~77.5k)"

        pattern_signal = "NEUTRAL"
        notes = f"Reżim: {regime}. Miesięczny MACD rośnie: {monthly_macd_turning_up}. Cost Basis Zone: {cost_basis_status}."
        
        if regime.startswith("HOSSA"):
            if monthly_macd_turning_up and (div_1w == "BYCZA_DYWERGENCJA" or cost_basis_status != "NEUTRAL_ZONE" or rsi_4h < 35):
                pattern_signal = "POTENTIAL_LONG_SETUP"
            else:
                pattern_signal = "POTENTIAL_LONG_SWING"
        else:
            if monthly_macd_turning_up:
                pattern_signal = "POTENTIAL_MACRO_BOTTOM_WATCH"
            else:
                pattern_signal = "POTENTIAL_SHORT_SETUP"

        return {
            "pattern_signal": pattern_signal,
            "regime": regime,
            "monthly_macd_turning_up": monthly_macd_turning_up,
            "cost_basis_status": cost_basis_status,
            "divergences": {"1w": div_1w, "1d": div_1d, "4h": div_4h},
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


# --- 3. DYNAMICZNY SKAN TOP SELEKCJI OKX (EKSTREMA) ---
async def fetch_okx_top_extremes(session):
    symbols = ["SOL", "SUI", "ETH", "AVAX", "LINK", "ADA", "DOGE", "NEAR", "APT"]
    results = []
    for sym in symbols:
        closes = await fetch_okx_candles(session, sym, "1d")
        if closes and len(closes) >= 20:
            rsi = HistoricalPatternEngine.calculate_rsi(closes)
            results.append({
                "symbol": sym,
                "close": closes[-1],
                "rsi": rsi,
                "extreme_score": abs(rsi - 50)
            })
    results = sorted(results, key=lambda x: x["extreme_score"], reverse=True)
    return results


# --- 4. OPEN INTEREST, FUNDING RATES & MAPA LIKWIDACJI (DLA BTC I STRK) ---
async def fetch_derivatives_and_liquidations(session):
    derivatives_info = {
        "open_interest_okx": "Brak danych",
        "open_interest_bybit": "Brak danych",
        "open_interest_strk": "Brak danych",
        "funding_rate_okx": "0.01%",
        "funding_rate_bybit": "0.01%",
        "funding_rate_strk": "0.01%",
        "liquidations_bybit": "Brak danych (stabilny przepływ)",
        "liquidation_map_strk": "Brak danych (obszar nagromadzenia $0.060 - $0.061)",
        "realized_price": "US Spot ETF Cost Basis: ~81.5k USD | True Market Mean: ~77.5k USD | Short-Term Holder: ~74.6k USD"
    }
    
    # 1. Open Interest BTC OKX
    try:
        url_oi_okx = "https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP"
        async with session.get(url_oi_okx, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("data"):
                    derivatives_info["open_interest_okx"] = f"{float(res['data'][0].get('oiCcy', 0)):,.2f} BTC"
    except Exception as e:
        print(f"OI OKX error: {e}")

    # 2. Open Interest BTC Bybit
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

    # 3. Open Interest STRK OKX (Dedykowany dla pozycji użytkownika)
    try:
        url_oi_strk = "https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=STRK-USDT-SWAP"
        async with session.get(url_oi_strk, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("data"):
                    derivatives_info["open_interest_strk"] = f"{float(res['data'][0].get('oiCcy', 0)):,.2f} STRK"
    except Exception as e:
        print(f"OI STRK OKX error: {e}")

    # 4. Funding Rate BTC OKX
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

    # 5. Funding Rate BTC Bybit
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

    # 6. Funding Rate STRK OKX
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

    # 7. Presja likwidacyjna / transakcje Bybit BTC
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

    # 8. Skanowanie arkusza i mapy likwidacji STRK (szukanie ściany $0.060 - $0.061)
    try:
        url_book_strk = "https://www.okx.com/api/v5/market/books?instId=STRK-USDT-SWAP&sz=50"
        async with session.get(url_book_strk, timeout=8) as resp:
            if resp.status == 200:
                res = await resp.json()
                data = res.get("data", [])
                if data:
                    asks = data[0].get("asks", [])
                    # Sprawdzamy nagromadzenie zleceń w przedziale $0.060 - $0.061
                    wall_volume = 0
                    for ask in asks:
                        price = float(ask[0])
                        size = float(ask[1])
                        if 0.060 <= price <= 0.061:
                            wall_volume += size
                    derivatives_info["liquidation_map_strk"] = f"Nagromadzenie Ask ($0.060-$0.061): {wall_volume:,.0f} STRK (Cel i Opór strukturalny)"
    except Exception as e:
        print(f"STRK Orderbook/Liquidation Map error: {e}")

    return derivatives_info


# --- 5. GENEROWANIE RAPORTU PRZEZ GEMINI DLA TELEGRAMA Z ROTACJĄ MODELI ---
async def generate_ai_analysis(tech_summary, deriv_data, pattern_summary, okx_extremes, max_retries=3, initial_delay=5):
    if not GEMINI_API_KEY:
        print("Brak klucza GEMINI_API_KEY.")
        return "Brak skonfigurowanego klucza API Gemini.", "Brak danych."

    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
Jesteś profesjonalnym analitykiem rynkowym i systemem Market Maker Monitor.
Przygotuj profesjonalny raport handlowy uwzględniający wyższe interwały (1h, 4h, 1D, 1W, 1M) z uśrednionych danych z giełd (OKX, Binance, Kraken, Bybit), wyliczone wzorce historyczne oraz dane o instrumentach pochodnych i likwidacjach.

BEZWZGLĘDNE ZASADY FORMATOWANIA TELEGRAMA:
1. ŻADNEGO WSTĘPU. Raport rozpoczyna się od analizy BTC.
2. BEZWZGLĘDNY ZAKAZ PODAWANIA LICZB/WARTOŚCI WSKAŹNIKÓW w tekście (brak podawania konkretnych liczb RSI, MACD, StochRSI). Użytkownik odczytuje wartości sam z pliku HTML.
3. AKTYWA TARGETOWE: BTC, STRK (Starknet) oraz S (Sonic / S/USDC). Uwaga: 'S' oznacza Sonic, nie pomył z Solaną (SOL).
4. Przekaz ma być skondensowany, skupiony wyłącznie na strukturze, reżimie rynkowym, płynności oraz poziomach Long/Short z wielodniowym horyzontem trzymania pozycji (swing).

PRZETORZONE SYGNAŁY WZORCÓW HISTORYCZNYCH:
{pattern_summary}

DANE TECHNICZNE (UŚREDNIONE Z GIEŁD):
{tech_summary}

TOP SELEKCJA EKSTREMÓW OKX (DANE Z WYKRESU 1D):
{okx_extremes}

DANE POCHODNE I LIKWIDACJE:
- Open Interest (OKX BTC): {deriv_data['open_interest_okx']}
- Open Interest (OKX STRK): {deriv_data['open_interest_strk']}
- Funding Rate (OKX BTC): {deriv_data['funding_rate_okx']}
- Funding Rate (OKX STRK): {deriv_data['funding_rate_strk']}
- Mapa Likwidacji / Ściana Płynności STRK: {deriv_data['liquidation_map_strk']}
- Monitor Likwidacji / Presja (Bybit BTC): {deriv_data['liquidations_bybit']}
- Baza Kosztowa (Cost Basis): {deriv_data['realized_price']}

PODZIEL RAPORT NA DWIE CZĘŚCI ROZDZIELONE SEKCJA "Top Selekcja":
Część 1: Kondycja Rynku, BTC, STRK (Starknet) uwzględniając strefę płynności i cel $0.060-$0.061, S (Sonic).
Część 2: Top Selekcja Altcoinowa z OKX (2-3 najlepsze układy z precyzyjnym Entry, SL, TP).
"""
    
    candidate_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    
    for model_name in candidate_models:
        delay = initial_delay
        for attempt in range(1, max_retries + 1):
            try:
                print(f"Próba wywołania Gemini ({model_name}), próba {attempt}...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                if response and response.text:
                    text = response.text.strip()
                    parts = text.split("Top Selekcja")
                    part1 = parts[0].strip()
                    part2 = ("Top Selekcja" + parts[1]) if len(parts) > 1 else "Top Selekcja Altcoinowa z OKX: Brak dodatkowych wskazań."
                    return part1, part2
            except APIError as e:
                print(f"Ostrzeżenie API Gemini {model_name}: {e}")
            except Exception as e:
                print(f"Błąd wywołania {model_name}: {e}")
                
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay *= 2

    return "Błąd generowania analizy przez AI (limity/przeciążenie API Gemini).", "Spróbuj ponownie później."


# --- 6. WYSYŁKA NA TELEGRAM ---
async def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Brak danych konfiguracyjnych Telegrama.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        async with session.post(url, json=payload) as resp:
            res_text = await resp.text()
            print(f"Odpowiedź Telegram API: {res_text}")
            return res_text


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
            
            # Weryfikacja wzorców historycznych
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
            pattern_summary += f"- {sym}: Reżim: {pat.get('regime')}, Sygnał Wzorca: {pat.get('pattern_signal')}, Notatki: {pat.get('notes')}\n"

        okx_summary = "Skanowanie OKX Ekstremów 1D:\n"
        for item in okx_extremes[:3]:
            okx_summary += f"- {item['symbol']}: Cena: ${item['close']}, RSI: {item['rsi']}\n"

        # 1. Generujemy profesjonalną analizę AI dla Telegrama (dwie niezależne wiadomości)
        part1, part2 = await generate_ai_analysis(tech_summary, deriv_data, pattern_summary, okx_summary)
        await send_telegram(part1)
        await asyncio.sleep(1)
        await send_telegram(part2)
        
        # 2. Budujemy czyste, potężne repozytorium surowych danych w pliku HTML (BEZ TEKSTU AI)
        html_boxes = ""
        for sym in symbols:
            rows = ""
            for bar in ["1h", "4h", "1d", "1w", "1M"]:
                d = market_data[sym][bar]
                rows += f'''
                <tr>
                    <td><b>{bar.upper()}</b></td>
                    <td>${d["close"]}</td>
                    <td>{d["rsi"]}</td>
                    <td>{d["stoch_k"]}</td>
                    <td>{d["macd_line"]} / {d["macd_signal"]} ({d["macd_hist"]})</td>
                    <td>${d["ema20"]}</td>
                    <td>${d["ema50"]}</td>
                    <td>${d["ema200"]}</td>
                </tr>
                '''
            
            pat = pattern_evaluations[sym]
            html_boxes += f"""
            <div class="tech-card">
                <h3>{sym} — Sygnał Wzorca: <span class="badge">{pat.get('pattern_signal')}</span></h3>
                <p class="notes"><b>Diagnoza Wzorców Historycznych:</b> {pat.get('notes')}</p>
                <p class="notes"><b>Dywergencje RSI:</b> 1W: {pat.get('divergences', {}).get('1w')}, 1D: {pat.get('divergences', {}).get('1d')}, 4H: {pat.get('divergences', {}).get('4h')}</p>
                <table>
                    <thead>
                        <tr>
                            <th>Interwał</th>
                            <th>Cena Zamknięcia</th>
                            <th>RSI (14)</th>
                            <th>StochRSI K</th>
                            <th>MACD (Line / Signal / Hist)</th>
                            <th>EMA 20</th>
                            <th>EMA 50</th>
                            <th>EMA 200</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
            """

        okx_box_rows = ""
        for item in okx_extremes:
            okx_box_rows += f'''
            <tr>
                <td><b>{item["symbol"]}</b></td>
                <td>${item["close"]}</td>
                <td>{item["rsi"]}</td>
                <td>{item["extreme_score"]:.2f}</td>
            </tr>
            '''

        html_content = f"""<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Market Maker Monitor — Repozytorium Analityczne</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f1115; color: #e1e1e6; margin: 0; padding: 25px; line-height: 1.6; }}
        .container {{ max-width: 1100px; margin: 0 auto; background: #16181d; padding: 30px; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.7); }}
        h1 {{ color: #00e676; border-bottom: 2px solid #232730; padding-bottom: 12px; margin-top: 0; }}
        h2 {{ color: #40c4ff; margin-top: 35px; border-bottom: 1px solid #232730; padding-bottom: 8px; }}
        h3 {{ color: #ffab40; margin-top: 20px; }}
        .tech-card {{ background: #1a1d24; padding: 20px; border-radius: 8px; margin-top: 20px; border-left: 5px solid #00e676; }}
        .notes {{ font-size: 0.9em; color: #b0b5c0; margin: 5px 0 15px 0; }}
        .badge {{ background: #232730; color: #00e676; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.88em; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #2a2e39; }}
        th {{ background-color: #0f1115; color: #8892b0; font-weight: 600; }}
        tr:hover {{ background-color: #232730; }}
        .deriv-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 15px; margin-top: 15px; }}
        .deriv-card {{ background: #1a1d24; padding: 15px; border-radius: 6px; border: 1px solid #232730; }}
        .deriv-card span {{ display: block; font-size: 0.8em; color: #8892b0; }}
        .deriv-card strong {{ font-size: 1.1em; color: #00e676; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Market Maker Monitor — Live Analytical Database</h1>
        <p>Data aktualizacji: <b>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</b></p>
        
        <h2>1. Rynek Pochodnych, Funding Rates & Baza Kosztowa On-Chain</h2>
        <div class="deriv-grid">
            <div class="deriv-card">
                <span>Open Interest (BTC OKX)</span>
                <strong>{deriv_data['open_interest_okx']}</strong>
            </div>
            <div class="deriv-card">
                <span>Open Interest (STRK OKX)</span>
                <strong>{deriv_data['open_interest_strk']}</strong>
            </div>
            <div class="deriv-card">
                <span>Funding Rate (BTC OKX)</span>
                <strong>{deriv_data['funding_rate_okx']}</strong>
            </div>
            <div class="deriv-card">
                <span>Funding Rate (STRK OKX)</span>
                <strong>{deriv_data['funding_rate_strk']}</strong>
            </div>
        </div>
        <div class="tech-card" style="border-left-color: #40c4ff; margin-top: 20px;">
            <p><b>Mapa Likwidacji / Ściana Płynności STRK ($0.060 - $0.061):</b> {deriv_data['liquidation_map_strk']}</p>
            <p><b>Presja Likwidacyjna & Duże Zlecenia (Bybit BTC):</b> {deriv_data['liquidations_bybit']}</p>
            <p><b>Poziomy Bazy Kosztowej (Cost Basis):</b> {deriv_data['realized_price']}</p>
        </div>

        <h2>2. Skan Ekstremów Płynnościowych OKX (Wszystkie monitorowane altcoiny 1D)</h2>
        <table>
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Cena ($)</th>
                    <th>RSI (1D)</th>
                    <th>Wskaźnik Odchylenia (Extreme Score)</th>
                </tr>
            </thead>
            <tbody>
                {okx_box_rows}
            </tbody>
        </table>

        <h2>3. Pełne Zestawienie Wskaźników Technicznych z 4 Giełd (OKX, Binance, Kraken, Bybit)</h2>
        {html_boxes}
    </div>
</body>
</html>
"""
        # 3. Wysłanie czystego repozytorium danych na FTP
        upload_html_to_ftp(html_content)
        print("Proces zakończony pomyślnie.")

if __name__ == "__main__":
    asyncio.run(main())
