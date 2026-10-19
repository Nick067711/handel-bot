import os
import subprocess
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
    """Wysyła wiadomość bezpośrednio na Telegram (zwykły tekst - brak błędów)."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("Wiadomość na Telegram została pomyślnie wysłana!")
        else:
            print(f"Błąd wysyłki na Telegram (kod {res.status_code}): {res.text}")
    except Exception as e:
        print(f"Błąd wysyłki na Telegram: {e}")

def save_report_to_github(report_content):
    """Zapisuje wygenerowany raport do pliku latest_report.md na GitHubie i wykonuje commit & push."""
    try:
        filename = "latest_report.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report_content)
            
        subprocess.run(["git", "config", "--global", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "--global", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", filename], check=True)
        subprocess.run(["git", "commit", "-m", "Automatyczna aktualizacja latest_report.md [skip ci]"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("Raport został pomyślnie zapisany w repozytorium GitHub!")
    except Exception as e:
        print(f"Błąd zapisu raportu na GitHub: {e}")

def run_analysis():
    raw_tickers = fetch_okx_data()
    btc_onchain = fetch_btc_onchain_data()

    # Filtrowanie i porządkowanie głównych aktywów (SPOT z USDT/USDC)
    relevant_tickers = []
    for item in raw_tickers:
        inst_id = item.get('instId', '')
        if inst_id.endswith("-USDT") or inst_id.endswith("-USDC") or "-USDT-" in inst_id or "-USDC-" in inst_id:
            try:
                vol = float(item.get('volCcy24h', 0))
            except (ValueError, TypeError):
                vol = 0
            relevant_tickers.append((vol, item))

    # Sortowanie po wolumenie malejąco
    relevant_tickers.sort(key=lambda x: x[0], reverse=True)
    sorted_items = [t[1] for t in relevant_tickers]

    # Przygotowanie tekstu pod kluczowe pozycje
    key_assets = [item for item in sorted_items if any(k in item.get('instId', '').upper() for k in ["BTC", "STRK", "S-USDC", "S-USDT", "S/USDC", "SONIC"])]
    
    key_summary = []
    for item in key_assets:
        key_summary.append(
            f"Symbol: {item.get('instId')}, Price: {item.get('last')}, "
            f"High24h: {item.get('high24h')}, Low24h: {item.get('low24h')}, Vol24h: {item.get('volCcy24h')}"
        )
    key_text = "\n".join(key_summary)

    # --- ZAPYTANIE 1: KONDYCJA RYNKU, BTC, STRK & SONIC ---
    prompt1 = f"""
Jesteś analitykiem krypto. Stwórz czytelną, zwięzłą CZĘŚĆ 1 raportu w języku polskim dla kluczowych aktywów.

ZASADY FORMATOWANIA I TREŚCI:
- Żadnych sztywnych ram kapitałowych. Podaj bezpieczne widełki pozycji (Safe Position Size) w oparciu o płynność.
- Żadnych lateksowych backslashów (np. pisz $82,750 zamiast $82\,750).

Dane On-Chain BTC:
{btc_onchain}

Dane kluczowych aktywów OKX:
{key_text}

STRUKTURA:
1. BITCOIN & SMART MONEY:
   - Kondycja makro BTC, sentyment i zwięzła ocena kierunku rynku.
   - Safe Position Size.
2. STARKNET (STRK):
   - Ocena strukturalna i jednoznaczna decyzja.
   - Safe Position Size.
3. SONIC (S / S/USDC):
   - Analiza właściwej pary S (bezwzględny zakaz pisania o podróbkach/memecoinach).
   - Safe Position Size.
"""

    print("Generowanie Części 1...")
    res1 = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt1,
    )
    
    header1 = "📊 CZĘŚĆ 1: KONDYCJA RYNKU, BTC, STRK & SONIC\n\n"
    part1_full = header1 + res1.text.strip()
    send_telegram_message(part1_full)

    # --- ZAPYTANIE 2: TOP SELEKCJA ALTCOINOWA ---
    altcoins = [item for item in sorted_items if not any(k in item.get('instId', '').upper() for k in ["BTC-", "ETH-", "USDT-", "USDC-"])][:40]
    
    alt_summary = []
    for item in altcoins:
        alt_summary.append(
            f"Symbol: {item.get('instId')}, Price: {item.get('last')}, "
            f"High24h: {item.get('high24h')}, Low24h: {item.get('low24h')}, Vol24h: {item.get('volCcy24h')}"
        )
    alt_text = "\n".join(alt_summary)

    prompt2 = f"""
Jesteś analitykiem krypto. Wybierz TYLKO 2 do 3 absolutnie najlepszych okazji technicznych z poniższej listy.

ZASADY:
- Szukaj po wielotygodniowych korektach od struktur HH/LH, ze skrajnymi odczytami RSI/MACD.
- Jeśli brak układów, napisz: "Brak bezpiecznych konfiguracji altcoinowych dla obecnej sesji."
- Bez backslashów w kwotach ($XX,XXX).

Lista altcoinów z OKX:
{alt_text}

STRUKTURA DLA KAŻDEGO WYBRANEGO ALTA:
- Symbol i opis układu (1 zdanie)
- Strefa Wejścia (Entry)
- Stop Loss (SL)
- Take Profit (TP)
- Bezpieczne widełki pozycji (Safe Position Size).
"""

    print("Generowanie Części 2...")
    res2 = client.models.generate_content(
        model='gemini-3.8-flash',
        contents=prompt2,
    )

    header2 = "🎯 CZĘŚĆ 2: TOP SELEKCJA ALTCOINOWA\n\n"
    part2_full = header2 + res2.text.strip()
    send_telegram_message(part2_full)

    # --- ZAPIS CAŁOŚCI DO PLIKU NA GITHUBIE ---
    full_combined_report = f"# Raport Rynkowy - {os.environ.get('GITHUB_RUN_ID', 'Lokalny')}\n\n{part1_full}\n\n---\n\n{part2_full}"
    save_report_to_github(full_combined_report)

if __name__ == "__main__":
    run_analysis()
