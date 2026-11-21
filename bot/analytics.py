"""Сбор статистики, выводы для сценариста и выбор следующей темы."""
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone

from .config import DATA, REPORTS, log
from .state import load_history, now_iso, save_history


def _age_hours(item: dict) -> float:
    try:
        t = datetime.fromisoformat(item["posted_at"])
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600
    except (KeyError, ValueError):
        return 0


def sync(token: str) -> dict:
    """Подтягивает просмотры/лайки по всем видео и сохраняет в history.json."""
    from . import tiktok

    videos = tiktok.list_videos(token)
    by_id = {str(v["id"]): v for v in videos}
    history = load_history()
    for item in history:
        if item.get("status") != "published":
            continue
        v = by_id.get(str(item.get("video_id") or ""))
        if not v:  # запасной вариант: поиск по началу подписи
            key = (item.get("caption") or "")[:40].strip().lower()
            v = next((x for x in videos if key and key in (x.get("video_description") or x.get("title") or "").lower()), None)
            if v:
                item["video_id"] = str(v["id"])
        if v:
            item["stats"] = {k: v.get(k, 0) for k in ("view_count", "like_count", "comment_count", "share_count")}
            item["share_url"] = v.get("share_url")
            item["stats_updated"] = now_iso()
    save_history(history)

    user = tiktok.user_stats(token)
    snap_file = DATA / "account_daily.json"
    snaps = json.loads(snap_file.read_text(encoding="utf-8")) if snap_file.exists() else []
    today = datetime.now(timezone.utc).date().isoformat()
    snaps = [s for s in snaps if s["date"] != today] + [{"date": today, **user}]
    snap_file.write_text(json.dumps(snaps, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Статистика обновлена: {len(videos)} видео, подписчиков {user.get('follower_count')}")
    return user


def _mature(history: list[dict]) -> list[dict]:
    """Видео старше 12 часов со статистикой — их уже честно сравнивать."""
    return [h for h in history if h.get("stats") and _age_hours(h) >= 12]


def _score(h: dict) -> float:
    s = h["stats"]
    views = s.get("view_count", 0)
    eng = (s.get("like_count", 0) + 2 * s.get("comment_count", 0) + 3 * s.get("share_count", 0)) / max(views, 1)
    return math.log1p(views) * (1 + min(eng, 0.5))


def group_stats(history: list[dict], key: str) -> list[tuple[str, int, float, float]]:
    groups = defaultdict(list)
    for h in _mature(history):
        groups[h.get(key) or "—"].append(h)
    rows = []
    for name, items in groups.items():
        views = sum(i["stats"].get("view_count", 0) for i in items) / len(items)
        rows.append((name, len(items), views, sum(_score(i) for i in items) / len(items)))
    return sorted(rows, key=lambda r: r[3], reverse=True)


def choose_topic(cfg: dict) -> str:
    topics = cfg["channel"]["topics"]
    history = load_history()
    stats = {name: score for name, _, _, score in group_stats(history, "topic")}
    recent = [h.get("topic") for h in history[-2:]]
    candidates = [t for t in topics if t not in recent] or topics
    untried = [t for t in candidates if t not in stats]
    if untried and random.random() < 0.7:
        return random.choice(untried)
    if random.random() < cfg["channel"].get("explore_rate", 0.3):
        return random.choice(candidates)
    return max(candidates, key=lambda t: stats.get(t, 0))


def insights(history: list[dict]) -> str:
    mature = _mature(history)
    if len(mature) < 3:
        return ""
    lines = []
    topics = group_stats(history, "topic")
    lines.append("Лучшие темы (средние просмотры): " +
                 "; ".join(f"{n} — {int(v)}" for n, _, v, _ in topics[:3]))
    hooks = group_stats(history, "hook_style")
    if hooks:
        lines.append("Крючки по эффективности: " + " > ".join(n for n, *_ in hooks))
    ranked = sorted(mature, key=_score, reverse=True)
    lines.append("Самые удачные ролики: " + "; ".join(
        f"«{h['title']}» ({h['stats']['view_count']} просм.)" for h in ranked[:3]))
    lines.append("Слабые ролики (не повторяй их подход): " + "; ".join(
        f"«{h['title']}» ({h['stats']['view_count']} просм.)" for h in ranked[-2:]))
    return "\n".join(lines)


def best_hours(history: list[dict]) -> list[tuple[int, float]]:
    buckets = defaultdict(list)
    for h in _mature(history):
        hour = datetime.fromisoformat(h["posted_at"]).hour
        buckets[hour].append(h["stats"].get("view_count", 0))
    return sorted(((hr, sum(v) / len(v)) for hr, v in buckets.items()), key=lambda x: -x[1])


def write_report(user: dict | None = None) -> str:
    history = load_history()
    published = [h for h in history if h.get("status") == "published"]
    total_views = sum(h.get("stats", {}).get("view_count", 0) for h in published)
    md = [f"# Отчёт ИИ-блогера — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", ""]
    if user:
        md += [f"**Аккаунт:** {user.get('display_name', '')} · подписчики **{user.get('follower_count', 0)}** · "
               f"лайки {user.get('likes_count', 0)} · видео {user.get('video_count', 0)}", ""]
    md += [f"Опубликовано ботом: **{len(published)}**, суммарно просмотров: **{total_views}**", ""]

    md += ["## Темы", "| Тема | Роликов | Ср. просмотры |", "|---|---|---|"]
    md += [f"| {n} | {c} | {int(v)} |" for n, c, v, _ in group_stats(history, "topic")]
    md += ["", "## Крючки", "| Стиль | Роликов | Ср. просмотры |", "|---|---|---|"]
    md += [f"| {n} | {c} | {int(v)} |" for n, c, v, _ in group_stats(history, "hook_style")]
    hours = best_hours(history)
    if hours:
        md += ["", "## Лучшее время публикации (UTC)", ", ".join(f"{h:02d}:00 — {int(v)}" for h, v in hours[:3])]
    md += ["", "## Последние ролики", "| Дата | Название | Просм. | Лайки | Комм. | Репосты |", "|---|---|---|---|---|---|"]
    for h in reversed(published[-20:]):
        s = h.get("stats", {})
        title = f"[{h['title']}]({h['share_url']})" if h.get("share_url") else h["title"]
        md.append(f"| {h['posted_at'][:16]} | {title} | {s.get('view_count', '—')} | {s.get('like_count', '—')} | "
                  f"{s.get('comment_count', '—')} | {s.get('share_count', '—')} |")
    failed = [h for h in history if h.get("status") not in ("published", "dry_run")][-5:]
    if failed:
        md += ["", "## Ошибки", *[f"- {h.get('created_at', '')}: {h.get('status')} — {h.get('reason', '')}" for h in failed]]
    text = "\n".join(md) + "\n"
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "latest.md").write_text(text, encoding="utf-8")
    return text
