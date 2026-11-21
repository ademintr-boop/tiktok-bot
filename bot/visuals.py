"""Фоновые ролики: Pexels (бесплатные стоки, вертикальные) или анимированный градиент."""
import random
from pathlib import Path

import requests

from .config import env, log

PALETTES = [
    ("0x1b2a4a", "0x5a2d6e"), ("0x14323f", "0x3d1f4f"), ("0x3a1620", "0x16324a"),
    ("0x123c3c", "0x2b2b5c"), ("0x2a1f4d", "0x5c2a3a"),
]


def _pick_file(video: dict) -> dict | None:
    files = [f for f in video.get("video_files", []) if f.get("height") and f.get("width")]
    portrait = [f for f in files if f["height"] > f["width"] and f["height"] >= 1280]
    pool = portrait or [f for f in files if f["height"] >= 720]
    if not pool:
        return None
    return min(pool, key=lambda f: abs(f["height"] - 1920))


def pexels_clip(query: str, dest: Path, used: set[int]) -> Path | None:
    key = env("PEXELS_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={"query": query, "orientation": "portrait", "per_page": 15, "size": "medium"},
            timeout=30,
        )
        r.raise_for_status()
        videos = [v for v in r.json().get("videos", []) if v["id"] not in used and v.get("duration", 0) >= 4]
        random.shuffle(videos)
        for v in videos:
            f = _pick_file(v)
            if not f:
                continue
            with requests.get(f["link"], stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as out:
                    for part in resp.iter_content(1 << 20):
                        out.write(part)
            used.add(v["id"])
            return dest
    except requests.RequestException as e:
        log(f"Pexels: {e}")
    return None


def gather_backgrounds(cfg: dict, story: dict, out_dir: Path) -> list[dict]:
    """Для каждой сцены: {'file': Path} или {'gradient': (c0, c1)}."""
    used: set[int] = set()
    result = []
    for i, scene in enumerate(story["scenes"]):
        clip = None
        if cfg["video"].get("background") == "pexels":
            clip = pexels_clip(scene["search_query"], out_dir / f"bg_{i}.mp4", used)
            if not clip:
                clip = pexels_clip("dark night mystery", out_dir / f"bg_{i}.mp4", used)
        result.append({"file": clip} if clip else {"gradient": random.choice(PALETTES)})
    return result
