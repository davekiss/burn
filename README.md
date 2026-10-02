# burn

One-word-at-a-time burned-in captions for short-form video, built to be driven by a coding agent.

```
burn transcribe talk.mp4          # local Whisper, word timings, your glossary applied
burn lint talk.words.json         # finds the mechanical errors, prints one fix command
burn sheet talk.mp4 talk.words.json -t 42   # every caption style, tiled in one image
burn render talk.mp4 talk.words.json --style pop   # burns it in, then verifies its own output
```

There's no UI. The agent (Claude Code, Codex, whatever you use) is the editor: it reads the
transcript, fixes what Whisper got wrong, picks a style by looking at a contact sheet, renders,
and checks the result by looking at a labeled frame grid. Every step prints the next command and
supports `--json`.

## Why it's agent-shaped

- **Review is a loop, not a vibe.** `burn lint` catches what agents otherwise have to hunt for:
  Whisper's "Thank you." hallucination at the end of a clip, lowercase `i`, sentence-case slips,
  stutters, a name written three ways. Safe fixes come back as a single `burn fix` command;
  judgment calls are listed separately.
- **It learns your vocabulary.** `~/.config/burn/glossary.txt` holds the names and jargon you say
  a lot (`Claude Code <= cloud code`). It primes Whisper, gets applied automatically after every
  transcription, and powers lint's "did you mean" check. Every video makes the next one cleaner.
- **Doubts get checked, not guessed.** `burn listen VIDEO 12.5 18` re-transcribes a span several
  ways (plain, glossary-primed, sampled) and prints them next to the current text.
- **Done means verified.** `burn render` ends with `burn verify`: duration, audio and size are
  checked against the source, and a 3x3 grid of frames, each labeled with the word that should be
  on screen, is written for the agent to look at.
- **Indices are explicit and edits are atomic.** `burn show --index` and `burn fix --set 41-42=...`
  apply a whole batch against the indices the agent just read.

## Install

Requires `ffmpeg` built with libass (Homebrew's is) and [uv](https://docs.astral.sh/uv/).
Transcription uses [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), so it
needs an Apple Silicon Mac. Everything else (lint, fix, render, verify) runs anywhere ffmpeg does.

```sh
uv tool install git+https://github.com/davekiss/burn
burn skill install        # adds the Claude Code skill to ~/.claude/skills/burn
```

Other agents: point them at [AGENTS.md](AGENTS.md).

The first transcription downloads `whisper-large-v3-turbo` (~1.6 GB). After that, 2 minutes of
audio takes about 30 seconds on an M-series Mac.

## Styles

| style | look |
|---|---|
| `bold`  | White Montserrat Black, heavy outline, quick grow-in (default) |
| `pop`   | Yellow Anton, outline, bouncy overshoot |
| `box`   | White on a solid black box, for busy footage |
| `clean` | Mixed case, soft outline and shadow, fade |
| `neon`  | Bebas Neue with a cyan glow |

Any key can be overridden: `--opt color=#ff4d4d --opt size=0.12 --opt position=lower --opt uppercase=false`.
Sizes are fractions of the short side of the frame, so presets work for 16:9 and 9:16.
Drop extra `.ttf` files in `src/burn/fonts/` and reference them by family name with `--opt font=...`.

## Commands

| command | does |
|---|---|
| `transcribe VIDEO` | Whisper → `VIDEO.words.json` (word timings + confidence), glossary applied |
| `lint W.json` | Suggestions marked AUTO (safe) or CHECK (judgment), plus one fix command |
| `show W.json [--index]` | Readable transcript; `⟨word⟩` marks low confidence |
| `fix W.json --set I[-J]=TEXT --replace FROM=>TO [--shift S] [--done]` | Batch edits |
| `listen VIDEO START END` | Re-transcribe a span several ways to settle a doubtful word |
| `glossary [list\|add\|path]` | Manage the vocabulary file |
| `styles` | Presets and overridable keys |
| `sheet VIDEO W.json -t T` | All styles at one moment, tiled and labeled |
| `still VIDEO W.json -t T [-t T2]` | Single frames in one style |
| `render VIDEO W.json --style S` | Burn in (`--clip A:B` preview, `--fps 60`, `--encoder videotoolbox`) |
| `verify OUT.mp4` | Checks plus a labeled frame grid (runs automatically after render) |

`render` writes `OUT.ass` (reusable in any editor that takes ASS subtitles) and `OUT.burn.json`
(what `verify` needs) next to the video.

## Development

```sh
uv run pytest
```

The render tests generate a synthetic video, so they need ffmpeg but no fixtures.

## License

MIT. Bundled fonts (Montserrat, Anton, Bebas Neue) are under the SIL Open Font License; see `src/burn/fonts/`.
