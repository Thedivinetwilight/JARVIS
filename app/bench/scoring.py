"""Text scoring for the voice benchmark: word error rate, entity accuracy, task success.

Definitions are fixed in advance and repeated in the result document, because a WER number without
its normalisation rules is not comparable with anything:

* normalisation: Unicode NFKC, case-folded, punctuation removed, digits kept, whitespace collapsed;
  no stemming, no number-to-word rewriting, no language-specific rules (so the same rule applies to
  every language in the matrix, including the ones with no whitespace tokenisation);
* **WER** = (substitutions + deletions + insertions) / reference words, computed on the normalised
  token sequences; an empty reference is refused rather than scored;
* **entity accuracy** = fraction of the fixture's *declared* critical entities that appear in the
  transcript (whole-phrase match on the normalised text). It is a strict, unforgiving check: this is
  the class of error that would write the wrong name, date or amount into a system of record;
* **task success** = every declared entity present **and** non-empty output. It is a proxy for "the
  pipeline delivered the content a task needs", not a claim that an assistant understood anything.

Nothing here rewards verbosity and nothing here is weighted: a hallucinated extra word counts as an
insertion, which is exactly the direction a personal assistant should be punished in.
"""

from __future__ import annotations

import re
import unicodedata

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    folded = unicodedata.normalize("NFKC", text).casefold()
    stripped = _PUNCTUATION.sub(" ", folded)
    return _WHITESPACE.sub(" ", stripped).strip()


def tokens(text: str) -> list[str]:
    normalised = normalise_text(text)
    return normalised.split(" ") if normalised else []


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    """Levenshtein distance over token sequences (unit costs)."""
    if not reference:
        return len(hypothesis)
    previous = list(range(len(hypothesis) + 1))
    for row, ref_token in enumerate(reference, start=1):
        current = [row]
        for column, hyp_token in enumerate(hypothesis, start=1):
            cost = 0 if ref_token == hyp_token else 1
            current.append(min(
                previous[column] + 1,          # deletion
                current[column - 1] + 1,       # insertion
                previous[column - 1] + cost,   # substitution / match
            ))
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> dict:
    """WER with its components, so a reviewer can see *how* it failed."""
    reference_tokens = tokens(reference)
    if not reference_tokens:
        raise ValueError("a reference transcript is required to compute WER")
    hypothesis_tokens = tokens(hypothesis or "")
    distance = edit_distance(reference_tokens, hypothesis_tokens)
    return {
        "wer": round(distance / len(reference_tokens), 6),
        "reference_tokens": len(reference_tokens),
        "hypothesis_tokens": len(hypothesis_tokens),
        "edits": distance,
        "exact_match": distance == 0,
    }


def entity_hits(entities, transcript: str) -> dict:
    """Which declared entities survive recognition, and which were dropped or mangled."""
    normalised = normalise_text(transcript or "")
    found, missing = [], []
    for entity in entities or []:
        text = normalise_text(str(entity.get("text", "")))
        if not text:
            continue
        (found if text in normalised else missing).append(
            {"kind": entity.get("kind"), "text": entity.get("text")}
        )
    total = len(found) + len(missing)
    return {
        "total": total,
        "found": len(found),
        "accuracy": round(len(found) / total, 6) if total else None,
        "missing": missing,
        "not_measured": None if total else "no_declared_entities",
    }


def task_success(entities, transcript: str) -> dict:
    """All declared entities present and a non-empty transcript. A proxy, stated as one."""
    if not (transcript or "").strip():
        return {"success": False, "reason": "empty_output", "n": 1}
    hits = entity_hits(entities, transcript)
    if hits["total"] == 0:
        return {"success": None, "reason": "no_declared_entities",
                "not_measured": "the fixture declares no critical entities"}
    return {
        "success": hits["found"] == hits["total"],
        "reason": None if hits["found"] == hits["total"] else "missing_entities",
        "missing": hits["missing"],
    }
