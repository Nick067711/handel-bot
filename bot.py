import os
import json
import ftplib
import asyncio
import aiohttp
import urllib.request
import urllib.parse
import numpy as np
from datetime import datetime, timezone
from google import genai
from google.genai import types

# --- KONFIGURACJA ŚRODOWISKOWA / API ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

FTP_HOST = os.getenv("FTP_HOST", "piwecki.pl")
FTP_USER = os.getenv("FTP_USER", "")
FTP_PASS = os.getenv("FTP_PASS", "")
FTP_DIR = os.getenv("FTP_DIR", "domains/piwecki.pl/public_html/analiza")

# --- 1. SILNIK ANALIZY TECHNICZNEJ (Wielorezolucyjny dla BTC i NEAR) ---
class TradeEngine:
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
            rsi_val = TradeEngine.calculate_rsi(sub_prices, rsi_period)
            rsi_series.append(rsi_val)
            
        if len(rsi_series) < stoch_period:
            return 50.0
            
        rsi_slice = rsi_series[-stoch_period:]
        min_rsi = min(rsi_slice)
        max_rsi = max(rsi_slice)
        
        if max_rsi == min_rsi:
            return 100.0
        return round(float(((rsi_series[-1] - min_rsi) / (max_rsi - min_rsi)) * 100), 2)


# --- 2. POBIERANIE ŚWIEC Z WIELU GIEŁD ---
async def fetch_okx_candles(session, symbol, bar):
    bar_map = {"1h": "1H", "4h": "4H", "1d": "1D"}
    inst_id = f"{symbol}-USDT-SWAP"
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
    bar_map = {"1h": "1h", "4h": "4h", "1d": "1d"}
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
    bar_map = {"1h": 60, "4h": 240, "1d": 1440}
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
    bar_map = {"1h": "60", "4h": "240", "1d": "D"}
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
            rsi = TradeEngine.calculate_rsi(closes)
            ema20 = TradeEngine.calculate_ema(closes, 20)
            ema50 = TradeEngine.calculate_ema(closes, 50)
            macd = TradeEngine.calculate_macd(closes)
            stoch_k = TradeEngine.calculate_stoch_rsi(closes)
            
            valid_indicators.append({
                "close": closes[-1],
                "rsi": rsi,
                "stoch_k": stoch_k,
                "macd_hist": macd["hist"],
                "ema20": ema20,
                "ema50": ema50
            })

    if not valid_indicators:
        return {"close": 0.0, "rsi": 50.0, "stoch_k": 50.0, "macd_hist": 0.0, "raw_closes": []}

    decimals = 4 if symbol == "NEAR" else 2
    best_raw_closes = max(valid_closes_list, key=len) if valid_closes_list else []

    return {
        "close": round(float(np.mean([d["close"] for d in valid_indicators])), decimals),
        "rsi": round(float(np.mean([d["rsi"] for d in valid_indicators])), 2),
        "stoch_k": round(float(np.mean([d["stoch_k"] for d in valid_indicators])), 2),
        "macd_hist": round(float(np.mean([d["macd_hist"] for d in valid_indicators])), 4),
        "ema20": round(float(np.mean([d["ema20"] for d in valid_indicators])), decimals),
        "ema50": round(float(np.mean([d["ema50"] for d in valid_indicators])), decimals),
        "raw_closes": best_raw_closes
    }


# --- 3. PEŁNA WIELOGIEŁDOWA AGREGACJA PŁYNNOŚCI I ORDERBOOKÓW ---
def aggregate_baskets_to_zones(walls, current_price, is_btc=True):
    # Szersze zakresy dla day tradingu (1h/4h/1d)
    bin_size = 1000.0 if is_btc else 0.20
    token_name = "BTC" if is_btc else "NEAR"
    asks_bins, bids_bins = {}, {}
    
    for w in walls:
        p = w["price"]
        s = w["size"]
        if w["type"] == "Ask" and current_price <= p <= current_price * 1.15:
            b_key = int(p / bin_size) * bin_size
            asks_bins.setdefault(b_key, {"token": 0.0, "usd": 0.0})
            asks_bins[b_key]["token"] += s
            asks_bins[b_key]["usd"] += s * p
        elif w["type"] == "Bid" and current_price * 0.85 <= p <= current_price:
            b_key = int(p / bin_size) * bin_size
            bids_bins.setdefault(b_key, {"token": 0.0, "usd": 0.0})
            bids_bins[b_key]["token"] += s
            bids_bins[b_key]["usd"] += s * p
            
    best_ask, best_bid = "Brak", "Brak"
    if asks_bins:
        k_ask = max(asks_bins, key=lambda x: asks_bins[x]["token"])
        best_ask = f"${k_ask:,.2f} - ${k_ask+bin_size:,.2f} | {asks_bins[k_ask]['token']:,.2f} {token_name}"
    if bids_bins:
        k_bid = max(bids_bins, key=lambda x: bids_bins[x]["token"])
        best_bid = f"${k_bid:,.2f} - ${k_bid+bin_size:,.2f} | {bids_bins[k_bid]['token']:,.2f} {token_name}"
    return best_ask, best_bid

async def fetch_multi_exchange_liquidity(session):
    liquidity_data = {"btc_liquidation_map": "Brak", "near_liquidation_map": "Brak"}
    
    # --- BTC ORDERBOOKS (OKX, Binance, Bybit, Kraken) ---
    btc_walls = []
    
    # OKX BTC
    try:
        async with session.get("https://www.okx.com/api/v5/market/books?instId=BTC-USDT-SWAP&sz=300", timeout=5) as resp:
            res = await resp.json()
            if res.get("data"):
                for a in res["data"][0].get("asks", []): btc_walls.append({"type": "Ask", "price": float(a[0]), "size": float(a[1])})
                for b in res["data"][0].get("bids", []): btc_walls.append({"type": "Bid", "price": float(b[0]), "size": float(b[1])})
    except Exception: pass

    # Binance BTC
    try:
        async with session.get("https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=500", timeout=5) as resp:
            res = await resp.json()
            for a in res.get("asks", []): btc_walls.append({"type": "Ask", "price": float(a[0]), "size": float(a[1])})
            for b in res.get("bids", []): btc_walls.append({"type": "Bid", "price": float(b[0]), "size": float(b[1])})
    except Exception: pass

    # Bybit BTC
    try:
        async with session.get("https://api.bybit.com/v5/market/orderbook?category=linear&symbol=BTCUSDT&limit=200", timeout=5) as resp:
            res = await resp.json()
            result = res.get("result", {})
            for a in result.get("a", []): btc_walls.append({"type": "Ask", "price": float(a[0]), "size": float(a[1])})
            for b in result.get("b", []): btc_walls.append({"type": "Bid", "price": float(b[0]), "size": float(b[1])})
    except Exception: pass

    # Kraken BTC
    try:
        async with session.get("https://api.kraken.com/0/public/Depth?pair=XBTUSDT&count=100", timeout=5) as resp:
            res = await resp.json()
            result = res.get("result", {}).get("XXBTZUSDT", {}) or res.get("result", {}).get("XBTUSDT", {})
            for a in result.get("asks", []): btc_walls.append({"type": "Ask", "price": float(a[0]), "size": float(a[1])})
            for b in result.get("bids", []): btc_walls.append({"type": "Bid", "price": float(b[0]), "size": float(b[1])})
    except Exception: pass

    if btc_walls:
        ask_z, bid_z = aggregate_baskets_to_zones(btc_walls, 80000.0, is_btc=True)
        liquidity_data["btc_liquidation_map"] = f"Opór (Ask): {ask_z} | Wsparcie (Bid): {bid_z}"

    # --- NEAR ORDERBOOKS (OKX, Binance, Bybit) ---
    near_walls = []
    
    # OKX NEAR
    try:
        async with session.get("https://www.okx.com/api/v5/market/books?instId=NEAR-USDT-SWAP&sz=300", timeout=5) as resp:
            res = await resp.json()
            if res.get("data"):
                for a in res["data"][0].get("asks", []): near_walls.append({"type": "Ask", "price": float(a[0]), "size": float(a[1])})
                for b in res["data"][0].get("bids", []): near_walls.append({"type": "Bid", "price": float(b[0]), "size": float(b[1])})
    except Exception: pass

    # Binance NEAR
    try:
        async with session.get("https://api.binance.com/api/v3/depth?symbol=NEARUSDT&limit=500", timeout=5) as resp:
            res = await resp.json()
            for a in res.get("asks", []): near_walls.append({"type": "Ask", "price": float(a[0]), "size": float(a[1])})
            for b in res.get("bids", []): near_walls.append({"type": "Bid", "price": float(b[0]), "size": float(b[1])})
    except Exception: pass

    # Bybit NEAR
    try:
        async with session.get("https://api.bybit.com/v5/market/orderbook?category=linear&symbol=NEARUSDT&limit=200", timeout=5) as resp:
            res = await resp.json()
            result = res.get("result", {})
            for a in result.get("a", []): near_walls.append({"type": "Ask", "price": float(a[0]), "size": float(a[1])})
            for b in result.get("b", []): near_walls.append({"type": "Bid", "price": float(b[0]), "size": float(b[1])})
    except Exception: pass

    if near_walls:
        ask_zn, bid_zn = aggregate_baskets_to_zones(near_walls, 4.5, is_btc=False)
        liquidity_data["near_liquidation_map"] = f"Opór (Ask): {ask_zn} | Wsparcie (Bid): {bid_zn}"

    return liquidity_data


# --- 4. ANALIZA AI (PROSTY JĘZYK, AKAPITY, BRAK ŻARGONU) ---
async def generate_ai_report(btc_data, near_data, liquidity_data):
    if not GEMINI_API_KEY:
        raise RuntimeError("Brak klucza API Gemini.")
        
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
Jesteś profesjonalnym doradcą day tradingu skupionym na korelacji Bitcoina (BTC) i NEAR.
Przeanalizuj poniższe dane rynkowe zebrane z wielu giełd (OKX, Binance, Bybit, Kraken) z interwałów 1h, 4h oraz 1d:

BTC DANE:
{json.dumps(btc_data, indent=2)}

NEAR DANE:
{json.dumps(near_data, indent=2)}

PŁYNNOŚĆ I STREFY RYNKU (WIELOGIEŁDOWE):
{json.dumps(liquidity_data, indent=2)}

Zwróć odpowiedź WYŁĄCZNIE w formacie JSON z trzema kluczami:
1. "html_content": pełny kod HTML raportu dla strony internetowej https://piwecki.pl/analiza/ (ciemny motyw, czytelna struktura).
2. "telegram_msg_btc": Wiadomość Telegram nr 1 (Kondycja Rynku, relacja BTC i NEAR, korelacja). Pisz krótkimi, czytelnymi akapitami opisowymi. Unikaj suchego żargonu technicznego – tłumacz sytuację przystępnym językiem (jak dla inwestora szukającego jasnych wniosków).
3. "telegram_msg_signals": Wiadomość Telegram nr 2 (Okazja Day Tradingowa). Opisz w prostych akapitach, czy warto szukać pozycji długich (Long) czy krótkich (Short), podając czytelne strefy wejścia, poziom obrony (Stop Loss), cel (Take Profit) oraz bezpieczną wielkość pozycji.

BEZWZGLĘDNE ZASADY:
- Zakaz stosowania formatowania LaTeX (zakaz ukośników wstecznych). Wszystkie ceny zapisuj w czystym formacie tekstowym np. `$81,500` lub `$4.55`.
- Wiadomości na Telegram mają być opisowe, podzielone na zgrabne akapity, z użyciem znaczników <b> do delikatnych wyróżnień, bez przytłaczania surowymi rzędami cyfr wskaźników.
"""

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Próba {attempt + 1} nie powiodła się: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
            else:
                return None


# --- 5. FTP & TELEGRAM ---
def upload_to_ftp(html_content):
    if not all([FTP_HOST, FTP_USER, FTP_PASS]):
        print("Błąd: Brak pełnej konfiguracji zmiennych środowiskowych FTP.")
        return False
        
    try:
        print(f"Łączenie z serwerem FTP: {FTP_HOST}...")
        with ftplib.FTP(FTP_HOST, timeout=15) as ftp:
            ftp.login(FTP_USER, FTP_PASS)
            
            dirs = FTP_DIR.split("/")
            for d in dirs:
                if d:
                    try:
                        ftp.cwd(d)
                    except ftplib.error_perm:
                        ftp.mkd(d)
                        ftp.cwd(d)
                        
            temp_filename = "index.html"
            with open(temp_filename, "w", encoding="utf-8") as f:
                f.write(html_content)
                
            with open(temp_filename, "rb") as f:
                ftp.storbinary(f"STOR index.html", f)
                
            os.remove(temp_filename)
            print("Sukces! Raport HTML zaktualizowany na serwerze: https://piwecki.pl/analiza/")
            return True
    except Exception as e:
        print(f"Błąd FTP: {e}")
        return False

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not text:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if not res_data.get("ok"):
                print(f"Telegram API error: {res_data}")
    except Exception as e:
        print(f"Błąd wysyłki Telegram: {e}")


# --- 6. GŁÓWNA FUNKCJA (Z ODPRONŚCIĄ NA BŁĘDY AI) ---
async def main():
    print("Start cyklu analitycznego BTC & NEAR...")
    async with aiohttp.ClientSession() as session:
        btc_data = {}
        near_data = {}
        for tf in ["1h", "4h", "1d"]:
            btc_data[tf] = await fetch_aggregated_market_data(session, "BTC", tf)
            near_data[tf] = await fetch_aggregated_market_data(session, "NEAR", tf)
            
        liquidity_data = await fetch_multi_exchange_liquidity(session)
        
        # Próba uzyskania raportu z AI
        report_data = await generate_ai_report(btc_data, near_data, liquidity_data)
        
        if report_data and report_data.get("html_content"):
            # Pełny sukces - wysyłamy raport AI na FTP i Telegram
            upload_to_ftp(report_data["html_content"])
            
            if report_data.get("telegram_msg_btc"):
                send_telegram_message(report_data["telegram_msg_btc"])
                await asyncio.sleep(1.5)
                
            if report_data.get("telegram_msg_signals"):
                send_telegram_message(report_data["telegram_msg_signals"])
        else:
            print("Ostrzeżenie: Model AI nie zwrócił odpowiedzi (chwilowy problem z API). Wysyłam awaryjny raport na FTP...")
            
            # Awaryjny HTML, żeby strona na FTP ZAWSZE się zaktualizowała i nie wisiała
            fallback_html = f"""
            <!DOCTYPE html>
            <html lang="pl">
            <head>
                <meta charset="UTF-8">
                <title>Market Maker Monitor - BTC & NEAR</title>
                <style>
                    body {{ background-color: #121212; color: #e0e0e0; font-family: Arial, sans-serif; text-align: center; padding-top: 50px; }}
                    .box {{ background: #1e1e1e; padding: 30px; border-radius: 10px; display: inline-block; max-width: 600px; }}
                    h2 {{ color: #ff9800; }}
                </style>
            </head>
            <body>
                <div class="box">
                    <h2>Market Maker Monitor - BTC & NEAR</h2>
                    <p>Trwa aktualizacja danych rynkowych. Model AI generuje bieżącą analizę...</p>
                    <p><small>Ostatnia próba: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</small></p>
                </div>
            </body>
            </html>
            """
            upload_to_ftp(fallback_html)
            
    print("Cykl analityczny zakończony.")
