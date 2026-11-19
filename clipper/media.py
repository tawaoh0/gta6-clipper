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


def _hook_card(text: str) -> Image.Image:
    """Bandeau d'accroche : dégradé magenta → orange, texte blanc très gras, ombre, léger penché."""
    # Le texte doit tenir en entier sur 3 lignes : on réduit la taille si besoin.
    text = text.strip().upper()
    for size, width in ((76, 17), (68, 19), (60, 22), (54, 25)):
        lines = textwrap.wrap(text, width=width)
        if len(lines) <= 3:
            break
    else:
        lines = textwrap.wrap(text, width=25)[:3]
        lines[-1] = lines[-1].rstrip(" ,.»") + "… »" if text.endswith("»") else lines[-1] + "…"
    lines = lines or [""]
    font = _font(size, 900)
    probe_d = ImageDraw.Draw(_blank())
    widths = [probe_d.textlength(l, font=font) for l in lines]
    line_h, pad_x, pad_y = round(size * 1.21), 54, 34
    bw = int(min(max(widths) + 2 * pad_x, W - 90))
    bh = int(len(lines) * line_h + 2 * pad_y)

    # Dégradé horizontal dans un rectangle arrondi
    grad = Image.new("RGBA", (bw, bh))
    gp = grad.load()
    c0, c1 = (255, 46, 136), (255, 112, 64)
    for x in range(bw):
        t = x / max(bw - 1, 1)
        col = tuple(round(c0[i] + (c1[i] - c0[i]) * t) for i in range(3)) + (255,)
        for y in range(bh):
            gp[x, y] = col
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=42, fill=255)
    card = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    card.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((3, 3, bw - 4, bh - 4), radius=40, outline=(255, 255, 255, 90), width=3)  # liseré
    d.rounded_rectangle((18, 10, bw - 18, 16), radius=3, fill=(255, 255, 255, 60))               # reflet
    for i, line in enumerate(lines):
        cy = pad_y + line_h * (i + 0.5)
        d.text((bw / 2 + 3, cy + 5), line, font=font, fill=(90, 0, 40, 150), anchor="mm")   # ombre du texte
        d.text((bw / 2, cy), line, font=font, fill="white", anchor="mm")

    # Ombre portée + inclinaison
    pad = 60
    full = Image.new("RGBA", (bw + 2 * pad, bh + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", full.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((pad + 6, pad + 18, pad + bw + 6, pad + bh + 18), radius=42,
                                             fill=(0, 0, 0, 150))
    from PIL import ImageFilter
    full = Image.alpha_composite(full, shadow.filter(ImageFilter.GaussianBlur(18)))
    full.alpha_composite(card, (pad, pad))
    return full.rotate(2.5, resample=Image.BICUBIC, expand=True)


def _hook_track(text: str, y_center: int, seconds: float, work: Path) -> Path:
    """Fichier concat : l'accroche arrive avec un effet « pop », reste, puis s'efface en fondu."""
    card = _hook_card(text)
    frame = 1 / 30
    steps = [(0.5, 1.0), (0.82, 1.0), (1.07, 1.0), (1.0, 1.0)]           # (taille, opacité) à l'entrée
    hold = max(seconds - len(steps) * frame * 2 - 0.3, 0.3)
    fade = [(1.0, 0.66), (1.0, 0.33)]
    lines = []
    for i, (scale, alpha) in enumerate(steps + [(1.0, 1.0)] + fade):
        img = _blank()
        c = card.resize((max(1, round(card.width * scale)), max(1, round(card.height * scale))), Image.LANCZOS)
        if alpha < 1:
            c.putalpha(c.getchannel("A").point(lambda v, a=alpha: round(v * a)))
        img.alpha_composite(c, (round((W - c.width) / 2), round(y_center - c.height / 2)))
        png = work / f"hook_{i}.png"
        img.save(png)
        dur = hold if i == len(steps) else (0.15 if i > len(steps) else frame * 2)
        lines += [f"file '{png.name}'", f"duration {dur:.3f}"]
    blank = work / "hook_blank.png"
    _blank().save(blank)
    lines += [f"file '{blank.name}'", "duration 0.1", f"file '{blank.name}'"]
    path = work / "hook.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


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
           cam_position: str = "top", start: float = 0.0, end: float | None = None,
           hook: str = "", hook_seconds: float = 2.5, burned_band=None) -> Path:
    """Monte la vidéo verticale. start/end : extrait de la source à garder (en secondes).
    hook : texte affiché en gros pendant les premières secondes (vide = aucun)."""
    info = probe(src)
    start = max(0.0, min(start or 0.0, info["duration"] - 1))
    end = min(end or info["duration"], info["duration"])
    if end - start < 1:
        end = min(start + 1, info["duration"])
    dur = end - start
    # Sous-titres recalés sur l'extrait gardé
    chunks = [(max(a, start) - start, min(b, end) - start, t) for a, b, t in (chunks or []) if b > start and a < end]
    work = out.parent / f".{out.stem}_work"
    work.mkdir(parents=True, exist_ok=True)

    # Sous-titres déjà incrustés par le stream : on coupe la bande où ils se trouvent (haut ou bas).
    pre, src_h = "", info["height"]
    if burned_band:
        b0, b1 = burned_band
        if b0 >= 0.55:
            pre, src_h = f"crop=iw:trunc(ih*{b0:.4f}/2)*2:0:0,", round(info["height"] * b0)
        elif b1 <= 0.45:
            pre, src_h = (f"crop=iw:trunc(ih*{1 - b1:.4f}/2)*2:0:trunc(ih*{b1:.4f}),",
                          round(info["height"] * (1 - b1)))

    if facecam:
        # Caméra découpée sur toute la largeur, jeu recadré au centre dans le reste.
        sw, sh = info["width"], info["height"]
        fx, fy, fw, fh = facecam
        ix, iy = fw * sw * 0.01, fh * sh * 0.01  # on rogne 1 % pour retirer le liseré du cadre
        cx, cy = round(fx * sw + ix), round(fy * sh + iy)
        cw, ch = round(fw * sw - 2 * ix) // 2 * 2, round(fh * sh - 2 * iy) // 2 * 2
        cam_h = min(max(round(W * ch / cw), 540), 760) // 2 * 2
        game_h = H - cam_h
        cam = (f"[0:v]crop={cw}:{ch}:{cx}:{cy},"
               f"scale={W}:{cam_h}:force_original_aspect_ratio=increase,"
               f"crop={W}:{cam_h}:(iw-{W})/2:(ih-{cam_h})*0.2,"  # visage plutôt en haut du cadre
               f"unsharp=5:5:0.6[cam]")
        game = f"[0:v]{pre}crop='min(iw,ih*{W}/{game_h})':ih,scale={W}:{game_h}[game]"
        if cam_position == "bottom":
            video = f"{cam};{game};[game][cam]vstack[base]"
            credit_y, sub_y, hook_y = game_h - 38, game_h - 300, round(game_h * 0.36)
        else:
            video = f"{cam};{game};[cam][game]vstack[base]"
            credit_y, sub_y, hook_y = cam_h - 38, cam_h + 220, cam_h + round(game_h * 0.55)
    else:
        video = (
            f"[0:v]{pre}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=24:2,eq=brightness=-0.12[bg];"
            f"[0:v]{pre}crop=iw/{zoom}:ih,scale={W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
        )
        fg_h = round(W / (info["width"] / zoom) * src_h)
        credit_y = (H - fg_h) // 2 - 130
        sub_y = (H + fg_h) // 2 + 200
        hook_y = max((H - fg_h) // 2 - 330, 260)

    inputs = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
              "-loop", "1", "-i", str(_credit_png(streamer, credit_y, work / "credit.png"))]
    graph = video + ";[base][1:v]overlay=0:0[v1]"
    n, last = 2, "v1"
    if chunks:
        subs = _subtitle_track(chunks, dur, sub_y, work)
        inputs += ["-f", "concat", "-safe", "0", "-i", str(subs)]
        graph += f";[{last}][{n}:v]overlay=0:0:format=auto[v{n}]"
        last, n = f"v{n}", n + 1
    if hook.strip():
        inputs += ["-f", "concat", "-safe", "0", "-i", str(_hook_track(hook, hook_y, hook_seconds, work))]
        graph += f";[{last}][{n}:v]overlay=0:0:format=auto:eof_action=pass[v{n}]"
        last, n = f"v{n}", n + 1
    graph += f";[{last}]null[v]"

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
