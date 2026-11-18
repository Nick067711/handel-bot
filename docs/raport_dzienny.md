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

Krok pośredni **0b (góra trójkąta)**: sprzedaż 0,0340–0,0342 (limit 0,0341), odkup 0,0328–0,0330, przy wybiciu odkup po 2 zamknięciach 1h nad 0,0346. Dotyczy pozycji kupionej 17.11.2026 po 0,0330. Rekomenduj sprzedaż, gdy Sonic podchodzi do 0,0340–0,0342 na zwykłym wolumenie (bez kilkukrotnego skoku) i BTC nie wybija nad 65 700; wcześniejsze dotknięcia góry są pewniejsze niż późniejsze (trójkąt się zwęża). Przy skoku wolumenu albo wybiciu BTC – „trzymać do 0,0392”.

**Wzorzec schodów (VIII–X 2026, data/sonic_plan.json → stair_pattern_aug_oct_2026):** wybicie 4–5 dni, potem poziomy przedział 17–26 dni; prawdziwe wybicie = zamknięcie dnia nad górą przedziału przy kilkukrotnym wolumenie. Pierwsze cofnięcie po szczycie wybicia jest największe (−17 do −33%) i kończy się 6–12% nad górą poprzedniego przedziału – to najważniejszy ruch do złapania. W raporcie podawaj: w jakiej fazie jest Sonic (przedział/wybicie), ile dni trwa obecny przedział, a po wybiciu – szacowaną strefę pierwszego odkupu (dla obecnej góry 0,0346: ok. 0,0367–0,0388) i porównaj ją z odkupem kroku 1 w planie.

## 3a. Zasada: odbicie w przebity poziom
Jeśli BTC (albo Sonic) przebił wsparcie i teraz odbija z powrotem do tego poziomu (przebita podstawa = opór), NIE sugeruj zakupu w trakcie takiego odbicia. Napisz wprost: „to odbicie w opór X – nie wchodź przed zamknięciem 1h nad X albo poczekaj na powrót do dołka Y”. Takie odbicia są zwykle sprzedawane (np. 17.11.2026: BTC przebił 64 000, odbił z 63 336 do 64 483, dokładnie w przebitą podstawę 64 250–64 500, i znów spadł). Tę samą zasadę stosuj w każdej odpowiedzi na żywo.

## 4. Makro i tło rynku
Krótkie wyszukiwanie w sieci z bieżącą datą: Fed/stopy, dane z USA, przepływy do ETF (napływy/odpływy z ostatnich dni), likwidacje na rynku kontraktów, podaż stablecoinów, ważne wiadomości o Sonicu/Sonic Labs z ostatniej doby. To tło podaj ZAWSZE, zanim ocenisz sensowność zakupu lub sprzedaży – nie dopiero po fakcie. Przy strefach likwidacji sprawdź datę źródła i czy cena już do nich doszła.

## 5. Odpowiedź
Nagłówek: „Raport DD.MM.RRRR”. MAKSYMALNIE 10 krótkich zdań (12, jeśli w grze jest krok pośredni), bez tabel i długich list. Kolejność: BTC (stan, kierunek, kluczowy poziom) → Sonic względem planu (cena, najbliższy poziom, odległość w %) → czy wykresy i momentum potwierdzają bota → makro → krok pośredni (tylko gdy w pobliżu) → na co uważać dziś. Konkretne poziomy, gdzie ma sens – prawdopodobieństwa. Ostatnie zdanie: „To nie jest rekomendacja. Nie jestem doradcą finansowym.”

## 6. Następny raport
Na końcu zaplanuj narzędziem `send_later` kolejną wiadomość do TEGO SAMEGO czatu na jutro, 07:17 (strefa Europe/Warsaw), o treści:
„Codzienny raport: wykonaj instrukcję z pliku docs/raport_dzienny.md w repozytorium Nick067711/handel-bot.”
Jeśli `send_later` jest niedostępne albo się nie uda, napisz to na końcu raportu, żeby użytkownik mógł wznowić raporty.
