import os
import requests
from google import genai

# 1. Pobieranie kluczy ze zmiennych środowiskowych (GitHub Secrets)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# 2. Inicjalizacja klienta Google GenAI
client = genai.Client(api_key=GEMINI_API_KEY)

def fetch_okx_data():
    """Pobiera dane rynkowe z OKX z osobnym uwzględnieniem SPOT i SWAP/PERP."""
    all_tickers = []
    
    # Pobieranie rynku SPOT
    try:
        url_spot = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
        res_spot = requests.get(url_spot, timeout=10)
        data_spot = res_spot.json()
        if data_spot.get("code") == "0":
            all_tickers.extend(data_spot.get("data", []))
    except Exception as e:
        print(f"Błąd pobierania danych OKX SPOT: {e}")

    # Pobieranie rynku SWAP
    try:
        url_swap = "https://www.okx.com/api/v5/market/tickers?instType=SWAP"
        res_swap = requests.get(url_swap, timeout=10)
        data_swap = res_swap.json()
        if data_swap.get("code") == "0":
            all_tickers.extend(data_swap.get("data", []))
    except Exception as e:
        print(f"Błąd pobierania danych OKX SWAP: {e}")

    return all_tickers

def send_telegram_message(message):
    """Wysyła wiadomość bezpośrednio na Twój czat Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("Wiadomość na Telegram została pomyślnie wysłana!")
        else:
            print(f"Błąd wysyłki na Telegram: {res.text}")
    except Exception as e:
        print(f"Błąd wysyłki na Telegram: {e}")

def run_analysis():
    # Pobranie surowych danych rynkowych (SPOT + SWAP)
    raw_tickers = fetch_okx_data()
    
    # Rozdzielenie rynku na Top Volume SPOT oraz dedykowane aktywa
    spot_tickers = [item for item in raw_tickers if "-USDT" in item.get('instId', '') or "-USDC" in item.get('instId', '') or "/USDC" in item.get('instId', '')]
    
    top_volume_spot = spot_tickers[:40]
    
    # Wymuszenie stałej obecności STRK oraz Sonic (S/USDC, S-USDC, S/USDT itp.)
    mandatory_assets = [
        item for item in raw_tickers 
        if any(target in item.get('instId', '').upper() for target in ["STRK", "S-USDC", "S-USDT", "S/USDC", "SONIC"])
    ]
    
    final_tickers_list = top_volume_spot + [item for item in mandatory_assets if item not in top_volume_spot]

    tickers_summary = []
    for item in final_tickers_list:
        tickers_summary.append(
            f"Symbol: {item.get('instId')}, Price: {item.get('last')}, "
            f"24h High: {item.get('high24h')}, 24h Low: {item.get('low24h')}, "
            f"Vol24h: {item.get('volCcy24h')}"
        )
    
    tickers_text = "\n".join(tickers_summary)

    # KROK 1: Wstępna analiza przez model
    prompt_step1 = f"""
Przeanalizuj poniższe dane rynkowe z giełdy OKX dla tradera dysponującego kapitałem $20k–$30k USD.

Zasady analizy:
1. Zweryfikuj korelację z Bitcoinem (BTC).
2. Przeanalizuj Starknet (STRK) na podstawie danych.
3. Przeanalizuj Sonic (S/USDC lub pary z S) ściśle na podstawie faktycznych liczb z tabeli (cena, wolumen). 
   - UWAGA: Bezwzględnie zakazuje się wspominania o jakichkolwiek "podróbkach", memecoinach czy alternatywnych tokenach. Skup się wyłącznie na właściwej parze S z danych.
4. Filtruj pozostałe altcoiny pod kątem wielotygodniowych korekt od struktur HH/LH, wskaźników i dywergencji.

Dane rynkowe:
{tickers_text}

Wygeneruj wstępny raport rynkowy.
"""

    print("Generowanie wstępnej analizy AI...")
    response1 = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt_step1,
    )
    draft_report = response1.text

    # KROK 2: Automatyczny audyt sam siebie (Fact-checking loop)
    prompt_step2 = f"""
Jesteś rygorystycznym audytorem finansowym i fact-checkerem. Twoim zadaniem jest sprawdzenie poniższego szkicu raportu pod kątem błędu halucynacji (zmyślania danych).

Oto oryginalne, surowe dane z giełdy OKX:
{tickers_text}

Oto wstępny szkic raportu do weryfikacji:
{draft_report}

Zasady audytu:
1. Zweryfikuj każdą cenę i wolumen w szkicu z oryginalnymi danymi z OKX. Jeśli w szkicu pojawiły się zmyślone obroty lub liczby spoza tabeli, bezwzględnie je skoryguj do prawdziwych wartości z danych źródłowych.
2. Sprawdź sekcję dotyczącą Sonic (S/USDC / S): Upewnij się, że opiera się ściśle na rzeczywistym wolumenie z danych. BEZWZGLĘDNIE ZAKAZANE JEST wspominanie o jakichkolwiek "podróbkach", klonach czy ostrzeganie przed nimi – użytkownik ich nie chce.
3. Usuń wszelkie przekłamania i wygeneruj ostateczny, czysty, w pełni zweryfikowany raport w języku polskim, gotowy do wysyłki na Telegram.
"""

    print("Przeprowadzanie automatycznego audytu (self-correction)...")
    response2 = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt_step2,
    )
    
    final_report = response2.text
    print("Analiza i audyt ukończone.")

    # Formatowanie wiadomości pod Telegram
    header = "🚨 *Raport Rynkowy AI (Zweryfikowany) — OKX, STRK & SONIC*\n\n"
    full_message = header + final_report
    
    if len(full_message) > 4000:
        full_message = full_message[:3900] + "\n\n*(Wiadomość przycięta ze względu na limit długości)*"

    send_telegram_message(full_message)

if __name__ == "__main__":
    run_analysis()
