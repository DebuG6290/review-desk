#!/usr/bin/env python3
"""Cut the demo video: title cards + the recorded run (waiting parts sped up,
with the speed on screen) + screenshots of the bambi run + closing card.

  python demo/make_video.py [--extra shot1.png --extra shot2.png ...]
Writes demo/review-desk-demo.mp4 (H.264, 1280x720, 30 fps).
"""

import argparse
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW, CARDS, PARTS = HERE / "raw", HERE / "cards", HERE / "raw" / "parts"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p", "-r", "30", "-an"]


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def label(text):
    text = text.replace(":", r"\:").replace("'", "")
    return (f"drawtext=fontfile={FONT}:text='{text}':x=(w-tw)/2:y=h-60:fontsize=26:fontcolor=white:"
            "box=1:boxcolor=black@0.65:boxborderw=12")


def still(png, seconds, out, caption=None):
    vf = ("scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:0x0f172a,"
          "format=yuv420p" + ("," + label(caption) if caption else ""))
    run(["ffmpeg", "-y", "-loop", "1", "-t", str(seconds), "-i", str(png), "-vf", vf, *ENC, str(out)])


def clip(video, start, end, speed, out, caption=None):
    vf = f"setpts=PTS/{speed},fps=30,scale=1280:720" + ("," + label(caption) if caption else "")
    run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(video), "-vf", vf, *ENC, str(out)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extra", action="append", default=[], help="screenshot:caption shown after the bambi card")
    a = ap.parse_args()
    t = json.loads((RAW / "timeline.json").read_text())
    video = t["video"]
    PARTS.mkdir(parents=True, exist_ok=True)
    parts = []

    def add(name):
        parts.append(PARTS / name)
        return PARTS / name

    still(CARDS / "01-title.png", 5, add("a.mp4"))
    still(CARDS / "02-how.png", 10, add("b.mp4"))
    still(CARDS / "03-live.png", 4, add("c.mp4"))
    s1 = t["pr_link_sent"] + 4
    s0 = max(t["room_open"] - 1, 0)
    sp0 = max(1, round((s1 - s0) / 14))
    clip(video, s0, s1, sp0, add("d.mp4"),
         "A person drops the PR link for @fixer" + (f" ({sp0}x)" if sp0 > 1 else ""))
    work = t["checker_asks_approval"] - 1 - s1
    sp = max(1, round(work / 38))
    clip(video, s1, t["checker_asks_approval"] - 1, sp, add("e.mp4"),
         f"Fixer patches + hands off, Checker tests (real run, {sp}x speed)")
    clip(video, t["checker_asks_approval"] - 1, t["approval_sent"] + 3, 1, add("f.mp4"),
         "Checker: checks passed, reply drafted - waiting for a human")
    pub = t["published"] - (t["approval_sent"] + 3)
    sp2 = max(1, round(pub / 8))
    clip(video, t["approval_sent"] + 3, t["published"], sp2, add("g.mp4"),
         f"Approved in the room - rd publish verifies it ({sp2}x)")
    clip(video, t["published"], t["end"], 1, add("h.mp4"), "Pushed to the sandbox remote, reply filed")
    still(CARDS / "04-bambi.png", 6, add("i.mp4"))
    for n, spec in enumerate(a.extra):
        png, _, cap = spec.partition(":")
        still(png, 7, add(f"j{n}.mp4"), cap or None)
    still(CARDS / "05-end.png", 7, add("k.mp4"))
    lst = PARTS / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    out = HERE / "review-desk-demo.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout.strip()
    print(out, f"{float(dur):.1f}s", f"{out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
