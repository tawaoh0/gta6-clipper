"""Téléchargement, sous-titres et montage vertical 9:16."""
import textwrap
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import RES
from .tools import ffmpeg, run, video_encoder

W, H = 1080, 1920
FONT = RES / "assets" / "fonts" / "Montserrat.ttf"   # police libre (OFL), poids variable


def _font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT), size)
    f.set_variation_by_axes([weight])
    return f


# ---------- téléchargement ----------

def download(url: str, dest_dir: Path) -> Path:
    from yt_dlp import YoutubeDL

    opts = {
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


def probe(path: Path) -> dict:
    import av

    with av.open(str(path)) as c:
        v = c.streams.video[0]
        duration = c.duration / 1_000_000 if c.duration else float(v.duration * v.time_base)
        return {
            "duration": duration,
            "width": v.codec_context.width,
            "height": v.codec_context.height,
            "has_audio": bool(c.streams.audio),
        }


# ---------- transcription ----------

@lru_cache(maxsize=1)
def _whisper(model_name: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device="cpu", compute_type="int8")


def transcribe(path: Path, model_name: str, language: str) -> tuple[str, list[tuple[float, float, str]]]:
    """Renvoie (texte complet, morceaux de 1 à 3 mots avec leurs horaires)."""
    model = _whisper(model_name)
    segments, _ = model.transcribe(
        str(path),
        language=None if language == "auto" else language,
        word_timestamps=True,
        vad_filter=True,
    )
    words, full = [], []
    for seg in segments:
        full.append(seg.text.strip())
        for w in seg.words or []:
            text = w.word.strip()
            if not text:
                continue
            # Whisper coupe « t'es » en « t » + « 'es » : on recolle autour des apostrophes.
            if words and (text[0] in "'’-" or words[-1][2][-1] in "'’"):
                s, _, prev = words[-1]
                words[-1] = (s, w.end, prev + text)
            else:
                words.append((w.start, w.end, text))

    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= 3 or w[0] - cur[-1][1] > 0.6 or len(" ".join(x[2] for x in cur)) > 18):
            chunks.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
            cur = []
        cur.append(w)
    if cur:
        chunks.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
    return " ".join(full), chunks


# ---------- images transparentes (sous-titres, crédit) ----------

def _blank() -> Image.Image:
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def _credit_png(streamer: str, y: int, out: Path) -> Path:
    img = _blank()
    d = ImageDraw.Draw(img)
    font = _font(44, 700)
    text = f"twitch.tv/{streamer}"
    tw = d.textlength(text, font=font)
    pad_x, box_h = 34, 76
    x0 = (W - tw) / 2 - pad_x
    d.rounded_rectangle((x0, y, x0 + tw + 2 * pad_x, y + box_h), radius=38, fill=(145, 70, 255, 235))
    d.text((W / 2, y + box_h / 2), text, font=font, fill="white", anchor="mm")
    img.save(out)
    return out


def _subtitle_png(text: str, y: int, out: Path) -> Path:
    img = _blank()
    d = ImageDraw.Draw(img)
    font = _font(80, 900)
    lines = textwrap.wrap(text.upper(), width=16) or [""]
    line_h = 96
    for i, line in enumerate(lines):
        d.text(
            (W / 2, y + i * line_h), line, font=font, anchor="mm",
            fill=(255, 230, 0) if i == 0 else "white",
            stroke_width=9, stroke_fill="black",
        )
    img.save(out)
    return out


def _subtitle_track(chunks, duration: float, y: int, work: Path) -> Path:
    """Fichier concat ffmpeg : une image transparente par morceau de phrase."""
    blank = work / "sub_blank.png"
    _blank().save(blank)
    lines, t = [], 0.0
    for i, (start, end, text) in enumerate(chunks):
        start, end = max(start, t), min(max(end, start + 0.35), duration)
        if start - t > 0.01:
            lines += [f"file '{blank.name}'", f"duration {start - t:.3f}"]
        png = _subtitle_png(text, y, work / f"sub_{i:03d}.png")
        lines += [f"file '{png.name}'", f"duration {end - start:.3f}"]
        t = end
    lines += [f"file '{blank.name}'", f"duration {max(duration - t, 0.1):.3f}", f"file '{blank.name}'"]
    path = work / "subs.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


# ---------- montage ----------

def render(src: Path, out: Path, streamer: str, chunks, facecam=None, zoom: float = 1.25,
           cam_position: str = "top") -> Path:
    info = probe(src)
    dur = info["duration"]
    work = out.parent / f".{out.stem}_work"
    work.mkdir(parents=True, exist_ok=True)

    if facecam:
        # Caméra découpée sur toute la largeur, jeu recadré au centre dans le reste.
        sw, sh = info["width"], info["height"]
        fx, fy, fw, fh = facecam
        ix, iy = fw * sw * 0.02, fh * sh * 0.02  # on rogne 2 % pour retirer le liseré du cadre
        cx, cy = round(fx * sw + ix), round(fy * sh + iy)
        cw, ch = round(fw * sw - 2 * ix) // 2 * 2, round(fh * sh - 2 * iy) // 2 * 2
        cam_h = min(max(round(W * ch / cw), 540), 760) // 2 * 2
        game_h = H - cam_h
        cam = (f"[0:v]crop={cw}:{ch}:{cx}:{cy},"
               f"scale={W}:{cam_h}:force_original_aspect_ratio=increase,"
               f"crop={W}:{cam_h}:(iw-{W})/2:(ih-{cam_h})*0.2,"  # visage plutôt en haut du cadre
               f"unsharp=5:5:0.6[cam]")
        game = f"[0:v]crop='min(iw,ih*{W}/{game_h})':ih,scale={W}:{game_h}[game]"
        if cam_position == "bottom":
            video = f"{cam};{game};[game][cam]vstack[base]"
            credit_y, sub_y = game_h - 38, game_h - 300
        else:
            video = f"{cam};{game};[cam][game]vstack[base]"
            credit_y, sub_y = cam_h - 38, cam_h + 220
    else:
        video = (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=24:2,eq=brightness=-0.12[bg];"
            f"[0:v]crop=iw/{zoom}:ih,scale={W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
        )
        fg_h = round(W / (info["width"] / zoom) * info["height"])
        credit_y = (H - fg_h) // 2 - 130
        sub_y = (H + fg_h) // 2 + 200

    inputs = ["-i", str(src), "-loop", "1", "-i", str(_credit_png(streamer, credit_y, work / "credit.png"))]
    graph = video + ";[base][1:v]overlay=0:0[v1]"
    if chunks:
        subs = _subtitle_track(chunks, dur, sub_y, work)
        inputs += ["-f", "concat", "-safe", "0", "-i", str(subs)]
        graph += ";[v1][2:v]overlay=0:0:format=auto[v]"
    else:
        graph += ";[v1]null[v]"

    cmd = [
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *inputs,
        "-filter_complex", graph, "-map", "[v]",
    ]
    if info["has_audio"]:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k"]
    cmd += [
        "-t", f"{dur:.3f}", "-r", "30", *video_encoder(),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
    ]
    run(cmd, check=True)
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    return out
