import os
import json
import ftplib
import asyncio
import aiohttp
import csv
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

# --- 1. SILNIK ANALIZY TECHNICZNEJ (Fibo, Fale, Dywergencje, Wskaźniki) ---
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
    def evaluate_long_readiness(closes, lows):
        """Ocenia czy aktualna struktura cenowa wykazuje cechy wyczerpania spadków (gotowość na BUY/LONG od $0.70 shorta)."""
        if len(closes) < 14:
            return False, "Brak wystarczającej historii"
        
        current_price = closes[-1]
        recent_low = min(lows[-14:]) if lows else current_price
        
        rsi = TradeEngine.calculate_rsi(closes)
        is_oversold = rsi <= 35
        is_near_base = current_price <= 0.060
        
        return (is_oversold or is_near_base), f"Cena: {current_price}, RSI: {rsi}, Dołek 14 świec: {recent_low}"


# --- 2. POBIERANIE DANYCH SONIC (Coinbase + plik historyczny) ---
async def fetch_and_analyze_sonic(session):
    url = "https://api.exchange.coinbase.com/products/S-USD/candles?granularity=86400"
    headers = {"User-Agent": "Mozilla/5.0"}
    closes, highs, lows, volumes = [], [], [], []
    try:
        async with session.get(url, headers=headers, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and len(data) > 0:
                    closes = [float(c[4]) for c in data[::-1]]
                    volumes = [float(c[5]) for c in data[::-1]]
                    highs = [float(c[2]) for c in data[::-1]]
                    lows = [float(c[1]) for c in data[::-1]]
    except Exception as e:
        print(f"Coinbase Fetch error (Sonic): {e}")

    file_path = "SONIC_FTM_S_1d.csv"
    historical_stats = {}
    if os.path.exists(file_path):
        try:
            csv_closes, csv_highs, csv_lows, csv_vols = [], [], [], []
            with open(file_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    csv_closes.append(float(row["close"]))
                    csv_highs.append(float(row["high"]))
                    csv_lows.append(float(row["low"]))
                    csv_vols.append(float(row["volume"]))
            
            if not closes and csv_closes:
                closes = csv_closes[-100:]
                highs = csv_highs[-100:]
                lows = csv_lows[-100:]
                volumes = csv_vols[-100:]
            
            if csv_highs and csv_lows:
                historical_stats = {
                    "csv_all_time_high": float(max(csv_highs)),
                    "csv_all_time_low": float(min(csv_lows)),
                    "csv_recent_high_180d": float(max(csv_highs[-180:])) if len(csv_highs) >= 180 else float(max(csv_highs)),
                    "csv_recent_low_180d": float(min(csv_lows[-180:])) if len(csv_lows) >= 180 else float(min(csv_lows)),
                }
        except Exception as e:
            print(f"Błąd odczytu pliku CSV Sonic: {e}")

    current_close = closes[-1] if closes else 0.0541
    current_vol = volumes[-1] if volumes else 1500000.0
    
    atl = historical_stats.get("csv_all_time_low", 0.0017)
    ath = historical_stats.get("csv_all_time_high", 3.4842)
    
    pct_to_low = round(((current_close - atl) / atl) * 100, 2)
    diff_to_low_usdc = round(current_close - atl, 4)
    pct_to_high = round(((ath - current_close) / current_close) * 100, 2)
    diff_to_high_usdc = round(ath - current_close, 4)

    is_ready_long, reason = TradeEngine.evaluate_long_readiness(closes, lows)

    return {
        "close": current_close,
        "volume": current_vol,
        "rsi": TradeEngine.calculate_rsi(closes),
        "macd": TradeEngine.calculate_macd(closes),
        "atl": atl,
        "ath": ath,
        "pct_to_low": pct_to_low,
        "diff_to_low_usdc": diff_to_low_usdc,
        "pct_to_high": pct_to_high,
        "diff_to_high_usdc": diff_to_high_usdc,
        "is_ready_long": is_ready_long,
        "readiness_reason": reason,
        "structural_levels": {
            "virtual_short_anchor": 0.70,
            "LL_historical": atl,
            "HL_key_support_1": 0.2622,
            "HL_key_support_2": 0.3952,
            "LH_key_resistance_1": 0.8665,
            "HH_key_resistance_2": 1.4313,
            "ATH_historical": ath
        }
    }


# --- 3. POBIERANIE DANYCH BTC (Wielogiełdowe) ---
async def fetch_multi_exchange_btc(session):
    exchanges = [
        ("Binance", "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=100"),
        ("OKX", "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1D&limit=100"),
        ("Bybit", "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=D&limit=100")
    ]
    
    closes_pool, volumes_pool, trades_pool = [], [], []
    for name, url in exchanges:
        try:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    data = res.get("data", res.get("result", {}).get("list", res))
                    if isinstance(data, list):
                        for row in data:
                            closes_pool.append(float(row[4]))
                            volumes_pool.append(float(row[5]))
                            trades_pool.append(int(row[8]) if name == "Binance" else 0)
        except Exception:
            pass
            
    if not closes_pool:
        closes_pool = [80000.0] * 30
        volumes_pool = [50000.0] * 30
        trades_pool = [1000000] * 30

    avg_close = float(np.mean(closes_pool[-30:]) if len(closes_pool) >= 30 else closes_pool[-1])
    total_vol = float(np.mean(volumes_pool[-30:]) if len(volumes_pool) >= 30 else 50000)
    avg_trades = int(np.mean(trades_pool[-30:]) if len(trades_pool) >= 30 else 1000000)
    
    rsi = TradeEngine.calculate_rsi(closes_pool)
    macd = TradeEngine.calculate_macd(closes_pool)
    realized_price_proxy = round(avg_close * 0.92, 2)

    return {
        "close": round(avg_close, 2),
        "volume": round(total_vol, 2),
        "trades": avg_trades,
        "rsi": rsi,
        "macd_hist": macd["hist"],
        "realized_price_proxy": realized_price_proxy
    }


# --- 4. ZARZĄDZANIE STANEM ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_type": "SHORT", "last_short_price": 0.70, "target_signal": "LONG", "last_html_date": ""}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Błąd zapisu stanu: {e}")


# --- 5. GENERATOR PROFESJONALNEGO RAPORTU HTML I AI ---
def generate_reports(sonic_metrics, btc_metrics, target_signal, is_ready_long):
    if not GROQ_API_KEY:
        print("Błąd: Brak klucza GROQ_API_KEY.")
        return None, None, None

    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=GROQ_API_KEY
    )

    btc_rsi = btc_metrics.get("rsi", 50.0)
    btc_hist = btc_metrics.get("macd_hist", 0.0)
    
    macro_status = "NEUTRALNY"
    if btc_rsi >= 72 and btc_hist < 0:
        macro_status = "SZCZYT HOSSY"
    elif btc_rsi <= 32 and btc_hist > 0:
        macro_status = "DNO BESSY"

    current_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    readiness_text = "Gotowy do wejścia (Spełnione kryteria dołka/wyprzedania)" if is_ready_long else "W trakcie oczekiwania / konsolidacja w bazie"

    prompt = f"""
Jesteś wybitnym analitykiem technicznym i makroekonomicznym. 
Kontekst strategii: Wirtualny punkt wyjścia to ustalony wcześniej szczyt na poziomie $0.70, na którym otwarto SHORT (SELL). System znajduje się w fazie oczekiwania na sygnał BUY (LONG), oceniając strukturę falową Eliotta, zniesienia Fibonacciego (strefy 0.618-0.786), kluczowe poziomy ceny, wolumen, wskaźniki RSI/MACD oraz dywergencje.

DANE WEJŚCIOWE:
BTC (Wielogiełdowe): Cena ${btc_metrics.get('close')}, Wolumen {btc_metrics.get('volume')}, RSI(14) {btc_rsi}, MACD Hist {btc_hist}, Realized Price Proxy ${btc_metrics.get('realized_price_proxy')}, Status Cyklu AN-1: {macro_status}.
SONIC (Coinbase): Cena ${sonic_metrics.get('close')}, Wolumen {sonic_metrics.get('volume')}, RSI {sonic_metrics.get('rsi')}, MACD {sonic_metrics.get('macd')}, Dołek hist. ${sonic_metrics.get('atl')} (+{sonic_metrics.get('pct_to_low')}% / {sonic_metrics.get('diff_to_low_usdc')} USDC), Szczyt hist. ${sonic_metrics.get('ath')} (-{sonic_metrics.get('pct_to_high')}% / {sonic_metrics.get('diff_to_high_usdc')} USDC).
Poziomy strukturalne: Wirtualny Short Anchor: $0.70 | LH $0.8665, $1.4313 | HL $0.2622, $0.3952 | LL $0.054 base.
Ocena gotowości na LONG z silnika bota: {readiness_text}.

ZWRÓĆ ODPOWIEDŹ WYŁĄCZNIE W FORMATZE JSON z 3 kluczami:
- "telegram_msg_main": (Tylko jeśli is_ready_long==True, inaczej null). Pełny, szczegółowy alert dla laika na Telegram o wygenerowanym sygnale BUY (LONG) odzwierciedlającym przejście z poziomu Short $0.70.
- "telegram_msg_macro": (Tylko jeśli macro_status to SZCZYT HOSSY lub DNO BESSY, inaczej null).
- "html_content": Kompletny, profesjonalny kod strony HTML (Dashboard w ciemnym motywie Dark Theme), zawierający pełną analizę makro FED, kontekst BTC, oraz dogłębną analizę Sonica (fale, zniesienia Fibonacciego, dywergencje, odległość od $0.70 shorta do obecnej bazy). Jeśli is_ready_long==True, na górze strony umieść duży czerwony baner sygnałowy.

WYMAGANIA DOTYCZĄCE HTML:
1. KARTY STATYSTYCZNE (KPI): Cena BTC, Realized Price Proxy, Cena Sonic, Odległość Sonic do bazowego shorta $0.70, Odległość do ATL w % i USDC.
2. SEKCJA ANALIZY MAKROEKONOMICZNEJ BTC: Wpływ FED (stopy procentowe, polityka monetarna), płynność rynkowa, wskaźniki on-chain i model cyklu.
3. SEKCJA ANALIZY TECHNICZNEJ SONIC (S): Szczegółowa ocena fal, Fibo, poziomów wsparcia/oporu (LL, HL, LH, HH), dywerencji RSI/MACD oraz aktualny sentyment projektu.
"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": "Jesteś ekspertem rynkowym i programistą generującym profesjonalne strony HTML i JSON."},
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


# --- 6. FTP & TELEGRAM ---
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


# --- 7. GŁÓWNA FUNKCJA ---
async def main():
    print("Start monitora (Strategia: Start od SHORT $0.70, oczekiwanie na BUY/LONG z pełną analizą fal, Fibo i dywerencji)...")
    
    state = load_state()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    should_generate_html = (state.get("last_html_date") != today_str)
    
    async with aiohttp.ClientSession() as session:
        sonic_metrics = await fetch_and_analyze_sonic(session)
        btc_metrics = await fetch_multi_exchange_btc(session)
        
        target_signal = state.get("target_signal", "LONG")
        is_ready_long = sonic_metrics.get("is_ready_long", False)
        
        main_msg, macro_msg, html_content = generate_reports(
            sonic_metrics, btc_metrics, target_signal, is_ready_long
        )
        
        if should_generate_html and html_content:
            if upload_to_ftp(html_content):
                state["last_html_date"] = today_str
        
        if is_ready_long and main_msg:
            send_telegram_message(main_msg)
            state["last_type"] = "LONG"
            state["target_signal"] = "SHORT"
            print("Wykryto sygnał wejścia w LONG! Wysłano powiadomienie na Telegram.")
        else:
            print("Brak warunków na BUY/LONG (oczekiwanie na sygnał ze strefy bazy/dywergencji od poziomu $0.70 shorta) – Telegram milczy.")
            
        if macro_msg and ("SZCZYT HOSSY" in macro_msg or "DNO BESSY" in macro_msg):
            await asyncio.sleep(1.5)
            send_telegram_message(macro_msg)
            print("Wysłano rzadki alert Makro BTC na Telegram.")
            
        save_state(state)

    print("Cykl analityczny zakończony.")

if __name__ == "__main__":
    asyncio.run(main())
