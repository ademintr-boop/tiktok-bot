"""Сборка вертикального MP4 1080x1920 через ffmpeg."""
import random
import subprocess
from pathlib import Path

from .config import ROOT, log


def _run(cmd: list[str], cwd: Path) -> None:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg error:\n{res.stderr[-2000:]}")


def scene_durations(story: dict, words: list[dict], duration: float) -> list[float]:
    """Длительность каждой сцены по моменту, когда в озвучке начинается её первое слово."""
    counts = [len(s["text"].split()) for s in story["scenes"]]
    bounds, idx = [0.0], 0
    for c in counts[:-1]:
        idx += c
        bounds.append(words[min(idx, len(words) - 1)]["start"] if words else 0)
    bounds.append(duration)
    durs = [max(0.8, b - a) for a, b in zip(bounds, bounds[1:])]
    scale = duration / sum(durs)
    return [d * scale for d in durs]


def render_segment(bg: dict, dur: float, idx: int, cfg: dict, out_dir: Path) -> str:
    v = cfg["video"]
    W, H, fps = v["width"], v["height"], v["fps"]
    name = f"seg_{idx}.mp4"
    look = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1"
        + (",eq=brightness=-0.10:saturation=0.8" if "file" in bg else "")
        + ",vignette=PI/5,"
        + f"fade=t=in:st=0:d=0.25,fade=t=out:st={max(0, dur - 0.25):.3f}:d=0.25,format=yuv420p"
    )
    if "file" in bg:
        src = ["-stream_loop", "-1", "-i", str(bg["file"].name)]
    else:
        c0, c1 = bg["gradient"]
        src = ["-f", "lavfi", "-i",
               f"gradients=s={W}x{H}:c0={c0}:c1={c1}:x0=0:y0=0:x1={W}:y1={H}:speed=0.012:r={fps}:seed={random.randint(1, 9999)}"]
    _run(["ffmpeg", "-y", *src, "-t", f"{dur:.3f}", "-an", "-vf", look,
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", name], out_dir)
    return name


def pick_music() -> Path | None:
    tracks = [p for p in (ROOT / "assets" / "music").glob("*") if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".ogg"}]
    return random.choice(tracks) if tracks else None


def render_video(cfg: dict, story: dict, backgrounds: list[dict], voice: Path | None,
                 words: list[dict], duration: float, ass: Path, out_dir: Path) -> Path:
    tail = 0.8
    total = duration + tail
    durs = scene_durations(story, words, duration)
    durs[-1] += tail
    segs = []
    for i, (bg, d) in enumerate(zip(backgrounds, durs)):
        log(f"  сцена {i + 1}/{len(durs)}: {d:.1f} c")
        segs.append(render_segment(bg, d, i, cfg, out_dir))
    (out_dir / "segments.txt").write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "segments.txt", "-c", "copy", "bg.mp4"], out_dir)

    inputs = ["-i", "bg.mp4"]
    if voice:
        inputs += ["-i", voice.name]
    else:
        inputs += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    music = pick_music()
    afilter = "[1:a]apad,atrim=0:{t:.3f},volume=1.0[voice]".format(t=total)
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        vol = cfg["video"].get("music_volume", 0.12)
        afilter += (f";[2:a]atrim=0:{total:.3f},volume={vol},afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5[m]"
                    f";[voice][m]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        afilter += ";[voice]anull[aout]"

    final = "video.mp4"
    _run(["ffmpeg", "-y", *inputs,
          "-filter_complex", f"[0:v]ass={ass.name}[v];{afilter}",
          "-map", "[v]", "-map", "[aout]", "-t", f"{total:.3f}",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
          "-r", str(cfg["video"]["fps"]),
          "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", final], out_dir)
    return out_dir / final
