# AGENTS.md

`burn` is a CLI meant to be operated by you. The full workflow is in
[src/burn/skill/SKILL.md](src/burn/skill/SKILL.md): transcribe → lint → show → fix → glossary add → style → render → read the verify grid.

## Working on this repo

- `src/burn/cli.py`: argparse and commands. `words.py`: transcript editing. `glossary.py`: vocabulary.
  `lint.py`: transcript checks. `render.py`: styles, ASS generation, ffmpeg.
- `uv run pytest`. Render tests build a synthetic video with ffmpeg; no fixtures.
- Tests must be able to fail: assert behavior (pixels on screen, timings, resulting text), not constants.
- New lint checks: label them `auto` only if applying them blindly can't make a correct transcript wrong.
- New style presets: add to `STYLES` in `render.py`, then compare them with `burn sheet` on a real video.
