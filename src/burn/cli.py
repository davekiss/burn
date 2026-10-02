"""burn: one-word-at-a-time burned-in captions, built for agents.

  transcribe -> lint / show / fix (review) -> sheet / still (style) -> render -> verify

  burn transcribe VIDEO                      Whisper (local) -> VIDEO.words.json, glossary applied
  burn lint W.json                           fix-ready suggestions + one `burn fix` command
  burn show W.json [--index]                 read the transcript
  burn listen VIDEO 12.5 18 [--words W]      re-transcribe a span to double-check a word
  burn fix W.json --replace "a=>B" --set "12-13=Text" [--done]
  burn glossary add "Claude Code <= cloud code" "Mux"
  burn sheet VIDEO W.json -t 42              every style tiled in one PNG
  burn render VIDEO W.json --style bold      burn in, then verify automatically
  burn verify OUT.mp4                        duration/audio checks + labeled frame grid
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import glossary as gl
from . import render as rd
from .lint import fix_command, lint
from .words import core, lines_of, load, replace_phrase, replace_span, save

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
LOW_CONFIDENCE = 0.5


def die(msg: str, hint: str | None = None):
    print(f"error: {msg}" + (f"\n  hint: {hint}" if hint else ""), file=sys.stderr)
    sys.exit(2)


def emit(a, data: dict, text: str):
    print(json.dumps(data, indent=1, ensure_ascii=False) if getattr(a, "json", False) else text)


def words_path_for(video) -> Path:
    v = Path(video).expanduser()
    return v.with_suffix(".words.json")


def need(path, what="file"):
    p = Path(path).expanduser()
    if not p.exists():
        die(f"{what} not found: {p}")
    return p


def whisper(audio, model, prompt=None, temperature=0.0, language="en"):
    try:
        import mlx_whisper
    except ImportError:
        die("mlx-whisper is not installed (it needs Apple Silicon)",
            "transcription runs on Apple Silicon Macs; everything else works anywhere ffmpeg+libass does")
    return mlx_whisper.transcribe(str(audio), path_or_hf_repo=model, word_timestamps=True, language=language,
                                  initial_prompt=prompt, condition_on_previous_text=False,
                                  temperature=temperature, verbose=None)


def result_words(result, offset=0.0):
    out = []
    for seg in result["segments"]:
        for w in seg.get("words", []):
            text = w["word"].strip()
            if text:
                out.append(dict(start=round(w["start"] + offset, 3), end=round(w["end"] + offset, 3),
                                text=text, p=round(w.get("probability", 1.0), 3)))
    return out


# --------------------------------------------------------------------------- transcript
def cmd_transcribe(a):
    video = need(a.video, "video")
    out = Path(a.output) if a.output else words_path_for(video)
    terms = [] if a.no_glossary else gl.load()
    prompt = gl.prompt(terms, a.prompt)
    print(f"transcribing {video.name} ({a.model}) ...", file=sys.stderr)
    result = whisper(video, a.model, prompt, language=a.language)
    words = result_words(result)
    changes = gl.apply(words, terms)
    doc = dict(source=str(video), model=a.model, language=result.get("language"), prompt=prompt,
               reviewed=False, words=words)
    save(out, doc)
    issues = lint(words, terms)
    low = sum(1 for w in words if w["p"] < LOW_CONFIDENCE)
    emit(a, dict(words_file=str(out), words=len(words), low_confidence=low, glossary_changes=changes,
                 lint_issues=len(issues)),
         f"wrote {out}\n  {len(words)} words, {low} low-confidence, {len(terms)} glossary terms"
         + (f"\n  glossary fixed: {'; '.join(changes)}" if changes else "")
         + f"\nnext: burn lint '{out}'")


def cmd_show(a):
    doc = load(need(a.words))
    words = doc["words"][a.from_: (a.to + 1 if a.to is not None else None)]
    if a.json:
        print(json.dumps(words, ensure_ascii=False))
        return
    print(f"# {doc['source']}  reviewed={doc.get('reviewed')}  words={len(doc['words'])}  "
          f"⟨w⟩ = confidence < {LOW_CONFIDENCE}")
    for line in lines_of(words):
        parts = []
        for w in line:
            t = f"⟨{w['text']}⟩" if w.get("p", 1) < LOW_CONFIDENCE else w["text"]
            parts.append(f"{w['i']}:{t}" if a.index else t)
        print(f"{line[0]['i']:>4}-{line[-1]['i']:<4} {line[0]['start']:>7.2f}s  {' '.join(parts)}")


def cmd_lint(a):
    path = need(a.words)
    doc = load(path)
    issues = lint(doc["words"], gl.load())
    cmd = fix_command(str(path), issues)
    if a.json:
        print(json.dumps(dict(issues=[x.to_dict() for x in issues], fix_command=cmd), indent=1, ensure_ascii=False))
        return
    if not issues:
        print("no issues found. Still read `burn show` once for meaning-level errors.")
        return
    for conf, title in (("auto", "AUTO (safe to apply)"), ("check", "CHECK (use judgment / burn listen)")):
        group = [x for x in issues if x.confidence == conf]
        if group:
            print(f"{title}:")
            for x in group:
                rng = f"{x.i}" if x.i == x.j else f"{x.i}-{x.j}"
                print(f"  {x.kind:<19} {rng:>9} {x.start:>7.2f}s  {x.text!r:<24} {x.message}"
                      + (f"   [{x.fix}]" if x.fix else ""))
    if cmd:
        print(f"\napply all AUTO fixes:\n  {cmd}")
    print("\nthen read `burn show` for errors lint can't see (wrong-but-real words), and add new names "
          "with `burn glossary add`.")


def cmd_fix(a):
    path = need(a.words)
    doc = load(path)
    words = doc["words"]
    edits = []
    for s in a.set or []:
        rng, sep, text = s.partition("=")
        if not sep:
            die(f"--set needs I=TEXT or I-J=TEXT, got {s!r}")
        lo, _, hi = rng.partition("-")
        lo, hi = int(lo), int(hi or lo)
        if not (0 <= lo <= hi < len(words)):
            die(f"--set index {rng} out of range 0-{len(words) - 1}", "indices come from the latest `burn show --index`")
        edits.append((lo, hi, text))
    spans = sorted(edits)
    for (lo1, hi1, _), (lo2, _, _) in zip(spans, spans[1:]):
        if lo2 <= hi1:
            die(f"--set spans overlap at {lo2}")
    for lo, hi, text in sorted(edits, reverse=True):  # highest first so earlier indices stay valid
        print(f"  [{lo}-{hi}] {' '.join(w['text'] for w in words[lo:hi + 1])!r} -> {text!r}")
        replace_span(words, lo, hi, text)
    for r in a.replace or []:
        src, sep, dst = r.partition("=>")
        if not sep:
            die(f"--replace needs FROM=>TO, got {r!r}")
        print(f"  {src.strip()!r} -> {dst.strip()!r}: {replace_phrase(words, src.strip(), dst.strip())} change(s)")
    if a.shift:
        for w in words:
            w["start"] = round(max(0, w["start"] + a.shift), 3)
            w["end"] = round(max(0, w["end"] + a.shift), 3)
        print(f"  shifted all words by {a.shift:+.3f}s")
    if a.done:
        doc["reviewed"] = True
    save(path, doc)
    print(f"saved {path} ({len(words)} words, reviewed={doc['reviewed']})"
          + ("" if a.done else "\nindices changed if words were added/removed; re-run `burn show --index` before more --set"))


def cmd_listen(a):
    video = need(a.video, "video")
    s, e = max(0.0, a.start - a.pad), a.end + a.pad
    terms = gl.load()
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "clip.wav"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}",
                        "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
        takes = [("plain", None, 0.0), ("glossary prompt", gl.prompt(terms), 0.0), ("sampled t=0.4", None, 0.4)]
        if a.context:
            takes.insert(1, ("context prompt", a.context, 0.0))
        results = []
        for name, prompt, temp in takes:
            if name == "glossary prompt" and not prompt:
                continue
            ws = result_words(whisper(wav, a.model, prompt, temperature=temp), offset=s)
            results.append(dict(take=name, text=" ".join(w["text"] for w in ws),
                                words=[f"{w['text']}({w['p']:.2f})" for w in ws if a.start <= w["start"] <= a.end]))
    current = None
    wpath = Path(a.words) if a.words else words_path_for(video)
    if wpath.exists():
        cw = [w for w in load(wpath)["words"] if a.start - 0.05 <= w["start"] <= a.end]
        if cw:
            current = dict(range=f"{cw[0]['i']}-{cw[-1]['i']}", text=" ".join(w["text"] for w in cw))
    if a.json:
        print(json.dumps(dict(current=current, takes=results), indent=1, ensure_ascii=False))
        return
    if current:
        print(f"current  [{current['range']}]  {current['text']}")
    for r in results:
        print(f"{r['take']:<16} {r['text']}")
    print("\nIf every take agrees, Whisper is hearing it consistently: keep it, or decide from context.")


def cmd_glossary(a):
    path = gl.default_path()
    if a.action == "add":
        if not a.entries:
            die('nothing to add', 'burn glossary add "Claude Code <= cloud code" "Mux"')
        print(f"added/updated {', '.join(gl.add(a.entries, path))} in {path}")
    elif a.action == "path":
        print(path)
    else:
        terms = gl.load(path)
        if a.json:
            print(json.dumps([t.__dict__ for t in terms]))
        else:
            print(f"# {path}" + ("" if terms else " (empty)"))
            for t in terms:
                print(t.text + (f"  <= {', '.join(t.aliases)}" if t.aliases else ""))


# --------------------------------------------------------------------------- output
def style_from(a) -> dict:
    try:
        return rd.resolve_style(a.style, a.opt or [])
    except rd.StyleError as e:
        die(str(e), "burn styles")


def cmd_styles(a):
    if a.json:
        print(json.dumps(rd.STYLES, indent=1))
        return
    for name, s in rd.STYLES.items():
        print(f"{name:<7} {s['desc']}")
    print(f"\npositions: {', '.join(rd.POSITIONS)}  (or --opt y=0.0-1.0)")
    print("override with --opt KEY=VALUE, e.g. --opt color=#ff4d4d --opt uppercase=false --opt size=0.12")
    print("keys:", ", ".join(sorted(set(rd.resolve_style("bold")) - {"desc"} | rd.OPTIONAL_KEYS)))


def cmd_still(a):
    doc = load(need(a.words))
    info = rd.probe(need(a.video, "video"))
    s = style_from(a)
    outdir = Path(a.outdir or tempfile.mkdtemp(prefix="burn-"))
    outdir.mkdir(parents=True, exist_ok=True)
    for t in a.t:
        out = outdir / f"{a.style}-{t:.2f}.png"
        rd.burn(a.video, rd.build_ass(doc["words"], s, info["width"], info["height"]), out,
                ss=t, frames=1, copyts=True, quiet=True)
        print(out)


def cmd_sheet(a):
    doc = load(need(a.words))
    info = rd.probe(need(a.video, "video"))
    names = a.styles.split(",") if a.styles else list(rd.STYLES)
    tmp = Path(tempfile.mkdtemp(prefix="burn-sheet-"))
    tiles = []
    for name in names:
        try:
            s = rd.resolve_style(name, a.opt or [])
        except rd.StyleError as e:
            die(str(e))
        img = tmp / f"{name}.png"
        rd.burn(a.video, rd.build_ass(doc["words"], s, info["width"], info["height"]), img,
                ss=a.t, frames=1, copyts=True, quiet=True)
        tiles.append((name, img))
    print(rd.tile(tiles, Path(a.output) if a.output else tmp / f"sheet-{a.t:.2f}.png"))


def cmd_render(a):
    wpath = need(a.words)
    doc = load(wpath)
    if not doc.get("reviewed"):
        print("warning: transcript not marked reviewed. Run `burn lint`, then `burn fix ... --done`.", file=sys.stderr)
    video = need(a.video, "video")
    info = rd.probe(video)
    s = style_from(a)
    out = Path(a.output) if a.output else video.with_name(f"{video.stem}.captioned-{a.style}.mp4")
    ss = d = None
    offset = 0.0
    if a.clip:
        start, _, end = a.clip.partition(":")
        ss, d = float(start), float(end) - float(start)
        offset = -ss
    out.with_suffix(".ass").write_text(rd.build_ass(doc["words"], s, info["width"], info["height"]))
    extra = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-pix_fmt", "yuv420p"]
    if a.encoder == "videotoolbox":
        extra += ["-c:v", "h264_videotoolbox", "-q:v", str(a.quality or 65)]
    else:
        extra += ["-c:v", "libx264", "-preset", a.preset, "-crf", str(a.quality or 18)]
    if a.fps:
        extra += ["-r", str(a.fps)]
    print(f"rendering {out.name}  style={a.style}  {info['width']}x{info['height']}  {d or info['duration']:.1f}s",
          file=sys.stderr)
    rd.burn(video, rd.build_ass(doc["words"], s, info["width"], info["height"], offset=offset), out,
            ss=ss, duration=d, extra=extra)
    sidecar = dict(source=str(video), words=str(wpath.resolve()), style=a.style, opts=a.opt or [],
                   clip=[ss, ss + d] if a.clip else None)
    out.with_suffix(".burn.json").write_text(json.dumps(sidecar, indent=1) + "\n")
    print(out)
    if not a.no_verify:
        a.output_video = str(out)
        a.frames = 9
        cmd_verify(a)


def cmd_verify(a):
    out = need(getattr(a, "output_video", None) or a.video, "rendered video")
    side_path = out.with_suffix(".burn.json")
    if not side_path.exists():
        die(f"no {side_path.name} beside the video", "verify works on files written by `burn render`")
    side = json.loads(side_path.read_text())
    src, got = rd.probe(side["source"]), rd.probe(out)
    clip = side.get("clip")
    want_dur = (clip[1] - clip[0]) if clip else src["duration"]
    checks = {
        "duration": abs(got["duration"] - want_dur) < 0.25,
        "audio": got["audio"] == src["audio"],
        "size": (got["width"], got["height"]) == (src["width"], src["height"]),
    }
    s = rd.resolve_style(side["style"], side["opts"])
    shown = rd.timeline(load(side["words"])["words"], s)
    lo, hi = (clip if clip else (0, src["duration"]))
    shown = [x for x in shown if lo <= x[0] and x[1] <= hi]
    n = min(a.frames, len(shown))
    picks = [shown[round(k * (len(shown) - 1) / max(n - 1, 1))] for k in range(n)] if n else []
    tmp = Path(tempfile.mkdtemp(prefix="burn-verify-"))
    tiles = []
    for k, (start, end, text) in enumerate(picks):
        t = start + min(rd.SETTLE, (end - start) / 2)  # after the entrance animation, before the word leaves
        img = tmp / f"{k}.png"
        try:
            rd.grab(out, t - lo, img)
        except subprocess.CalledProcessError:
            pass
        if img.exists():
            tiles.append((f"{t:.1f}s  expect: {text}", img))
    checks["frames"] = len(tiles) == len(picks)
    grid = rd.tile(tiles, tmp / "verify.png", cols=3, tile_w=720) if tiles else None
    ok = all(checks.values())
    emit(a, dict(ok=ok, checks=checks, duration=[round(got["duration"], 2), round(want_dur, 2)], grid=str(grid)),
         f"verify: {'OK' if ok else 'FAILED'}  "
         + "  ".join(f"{k}={'ok' if v else 'MISMATCH'}" for k, v in checks.items())
         + f"\n  frames: {grid}\n  Read the frames image: each tile should show its 'expect' word, legible and on-style.")
    if not ok:
        sys.exit(1)


def cmd_skill(a):
    src = Path(__file__).resolve().parent / "skill" / "SKILL.md"
    dest = Path(a.dir).expanduser() / "burn"
    if dest.is_symlink() or dest.exists():
        if not a.force:
            die(f"{dest} exists", "pass --force to replace it")
        dest.unlink() if dest.is_symlink() else shutil.rmtree(dest)
    if a.link:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(src.parent)
    else:
        dest.mkdir(parents=True)
        shutil.copy(src, dest / "SKILL.md")
    print(f"installed skill -> {dest}")


# --------------------------------------------------------------------------- argparse
def main(argv=None):
    p = argparse.ArgumentParser(prog="burn", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, fn, help, json_flag=True):
        sp = sub.add_parser(name, help=help, description=help)
        if json_flag:
            sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(fn=fn)
        return sp

    def style_args(sp):
        sp.add_argument("--style", default="bold", help=f"one of: {', '.join(rd.STYLES)}")
        sp.add_argument("--opt", action="append", metavar="KEY=VALUE", help="override a style key (repeatable)")

    t = cmd("transcribe", cmd_transcribe, "Whisper (local, Apple Silicon) -> words.json; applies the glossary")
    t.add_argument("video")
    t.add_argument("-o", "--output", help="default: VIDEO.words.json")
    t.add_argument("--model", default=DEFAULT_MODEL)
    t.add_argument("--language", default="en")
    t.add_argument("--prompt", help="extra vocabulary hint for this video, added to the glossary terms")
    t.add_argument("--no-glossary", action="store_true")

    s = cmd("show", cmd_show, "print the transcript; ⟨word⟩ marks low confidence")
    s.add_argument("words")
    s.add_argument("--index", action="store_true", help="prefix every word with its index")
    s.add_argument("--from", dest="from_", type=int, default=0)
    s.add_argument("--to", type=int)

    cmd("lint", cmd_lint, "find transcript problems; prints one `burn fix` command for the safe ones").add_argument("words")

    f = cmd("fix", cmd_fix, "edit words; --set indices refer to the latest `show --index`, applied together",
            json_flag=False)
    f.add_argument("words")
    f.add_argument("--set", action="append", metavar="I[-J]=TEXT",
                   help="replace word I or span I..J; empty TEXT deletes; multi-word TEXT splits the timing")
    f.add_argument("--replace", action="append", metavar="FROM=>TO",
                   help="replace a phrase everywhere (case/punctuation-insensitive). Not for common words.")
    f.add_argument("--shift", type=float, help="shift all timings by N seconds")
    f.add_argument("--done", action="store_true", help="mark reviewed")

    li = cmd("listen", cmd_listen, "re-transcribe START..END several ways to double-check what was said")
    li.add_argument("video")
    li.add_argument("start", type=float)
    li.add_argument("end", type=float)
    li.add_argument("--words", help="words.json to compare against (default: VIDEO.words.json)")
    li.add_argument("--pad", type=float, default=1.5, help="seconds of context on each side (default 1.5)")
    li.add_argument("--context", help="a prompt to try, e.g. your best guess of the sentence")
    li.add_argument("--model", default=DEFAULT_MODEL)

    g = cmd("glossary", cmd_glossary, "list/add proper nouns and known mishearings (~/.config/burn/glossary.txt)")
    g.add_argument("action", nargs="?", default="list", choices=["list", "add", "path"])
    g.add_argument("entries", nargs="*", help='"Term" or "Term <= mishearing, other mishearing"')

    cmd("styles", cmd_styles, "list style presets and overridable keys")

    st = cmd("still", cmd_still, "render caption frame(s) to PNG", json_flag=False)
    st.add_argument("video"); st.add_argument("words")
    st.add_argument("-t", type=float, action="append", required=True, help="timestamp (repeatable)")
    st.add_argument("--outdir")
    style_args(st)

    sh = cmd("sheet", cmd_sheet, "every style at one timestamp, tiled into a single labeled PNG", json_flag=False)
    sh.add_argument("video"); sh.add_argument("words")
    sh.add_argument("-t", type=float, required=True, help="pick a time mid-word")
    sh.add_argument("--styles", help="comma list (default: all)")
    sh.add_argument("--opt", action="append", metavar="KEY=VALUE")
    sh.add_argument("-o", "--output")

    r = cmd("render", cmd_render, "burn captions into a new mp4, then verify it")
    r.add_argument("video"); r.add_argument("words")
    r.add_argument("-o", "--output", help="default: VIDEO.captioned-STYLE.mp4")
    r.add_argument("--clip", metavar="START:END", help="render only this range (seconds) as a preview")
    r.add_argument("--encoder", choices=["x264", "videotoolbox"], default="x264",
                   help="videotoolbox = Mac hardware encoder, much faster, slightly larger files")
    r.add_argument("--quality", type=int, help="x264 CRF (default 18) or videotoolbox q:v (default 65)")
    r.add_argument("--preset", default="medium", help="x264 preset")
    r.add_argument("--fps", type=float, help="output frame rate, e.g. 60 for 120fps screen recordings")
    r.add_argument("--no-verify", action="store_true")
    style_args(r)

    v = cmd("verify", cmd_verify, "check a rendered file and build a labeled frame grid to look at")
    v.add_argument("video", help="the rendered mp4 (needs its .burn.json sidecar)")
    v.add_argument("--frames", type=int, default=9)

    sk = cmd("skill", cmd_skill, "install the agent skill (SKILL.md) for Claude Code", json_flag=False)
    sk.add_argument("action", choices=["install"])
    sk.add_argument("--dir", default="~/.claude/skills")
    sk.add_argument("--link", action="store_true", help="symlink instead of copy (for development)")
    sk.add_argument("--force", action="store_true")

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except subprocess.CalledProcessError as e:
        die(f"{Path(e.cmd[0]).name} failed (exit {e.returncode})", "rerun with the same args to see ffmpeg's error above")


if __name__ == "__main__":
    main()
