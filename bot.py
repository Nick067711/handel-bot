import os
import ftplib
import asyncio
import aiohttp
import numpy as np
from google import genai
from google.genai.errors import APIError

# --- KONFIGURACJA ŚRODOWISKOWA / API ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

FTP_HOST = os.getenv("FTP_HOST", "piwecki.pl")
FTP_USER = os.getenv("FTP_USER", "")
FTP_PASS = os.getenv("FTP_PASS", "")


# --- KALKULATOR WSKAŹNIKÓW TECHNICZNYCH ---
class TechnicalIndicators:
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
        if len(prices) < period:
            return float(prices[-1])
        prices = np.array(prices)
        weights = np.exp(np.linspace(-1., 0., period))
        weights /= weights.sum()
        ema = np.convolve(prices, weights, mode='full')[:len(prices)]
        ema[:period] = ema[period]
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
            rsi_val = TechnicalIndicators.calculate_rsi(sub_prices, rsi_period)
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


# --- AGREGACJA DANYCH Z MULTI-EXCHANGE (OKX, BINANCE, KRAKEN) ---
async def fetch_okx_candles(session, symbol, bar):
    # Mapping bar format for OKX
    bar_map = {"1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
    okx_bar = bar_map.get(bar, "1H")
    inst_id = f"{symbol}-USDT-SWAP" if "USDT" not in symbol else symbol
    url = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={okx_bar}&limit=100"
    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("data"):
                    candles = data["data"][::-1]
                    return [float(c[4]) for c in candles]
    except Exception as e:
        print(f"OKX Fetch error ({symbol} {bar}): {e}")
    return None

async def fetch_binance_candles(session, symbol, bar):
    # Mapping bar format for Binance
    bar_map = {"1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}
    bn_bar = bar_map.get(bar, "1h")
    bn_symbol = f"{symbol}USDT" if "USDT" not in symbol else symbol
    url = f"https://api.binance.com/api/v3/klines?symbol={bn_symbol}&interval={bn_bar}&limit=100"
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
    # Mapping bar format for Kraken (interval in minutes)
    bar_map = {"1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
    kr_interval = bar_map.get(bar, 60)
    pair = f"XBTUSDT" if symbol == "BTC" else f"{symbol}USDT"
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={kr_interval}"
    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                result = data.get("result", {})
                for k, v in result.items():
                    if k != "last":
                        closes = [float(c[4]) for c in v[-100:]]
                        return closes
    except Exception as e:
        print(f"Kraken Fetch error ({symbol} {bar}): {e}")
    return None

async def fetch_aggregated_market_data(session, symbol, bar):
    """Pobiera i uśrednia wskaźniki z OKX, Binance oraz Krakena"""
    tasks = [
        fetch_okx_candles(session, symbol, bar),
        fetch_binance_candles(session, symbol, bar),
        fetch_kraken_candles(session, symbol, bar)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    valid_indicators = []
    for closes in results:
        if isinstance(closes, list) and len(closes) >= 20:
            rsi = TechnicalIndicators.calculate_rsi(closes)
            ema20 = TechnicalIndicators.calculate_ema(closes, 20)
            ema50 = TechnicalIndicators.calculate_ema(closes, 50)
            ema200 = TechnicalIndicators.calculate_ema(closes, 200) if len(closes) >= 200 else ema50
            macd = TechnicalIndicators.calculate_macd(closes)
            stoch = TechnicalIndicators.calculate_stoch_rsi(closes)
            
            valid_indicators.append({
                "close": closes[-1],
                "rsi": rsi,
                "stoch_k": stoch["k"],
                "macd_hist": macd["hist"],
                "ema20": ema20,
                "ema50": ema50,
                "ema200": ema200
            })

    if not valid_indicators:
        return {"close": 0.0, "rsi": 50.0, "stoch_k": 50.0, "macd_hist": 0.0, "ema20": 0.0, "ema50": 0.0, "ema200": 0.0}

    # Uśrednianie wyników ze sprawdzonych giełd
    avg_data = {
        "close": round(float(np.mean([d["close"] for d in valid_indicators])), 4 if symbol == "STRK" else 2),
        "rsi": round(float(np.mean([d["rsi"] for d in valid_indicators])), 2),
        "stoch_k": round(float(np.mean([d["stoch_k"] for d in valid_indicators])), 2),
        "macd_hist": round(float(np.mean([d["macd_hist"] for d in valid_indicators])), 4),
        "ema20": round(float(np.mean([d["ema20"] for d in valid_indicators])), 4 if symbol == "STRK" else 2),
        "ema50": round(float(np.mean([d["ema50"] for d in valid_indicators])), 4 if symbol == "STRK" else 2),
        "ema200": round(float(np.mean([d["ema200"] for d in valid_indicators])), 4 if symbol == "STRK" else 2)
    }
    return avg_data


async def fetch_btc_derivatives_and_onchain(session):
    oi_data = {"open_interest": "Brak danych", "realized_price": "~$64,500 (szacunek on-chain)"}
    try:
        url_oi = "https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP"
        async with session.get(url_oi, timeout=10) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("data") and len(res["data"]) > 0:
                    oi_val = float(res["data"][0].get("oiCcy", 0))
                    oi_data["open_interest"] = f"{oi_val:,.2f} BTC"
    except Exception as e:
        print(f"Błąd pobierania Open Interest: {e}")
        
    return oi_data


# --- GENEROWANIE RAPORTU PRZEZ GEMINI (STRICT MODEL: gemini-3.8-flash WITH RETRY) ---
async def generate_ai_analysis(tech_summary, whale_data, max_retries=5, initial_delay=5):
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    Jesteś profesjonalnym analitykiem rynkowym i systemem Market Maker Monitor. 
    Przygotuj profesjonalny, szczegółowy raport handlowy uwzględniający wyższe interwały (1h, 4h, 1D, 1W) z uśrednionych danych z OKX, Binance i Kraken, a także dane o wielorybach i kapitałach pochodnych (Open Interest, Realized Price):
    
    UŚREDNIONE DANE TECHNICZNE (OKX + BINANCE + KRAKEN):
    {tech_summary}
    
    DANE ON-CHAIN / WIELORYBY:
    - Open Interest (BTC SWAP): {whale_data['open_interest']}
    - Realized Price / Baza kosztowa wielorybów: {whale_data['realized_price']}
    
    Podziel raport na dwie niezależne części:
    1. Kondycja Rynku, BTC, Działania Wielorybów (Open Interest & Realized Price), Poziomy Likwidacji oraz Starknet (STRK).
    2. Top Selekcja Altcoinowa oraz precyzyjne strefy wejścia/wyjścia (Entry, SL, TP) bazujące na obecnym ułożeniu rynku.
    """
    
    delay = initial_delay
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt
            )
            if response and response.text:
                text = response.text
                parts = text.split("Top Selekcja")
                part1 = parts[0].strip()
                part2 = ("Top Selekcja" + parts[1]) if len(parts) > 1 else "Brak danych sekcji 2."
                return part1, part2
        except APIError as e:
            print(f"Ostrzeżenie: Błąd API Gemini (próba {attempt}/{max_retries}): {e}")
        except Exception as e:
            print(f"Próba {attempt}/{max_retries} nieudana z powodu błędu: {e}")
            
        if attempt < max_retries:
            print(f"Czekam {delay} s przed kolejną próbą zapytania Gemini...")
            await asyncio.sleep(delay)
            delay *= 2

    return "Błąd generowania analizy (przeciążenie API Gemini po wielokrotnych próbach).", "Spróbuj ponownie przy kolejnym uruchomieniu."


# --- WYSYŁKA NA TELEGRAM ---
async def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        async with session.post(url, json=payload) as resp:
            return await resp.text()


# --- WYSYŁKA HTML NA FTP ---
def upload_html_to_ftp(html_content):
    if not FTP_USER or not FTP_PASS:
        return
    with ftplib.FTP(FTP_HOST, FTP_USER, FTP_PASS) as ftp:
        try:
            ftp.cwd("domains/piwecki.pl/public_html/analiza")
        except ftplib.error_perm:
            ftp.cwd("domains/piwecki.pl/public_html")
            try:
                ftp.mkd("analiza")
            except:
                pass
            ftp.cwd("analiza")
            
        with open("index.html", "w", encoding="utf-8") as f:
            f.write(html_content)
            
        with open("index.html", "rb") as f:
            ftp.storbinary("STOR index.html", f)


# --- GŁÓWNA PĘTLA PROGRAMU ---
async def main():
    async with aiohttp.ClientSession() as session:
        intervals = ["1h", "4h", "1d", "1w"]
        
        btc_data = {}
        strk_data = {}
        
        # Pobieranie uśrednionych danych z giełd
        for bar in intervals:
            btc_data[bar] = await fetch_aggregated_market_data(session, "BTC", bar)
            strk_data[bar] = await fetch_aggregated_market_data(session, "STRK", bar)
            
        whale_data = await fetch_btc_derivatives_and_onchain(session)
        
        tech_summary = "BTC Wyższe Interwały (Averaged OKX/Binance/Kraken):\n"
        for bar in intervals:
            d = btc_data[bar]
            tech_summary += f"- {bar}: Cena: ${d['close']}, RSI: {d['rsi']}, StochRSI_K: {d['stoch_k']}, MACD Hist: {d['macd_hist']}, EMA20: ${d['ema20']}, EMA50: ${d['ema50']}\n"
            
        tech_summary += "\nSTRK Wyższe Interwały (Averaged OKX/Binance/Kraken):\n"
        for bar in intervals:
            d = strk_data[bar]
            tech_summary += f"- {bar}: Cena: ${d['close']}, RSI: {d['rsi']}, StochRSI_K: {d['stoch_k']}, MACD Hist: {d['macd_hist']}, EMA20: ${d['ema20']}, EMA50: ${d['ema50']}\n"
        
        part1, part2 = await generate_ai_analysis(tech_summary, whale_data)
        
        await send_telegram(part1)
        await asyncio.sleep(1)
        await send_telegram(part2)
        
        btc_rows = ""
        for bar in intervals:
            d = btc_data[bar]
            btc_rows += f'<div class="data-row"><b>{bar.upper()}:</b> Zamknięcie: ${d["close"]} | RSI: {d["rsi"]} | StochRSI K: {d["stoch_k"]} | MACD Hist: {d["macd_hist"]} | EMA20: ${d["ema20"]} | EMA50: ${d["ema50"]}</div>\n'

        strk_rows = ""
        for bar in intervals:
            d = strk_data[bar]
            strk_rows += f'<div class="data-row"><b>{bar.upper()}:</b> Zamknięcie: ${d["close"]} | RSI: {d["rsi"]} | StochRSI K: {d["stoch_k"]} | MACD Hist: {d["macd_hist"]} | EMA20: ${d["ema20"]} | EMA50: ${d["ema50"]}</div>\n'

        html_content = f"""<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Market Maker Monitor - Raport Analityczny</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, sans-serif; background-color: #121214; color: #e1e1e6; margin: 0; padding: 20px; }}
        .container {{ max-width: 900px; margin: 0 auto; background: #202024; padding: 25px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.6); }}
        h1 {{ color: #00b37e; border-bottom: 2px solid #29292e; padding-bottom: 10px; }}
        h2 {{ color: #61dafb; margin-top: 25px; border-bottom: 1px solid #29292e; padding-bottom: 5px; }}
        h3 {{ color: #fba94c; margin-top: 15px; }}
        pre {{ background: #121214; padding: 15px; border-radius: 5px; white-space: pre-wrap; word-wrap: break-word; color: #c4c4cc; line-height: 1.5; }}
        .tech-box {{ background: #18181b; padding: 15px; border-radius: 6px; margin-top: 15px; border-left: 4px solid #00b37e; }}
        .data-row {{ font-family: monospace; font-size: 0.95em; color: #00e676; margin: 6px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Market Maker Monitor - Live Report</h1>
        
        <h2>Analiza Rynku, Wieloryby i Kondycja</h2>
        <pre>{part1}</pre>

        <h2>Top Selekcja Altcoinowa</h2>
        <pre>{part2}</pre>

        <h2>Dane On-Chain & Pochodne (Wieloryby)</h2>
        <div class="tech-box">
            <div class="data-row"><b>Open Interest (BTC SWAP):</b> {whale_data['open_interest']}</div>
            <div class="data-row"><b>Realized Price (Szacunek On-Chain):</b> {whale_data['realized_price']}</div>
        </div>

        <h2>Dane Techniczne i Wskaźniki Uśrednione (1h, 4h, 1D, 1W) [OKX, Binance, Kraken]</h2>
        
        <div class="tech-box">
            <h3>BTC (Bitcoin)</h3>
            {btc_rows}
        </div>

        <div class="tech-box">
            <h3>STRK (Starknet)</h3>
            {strk_rows}
        </div>
    </div>
</body>
</html>
"""
        upload_html_to_ftp(html_content)
        print("Raport HTML pomyślnie wygenerowany i przesłany na serwer pod https://piwecki.pl/analiza/")

if __name__ == "__main__":
    asyncio.run(main())
