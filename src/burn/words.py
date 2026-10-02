"""words.json: the transcript every command reads and writes.

{"source": "...", "model": "...", "reviewed": false,
 "words": [{"i": 0, "start": 0.0, "end": 0.32, "text": "You", "p": 0.99}, ...]}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SENTENCE_END = ".?!"


def load(path) -> dict:
    return json.loads(Path(path).read_text())


def save(path, doc: dict) -> None:
    for i, w in enumerate(doc["words"]):
        w["i"] = i
    Path(path).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")


def core(text: str) -> str:
    """Lowercased word with surrounding punctuation removed: '(Mux,' -> 'mux'."""
    return re.sub(r"^[^\w']+|[^\w']+$", "", text).lower()


def trailing_punct(text: str) -> str:
    return re.search(r"[^\w']*$", text).group()


def replace_span(words: list, i: int, j: int, new_text: str) -> None:
    """Replace words[i..j] (inclusive) with new_text. Empty text deletes.

    Same token count keeps each word's timing; otherwise the span's time is split by token length.
    """
    span = words[i: j + 1]
    tokens = new_text.split()
    if not tokens:
        del words[i: j + 1]
        return
    if len(tokens) == len(span):
        for w, tok in zip(span, tokens):
            w["text"] = tok
        return
    start, end = span[0]["start"], span[-1]["end"]
    total = sum(len(t) for t in tokens)
    t0, new = start, []
    for tok in tokens:
        t1 = t0 + (end - start) * len(tok) / total
        new.append(dict(start=round(t0, 3), end=round(t1, 3), text=tok, p=1.0))
        t0 = t1
    words[i: j + 1] = new


def replace_phrase(words: list, src: str, dst: str) -> int:
    """Replace every occurrence of phrase src (case/punctuation-insensitive) with dst.

    Keeps the matched span's trailing punctuation. Spans already reading exactly dst are left
    alone and not counted, so the return value is the number of real changes.
    """
    pat = [core(t) for t in src.split()]
    n, i, hits = len(pat), 0, 0
    if not n:
        return 0
    dst_tokens = dst.split()
    while i <= len(words) - n:
        span = words[i: i + n]
        if [core(w["text"]) for w in span] == pat:
            trail = trailing_punct(span[-1]["text"])
            new_text = " ".join(dst_tokens) + trail
            if " ".join(w["text"] for w in span) != new_text:
                replace_span(words, i, i + n - 1, new_text)
                hits += 1
            i += max(len(dst_tokens), 1) if dst_tokens else 0
        else:
            i += 1
    return hits


def lines_of(words: list, max_words: int = 14, pause: float = 0.6):
    """Group words into readable lines: break on sentence end, pauses, or length."""
    line = []
    for w in words:
        if line and (len(line) >= max_words or w["start"] - line[-1]["end"] > pause
                     or line[-1]["text"][-1:] in SENTENCE_END):
            yield line
            line = []
        line.append(w)
    if line:
        yield line
