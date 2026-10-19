import os
import requests
from google import genai

# 1. Pobieranie kluczy ze zmiennych środowiskowych (GitHub Secrets)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

client = genai.Client(api_key=GEMINI_API_KEY)

def fetch_okx_data():
    """Pobiera dane rynkowe SPOT i SWAP z OKX."""
    all_tickers = []
    try:
        res_spot = requests.get("https://www.okx.com/api/v5/market/tickers?instType=SPOT", timeout=15).json()
        if res_spot.get("code") == "0":
            all_tickers.extend(res_spot.get("data", []))
    except Exception as e:
        print(f"Błąd OKX SPOT: {e}")

    try:
        res_swap = requests.get("https://www.okx.com/api/v5/market/tickers?instType=SWAP", timeout=15).json()
        if res_swap.get("code") == "0":
            all_tickers.extend(res_swap.get("data", []))
    except Exception as e:
        print(f"Błąd OKX SWAP: {e}")

    return all_tickers

def fetch_btc_onchain_data():
    """Pobiera darmowe wskaźniki makro i derywaty dla BTC."""
    onchain_info = {}
    try:
        res = requests.get("https://api.blockchain.info/stats", timeout=10).json()
        onchain_info['hash_rate'] = res.get('hash_rate')
        onchain_info['n_tx'] = res.get('n_tx')
        onchain_info['market_price_usd'] = res.get('market_price_usd')
    except Exception as e:
        print(f"Błąd pobierania danych on-chain BTC: {e}")
        
    return onchain_info

def send_telegram_message(message):
    """Wysyła wiadomość bezpośrednio na Telegram."""
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
    raw_tickers = fetch_okx_data()
    btc_onchain = fetch_btc_onchain_data()

    tickers_summary = []
    for item in raw_tickers:
        tickers_summary.append(
            f"Symbol: {item.get('instId')}, Price: {item.get('last')}, "
            f"High24h: {item.get('high24h')}, Low24h: {item.get('low24h')}, Vol24h: {item.get('volCcy24h')}"
        )
    
    tickers_text = "\n".join(tickers_summary)

    prompt = f"""
Jesteś starszym analitykiem krypto. Twoim zadaniem jest stworzyć zwięzły, niezwykle czytelny raport w języku polskim, podzielony NA DWIE OSOBNE CZĘŚCI (rozdzielone znacznikiem `---SPLIT---`).

NIE narzucaj sztywnego kapitału. Zamiast tego dla każdego omawianego aktywa podaj **bezpieczne widełki wielkości pozycji / płynności** w oparciu o jego rzeczywisty wolumen na OKX, aby uniknąć poślizgu cenowego.

Dane On-Chain / Makro BTC:
{btc_onchain}

Dane rynkowe OKX:
{tickers_text}

WYMAGANA STRUKTURA ODPOWIEDZI:

CZĘŚĆ 1 (Kondycja Rynku & Kluczowe Aktywa):
1. 🪙 **BITCOIN & SMART MONEY**:
   - Kondycja makro BTC, cena, opór/wsparcie i sentyment dużych graczy.
   - **Bezpieczne widełki pozycji**: Sugestia wielkości zaangażowania bazująca na płynności BTC.
2. ⚡ **STARKNET (STRK)**:
   - Status strukturalny i rekomendacja.
   - **Bezpieczne widełki pozycji**: Maksymalna sugerowana wielkość wejścia bazująca na wolumenie SWAP/SPOT STRK.
3. 🌀 **SONIC (S / S/USDC)**:
   - Analiza wyłącznie właściwej pary S z danych źródłowych (zakaz pisania o podróbkach).
   - **Bezpieczne widełki pozycji**: Ocena płynności i zalecany bezpieczny przedział kapitału na transakcję.

---SPLIT---

CZĘŚĆ 2 (Top Okazje Altcoinowe - Maksymalnie 2-3 projekty):
Wybierz TYLKO 2-3 najsilniejsze układy z całego rynku OKX.
Dla każdego z nich podaj:
- **Symbol** i opis układu (1 zdanie)
- **Strefa Wejścia (Entry)**
- **Stop Loss (SL)**
- **Take Profit (TP)**
- **Bezpieczne widełki pozycji**: Sugerowany przedział kapitału wejściowego dla tego konkretnego tokena wynikający z jego wolumenu 24h.

Jeśli brak układów, napisz: "Brak bezpiecznych konfiguracji altcoinowych dla obecnej sesji."
"""

    print("Generowanie raportu AI bez sztywnych ram kapitałowych...")
    response = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt,
    )
    
    full_text = response.text

    if "---SPLIT---" in full_text:
        part1, part2 = full_text.split("---SPLIT---", 1)
    else:
        part1 = full_text
        part2 = ""

    header1 = "📊 *CZĘŚĆ 1: KONDYCJA RYNKU, BTC, STRK & SONIC*\n\n"
    send_telegram_message(header1 + part1.strip())

    if part2.strip():
        header2 = "🎯 *CZĘŚĆ 2: TOP SELEKCJA ALTCOINOWA*\n\n"
        send_telegram_message(header2 + part2.strip())

if __name__ == "__main__":
    run_analysis()
