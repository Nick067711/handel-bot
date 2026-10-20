import os
import time
from datetime import datetime, timezone
import requests
import numpy as np

# --- KONFIGURACJA ŚRODOWISKOWA ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# --- 1. SILNIK ANALIZY TECHNICZNEJ I WZORCÓW HISTORYCZNYCH (Top-Down + Dywergencje + EMA200 Regime) ---
class HistoricalPatternEngine:
    @staticmethod
    def calculate_rsi(prices, period=14):
        if len(prices) < period + 1:
            return 50.0
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        
        for i in range(period, len(prices) - 1):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(float(100 - (100 / (1 + rs))), 2)

    @staticmethod
    def calculate_ema(prices, period):
        if len(prices) < period:
            return float(prices[-1])
        prices = np.array(prices)
        weights = np.exp(np.linspace(-1., 0., period))
        weights /= weights.sum()
        ema = np.convolve(prices, weights, mode='full')[:len(prices)]
        ema[:period] = ema[period]
        return round(float(ema[-1]), 5)

    @staticmethod
    def detect_multi_timeframe_divergence(prices):
        """Wykrywanie dywergencji (Cena vs RSI) dla wzorców historycznych."""
        if len(prices) < 30:
            return "BRAK"
        p = np.array(prices)
        rsi_series = []
        for i in range(14, len(p) + 1):
            rsi_series.append(HistoricalPatternEngine.calculate_rsi(p[:i], 14))
        
        if len(rsi_series) < 15:
            return "BRAK"
            
        recent_prices = p[-15:]
        recent_rsi = np.array(rsi_series[-15:])
        
        p_min1_idx = np.argmin(recent_prices[:7])
        p_min2_idx = 7 + np.argmin(recent_prices[7:])
        if recent_prices[p_min2_idx] < recent_prices[p_min1_idx] and recent_rsi[p_min2_idx] > recent_rsi[p_min1_idx]:
            return "BYCZA_DYWERGENCJA"
            
        p_max1_idx = np.argmax(recent_prices[:7])
        p_max2_idx = 7 + np.argmax(recent_prices[7:])
        if recent_prices[p_max2_idx] > recent_prices[p_max1_idx] and recent_rsi[p_max2_idx] < recent_rsi[p_max1_idx]:
            return "NIEDŹWIEDZIA_DYWERGENCJA"
            
        return "BRAK"

    @classmethod
    def evaluate_asset_patterns(cls, timeframes_data):
        """
        Analiza Top-Down (1W -> 1D -> 4H -> 1H):
        - Sprawdzenie reżimu rynku na 1D (Cena vs EMA200 -> Hossa / Bessa).
        - Weryfikacja dywergencji i struktur na wyższych i niższych interwałach.
        """
        c_1w = timeframes_data.get("1w", [])
        c_1d = timeframes_data.get("1d", [])
        c_4h = timeframes_data.get("4h", [])
        c_1h = timeframes_data.get("1h", [])
        
        if not c_1d or len(c_1d) < 200:
            return {"pattern_signal": "NEUTRAL", "regime": "UNKNOWN", "notes": "Niewystarczająca historia danych"}
            
        close_price = c_1d[-1]
        ema200_1d = cls.calculate_ema(c_1d, 200)
        
        # Określenie reżimu makro: Hossa (powyżej EMA200) vs Bessa (poniżej EMA200)
        regime = "HOSSA (Bullish Regime)" if close_price > ema200_1d else "BESSA (Bearish Regime)"
        
        div_1w = cls.detect_multi_timeframe_divergence(c_1w)
        div_1d = cls.detect_multi_timeframe_divergence(c_1d)
        div_4h = cls.detect_multi_timeframe_divergence(c_4h)
        
        rsi_1h = cls.calculate_rsi(c_1h) if c_1h else 50.0
        rsi_4h = cls.calculate_rsi(c_4h) if c_4h else 50.0
        
        # Wzorzec historyczny oparty o kontekst top-down i dywergencje
        pattern_signal = "NEUTRAL"
        notes = f"Reżim: {regime}. Dywergencje -> 1W: {div_1w}, 1D: {div_1d}, 4H: {div_4h}."
        
        if regime.startswith("HOSSA"):
            if div_1w == "BYCZA_DYWERGENCJA" or div_1d == "BYCZA_DYWERGENCJA" or rsi_4h < 35:
                pattern_signal = "POTENTIAL_LONG_SETUP"
                notes += " Wzorzec historyczny wskazuje na silne wsparcie w trendzie wzrostowym (dip buying)."
            elif div_1d == "NIEDŹWIEDZIA_DYWERGENCJA" and rsi_1h > 70:
                pattern_signal = "POTENTIAL_SHORT_CORRECTION"
                notes += " Ostrzeżenie o lokalnym przegrzaniu w hossie."
        else: # Bessa
            if div_1d == "NIEDŹWIEDZIA_DYWERGENCJA" or rsi_4h > 65:
                pattern_signal = "POTENTIAL_SHORT_SETUP"
                notes += " Wzorzec historyczny wskazuje na odbicie do oporu w trendzie spadkowym (sell the rip)."
            elif div_1w == "BYCZA_DYWERGENCJA":
                pattern_signal = "POTENTIAL_MACRO_BOTTOM_WATCH"
                notes += " Sygnał wstępny budowania makro dna w bessie."

        return {
            "pattern_signal": pattern_signal,
            "regime": regime,
            "divergences": {"1w": div_1w, "1d": div_1d, "4h": div_4h},
            "notes": notes
        }


# --- 2. POBIERANIE DANYCH SPOT / ŚWIECE Z WIELU GIEŁD ---
def fetch_okx_candles(symbol, bar):
    bar_map = {"1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
    inst_id = f"{symbol}-USDT-SWAP" if "USDT" not in symbol else symbol
    url = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar_map.get(bar, '1H')}&limit=250"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data"):
                return [float(c[4]) for c in data["data"][::-1]]
    except Exception as e:
        print(f"OKX Error ({symbol} {bar}): {e}")
    return None

def fetch_binance_candles(symbol, bar):
    bar_map = {"1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}
    bn_symbol = f"{symbol}USDT" if "USDT" not in symbol else symbol
    url = f"https://api.binance.com/api/v3/klines?symbol={bn_symbol}&interval={bar_map.get(bar, '1h')}&limit=250"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return [float(c[4]) for c in data]
    except Exception as e:
        print(f"Binance Error ({symbol} {bar}): {e}")
    return None

def fetch_kraken_candles(symbol, bar):
    bar_map = {"1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
    pair = f"XBTUSDT" if symbol == "BTC" else f"{symbol}USDT"
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={bar_map.get(bar, 60)}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result", {})
            for k, v in result.items():
                if k != "last":
                    return [float(c[4]) for c in v[-250:]]
    except Exception as e:
        print(f"Kraken Error ({symbol} {bar}): {e}")
    return None

def fetch_bybit_candles(symbol, bar):
    bar_map = {"1h": "60", "4h": "240", "1d": "D", "1w": "W"}
    by_symbol = f"{symbol}USDT" if "USDT" not in symbol else symbol
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={by_symbol}&interval={bar_map.get(bar, '60')}&limit=250"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            list_data = data.get("result", {}).get("list", [])
            if list_data:
                return [float(c[4]) for c in list_data[::-1]]
    except Exception as e:
        print(f"Bybit Error ({symbol} {bar}): {e}")
    return None


# --- 3. POBIERANIE POCHODNYCH I ON-CHAIN ---
def fetch_derivatives_data(symbol):
    derivatives = {"okx_oi": 0.0, "bybit_oi": 0.0, "funding_rate": 0.0, "liquidations_summary": "Brak anomalii"}
    try:
        inst_id = f"{symbol}-USDT-SWAP"
        url = f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst_id}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data"):
                derivatives["okx_oi"] = float(data["data"][0]["oi"])
    except Exception as e:
        print(f"OKX OI Error ({symbol}): {e}")

    try:
        url = "https://api.bybit.com/v5/market/open-interest"
        params = {"category": "linear", "symbol": f"{symbol}USDT", "intervalTime": "4h", "limit": 1}
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            lst = data.get("result", {}).get("list", [])
            if lst:
                derivatives["bybit_oi"] = float(lst[0]["openInterest"])
    except Exception as e:
        print(f"Bybit OI Error ({symbol}): {e}")

    try:
        url = "https://api.bybit.com/v5/market/tickers"
        params = {"category": "linear", "symbol": f"{symbol}USDT"}
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            lst = data.get("result", {}).get("list", [])
            if lst:
                item = lst[0]
                derivatives["funding_rate"] = float(item.get("fundingRate", 0))
                derivatives["liquidations_summary"] = f"Funding: {item.get('fundingRate')}, OI Val: {item.get('openInterestValue', 'N/A')}"
    except Exception as e:
        print(f"Tickers Error ({symbol}): {e}")

    return derivatives


# --- 4. KONTEKST MAKROEKONOMICZNY I SENTYMENT ---
def fetch_macro_and_sentiment_context():
    onchain_info = "Brak danych on-chain"
    try:
        resp = requests.get("https://blockchain.info/stats?format=json", timeout=5)
        if resp.status_code == 200:
            bc_data = resp.json()
            onchain_info = f"Market Price USD: {bc_data.get('market_price_usd')}, Hashrate: {bc_data.get('hash_rate')}"
    except Exception as e:
        print(f"Blockchain.info Error: {e}")

    sentiment_data = {
        "forum_sentiment": "Mieszany/Neutralny z rosnącą ostrożnością w kluczowych strefach popytowych",
        "fed_context": "Polityka FED: fokus na płynność repo i odczyty inflacyjne",
        "onchain_cost_basis": "Szacunkowa baza kosztowa ETF: ~81.5k USD | True Market Mean: ~77.5k USD | STH Cost Basis: ~74.6k USD",
        "blockchain_stats": onchain_info
    }
    return sentiment_data


def get_aggregated_analysis(symbol):
    intervals = ["1h", "4h", "1d", "1w"]
    timeframes_candles = {}
    metrics_summary = {}

    for bar in intervals:
        sources = [
            fetch_okx_candles(symbol, bar),
            fetch_binance_candles(symbol, bar),
            fetch_kraken_candles(symbol, bar),
            fetch_bybit_candles(symbol, bar)
        ]
        
        valid_closes = [closes for closes in sources if isinstance(closes, list) and len(closes) >= 20]
        if valid_closes:
            # Używamy najdłuższej dostępnej tablicy świec dla danego interwału
            best_closes = max(valid_closes, key=len)
            timeframes_candles[bar] = best_closes
            
            c = np.array(best_closes)
            close_price = float(c[-1])
            ema20 = HistoricalPatternEngine.calculate_ema(c, 20)
            rsi = HistoricalPatternEngine.calculate_rsi(c)
            macd = HistoricalPatternEngine.calculate_macd(c)
            
            metrics_summary[bar] = {
                "close": close_price,
                "rsi": rsi,
                "macd_hist": macd["hist"],
                "ema20": ema20
            }
        else:
            timeframes_candles[bar] = []
            metrics_summary[bar] = {"close": 0.0, "rsi": 50.0, "macd_hist": 0.0, "ema20": 0.0}

    # Uruchomienie silnika wzorców historycznych (Top-Down + Regim 1D EMA200 + Dywergencje)
    pattern_evaluation = HistoricalPatternEngine.evaluate_asset_patterns(timeframes_candles)
    
    return {
        "metrics": metrics_summary,
        "historical_patterns": pattern_evaluation
    }


def get_okx_top_extremes():
    symbols = ["SOL", "SUI", "ETH", "AVAX", "LINK", "ADA", "DOGE", "NEAR", "APT"]
    results = []
    for sym in symbols:
        c_1d = fetch_okx_candles(sym, "1d")
        if c_1d and len(c_1d) >= 50:
            rsi = HistoricalPatternEngine.calculate_rsi(c_1d)
            results.append({
                "symbol": sym,
                "rsi": rsi,
                "extreme_score": abs(rsi - 50)
            })
    results = sorted(results, key=lambda x: x["extreme_score"], reverse=True)
    return results[:3]


# --- 5. SYNTEZA PRZEZ MODEL AI (Gemini 3.8-flash) ---
def generate_ai_report(market_data, derivatives_data, macro_sentiment):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""
Jesteś wybitnym analitykiem rynków kryptowalut. Masz do dyspozycji:
1. Zbiorcze wskaźniki techniczne multi-exchange oraz **wstępne sygnały z algorytmicznego silnika wzorców historycznych** (ocena reżimu Hossa/Bessa względem 1D EMA200, analizy top-down 1W-1D-4H-1H oraz wykryte dywergencje).
2. Dane pochodne (Open Interest, Funding Rate, Likwidacje).
3. Dane on-chain i makro (baza kosztowa ETF, True Market Mean, polityka FED).

Twoim zadaniem jest przeprowadzenie krytycznej syntezy tych danych. Przeanalizuj, czy sygnały wygenerowane przez silnik wzorców historycznych są spójne z płynnością, OI, likwidacjami i makro, czy też wymagają korekty. Wyciągnij ostateczne wnioski taktyczne na Telegrama.

BEZWZGLĘDNE ZASADY FORMATOWANIA TELEGRAMA:
1. ŻADNEGO WSTĘPU. Wiadomość zaczyna się NATYCHMIAST od nagłówka BTC.
2. **MONITORING CYKLU (SZCZYT HOSSY / DNO BESSY / LOKALNE DNA):** Przeanalizuj dane pod kątem ekstremów cyklu i dywerencji. JEŚLI wykryjesz historyczne ekstremum, umieść na samej górze sekcji BTC komunikat: `🚨 **ALERT CYKLICZNY: [SZCZYT HOSSY / DNO BESSY] NA BTC!** 🚨`. Jeśli nie, zacznij bezpośrednio od nagłówka BTC.
3. ŻADNYCH WSPOMNIEŃ O WARTOŚCIACH WSKAŹNIKÓW w tekście (zakaz podawania liczb RSI, MACD – użytkownik czyta je sam z wykresów). Skup się wyłącznie na strukturze ceny, dywergencjach, reżimie rynku, płynności, OI, likwidacjach i sentymentzie.
4. Dla każdego głównego aktywa podaj precyzyjne poziomy dla pozycji Long oraz Short wraz z szacowanym wielodniowym horyzontem trzymania pozycji.

STRUKTURA RAPORTU:

### BTC
[Precyzyjny opis sytuacji z uwzględnieniem wniosków z silnika historycznych wzorców, reżimu rynku, bazy kosztowej on-chain i makro].
Przeanalizowane poziomy:
- Long: Przedział cenowy [X - Y] | Zasięg/TP (zamknięcie): [Cena]
- Short: Przedział cenowy [X - Y] | Zasięg/TP (zamknięcie, horyzont wielodniowy): [Cena]

### STRK
[Opis w kontekście kondycji rynku, BTC, sentymentu i danych pochodnych].
Przeanalizowane poziomy:
- Long: Przedział cenowy [X - Y] | Zasięg/TP: [Cena]
- Short: Przedział cenowy [X - Y] | Zasięg/TP: [Cena]

### S (Solana)
[Opis w kontekście makro i technicznym].
Przeanalizowane poziomy:
- Long: Przedział cenowy [X - Y] | Zasięg/TP: [Cena]
- Short: Przedział cenowy [X - Y] | Zasięg/TP: [Cena]

### TOP 3 OKX (Ekstremalne Okazje 1W/1D)
Wybierz 3 projekty z OKX o największym ekstremum strukturalnym:
1. **[Nazwa Altcoina]**: [Krótki kontekst].
   - Wejście [Long/Short]: Przedział [X - Y] | Zasięg: [Cena]
2. **[Nazwa Altcoina]**: [Krótki kontekst].
   - Wejście [Long/Short]: Przedział [X - Y] | Zasięg: [Cena]
3. **[Nazwa Altcoina]**: [Krótki kontekst].
   - Wejście [Long/Short]: Przedział [X - Y] | Zasięg: [Cena]

Dane analityczne, wzorce historyczne i dywergencje:
{market_data}
{derivatives_data}
{macro_sentiment}
"""

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    max_retries = 5
    backoff_factor = 2
    
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=payload, timeout=45)
            if resp.status_code == 200:
                res_json = resp.json()
                return res_json["candidates"][0]["content"]["parts"][0]["text"]
            elif resp.status_code in [429, 503]:
                sleep_time = backoff_factor ** attempt
                print(f"API błąd {resp.status_code}. Ponawianie za {sleep_time}s...")
                time.sleep(sleep_time)
            else:
                print(f"Gemini API Error: {resp.status_code} - {resp.text}")
                break
        except Exception as e:
            print(f"Wyjątek podczas łączenia z Gemini: {e}")
            time.sleep(2 ** attempt)
            
    return "Błąd generowania raportu przez AI."


# --- 6. GENEROWANIE PLIKU HTML (`index.html`) ---
def generate_html_report(aggregated_data, derivatives_data, macro_sentiment, top_extremes):
    html_content = f"""<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <title>Raw Market, Macro & Derivatives Repository</title>
    <style>
        body {{ font-family: monospace; background: #121212; color: #00ff66; padding: 20px; }}
        h1, h2 {{ color: #ffffff; border-bottom: 1px solid #333; padding-bottom: 5px; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
        th, td {{ border: 1px solid #333; padding: 8px; text-align: left; }}
        th {{ background: #1e1e1e; color: #fff; }}
        .raw-data {{ background: #1a1a1a; padding: 15px; border-left: 3px solid #00ff66; white-space: pre-wrap; }}
    </style>
</head>
<body>
    <h1>RAW MARKET, MACRO & DERIVATIVES REPOSITORY</h1>
    <p>Ostatnia aktualizacja: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
    
    <h2>1. Kontekst Makro, On-Chain & Sentyment</h2>
    <div class="raw-data">
        {str(macro_sentiment)}
    </div>

    <h2>2. Sygnały Algorytmiczne Wzorców Historycznych i Wskaźniki Multi-Exchange</h2>
    <table>
        <tr>
            <th>Aktywo</th>
            <th>Reżim (1D EMA200)</th>
            <th>Sygnał Wzorca</th>
            <th>Dywergencje (1W / 1D / 4H)</th>
            <th>Notatki Silnika</th>
        </tr>
    """
    
    for symbol, data in aggregated_data.items().get("main_assets", aggregated_data).items():
        if isinstance(data, dict) and "historical_patterns" in data:
            pat = data["historical_patterns"]
            divs = pat.get("divergences", {})
            div_str = f"1W: {divs.get('1w')} | 1D: {divs.get('1d')} | 4H: {divs.get('4h')}"
            html_content += f"""
        <tr>
            <td>{symbol}</td>
            <td>{pat.get('regime')}</td>
            <td><b>{pat.get('pattern_signal')}</b></td>
            <td>{div_str}</td>
            <td>{pat.get('notes')}</td>
        </tr>
            """
            
    html_content += f"""
    </table>

    <h2>3. Open Interest & Liquidations (Derivatives)</h2>
    <table>
        <tr>
            <th>Aktywo</th>
            <th>OKX OI</th>
            <th>Bybit OI</th>
            <th>Funding Rate</th>
            <th>Podsumowanie Likwidacji</th>
        </tr>
    """
    
    for sym, deriv in derivatives_data.items():
        html_content += f"""
        <tr>
            <td>{sym}</td>
            <td>{deriv.get('okx_oi', 0)}</td>
            <td>{deriv.get('bybit_oi', 0)}</td>
            <td>{deriv.get('funding_rate', 0)}</td>
            <td>{deriv.get('liquidations_summary', '')}</td>
        </tr>
        """

    html_content += f"""
    </table>

    <h2>4. Surowy słownik danych analitycznych (JSON)</h2>
    <div class="raw-data">
        {str(aggregated_data)}<br><br>{str(derivatives_data)}
    </div>
</body>
</html>
    """
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("Wygenerowano plik index.html!")


# --- WYSYŁKA NA TELEGRAM ---
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"Telegram Send Error: {resp.text}")
    except Exception as e:
        print(f"Telegram Exception: {e}")


# --- GŁÓWNA FUNKCJA BOTA ---
def main():
    print("Rozpoczynanie pobierania danych, analizy top-down wzorców historycznych i pochodnych...")
    symbols = ["BTC", "STRK", "SOL", "SUI"]
    
    market_data = {}
    derivatives_data = {}
    
    for symbol in symbols:
        market_data[symbol] = get_aggregated_analysis(symbol)
        derivatives_data[symbol] = fetch_derivatives_data(symbol)
            
    macro_sentiment = fetch_macro_and_sentiment_context()
    top_extremes = get_okx_top_extremes()
    
    print("Przekazywanie kompletnego zestawu danych (wzorce historyczne + OI + makro) do AI (Gemini)...")
    telegram_text = generate_ai_report({
        "main_assets": market_data,
        "okx_extremes": top_extremes
    }, derivatives_data, macro_sentiment)
    
    print("Wysyłanie ostatecznego raportu na Telegram...")
    send_telegram_message(telegram_text)
    
    print("Generowanie pliku index.html dla serwera FTP...")
    generate_html_report(market_data, derivatives_data, macro_sentiment, top_extremes)
    print("Bot zakończył działanie pomyślnie.")

if __name__ == "__main__":
    main()
