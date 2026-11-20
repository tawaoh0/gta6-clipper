"""Effets optionnels du montage : emojis automatiques, bruitages, filigrane.

Emojis : choisis à partir de ce qui est dit (rires, « quoi ?! », « je suis mort »…) et du son
(gros pic = 😱 ou 🔥). Au plus 3 par clip, espacés d'au moins 3 s. Ils apparaissent avec un
petit « pop », flottent un peu vers le haut, puis disparaissent.
Bruitages : « whoosh » au début du zoom, « boom » sur le moment fort, « pop » à chaque emoji.
Les sons sont fabriqués par packaging/make_sfx.py (ils appartiennent au projet).
"""
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .config import RES

EMOJI_DIR = RES / "assets" / "emoji"
SFX_DIR = RES / "assets" / "sfx"
W, H = 1080, 1920

LAUGH = re.compile(r"\b(ha(ha)+|ah(ah)+|hé(hé)+|hi(hi)+|mdr|ptdr|lol|xd|rires?)\b", re.I)
SHOCK = re.compile(r"\b(quoi|what|non+|nan+|attends?|pardon|sérieux|comment)\b", re.I)
WOW = re.compile(r"\b(incroyable|dingue|fou|folle|ouf|insane|masterclass|énorme)\b", re.I)
DEAD = re.compile(r"\b(mort|dead|mourir|tué|crevé)\b", re.I)
CRY = re.compile(r"\b(pleure|pleurer|ouin|triste|nul+e?)\b", re.I)
OH = re.compile(r"\b(oh+|waouh|wow|ouah|woah)\b", re.I)


def pick_emojis(words, start: float, end: float, loudness=None, rate: int = 10,
                max_count: int = 3) -> list[tuple[float, str]]:
    """[(instant dans la source, code de l'emoji)], du plus fort au moins fort, espacés de 3 s."""
    found = []
    for w in words or []:
        a, b, text = w[0], w[1], w[2]
        if b < start or a > end:
            continue
        for rx, cp, weight in ((LAUGH, "1f602", 3), (DEAD, "1f480", 3), (WOW, "1f92f", 2.5),
                               (CRY, "1f62d", 2), (SHOCK, "1f631", 1.5), (OH, "1f62e", 1.2)):
            if rx.search(text):
                found.append((weight, a, cp))
                break
    if loudness is not None and len(loudness) > rate * 2:
        seg = loudness[int(start * rate): int(end * rate)]
        if len(seg):
            base = float(np.median(seg))
            i = int(np.argmax(seg))
            if seg[i] - base > 12:   # très gros pic de son : réaction forte
                found.append((2.8, start + i / rate, "1f631" if seg[i] - base > 16 else "1f525"))
    found.sort(key=lambda x: -x[0])
    chosen = []
    for _, t, cp in found:
        # Pas pendant l'accroche du début (≈ 3 s), ni dans la dernière seconde.
        if all(abs(t - c[0]) >= 3 for c in chosen) and start + 3.0 <= t <= end - 1:
            chosen.append((t, cp))
        if len(chosen) >= max_count:
            break
    return sorted(chosen)


def emoji_track(events, duration: float, x: int, y: int, work: Path) -> Path | None:
    """Fichier concat : chaque emoji apparaît (pop), flotte 1,2 s, puis s'efface."""
    events = [(t, cp) for t, cp in events if (EMOJI_DIR / f"{cp}.png").exists() and t < duration]
    if not events:
        return None
    blank = work / "emo_blank.png"
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    lines, now, n = [], 0.0, 0
    frame = 1 / 30
    for t, cp in events:
        t = max(t, now)
        if t - now > 0.01:
            lines += [f"file '{blank.name}'", f"duration {t - now:.3f}"]
        src = Image.open(EMOJI_DIR / f"{cp}.png").convert("RGBA")
        steps = [(0.4, 1, 0), (0.85, 1, -4), (1.15, 1, -8), (1.0, 1, -12)]       # entrée
        steps += [(1.0, 1, -12 - k * 4) for k in range(1, 10)]                   # flotte
        steps += [(1.0, 0.6, -52), (1.0, 0.3, -56)]                               # sortie
        total = 0.0
        for k, (scale, alpha, dy) in enumerate(steps):
            size = round(190 * scale)
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            e = src.resize((size, size), Image.LANCZOS).rotate(-8 + k % 3 * 0, resample=Image.BICUBIC)
            if alpha < 1:
                e.putalpha(e.getchannel("A").point(lambda v, a=alpha: round(v * a)))
            shadow = Image.new("RGBA", e.size, (0, 0, 0, 0))
            shadow.putalpha(e.getchannel("A").point(lambda v: v // 3))
            img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)), (x - size // 2 + 6, y - size // 2 + dy + 10))
            img.alpha_composite(e, (x - size // 2, y - size // 2 + dy))
            png = work / f"emo_{n:03d}.png"
            n += 1
            img.save(png)
            d = frame * 2 if k < 4 else (0.12 if k < len(steps) - 2 else 0.1)
            lines += [f"file '{png.name}'", f"duration {d:.3f}"]
            total += d
        now = t + total
    lines += [f"file '{blank.name}'", f"duration {max(duration - now, 0.1):.3f}", f"file '{blank.name}'"]
    path = work / "emojis.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def watermark_png(opts: dict, work: Path, font_fn) -> Path | None:
    """Logo et/ou pseudo dans un coin, semi-transparent."""
    text = (opts.get("text") or "").strip()
    logo = opts.get("logo") or ""
    if not text and not (logo and Path(logo).exists()):
        return None
    opacity = min(max(float(opts.get("opacity", 0.75)), 0.15), 1.0)
    parts = []
    if logo and Path(logo).exists():
        im = Image.open(logo).convert("RGBA")
        im.thumbnail((150, 150), Image.LANCZOS)
        parts.append(im)
    if text:
        font = font_fn(42, 800)
        tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        tw = int(tmp.textlength(text, font=font)) + 16
        im = Image.new("RGBA", (tw, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.text((10, 34), text, font=font, fill=(0, 0, 0, 150), anchor="lm")
        d.text((8, 32), text, font=font, fill="white", anchor="lm", stroke_width=2, stroke_fill=(0, 0, 0, 160))
        parts.append(im)
    gap = 12
    bw = sum(p.width for p in parts) + gap * (len(parts) - 1)
    bh = max(p.height for p in parts)
    badge = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    x = 0
    for p in parts:
        badge.alpha_composite(p, (x, (bh - p.height) // 2))
        x += p.width + gap
    badge.putalpha(badge.getchannel("A").point(lambda v: round(v * opacity)))
    corner = opts.get("corner", "tr")
    m = 42
    px = m if corner in ("tl", "bl") else W - bw - m
    py = m + 40 if corner in ("tl", "tr") else H - bh - m - 150   # évite l'interface TikTok en bas
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.alpha_composite(badge, (px, py))
    out = work / "watermark.png"
    img.save(out)
    return out


def sfx_inputs(events, volume: float) -> tuple[list[str], list[str]]:
    """Entrées ffmpeg et filtres audio pour les bruitages [(instant, nom)].
    Renvoie (arguments -i…, étiquettes à mixer)."""
    args, labels, filters = [], [], []
    for i, (t, name) in enumerate(events):
        f = SFX_DIR / f"{name}.wav"
        if not f.exists() or t < 0:
            continue
        args += ["-i", str(f)]
        labels.append((len(labels), t, name))
    return args, labels


def sfx_graph(first_input: int, labels, volume: float) -> tuple[str, str]:
    """Filtre qui mélange le son du clip [0:a] avec les bruitages. Renvoie (filtre, sortie)."""
    parts, mix = [], ["[0:a]"]
    gain = {"whoosh": 0.55, "boom": 0.9, "pop": 0.45}
    for k, t, name in labels:
        ms = int(t * 1000)
        parts.append(f"[{first_input + k}:a]volume={gain.get(name, 0.6) * volume:.2f},adelay={ms}|{ms}[sx{k}]")
        mix.append(f"[sx{k}]")
    parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first[aout]")
    return ";".join(parts), "[aout]"
