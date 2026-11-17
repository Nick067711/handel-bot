# Codzienny raport w czacie Claude – instrukcja

Ten plik czyta Claude w czacie „Raport” każdego dnia o 7:17 (czas warszawski).
Odpowiedź po polsku, kwoty w USD. Nic nie zmieniaj w repozytorium (bez commitów i pushy) i nie wysyłaj wiadomości poza odpowiedzią w czacie.

## 1. Dane bota
Dodaj repozytorium `Nick067711/handel-bot` (odczyt, narzędzie add_repo), sklonuj (`git clone --depth 1`) albo zrób `git pull`, jeśli już jest. Przeczytaj:
- `raport/stan.json` – kopia danych z dziennego raportu bota (plan etapowy: faza, „co_robic_teraz”, cel sprzedaży i strefa zakupu z limitami wariantu ostrożnego, potwierdzenia trendu, prognoza kapitału; BTC: poziomy, scenariusze, wskaźniki; Sonic: wskaźniki, wsparcia/opory; komentarz AI i notatki makro),
- `raport/sygnaly.json` – powiadomienia ntfy z ostatnich 24–48 h,
- 2–3 najnowsze pliki z `raport/archiwum/`,
- `data/sonic_plan.json` – poziomy planu, w tym KROKI POŚREDNIE (`"intermediate": true`: 1a ok. 0,0365, 4a ok. 0,0600, 5a ok. 0,0740 – widełki sprzedaży, odkup, wybicie). Bot NIE wysyła sygnałów dla kroków pośrednich – decyzja należy do Ciebie.

Jeśli `raport/stan.json` jest starszy niż ok. 30 godzin, napisz to wprost i sprawdź przez `gh api` ostatnie uruchomienia workflow „Sonic & BTC – monitor i raport”.

## 2. Własna weryfikacja wykresów
Świeże świece z `https://data-api.binance.vision/api/v3/klines` (BTCUSDT, SUSDT; 1h, 4h, 1d). Policz: RSI14, MACD 12/26/9 (histogram i kierunek), EMA20/EMA50, SMA200 (BTC), SMA100 (Sonic), wolumen względem średniej. Oceń, czy to, co pokazuje bot, ma odzwierciedlenie na wykresach i czy momentum 4h/1D wspiera plan.
Kontekst: użytkownik handluje Sonikiem tylko na Coinbase (S-USD, płytki rynek – ceny mogą nie schodzić tak nisko jak na Binance), plan na 1D/4h (1h tylko do doprecyzowania), bez handlu w ciągu dnia. Wariant ostrożny: sprzedaż w połowie między dolną granicą widełek a głównym poziomem, odkup na górnej granicy widełek.

## 3. Kroki pośrednie – Twoja decyzja
Jeśli Sonic jest do ok. 5% od widełek najbliższego kroku pośredniego (albo w nich), oceń, czy go wykonać. Sprzedaż na pośrednim ma sens tylko przy realnym ryzyku odwrócenia (słabnące momentum Sonica na 4h/1h przy oporze, słabość BTC, zagrożenie kluczowego wsparcia, negatywne makro). Rachunek: ok. +5% Soniców przy trafionym odkupie, ok. −4% przy kupnie na wybiciu. Podaj jednoznacznie: „sprzedać na pośrednim (limit X, odkup Y)” albo „nie sprzedawać, trzymać do głównego celu”. Jeśli żaden pośredni nie jest w pobliżu – nie wspominaj o nich.
Niezależnie od tego: przy wysokim ryzyku silnego spadku BTC, który pociągnąłby Sonica pod plan, powiedz to wprost i wskaż poziom ochronnej sprzedaży.

## 4. Makro
Krótkie wyszukiwanie w sieci z bieżącą datą: Fed/stopy, dane z USA, przepływy do ETF, ważne wiadomości o Sonicu/Sonic Labs z ostatniej doby.

## 5. Odpowiedź
Nagłówek: „Raport DD.MM.RRRR”. MAKSYMALNIE 10 krótkich zdań (12, jeśli w grze jest krok pośredni), bez tabel i długich list. Kolejność: BTC (stan, kierunek, kluczowy poziom) → Sonic względem planu (cena, najbliższy poziom, odległość w %) → czy wykresy i momentum potwierdzają bota → makro → krok pośredni (tylko gdy w pobliżu) → na co uważać dziś. Konkretne poziomy, gdzie ma sens – prawdopodobieństwa. Ostatnie zdanie: „To nie jest rekomendacja. Nie jestem doradcą finansowym.”

## 6. Następny raport
Na końcu zaplanuj narzędziem `send_later` kolejną wiadomość do TEGO SAMEGO czatu na jutro, 07:17 (strefa Europe/Warsaw), o treści:
„Codzienny raport: wykonaj instrukcję z pliku docs/raport_dzienny.md w repozytorium Nick067711/handel-bot.”
Jeśli `send_later` jest niedostępne albo się nie uda, napisz to na końcu raportu, żeby użytkownik mógł wznowić raporty.
