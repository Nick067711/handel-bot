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

# --- SILNIK ANALIZY TECHNICZNEJ DLA BTC I NEAR ---
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

# --- POBIERANIE ŚWIEC Z WIELU INTERWAŁÓW DLA BTC I NEAR ---
async def fetch_candles(session, symbol, bar):
    # Mapowanie interwałów OKX
    bar_map = {"1h": "1H", "4h": "4H", "1d": "1D"}
    inst_id = f"{symbol}-USDT-SWAP"
    url = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar_map.get(bar, '1H')}&limit=100"
    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("data"):
                    return [float(c[4]) for c in data["data"][::-1]] # Ceny zamknięcia rosnąco
    except Exception as e:
        print(f"Błąd pobierania świec {symbol} ({bar}): {e}")
    return []

async def gather_multi_timeframe_data(session, symbol):
    data = {}
    for tf in ["1h", "4h", "1d"]:
        closes = await fetch_candles(session, symbol, tf)
        if closes:
            rsi = TradeEngine.calculate_rsi(closes)
            stoch_k = TradeEngine.calculate_stoch_rsi(closes)
            data[tf] = {
                "close": closes[-1],
                "rsi": rsi,
                "stoch_k": stoch_k,
                "raw_closes": closes
            }
        else:
            data[tf] = {"close": 0.0, "rsi": 50.0, "stoch_k": 50.0, "raw_closes": []}
    return data

# --- POBIERANIE PŁYNNOŚCI I ORDERBOOKÓW ---
async def fetch_orderbooks(session):
    books = {"btc_price": 0.0, "near_price": 0.0, "info": "Pobierane z rynków"}
    try:
        async with session.get("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP", timeout=5) as resp:
            res = await resp.json()
            if res.get("data"):
                books["btc_price"] = float(res["data"][0]["last"])
        async with session.get("https://www.okx.com/api/v5/market/ticker?instId=NEAR-USDT-SWAP", timeout=5) as resp:
            res = await resp.json()
            if res.get("data"):
                books["near_price"] = float(res["data"][0]["last"])
    except Exception as e:
        print(f"Błąd pobierania cen: {e}")
    return books

# --- ANALIZA AI Z DECYZJĄ O SYGNALE ---
async def generate_ai_trade_decision(btc_data, near_data, market_prices):
    if not GEMINI_API_KEY:
        raise RuntimeError("Brak klucza API Gemini.")
        
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
Jesteś systemem algorytmicznym Day Trading Market Maker Monitor. Skupiasz się wyłącznie na korelacji między BTC a NEAR.
Przeanalizuj poniższe dane wielointerwałowe (1h, 4h, 1d) dla obu aktywów:

BTC DANE:
{json.dumps(btc_data, indent=2)}

NEAR DANE:
{json.dumps(near_data, indent=2)}

BIEŻĄCE CENY RYNKOWE:
{json.dumps(market_prices, indent=2)}

Twoje zadanie:
1. Oceń, czy w obecnym oknie czasowym występuje silny, klarowny układ techniczny (ekstremalne RSI, retest strefy, korelacja wskazująca na nagły ruch) pod **Long** lub **Short** dla BTC lub NEAR.
2. Jeśli sytuacja jest neutralna i brak wyraźnej okazji, ustaw pole "signal_triggered" na false.
3. Jeśli widzisz okazję spełniającą rygorystyczne kryteria day tradingu, ustaw "signal_triggered" na true i podaj parametry (Entry, SL, TP, Safe Position Size - uwzględniając brak dźwigni lub niską x2/x3).

Zwróć odpowiedź WYŁĄCZNIE w formacie JSON z kluczami:
- "html_content": pełny kod HTML raportu na stronę https://piwecki.pl/analiza/
- "signal_triggered": boolean (true/false) - czy wysłać alert na Telegram
- "telegram_message": treść powiadomienia na Telegram (tylko jeśli signal_triggered jest true, w przeciwnym razie pusty string).

ZASADY:
- Bezwzględny zakaz stosowania formatowania LaTeX (zakaz ukośników wstecznych typu `\\,`). Ceny zapisuj w czystym formacie tekstowym np. `$81,500` lub `$4.55`.
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

def upload_to_ftp(html_content):
    if not all([FTP_HOST, FTP_USER, FTP_PASS]):
        return
    try:
        with ftplib.FTP_TLS(FTP_HOST) as ftp:
            ftp.login(FTP_USER, FTP_PASS)
            ftp.prot_p()
            dirs = FTP_DIR.split("/")
            for d in dirs:
                if d:
                    try:
                        ftp.cwd(d)
                    except ftplib.error_perm:
                        ftp.mkd(d)
                        ftp.cwd(d)
            with open("index.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            with open("index.html", "rb") as f:
                ftp.storbinary("STOR index.html", f)
            os.remove("index.html")
            print("Raport zaktualizowany na serwerze.")
    except Exception as e:
        print(f"Błąd FTP: {e}")

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not text:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            pass
    except Exception as e:
        print(f"Błąd wysyłki Telegram: {e}")

async def main():
    print("Start cyklu analitycznego BTC & NEAR...")
    async with aiohttp.ClientSession() as session:
        btc_data = await gather_multi_timeframe_data(session, "BTC")
        near_data = await gather_multi_timeframe_data(session, "NEAR")
        market_prices = await fetch_orderbooks(session)
        
        result = await generate_ai_trade_decision(btc_data, near_data, market_prices)
        if not result:
            print("Brak odpowiedzi od modelu AI.")
            return
            
        # Aktualizacja strony HTML na serwerze za każdym razem (archiwum i monitoring)
        upload_to_ftp(result.get("html_content", ""))
        
        # Wysyłka na Telegram TYLKO wtedy, gdy AI wykryje sygnał handlowy
        if result.get("signal_triggered") and result.get("telegram_message"):
            print("Wykryto okazję rynkową! Wysyłam powiadomienie na Telegram...")
            send_telegram_message(result["telegram_message"])
        else:
            print("Brak klarownego sygnału na wejście – pomijam powiadomienie na Telegramie.")

if __name__ == "__main__":
    asyncio.run(main())
