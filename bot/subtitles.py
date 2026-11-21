"""Субтитры в формате ASS: крупные слова по центру, текущее слово подсвечено."""
from pathlib import Path

WHITE = "&H00FFFFFF"
YELLOW = "&H0000E5FF"  # ASS: &HAABBGGRR


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")")


def chunk_words(words: list[dict], size: int) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= size or w["word"].rstrip()[-1:] in ".!?…,:;—":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(cfg: dict, words: list[dict], title: str, duration: float, path: Path) -> Path:
    v = cfg["video"]
    W, H, font = v["width"], v["height"], v["font"]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},88,{WHITE},{WHITE},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,5,80,80,0,1
Style: Title,{font},66,{WHITE},{WHITE},&H50000000,&H50000000,1,0,0,0,100,100,0,0,3,22,0,8,110,110,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    # Заголовок-крючок сверху первые 3 секунды
    lines.append(f"Dialogue: 1,{_ts(0)},{_ts(min(3.0, duration))},Title,,0,0,0,,{{\\fad(150,300)}}{_esc(title.upper())}")

    chunks = chunk_words(words, v.get("subtitle_words_per_chunk", 3))
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else duration
        chunk_end = min(next_start, chunk[-1]["end"] + 0.4)
        for wi, w in enumerate(chunk):
            start = w["start"]
            end = chunk[wi + 1]["start"] if wi + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            parts = []
            for k, x in enumerate(chunk):
                txt = _esc(x["word"].upper())
                parts.append(f"{{\\c{YELLOW}&}}{txt}{{\\c{WHITE}&}}" if k == wi else txt)
            pop = "{\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" if wi == 0 else ""
            # \pos — чуть ниже центра кадра, над интерфейсом TikTok
            lines.append(
                f"Dialogue: 0,{_ts(start)},{_ts(end)},Sub,,0,0,0,,{{\\pos({W // 2},{int(H * 0.62)})}}{pop}{' '.join(parts)}"
            )
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_srt(words: list[dict], path: Path, size: int = 6) -> Path:
    """Обычные SRT-субтитры (на случай, если нужны отдельно)."""
    def ts(t):
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    out = []
    for i, ch in enumerate(chunk_words(words, size), 1):
        out.append(f"{i}\n{ts(ch[0]['start'])} --> {ts(ch[-1]['end'])}\n{' '.join(w['word'] for w in ch)}\n")
    path.write_text("\n".join(out), encoding="utf-8")
    return path
