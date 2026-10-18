import os
import requests
import json
import smtplib
from email.mime.text import MIMEText
from twilio.rest import Client

# ==========================================
# KONFIGURACJA ODBIORCÓW I DOSTĘPÓW
# ==========================================
EMAIL_TO = "nick06711@gmail.com"
EMAIL_SUBJECT = "Aktualności rynkowe i skaner okazji"
WHATSAPP_TO = "whatsapp:+48695777700"

# Dane SMTP (np. Gmail - wymagane hasło aplikacji)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER", "twoj_email@gmail.com")
SMTP_PASS = os.getenv("SMTP_PASS", "twoje_haslo_aplikacji")

# Dane Twilio (WhatsApp API)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "AC_TWOJ_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "TWOJ_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# Parametry filtracji kapitałowej i płynności
MIN_VOLUME_USD = 5000000  # Minimalny dzienny obrót w USD (5 mln USD dla odpowiedniej płynności pozycji 20-30k USD)

# ==========================================
# 1. POBIERANIE DANYCH Z OKX I BINANCE
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

def scan_okx_market_extremes():
    """Pobiera wszystkie pary SWAP z OKX, filtruje po wolumenie USD i szuka skrajności (Short/Long)"""
    coins_data = []
    try:
        url = "https://www.okx.com/api/v5/market/tickers?instType=SWAP"
        res = requests.get(url, timeout=15).json()
        if res.get("code") == "0" and res.get("data"):
            for item in res["data"]:
                inst_id = item["instId"]
                if not inst_id.endswith("-USDT-SWAP"):
                    continue
                
                # Wolumen w USD (volCcy24h lub szacunek z vol24h * cena)
                last_price = float(item.get("last", 0))
                vol24h = float(item.get("vol24h", 0))
                vol_usd = float(item.get("volCcy24h", last_price * vol24h))
                
                # Filtr płynności pod kapitał 20-30k USD
                if vol_usd < MIN_VOLUME_USD:
                    continue
                
                # Zmiana procentowa 24h jako prosty wskaźnik dynamiki/wykupienia
                sod_utc0 = float(item.get("sodUtc0", last_price))
                change_24h = ((last_price - sod_utc0) / sod_utc0) * 100 if sod_utc0 > 0 else 0.0
                
                coins_data.append({
                    "symbol": inst_id,
                    "price": last_price,
                    "change_24h": change_24h,
                    "vol_usd": vol_usd
                })
    except Exception as e:
        print(f"Błąd skanowania rynku OKX: {e}")
        
    # Sortowanie: największe wzrosty (kandydaci do Short / wykupione) oraz największe spadki (kandydaci do Long / wyprzedane)
    coins_data.sort(key=lambda x: x["change_24h"], reverse=True)
    
    top_shorts = coins_data[:3] if len(coins_data) >= 3 else coins_data
    top_longs = coins_data[-3:] if len(coins_data) >= 3 else []
    top_longs.reverse() # Od największego wyprzedania
    
    return top_shorts, top_longs

# ==========================================
# 2. GENEROWANIE RAPORTU DECYZYJNEGO
# ==========================================
def generate_report():
    btc_ticker = fetch_okx_data("BTC-USDT-SWAP")
    strk_ticker = fetch_okx_data("STRK-USDT-SWAP")
    btc_oi = fetch_okx_open_interest("BTC-USDT-SWAP")
    strk_oi = fetch_okx_open_interest("STRK-USDT-SWAP")
    btc_funding = fetch_okx_funding_rate("BTC-USDT-SWAP")
    strk_funding = fetch_okx_funding_rate("STRK-USDT-SWAP")
    
    btc_liqs = fetch_binance_liquidations("BTCUSDT")
    strk_liqs = fetch_binance_liquidations("STRKUSDT")

    btc_price = float(btc_ticker['last']) if btc_ticker else 0.0
    strk_price = float(strk_ticker['last']) if strk_ticker else 0.0

    # Skanowanie całego rynku w poszukiwaniu skrajności
    top_shorts, top_longs = scan_okx_market_extremes()

    report_text = f"""BTC: WSTRZYMAJ SIĘ OD HANDLU, RE-TEST OPORU EMA
STRK: WSTRZYMAJ SIĘ OD HANDLU, BUDOWANIE BAZY W STREFIE OKOLICZNIE 0,064-0,069

==================================================
SZCZEGÓŁOWA ANALIZA RYNKOWA (OKX + BINANCE)
==================================================

1. BITCOIN (BTC) - KOTWICA RYNKU:
• Aktualna cena: ${btc_price:,.2f}
• Otwarte Pozycje (OI): {btc_oi.get('oiCcy', 'N/A') if btc_oi else 'N/A'} BTC
• Stopa finansowania: {float(btc_funding.get('fundingRate', 0))*100:.4f}%
• Likwidacje (Binance): Longi: ${btc_liqs['long_liqs']:,.0f} | Shorty: ${btc_liqs['short_liqs']:,.0f}

2. STARKNET (STRK):
• Aktualna cena: ${strk_price:.4f}
• Otwarte Pozycje (OI): {strk_oi.get('oiCcy', 'N/A') if strk_oi else 'N/A'} STRK
• Stopa finansowania: {float(strk_funding.get('fundingRate', 0))*100:.4f}%
• Likwidacje (Binance): Longi: ${strk_liqs['long_liqs']:,.0f} | Shorty: ${strk_liqs['short_liqs']:,.0f}

==================================================
SKANER RYNKU OKX (Płynność > $5M, Kapitał 20-30k USD)
==================================================

🔴 TOP 3 KANDYDACI DO SHORT (Skrajnie wykupione):
"""
    for i, coin in enumerate(top_shorts, 1):
        report_text += f"{i}. {coin['symbol']} | Cena: ${coin['price']} | Zmiana 24h: +{coin['change_24h']:.2f}% | Wolumen: ${coin['vol_usd']:,.0f}\n"

    report_text += "\n🟢 TOP 3 KANDYDACI DO LONG (Skrajnie wyprzedane):\n"
    for i, coin in enumerate(top_longs, 1):
        report_text += f"{i}. {coin['symbol']} | Cena: ${coin['price']} | Zmiana 24h: {coin['change_24h']:.2f}% | Wolumen: ${coin['vol_usd']:,.0f}\n"

    return report_text

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
        print(f"WhatsApp wysłany pomyślnie (SID: {message.sid}).")
    except Exception as e:
        print(f"Błąd wysyłki WhatsApp: {e}")

if __name__ == "__main__":
    raport = generate_report()
    send_email(raport)
    send_whatsapp(raport)