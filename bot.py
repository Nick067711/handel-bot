import os
import json
import ftplib
import asyncio
import aiohttp
import urllib.request
import urllib.parse
import numpy as np
from datetime import datetime, timezone
from openai import OpenAI

# --- KONFIGURACJA ŚRODOWISKOWA / API ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

FTP_HOST = os.getenv("FTP_HOST", "piwecki.pl")
FTP_USER = os.getenv("FTP_USER", "")
FTP_PASS = os.getenv("FTP_PASS", "")
FTP_DIR = os.getenv("FTP_DIR", "domains/piwecki.pl/public_html/analiza")

STATE_FILE = "sonic_signal_state.json"

# --- 1. SILNIK ANALIZY TECHNICZNEJ (AN-1 / Fibo / Dywergencje / Wskaźniki) ---
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


# --- 2. POBIERANIE SZCZEGÓŁOWYCH DANYCH RYNKOWYCH ---
async def fetch_coinbase_sonic(session):
    url = "https://api.exchange.coinbase.com/products/S-USD/candles?granularity=86400"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, headers=headers, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and len(data) > 0:
                    closes = [float(c[4]) for c in data[::-1]]
                    volumes = [float(c[5]) for c in data[::-1]]
                    highs = [float(c[2]) for c in data[::-1]]
                    lows = [float(c[1]) for c in data[::-1]]
                    current_close = closes[-1]
                    
                    all_time_low = min(lows) if lows else 0.0023
                    all_time_high = max(highs) if highs else 3.3092
                    
                    pct_to_low = round(((current_close - all_time_low) / all_time_low) * 100, 2)
                    diff_to_low_usdc = round(current_close - all_time_low, 4)
                    
                    pct_to_high = round(((all_time_high - current_close) / current_close) * 100, 2)
                    diff_to_high_usdc = round(all_time_high - current_close, 4)

                    return {
                        "closes": closes,
                        "volumes": volumes,
                        "highs": highs,
                        "lows": lows,
                        "close": current_close,
                        "volume": volumes[-1],
                        "atl": all_time_low,
                        "ath": all_time_high,
                        "pct_to_low": pct_to_low,
                        "diff_to_low_usdc": diff_to_low_usdc,
                        "pct_to_high": pct_to_high,
                        "diff_to_high_usdc": diff_to_high_usdc
                    }
    except Exception as e:
        print(f"Coinbase Fetch error (Sonic): {e}")
    
    return {
        "closes": [0.054, 0.055, 0.0541],
        "volumes": [1500000.0],
        "highs": [0.056],
        "lows": [0.052],
        "close": 0.0541,
        "volume": 1500000.0,
        "atl": 0.0023,
        "ath": 3.3092,
        "pct_to_low": 2250.0,
        "diff_to_low_usdc": 0.0518,
        "pct_to_high": 6025.0,
        "diff_to_high_usdc": 3.2551
    }

async def fetch_binance_btc_comprehensive(session):
    """Pobiera wielorezolucyjne dane BTC (1w, 1d, 4h, 1h) z Binance wraz z wolumenem, transakcjami i wyliczanym Realized Price proxy."""
    timeframes = {"1w": "1w", "1d": "1d", "4h": "4h", "1h": "1h"}
    btc_data = {}
    
    for tf_key, tf_val in timeframes.items():
        url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={tf_val}&limit=100"
        try:
            async with session.get(url, timeout=8) as resp:
                if resp.status == 200:
                    kline_raw = await resp.json()
                    if isinstance(kline_raw, list) and len(kline_raw) > 0:
                        # Binance kline format:
                        # [0: open_time, 1: open, 2: high, 3: low, 4: close, 5: volume, 6: close_time,
                        #  7: quote_asset_volume, 8: number_of_trades, 9: taker_buy_base_volume, 10: taker_buy_quote_volume]
                        opens = [float(k[1]) for k in kline_raw]
                        highs = [float(k[2]) for k in kline_raw]
                        lows = [float(k[3]) for k in kline_raw]
                        closes = [float(k[4]) for k in kline_raw]
                        volumes = [float(k[5]) for k in kline_raw]
                        quote_volumes = [float(k[7]) for k in kline_raw]
                        trades_count = [int(k[8]) for k in kline_raw]
                        taker_buy_base = [float(k[9]) for k in kline_raw]
                        taker_buy_quote = [float(k[10]) for k in kline_raw]
                        
                        rsi = TradeEngine.calculate_rsi(closes)
                        macd = TradeEngine.calculate_macd(closes)
                        ema20 = TradeEngine.calculate_ema(closes, 20)
                        ema50 = TradeEngine.calculate_ema(closes, 50)
                        ema200 = TradeEngine.calculate_ema(closes, 200 if len(closes) >= 200 else len(closes))
                        
                        btc_data[tf_key] = {
                            "close": closes[-1],
                            "open": opens[-1],
                            "high": highs[-1],
                            "low": lows[-1],
                            "volume": volumes[-1],
                            "quote_volume": quote_volumes[-1],
                            "trades": trades_count[-1],
                            "taker_buy_base": taker_buy_base[-1],
                            "taker_buy_quote": taker_buy_quote[-1],
                            "rsi": rsi,
                            "macd": macd,
                            "ema20": ema20,
                            "ema50": ema50,
                            "ema200": ema200,
                            "closes_history": closes
                        }
        except Exception as e:
            print(f"Binance BTC Fetch error ({tf_key}): {e}")
            
    # Jeśli brak danych, uzupełnij awaryjnie
    if not btc_data:
        btc_data["1d"] = {
            "close": 80000.0, "volume": 50000.0, "quote_volume": 4000000000.0,
            "trades": 1200000, "taker_buy_base": 25000.0, "taker_buy_quote": 2000000000.0,
            "rsi": 50.0, "macd": {"macd": 0, "signal": 0, "hist": 0}, "ema20": 79000.0, "ema50": 75000.0, "ema200": 65000.0,
            "closes_history": [80000.0]
        }
        
    # Obliczenie przybliżonego wskaźnika Realized Price proxy (średnia ważona wolumenem z dłuższego okresu np. 1D VWAP / koszt bazowy)
    d1_closes = btc_data.get("1d", {}).get("closes_history", [80000.0])
    # Realized price proxy jako długoterminowy VWAP lub wykładnicza średnia cena realizacji
    realized_price_proxy = round(float(np.mean(d1_closes[-90:]) * 0.92), 2) if len(d1_closes) >= 90 else round(float(np.mean(d1_closes) * 0.95), 2)
    btc_data["realized_price_proxy"] = realized_price_proxy
    
    return btc_data


# --- 3. ZARZĄDZANIE STANEM SYGNAŁÓW I HARMONOGRAMEM ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_type": "SHORT", "last_html_date": "", "last_alert_text": ""}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Błąd zapisu stanu: {e}")


# --- 4. GENEROWANIE RAPORTÓW PRZEZ AI (GPT-OSS 120B) ---
def generate_reports(sonic_data, btc_data, last_required_type, should_generate_html, active_alert_text):
    if not GROQ_API_KEY:
        print("Błąd: Brak klucza GROQ_API_KEY.")
        return None, None, None

    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=GROQ_API_KEY
    )

    btc_1d_rsi = btc_data.get("1d", {}).get("rsi", 50.0)
    btc_1d_hist = btc_data.get("1d", {}).get("macd", {}).get("hist", 0.0)
    
    macro_status = "NEUTRALNY"
    if btc_1d_rsi >= 72 and btc_1d_hist < 0:
        macro_status = "SZCZYT HOSSY"
    elif btc_1d_rsi <= 32 and btc_1d_hist > 0:
        macro_status = "DNO BESSY"

    prompt = f"""
Jesteś profesjonalnym analitykiem kryptowalutowym. Wykorzystaj pełną wiedzę z backtestu (fale Eliotta, zniesienia Fibonacciego 0.618-0.786, poziomy HH, HL, LH, LL, RSI, MACD, dywergencje, dane wolumenowe, transakcyjne oraz Realized Price). Przygotuj:
1. Alert cenowy dla Sonica (S) z Coinbase z wymuszonym typem: {last_required_type} na Telegram.
2. Alert makro dla BTC na Telegram (wyłącznie jeśli status cyklu to Szczyt Hossy lub Dno Bessy). Status: {macro_status}.
3. {"Dla raportu dziennego HTML na stronie https://piwecki.pl/analiza/ wygeneruj pełną stronę HTML wyłącznie dla BTC oraz Sonic (S). Raport musi zawierać kompletne dane techniczne BTC (ceny, wolumeny, transakcje, szacowany Realized Price, wskaźniki wielorezolucyjne 1w/1d/4h/1h), dane Sonica (w tym położenie procentowe i kwotowe w USDC względem dołka/szczytu), dogłębną analizę makroekonomiczną BTC, najnowsze newsy i sentyment wokół ekosystemu Sonic oraz – jeśli w tym cyklu wygenerowano alert cenowy – WYRAŹNY, CZERWONY POGRUBIONY KOMUNIKAT ALERTU w górnej części strony." if should_generate_html else "HTML: zwróć null."}

KOMUNIKAT AKTUALNEGO ALERTU CENOWEGO (jeśli występuje w tym cyklu): {active_alert_text}

KOMPREHENSYWNE DANE BTC (Binance):
{json.dumps(btc_data, indent=2)}

DANE SONIC (Coinbase):
{json.dumps(sonic_data, indent=2)}
Położenie Sonica: {sonic_data.get('pct_to_low')}% powyżej dołka ({sonic_data.get('diff_to_low_usdc')} USDC), {sonic_data.get('pct_to_high')}% do szczytu ({sonic_data.get('diff_to_high_usdc')} USDC).

POZIOMY STRUKTURALNE SONICA:
- Opory / LH: $0.8665, $1.4313
- Wsparcia / HL / LL: $0.2622, $0.3952, $0.054

Zwróć odpowiedź WYŁĄCZNIE w formacie JSON z kluczami:
- "telegram_msg_main": Alert cenowy dla Sonica (HTML Telegram).
- "telegram_msg_macro": Alert makro BTC (HTML Telegram, lub null jeśli status neutralny).
- "html_content": Kompletny kod strony HTML (lub null).
"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": "Jesteś analitykiem kryptowalut zwracającym dane wyłącznie w formacie JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.2
        )
        data = json.loads(response.choices[0].message.content)
        return data.get("telegram_msg_main"), data.get("telegram_msg_macro"), data.get("html_content")
    except Exception as e:
        print(f"Błąd zapytania do AI: {e}")
        return None, None, None


# --- 5. FTP & TELEGRAM ---
def upload_to_ftp(html_content):
    if not all([FTP_HOST, FTP_USER, FTP_PASS]) or not html_content:
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
                ftp.storbinary("STOR index.html", f)
                
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


# --- 6. GŁÓWNA FUNKCJA ---
async def main():
    print("Start systemu analitycznego (Dzienny HTML BTC+Sonic z kompleksowymi danymi cenowymi, wolumenowymi, transakcyjnymi i Realized Price)...")
    
    state = load_state()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    should_generate_html = (state.get("last_html_date") != today_str)
    
    async with aiohttp.ClientSession() as session:
        sonic_metrics = await fetch_coinbase_sonic(session)
        btc_metrics = await fetch_binance_btc_comprehensive(session)
        
        last_type = state.get("last_type", "SHORT")
        next_type = "LONG" if last_type == "SHORT" else "SHORT"
        
        # Przygotowanie treści alertu cenowego dla pliku HTML
        active_alert_text = f"NOWY SYGNAŁ CENOWY: {next_type} dla Sonica (S)"
        
        main_msg, macro_msg, html_content = generate_reports(
            sonic_metrics, btc_metrics, next_type, should_generate_html, active_alert_text
        )
        
        # Aktualizacja strony HTML na FTP (zawierającej również komunikat alertu w czerwonej pogrubionej czcionce)
        if should_generate_html and html_content:
            if upload_to_ftp(html_content):
                state["last_html_date"] = today_str
        
        # Wysyłka alertu dla Sonica na Telegram
        if main_msg:
            send_telegram_message(main_msg)
            state["last_type"] = next_type
            print(f"Wysłano alert cenowy Sonica ({next_type}) na Telegram.")
            
        # Wysyłka alertu Makro BTC na Telegram (tylko Szczyt Hossy / Dno Bessy)
        if macro_msg and ("SZCZYT HOSSY" in macro_msg or "DNO BESSY" in macro_msg):
            await asyncio.sleep(1.5)
            send_telegram_message(macro_msg)
            print("Wysłano alert Makro BTC na Telegram.")
            
        save_state(state)

    print("Cykl analityczny zakończony.")

if __name__ == "__main__":
    asyncio.run(main())
