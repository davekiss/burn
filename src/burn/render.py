"""Styles, ASS subtitle generation, and the ffmpeg calls that burn them in."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

FONTS = Path(__file__).resolve().parent / "fonts"

# Sizes are fractions of min(width, height) so presets work for 16:9 and 9:16 alike.
# Colors are "#RRGGBB" or "#RRGGBBAA" (AA = opacity, ff = opaque).
STYLES: dict[str, dict] = {
    "bold": dict(
        desc="White Montserrat Black, heavy black outline, quick scale-in. The safe default.",
        font="Montserrat Black", size=0.14, color="#ffffff", outline="#000000", outline_w=0.09,
        shadow="#00000099", shadow_d=0.04, uppercase=True, anim="grow",
    ),
    "pop": dict(
        desc="Yellow Anton, black outline, bouncy overshoot. Loud, TikTok-style.",
        font="Anton", size=0.16, color="#ffe600", outline="#000000", outline_w=0.08,
        shadow="#000000aa", shadow_d=0.05, uppercase=True, anim="bounce",
    ),
    "box": dict(
        desc="White Montserrat ExtraBold on a solid black box. Readable over busy footage.",
        font="Montserrat ExtraBold", size=0.10, color="#ffffff", box="#000000dd", box_pad=0.18,
        uppercase=False, anim="grow",
    ),
    "clean": dict(
        desc="Mixed-case Montserrat ExtraBold, thin soft outline + shadow, gentle fade. Understated.",
        font="Montserrat ExtraBold", size=0.095, color="#ffffff", outline="#000000c0", outline_w=0.045,
        shadow="#000000b0", shadow_d=0.035, blur=0.03, uppercase=False, anim="fade",
    ),
    "neon": dict(
        desc="Tall Bebas Neue in white with a cyan glow. Techy.",
        font="Bebas Neue", size=0.19, color="#ffffff", outline="#101018", outline_w=0.04,
        glow="#00e5ff", glow_w=0.16, uppercase=True, anim="grow",
    ),
}
COMMON = dict(position="bottom", strip_punct=True, hold=0.35, gap_fill=0.6)
OPTIONAL_KEYS = {"y", "glow", "glow_w", "box", "box_pad", "blur", "outline", "outline_w", "shadow", "shadow_d"}
POSITIONS = {"bottom": 0.86, "lower": 0.78, "center": 0.5, "top": 0.16}
STRIP_PUNCT = ".,;:"  # trimmed from displayed words; ?!' are kept
ANIMS = {
    "none": "",
    "grow": r"\fscx82\fscy82\t(0,80,\fscx100\fscy100)",
    "bounce": r"\fscx60\fscy60\t(0,90,\fscx114\fscy114)\t(90,170,\fscx100\fscy100)",
    "fade": r"\fad(70,0)",
}
SETTLE = 0.2  # seconds until every entrance animation has finished


class StyleError(ValueError):
    pass


def resolve_style(name: str, opts: list[str] | dict | None = None) -> dict:
    if name not in STYLES:
        raise StyleError(f"unknown style {name!r}; choose from {', '.join(STYLES)}")
    style = {**STYLES[name], **COMMON}
    items = opts.items() if isinstance(opts, dict) else (kv.partition("=")[::2] for kv in opts or [])
    for key, val in items:
        if key not in style and key not in OPTIONAL_KEYS:
            raise StyleError(f"unknown option {key!r}; valid: {', '.join(sorted(set(style) | OPTIONAL_KEYS))}")
        cur = style.get(key)
        if isinstance(val, str) and isinstance(cur, bool):
            val = val.lower() in {"1", "true", "yes", "on"}
        elif isinstance(val, str) and (isinstance(cur, (int, float)) or key in {"y", "glow_w", "box_pad",
                                                                                 "blur", "outline_w", "shadow_d"}):
            val = float(val)
        style[key] = val
    if style.get("y") is None and style["position"] not in POSITIONS:
        raise StyleError(f"unknown position {style['position']!r}; choose from {', '.join(POSITIONS)} or y=0.0-1.0")
    return style


def display_text(text: str, s: dict) -> str:
    if s["strip_punct"]:
        text = text.strip(STRIP_PUNCT) or text
    return text.upper() if s["uppercase"] else text


def timeline(words: list, s: dict) -> list[tuple[float, float, str]]:
    """(start, end, displayed text) for each word as it will appear on screen."""
    out = []
    for k, w in enumerate(words):
        start = w["start"]
        end = max(w["end"], start + 0.08)
        nxt = words[k + 1]["start"] if k + 1 < len(words) else None
        if nxt is not None and nxt - end < s["gap_fill"]:
            end = nxt  # no flicker between closely spoken words
        else:
            end = min(end + s["hold"], nxt) if nxt is not None else end + s["hold"]
        if end > start:
            out.append((start, end, display_text(w["text"], s)))
    return out


def ass_color(hexstr: str) -> str:
    """'#RRGGBB[AA]' -> '&HAABBGGRR' (ASS alpha is inverted: 00 = opaque)."""
    h = hexstr.lstrip("#")
    alpha = 255 - int(h[6:8], 16) if len(h) == 8 else 0
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_override_color(hexstr: str) -> str:
    """'#RRGGBB' -> '&HBBGGRR&' for inline \\c tags."""
    h = hexstr.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def ts(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def build_ass(words: list, s: dict, width: int, height: int, offset: float = 0.0) -> str:
    size = round(s["size"] * min(width, height))
    y = s.get("y") or POSITIONS[s["position"]]
    an = 5 if 0.3 < y < 0.7 else 2
    pos = rf"\an{an}\pos({width // 2},{round(y * height)})"

    if "box" in s:
        border_style, outline_c, outline_w, shadow_w = 3, ass_color(s["box"]), s.get("box_pad", 0.18) * size, 0
    else:
        border_style, outline_c = 1, ass_color(s.get("outline", "#000000"))
        outline_w, shadow_w = s.get("outline_w", 0) * size, s.get("shadow_d", 0) * size
    back = ass_color(s.get("shadow", "#00000080"))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,{s['font']},{size},{ass_color(s['color'])},&H000000FF,{outline_c},{back},0,0,0,0,100,100,0,0,{border_style},{outline_w:.1f},{shadow_w:.1f},{an},0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    anim = ANIMS.get(s["anim"], s["anim"])  # unknown names pass through as raw ASS tags
    blur = rf"\blur{s['blur'] * size:.1f}" if s.get("blur") else ""
    events = []
    for start, end, text in timeline(words, s):
        start, end = start + offset, end + offset
        if end <= 0:
            continue
        if "glow" in s:
            gw = s.get("glow_w", 0.16) * size
            events.append(f"Dialogue: 0,{ts(start)},{ts(end)},Word,,0,0,0,,"
                          rf"{{{pos}{anim}\bord{gw:.1f}\blur{gw * 0.8:.1f}\3c{ass_override_color(s['glow'])}\shad0}}"
                          f"{ass_escape(text)}")
        events.append(f"Dialogue: 1,{ts(start)},{ts(end)},Word,,0,0,0,,{{{pos}{anim}{blur}}}{ass_escape(text)}")
    return header + "\n".join(events) + "\n"


# --------------------------------------------------------------------------- ffmpeg
def probe(video) -> dict:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,width,height:stream_side_data=rotation:format=duration", "-of", "json", str(video)])
    info = json.loads(out)
    v = next(s for s in info["streams"] if s.get("codec_type") == "video")
    w, h = v["width"], v["height"]
    rot = next((abs(int(sd.get("rotation", 0))) for sd in v.get("side_data_list", [])), 0)
    if rot in (90, 270):
        w, h = h, w
    return dict(width=w, height=h, duration=float(info["format"].get("duration", 0)),
                audio=any(s.get("codec_type") == "audio" for s in info["streams"]))


def burn(video, ass_text, out, ss=None, duration=None, frames=None, extra=(), copyts=False, quiet=False):
    """Run ffmpeg with the ass filter. The ASS file lives in a temp dir so no filter-path escaping is needed.

    copyts keeps source timestamps after an input seek, so captions line up without shifting
    (shifting would restart entrance animations at the seek point).
    """
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "c.ass").write_text(ass_text)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + ([] if quiet else ["-stats"])
        if ss is not None:
            cmd += ["-ss", f"{ss:.3f}"] + (["-copyts"] if copyts else [])
        if duration is not None:
            cmd += ["-t", f"{duration:.3f}"]
        cmd += ["-i", str(Path(video).resolve()), "-vf", f"ass=c.ass:fontsdir={FONTS}"]
        if frames:
            cmd += ["-frames:v", str(frames), "-update", "1"]
        cmd += [*extra, str(Path(out).resolve())]
        subprocess.run(cmd, check=True, cwd=tmp)


def grab(video, t: float, out) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "quiet", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                    "-frames:v", "1", "-update", "1", str(out)], check=True)


def tile(images: list[tuple[str, Path]], out, cols: int = 2, tile_w: int = 960) -> Path:
    """Scale, label (top-left) and grid a list of (label, png) into one PNG."""
    first = probe(images[0][1])
    th = round(tile_w * first["height"] / first["width"] / 2) * 2
    font = FONTS / "Montserrat-ExtraBold.ttf"
    inputs, chains = [], []
    for k, (label, img) in enumerate(images):
        inputs += ["-i", str(img)]
        safe = label.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:").replace("%", "\\%")
        chains.append(f"[{k}:v]scale={tile_w}:{th},drawtext=fontfile={font}:text='{safe}':x=16:y=14:"
                      f"fontsize={max(18, tile_w // 30)}:fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=8[t{k}]")
    n = len(images)
    if n % cols:
        pad = cols - n % cols
        for k in range(n, n + pad):
            chains.append(f"color=c=0x111111:s={tile_w}x{th}:d=1[t{k}]")
        n += pad
    if n == 1:
        graph = ";".join(chains) + ";[t0]null"
    else:
        layout = "|".join(f"{(k % cols) * tile_w}_{(k // cols) * th}" for k in range(n))
        graph = ";".join(chains) + ";" + "".join(f"[t{k}]" for k in range(n)) + f"xstack=inputs={n}:layout={layout}"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", graph,
                    "-frames:v", "1", "-update", "1", str(out)], check=True)
    return Path(out)
