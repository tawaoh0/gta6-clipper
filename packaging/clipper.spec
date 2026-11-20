# -*- mode: python ; coding: utf-8 -*-
# Construit l'app : uv run pyinstaller packaging/clipper.spec --noconfirm
# Mac : dist/Grand Clip Auto.app    Windows : dist/Grand Clip Auto/Grand Clip Auto.exe
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent
NAME = "Grand Clip Auto"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "clipper" / "__init__.py").read_text()).group(1)

datas = [
    (str(ROOT / "ui"), "ui"),
    (str(ROOT / "assets" / "fonts"), "assets/fonts"),
    (str(ROOT / "assets" / "emoji"), "assets/emoji"),
    (str(ROOT / "assets" / "sfx"), "assets/sfx"),
    (str(ROOT / "models"), "models"),
]
binaries, hidden = [], ["clipper.web", "clipper.pipeline", "clipper.twitch", "clipper.media",
                        "clipper.facecam", "clipper.caption", "clipper.db", "clipper.tiktok",
                        "clipper.jobs", "clipper.burned_subs", "clipper.moment", "clipper.hooks", "clipper.analysis", "clipper.storage", "clipper.youtube",
                        "clipper.effects", "clipper.options", "clipper.compile", "clipper.parts"]
for pkg in ("faster_whisper", "ctranslate2", "imageio_ffmpeg", "webview", "desktop_notifier", "yt_dlp", "av"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
datas += collect_data_files("certifi")
binaries += collect_dynamic_libs("onnxruntime")

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = str(ROOT / "packaging" / ("icon.icns" if sys.platform == "darwin" else "icon.ico"))

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME,
    console=False,          # pas de fenêtre de terminal
    icon=icon,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=icon,
        bundle_identifier="app.grandclipauto.desktop",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSHumanReadableCopyright": "Clips : avec l'accord des streamers.",
        },
    )
