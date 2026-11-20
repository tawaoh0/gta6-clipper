"""TikTok en plusieurs parties : une vidéo montée découpée en « Partie 1/3 », « Partie 2/3 »…

On part de la vidéo déjà montée (cam, sous-titres et effets sont donc gardés).
  - Les coupures tombent dans une pause entre deux phrases, près de la durée visée.
  - Chaque partie a un badge « PARTIE 2/3 » en haut.
  - À partir de la 2e partie, un titre « PARTIE 2 » s'affiche au début.
  - Toutes sauf la dernière finissent par « SUITE EN PARTIE 3 ».
Chaque partie devient un clip « à poster », avec sa légende numérotée.
"""
import json
import math
import time
from pathlib import Path

from PIL import Image, ImageDraw

from . import db as journal
from .media import H, W, _font, _hook_track, probe
from .tools import ffmpeg, run, video_encoder


def _badge(i: int, n: int, path: Path) -> Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = _font(52, 900)
    text = f"PARTIE {i}/{n}"
    tw = d.textlength(text, font=font)
    x0, y0 = (W - tw) / 2 - 34, 70
    d.rounded_rectangle((x0, y0, x0 + tw + 68, y0 + 86), radius=43, fill=(12, 8, 20, 215), outline=(255, 46, 136), width=4)
    d.text((W / 2, y0 + 43), text, font=font, fill="white", anchor="mm")
    img.save(path)
    return path


def _cuts(duration: float, n: int, pauses: list[float]) -> list[float]:
    """Instants de coupure : régulièrement espacés, déplacés vers la pause la plus proche (±3 s)."""
    cuts = []
    for k in range(1, n):
        t = duration * k / n
        near = [p for p in pauses if abs(p - t) <= 3 and (not cuts or p - cuts[-1] > 6)]
        cuts.append(min(near, key=lambda p: abs(p - t)) if near else t)
    return cuts


def split(clip_id: str, part_seconds: float = 30) -> str:
    conn = journal.connect()
    r = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if not r or not r["video_path"] or not Path(r["video_path"]).exists():
        raise RuntimeError("La vidéo de ce clip n'existe plus.")
    video = Path(r["video_path"])
    info = probe(video)
    dur = info["duration"]
    n = max(2, math.ceil(dur / max(part_seconds, 8) - 0.15))
    if dur < 16:
        raise RuntimeError("Clip trop court pour être coupé en plusieurs parties (moins de 16 s).")

    # Pauses entre les phrases, dans le temps de la vidéo montée
    words = json.loads(r["chunks"] or "[]")
    off = r["trim_start"] or 0.0
    pauses = [(words[i][1] + words[i + 1][0]) / 2 - off for i in range(len(words) - 1)
              if words[i + 1][0] - words[i][1] > 0.35]
    bounds = [0.0, *_cuts(dur, n, pauses), dur]

    work = video.parent / f".{video.stem}_parts"
    work.mkdir(exist_ok=True)
    made = []
    lines = (r["caption"] or "").strip().splitlines()
    hook = lines[0] if lines else (r["title"] or "")
    rest = "\n".join(lines[1:]).strip()
    for i in range(1, n + 1):
        a, b = bounds[i - 1], bounds[i]
        length = b - a
        out = video.with_name(f"{video.stem}_partie{i}.mp4")
        inputs = ["-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(video),
                  "-loop", "1", "-i", str(_badge(i, n, work / f"badge{i}.png"))]
        graph = "[0:v][1:v]overlay=0:0:shortest=1[v1]"
        last, k = "v1", 2
        if i > 1:   # titre « PARTIE 2 » au début
            inputs += ["-f", "concat", "-safe", "0", "-i", str(_hook_track(f"PARTIE {i}", 1060, 1.6, work))]
            graph += f";[{last}][{k}:v]overlay=0:0:format=auto:eof_action=pass[v{k}]"
            last, k = f"v{k}", k + 1
        if i < n:   # « la suite → partie 3 » à la fin
            sub = work / f"suite{i}"
            sub.mkdir(exist_ok=True)
            track = _hook_track(f"SUITE EN PARTIE {i + 1}", 980, 1.8, sub)
            inputs += ["-itsoffset", f"{max(length - 1.8, 0):.2f}", "-f", "concat", "-safe", "0", "-i", str(track)]
            graph += f";[{last}][{k}:v]overlay=0:0:format=auto:eof_action=pass[v{k}]"
            last, k = f"v{k}", k + 1
        cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *inputs,
               "-filter_complex", graph + f";[{last}]null[v]", "-map", "[v]"]
        if info["has_audio"]:
            cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k"]
        cmd += ["-t", f"{length:.2f}", "-r", "30", *video_encoder(), "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(out)]
        run(cmd, check=True)

        caption = f"Partie {i}/{n} · {hook}\n\n{rest}".strip()
        if i < n:
            caption = caption.replace("\n\n", f"\n\n➡️ La suite en partie {i + 1}\n\n", 1)
        caption = caption.rstrip() + f" #partie{i}"
        out.with_suffix(".txt").write_text(caption, encoding="utf-8")
        pid = f"{clip_id}-p{i}"
        journal.insert(conn, {"id": pid, "broadcaster_name": r["streamer"],
                              "title": f"{r['title']} (partie {i}/{n})", "url": r["url"] or "",
                              "view_count": r["views"] or 0, "score": r["score"] or 0,
                              "game_name": r["game"] or ""},
                       "manual", video_path=str(out), caption=caption, ready_at=time.time(),
                       hook=f"Partie {i}/{n}", parent_id=clip_id)
        made.append(pid)
    for p in sorted(work.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    work.rmdir()
    return f"{n} parties créées ({', '.join(f'{(bounds[i] - bounds[i - 1]):.0f} s' for i in range(1, n + 1))})"
