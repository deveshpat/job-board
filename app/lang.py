"""Which language a posting is written in — a small stopword count, no model or dependency needed.

A posting written in German almost always wants German speakers, so this backs a hard gate against the
languages on your profile (see pipeline.score_answers). English + the common European job-board languages.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

STOPWORDS = {
    "English": "the and for with you your are our will this that have from team work we be in of to a an on as or is at by it",
    "German": "und der die das mit für wir sie ihr ihre ist ein eine einen dem den des zu auf im bei als oder nicht sind werden du dein deine uns unser unsere wie auch",
    "French": "et le la les des une un pour avec vous nous est dans sur par au aux du ce cette sont être votre notre qui que en",
    "Spanish": "y el la los las de del con para por una un es en que nuestro nuestra somos tu tus su sus como más equipo",
    "Dutch": "en de het een van voor met je jij wij we zijn op in te bij als ons onze jouw niet ook naar",
    "Portuguese": "e o a os as de do da dos das com para por uma um é em que nosso nossa você seu sua como mais equipe",
    "Italian": "e il lo la gli le di del della con per una un è in che nostro nostra sei come più anche siamo",
    "Polish": "i w z na do się jest że dla od jak oraz nie to być twoje nasz nasze jesteś będziesz",
    "Swedish": "och att det som är för med på av en ett vi du din dina vår våra har inte kan",
}
_SETS = {lang: set(words.split()) for lang, words in STOPWORDS.items()}
_TOKEN = re.compile(r"[a-zàâäçéèêëîïôöûùüÿñæœßąćęłńóśźżåø]+")


def detect(text: str, min_tokens: int = 40) -> Optional[str]:
    """The posting's language ('English', 'German', …), or None when there's too little text to tell."""
    text = re.sub(r"https?://\S+|www\.\S+|\S+@\S+", " ", (text or "").lower())       # URLs look like any language
    tokens = [t for t in _TOKEN.findall(text) if len(t) > 1]                           # "a", "e", "o", "y" are everyone's
    if len(tokens) < min_tokens:
        return None
    hits = Counter()
    for t in tokens:
        for lang, words in _SETS.items():
            if t in words:
                hits[lang] += 1
    if not hits:
        return None
    lang, n = hits.most_common(1)[0]
    runner = hits.most_common(2)[1][1] if len(hits) > 1 else 0
    # clear winner, and a real share of the text (not an English posting quoting a German address)
    return lang if n >= 8 and n >= 1.5 * runner and n / len(tokens) >= 0.06 else None
