import os
import time
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
# 1. FUNKCJA POBIERANIA POMOCNICZYCH METRYK OKX
# ==========================================
def fetch_okx_deriv_data(inst_id, ccy):
    """
    Pobiera dane instrumentu z OKX API:
    - Ticker (Cena i wolumen 24h)
    - Open Interest (Otwarte pozycje na kontraktach SWAP)
    - Long/Short Ratio (Sentyment inwestorów)
    """
    metrics = {
        "price": "N/A",
        "vol24h": "N/A",
        "open_interest": "N/A",
        "long_short_ratio": "N/A"
    }
    
    # 1. Ticker Spot/Swap
    try:
        url_ticker = f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"
        res = requests.get(url_ticker, timeout=8).json()
        if res.get("code") == "0" and res.get("data"):
            metrics["price"] = res["data"][0].get("last", "N/A")
            metrics["vol24h"] = res["data"][0].get("volCcy24h", "N/A")
    except Exception as e:
        print(f"[OKX Ticker Error] {inst_id}: {e}")

    # 2. Open Interest (Swap)
    try:
        swap_inst_id = f"{ccy}-USDT-SWAP"
        url_oi = f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={swap_inst_id}"
        res_oi = requests.get(url_oi, timeout=8).json()
        if res_oi.get("code") == "0" and res_oi.get("data"):
            metrics["open_interest"] = res_oi["data"][0].get("oi", "N/A")
    except Exception as e:
        print(f"[OKX OI Error] {swap_inst_id}: {e}")

    # 3. Long / Short Ratio
    try:
        url_ls = f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}"
        res_ls = requests.get(url_ls, timeout=8).json()
        if res_ls.get("code") == "0" and res_ls.get("data") and len(res_ls["data"]) > 0:
            metrics["long_short_ratio"] = res_ls["data"][0][1] if len(res_ls["data"][0]) > 1 else "N/A"
    except Exception as e:
        print(f"[OKX L/S Ratio Error] {ccy}: {e}")

    return metrics

# ==========================================
# 2. POBIERANIE WSZYSTKICH DANYCH RYNKOWYCH
# ==========================================
def fetch_all_market_data():
    """Gromadzi kompleksowe dane z OKX oraz Blockchain.info."""
    data = {}

    print("[Dane] Pobieram dane dla Bitcoina (BTC)...")
    data["btc"] = fetch_okx_deriv_data("BTC-USDT", "BTC")

    print("[Dane] Pobieram dane dla Starknet (STRK)...")
    data["strk"] = fetch_okx_deriv_data("STRK-USDT", "STRK")

    print("[Dane] Pobieram dane dla Sonic (S)...")
    data["s"] = fetch_okx_deriv_data("S-USDT", "S")

    # On-chain Bitcoin
    try:
        res_chain = requests.get("https://api.blockchain.info/stats", timeout=8).json()
        data["btc_hashrate"] = res_chain.get("hash_rate", "N/A")
        data["btc_n_tx"] = res_chain.get("n_tx", "N/A")
    except Exception as e:
        print(f"[Blockchain.info Error]: {e}")
        data["btc_hashrate"] = "N/A"
        data["btc_n_tx"] = "N/A"

    return data

# ==========================================
# 3. GENEROWANIE RAPORTU Z OBSŁUGĄ BŁĘDÓW 503 I FALLBACKAMI
# ==========================================
def generate_report(m_data):
    """Generuje raport z użyciem Gemini API, z zabezpieczeniem przed przeciążeniem serwerów (503)."""
    if not GEMINI_API_KEY:
        print("[BŁĄD] Brak GEMINI_API_KEY!")
        return "Błąd: Brak klucza API dla Gemini."

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
Jesteś głównym analitykiem i ekspertem od zarządzania ryzykiem dla projektu Market Maker Monitor.
Przeanalizuj poniższy zestaw surowych danych rynkowych pochodzących bezpośrednio z OKX API oraz Blockchain.info.

=== PEŁNE DANE RYNKOWE ===
BITCOIN (BTC):
- Cena Spot: ${m_data['btc']['price']}
- Wolumen 24h: ${m_data['btc']['vol24h']}
- Open Interest (Kontrakty Swap): {m_data['btc']['open_interest']} BTC
- Long/Short Account Ratio: {m_data['btc']['long_short_ratio']}
- Hashrate sieci: {m_data['btc_hashrate']} GH/s
- Liczba transakcji 24h: {m_data['btc_n_tx']}

STARKNET (STRK) - PRIORYTETOWY AKTUALNY TRADE:
- Cena Spot: ${m_data['strk']['price']}
- Wolumen 24h: ${m_data['strk']['vol24h']}
- Open Interest (Kontrakty Swap): {m_data['strk']['open_interest']} STRK
- Long/Short Account Ratio: {m_data['strk']['long_short_ratio']}

SONIC (S):
- Cena Spot: ${m_data['s']['price']}
- Wolumen 24h: ${m_data['s']['vol24h']}

=== ZASADY I INSTRUKCJE FORMATOWANIA ===
1. BEZWZGLĘDNY ZAKAZ używania składni LaTeX (np. zakaz backslashy). Ceny i liczby pisz wyłącznie jako $82,750 lub $0.0583.
2. Raport musi zawierać:
   - SEKCJĘ 1: Kondycja Rynku & Smart Money (BTC & On-Chain)
   - SEKCJĘ 2: Szczegółowa Analiza Starknet (STRK) w oparciu o Open Interest i Long/Short Ratio (ocena ryzyka likwidacji, wyczerpania spadków i presji rynkowej).
   - SEKCJĘ 3: Sonic (S) oraz Top Selekcję Altcoinową z twardymi strefami (Entry, SL, TP i Safe Position Size).
"""

    # Lista modeli do wypróbowania w razie błędu 503 (przeciążenia)
    models_to_try = ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-1.5-flash"]

    for model_name in models_to_try:
        for attempt in range(2):  # Dwa podejścia na dany model
            try:
                print(f"[AI] Próbuję wygenerować raport (model: {model_name}, próba {attempt+1})...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                if response and response.text:
                    print(f"[AI] Sukces z modelem {model_name}!")
                    return response.text
            except Exception as e:
                print(f"[AI Ostrzeżenie] Model {model_name} nie odpowiedział (Błąd: {e}). Czekam 5 sekund...")
                time.sleep(5)

    return "Błąd krytyczny: Wszystkie modele Gemini są obecnie przeciążone (503 Unavailable). Spróbuj uruchomić akcję ponownie za kilka minut."

# ==========================================
# 4. WYSYŁKA RAPORTU NA SERWER FTP (UTF-8)
# ==========================================
def upload_to_ftp(content_text, remote_filename="latest_report.txt"):
    """Zapisuje plik lokalnie w UTF-8 i wysyła na serwer FTP do public_html."""
    if not FTP_HOST or not FTP_USER or not FTP_PASS:
        print("[FTP] Brak danych logowania FTP. Pomijam wysyłkę.")
        return

    local_temp_file = "temp_report.txt"
    try:
        with open(local_temp_file, "w", encoding="utf-8") as f:
            f.write(content_text)

        print(f"[FTP] Łączenie z serwerem FTP: {FTP_HOST}...")
        with ftplib.FTP(FTP_HOST, FTP_USER, FTP_PASS) as ftp:
            target_dir = "domains/piwecki.pl/public_html"
            print(f"[FTP] Przechodzę do katalogu: {target_dir}")
            
            try:
                ftp.cwd(target_dir)
            except Exception as e:
                print(f"[FTP BŁĄD] Przejście do katalogu {target_dir} nieudane: {e}")
                return

            with open(local_temp_file, "rb") as file_to_send:
                ftp.storbinary(f"STOR {remote_filename}", file_to_send)

        print(f"[FTP] Sukces! Plik {remote_filename} zapisany pod adresem https://piwecki.pl/{remote_filename}")
    except Exception as e:
        print(f"[FTP BŁĄD] Wyjątek podczas wysyłania FTP: {e}")
    finally:
        if os.path.exists(local_temp_file):
            os.remove(local_temp_file)

# ==========================================
# 5. WYSYŁKA NA TELEGRAM
# ==========================================
def send_telegram_messages(report_text):
    """Wysyła raport na Telegram bez parse_mode."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Brak danych Telegram API. Pomijam.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

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
                print(f"[TELEGRAM BŁĄD] {res.text}")
        except Exception as e:
            print(f"[TELEGRAM BŁĄD] Wyjątek: {e}")

# ==========================================
# GŁÓWNY PROGRAM
# ==========================================
if __name__ == "__main__":
    print("=== Start Market Maker Monitor Bot ===")
    
    # 1. Pobranie danych z OKX i Blockchain
    m_data = fetch_all_market_data()

    # 2. Generowanie raportu przez Gemini API (z zabezpieczeniem 503)
    report = generate_report(m_data)

    # 3. Zapis lokalnej kopii w repozytorium (UTF-8)
    with open("latest_report.md", "w", encoding="utf-8") as f:
        f.write(report)

    # 4. Wysyłka pliku na serwer FTP (stały URL do odczytu na czacie)
    upload_to_ftp(report, remote_filename="latest_report.txt")

    # 5. Powiadomienie na Telegram
    send_telegram_messages(report)

    print("=== Zakończono pomyślnie ===")
