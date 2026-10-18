import os
import requests
import json
import smtplib
from email.mime.text import MIMEText
from twilio.rest import Client
import google.generativeai as genai

# ==========================================
# KONFIGURACJA ODBIORCÓW I DOSTĘPÓW
# ==========================================
EMAIL_TO = "nick06711@gmail.com"
EMAIL_SUBJECT = "Aktualności rynkowe i analiza AI"
WHATSAPP_TO = "whatsapp:+48695777700"

# Dane SMTP
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER", "twoj_email@gmail.com")
SMTP_PASS = os.getenv("SMTP_PASS", "twoje_haslo_aplikacji")

# Dane Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "AC_TWOJ_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "TWOJ_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# Konfiguracja Gemini API (Twój klucz z Google AI Studio)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "TWOJ_GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

MIN_VOLUME_USD = 5000000  # Minimalny wolumen USD dla płynności pozycji 20-30k USD

# ==========================================
# 1. POBIERANIE DANYCH Z GIEŁD
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
    """Pobiera pełny zestaw surowych danych rynkowych do analizy przez AI"""
    btc_ticker = fetch_okx_data("BTC-USDT-SWAP")
    strk_ticker = fetch_okx_data("STRK-USDT-SWAP")
    btc_oi = fetch_okx_open_interest("BTC-USDT-SWAP")
    strk_oi = fetch_okx_open_interest("STRK-USDT-SWAP")
    btc_funding = fetch_okx_funding_rate("BTC-USDT-SWAP")
    strk_funding = fetch_okx_funding_rate("STRK-USDT-SWAP")
    
    btc_liqs = fetch_binance_liquidations("BTCUSDT")
    strk_liqs = fetch_binance_liquidations("STRKUSDT")

    # Skanowanie całego rynku OKX pod kątem wolumenu i skrajności
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
    top_shorts_raw = coins_data[:5]
    top_longs_raw = coins_data[-5:]

    snapshot = {
        "btc": {"price": btc_ticker.get("last") if btc_ticker else "N/A", "oi": btc_oi, "funding": btc_funding, "liqs": btc_liqs},
        "strk": {"price": strk_ticker.get("last") if strk_ticker else "N/A", "oi": strk_oi, "funding": strk_funding, "liqs": strk_liqs},
        "top_up_candidates": top_shorts_raw,
        "top_down_candidates": top_longs_raw
    }
    return json.dumps(snapshot, indent=2)

# ==========================================
# 2. ANALIZA PRZEZ GEMINI API (MÓZG BOTA)
# ==========================================
def generate_ai_report():
    raw_data = get_market_snapshot()
    
    prompt = f"""
Jesteś profesjonalnym analitykiem rynków kryptowalut współpracującym z Market Makerem zarządzającym bazowym kapitałem 20,000 - 30,000 USD. Twoim zadaniem jest przeanalizować poniższe surowe dane rynkowe z OKX i Binance.

Oto wytyczne:
1. Pierwsze dwie linijki raportu muszą zawierać konkretne decyzje handlowe w formacie nagłówkowym dla BTC oraz STRK (pamiętaj, że STRK ma istniejącą pozycję long zejściową z celem wyjścia w okolicach 0.0690).
2. Następnie przeprowadź szczegółową analizę rynkową uwzględniając OI, Funding Rates i likwidacje.
3. Na podstawie przefiltrowanych danych wybierz dokładnie **Top 3 kandydatów do Short** (skrajnie wykupione o dużym wolumenie) oraz **Top 3 kandydatów do Long** (skrajnie wyprzedane o dużym wolumenie) pod kątem bezpiecznego wejścia z kapitałem 20-30k USD.

Oto surowe dane rynkowe:
{raw_data}

Przygotuj spójny, profesjonalny i czytelny raport tekstowy gotowy do wysłania.
"""
    try:
        model = genai.GenerativeModel("gemini-2.5-flash") # lub odpowiedni model
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Błąd generowania analizy przez Gemini API: {e}")
        return "Błąd generowania analizy AI."

# ==========================================
# 3. WYSYŁKA POWIADOMIEŃ
# ==========================================
def send_email(text_content):
    try:
        msg = MIMEText(text_content, 'plain', 'utf-8')
        msg['Subject'] = EMAIL_SUBJECT
        msg['From'] = SMTP_USER
        msg['To'] = EMAIL_TO

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print("E-mail wysłany pomyślnie.")
    except Exception as e:
        print(f"Błąd wysyłki e-mail: {e}")

def send_whatsapp(text_content):
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=text_content,
            from_=TWILIO_WHATSAPP_FROM,
            to=WHATSAPP_TO
        )
        print(f"WhatsApp wysłany pomyślnie.")
    except Exception as e:
        print(f"Błąd wysyłki WhatsApp: {e}")

if __name__ == "__main__":
    raport = generate_ai_report()
    send_email(raport)
    send_whatsapp(raport)