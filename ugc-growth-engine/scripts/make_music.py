"""Synthesize three royalty-free background loops (low/medium/high energy) into assets/music/.

Generated from scratch with ffmpeg's aevalsrc (sine chords + simple percussion), so there are no
licensing questions. Drop your own royalty-free tracks named `<energy>_*.mp3` there to use them instead.
Usage: python scripts/make_music.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.settings import MUSIC_DIR, ffmpeg_bin  # noqa: E402

CHORDS = [(261.63, 329.63, 392.00), (196.00, 246.94, 293.66), (220.00, 261.63, 329.63), (174.61, 220.00, 261.63)]


def chord_expr(bar: float) -> str:
    def sines(c: tuple[float, ...]) -> str:
        return "+".join(f"sin(2*PI*{f}*t)+0.3*sin(4*PI*{f}*t)" for f in c)
    parts = [f"({sines(c)})" for c in CHORDS]
    cycle = bar * 4
    return (f"if(lt(mod(t,{cycle}),{bar}),{parts[0]},if(lt(mod(t,{cycle}),{2 * bar}),{parts[1]},"
            f"if(lt(mod(t,{cycle}),{3 * bar}),{parts[2]},{parts[3]})))")


def track(name: str, beat: float, kick: bool, hat: bool, pad_gain: float) -> None:
    bar = beat * 4
    expr = f"{pad_gain}*({chord_expr(bar)})*(0.75+0.25*sin(2*PI*t/{beat}))"
    if kick:
        expr += f"+0.55*sin(2*PI*55*mod(t,{beat}))*exp(-14*mod(t,{beat}))"
    if hat:
        expr += f"+0.08*(random(0)*2-1)*exp(-45*mod(t+{beat / 2},{beat}))"
    out = MUSIC_DIR / f"{name}.mp3"
    subprocess.run(
        [ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"aevalsrc=exprs='{expr}':s=44100:d={bar * 8}",
         "-af", "lowpass=f=3500,aecho=0.6:0.5:60:0.25,afade=t=in:d=0.5,loudnorm=I=-16:TP=-1.5:LRA=11",
         "-ac", "2", "-ar", "44100", "-b:a", "128k", str(out)],
        check=True, timeout=180,
    )
    print(f"wrote {out}")


def main() -> None:
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    track("low_calm_pad", beat=0.857, kick=False, hat=False, pad_gain=0.05)
    track("medium_soft_groove", beat=0.6, kick=True, hat=False, pad_gain=0.045)
    track("high_upbeat", beat=0.5, kick=True, hat=True, pad_gain=0.04)


if __name__ == "__main__":
    main()
