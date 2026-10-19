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
    """Pobiera WSZYSTKIE dane rynkowe z OKX (SPOT + SWAP/PERP)."""
    all_tickers = []
    
    # Pobieranie rynku SPOT
    try:
        url_spot = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
        res_spot = requests.get(url_spot, timeout=15)
        data_spot = res_spot.json()
        if data_spot.get("code") == "0":
            all_tickers.extend(data_spot.get("data", []))
    except Exception as e:
        print(f"Błąd pobierania danych OKX SPOT: {e}")

    # Pobieranie rynku SWAP
    try:
        url_swap = "https://www.okx.com/api/v5/market/tickers?instType=SWAP"
        res_swap = requests.get(url_swap, timeout=15)
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
    # Pobranie WSZYSTKICH surowych danych rynkowych
    raw_tickers = fetch_okx_data()
    
    tickers_summary = []
    for item in raw_tickers:
        tickers_summary.append(
            f"Symbol: {item.get('instId')}, Price: {item.get('last')}, "
            f"24h High: {item.get('high24h')}, 24h Low: {item.get('low24h')}, "
            f"Vol24h: {item.get('volCcy24h')}"
        )
    
    tickers_text = "\n".join(tickers_summary)

    # KROK 1: Wstępna pełna analiza rynkowa
    prompt_step1 = f"""
Przeanalizuj CAŁOŚCIOWE dane rynkowe z giełdy OKX dla tradera dysponującego kapitałem $20k–$30k USD.

Zasady analizy:
1. Zweryfikuj korelację z Bitcoinem (BTC-USDT) jako makro-kotwicą całego rynku.
2. DEDYKOWANA ANALIZA STRK: Przeanalizuj Starknet (STRK) na podstawie danych pod kątem aktualnego statusu, struktury i potencjału.
3. DEDYKOWANA ANALIZA SONIC (S/USDC / pary S): Przeanalizuj sytuację Sonic ściśle na podstawie faktycznych danych z tabeli. 
   - BEZWZGLĘDNIE ZAKAZANE JEST wspominanie o jakichkolwiek "podróbkach", memecoinach czy klonach.
4. Przeskanuj WSZYSTKIE pozostałe altcoiny z listy pod kątem wielotygodniowych korekt od struktur HH/LH, skrajnych odczytów wskaźników oraz potencjalnych dywergencji.
5. Wybierz wyłącznie układy o wysokim prawdopodobieństwie sukcesu, odpowiadające kapitałowi $20k–$30k.

Pełne dane rynkowe OKX:
{tickers_text}

Wygeneruj wstępny raport rynkowy.
"""

    print("Generowanie pełnej analizy AI (cały rynek OKX)...")
    response1 = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt_step1,
    )
    draft_report = response1.text

    # KROK 2: Automatyczny audyt sam siebie (Fact-checking loop)
    prompt_step2 = f"""
Jesteś rygorystycznym audytorem finansowym i fact-checkerem. Sprawdź poniższy szkic raportu pod kątem błędu halucynacji (zmyślania danych).

Oto oryginalne, pełne dane źródłowe z giełdy OKX:
{tickers_text}

Oto wstępny szkic raportu do weryfikacji:
{draft_report}

Zasady audytu:
1. Upewnij się, że analiza makro dla Bitcoina (BTC) opiera się na danych z arkusza.
2. Zweryfikuj każdą cenę i wolumen w szkicu z danymi źródłowymi. Skoryguj wszelkie zmyślone liczby.
3. Dla Sonic (S / S/USDC): Upewnij się, że opiera się ściśle na rzeczywistym wolumenie z tabeli. BEZWZGLĘDNIE ZAKAZANE jest jakiekolwiek wspominanie o "podróbkach" lub ostrzeganie przed klonami.
4. Wygeneruj ostateczny, czysty, w pełni zweryfikowany raport w języku polskim, gotowy do wysyłki na Telegram.
"""

    print("Przeprowadzanie automatycznego audytu (self-correction)...")
    response2 = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt_step2,
    )
    
    final_report = response2.text
    print("Analiza i audyt ukończone.")

    # Formatowanie wiadomości pod Telegram
    header = "🚨 *Pełny Raport Rynkowy AI (Zweryfikowany) — OKX, STRK & SONIC*\n\n"
    full_message = header + final_report
    
    if len(full_message) > 4000:
        full_message = full_message[:3900] + "\n\n*(Wiadomość przycięta ze względu na limit długości)*"

    send_telegram_message(full_message)

if __name__ == "__main__":
    run_analysis()
