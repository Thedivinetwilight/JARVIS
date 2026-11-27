"""Deterministic lexical search helpers shared by memory and episodic retrieval.

Strategy (see docs/experiments/EXP-001-retrieval.md for the measured comparison):
1. Tokenise the query with the same rules SQLite's ``unicode61`` tokenizer uses
   (letters/digits; underscore and punctuation separate tokens), lowercase it,
   and drop stop words.
2. Build an FTS5 ``OR`` query of quoted tokens (quoting prevents FTS syntax injection).
   The index uses the ``porter`` stemmer, so "voltages" matches "voltage".
3. If FTS5 is unavailable, fall back to per-token ``LIKE`` matching scored in Python.

Known limitation: purely lexical. Paraphrases that share no word stems with the
stored text are not found. That limitation is measured, not hidden.
"""
from __future__ import annotations

import re

STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers herself him himself his how i if in into is it its itself just know let me
more most my myself no nor not now of off on once only or other our ours ourselves out over own
please recall remember same say said she should so some such tell than that the their theirs them
themselves then there these they this those through to too under until up very was we were what
when where which while who whom why will with would you your yours yourself yourselves
""".split())

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
MAX_QUERY_TOKENS = 12


def tokenize(text: str) -> list[str]:
    """Lowercased content tokens in first-seen order, without stop words or duplicates."""
    seen: dict[str, None] = {}
    for token in _TOKEN.findall(text.lower()):
        if token in STOPWORDS or (len(token) < 2 and not token.isdigit()):
            continue
        seen.setdefault(token, None)
    return list(seen)[:MAX_QUERY_TOKENS]


def fts_query(tokens: list[str]) -> str:
    return " OR ".join(f'"{t}"' for t in tokens)


def like_score(tokens: list[str], *fields: tuple[str, float]) -> float:
    """Fallback relevance: weighted fraction of query tokens present in the given fields."""
    if not tokens:
        return 0.0
    total = 0.0
    for token in tokens:
        best = 0.0
        for text, weight in fields:
            if text and token in text.lower():
                best = max(best, weight)
        total += best
    max_weight = max((w for _, w in fields), default=1.0)
    return total / (len(tokens) * max_weight)
