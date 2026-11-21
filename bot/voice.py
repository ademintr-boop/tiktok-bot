"""Озвучка через edge-tts с пословными таймингами для субтитров."""
import asyncio
import json
import re
import subprocess
from pathlib import Path

from .config import log


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


async def _edge(text: str, voice: str, rate: str, pitch: str, out: Path) -> list[dict]:
    import edge_tts

    try:
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    except TypeError:  # старые версии edge-tts
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    words = []
    with open(out, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append({"word": chunk["text"], "start": start, "end": start + chunk["duration"] / 1e7})
    return words


def estimate_words(text: str, duration: float) -> list[dict]:
    """Запасной вариант: распределить слова по длительности пропорционально длине."""
    tokens = re.findall(r"\S+", text)
    weights = [len(t) + 2 for t in tokens]
    total = sum(weights) or 1
    t, words = 0.0, []
    for tok, w in zip(tokens, weights):
        d = duration * w / total
        words.append({"word": tok, "start": t, "end": t + d})
        t += d
    return words


def attach_punctuation(text: str, words: list[dict]) -> list[dict]:
    """edge-tts отдаёт слова без знаков препинания — вернём их из исходного текста."""
    tokens = re.findall(r"\S+", text)
    ti = 0
    for w in words:
        core = re.sub(r"\W", "", w["word"].lower())
        for j in range(ti, min(ti + 4, len(tokens))):
            if re.sub(r"\W", "", tokens[j].lower()) == core:
                w["word"] = tokens[j]
                ti = j + 1
                break
    return words


def synthesize(cfg: dict, text: str, out_dir: Path) -> tuple[Path, list[dict], float]:
    v = cfg["voice"]
    out = out_dir / "voice.mp3"
    words = asyncio.run(_edge(text, v["name"], v["rate"], v["pitch"], out))
    duration = audio_duration(out)
    if len(words) < len(text.split()) * 0.6:
        log("edge-tts не вернул тайминги слов — оцениваю по длине")
        words = estimate_words(text, duration)
    else:
        words = attach_punctuation(text, words)
    return out, words, duration
