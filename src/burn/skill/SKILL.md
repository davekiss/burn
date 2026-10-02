---
name: burn
description: Burn one-word-at-a-time social captions into a video with the `burn` CLI. Local Whisper transcription, agent review with lint, style contact sheets, ffmpeg render with self-verification. Use when asked to caption, subtitle, or add burned-in/animated word captions to a video.
---

# burn

`burn` must be on PATH (`uv tool install git+https://github.com/davekiss/burn`). Every command
supports `-h`; most support `--json`. Outputs land next to the video unless `-o` is given.

## The loop

1. **Transcribe.** `burn transcribe VIDEO [--prompt "names specific to this video"]`
   The glossary (`burn glossary`) is applied automatically. Writes `VIDEO.words.json`.

2. **Lint.** `burn lint W.json`
   - AUTO items are mechanical (casing, lowercase i, end-of-clip "Thank you." hallucinations,
     glossary spelling). Run the printed `burn fix ...` command.
   - CHECK items need judgment: stutters (usually delete for captions), glossary near-misses,
     low-confidence words. Decide each one.

3. **Read it.** `burn show W.json`. Lint can't catch a real word that's wrong
   ("sending them to the ending model"). Read for meaning, with the video's topic in mind.
   Fix transcription errors, not the speaker's grammar or word choice.
   Not sure? `burn listen VIDEO START END` re-transcribes the span several ways. If every take
   agrees, keep the text and mention it to the user instead of guessing.

4. **Fix.** `burn show W.json --index`, then one call:
   `burn fix W.json --set "236=" --set "41-42=Claude Code" --replace "mux=>Mux" --done`
   - `--set` indices come from the latest `show --index`. Batch all `--set` edits in one call;
     if words were added or removed, re-run `show --index` before the next batch.
   - `--replace` changes every occurrence: use it for names, never for common words.
   - `--done` marks the transcript reviewed (render warns otherwise).

5. **Teach the glossary.** For any name or jargon you corrected:
   `burn glossary add "Claude Code <= cloud code" "Mux"`. Next video gets it right on the first pass.

6. **Style.** Use the user's requested style. If none, use `bold`, or offer options with
   `burn sheet VIDEO W.json -t T` (pick T mid-word from `show`) and read the PNG yourself.
   Tweak with `--opt key=value` (`burn styles` lists keys).

7. **Render.** `burn render VIDEO W.json --style bold`
   - Unsure about a style or position? Try `--clip 30:40` first.
   - 120fps screen recordings: `--fps 60` for social; `--encoder videotoolbox` is much faster on a Mac.
   - Render runs `verify` at the end. **Read the frame grid it prints.** Each tile says which
     word it expects; confirm the word is there, legible, and not covering anything important.
     Also look for anything sensitive visible in the footage (API keys, tokens, emails) and tell the user.
     If verify reports a mismatch, it exits 1. Fix the cause before you report success.

8. **Report** the output path, the corrections made, and any CHECK items you left as-is.
