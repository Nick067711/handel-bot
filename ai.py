# -*- coding: utf-8 -*-
"""
Zapytania do Groq: wyszukiwanie w sieci (nastroje, makro) oraz komentarz analityczny do raportu.

Darmowy plan Groq ma limit TPM (tokeny na minutę, dla gpt-oss-120b: 8000). Liczy się do niego
wejście + zarezerwowany limit odpowiedzi. Dlatego:
  - każde zapytanie jest przycinane tak, by zmieścić się w budżecie (MAX_TPM),
  - między zapytaniami jest odstęp ~1 minuty,
  - przy błędzie 429/413 następuje odczekanie i jedna ponowna próba (przy 413 z krótszymi danymi),
  - komentarz jest pisany w dwóch mniejszych zapytaniach: BTC i Sonic.
"""
import os, re, json, time

from openai import OpenAI

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_TPM = int(os.getenv("GROQ_MAX_TPM", "8000"))       # limit planu; przy płatnym planie można podnieść
BUDGET = int(MAX_TPM * 0.92)                            # zapas na narzut formatu wiadomości
PAUSE_S = int(os.getenv("GROQ_PAUSE_S", "62"))          # odstęp między zapytaniami (okno TPM = 60 s)

_last_call = [0.0]


def _client():
    return OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY, timeout=180, max_retries=0)


def est_tokens(text):
    """Zgrubny szacunek: polski tekst i JSON to ok. 3 znaki na token."""
    return int(len(text) / 3.0) + 20


def _pace():
    wait = PAUSE_S - (time.time() - _last_call[0])
    if _last_call[0] and wait > 0:
        print(f"[AI] czekam {wait:.0f} s (limit tokenów na minutę)")
        time.sleep(wait)


def _call(messages, max_tokens, tools=None, reasoning="low", temperature=0.3, label="AI"):
    """Zwraca (tekst, kod_błędu). kod_błędu: None, 413 (za duże zapytanie) albo 'err'."""
    for attempt in (1, 2):
        _pace()
        kw = dict(model=GROQ_MODEL, messages=messages, max_completion_tokens=max_tokens,
                  reasoning_effort=reasoning, temperature=temperature)
        if tools:
            kw.update(tools=tools, tool_choice="required")
        try:
            r = _client().chat.completions.create(**kw)
            _last_call[0] = time.time()
            ch = r.choices[0]
            if ch.finish_reason == "length":
                print(f"[{label}] odpowiedź ucięta przez limit tokenów – używam tego, co przyszło")
            return (ch.message.content or "").strip(), None
        except Exception as e:
            _last_call[0] = time.time()
            status = getattr(e, "status_code", None)
            msg = str(e)
            if status == 413:
                print(f"[{label}] zapytanie za duże dla limitu planu (413)")
                return None, 413
            if status == 429 and attempt == 1:
                m = re.search(r"try again in ([\d.]+)s", msg)
                wait = min(90, float(m.group(1)) + 2) if m else 65
                print(f"[{label}] limit zapytań (429) – czekam {wait:.0f} s i ponawiam")
                time.sleep(wait)
                continue
            print(f"[{label}] błąd: {type(e).__name__}: {msg[:200]}")
            return None, "err"
    return None, "err"


def _strip_citations(text):
    return re.sub(r"【[^】]*】", "", text or "").strip()


def shorten(text, max_chars):
    """Przycina tekst na granicy linii, zostawiając listę źródeł, jeśli się mieści."""
    if not text or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    return cut[:cut.rfind("\n")] if "\n" in cut else cut


# ----------------------------------------------------------------- wyszukiwanie w sieci
def web_research(topic_prompt, label):
    if not GROQ_API_KEY:
        return None
    txt, err = _call([{"role": "user", "content": topic_prompt}], max_tokens=1800,
                     tools=[{"type": "browser_search"}], temperature=1, label=label)
    return _strip_citations(txt) or None


def research_sonic_sentiment(today):
    return web_research(
        f"Dziś jest {today}. Wyszukaj najnowsze (ostatnie 7–14 dni) informacje o kryptowalucie Sonic (ticker S, dawniej Fantom FTM, "
        "Sonic Labs): wiadomości, aktualizacje, partnerstwa, problemy, odblokowania tokenów oraz nastroje inwestorów (X, Reddit, portale). "
        "Odpowiedz po polsku, maks. 150 słów, w punktach: (1) wydarzenia z datami, (2) nastrój: pozytywny/neutralny/negatywny i dlaczego, "
        "(3) ryzyka. Na końcu 'Źródła:' i maks. 4 adresy URL. Nie podawaj cen.", "AI nastroje Sonic")


def research_macro(today):
    return web_research(
        f"Dziś jest {today}. Wyszukaj najnowsze informacje makro ważne dla Bitcoina: ostatnia i najbliższa decyzja FED/FOMC (data, stopy, "
        "głosowanie), oczekiwania co do stóp, ostatnie CPI/PCE, przepływy do ETF-ów BTC w ostatnim tygodniu, ropa, dolar, rentowności, "
        "geopolityka. Odpowiedz po polsku, maks. 150 słów, w punktach z datami i liczbami. Na końcu 'Źródła:' i maks. 4 adresy URL. "
        "Nie podawaj ceny BTC.", "AI makro")


# ----------------------------------------------------------------- komentarz
RULES = """Pisz po polsku, rzeczowo i zrozumiale dla laika; wyjaśniaj skróty przy pierwszym użyciu.
Liczby bierz WYŁĄCZNIE z DANYCH i RESEARCHU – niczego nie zmyślaj; gdy czegoś brak, napisz, że brak danych.
Fazę rynku oceniaj na podstawie danych, bez z góry przyjętej tezy. Statystyki historyczne to prawdopodobieństwa, nie pewniki.
Nie wydawaj poleceń inwestycyjnych – opisuj scenariusze i poziomy. Możesz używać **pogrubień**. Bez tabel, kodu i HTML."""


def _parse_sections(text, allowed):
    parts, cur = {}, None
    for line in (text or "").splitlines():
        m = re.match(r"^\s*\[?\s*(BTC|SONIC|SYGNA[ŁL]|RYZYKA)\s*\]?\s*:?\s*$", line.strip(), re.I)
        if m:
            cur = m.group(1).upper().replace("Ł", "L"); parts[cur] = []; continue
        if cur:
            parts[cur].append(line)
    out = {k: "\n".join(v).strip() for k, v in parts.items() if k in allowed and "\n".join(v).strip()}
    if not out and text and len(allowed) == 1:      # model pominął nagłówek – bierzemy cały tekst
        out = {allowed[0]: text.strip()}
    return out


def _commentary_call(task, fmt, data, research, research_label, allowed, label, out_tokens):
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    for research_chars in (1600, 700, 0):
        r_txt = shorten(research, research_chars) if research_chars else None
        prompt = (f"{task}\n\nZASADY:\n{RULES}\n\nFORMAT:\n{fmt}\n\nDANE:\n{data_json}\n\n"
                  f"{research_label}:\n{r_txt or 'brak'}")
        est_in = est_tokens(prompt) + 40
        max_out = min(out_tokens, BUDGET - est_in)
        if max_out < 1200:
            continue                                  # za mało miejsca na odpowiedź – skróć research
        print(f"[{label}] szacunek: wejście ~{est_in} tok., odpowiedź do {max_out} tok.")
        text, err = _call([{"role": "system", "content": "Jesteś rzetelnym analitykiem rynku kryptowalut. Nigdy nie wymyślasz liczb."},
                           {"role": "user", "content": prompt}], max_tokens=max_out, label=label)
        if err == 413:
            continue
        if text:
            parts = _parse_sections(text, allowed)
            if parts:
                return parts
            print(f"[{label}] nie rozpoznano sekcji odpowiedzi")
        return {}
    print(f"[{label}] dane nie mieszczą się w limicie planu Groq")
    return {}


def commentary(btc_data, sonic_data, macro_text, sentiment_text):
    """Dwa zapytania (BTC i Sonic). Zwraca dict sekcji BTC/SONIC/SYGNAL/RYZYKA albo None."""
    if not GROQ_API_KEY:
        print("[AI] brak GROQ_API_KEY – raport bez komentarza AI")
        return None
    out = {}
    out.update(_commentary_call(
        "Napisz komentarz o Bitcoinie do dziennego raportu dla inwestora indywidualnego.",
        "[BTC]\n3–4 akapity: kondycja BTC na tle makro (RESEARCH MAKRO), co mówią wskaźniki z DANYCH, czy tło sprzyja altcoinom.",
        btc_data, macro_text, "RESEARCH MAKRO (wyszukiwanie w sieci)", ("BTC",), "AI komentarz BTC", 2600))
    out.update(_commentary_call(
        "Napisz komentarz o Sonic (S) do dziennego raportu dla inwestora indywidualnego.",
        "[SONIC]\n3–4 akapity: położenie ceny w historii i strukturze fal, najbliższe wsparcia/opory z odległościami, kierunek ruchu, "
        "księga zleceń, nastroje (RESEARCH SONIC), porównanie z typowymi zniesieniami Fibonacciego tego aktywa.\n"
        "[SYGNAL]\n1–2 akapity: stan sygnałów LONG/SHORT, których warunków brakuje, przy jakich poziomach mogą się pojawić.\n"
        "[RYZYKA]\n3–5 punktów zaczynających się od \"- \".",
        sonic_data, sentiment_text, "RESEARCH SONIC – NASTROJE (wyszukiwanie w sieci)", ("SONIC", "SYGNAL", "RYZYKA"),
        "AI komentarz Sonic", 3200))
    return out or None
