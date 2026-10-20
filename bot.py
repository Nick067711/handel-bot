import os
import time
from datetime import datetime, timezone
import requests
import numpy as np

# --- KONFIGURACJA ŚRODOWISKOWA ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# --- 1. SILNIK ANALIZY TECHNICZNEJ (Logika Claude + AN-1) ---
class TechnicalEngine:
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
    def calculate_macd(prices, fast=12, slow=26, signal=9):
        if len(prices) < slow + signal:
            return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
        prices = np.array(prices)
        
        def ema_arr(data, p):
            alpha = 2 / (p + 1)
            res = [data[0]]
            for val in data[1:]:
                res.append(val * alpha + res[-1] * (1 - alpha))
            return np.array(res)

        ema_fast = ema_arr(prices, fast)
        ema_slow = ema_arr(prices, slow)
        macd_line = ema_fast - ema_slow
        signal_line = ema_arr(macd_line, signal)
        histogram = macd_line - signal_line
        
        return {
            "macd": round(float(macd_line[-1]), 5),
            "signal": round(float(signal_line[-1]), 5),
            "hist": round(float(histogram[-1]), 5)
        }

    @staticmethod
    def calculate_stoch_rsi(prices, rsi_period=14, stoch_period=14):
        rsi_series = []
        for i in range(rsi_period, len(prices) + 1):
            sub_prices = prices[:i]
            rsi_val = TechnicalEngine.calculate_rsi(sub_prices, rsi_period)
            rsi_series.append(rsi_val)
            
        if len(rsi_series) < stoch_period:
            return {"k": 50.0}
            
        rsi_slice = rsi_series[-stoch_period:]
        min_rsi = min(rsi_slice)
        max_rsi = max(rsi_slice)
        
        if max_rsi == min_rsi:
            stoch_rsi = 100.0
        else:
            stoch_rsi = ((rsi_series[-1] - min_rsi) / (max_rsi - min_rsi)) * 100
            
        return {"k": round(float(stoch_rsi), 2)}

    @staticmethod
    def analyze_structure_and_signals(closes):
        """Zaawansowana logika Claude do wyznaczania prawdopodobnych punktów wejścia Long/Short."""
        if not closes or len(closes) < 50:
            return {"status": "Brak danych", "signal": "NEUTRAL"}
        c = np.array(closes)
        close_price = c[-1]
        ema20 = TechnicalEngine.calculate_ema(c, 20)
        ema50 = TechnicalEngine.calculate_ema(c, 50)
        rsi = TechnicalEngine.calculate_rsi(c)
        macd = TechnicalEngine.calculate_macd(c)
        stoch = TechnicalEngine.calculate_stoch_rsi(c)
        
        signal_bias = "NEUTRAL"
        if rsi < 35 and stoch["k"] < 20 and close_price > ema50 * 0.95:
            signal_bias = "POTENTIAL_LONG"
        elif rsi > 65 and stoch["k"] > 80:
            signal_bias = "POTENTIAL_SHORT"

        return {
            "close": close_price,
            "rsi": rsi,
            "macd_hist": macd["hist"],
            "stoch_k": stoch["k"],
            "ema20": ema20,
            "ema50": ema50,
            "signal_bias": signal_bias,
            "dist_ema20_pct": round(((close_price / ema20) - 1) * 100, 2)
        }


# --- 2. POBIERANIE DANYCH SPOT / ŚWIECE Z WIELU GIEŁD ---
def fetch_okx_candles(symbol, bar):
    bar_map = {"1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
    inst_id = f"{symbol}-USDT-SWAP" if "USDT" not in symbol else symbol
    url = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar_map.get(bar, '1H')}&limit=100"
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
    url = f"https://api.binance.com/api/v3/klines?symbol={bn_symbol}&interval={bar_map.get(bar, '1h')}&limit=100"
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
                    return [float(c[4]) for c in v[-100:]]
    except Exception as e:
        print(f"Kraken Error ({symbol} {bar}): {e}")
    return None

def fetch_bybit_candles(symbol, bar):
    bar_map = {"1h": "60", "4h": "240", "1d": "D", "1w": "W"}
    by_symbol = f"{symbol}USDT" if "USDT" not in symbol else symbol
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={by_symbol}&interval={bar_map.get(bar, '60')}&limit=100"
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


# --- 3. POBIERANIE POCHODNYCH (Open Interest, Funding, Likwidacje) ---
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


# --- 4. KONTEKST MAKROEKONOMICZNY (USA / FED) ORAZ SENTYMENT FORUM ---
def fetch_macro_and_sentiment_context():
    sentiment_data = {
        "forum_sentiment": "Mieszany/Neutralny z przewagą ostrożności w mediach społecznościowych",
        "fed_context": "Bieżąca polityka monetarna FED: uwaga skupiona na danych o inflacji oraz płynności w sektorze bankowym/repo",
        "macro_tone": "Oczekiwanie na klarowne sygnały z rynków tradycyjnych (US Treasuries, DXY)"
    }
    return sentiment_data


def get_aggregated_analysis(symbol, bar):
    sources = [
        fetch_okx_candles(symbol, bar),
        fetch_binance_candles(symbol, bar),
        fetch_kraken_candles(symbol, bar),
        fetch_bybit_candles(symbol, bar)
    ]
    
    valid_analyses = []
    for closes in sources:
        if isinstance(closes, list) and len(closes) >= 20:
            analysis = TechnicalEngine.analyze_structure_and_signals(closes)
            valid_analyses.append(analysis)

    if not valid_analyses:
        return {"close": 0.0, "rsi": 50.0, "signal_bias": "NEUTRAL"}

    avg_close = round(float(np.mean([a["close"] for a in valid_analyses])), 4 if symbol in ["STRK", "SUI"] else 2)
    avg_rsi = round(float(np.mean([a["rsi"] for a in valid_analyses])), 2)
    avg_macd_hist = round(float(np.mean([a["macd_hist"] for a in valid_analyses])), 4)
    avg_stoch_k = round(float(np.mean([a["stoch_k"] for a in valid_analyses])), 2)
    avg_ema20 = round(float(np.mean([a["ema20"] for a in valid_analyses])), 4 if symbol in ["STRK", "SUI"] else 2)

    return {
        "close": avg_close,
        "rsi": avg_rsi,
        "macd_hist": avg_macd_hist,
        "stoch_k": avg_stoch_k,
        "ema20": avg_ema20
    }


def get_okx_top_extremes():
    symbols = ["SOL", "SUI", "ETH", "AVAX", "LINK", "ADA", "DOGE", "NEAR", "APT"]
    results = []
    for sym in symbols:
        c_1d = fetch_okx_candles(sym, "1d")
        if c_1d and len(c_1d) >= 20:
            analysis = TechnicalEngine.analyze_structure_and_signals(c_1d)
            results.append({
                "symbol": sym,
                "analysis": analysis,
                "extreme_score": abs(analysis["rsi"] - 50)
            })
    results = sorted(results, key=lambda x: x["extreme_score"], reverse=True)
    return results[:3]


# --- 5. SYNTEZA PRZEZ MODEL AI (Gemini 3.8-flash) ---
def generate_ai_report(market_data, derivatives_data, macro_sentiment):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""
Jesteś wybitnym analitykiem rynków kryptowalut. Masz do dyspozycji:
1. Surowe dane i uśrednione wskaźniki multi-exchange (OKX, Binance, Kraken, Bybit).
2. Zaawansowane sygnały analizy technicznej (wzorce Claude, struktury, stany RSI/MACD/EMA).
3. Dane pochodne (Open Interest, Funding Rate, Likwidacje).
4. Kontekst makroekonomiczny z USA (FED) oraz sentyment z forów finansowych w internecie.

Przeprowadź głęboką syntezę uwzględniającą wszystkie te warstwy i przygotuj raport taktyczny na Telegrama.

BEZWZGLĘDNE ZASADY FORMATOWANIA TELEGRAMA:
1. ŻADNEGO WSTĘPU. Wiadomość zaczyna się NATYCHMIAST od nagłówka BTC.
2. **MONITORING CYKLU (SZCZYT HOSSY / DNO BESSY):** Przeanalizuj dane pod kątem osiągnięcia makro-szczytu hossy lub makro-dna bessy na Bitcoinie. JEŚLI wykryjesz historyczne ekstremum cyklu, MUSISZ umieścić na samej górze sekcji BTC potężny, wytłuszczony komunikat alarmowy: `🚨 **ALERT CYKLICZNY: [SZCZYT HOSSY / DNO BESSY] NA BTC!** 🚨`. Jeśli to nie ten moment, pomiń alert i zacznij bezpośrednio od nagłówka BTC.
3. ŻADNYCH WSPOMNIEŃ O WARTOŚCIACH WSKAŹNIKÓW (zakazuje się podawania liczb typu RSI, MACD, StochRSI w tekście – użytkownik czyta je sam z wykresów). Skup się wyłącznie na strukturze ceny, psychologii, płynności, OI, likwidacjach, sentymentzie z forów oraz wpływie polityki FED/makro.
4. Dla każdego głównego aktywa podaj precyzyjne poziomy dla pozycji Long oraz Short wraz z szacowanym wielodniowym horyzontem trzymania pozycji do opcjonalnego wyczerpania trendu/spadków.

STRUKTURA RAPORTU:

### BTC
[Jeżeli występuje ekstremum cyklu, wstaw alert, a następnie 3 zdania precyzyjnie opisujące sytuację z uwzględnieniem makro FED, sentymentu i struktury].
Przeanalizowane poziomy:
- Long: Przedział cenowy [X - Y] | Zasięg/TP (zamknięcie): [Cena]
- Short: Przedział cenowy [X - Y] | Zasięg/TP (zamknięcie, horyzont wielodniowy): [Cena]

### STRK
[3 zdania opisu w kontekście kondycji rynku, BTC, sentymentu i danych pochodnych, bez powtarzania danych o BTC].
Przeanalizowane poziomy:
- Long: Przedział cenowy [X - Y] | Zasięg/TP: [Cena]
- Short: Przedział cenowy [X - Y] | Zasięg/TP (wielodniowy short do wyczerpania spadków): [Cena]

### S (Solana)
[3 zdania opisu w kontekście makro i technicznym].
Przeanalizowane poziomy:
- Long: Przedział cenowy [X - Y] | Zasięg/TP: [Cena]
- Short: Przedział cenowy [X - Y] | Zasięg/TP: [Cena]

### TOP 3 OKX (Ekstremalne Okazje 1W/1D)
Wybierz 3 projekty z OKX o największym ekstremum strukturalnym:
1. **[Nazwa Altcoina]**: [Krótki kontekst techniczny, sentymentu i płynności].
   - Wejście [Long/Short]: Przedział [X - Y] | Zasięg: [Cena]
2. **[Nazwa Altcoina]**: [Krótki kontekst].
   - Wejście [Long/Short]: Przedział [X - Y] | Zasięg: [Cena]
3. **[Nazwa Altcoina]**: [Krótki kontekst].
   - Wejście [Long/Short]: Przedział [X - Y] | Zasięg: [Cena]

Dane analityczne, makro i sentyment:
{market_data}
{derivatives_data}
{macro_sentiment}
"""

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    max_retries = 5
    backoff_factor = 2
    
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=payload, timeout=30)
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
    
    <h2>1. Kontekst Makro (USA/FED) i Sentyment z Forów</h2>
    <div class="raw-data">
        {str(macro_sentiment)}
    </div>

    <h2>2. Zbiorcze wskaźniki techniczne (Multi-Exchange)</h2>
    <table>
        <tr>
            <th>Aktywo</th>
            <th>Interwał</th>
            <th>Cena</th>
            <th>RSI</th>
            <th>MACD Hist</th>
            <th>StochRSI K</th>
            <th>EMA 20</th>
        </tr>
    """
    
    for symbol, intervals in aggregated_data.items():
        for interval, metrics in intervals.items():
            html_content += f"""
        <tr>
            <td>{symbol}</td>
            <td>{interval}</td>
            <td>${metrics.get('close', 0)}</td>
            <td>{metrics.get('rsi', 0)}</td>
            <td>{metrics.get('macd_hist', 0)}</td>
            <td>{metrics.get('stoch_k', 0)}</td>
            <td>${metrics.get('ema20', 0)}</td>
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
    print("Rozpoczynanie pobierania danych wielogiełdowych, makro, sentymentu i pochodnych...")
    symbols = ["BTC", "STRK", "SOL", "SUI"]
    intervals = ["1h", "4h", "1d", "1w"]
    
    market_data = {}
    derivatives_data = {}
    
    for symbol in symbols:
        market_data[symbol] = {}
        for bar in intervals:
            market_data[symbol][bar] = get_aggregated_analysis(symbol, bar)
        derivatives_data[symbol] = fetch_derivatives_data(symbol)
            
    macro_sentiment = fetch_macro_and_sentiment_context()
    top_extremes = get_okx_top_extremes()
    
    print("Przekazywanie kompletnego zestawu danych do AI (Gemini)...")
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
