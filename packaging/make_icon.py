"""Dessine l'icône de l'app (packaging/icon.png, .icns pour Mac, .ico pour Windows).

Ciel de nuit, soleil couchant magenta → orange, et un cadre vertical 9:16 avec un bouton lecture.
Usage : uv run python packaging/make_icon.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
S = 1024


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient(size, stops, vertical=True):
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    n = h if vertical else w
    for i in range(n):
        t = i / (n - 1)
        for k in range(len(stops) - 1):
            (t0, c0), (t1, c1) = stops[k], stops[k + 1]
            if t0 <= t <= t1:
                c = lerp(c0, c1, (t - t0) / (t1 - t0))
                break
        for j in range(w if vertical else h):
            px[(j, i) if vertical else (i, j)] = c
    return img


def draw() -> Image.Image:
    # Fond : nuit violette qui se réchauffe vers le bas
    bg = gradient((S, S), [(0, (20, 14, 34)), (0.55, (36, 16, 48)), (1, (92, 22, 64))])
    # Soleil couchant
    sun = gradient((S, S), [(0, (255, 195, 90)), (0.5, (255, 122, 69)), (1, (255, 46, 136))])
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).ellipse((172, 430, 852, 1110), fill=255)
    for y in range(640, S, 70):  # bandes horizontales façon soleil rétro
        ImageDraw.Draw(m).rectangle((0, y, S, y + 18 + (y - 640) // 14), fill=0)
    glow = m.filter(ImageFilter.GaussianBlur(60))
    bg = Image.composite(sun, bg, ImageChops.lighter(m, glow.point(lambda v: v // 3)))

    # Cadre vertical 9:16 (la vidéo TikTok)
    fr = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(fr)
    x0, y0, x1, y1 = 342, 170, 682, 774
    d.rounded_rectangle((x0 + 10, y0 + 24, x1 + 10, y1 + 24), 56, fill=(0, 0, 0, 120))
    fr = fr.filter(ImageFilter.GaussianBlur(18))
    d = ImageDraw.Draw(fr)
    d.rounded_rectangle((x0, y0, x1, y1), 56, fill=(12, 9, 22, 235), outline=(255, 255, 255, 235), width=22)
    d.rounded_rectangle((x0 + 50, y0 + 60, x1 - 50, y0 + 190), 26, fill=(255, 46, 136, 255))  # la facecam
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 + 70
    d.polygon([(cx - 62, cy - 86), (cx - 62, cy + 86), (cx + 90, cy)], fill=(255, 255, 255, 255))  # lecture
    img = Image.alpha_composite(bg.convert("RGBA"), fr)

    # Coins arrondis façon icône d'app
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), 228, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def main():
    icon = draw()
    icon.save(HERE / "icon.png")
    icon.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if sys.platform == "darwin" and shutil.which("iconutil"):
        iconset = HERE / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        # Sur Mac, l'icône laisse une petite marge autour du carré arrondi
        padded = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        padded.paste(icon.resize((824, 824), Image.LANCZOS), (100, 100))
        for s in (16, 32, 128, 256, 512):
            padded.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            padded.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "icon.icns")], check=True)
        shutil.rmtree(iconset)
    print("icônes écrites dans", HERE)


if __name__ == "__main__":
    main()
