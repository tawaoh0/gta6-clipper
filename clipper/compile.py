"""Compilation « Top du jour » : les meilleurs clips de la journée en une seule vidéo.

On prend les N clips du jour qui ont fait le plus de vues par heure. Pour chacun, on garde
~11 s autour de son moment fort (là où le son monte). On les enchaîne en compte à rebours
(#5 → #1) avec une transition glissée, un badge de rang, et un titre « TOP 5 DU JOUR » au début.
"""
import json
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from . import db as journal
from .config import load_config, output_dir
from .media import H, W, _font, _hook_track, probe
from .tools import ffmpeg, run, video_encoder

FADE = 0.3
TODO_LIKE = ("manual", "ready", "posted", "sent")


def day_clips(day: str, conn=None) -> list:
    conn = conn or journal.connect()
    d0 = datetime.strptime(day, "%Y-%m-%d").timestamp()
    rows = conn.execute(
        "SELECT * FROM clips WHERE seen_at >= ? AND seen_at < ? AND id NOT LIKE 'top-%' "
        f"AND status IN ({','.join('?' * len(TODO_LIKE))}) ORDER BY score DESC",
        (d0, d0 + 86400, *TODO_LIKE)).fetchall()
    # Il faut la vidéo et du son (les transitions mélangent aussi l'audio)
    return [r for r in rows if r["video_path"] and Path(r["video_path"]).exists()
            and probe(Path(r["video_path"]))["has_audio"]]


def _rank_png(rank: int, path: Path) -> Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = _font(96, 900)
    text = f"#{rank}"
    tw = d.textlength(text, font=font)
    x0, y0 = 46, 150
    d.rounded_rectangle((x0, y0, x0 + tw + 56, y0 + 128), radius=30, fill=(255, 46, 136, 240))
    d.text((x0 + 28 + tw / 2, y0 + 64), text, font=font, fill="white", anchor="mm", stroke_width=3, stroke_fill=(120, 0, 50))
    img.save(path)
    return path


def _segment(r, seconds: float) -> tuple[float, float]:
    """Morceau de la vidéo montée à garder : autour du moment fort, sinon vers le milieu."""
    dur = probe(Path(r["video_path"]))["duration"]
    length = min(seconds, dur)
    center = (r["zoom_at"] - (r["trim_start"] or 0)) if r["zoom_at"] is not None else dur * 0.55
    a = max(0.0, min(center - 0.6 * length, dur - length))
    return a, a + length


def build(day: str | None = None) -> str:
    cfg = load_config()
    p = cfg["pipeline"]
    day = day or datetime.now().strftime("%Y-%m-%d")
    conn = journal.connect()
    clips = day_clips(day, conn)[: int(p.get("top_clips", 5))]
    if len(clips) < 2:
        raise RuntimeError("Il faut au moins 2 clips montés aujourd'hui pour faire un Top du jour.")
    order = list(reversed(clips))            # compte à rebours : le meilleur à la fin
    n = len(order)
    out_dir = output_dir(cfg) / day
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"top_du_jour_{day}.mp4"
    work = out_dir / f".top_{day}_work"
    work.mkdir(exist_ok=True)

    inputs, vparts, aparts, lengths = [], [], [], []
    for i, r in enumerate(order):
        a, b = _segment(r, float(p.get("top_seconds", 11)))
        inputs += ["-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", r["video_path"]]
        lengths.append(b - a)
    for i in range(n):
        inputs += ["-loop", "1", "-i", str(_rank_png(n - i, work / f"rank_{i}.png"))]
    title = f"TOP {n} DU JOUR"
    inputs += ["-f", "concat", "-safe", "0", "-i", str(_hook_track(title, 760, 2.2, work))]

    # Chaque morceau : badge de rang par-dessus, puis transition glissée vers le suivant.
    for i in range(n):
        vparts.append(f"[{i}:v]settb=AVTB,fps=30,format=yuv420p,setsar=1[s{i}];"
                      f"[s{i}][{n + i}:v]overlay=0:0:shortest=1[r{i}]")
        aparts.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[a{i}]")
    chain_v, chain_a, offset = "r0", "a0", 0.0
    for i in range(1, n):
        offset += lengths[i - 1] - FADE
        vparts.append(f"[{chain_v}][r{i}]xfade=transition=slideup:duration={FADE}:offset={offset:.2f}[x{i}]")
        aparts.append(f"[{chain_a}][a{i}]acrossfade=d={FADE}[y{i}]")
        chain_v, chain_a = f"x{i}", f"y{i}"
    vparts.append(f"[{chain_v}][{2 * n}:v]overlay=0:0:format=auto:eof_action=pass[v]")
    total = sum(lengths) - FADE * (n - 1)
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(vparts + aparts), "-map", "[v]", "-map", f"[{chain_a}]",
           "-t", f"{total:.2f}", "-r", "30", *video_encoder(), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    run(cmd, check=True)
    for f in work.iterdir():
        f.unlink()
    work.rmdir()

    # Légende : accroche + crédit de chaque streamer + hashtags communs
    streamers = list(dict.fromkeys(r["streamer"] for r in clips))
    tags = []
    for r in clips:
        last = (r["caption"] or "").strip().splitlines()[-1:] or [""]
        tags += [t for t in last[0].split() if t.startswith("#")]
    tags = list(dict.fromkeys(["#top5", *tags, "#clip"]))[:10]
    caption = (f"Les {n} meilleurs moments du jour 🔥\n\n"
               f"🎥 Clips : {', '.join(streamers)} sur Twitch\n\n{' '.join(tags)}")
    out.with_suffix(".txt").write_text(caption, encoding="utf-8")
    cid = f"top-{day}"
    journal.insert(conn, {"id": cid, "broadcaster_name": "Top du jour", "title": f"Top {n} du {day}",
                          "url": "", "view_count": sum(r["views"] or 0 for r in clips),
                          "score": max(r["score"] or 0 for r in clips), "game_name": clips[0]["game"] or ""},
                   "manual", video_path=str(out), caption=caption, ready_at=time.time(),
                   hook=f"Top {n} du jour", cut_reason=json.dumps([r["id"] for r in order]))
    return f"Top du jour créé ({n} clips, {total:.0f} s)"
