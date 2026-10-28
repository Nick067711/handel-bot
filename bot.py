import os
import json
import ftplib
import asyncio
import aiohttp
import csv
import urllib.request
import urllib.parse
import re
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

# --- KOMPLEKSOWA MACIERZ STRUKTURALNA (ZAKODOWANE DANE Z CSV ORAZ CYKLE 2023-2026) ---
HISTORICAL_STRUCTURE_MATRIX = {
    "global_extremes": {
        "absolute_low_LL": {"price": 0.00170, "date": "2020-03-13", "desc": "Absolutny dołek wszech czasów"},
        "standard_low_LL": {"price": 0.00542, "date": "2020-06-02", "desc": "Najniższy dołek w standardowej płynności"},
        "ATH_highest_HH": {"price": 3.48420, "date": "2021-10-28", "desc": "Absolutny szczyt wszech czasów (ATH)"}
    },
    "historical_waves_and_retracements": [
        {
            "wave_name": "Impuls Styczeń 2021 ($0.26)",
            "start_low": 0.01264, "start_date": "2020-11-04",
            "peak_high": 0.26000, "peak_date": "2021-01-29",
            "correction_low_HL": 0.09666, "correction_date": "2021-02-02",
            "retracement_pct": -62.82, "fibo_zone": "61.8% - 78.6%"
        },
        {
            "wave_name": "Impuls Hossa 2021 ($0.87)",
            "start_low": 0.09666, "start_date": "2021-02-02",
            "peak_high": 0.87797, "peak_date": "2021-02-25",
            "correction_low_HL": 0.15300, "correction_date": "2021-07-20",
            "retracement_pct": -82.57, "fibo_zone": "78.6% - 88.6%"
        },
        {
            "wave_name": "Impuls Wiosna 2024 ($1.22)",
            "start_low": 0.30690, "start_date": "2024-01-23",
            "peak_high": 1.22890, "peak_date": "2024-03-22",
            "correction_low_HL": 0.25750, "correction_date": "2024-08-05",
            "retracement_pct": -79.05, "fibo_zone": "78.6%"
        },
        {
            "wave_name": "Impuls Zima 2024 ($1.47)",
            "start_low": 0.53000, "start_date": "2024-11-04",
            "peak_high": 1.47820, "peak_date": "2024-12-16",
            "correction_low_HL": 0.03653, "correction_date": "2026-02-28",
            "retracement_pct": -97.53, "fibo_zone": "Głęboka re-akumulacja"
        }
    ],
    "structural_progression_2023_2026": [
        {"date": "2023-02-03", "type": "LH", "price": 0.65550},
        {"date": "2023-06-10", "type": "LL", "price": 0.21270},
        {"date": "2023-10-19", "type": "LL", "price": 0.17070},
        {"date": "2023-12-25", "type": "HH", "price": 0.56690},
        {"date": "2024-03-22", "type": "HH", "price": 1.22890},
        {"date": "2024-08-05", "type": "HL", "price": 0.25750},
        {"date": "2024-12-16", "type": "HH", "price": 1.47820},
        {"date": "2025-02-21", "type": "LH", "price": 0.99000},
        {"date": "2025-10-10", "type": "LL", "price": 0.07580},
        {"date": "2025-12-24", "type": "LL", "price": 0.06710},
        {"date": "2026-02-28", "type": "LL", "price": 0.03653},
        {"date": "2026-06-25", "type": "LL", "price": 0.01901},
        {"date": "2026-10-13", "type": "LH", "price": 0.07393}
    ],
    "current_consolidation_matrix": {
        "base_zone_low": 0.0450,
        "base_zone_high": 0.0540,
        "virtual_short_anchor": 0.0700,
        "recent_local_low": 0.03653,
        "recent_local_high": 0.07393
    }
}


# --- 1. SILNIK WSKAŹNIKÓW ---
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


# --- 2. POBIERANIE DANYCH SONIC ---
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
        print(f"Błąd Coinbase Sonic: {e}")

    file_path = "SONIC_FTM_S_1d.csv"
    if os.path.exists(file_path) and not closes:
        try:
            with open(file_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    closes.append(float(row["close"]))
                    highs.append(float(row["high"]))
                    lows.append(float(row["low"]))
                    volumes.append(float(row["volume"]))
        except Exception as e:
            print(f"Błąd odczytu CSV Sonic: {e}")

    current_close = closes[-1] if closes else 0.04788
    current_vol = volumes[-1] if volumes else 1500000.0
    
    short_anchor = HISTORICAL_STRUCTURE_MATRIX["current_consolidation_matrix"]["virtual_short_anchor"]
    pct_from_short = round(((short_anchor - current_close) / short_anchor) * 100, 2)
    diff_from_short = round(short_anchor - current_close, 4)

    return {
        "close": current_close,
        "volume": current_vol,
        "rsi": TradeEngine.calculate_rsi(closes),
        "macd": TradeEngine.calculate_macd(closes),
        "pct_from_short": pct_from_short,
        "diff_from_short_usd": diff_from_short,
        "structural_matrix": HISTORICAL_STRUCTURE_MATRIX
    }


# --- 3. POBIERANIE DANYCH BITCOIN ---
async def fetch_multi_exchange_btc(session):
    exchanges = [
        ("Binance", "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=100"),
        ("OKX", "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1D&limit=100"),
        ("Bybit", "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=D&limit=100")
    ]
    
    closes_pool, volumes_pool = [], []
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
        except Exception:
            pass
            
    if not closes_pool:
        closes_pool = [65000.0] * 30
        volumes_pool = [50000.0] * 30

    avg_close = float(np.mean(closes_pool[-30:]) if len(closes_pool) >= 30 else closes_pool[-1])
    total_vol = float(np.mean(volumes_pool[-30:]) if len(volumes_pool) >= 30 else 50000)
    
    rsi = TradeEngine.calculate_rsi(closes_pool)
    macd = TradeEngine.calculate_macd(closes_pool)

    return {
        "close": round(avg_close, 2),
        "volume": round(total_vol, 2),
        "rsi": rsi,
        "macd_hist": macd["hist"],
        "realized_price_proxy": round(avg_close * 0.92, 2)
    }


# --- 4. ZARZĄDZANIE STANEM ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_html_date": ""}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Błąd zapisu stanu: {e}")


# --- 5. INTELIGENTNY ANALITYK AI Z INTELIGENTNYM WYCINANIEM JSON ---
def generate_reports_and_decide(sonic_metrics, btc_metrics):
    if not GROQ_API_KEY:
        print("Błąd: Brak klucza GROQ_API_KEY")
        return False, None, None, None

    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=GROQ_API_KEY
    )

    current_date_str = datetime.now(timezone.utc).strftime("%d.%m.%Y, %H:%M UTC")

    prompt = f"""
Jesteś głównym, wybitnym analitykiem rynkowym i kwantytatywnym. Przygotuj niezwykle dogłębny, profesjonalny, rozbudowany i merytorycznie bogaty raport rynkowy dla Bitcoina oraz Sonica.
Twoja odpowiedź musi być PRAWIDŁOWYM OBIEKTEM JSON. Nie dodawaj żadnego tekstu przed ani po obiekcie JSON.

Struktura JSON:
{{
  "is_in_buy_or_sell_zone": false,
  "telegram_msg_main": null,
  "telegram_msg_macro": null,
  "html_content": "<!DOCTYPE html><html lang='pl'><head><meta charset='UTF-8'><title>Raport Rynkowy</title>...</head><body>...</body></html>"
}}

WYMOGI DOTYCZĄCE PLIKU HTML:
1. **Data na samej górze**: Na samej górze raportu (w nagłówku głównym lub podtytule) MUSI wyraźnie widnieć aktualna data i czas raportu: "{current_date_str}".
2. **Układ i Stylistyka**: Ciemny motyw (Dark Theme), szerokie marginesy boczne (max-width: 1100px, margin: 40px auto), duży odstęp od góry (padding-top: 40px), nowoczesne karty (cards), elegancka typografia, odpowiednia interlinia i paddingi. Zero "biednych" sekcji – treść musi być wyczerpująca i profesjonalna.
3. **Sekcja Makroekonomiczna Bitcoina (BTC)**: Szczegółowa analiza kondycji lidera rynku (cena ${btc_metrics.get('close')} USD, wolumen wielogiełdowy w USD, RSI {btc_metrics.get('rsi')}, MACD hist {btc_metrics.get('macd_hist')}, szacowany Realized Price Proxy). Omówienie wpływu sytuacji makro (FED/FOMC) na obecną hossę.
4. **Sekcja Analityczna Sonica (S)**: Wysoce profesjonalna, długa i szczegółowa analiza strukturalna:
   - Twarde dane historyczne: Absolutny dołek $0.00170 USD oraz ATH $3.48420 USD (bez wzmianek o pandemii).
   - Analiza falowa i Fibo: Szczegółowe omówienie kluczowych ruchów, w tym impulsu ze stycznia 2021 r. do $0.26 i modelowej korekty do $0.09666 (-62.82%), oraz sekwencji struktur HH/HL/LH/LL z lat 2023-2026.
   - Aktualna pozycja ceny (${sonic_metrics.get('close')} USD), odległość od kluczowego wsparcia bazy ($0.045 - $0.054 USD) oraz oporu / Short Anchora ($0.070 USD).
   - Precyzyjne określenie obecnej fazy rynkowej w trwającej hossie (budowanie bazy, dynamika ruchu ceny i nastroje inwestorów wobec ekosystemu Sonic).
5. **Wolumen i Waluta**: Przy każdym wolumenie wyraźnie wskazana giełda (Coinbase dla Sonica, Agregat dla BTC) oraz waluta USD.
6. **Podpis AI**: Na samym dole stopka z oficjalnym podpisem: "Raport wygenerowany autonomicznie przez model AI: Groq (GPT-OSS-120b)".

DANE WEJŚCIOWE DO ANALIZY:
- Aktualna Data Raportu: {current_date_str}
- BTC: ${btc_metrics.get('close')} USD, Wolumen: {btc_metrics.get('volume')} USD, RSI: {btc_metrics.get('rsi')}, MACD Hist: {btc_metrics.get('macd_hist')}.
- SONIC: ${sonic_metrics.get('close')} USD, Wolumen: {sonic_metrics.get('volume')} (Coinbase, USD), RSI: {sonic_metrics.get('rsi')}, MACD: {sonic_metrics.get('macd')}.
- Odległość od Short Anchor ($0.070 USD): -{sonic_metrics.get('pct_from_short')}% (-{sonic_metrics.get('diff_from_short_usd')} USD).
- Pełna macierz historyczna CSV: {json.dumps(sonic_metrics.get('structural_matrix'), indent=2)}
"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": "Jesteś wybitnym analitykiem finansowym zwracającym wyłącznie poprawny obiekt JSON po polsku."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        raw_content = response.choices[0].message.content.strip()
        
        json_match = re.search(r'(\{[\s\S]*\})', raw_content)
        if json_match:
            json_str = json_match.group(1)
            try:
                data = json.loads(json_str)
                return data.get("is_in_buy_or_sell_zone", False), data.get("telegram_msg_main"), data.get("telegram_msg_macro"), data.get("html_content")
            except Exception as je:
                print(f"Błąd parsowania wyciętego JSON-a: {je}")

        raise ValueError("Nie znaleziono poprawnego obiektu JSON w odpowiedzi modelu.")

    except Exception as e:
        print(f"Błąd zapytania lub parsowania z AI: {e}")
        fallback_html = f"""<!DOCTYPE html>
<html lang="pl">
<head><meta charset="UTF-8"><title>Raport Rynkowy Sonic & BTC</title>
<style>body{{background:#0d1117;color:#c9d1d9;font-family:sans-serif;max-width:1100px;margin:40px auto;padding:40px;}}</style>
</head>
<body>
<h1>Raport Rynkowy Sonic & BTC</h1>
<p><strong>Data raportu:</strong> {current_date_str}</p>
<p>Trwa aktualizowanie pełnej analizy kwantytatywnej przez model AI w kolejnym cyklu.</p>
</body></html>"""
        return False, None, None, fallback_html


# --- 6. FTP & TELEGRAM ---
def upload_to_ftp(html_content):
    if not all([FTP_HOST, FTP_USER, FTP_PASS]) or not html_content:
        return False
        
    try:
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
            print("Sukces! Rozbudowany raport HTML zaktualizowany na serwerze: https://piwecki.pl/analiza/")
            return True
    except Exception as e:
        print(f"Błąd FTP: {e}")
        return False

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not text:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            pass
    except Exception as e:
        print(f"Błąd wysyłki Telegram: {e}")


# --- 7. GŁÓWNA PĘTLA ---
async def main():
    print("Uruchamianie bota: pełna analiza CSV + kontekst BTC + pogłębiony model AI...")
    state = load_state()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    async with aiohttp.ClientSession() as session:
        sonic_metrics = await fetch_and_analyze_sonic(session)
        btc_metrics = await fetch_multi_exchange_btc(session)
        
        is_in_zone, main_msg, macro_msg, html_content = generate_reports_and_decide(sonic_metrics, btc_metrics)
        
        if html_content:
            if upload_to_ftp(html_content):
                state["last_html_date"] = today_str
        
        if is_in_zone and main_msg:
            send_telegram_message(main_msg)
            print("Wysłano powiadomienie strefowe na Telegram!")
            
        if macro_msg:
            await asyncio.sleep(1)
            send_telegram_message(macro_msg)
            
        save_state(state)

    print("Cykl zakończony pomyślnie.")

if __name__ == "__main__":
    asyncio.run(main())
