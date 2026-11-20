"""Fabrique les icônes de l'app à partir du logo officiel « Tuile » (branding/export/logo-1024.png).

Sorties dans branding/export/app/ :
  icon.png   1024 px, format Mac (logo à 824 px centré avec ombre, comme les icônes Apple)
  icon.icns  pour l'app Mac
  icon.ico   pour Windows (16 à 256 px)
Pour les utiliser dans l'app : copier ces 3 fichiers dans packaging/ puis refaire le build.
Usage : uv run python branding/make_app_icons.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
SRC = HERE / "export" / "logo-1024.png"
OUT = HERE / "export" / "app"


def mac_canvas(logo: Image.Image) -> Image.Image:
    S, inner = 1024, 824
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    art = logo.resize((inner, inner), Image.LANCZOS)
    off = (S - inner) // 2
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    a = art.getchannel("A").point(lambda v: int(v * 0.55))
    sh = Image.new("RGBA", art.size, (0, 0, 0, 255))
    sh.putalpha(a)
    shadow.paste(sh, (off, off + 18), sh)
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(art, (off, off))
    return canvas


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logo = Image.open(SRC).convert("RGBA")
    mac = mac_canvas(logo)
    mac.save(OUT / "icon.png")

    # .ico Windows : le logo plein cadre arrondi, sans marge
    logo.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])

    # .icns Mac
    if sys.platform == "darwin" and shutil.which("iconutil"):
        iconset = OUT / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for s in (16, 32, 128, 256, 512):
            mac.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            mac.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "icon.icns")], check=True)
        shutil.rmtree(iconset)
    print("Icônes prêtes dans", OUT)


if __name__ == "__main__":
    main()
