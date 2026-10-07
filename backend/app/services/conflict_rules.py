"""Rule-based contradiction detection between two semantically similar memories.

Semantic similarity alone says two memories talk about the *same thing*; it
does not say whether they *disagree*. After the vector search has produced
candidates above ``CONFLICT_SIMILARITY_THRESHOLD`` these rules look for an
actual contradiction:

1. key_value      - "Database = MongoDB" vs "Database = PostgreSQL",
                    "token lifetime is 15 minutes" vs "... is 30 minutes"
2. decision_choice- "MongoDB was selected" vs "We selected PostgreSQL"
3. negation       - "Always retry failed payments" vs "Never retry failed payments"

The rules are deliberately conservative (a value that *contains* the other,
e.g. "PostgreSQL" -> "PostgreSQL + Redis", is treated as a refinement rather
than a contradiction) and every detection is only a *flag* for human review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_STOP_PREFIX = re.compile(r"^(?:the|our|a|an|this|that|we|current|new|primary)\s+", re.I)
_SPACES = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")

_KV_PATTERN = re.compile(
    r"(?P<key>[A-Za-z][A-Za-z0-9 _\-/]{1,40}?)\s*(?:=|:=|->|:)\s*(?P<val>[^\n.;,]{1,60})"
)
_IS_PATTERN = re.compile(
    r"(?P<key>[A-Za-z][A-Za-z0-9 _\-/]{2,40}?)\s+(?:is|are|was|were|will be|should be|must be|is set to|set to|equals)\s+"
    r"(?P<val>[^\n.;,]{1,60})",
    re.I,
)
_VERB_PATTERN = re.compile(
    r"(?P<key>[A-Za-z][A-Za-z0-9 _\-/]{2,40}?)\s+(?:must\s+|should\s+|will\s+|shall\s+|always\s+)?"
    r"(?:return|returns|respond with|responds with)\s+(?P<val>[^\n.;,]{1,60})",
    re.I,
)
_CHOICE_ACTIVE = re.compile(
    r"\b(?:selected|chose|chosen|adopted|picked|decided on|decided to use|standardi[sz]ed on|migrated to|switched to|went with)\s+"
    r"(?P<val>[A-Z][\w.+#\-]*(?:\s*(?:\+|and)\s*[A-Z][\w.+#\-]*)*)"
    r"(?:\s+(?:as|for)\s+(?:the\s+|our\s+)?(?P<key>[a-z][\w \-]{2,30}))?",
)
_CHOICE_PASSIVE = re.compile(
    r"\b(?P<val>[A-Z][\w.+#\-]*(?:\s*(?:\+|and)\s*[A-Z][\w.+#\-]*)*)\s+(?:was|has been|is|will be)\s+"
    r"(?:selected|chosen|adopted|picked|used)"
    r"(?:\s+(?:as|for)\s+(?:the\s+|our\s+)?(?P<key>[a-z][\w \-]{2,30}))?",
)

_NEGATIONS = {"not", "never", "no", "don't", "dont", "doesn't", "shouldn't", "mustn't", "cannot", "can't", "avoid", "disable", "disabled"}
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "be", "is", "are", "was", "were",
    "should", "must", "always", "do", "does", "we", "it", "this", "that", "when", "if", "by", "as", "at",
    "from", "our", "all", "any", "can", "will",
}
_VAGUE_KEYS = {
    "it", "this", "that", "there", "which", "who", "what", "issue", "problem", "reason", "result", "cause",
    # generic labels ("Fix: ...", "Root cause: ...") introduce prose, not a single-valued fact
    "fix", "solution", "workaround", "root cause", "note", "notes", "summary", "example", "update", "step",
    "steps", "todo", "warning", "impact", "resolution", "context", "details", "action", "outcome",
}


@dataclass(frozen=True)
class Contradiction:
    reason: str
    method: str


_KEY_TAIL = re.compile(r"\s+(?:for|in|on|of|to|with|at|because|since|and|when|so)\s+.*$")


def _norm_key(key: str) -> str:
    key = _SPACES.sub(" ", key.strip().lower())
    key = _KEY_TAIL.sub("", key)
    while True:
        stripped = _STOP_PREFIX.sub("", key)
        if stripped == key:
            break
        key = stripped
    return key.strip(" -_/")


def _norm_val(val: str) -> str:
    val = _SPACES.sub(" ", val.strip().lower())
    val = re.sub(r"^(?:the|a|an)\s+", "", val)
    return val.strip(" \"'`()[]")


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and w not in _NEGATIONS}


def extract_assertions(text: str) -> dict[str, str]:
    """Map normalised subject -> normalised value for simple factual statements."""
    found: dict[str, str] = {}
    for pattern in (_KV_PATTERN, _IS_PATTERN, _VERB_PATTERN):
        for m in pattern.finditer(text):
            key, val = _norm_key(m.group("key")), _norm_val(m.group("val"))
            if not key or not val or key in _VAGUE_KEYS or len(key.split()) > 5:
                continue
            found.setdefault(key, val)
    return found


def extract_choices(text: str) -> dict[str, str]:
    """Map decision subject (or 'choice') -> chosen option."""
    found: dict[str, str] = {}
    for pattern in (_CHOICE_ACTIVE, _CHOICE_PASSIVE):
        for m in pattern.finditer(text):
            key = _norm_key(m.group("key") or "choice")
            found.setdefault(key, _norm_val(m.group("val")))
    return found


def _values_disagree(a: str, b: str) -> bool:
    if a == b:
        return False
    # A refinement ("postgresql" -> "postgresql + redis") is not a contradiction.
    if a in b or b in a:
        return False
    return True


def _stated_in(value: str, text: str, ratio: float = 0.6) -> bool:
    """True when most content words of ``value`` already appear in ``text``
    (the other memory says the same thing in different words)."""
    words = _tokens(value)
    if not words:
        return False
    return len(words & _tokens(text)) / len(words) >= ratio


def _disagree(new_val: str, old_val: str, new_text: str, old_text: str) -> bool:
    return _values_disagree(new_val, old_val) and not _stated_in(new_val, old_text) and not _stated_in(old_val, new_text)


def _polarity_sentences(text: str) -> list[tuple[bool, set[str]]]:
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        words = set(re.findall(r"[a-z']+", sentence.lower()))
        tokens = _tokens(sentence)
        if len(tokens) >= 2:
            out.append((bool(words & _NEGATIONS), tokens))
    return out


def detect_contradiction(new_text: str, existing_text: str) -> Contradiction | None:
    # 1) explicit key/value facts
    new_facts, old_facts = extract_assertions(new_text), extract_assertions(existing_text)
    for key, new_val in new_facts.items():
        old_val = old_facts.get(key)
        if old_val is not None and _disagree(new_val, old_val, new_text, existing_text):
            return Contradiction(f"'{key}' is '{old_val}' in the existing memory but '{new_val}' in the new one", "key_value")

    # 2) decisions / technology choices
    new_choices, old_choices = extract_choices(new_text), extract_choices(existing_text)
    for key, new_val in new_choices.items():
        old_val = old_choices.get(key)
        if old_val is None and key != "choice":
            old_val = old_choices.get("choice")
        if old_val is None and key == "choice" and len(old_choices) == 1:
            old_val = next(iter(old_choices.values()))
        if old_val is not None and _disagree(new_val, old_val, new_text, existing_text):
            subject = "decision" if key == "choice" else key
            return Contradiction(f"Existing {subject} chose '{old_val}' but the new memory chooses '{new_val}'", "decision_choice")

    # 3) opposite polarity over (almost) the same statement
    for new_neg, new_tokens in _polarity_sentences(new_text):
        for old_neg, old_tokens in _polarity_sentences(existing_text):
            if new_neg == old_neg:
                continue
            overlap = len(new_tokens & old_tokens) / max(len(new_tokens | old_tokens), 1)
            if overlap >= 0.6:
                return Contradiction("The memories make opposite recommendations about the same subject", "negation")
    return None
