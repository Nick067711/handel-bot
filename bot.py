import os
import ftplib
import requests
from google import genai

# ==========================================
# KONFIGURACJA ZMIENNYCH ŚRODOWISKOWYCH
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FTP_HOST = os.getenv("FTP_HOST")
FTP_USER = os.getenv("FTP_USER")
FTP_PASS = os.getenv("FTP_PASS")

# ==========================================
# 1. FUNKCJA POBIERANIA DANYCH RYNKOWYCH
# ==========================================
def fetch_market_data():
    """Pobiera dane z OKX API i Blockchain.info."""
    data = {}
    try:
        # POBIERANIE CENY BTC Z OKX
        url_btc = "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT"
        res = requests.get(url_btc, timeout=10).json()
        if res.get("code") == "0" and res.get("data"):
            data["btc_price"] = res["data"][0].get("last", "N/A")
            data["btc_vol24h"] = res["data"][0].get("volCcy24h", "N/A")

        # POBIERANIE STRK Z OKX
        url_strk = "https://www.okx.com/api/v5/market/ticker?instId=STRK-USDT"
        res_strk = requests.get(url_strk, timeout=10).json()
        if res_strk.get("code") == "0" and res_strk.get("data"):
            data["strk_price"] = res_strk["data"][0].get("last", "N/A")

        # POBIERANIE SONIC (S) Z OKX
        url_s = "https://www.okx.com/api/v5/market/ticker?instId=S-USDT"
        res_s = requests.get(url_s, timeout=10).json()
        if res_s.get("code") == "0" and res_s.get("data"):
            data["s_price"] = res_s["data"][0].get("last", "N/A")

        # ON-CHAIN BITCOIN
        res_chain = requests.get("https://api.blockchain.info/stats", timeout=10).json()
        data["hash_rate"] = res_chain.get("hash_rate", "N/A")
        data["n_tx"] = res_chain.get("n_tx", "N/A")

    except Exception as e:
        print(f"[BŁĄD DANYCH] Wystąpił błąd podczas pobierania danych z API: {e}")

    return data

# ==========================================
# 2. GENEROWANIE RAPORTU (GEMINI-3.8-FLASH)
# ==========================================
def generate_report(market_data):
    """Generuje raport analityczny przy użyciu Gemini API."""
    if not GEMINI_API_KEY:
        print("[BŁĄD] Brak GEMINI_API_KEY!")
        return "Błąd: Brak klucza API dla Gemini."

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
Jesteś starszym analitykiem rynku kryptowalut dla projektu Market Maker Monitor.
Przeanalizuj poniższe dane rynkowe i przygotuj zwięzły, profesjonalny raport w języku polskim.

DANE RYNKOWE:
- BTC Cena: ${market_data.get('btc_price', 'N/A')}
- BTC Wolumen 24h: ${market_data.get('btc_vol24h', 'N/A')}
- STRK Cena: ${market_data.get('strk_price', 'N/A')}
- Sonic (S) Cena: ${market_data.get('s_price', 'N/A')}
- Hashrate sieci BTC: {market_data.get('hash_rate', 'N/A')} GH/s
- Liczba transakcji BTC 24h: {market_data.get('n_tx', 'N/A')}

WYMOGI FORMATOWANIA:
1. Pisz czystym tekstem. NIE używaj składni LaTeX (bezwzględny zakaz znaku backslash, np. brak \\, ).
2. Pieniądze i ceny formatuj standardowo, np. $82,750 lub $0.0815.
3. Podaj kondycję rynku, analizę BTC & Smart Money, Starknet (STRK), Sonic (S).
4. Dołącz 2-3 propozycje altcoinowe z podaną strefą wejścia (Entry), Stop Loss (SL), Take Profit (TP) oraz bezpiecznym rozmiarem pozycji (Safe Position Size).
"""

    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt
    )

    return response.text

# ==========================================
# 3. WYSYŁKA RAPORTU NA SERWER FTP
# ==========================================
def upload_to_ftp(content_text, remote_filename="latest_report.txt"):
    """Zapisuje plik lokalnie w UTF-8 i wysyła do folderu public_html na FTP."""
    if not FTP_HOST or not FTP_USER or not FTP_PASS:
        print("[FTP] Brak danych logowania FTP. Pomijam wysyłkę.")
        return

    local_temp_file = "temp_report.txt"
    try:
        # Wymuszamy kodowanie UTF-8 przy zapisie pliku lokalnego
        with open(local_temp_file, "w", encoding="utf-8") as f:
            f.write(content_text)

        print(f"[FTP] Łączenie z serwerem FTP: {FTP_HOST}...")
        with ftplib.FTP(FTP_HOST, FTP_USER, FTP_PASS) as ftp:
            target_dir = "domains/piwecki.pl/public_html"
            print(f"[FTP] Przechodzę do katalogu: {target_dir}")
            
            try:
                ftp.cwd(target_dir)
            except Exception as e:
                print(f"[FTP BŁĄD] Nie udało się przejść do {target_dir}: {e}")
                return

            with open(local_temp_file, "rb") as file_to_send:
                ftp.storbinary(f"STOR {remote_filename}", file_to_send)

        print(f"[FTP] Sukces! Plik {remote_filename} został pomyślnie wysłany do {target_dir}.")
    except Exception as e:
        print(f"[FTP BŁĄD] Nie udało się wysłać pliku na FTP: {e}")
    finally:
        if os.path.exists(local_temp_file):
            os.remove(local_temp_file)

# ==========================================
# 4. WYSYŁKA WIADOMOŚCI NA TELEGRAM
# ==========================================
def send_telegram_messages(report_text):
    """Wysyła raport na Telegram jako zwykły tekst bez parse_mode."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Brak danych powiadomień Telegram. Pomijam.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Dzielimy raport na mniejsze części, jeśli jest bardzo długi
    max_length = 4000
    chunks = [report_text[i:i+max_length] for i in range(0, len(report_text), max_length)]

    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk
        }
        try:
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                print("[TELEGRAM] Wiadomość wysłana pomyślnie.")
            else:
                print(f"[TELEGRAM BŁĄD] Odpowiedź serwera: {res.text}")
        except Exception as e:
            print(f"[TELEGRAM BŁĄD] Wyjątek podczas wysyłki: {e}")

# ==========================================
# GLÓWNA PĘTLA PROGRAMU
# ==========================================
if __name__ == "__main__":
    print("=== Start Market Maker Bot ===")
    
    # 1. Pobieranie danych
    data = fetch_market_data()
    print(f"[Dane] Pobrano dane rynkowe.")

    # 2. Generowanie raportu
    report = generate_report(data)
    print("[Raport] Raport wygenerowany pomyślnie.")

    # Zapis lokalny kopii raportu w UTF-8
    with open("latest_report.md", "w", encoding="utf-8") as f:
        f.write(report)

    # 3. Wysyłka na FTP
    upload_to_ftp(report, remote_filename="latest_report.txt")

    # 4. Wysyłka na Telegram
    send_telegram_messages(report)

    print("=== Zakończono pomyślnie ===")
