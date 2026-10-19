import os
import sys
import ftplib
import requests
from google import genai

# ==========================================
# 1. KONFIGURACJA I SEKRETY
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

FTP_HOST = os.getenv("FTP_HOST")
FTP_USER = os.getenv("FTP_USER")
FTP_PASS = os.getenv("FTP_PASS")

# ==========================================
# 2. FUNKCJA WYSYŁKI NA FTP
# ==========================================
def upload_to_ftp(content_text, remote_filename="latest_report.txt"):
    """Wysyła treść raportu jako plik .txt do folderu public_html na serwerze FTP."""
    if not FTP_HOST or not FTP_USER or not FTP_PASS:
        print("[FTP] Brak danych logowania FTP w zmiennych środowiskowych. Pomijam wysyłkę na FTP.")
        return

    local_temp_file = "temp_report.txt"
    try:
        with open(local_temp_file, "w", encoding="utf-8") as f:
            f.write(content_text)

        print(f"[FTP] Łączenie z serwerem FTP: {FTP_HOST}...")
        with ftplib.FTP(FTP_HOST, FTP_USER, FTP_PASS) as ftp:
            
            # Przechodzimy do odpowiedniego katalogu publicznego strony
            target_dir = "domains/piwecki.pl/public_html" # Jeśli Twój folder to public_html (z 'html' na końcu), zmień tutaj
            # (Uwaga: jeśli na serwerze katalog nazywa się dokładnie 'public.html', zostaw tak jak poniżej)
            
            print(f"[FTP] Przechodzę do katalogu: {target_dir}")
            try:
                ftp.cwd(target_dir)
            except Exception as e:
                print(f"[FTP BŁĄD] Nie znaleziono katalogu {target_dir}: {e}")
                return

            # Wysyłka pliku
            with open(local_temp_file, "rb") as file_to_send:
                ftp.storbinary(f"STOR {remote_filename}", file_to_send)
                
        print(f"[FTP] Sukces! Plik {remote_filename} został pomyślnie wysłany do {target_dir}.")
    except Exception as e:
        print(f"[FTP BŁĄD] Nie udało się wysłać pliku na FTP: {e}")
    finally:
        if os.path.exists(local_temp_file):
            os.remove(local_temp_file)

# ==========================================
# 3. WYSYŁKA NA TELEGRAM (CZYSTY TEKST)
# ==========================================
def send_telegram_message(text):
    """Wysyła wiadomość na Telegram bez parse_mode, aby zapobiec błędom składni."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Brak tokenu lub chat_id. Pomijam wysyłkę.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            print("[Telegram] Wiadomość wysłana pomyślnie.")
        else:
            print(f"[Telegram BŁĄD] Kod {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[Telegram BŁĄD] Wyjątek: {e}")

# ==========================================
# 4. POBIERANIE DANYCH RYNKOWYCH (OKX)
# ==========================================
def fetch_okx_market_data():
    """Pobiera podstawowe dane o płynności i wolumenie z OKX."""
    try:
        url = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
        res = requests.get(url, timeout=10).json()
        if res.get("code") == "0":
            data = res.get("data", [])
            # Filtrujemy pary USDT/USDC pod kątem najwyższego wolumenu
            sorted_data = sorted(data, key=lambda x: float(x.get("volCcy24h", 0)), reverse=True)
            top_pairs = []
            for item in sorted_data[:15]:
                top_pairs.append({
                    "instId": item["instId"],
                    "last": item["last"],
                    "volCcy24h": item["volCcy24h"]
                })
            return top_pairs
    except Exception as e:
        print(f"[OKX BŁĄD] {e}")
    return []

# ==========================================
# 5. GENEROWANIE RAPORTU PRZEZ GEMINI AI
# ==========================================
def generate_report():
    if not GEMINI_API_KEY:
        print("[GEMINI BŁĄD] Brak GEMINI_API_KEY!")
        sys.exit(1)

    client = genai.Client(api_key=GEMINI_API_KEY)
    okx_data = fetch_okx_market_data()

    prompt = f"""
Jesteś analitykiem krypto projektu Market Maker Monitor. Wygeneruj pełny raport rynkowy w dwóch częściach na podstawie danych:
Dane OKX: {okx_data}

Zasady i Guardraile:
1. Brak sztywnych ram kapitałowych. Podawaj elastyczne "Safe Position Size" w oparciu o wolumen 24h z OKX.
2. Bezwzględny zakaz stosowania znaków lateksowych (np. brak '\\,'). Wszystkie ceny i kwoty mają być podane w czystym tekście, np. $82,750 lub $0.0815.
3. Rygorystyczna walidacja poziomów wejścia: Każda strefa (Entry, SL, TP) musi wynikać z twardych struktur (order blocki, płynność, silne dywergencje). Unikaj pochopnych, fałszywych poziomów wsparcia.
4. Struktura raportu musi obejmować:
   - Część 1: Kondycja Rynku, BTC & Smart Money, Starknet (STRK), Sonic (S / S/USDC).
   - Część 2: Top Selekcja Altcoinowa (2-3 układy z OKX ze strefami wejścia, SL, TP i widełkami pozycji).

Sformatuj odpowiedź wyraźnie rozdzielając CZĘŚĆ 1 i CZĘŚĆ 2 za pomocą separatora ===SEPARATOR===.
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    
    return response.text

# ==========================================
# 6. GŁÓWNA LOGIKA
# ==========================================
def main():
    print("Rozpoczynam wygenerowanie raportu...")
    report_text = generate_report()

    # Zapis lokalny do pliku latest_report.md
    with open("latest_report.md", "w", encoding="utf-8") as f:
        f.write(report_text)
    print("[Plik] Zapisano raport do latest_report.md")

    # Wysyłka na FTP jako czysty plik .txt
    upload_to_ftp(report_text, "latest_report.txt")

    # Podział i wysyłka na Telegram
    if "===SEPARATOR===" in report_text:
        parts = report_text.split("===SEPARATOR===")
        part1 = parts[0].strip()
        part2 = parts[1].strip()
        
        send_telegram_message(f"--- CZĘŚĆ 1 ---\n\n{part1}")
        send_telegram_message(f"--- CZĘŚĆ 2 ---\n\n{part2}")
    else:
        send_telegram_message(report_text)

if __name__ == "__main__":
    main()
