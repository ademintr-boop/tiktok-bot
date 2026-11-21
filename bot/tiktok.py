"""Официальные TikTok API: OAuth, Content Posting API (Direct Post), Display API (статистика)."""
import os
import subprocess
import time
import urllib.parse
from pathlib import Path

import requests

from .config import env, log, mask

API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,user.info.stats,video.list,video.publish"


class TikTokError(RuntimeError):
    pass


def _check(r: requests.Response) -> dict:
    try:
        body = r.json()
    except ValueError:
        raise TikTokError(f"HTTP {r.status_code}: {r.text[:300]}")
    err = body.get("error") or {}
    if r.status_code >= 400 or (err.get("code") not in (None, "", "ok")):
        raise TikTokError(f"{err.get('code')}: {err.get('message')} (log_id={err.get('log_id')}, HTTP {r.status_code})")
    return body.get("data", body)


# ---------- OAuth ----------

def auth_url() -> str:
    params = {
        "client_key": env("TIKTOK_CLIENT_KEY", required=True),
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": env("TIKTOK_REDIRECT_URI", required=True),
        "state": os.urandom(8).hex(),
    }
    return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(params)


def _token_request(data: dict) -> dict:
    r = requests.post(
        f"{API}/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
        data={"client_key": env("TIKTOK_CLIENT_KEY", required=True),
              "client_secret": env("TIKTOK_CLIENT_SECRET", required=True), **data},
        timeout=30,
    )
    body = r.json()
    if "access_token" not in body:
        raise TikTokError(f"OAuth: {body.get('error')} {body.get('error_description')}")
    mask(body["access_token"])
    mask(body.get("refresh_token"))
    return body


def exchange_code(code: str) -> dict:
    code = urllib.parse.unquote(code.strip())
    tok = _token_request({"code": code, "grant_type": "authorization_code",
                          "redirect_uri": env("TIKTOK_REDIRECT_URI", required=True)})
    store_refresh_token(tok["refresh_token"])
    return tok


def store_refresh_token(token: str) -> None:
    """Сохраняет refresh-токен в секрет репозитория (нужен GH_PAT с правом Secrets: write)."""
    pat, repo = env("GH_PAT"), env("GITHUB_REPOSITORY")
    if not (pat and repo):
        log("GH_PAT не задан — не могу обновить секрет TIKTOK_REFRESH_TOKEN автоматически")
        return
    subprocess.run(["gh", "secret", "set", "TIKTOK_REFRESH_TOKEN", "--repo", repo],
                   input=token, text=True, check=True, env={**os.environ, "GH_TOKEN": pat},
                   capture_output=True)
    log("Секрет TIKTOK_REFRESH_TOKEN обновлён")


def access_token() -> str:
    refresh = env("TIKTOK_REFRESH_TOKEN", required=True)
    tok = _token_request({"grant_type": "refresh_token", "refresh_token": refresh})
    if tok.get("refresh_token") and tok["refresh_token"] != refresh:
        store_refresh_token(tok["refresh_token"])
    return tok["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}


# ---------- Публикация ----------

def creator_info(token: str) -> dict:
    return _check(requests.post(f"{API}/post/publish/creator_info/query/", headers=_headers(token), timeout=30))


def publish_video(token: str, video: Path, caption: str, cfg: dict) -> dict:
    p = cfg["publish"]
    info = creator_info(token)
    options = info.get("privacy_level_options", [])
    privacy = p["privacy_level"] if p["privacy_level"] in options else "SELF_ONLY"
    if privacy != p["privacy_level"]:
        log(f"Уровень {p['privacy_level']} недоступен ({options}) — публикую как {privacy}")

    size = video.stat().st_size
    if size <= 64 * 1024 * 1024:
        chunk, count = size, 1
    else:
        chunk = 10 * 1024 * 1024
        count = size // chunk  # последний кусок забирает остаток

    body = {
        "post_info": {
            "title": caption,
            "privacy_level": privacy,
            "disable_comment": p["disable_comment"] or info.get("comment_disabled", False),
            "disable_duet": p["disable_duet"] or info.get("duet_disabled", False),
            "disable_stitch": p["disable_stitch"] or info.get("stitch_disabled", False),
            "video_cover_timestamp_ms": 1500,
            "is_aigc": bool(p.get("is_aigc", True)),
        },
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count},
    }
    data = _check(requests.post(f"{API}/post/publish/video/init/", headers=_headers(token), json=body, timeout=60))
    publish_id, upload_url = data["publish_id"], data["upload_url"]

    with open(video, "rb") as f:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            f.seek(start)
            payload = f.read(end - start + 1)
            r = requests.put(upload_url, data=payload, timeout=300, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(payload)),
                "Content-Range": f"bytes {start}-{end}/{size}"})
            if r.status_code not in (200, 201, 206):
                raise TikTokError(f"Загрузка куска {i + 1}/{count}: HTTP {r.status_code} {r.text[:200]}")
    log(f"Видео загружено, publish_id={publish_id}")
    return {"publish_id": publish_id, "privacy": privacy, **wait_status(token, publish_id)}


def wait_status(token: str, publish_id: str, timeout_s: int = 600) -> dict:
    deadline = time.time() + timeout_s
    delay = 5
    while time.time() < deadline:
        d = _check(requests.post(f"{API}/post/publish/status/fetch/", headers=_headers(token),
                                 json={"publish_id": publish_id}, timeout=30))
        status = d.get("status")
        if status == "PUBLISH_COMPLETE":
            ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
            return {"status": "published", "video_id": str(ids[0]) if ids else None}
        if status == "FAILED":
            return {"status": "failed", "reason": d.get("fail_reason")}
        time.sleep(delay)
        delay = min(delay * 1.5, 30)
    return {"status": "processing"}


# ---------- Статистика ----------

def user_stats(token: str) -> dict:
    fields = "display_name,follower_count,following_count,likes_count,video_count"
    r = requests.get(f"{API}/user/info/", headers=_headers(token), params={"fields": fields}, timeout=30)
    return _check(r).get("user", {})


def list_videos(token: str, limit: int = 100) -> list[dict]:
    fields = "id,title,video_description,create_time,duration,share_url,view_count,like_count,comment_count,share_count"
    out, cursor = [], None
    while len(out) < limit:
        body = {"max_count": 20}
        if cursor:
            body["cursor"] = cursor
        d = _check(requests.post(f"{API}/video/list/", headers=_headers(token), params={"fields": fields},
                                 json=body, timeout=30))
        out += d.get("videos", [])
        if not d.get("has_more"):
            break
        cursor = d.get("cursor")
    return out

