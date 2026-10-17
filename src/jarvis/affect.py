"""Emotional-context appraisal and adaptive communication (appraisal "affect-rules-v1").

What this is: a bounded, deterministic appraisal that separates
- **stated** feelings ("I'm stressed") — explicit self-reports, confidence HIGH;
- **possible** feelings inferred from weak cues ("ugh", "still broken") —
  confidence LOW/MEDIUM, never asserted as facts about the person;
- **stated absence** ("I'm not frustrated") — overrides earlier inferences;
- situational **urgency** (deadline words) — a property of the situation, not an emotion;
- **communication preferences** (stored, user-approved) and the resulting style.

It also derives functional signals from observable history:
- caution: elevated when the request is irreversible or high-risk;
- frustration analogue: repeated recent failures of the same work -> suggest a
  different approach / escalate instead of repeating (Addendum F2);
These signals may change *how* JARVIS communicates and what it suggests. They have
no input into the permission policy (security.py takes no appraisal argument).

What this is not: a model of human emotion, evidence of feelings in JARVIS, or a
reliable detector of anyone's internal state. Appraisals are stored as sensitive
data with a retention limit (default 30 days) and can be deleted.

Theory note: the dimensions are a small subset inspired by appraisal theories
(relevance, urgency, coping potential; e.g. Scherer's component process model and the
OCC model). No claim is made that this implements either theory.
"""
from __future__ import annotations

import re

from .contracts import AffectSignal, Appraisal, CommunicationStyle, Confidence

_INTENSIFIERS = r"(?:so |really |very |quite |a bit |a little |kind of |kinda |pretty |extremely |super |totally )*"
_FEELINGS = {
    "stressed": "stress", "stressed out": "stress", "anxious": "anxiety", "worried": "worry", "nervous": "worry",
    "overwhelmed": "overwhelm", "frustrated": "frustration", "annoyed": "frustration", "angry": "anger",
    "upset": "distress", "sad": "sadness", "down": "sadness", "tired": "fatigue", "exhausted": "fatigue",
    "burned out": "burnout", "burnt out": "burnout", "confused": "confusion", "lost": "confusion",
    "stuck": "stuck", "excited": "excitement", "happy": "positive", "great": "positive", "good": "positive",
    "relieved": "relief", "proud": "pride", "curious": "curiosity", "bored": "boredom", "lonely": "loneliness",
}
NEGATIVE = {"stress", "anxiety", "worry", "overwhelm", "frustration", "anger", "distress", "sadness", "fatigue",
            "burnout", "confusion", "stuck", "boredom", "loneliness"}
_FEELING_ALT = "|".join(sorted((re.escape(k) for k in _FEELINGS), key=len, reverse=True))
_STATED = re.compile(
    rf"\b(?:i\s*am|i'?m|im|i\s+feel|i'?m\s+feeling|i\s+am\s+feeling|feeling)\s+(?P<neg>not\s+|n't\s+)?{_INTENSIFIERS}"
    rf"(?P<feel>{_FEELING_ALT})\b", re.IGNORECASE)
_STATED_NEG = re.compile(rf"\b(?:i'?m|i\s+am)\s+not\s+{_INTENSIFIERS}(?P<feel>{_FEELING_ALT})\b", re.IGNORECASE)
# "I'm excited but honestly a bit worried": a feeling continued after a conjunction is also stated.
# Words that are often verbs/adverbs ("lost my keys", "down the road") are excluded here.
_AMBIGUOUS = {"lost", "down", "good", "great", "stuck"}
_CONT_ALT = "|".join(sorted((re.escape(k) for k in _FEELINGS if k not in _AMBIGUOUS), key=len, reverse=True))
_CONTINUATION = re.compile(
    rf"\b(?:but|and|yet|though|also)\s+(?:honestly\s+|also\s+|still\s+|now\s+)?(?:i'?m\s+|feeling\s+)?"
    rf"{_INTENSIFIERS}(?P<feel>{_CONT_ALT})\b", re.IGNORECASE)

# Weak cues: each maps to a *possible* reading. Several cues raise confidence to MEDIUM at most.
_CUES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\b(?:ugh+|argh+|grr+|ffs|damn it|dammit)\b", re.I), "frustration", "exclamation of annoyance"),
    (re.compile(r"\b(?:still|again)\b.{0,40}\b(?:broken|failing|fails|not working|doesn'?t work|crash(?:es|ing)?)\b|"
                r"\b(?:broken|failing|not working|doesn'?t work)\b.{0,20}\b(?:again|still)\b", re.I),
     "frustration", "repeated failure described"),
    (re.compile(r"!{2,}"), "frustration_or_excitement", "multiple exclamation marks"),
    (re.compile(r"\b(?:can'?t|cannot)\s+(?:figure|get|make)\b.{0,30}\b(?:work|out|right)\b", re.I),
     "stuck", "difficulty described"),
    (re.compile(r"\b(?:no idea|i give up|this is hopeless|nothing works)\b", re.I), "discouragement", "discouraged phrasing"),
    (re.compile(r"\b(?:awesome|amazing|yay|it finally works|finally works|it works now|works now)\b", re.I),
     "positive", "positive phrasing"),
)
_URGENT = re.compile(r"\b(?:asap|urgent(?:ly)?|immediately|right now|emergency|deadline|due (?:today|tonight|tomorrow)|"
                     r"in (?:an|one|two|\d+) hours?|by tonight|by tomorrow)\b", re.I)
_CALM = re.compile(r"\b(?:no rush|whenever|no hurry|take your time|low priority)\b", re.I)


def appraise(text: str, *, preferences: dict[str, str] | None = None, repeated_failures: int = 0,
             risky_request: bool = False) -> Appraisal:
    """Appraise one input. Pure function: no I/O, deterministic for the same inputs."""
    preferences = preferences or {}
    stated: list[AffectSignal] = []
    absent: set[str] = set()
    for match in _STATED_NEG.finditer(text):
        label = _FEELINGS[match.group("feel").lower()]
        absent.add(label)
        stated.append(AffectSignal(label, "stated_absent", Confidence.HIGH, match.group(0)[:80]))
    for match in _STATED.finditer(text):
        label = _FEELINGS[match.group("feel").lower()]
        if match.group("neg") or label in absent:
            continue
        if not any(s.label == label and s.basis == "stated" for s in stated):
            stated.append(AffectSignal(label, "stated", Confidence.HIGH, match.group(0)[:80]))
    if any(s.basis == "stated" for s in stated):
        for match in _CONTINUATION.finditer(text):
            label = _FEELINGS[match.group("feel").lower()]
            if label not in absent and not any(s.label == label and s.basis == "stated" for s in stated):
                stated.append(AffectSignal(label, "stated", Confidence.HIGH, match.group(0)[:80]))

    cue_hits: dict[str, list[str]] = {}
    for pattern, label, description in _CUES:
        if pattern.search(text):
            cue_hits.setdefault(label, []).append(description)
    if sum(1 for w in re.findall(r"\b[A-Z]{3,}\b", text) if w not in {"API", "USB", "CPU", "GPU", "LED", "PCB"}) >= 2:
        cue_hits.setdefault("frustration_or_excitement", []).append("several all-caps words")
    stated_labels = {s.label for s in stated if s.basis == "stated"}
    possible: list[AffectSignal] = []
    for label, cues in cue_hits.items():
        if label in stated_labels or label in absent:
            continue  # a direct statement outranks an inference
        confidence = Confidence.MEDIUM if len(cues) >= 2 else Confidence.LOW
        possible.append(AffectSignal(label, "inferred", confidence, "; ".join(cues)))

    urgency = "high" if _URGENT.search(text) else ("low" if _CALM.search(text) else "normal")

    uncertainty_parts = []
    if possible:
        uncertainty_parts.append("Inferred readings are uncertain guesses from wording, not facts about the person.")
    if stated and possible:
        uncertainty_parts.append("Stated feelings take precedence over inferred cues.")
    positives = {s.label for s in stated if s.basis == "stated"} - NEGATIVE
    negatives = {s.label for s in stated if s.basis == "stated"} & NEGATIVE
    if positives and negatives:
        uncertainty_parts.append("Mixed feelings were stated; both are kept.")

    style = choose_style(preferences=preferences, stated_negative=bool(negatives), urgency=urgency,
                         repeated_failures=repeated_failures)
    sensitive = bool([s for s in stated if s.basis == "stated"])
    return Appraisal(stated=tuple(stated), possible=tuple(possible), urgency=urgency, style=style,
                     uncertainty=" ".join(uncertainty_parts), caution="elevated" if risky_request else "normal",
                     repeated_failures=repeated_failures, sensitive=sensitive)


def choose_style(*, preferences: dict[str, str], stated_negative: bool, urgency: str,
                 repeated_failures: int) -> CommunicationStyle:
    reasons: list[str] = []
    verbosity = "standard"
    pref = (preferences.get("communication verbosity") or "").lower()
    if pref in {"brief", "concise", "short"}:
        verbosity = "brief"
        reasons.append("user prefers concise answers")
    elif pref in {"detailed", "thorough"}:
        verbosity = "detailed"
        reasons.append("user prefers detailed answers")
    if urgency == "high" and verbosity != "brief":
        verbosity = "brief"
        reasons.append("situation is urgent")
    tone, lead = "neutral", "answer"
    if stated_negative:
        tone, lead = "supportive", "acknowledgement"
        reasons.append("user stated a difficult feeling")
    if repeated_failures >= 2:
        reasons.append(f"{repeated_failures} recent failures: suggest a different approach")
    return CommunicationStyle(verbosity=verbosity, tone=tone, lead_with=lead, reasons=tuple(reasons))


_LABEL_PHRASES = {
    "stress": "stressed", "anxiety": "anxious", "worry": "worried", "overwhelm": "overwhelmed",
    "frustration": "frustrated", "anger": "angry", "distress": "upset", "sadness": "down", "fatigue": "tired",
    "burnout": "burned out", "confusion": "unsure", "stuck": "stuck", "boredom": "bored", "loneliness": "lonely",
}


def acknowledgement(appraisal: Appraisal) -> str:
    """A short, non-presumptuous acknowledgement for *stated* feelings only.

    Never claims JARVIS feels anything, never encourages dependence, and moves to
    a concrete next step.
    """
    negatives = [s.label for s in appraisal.stated if s.basis == "stated" and s.label in NEGATIVE]
    if not negatives:
        return ""
    phrase = _LABEL_PHRASES.get(negatives[0], negatives[0])
    return f"Thanks for telling me you're feeling {phrase}. Let's make this manageable — one concrete step at a time."


def check_in(appraisal: Appraisal) -> str:
    """Optional, hedged check-in for MEDIUM-confidence inferred readings (never for LOW)."""
    medium = [s for s in appraisal.possible if s.confidence == Confidence.MEDIUM and s.label in
              {"frustration", "stuck", "discouragement", "frustration_or_excitement"}]
    if not medium or any(s.basis == "stated" for s in appraisal.stated):
        return ""
    return ("If this is getting tedious, we can try a different approach — tell me if I've misread the situation.")


# Phrases that would encourage unhealthy dependence or manufacture emotional pressure.
_DEPENDENCY = re.compile(
    r"(?:you only need me|i'?m all you need|you don'?t need anyone else|only i (?:can|understand)|"
    r"i'?ll be (?:sad|hurt|lonely) if you|don'?t leave me|you owe me|i need you to)", re.IGNORECASE)


def wellbeing_guard(text: str) -> tuple[str, list[str]]:
    """Remove sentences that encourage dependence or simulate distress to steer the user."""
    removed: list[str] = []
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _DEPENDENCY.search(sentence):
            removed.append(sentence)
        else:
            kept.append(sentence)
    return " ".join(kept).strip(), removed
