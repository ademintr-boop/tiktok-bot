"""История публикаций: data/history.json хранится в репозитории и коммитится ботом."""
import json
from datetime import datetime, timezone

from .config import DATA


HISTORY = DATA / "history.json"


def load_history() -> list[dict]:
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8") or "[]")
    return []


def save_history(items: list[dict]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def posts_today(items: list[dict]) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for i in items if i.get("posted_at", "").startswith(today) and i.get("status") == "published")
