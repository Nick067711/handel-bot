import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from google import genai

# 1. Pobieranie kluczy ze zmiennych środowiskowych (GitHub Secrets)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
RECIPIENT_EMAIL = "nick06711@gmail.com"

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

def send_email(subject, body):
    """Wysyła wiadomość e-mail przez Gmail SMTP (port 465 SSL)."""
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = RECIPIENT_EMAIL
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        # Użycie SMTP_SSL na porcie 465 – stabilniejsze w chmurze GitHub Actions
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        print("E-mail został pomyślnie wysłany!")
    except Exception as e:
        print(f"Błąd wysyłki e-mail: {e}")

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

Wygeneruj czytelny, zwięzły raport rynkowy w języku polskim z rekomendacjami dla sesji.
"""

    print("Przesyłanie danych do analizy AI...")
    
    # Wywołanie modelu gemini-3.8-flash
    response = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt,
    )
    
    report_text = response.text
    print("Analiza ukończona.")

    # Wysyłka e-maila
    subject = "Raport Rynkowy AI — Monitoring OKX & STRK"
    send_email(subject, report_text)

if __name__ == "__main__":
    run_analysis()
