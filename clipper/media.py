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


def transcribe(path: Path, model_name: str, language: str) -> tuple[str, list[list]]:
    """Renvoie (texte complet, mots [début, fin, texte, confiance])."""
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
                s0, _, prev, pr = words[-1]
                words[-1] = [s0, w.end, prev + text, min(pr, w.probability)]
            else:
                words.append([w.start, w.end, text, w.probability])
    return " ".join(full), words


def chunk_words(words, max_words: int = 3, max_chars: int = 18) -> list[tuple[float, float, str, list]]:
    """Regroupe les mots en morceaux de sous-titres : (début, fin, texte, [(début, fin, mot)…]).

    Les anciens clips stockaient déjà des morceaux (3 valeurs) : on les garde tels quels."""
    if words and len(words[0]) == 3:
        return [(a, b, t, [(a, b, t)]) for a, b, t in words]
    chunks, cur = [], []
    for w in words:
        a, b, t = w[0], w[1], w[2]
        if cur and (len(cur) >= max_words or a - cur[-1][1] > 0.6 or len(" ".join(x[2] for x in cur) + t) > max_chars):
            chunks.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur), cur))
            cur = []
        cur.append((a, b, t))
    if cur:
        chunks.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur), cur))
    return chunks


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


SUB_STYLE = {"color": "#FFE600", "size": "M", "position": "auto", "mode": "karaoke", "words": 3}
SUB_SIZES = {"S": 64, "M": 80, "L": 96}


def _rgb(hexcolor: str) -> tuple[int, int, int]:
    h = hexcolor.lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 230, 0)


def _subtitle_img(text: str, y: int, style: dict, active: int | None = None, width: int = W,
                  height: int = H) -> Image.Image:
    """Sous-titre en majuscules, contour noir épais.
    mode « karaoke » : le mot en train d'être dit (index active) prend la couleur, les autres sont blancs.
    mode « bloc » : la première ligne prend la couleur, comme avant."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    size = SUB_SIZES.get(style.get("size", "M"), 80)
    font = _font(size, 900)
    color = _rgb(style.get("color", "#FFE600"))
    words = text.upper().split()
    max_w = width - 120
    lines, cur = [], []
    for i, wd in enumerate(words):   # retour à la ligne en gardant l'index de chaque mot
        test = " ".join(x for _, x in cur + [(i, wd)])
        if cur and d.textlength(test, font=font) > max_w:
            lines.append(cur)
            cur = []
        cur.append((i, wd))
    if cur:
        lines.append(cur)
    line_h = round(size * 1.2)
    stroke = max(6, round(size * 0.11))
    space = d.textlength(" ", font=font)
    karaoke = style.get("mode", "karaoke") == "karaoke"
    for li, line in enumerate(lines):
        widths = [d.textlength(wd, font=font) for _, wd in line]
        x = (width - (sum(widths) + space * (len(line) - 1))) / 2
        cy = y + li * line_h
        for (idx, wd), wdt in zip(line, widths):
            if karaoke:
                fill = color if idx == active else (255, 255, 255)
            else:
                fill = color if li == 0 else (255, 255, 255)
            d.text((x, cy), wd, font=font, anchor="lm", fill=fill, stroke_width=stroke, stroke_fill="black")
            x += wdt + space
    return img


def subtitle_preview(style: dict, text: str = "Attends il a fait quoi", active: int = 2) -> Image.Image:
    """Aperçu du style de sous-titres pour l'écran Réglages (une image de jeu stylisée derrière)."""
    from PIL import ImageFilter
    w, h = 540, 300
    bg = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(bg)
    for yy in range(h):
        t = yy / h
        px.line([(0, yy), (w, yy)], fill=(round(40 + 60 * t), round(70 + 40 * t), round(110 - 50 * t)))
    bg = bg.filter(ImageFilter.GaussianBlur(2))
    scale = 0.5
    sub = _subtitle_img(text, round(h / scale * 0.5), style, active=active, width=round(w / scale), height=round(h / scale))
    sub = sub.resize((w, h), Image.LANCZOS)
    bg.paste(sub, (0, 0), sub)
    return bg


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


def _subtitle_track(chunks, duration: float, y: int, work: Path, style: dict) -> Path:
    """Fichier concat ffmpeg : une image transparente par morceau (ou par mot en mode karaoké)."""
    blank = work / "sub_blank.png"
    _blank().save(blank)
    lines, t, n = [], 0.0, 0
    karaoke = style.get("mode", "karaoke") == "karaoke"
    for start, end, text, words in chunks:
        start, end = max(start, t), min(max(end, start + 0.35), duration)
        if end <= start:
            continue
        if start - t > 0.01:
            lines += [f"file '{blank.name}'", f"duration {start - t:.3f}"]
        # Une image par mot dit (le mot actif change de couleur), ou une seule image pour le morceau.
        steps = [(max(ws, start), we) for ws, we, _ in words] if karaoke and len(words) > 1 else [(start, end)]
        for k, (ws, we) in enumerate(steps):
            ws = max(ws, start)
            we = end if k == len(steps) - 1 else max(min(steps[k + 1][0], end), ws + 0.05)
            png = work / f"sub_{n:04d}.png"
            n += 1
            _subtitle_img(text, y, style, active=k if karaoke else None).save(png)
            lines += [f"file '{png.name}'", f"duration {max(we - ws, 0.04):.3f}"]
        t = end
    lines += [f"file '{blank.name}'", f"duration {max(duration - t, 0.1):.3f}", f"file '{blank.name}'"]
    path = work / "subs.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def _zoom_filter(at: float, dur: float) -> str:
    """Léger zoom avant (12 %) sur le moment fort : monte en 0,25 s, tient 1,2 s, redescend en 0,4 s."""
    a, b, c, d = at - 0.15, at + 0.1, at + 1.3, at + 1.7
    z = (f"if(lt(t,{a:.2f}),1,if(lt(t,{b:.2f}),1+0.12*(t-{a:.2f})/0.25,"
         f"if(lt(t,{c:.2f}),1.12,if(lt(t,{d:.2f}),1.12-0.12*(t-{c:.2f})/0.4,1))))")
    return (f"scale=w='trunc({W}*({z})/2)*2':h='trunc({H}*({z})/2)*2':eval=frame,"
            f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2")


# ---------- montage ----------

def render(src: Path, out: Path, streamer: str, chunks, facecam=None, zoom: float = 1.25,
           cam_position: str = "top", start: float = 0.0, end: float | None = None,
           hook: str = "", hook_seconds: float = 2.5, burned_band=None, sub_style: dict | None = None,
           zoom_at: float | None = None) -> Path:
    """Monte la vidéo verticale. start/end : extrait de la source à garder (en secondes).
    hook : texte affiché en gros pendant les premières secondes (vide = aucun)."""
    info = probe(src)
    start = max(0.0, min(start or 0.0, info["duration"] - 1))
    end = min(end or info["duration"], info["duration"])
    if end - start < 1:
        end = min(start + 1, info["duration"])
    dur = end - start
    # Sous-titres : mots regroupés selon le style, puis recalés sur l'extrait gardé
    style = {**SUB_STYLE, **(sub_style or {})}
    chunks = [
        (max(a, start) - start, min(b, end) - start, t, [(max(x, start) - start, min(y, end) - start, w) for x, y, w in ws])
        for a, b, t, ws in chunk_words(chunks or [], max_words=int(style.get("words", 3)))
        if b > start and a < end
    ]
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

    sub_y += {"high": -260, "low": 220}.get(style.get("position", "auto"), 0)
    sub_y = min(max(sub_y, 120), H - 160)
    if zoom_at is not None and 0.3 < zoom_at - start < dur - 0.5:
        video = video.replace("[base]", "[prezoom]") + f";[prezoom]{_zoom_filter(zoom_at - start, dur)}[base]"

    inputs = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
              "-loop", "1", "-i", str(_credit_png(streamer, credit_y, work / "credit.png"))]
    graph = video + ";[base][1:v]overlay=0:0[v1]"
    n, last = 2, "v1"
    if chunks:
        subs = _subtitle_track(chunks, dur, sub_y, work, style)
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
