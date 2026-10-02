"""End-to-end through ffmpeg + libass on a synthetic black video. Skipped without ffmpeg."""
import json
import shutil
import subprocess

import pytest

from burn.cli import main
from burn.words import save

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


@pytest.fixture
def clip(tmp_path):
    video = tmp_path / "black.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=black:s=320x240:r=30:d=4",
                    "-f", "lavfi", "-i", "sine=d=4", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(video)], check=True)
    words = tmp_path / "black.words.json"
    save(words, dict(source=str(video), reviewed=True, words=[
        dict(text="HELLO", start=1.0, end=1.6, p=1.0), dict(text="world", start=3.0, end=3.4, p=1.0)]))
    return video, words


def bright_pixels(png) -> int:
    raw = subprocess.check_output(["ffmpeg", "-loglevel", "error", "-i", str(png), "-f", "rawvideo",
                                   "-pix_fmt", "gray", "-"])
    return sum(1 for b in raw if b > 200)


def test_still_shows_caption_only_while_word_is_on_screen(clip, tmp_path, capsys):
    video, words = clip
    main(["still", str(video), str(words), "-t", "1.3", "-t", "2.5", "--outdir", str(tmp_path / "s")])
    on, off = capsys.readouterr().out.split()
    assert bright_pixels(on) > 300
    assert bright_pixels(off) == 0


def test_still_near_word_end_is_fully_faded_in(clip, tmp_path, capsys):
    # regression: stills used to shift caption times to the seek point, restarting the fade-in there
    video, words = clip
    main(["still", str(video), str(words), "-t", "1.9", "-t", "1.3", "--style", "clean",
          "--outdir", str(tmp_path / "s")])
    late, mid = capsys.readouterr().out.split()
    assert bright_pixels(late) > 0.8 * bright_pixels(mid) > 0


def test_render_clip_then_verify(clip, tmp_path, capsys):
    video, words = clip
    out = tmp_path / "out.mp4"
    main(["render", str(video), str(words), "-o", str(out), "--clip", "0.5:2.5", "--preset", "ultrafast", "--json"])
    report = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert report["ok"] is True
    assert report["duration"][1] == 2.0
    assert (tmp_path / "out.ass").exists() and (tmp_path / "out.burn.json").exists()


def test_verify_fails_when_output_is_truncated(clip, tmp_path):
    video, words = clip
    out = tmp_path / "out.mp4"
    main(["render", str(video), str(words), "-o", str(out), "--preset", "ultrafast", "--no-verify"])
    short = tmp_path / "short.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(out), "-t", "2", "-c", "copy", str(short)], check=True)
    (tmp_path / "short.burn.json").write_text((tmp_path / "out.burn.json").read_text())
    with pytest.raises(SystemExit) as e:
        main(["verify", str(short)])
    assert e.value.code == 1
