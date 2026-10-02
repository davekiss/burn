"""Glossary: proper nouns and jargon that should always be spelled one way.

One term per line, optional known mishearings after `<=`:

    Claude Code <= cloud code, clod code
    Mux
    .env

Used three ways: as Whisper's vocabulary prompt, auto-applied right after transcription,
and by `burn lint` to flag near-misses. Keep it to proper nouns and jargon; a common word
like "Go" would get capitalized everywhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .words import core, replace_phrase

PROMPT_CHARS = 600  # Whisper's prompt window is ~224 tokens


def default_path() -> Path:
    return Path(os.environ.get("BURN_GLOSSARY", "~/.config/burn/glossary.txt")).expanduser()


@dataclass
class Term:
    text: str
    aliases: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return " ".join(core(t) for t in self.text.split())


def parse_line(line: str) -> Term | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    text, _, aliases = line.partition("<=")
    return Term(text.strip(), [a.strip() for a in aliases.split(",") if a.strip()])


def load(path: Path | None = None) -> list[Term]:
    path = path or default_path()
    if not path.exists():
        return []
    return [t for t in (parse_line(l) for l in path.read_text().splitlines()) if t]


def add(entries: list[str], path: Path | None = None) -> list[str]:
    """Append entries (same syntax as the file), merging aliases into existing terms."""
    path = path or default_path()
    terms = load(path)
    by_key = {t.key: t for t in terms}
    changed = []
    for e in entries:
        new = parse_line(e)
        if not new:
            continue
        cur = by_key.get(new.key)
        if cur:
            cur.text = new.text
            cur.aliases += [a for a in new.aliases if a not in cur.aliases]
        else:
            terms.append(new)
            by_key[new.key] = new
        changed.append(new.text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(
        f"{t.text} <= {', '.join(t.aliases)}\n" if t.aliases else f"{t.text}\n" for t in terms))
    return changed


def prompt(terms: list[Term], extra: str | None = None) -> str | None:
    parts = [extra] if extra else []
    parts += [t.text for t in terms]
    text = ", ".join(parts)
    return text[:PROMPT_CHARS].rsplit(",", 1)[0] if len(text) > PROMPT_CHARS else (text or None)


def apply(words: list, terms: list[Term]) -> list[str]:
    """Rewrite known mishearings and normalize casing. Returns a human-readable change list."""
    changes = []
    for t in terms:
        for alias in t.aliases:
            if n := replace_phrase(words, alias, t.text):
                changes.append(f"{alias!r} -> {t.text!r} x{n}")
        if n := replace_phrase(words, t.text, t.text):
            changes.append(f"case -> {t.text!r} x{n}")
    return changes
