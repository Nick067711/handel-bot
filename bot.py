import os
import requests
import json
import smtplib
from email.mime.text import MIMEText
import google.generativeai as genai
from datetime import datetime

# ==========================================
# KONFIGURACJA ODBIORCÓW I DOSTĘPÓW
# ==========================================
EMAIL_TO = "nick06711@gmail.com"

# Dane SMTP
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER", "nick06711@gmail.com")
SMTP_PASS = os.getenv("SMTP_PASS")

# Konfiguracja Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

MIN_VOLUME_USD = 5000000  # Minimalny wolumen USD (dla pozycji 20-30k USD)

# ==========================================
# 1. POBIERANIE DANYCH RYNKOWYCH
# ==========================================
def fetch_okx_data(inst_id):
    try:
        url = f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"
        res = requests.get(url, timeout=10).json()
        if res.get("code") == "0" and res.get("data"):
            return res["data"][0]
    except Exception as e:
        print(f"Błąd OKX ticker dla {inst_id}: {e}")
    return None

def fetch_okx_open_interest(inst_id):
    try:
        url = f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst_id}"
        res = requests.get(url, timeout=10).json()
        if res.get("code") == "0" and res.get("data"):
            return res["data"][0]
    except Exception as e:
        print(f"Błąd OKX OI dla {inst_id}: {e}")
    return None

def fetch_okx_funding_rate(inst_id):
    try:
        url = f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"
        res = requests.get(url, timeout=10).json()
        if res.get("code") == "0" and res.get("data"):
            return res["data"][0]
    except Exception as e:
        print(f"Błąd OKX Funding dla {inst_id}: {e}")
    return None

def fetch_binance_liquidations(symbol="BTCUSDT"):
    try:
        url = f"https://fapi.binance.com/fapi/v1/forceOrders?symbol={symbol}&limit=20"
        res = requests.get(url, timeout=10).json()
        if isinstance(res, list):
            long_liqs = sum(float(x['origQty']) * float(x['price']) for x in res if x['side'] == 'SELL')
            short_liqs = sum(float(x['origQty']) * float(x['price']) for x in res if x['side'] == 'BUY')
            return {"long_liqs": long_liqs, "short_liqs": short_liqs}
    except Exception as e:
        print(f"Błąd likwidacji Binance dla {symbol}: {e}")
    return {"long_liqs": 0.0, "short_liqs": 0.0}

def get_market_snapshot():
    btc_ticker = fetch_okx_data("BTC-USDT-SWAP")
    strk_ticker = fetch_okx_data("STRK-USDT-SWAP")
    btc_oi = fetch_okx_open_interest("BTC-USDT-SWAP")
    strk_oi = fetch_okx_open_interest("STRK-USDT-SWAP")
    btc_funding = fetch_okx_funding_rate("BTC-USDT-SWAP")
    strk_funding = fetch_okx_funding_rate("STRK-USDT-SWAP")
    
    btc_liqs = fetch_binance_liquidations("BTCUSDT")
    strk_liqs = fetch_binance_liquidations("STRKUSDT")

    # Skanowanie płynnych par SWAP na OKX pod kątem zmian 24h
    coins_data = []
    try:
        url = "https://www.okx.com/api/v5/market/tickers?instType=SWAP"
        res = requests.get(url, timeout=15).json()
        if res.get("code") == "0" and res.get("data"):
            for item in res["data"]:
                inst_id = item["instId"]
                if not inst_id.endswith("-USDT-SWAP"):
                    continue
                last_price = float(item.get("last", 0))
                vol24h = float(item.get("vol24h", 0))
                vol_usd = float(item.get("volCcy24h", last_price * vol24h))
                
                if vol_usd < MIN_VOLUME_USD:
                    continue
                
                sod_utc0 = float(item.get("sodUtc0", last_price))
                change_24h = ((last_price - sod_utc0) / sod_utc0) * 100 if sod_utc0 > 0 else 0.0
                
                coins_data.append({
                    "symbol": inst_id,
                    "price": last_price,
                    "change_24h": change_24h,
                    "vol_usd": vol_usd
                })
    except Exception as e:
        print(f"Błąd skanowania rynku: {e}")

    coins_data.sort(key=lambda x: x["change_24h"], reverse=True)

    snapshot = {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "btc": {"price": btc_ticker.get("last") if btc_ticker else "N/A", "oi": btc_oi, "funding": btc_funding, "liqs": btc_liqs},
        "strk": {"price": strk_ticker.get("last") if strk_ticker else "N/A", "oi": strk_oi, "funding": strk_funding, "liqs": strk_liqs},
        "top_gainers": coins_data[:8], # Najmocniej rosnące do oceny pod shorty
        "top_losers": coins_data[-8:]   # Najmocniej spadające do oceny pod longi (szukanie dywergencji)
    }
    return json.dumps(snapshot, indent=2)

# ==========================================
# 2. ANALIZA I SELEKCJA PRZEZ AI (GEMINI)
# ==========================================
def generate_ai_analysis():
    raw_data = get_market_snapshot()
    
    prompt = f"""
Jesteś elitarnym analitykiem rynków kryptowalut współpracującym z Market Makerem zarządzającym bazowym kapitałem 20,000 - 30,000 USD. 

Przeanalizuj poniższe surowe dane rynkowe z OKX i Binance. 

RYGORystyczne KRYTERIA WYBORU PROJEKTÓW:
1. Nie wciskaj projektów na siłę! Jeśli żaden altcoin nie spełnia ostrych kryteriów, napisz wyraźnie, że brak rzetelnych okazji.
2. Projekt kwalifikujący się do pozycji (Short lub Long) musi spełniać:
   - Długi czas spadkowy/wzrostowy od struktury szczytów/dołków (HH/LH trwające wiele dni/tygodni).
   - Skrajne wskaźniki (RSI leżące na dnie lub suficie, wyczerpanie MACD, StochRSI = 0 lub 100).
   - Bezwzględna obecność potencjalnych dywergencji (np. bycza dywergencja na dołkach przy szukaniu longów).
   - Odpowiedni wolumen USD zapewniający płynność dla pozycji 20-30k USD.
3. Pierwsze linijki raportu muszą zawierać nagłówki decyzyjne dla BTC oraz STRK (pamiętaj: STRK ma pozycję long z celem wyjścia w okolicach 0.0690).

Oto dane rynkowe:
{raw_data}

Zwróć wynik w formacie JSON lub czytelnym tekście podzielonym na dwie sekcje:
- TYP RAPORTU: [RAPORT SESYJNY lub ALERT CENOWY] (Wybierz "ALERT CENOWY" tylko wtedy, gdy wykryto absolutnie wybitną okazję spełniającą wszystkie powyższe warunki dywergencji; w przeciwnym razie ustaw "RAPORT SESYJNY").
- TEMAT E-MAILA
- TREŚĆ RAPORTU
"""
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt)
        text = response.text
        return text
    except Exception as e:
        print(f"Błąd Gemini API: {e}")
        return None

# ==========================================
# 3. WYSYŁKA E-MAIL
# ==========================================
def send_email(subject, body):
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = SMTP_USER
        msg['To'] = EMAIL_TO

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print("E-mail wysłany pomyślnie.")
    except Exception as e:
        print(f"Błąd wysyłki e-mail: {e}")

if __name__ == "__main__":
    ai_output = generate_ai_analysis()
    if ai_output:
        # Domyślny temat i wysyłka
        subject = "Aktualności rynkowe i analiza AI"
        if "ALERT CENOWY" in ai_output:
            subject = "🚨 ALERT CENOWY: Wykryto okazję rynkową"
        elif "RAPORT SESYJNY" in ai_output:
            subject = "📊 Raport Sesyjny Rynku Krypto"
            
        send_email(subject, ai_output)