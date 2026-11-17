# handel-bot – Sonic (S) i Bitcoin

Bot uruchamiany co godzinę przez GitHub Actions:

- **ntfy** (temat `Sonic-buy-sell`) – tylko kluczowe sygnały planu etapowego, potwierdzone zamkniętą świecą 1h:
  zakup, sprzedaż, kupno na wybiciu, „przygotuj zlecenie” (cena 2% od widełek), nieudany odkup, unieważnienie planu.
- **Raport `index.html` na FTP** – raz dziennie (pierwsze uruchomienie po 05:00 UTC), plus kopia w `analiza/archiwum/RRRR-MM-DD.html`.

## Pliki

| Plik | Rola |
|---|---|
| `bot.py` | Uruchamia całość: dane → plan na świecach 1h → ntfy → raport → FTP. |
| `data/sonic_plan.json` | **Plan etapowy** z 17.11.2026: kroki sprzedaży i odkupu z widełkami, daty orientacyjne, unieważnienie, poziomy BTC. Tu zmieniasz poziomy. |
| `plan.py` | Odtwarza plan na zamkniętych świecach 1h i wyznacza sygnały, bieżący krok i „co robić teraz”. |
| `notify.py` | Treść i wysyłka powiadomień ntfy. |
| `report_plan.py` | Mapa planu w raporcie (wykres historii od sierpnia 2026 + prognoza), tabela kroków, poziomy BTC. |
| `sonic_core.py` | Wskaźniki, swingi (ZigZag), konsolidacje, Fibonacci, reguły setupów i backtest. |
| `sonic_history.py` | Baza wiedzy z historii 2019–2026: swingi na 4 poziomach, fale, konsolidacje, profil wskaźników w punktach zwrotnych, wyniki backtestu, wnioski. **Generowany** – nie edytuj ręcznie. |
| `analysis.py` | Analiza bieżąca: położenie ceny, poziomy wsparcia/oporu, księga zleceń, kontekst BTC. |
| `market_data.py` | Pobieranie danych z giełd i darmowych API. |
| `ai.py` | Groq: wyszukiwanie w sieci (nastroje, makro) i komentarz do raportu. |
| `report_html.py` | Wygląd raportu (HTML + wykresy SVG). |
| `data/SONIC_FTM_S_1d.csv` | Historia świec dziennych FTM/S z Binance – bot dokleja do niej nowe świece z Coinbase. |
| `tools/build_history.py` | Odświeża `sonic_history.py` na nowszym CSV (uruchamiane ręcznie, lokalnie). |

## Kopia wyników w repozytorium (folder `raport/`)

Po każdym raporcie dziennym i po każdym wysłanym sygnale workflow zapisuje i commituje:

| Plik | Zawartość |
|---|---|
| `raport/stan.json` | te same dane co w index.html (plan, BTC, Sonic, komentarz AI) w postaci JSON |
| `raport/index.html` | identyczna kopia strony z piwecki.pl/analiza |
| `raport/archiwum/RRRR-MM-DD.json` | stan z każdego dnia – do sprawdzania, czy rynek idzie zgodnie z planem |
| `raport/sygnaly.json` | dziennik wszystkich wysłanych powiadomień ntfy z pełną treścią |

Commity mają dopisek `[skip ci]` i nie uruchamiają bota ponownie. Workflow ma uprawnienie `contents: write`.

## Sekrety (Settings → Secrets and variables → Actions)

Wymagane: `FTP_HOST`, `FTP_USER`, `FTP_PASS`, `GROQ_API_KEY`.

ntfy: temat domyślnie `Sonic-buy-sell` (ustawiony w workflow). Można go nadpisać sekretem `NTFY_TOPIC`;
jeśli temat jest chroniony hasłem/tokenem na ntfy.sh – sekret `NTFY_TOKEN`. Sekrety `TELEGRAM_*` nie są już potrzebne.

Opcjonalne zmienne (w `env:` workflow): `FTP_DIR` (domyślnie `domains/piwecki.pl/public_html/analiza`),
`FTP_STATE_DIR` (domyślnie `domains/piwecki.pl/bot_state` – poza public_html), `REPORT_HOUR_UTC` (domyślnie 5),
`FTP_USE_TLS` (domyślnie 1 – szyfrowane FTPS, przy braku obsługi automatycznie zwykłe FTP), `GROQ_MODEL`.

## Ręczne uruchomienie

Actions → „Sonic & BTC – monitor i raport” → Run workflow. Domyślnie wymusza nowy raport HTML.

## Sygnały ntfy (plan etapowy)

Bot startuje co godzinę tuż po pełnej godzinie: odtwarza plan od `start.from` z `data/sonic_plan.json` na zamkniętych
świecach 1h, a potem **czuwa na żywo do :56** – co minutę sprawdza cenę Sonica (środek bid/ask z Coinbase).

| Sygnał | Kiedy |
|---|---|
| Zbliża się do sprzedaży / zakupu (na żywo) | cena w trakcie świecy jest 2% od widełek – obserwuj, wystaw limit |
| W WIDEŁKACH SPRZEDAŻY / W STREFIE ZAKUPU (na żywo) | cena właśnie weszła w widełki – sprzedaj / kup |
| Potwierdzenie 1h | po zamknięciu świecy: zamknięcie pod / w / nad widełkami (odrzucenie albo siła) |
| Krok pośredni (na żywo i 1h) | opór po drodze (0,0365, 0,0600, 0,0740) – decyzja na gorąco; podaje odkup i wybicie |
| Odkup / wybicie po kroku pośrednim | tylko jeśli sprzedałeś na pośrednim |
| Kupno na wybiciu | odkup się nie wykonał, 2 kolejne zamknięcia 1h ≥1% powyżej poprzedniego celu (przed tym ostrzeżenie na żywo) |
| Odkup zawiódł | 2 zamknięcia 1h pod dolną granicą widełek, w których kupiliśmy |
| Plan unieważniony | zamknięcie dnia poniżej 0,0300 |

Każdy sygnał idzie raz (ostrzeżenie i alert na żywo nie dublują się z sygnałami ze świec 1h).
Kroki pośrednie nie zmieniają stanu planu – plan dalej trzyma pozycję do głównego celu.

**Gdy sam kupisz lub sprzedasz inaczej niż plan**, ustaw w `data/sonic_plan.json` blok `start`:
`"from"` – czas (UTC) od którego liczyć, `"phase"` – `BUY` (czekam na zakup) lub `HOLD` (mam Sonica),
`"step"` – krok (`start`, `1`, `2`, …). Np. po zakupie w strefie startowej: `{"from": "2026-11-18T08:00:00Z", "phase": "HOLD", "step": "1"}`.

## Setupy pomocnicze (dawne reguły Fibonacciego, tylko w raporcie)

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

## Test lokalny bez FTP i ntfy

```
DRY_RUN=1 python bot.py
```

Raport zapisze się jako `index.html` w bieżącym folderze, a treść ewentualnych powiadomień ntfy pojawi się w konsoli.
