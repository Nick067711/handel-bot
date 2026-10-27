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

# --- PEŁNA MACIERZ POZIOMÓW HISTORYCZNYCH I STRUKTURY Z PLIKÓW CSV ---
HISTORICAL_STRUCTURE_MATRIX = {
    "LL_historical_standard": {"price": 0.0090, "date": "2019-11-25", "desc": "Najniższy poziom historyczny (z pominięciem anomalii COVID)"},
    "HL_base_accumulation": {"price": 0.0450, "date": "Bieżąca baza", "desc": "Dolna granica obecnej strefy konsolidacji"},
    "HL_support_recent": {"price": 0.0540, "date": "Bieżąca baza", "desc": "Górna granica pasma bazy"},
    "Virtual_Short_Anchor": {"price": 0.0700, "date": "Wrzesień/Październik 2026", "desc": "Wirtualny Short Anchor (szczyt lokalny)"},
    "LH_resistance_2024_march": {"price": 1.2289, "date": "2024-03-22", "desc": "Kluczowy opor / szczyt z marca 2024"},
    "HH_resistance_2024_december": {"price": 1.4782, "date": "2024-12-16", "desc": "Szczyt impulsowy z grudnia 2024"},
    "ATH_historical": {"price": 3.4842, "date": "2021-10-28", "desc": "Maksimum makro hossy"}
}


# --- 1. SILNIK MATEMATYCZNO-WSKAŹNIKOWY ---
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
        print(f"Błąd pobierania Coinbase (Sonic): {e}")

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
            print(f"Błąd odczytu pliku CSV Sonic: {e}")

    current_close = closes[-1] if closes else 0.0478
    current_vol = volumes[-1] if volumes else 1500000.0
    
    short_anchor = HISTORICAL_STRUCTURE_MATRIX["Virtual_Short_Anchor"]["price"]
    pct_to_short_anchor = round(((short_anchor - current_close) / short_anchor) * 100, 2)
    diff_to_short_anchor_usdc = round(short_anchor - current_close, 4)

    return {
        "close": current_close,
        "volume": current_vol,
        "rsi": TradeEngine.calculate_rsi(closes),
        "macd": TradeEngine.calculate_macd(closes),
        "pct_to_short_anchor": pct_to_short_anchor,
        "diff_to_short_anchor_usdc": diff_to_short_anchor_usdc,
        "structural_matrix": HISTORICAL_STRUCTURE_MATRIX
    }


# --- 3. POBIERANIE DANYCH BTC (WIELOGIEŁDOWE) ---
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
        closes_pool = [65000.0] * 30
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
    return {"last_type": "SHORT", "last_short_price": 0.070, "target_signal": "LONG", "last_html_date": ""}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Błąd zapisu stanu: {e}")


# --- 5. CENTRUM DECYZYJNE AI (GROQ / GPT-OSS-120B) ---
def generate_reports_and_decide(sonic_metrics, btc_metrics, target_signal):
    if not GROQ_API_KEY:
        print("Błąd: Brak klucza GROQ_API_KEY.")
        return False, None, None, None

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

    prompt = f"""
Jesteś najwyższej klasy analitykiem rynkowym (Model: Groq GPT-OSS-120b). Przeanalizuj poniższe dane i podejmij decyzję inwestycyjną.
Strategia: Start od wirtualnego SHORT z poziomu $0.070. Szukamy zwrotu na LONG w strefie bazy.

ZASADY RAPORTU HTML:
1. Marginesy i układ: Strona musi mieć szerokie marginesy boczne (np. max-width: 1000px, margin: 40px auto) oraz duży odstęp/oddech od góry. Czysty, nowoczesny Dark Theme, bardzo czytelna typografia.
2. Kierunek analizy: Znajdujemy się w fazie korygowania spadkowego od Shorta $0.070 w stronę bazy. Wszelkie wskaźniki postępu i cele mają pokazywać dążenie ceny w dół do bazy wsparcia (np. $0.045 - $0.054), a NIE w górę do odległych oporów.
3. Poziomy strukturalne: Wymień absolutny najniższy i najwyższy poziom historyczny (z datami, pomijając anomalię COVID) oraz najbliższe poziomy historyczne względem obecnej ceny.
4. Wolumen i waluta: Przy wolumenach wyraźnie zaznacz giełdę (np. Coinbase / wielogiełdowy agregat) oraz dopisz, że wartości są w USD (dolarach amerykańskich).
5. Podpis: Na samym dole strony umieść wyraźny podpis: "Raport wygenerowany autonomicznie przez model AI: Groq (GPT-OSS-120b)".

DANE WEJŚCIOWE:
- BTC: ${btc_metrics.get('close')} USD, RSI {btc_rsi}, MACD Hist {btc_hist}, Status: {macro_status}.
- SONIC: ${sonic_metrics.get('close')} USD, Wolumen: {sonic_metrics.get('volume')} (Coinbase, w USD), RSI {sonic_metrics.get('rsi')}, MACD {sonic_metrics.get('macd')}.
- Odległość od Shorta $0.070: -{sonic_metrics.get('pct_to_short_anchor')}% (-{sonic_metrics.get('diff_to_short_anchor_usdc')} USD).
- Macierz poziomów: {json.dumps(sonic_metrics.get('structural_matrix'), indent=2)}

ZWRÓĆ ODPOWIEDŹ WYŁĄCZNIE W FORMATZE JSON (W CAŁOŚCI PO POLSKU):
- "is_signal_trigger": (true / false)
- "telegram_msg_main": (Tylko jeśli is_signal_trigger==true, inaczej null) PO POLSKU.
- "telegram_msg_macro": (Tylko jeśli macro_status to SZCZYT HOSSY / DNO BESSY, inaczej null) PO POLSKU.
- "html_content": Kompletny kod HTML spełniający powyższe kryteria wizualne i merytoryczne (W CAŁOŚCI PO POLSKU).
"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": "Jesteś ekspertem rynkowym generującym profesjonalny HTML i analizy wyłącznie po polsku."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.2
        )
        data = json.loads(response.choices[0].message.content)
        return data.get("is_signal_trigger", False), data.get("telegram_msg_main"), data.get("telegram_msg_macro"), data.get("html_content")
    except Exception as e:
        print(f"Błąd zapytania do AI: {e}")
        return False, None, None, None


# --- 6. FTP & TELEGRAM ---
def upload_to_ftp(html_content):
    if not all([FTP_HOST, FTP_USER, FTP_PASS]) or not html_content:
        return False
        
    try:
        print(f"Łączenie z FTP: {FTP_HOST}...")
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
            print("Sukces! Raport HTML zaktualizowany: https://piwecki.pl/analiza/")
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


# --- 7. GŁÓWNA PĘTLA ---
async def main():
    print("Start monitora (Poprawiona estetyka HTML, poprawny kierunek celów w dół do bazy, podpis AI)...")
    
    state = load_state()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    should_generate_html = (state.get("last_html_date") != today_str)
    
    async with aiohttp.ClientSession() as session:
        sonic_metrics = await fetch_and_analyze_sonic(session)
        btc_metrics = await fetch_multi_exchange_btc(session)
        
        target_signal = state.get("target_signal", "LONG")
        
        is_trigger, main_msg, macro_msg, html_content = generate_reports_and_decide(
            sonic_metrics, btc_metrics, target_signal
        )
        
        if should_generate_html and html_content:
            if upload_to_ftp(html_content):
                state["last_html_date"] = today_str
        
        if is_trigger and main_msg:
            send_telegram_message(main_msg)
            state["last_type"] = "LONG"
            state["target_signal"] = "SHORT"
            print("Sygnał LONG wygenerowany przez AI! Wysłano na Telegram.")
        else:
            print("Brak sygnału (oczekiwanie w strefie bazy od shorta $0.070) – zaktualizowano czytelny raport HTML.")
            
        if macro_msg and ("SZCZYT HOSSY" in macro_msg or "DNO BESSY" in macro_msg):
            await asyncio.sleep(1.5)
            send_telegram_message(macro_msg)
            print("Wysłano alert Makro BTC.")
            
        save_state(state)

    print("Cykl zakończony.")

if __name__ == "__main__":
    asyncio.run(main())
