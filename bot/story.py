"""Генерация сценария через Claude API."""
import json
import re

import requests

from .config import env, log

API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = """Ты — сценарист вертикальных видео для TikTok. Пишешь цепляющие короткие
истории для озвучки диктором. Правила:
- Первая фраза — крючок, который удерживает зрителя в первые 2 секунды.
- Короткие предложения, разговорный язык, без сложных слов и аббревиатур (их читает синтезатор речи).
- Никаких реальных имён живых людей, брендов и реальных преступлений. История вымышленная.
- Никакой жестокости в деталях, контент 13+.
- Концовка — неожиданный поворот или открытый вопрос, провоцирующий комментарии.
Результат передай через инструмент submit_story."""

TOOL = {
    "name": "submit_story",
    "description": "Передать готовый сценарий ролика",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "короткое название до 60 символов"},
            "hook_style": {"type": "string", "enum": ["вопрос", "шок-факт", "от первого лица", "интрига"]},
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "текст для озвучки, 1-3 предложения"},
                        "search_query": {"type": "string", "description": "2-4 English words for a vertical stock background video, no faces"},
                    },
                    "required": ["text", "search_query"],
                },
            },
            "caption": {"type": "string", "description": "подпись до 120 символов с вопросом к зрителям"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "hook_style", "scenes", "caption", "hashtags"],
    },
}

SCHEMA = """{
  "title": "короткое название, до 60 символов",
  "hook_style": "один из: вопрос | шок-факт | от первого лица | интрига",
  "scenes": [
    {"text": "фрагмент текста для озвучки (1–3 предложения)",
     "search_query": "2-4 English words describing a calm vertical stock video for background, no people faces"}
  ],
  "caption": "подпись к видео до 120 символов, с вопросом к зрителям",
  "hashtags": ["#тег1", "#тег2", "#тег3"]
}"""


def build_prompt(cfg: dict, topic: str, insights: str, recent_titles: list[str]) -> str:
    ch, st = cfg["channel"], cfg["story"]
    words = int(st["target_seconds"] * st["words_per_minute"] / 60)
    recent = "\n".join(f"- {t}" for t in recent_titles[-30:]) or "- (пока нет)"
    return f"""Ниша канала: {ch['niche'].strip()}
Язык: {ch['language']}
Тема этого ролика: {topic}
Объём текста для озвучки: СТРОГО не больше {words} слов (≈{st['target_seconds']} секунд), 5–8 сцен.

Что показывает статистика канала:
{insights or '(данных пока мало — экспериментируй с крючками)'}

Уже были ролики (не повторяй сюжеты и названия):
{recent}

Передай результат через инструмент submit_story."""


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def generate_story(cfg: dict, topic: str, insights: str, recent_titles: list[str]) -> dict:
    key = env("ANTHROPIC_API_KEY", required=True)
    prompt = build_prompt(cfg, topic, insights, recent_titles)
    last_err = None
    for attempt in range(3):
        r = requests.post(
            API_URL,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": cfg["story"]["model"],
                "max_tokens": 16000,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [TOOL],
                "tool_choice": {"type": "auto"},
            },
            timeout=120,
        )
        if r.status_code >= 400:
            last_err = f"Claude API {r.status_code}: {r.text[:300]}"
            log(last_err)
            continue
        body = r.json()
        try:
            block = next((b for b in body.get("content", []) if b.get("type") == "tool_use"), None)
            if block:
                story = block["input"]
            else:
                text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
                if "{" not in text:
                    raise ValueError(f"пустой ответ, stop_reason={body.get('stop_reason')}")
                story = _extract_json(text)
            validate(story)
            story["topic"] = topic
            return story
        except (ValueError, KeyError) as e:
            last_err = f"Невалидный ответ модели: {e}"
            log(last_err)
    raise RuntimeError(last_err)


def validate(story: dict) -> None:
    if not story.get("scenes") or len(story["scenes"]) < 2:
        raise ValueError("слишком мало сцен")
    for s in story["scenes"]:
        if not s.get("text", "").strip():
            raise ValueError("пустая сцена")
        s.setdefault("search_query", "dark forest night")
    story.setdefault("title", story["scenes"][0]["text"][:60])
    story.setdefault("caption", story["title"])
    story.setdefault("hashtags", [])
    story.setdefault("hook_style", "интрига")


def full_text(story: dict) -> str:
    return " ".join(s["text"].strip() for s in story["scenes"])


def build_caption(cfg: dict, story: dict) -> str:
    ch = cfg["channel"]
    tags = []
    for t in list(story.get("hashtags", [])) + ch.get("base_hashtags", []):
        t = "#" + t.lstrip("#").replace(" ", "")
        if t.lower() not in (x.lower() for x in tags):
            tags.append(t)
    caption = f"{story['caption'].strip()}\n{ch.get('disclaimer', '')}\n{' '.join(tags[:6])}".strip()
    return caption[:2000]
