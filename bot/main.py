"""Точка входа.

  python -m bot.main run            # сгенерировать ролик и опубликовать
  python -m bot.main analytics      # обновить статистику и отчёт
  python -m bot.main auth-url       # ссылка для авторизации TikTok
  python -m bot.main auth-exchange CODE
  python -m bot.main demo           # демо-ролик без ключей и сети
"""
import json
import os
import sys
import traceback
from datetime import datetime, timezone

from . import analytics, render, story as story_mod, subtitles, visuals, voice
from .config import OUT, env, load_config, log
from .state import load_history, now_iso, posts_today, save_history

DEMO_STORY = {
    "title": "Мне позвонили с моего же номера",
    "hook_style": "от первого лица",
    "topic": "звонки и сообщения с неизвестных номеров",
    "scenes": [
        {"text": "В три часа ночи мне позвонили. На экране был мой собственный номер.", "search_query": "phone night"},
        {"text": "Я подумал, что это сбой оператора, и сбросил вызов.", "search_query": "dark room"},
        {"text": "Через минуту пришло сообщение: «Не открывай дверь. Это не я».", "search_query": "door dark"},
        {"text": "В ту же секунду в дверь постучали. Три раза. Медленно.", "search_query": "hallway night"},
        {"text": "А потом из-за двери раздался мой голос: «Открой, я забыл ключи».", "search_query": "shadow corridor"},
        {"text": "Я до сих пор не знаю, кто из нас настоящий. А вы бы открыли?", "search_query": "fog city night"},
    ],
    "caption": "А вы бы открыли дверь? Пишите в комментариях 👇",
    "hashtags": ["#страшилки", "#мистика"],
}


def summary(md: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")


def make_video(cfg: dict, story: dict, out_dir, demo: bool = False):
    text = story_mod.full_text(story)
    if demo:
        duration = len(text.split()) / cfg["story"]["words_per_minute"] * 60
        words = voice.estimate_words(text, duration)
        audio = None
        backgrounds = [{"gradient": visuals.PALETTES[i % len(visuals.PALETTES)]} for i in range(len(story["scenes"]))]
    else:
        log("Озвучка…")
        audio, words, duration = voice.synthesize(cfg, text, out_dir)
        log("Фоновые ролики…")
        backgrounds = visuals.gather_backgrounds(cfg, story, out_dir)
    ass = subtitles.build_ass(cfg, words, story["title"], duration, out_dir / "subs.ass")
    subtitles.build_srt(words, out_dir / "subs.srt")
    log("Рендер MP4…")
    return render.render_video(cfg, story, backgrounds, audio, words, duration, ass, out_dir), duration


def cmd_run() -> int:
    cfg = load_config()
    history = load_history()
    if posts_today(history) >= cfg["publish"]["max_posts_per_day"]:
        log("Дневной лимит публикаций достигнут — пропускаю")
        return 0

    from . import tiktok as _tt
    publish = cfg["publish"]["enabled"] and bool(_tt.load_refresh_token())
    token = None
    if publish:
        from . import tiktok
        token = tiktok.access_token()
        try:
            analytics.sync(token)
            history = load_history()
        except Exception as e:  # статистика не должна мешать публикации
            log(f"Не удалось обновить статистику: {e}")

    topic = analytics.choose_topic(cfg)
    log(f"Тема: {topic}")
    story = story_mod.generate_story(cfg, topic, analytics.insights(history), [h["title"] for h in history])
    log(f"Сценарий: {story['title']}")

    out_dir = OUT / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "story.json").write_text(json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8")
    video, duration = make_video(cfg, story, out_dir)
    caption = story_mod.build_caption(cfg, story)
    (out_dir / "caption.txt").write_text(caption, encoding="utf-8")
    log(f"Готово: {video} ({video.stat().st_size / 1e6:.1f} МБ, {duration:.0f} с)")

    record = {"created_at": now_iso(), "title": story["title"], "topic": topic,
              "hook_style": story.get("hook_style"), "caption": caption, "duration": round(duration, 1)}
    code = 0
    if publish:
        from . import tiktok
        try:
            res = tiktok.publish_video(token, video, caption, cfg)
            record.update(res)
            record["posted_at"] = now_iso()
            if res["status"] == "processing":  # TikTok ещё обрабатывает — считаем опубликованным
                record["status"] = "published"
        except Exception as e:
            record.update(status="failed", reason=str(e)[:500])
            traceback.print_exc()
            code = 1
    else:
        record["status"] = "dry_run"
        log("Публикация выключена или нет TIKTOK_REFRESH_TOKEN — только рендер")

    history.append(record)
    save_history(history)
    analytics.write_report()
    summary(f"### 🎬 {story['title']}\nТема: {topic} · {duration:.0f} c · статус: **{record['status']}**"
            f"{' (' + record.get('privacy', '') + ')' if record.get('privacy') else ''}\n\n```\n{caption}\n```")
    return code


def cmd_analytics() -> int:
    from . import tiktok
    token = tiktok.access_token()
    user = analytics.sync(token)
    summary(analytics.write_report(user))
    return 0


def cmd_demo() -> int:
    cfg = load_config()
    out_dir = OUT / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    video, d = make_video(cfg, DEMO_STORY, out_dir, demo=True)
    log(f"Демо: {video} ({d:.0f} c)")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        return cmd_run()
    if cmd == "analytics":
        return cmd_analytics()
    if cmd == "demo":
        return cmd_demo()
    if cmd == "auth-url":
        from . import tiktok
        url = tiktok.auth_url()
        log(url)
        summary(f"### Шаг 1. Откройте ссылку и разрешите доступ\n\n[Авторизовать TikTok]({url})\n\n"
                "Затем скопируйте код со страницы-приёмника и запустите этот workflow ещё раз, вставив код.")
        return 0
    if cmd == "auth-revoke":
        from . import tiktok
        tiktok.revoke()
        return 0
    if cmd == "auth-exchange":
        from . import tiktok
        tok = tiktok.exchange_code(sys.argv[2])
        ok = "Готово: бот подключён к TikTok"
        log(ok)
        summary(f"### {ok}\nСрок действия refresh-токена: {int(tok.get('refresh_expires_in', 0)) // 86400} дней. "
                "Если TikTok выдаёт новый токен, бот сохраняет его сам; по истечении срока запустите авторизацию заново.")
        return 0
    log(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
