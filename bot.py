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
    """Pobiera dane rynkowe z OKX."""
    try:
        url = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
        res = requests.get(url, timeout=10)
        data = res.json()
        if data.get("code") == "0":
            return data.get("data", [])
    except Exception as e:
        print(f"Błąd pobierania danych OKX: {e}")
    return []

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
    # Pobranie surowych danych rynkowych
    raw_tickers = fetch_okx_data()
    
    # Przygotowanie skróconego podsumowania danych dla Gemini
    top_tickers = []
    for item in raw_tickers[:40]:  # Główna część rynku pod kątem płynności
        top_tickers.append(
            f"Symbol: {item.get('instId')}, Price: {item.get('last')}, "
            f"24h High: {item.get('high24h')}, 24h Low: {item.get('low24h')}, "
            f"Vol24h: {item.get('volCcy24h')}"
        )
    
    tickers_summary = "\n".join(top_tickers)

    prompt = f"""
Przeanalizuj poniższe dane rynkowe z giełdy OKX dla tradera dysponującego kapitałem $20k–$30k USD.

Zasady analizy jakościowej:
1. Zweryfikuj korelację z Bitcoinem (BTC) jako makro-kotwicą rynku.
2. Przeanalizuj pozycję Starknet (STRK) pod kątem aktualnego statusu i potencjału.
3. Filtruj altcoiny pod kątem wielotygodniowych korekt cenowych od struktur HH/LH, skrajnych odczytów wskaźników oraz potencjalnych dywergencji.
4. Wybierz tylko układy o wysokim prawdopodobieństwie sukcesu.

Dane rynkowe:
{tickers_summary}

Wygeneruj czytelny, zwięzły raport rynkowy w języku polskim z rekomendacjami dla sesji, sformatowany estetycznie pod czytanie na telefonie.
"""

    print("Przesyłanie danych do analizy AI...")
    
    response = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt,
    )
    
    report_text = response.text
    print("Analiza ukończona.")

    # Formatowanie wiadomości pod Telegram
    header = "🚨 *Raport Rynkowy AI — Monitoring OKX & STRK*\n\n"
    full_message = header + report_text
    
    # Zabezpieczenie przed limitem długości wiadomości Telegrama (4000 znaków)
    if len(full_message) > 4000:
        full_message = full_message[:3900] + "\n\n*(Wiadomość przycięta ze względu na limit długości)*"

    send_telegram_message(full_message)

if __name__ == "__main__":
    run_analysis()
