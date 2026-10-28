# handel-bot – Sonic (S) i Bitcoin

Bot uruchamiany co godzinę przez GitHub Actions:

- **Telegram** – tylko gdy Sonic da sygnał LONG lub SHORT w wąskiej strefie cenowej, najwyżej raz dziennie.
- **Raport `index.html` na FTP** – raz dziennie (pierwsze uruchomienie po 05:00 UTC), plus kopia w `analiza/archiwum/RRRR-MM-DD.html`.

## Pliki

| Plik | Rola |
|---|---|
| `bot.py` | Uruchamia całość: dane → sygnał → Telegram → raport → FTP. |
| `sonic_core.py` | Wskaźniki, swingi (ZigZag), konsolidacje, Fibonacci, reguły setupów i backtest. |
| `sonic_history.py` | Baza wiedzy z historii 2019–2026: swingi na 4 poziomach, fale, konsolidacje, profil wskaźników w punktach zwrotnych, wyniki backtestu, wnioski. **Generowany** – nie edytuj ręcznie. |
| `analysis.py` | Analiza bieżąca: położenie ceny, poziomy wsparcia/oporu, księga zleceń, kontekst BTC. |
| `market_data.py` | Pobieranie danych z giełd i darmowych API. |
| `ai.py` | Groq: wyszukiwanie w sieci (nastroje, makro) i komentarz do raportu. |
| `report_html.py` | Wygląd raportu (HTML + wykresy SVG). |
| `data/SONIC_FTM_S_1d.csv` | Historia świec dziennych FTM/S z Binance – bot dokleja do niej nowe świece z Coinbase. |
| `tools/build_history.py` | Odświeża `sonic_history.py` na nowszym CSV (uruchamiane ręcznie, lokalnie). |

## Sekrety (Settings → Secrets and variables → Actions)

Wymagane: `FTP_HOST`, `FTP_USER`, `FTP_PASS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GROQ_API_KEY`.

Opcjonalne zmienne (w `env:` workflow): `FTP_DIR` (domyślnie `domains/piwecki.pl/public_html/analiza`),
`FTP_STATE_DIR` (domyślnie `domains/piwecki.pl/bot_state` – poza public_html), `REPORT_HOUR_UTC` (domyślnie 5),
`FTP_USE_TLS` (domyślnie 1 – szyfrowane FTPS, przy braku obsługi automatycznie zwykłe FTP), `GROQ_MODEL`.

## Ręczne uruchomienie

Actions → „Sonic & BTC – monitor i raport” → Run workflow. Domyślnie wymusza nowy raport HTML.

## Sygnały (reguły przetestowane na historii 2019–2026)

- **LONG**: korekta ostatniego wzrostu (swing ≥25%) dotyka linii Fibo 0,618/0,786 lub historycznego poziomu (±3,5%), nie przebija 0,886, świeca dzienna wzrostowa. Historycznie: 48 sygnałów, trafność 50%, średnio +0,24R (pod SMA200: +0,43R).
- **SHORT**: odbicie po spadku (swing ≥40%) dotyka 0,618/0,786 (±3,5%), nie przebija 0,886, świeca dzienna spadkowa. Historycznie: 29 sygnałów, 55%, +0,16R – niższa pewność.

Statystyki przeliczają się przy każdym uruchomieniu na pełnej historii. To prawdopodobieństwa, nie gwarancje.

## Odświeżenie bazy wiedzy

Podmień `data/SONIC_FTM_S_1d.csv` na nowszy eksport (ten sam format Binance), potem lokalnie:

```
pip install -r requirements.txt
python tools/build_history.py
```

i zrób commit `sonic_history.py` oraz CSV.

## Test lokalny bez FTP i Telegrama

```
DRY_RUN=1 python bot.py
```

Raport zapisze się jako `index.html` w bieżącym folderze, a treść ewentualnej wiadomości Telegram pojawi się w konsoli.
