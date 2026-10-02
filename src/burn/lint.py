"""Transcript checks that turn the mechanical part of review into fix-ready suggestions.

Each issue carries a `fix` (a `burn fix` argument) and a `confidence`:
  auto  - safe to apply as-is (casing, lowercase i, known hallucinations, glossary spelling)
  check - plausible, but listen or read context first (near-misses, stutters, low confidence)
"""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass

from .words import SENTENCE_END, core, trailing_punct

# Whisper's classic outro/silence hallucinations
HALLUCINATIONS = ["thank you", "thanks for watching", "thank you for watching", "thank you so much",
                  "please subscribe", "subtitles by", "bye", "you"]
PRONOUN_I = {"i", "i'm", "i've", "i'll", "i'd"}
# common words that are never capitalized mid-sentence
COMMON = set("""a an and as at be but by for from he her his how if in into is it its it's me my no not
of on or our out she so than that the their them then there these they this to too up us was we well
what when where which who why will with you your you're you'll""".split())
LOW_CONFIDENCE = 0.5
NEAR_MISS = 0.8


@dataclass
class Issue:
    kind: str
    i: int
    j: int
    start: float
    text: str
    message: str
    confidence: str  # auto | check
    fix: str | None = None  # a `burn fix` argument, e.g. "--set 12=I"

    def to_dict(self):
        return asdict(self)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _set(i: int, j: int, text: str) -> str:
    rng = f"{i}" if i == j else f"{i}-{j}"
    return f"--set {rng}={text}"


def lint(words: list, terms=()) -> list[Issue]:
    issues: list[Issue] = []
    n = len(words)

    def add(kind, i, j, msg, conf, fix=None):
        issues.append(Issue(kind, i, j, words[i]["start"],
                            " ".join(w["text"] for w in words[i:j + 1]), msg, conf, fix))

    # hallucinated phrases: at the very end, or after a long silence, with a near-zero-length or unsure word
    for phrase in HALLUCINATIONS:
        pat = phrase.split()
        for i in range(n - len(pat) + 1):
            j = i + len(pat) - 1
            if [core(w["text"]) for w in words[i:j + 1]] != pat:
                continue
            span = words[i:j + 1]
            at_end = j >= n - 2
            after_gap = i > 0 and span[0]["start"] - words[i - 1]["end"] > 1.5
            suspicious = min(w.get("p", 1) for w in span) < 0.3 or any(w["end"] - w["start"] < 0.02 for w in span)
            if (at_end or after_gap) and suspicious:
                add("hallucination", i, j, "likely Whisper hallucination (silence filler)", "auto", _set(i, j, ""))

    for k, w in enumerate(words):
        text, c = w["text"], core(w["text"])
        prev = words[k - 1]["text"] if k else None

        if not re.search(r"\w", text):
            add("empty", k, k, "punctuation-only word", "auto", _set(k, k, ""))
            continue
        if c in PRONOUN_I and text.lstrip("\"'(")[:1] == "i":
            add("lowercase-i", k, k, "pronoun I", "auto", _set(k, k, text.replace("i", "I", 1)))
        elif text[:1].islower() and (prev is None or prev[-1:] in SENTENCE_END):
            add("sentence-case", k, k, "sentence should start uppercase", "auto", _set(k, k, _cap(text)))
        elif (text[:1].isupper() and c in COMMON and prev is not None
              and prev[-1:] not in SENTENCE_END + ":\"" and not text.isupper()):
            add("mid-sentence-caps", k, k, "capitalized mid-sentence", "auto", _set(k, k, text[:1].lower() + text[1:]))

        if (k + 1 < n and c and c == core(words[k + 1]["text"]) and c not in {"that", "had", "is"}
                and text[-1:] not in SENTENCE_END):
            add("repeat", k, k + 1, "repeated word (stutter?)", "check", _set(k, k, ""))

        if w.get("p", 1) < LOW_CONFIDENCE and not any(x.i <= k <= x.j for x in issues if x.kind == "hallucination"):
            add("low-confidence", k, k, f"confidence {w['p']:.2f}; try `burn listen`", "check")

    # glossary: wrong casing (auto) and near-miss spellings (check)
    for t in terms:
        for alias in t.aliases:
            pat = [core(x) for x in alias.split()]
            for i in range(n - len(pat) + 1):
                if [core(w["text"]) for w in words[i:i + len(pat)]] == pat:
                    add("glossary-alias", i, i + len(pat) - 1, f"known mishearing of {t.text!r}", "auto",
                        f"--replace {alias}=>{t.text}")
        key, size = t.key, len(t.text.split())
        for i in range(n - size + 1):
            span = words[i:i + size]
            gram = " ".join(core(w["text"]) for w in span)
            shown = " ".join(w["text"] for w in span)
            want = t.text + trailing_punct(span[-1]["text"])
            if gram == key:
                if shown != want:
                    add("glossary-case", i, i + size - 1, f"glossary spells it {t.text!r}", "auto",
                        f"--replace {shown.rstrip('.,;:?!')}=>{t.text}")
            elif len(key) >= 4 and key not in gram and difflib.SequenceMatcher(None, gram, key).ratio() >= NEAR_MISS:
                add("glossary-near-miss", i, i + size - 1, f"sounds like glossary term {t.text!r}?", "check",
                    f"--replace {gram}=>{t.text}")

    # same word written several ways (mux / MUX), ignoring sentence-initial capitals
    forms: dict[str, dict[str, int]] = {}
    for k, w in enumerate(words):
        if k and words[k - 1]["text"][-1:] not in SENTENCE_END:
            c = core(w["text"])
            if len(c) > 1 and c not in PRONOUN_I and c not in COMMON:
                shown = w["text"].strip(".,;:?!\"'()")
                forms.setdefault(c, {})
                forms[c][shown] = forms[c].get(shown, 0) + 1
    glossary_keys = {t.key for t in terms}
    for c, variants in forms.items():
        if len(variants) > 1 and c not in glossary_keys and any(v != v.lower() for v in variants):
            best = max(variants, key=lambda v: (v != v.lower(), variants[v]))
            first = next(k for k, w in enumerate(words) if core(w["text"]) == c)
            add("inconsistent", first, first, f"written as {sorted(variants)}; add to glossary if it's a name",
                "check", f"--replace {c}=>{best}")

    issues.sort(key=lambda x: (x.i, x.kind))
    return dedupe(issues)


def dedupe(issues: list[Issue]) -> list[Issue]:
    """Drop issues inside a span already being deleted, and duplicate --set fixes on one index."""
    deleted = [(x.i, x.j) for x in issues if x.fix and x.fix.endswith("=") and x.confidence == "auto"]
    seen, out = set(), []
    for x in issues:
        inside = any(a <= x.i and x.j <= b and not (x.fix and x.fix.endswith("=")) for a, b in deleted)
        key = (x.i, x.j, x.fix)
        if inside or key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out


def fix_command(words_path: str, issues: list[Issue]) -> str | None:
    """One `burn fix` invocation applying every auto fix."""
    args, used = [], set()
    for x in issues:
        if x.confidence != "auto" or not x.fix or x.fix in used:
            continue
        used.add(x.fix)
        flag, _, val = x.fix.partition(" ")
        args.append(f"{flag} {_quote(val)}")
    return f"burn fix {_quote(words_path)} " + " ".join(args) if args else None


def _quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
