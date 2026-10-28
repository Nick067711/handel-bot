# -*- coding: utf-8 -*-
"""Zapytania do Groq: wyszukiwanie w sieci (nastroje, makro) oraz komentarz analityczny do raportu."""
import os, re, json

from openai import OpenAI

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def _client():
    return OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY, timeout=180, max_retries=2)


def _strip_citations(text):
    # znaczniki typu 【3†L10-L20】 z wyszukiwarki Groq
    return re.sub(r"【[^】]*】", "", text or "").strip()


def web_research(topic_prompt, max_tokens=3000):
    """Wyszukiwanie w sieci przez wbudowane narzędzie browser_search (gpt-oss). Zwraca tekst albo None."""
    if not GROQ_API_KEY:
        return None
    try:
        r = _client().chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": topic_prompt}],
            tools=[{"type": "browser_search"}],
            tool_choice="required",
            reasoning_effort="low",
            temperature=1,
            max_completion_tokens=max_tokens,
        )
        txt = _strip_citations(r.choices[0].message.content)
        return txt or None
    except Exception as e:
        print(f"[AI] wyszukiwanie nieudane: {type(e).__name__}: {str(e)[:200]}")
        return None


def research_sonic_sentiment(today):
    return web_research(
        f"Dziś jest {today}. Wyszukaj w internecie najnowsze (ostatnie 7–14 dni) informacje o kryptowalucie Sonic (ticker S, "
        "dawniej Fantom FTM, projekt Sonic Labs): wiadomości, aktualizacje projektu, partnerstwa, problemy, odblokowania tokenów, "
        "oraz nastroje inwestorów w mediach społecznościowych (X/Twitter, Reddit, portale krypto). "
        "Odpowiedz po polsku, maksymalnie 200 słów, w punktach: (1) najważniejsze wydarzenia z datami, "
        "(2) ogólny nastrój: pozytywny / neutralny / negatywny i dlaczego, (3) ryzyka. "
        "Na końcu wypisz 'Źródła:' i listę adresów URL, z których korzystałeś. Nie podawaj cen – mam je z giełdy.")


def research_macro(today):
    return web_research(
        f"Dziś jest {today}. Wyszukaj najnowsze informacje makroekonomiczne ważne dla Bitcoina: ostatnia i najbliższa decyzja FED/FOMC "
        "(data, stopy), oczekiwania rynku co do stóp, ostatnie dane o inflacji (CPI/PCE), przepływy do ETF-ów na bitcoina w ostatnim "
        "tygodniu, inne ważne wydarzenia (regulacje, geopolityka, płynność/dolar). "
        "Odpowiedz po polsku, maksymalnie 200 słów, w punktach z datami i liczbami. Na końcu 'Źródła:' i lista adresów URL. "
        "Nie podawaj ceny BTC – mam ją z giełd.")


SECTIONS = ("BTC", "SONIC", "SYGNAL", "RYZYKA")


def commentary(payload, macro_text, sentiment_text):
    """Komentarz do raportu. Zwraca dict sekcji (tekst z prostym markdownem) albo None."""
    if not GROQ_API_KEY:
        print("[AI] brak GROQ_API_KEY – raport bez komentarza AI")
        return None
    prompt = f"""Jesteś doświadczonym analitykiem rynku kryptowalut. Napisz komentarz do dziennego raportu dla inwestora indywidualnego.
Pisz po polsku, rzeczowo i zrozumiale dla laika; wyjaśniaj skróty przy pierwszym użyciu.

ZASADY:
- Liczby (ceny, poziomy, wskaźniki, statystyki) bierz WYŁĄCZNIE z sekcji DANE i RESEARCH. Niczego nie zmyślaj.
- Gdy czegoś nie ma w danych, napisz, że brak danych.
- Fazę rynku oceniaj na podstawie danych (struktura HH/HL/LH/LL, średnie, wskaźniki), bez z góry przyjętej tezy.
- Statystyki historyczne setupów i punktów zwrotnych traktuj jako prawdopodobieństwa, nie pewniki.
- Nie udzielaj porad inwestycyjnych w trybie rozkazującym; opisuj scenariusze i poziomy.

FORMAT (dokładnie te nagłówki, każdy w osobnej linii, bez innych nagłówków):
[BTC]
3–5 akapitów: kondycja BTC na tle makro (wykorzystaj RESEARCH MAKRO), co mówią wskaźniki, czy tło sprzyja altcoinom.
[SONIC]
3–5 akapitów: położenie ceny w kontekście historii i struktury fal, najbliższe wsparcia/opory z odległościami,
kierunek ruchu, księga zleceń, nastroje (z RESEARCH SONIC), porównanie z typowymi zniesieniami Fibonacciego tego aktywa.
[SYGNAL]
1–2 akapity: stan sygnału LONG/SHORT, których warunków brakuje, przy jakich poziomach sygnał może się pojawić.
[RYZYKA]
3–5 punktów zaczynających się od "- ".
Możesz używać **pogrubień**. Bez tabel, bez kodu, bez HTML.

DANE:
{json.dumps(payload, ensure_ascii=False, default=str)}

RESEARCH MAKRO (wyszukiwanie w sieci):
{macro_text or "brak – wyszukiwanie nie powiodło się"}

RESEARCH SONIC – NASTROJE (wyszukiwanie w sieci):
{sentiment_text or "brak – wyszukiwanie nie powiodło się"}
"""
    try:
        r = _client().chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": "Jesteś rzetelnym analitykiem. Nigdy nie wymyślasz liczb."},
                      {"role": "user", "content": prompt}],
            temperature=0.3,
            reasoning_effort="medium",
            max_completion_tokens=6000,
        )
        ch = r.choices[0]
        text = (ch.message.content or "").strip()
        if ch.finish_reason == "length":
            print("[AI] komentarz ucięty przez limit tokenów – używam tego, co przyszło")
        parts = {}
        cur = None
        for line in text.splitlines():
            m = re.match(r"^\s*\[?\s*(BTC|SONIC|SYGNA[ŁL]|RYZYKA)\s*\]?\s*:?\s*$", line.strip(), re.I)
            if m:
                cur = m.group(1).upper().replace("Ł", "L")
                parts[cur] = []
                continue
            if cur:
                parts[cur].append(line)
        parts = {k: "\n".join(v).strip() for k, v in parts.items() if "\n".join(v).strip()}
        if not parts:
            print("[AI] nie rozpoznano sekcji komentarza")
            return None
        return parts
    except Exception as e:
        print(f"[AI] komentarz nieudany: {type(e).__name__}: {str(e)[:200]}")
        return None
