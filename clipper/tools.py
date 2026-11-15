"""Outils système portables Mac / Windows : ffmpeg, encodeur vidéo, notifications, fichiers."""
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
# Sous Windows, empêche l'ouverture d'une fenêtre noire à chaque appel de ffmpeg.
NO_WINDOW = {"creationflags": 0x08000000} if IS_WIN else {}


@lru_cache(maxsize=1)
def ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **NO_WINDOW, **kw)


@lru_cache(maxsize=1)
def video_encoder() -> list[str]:
    """Encodeur matériel si la machine en a un qui marche, sinon x264 (logiciel)."""
    if IS_MAC:
        candidates = [["-c:v", "h264_videotoolbox", "-b:v", "10M"]]
    elif IS_WIN:
        candidates = [
            ["-c:v", "h264_nvenc", "-preset", "p5", "-b:v", "10M"],   # NVIDIA
            ["-c:v", "h264_qsv", "-b:v", "10M"],                      # Intel
            ["-c:v", "h264_amf", "-b:v", "10M"],                      # AMD
        ]
    else:
        candidates = []
    for enc in candidates:
        test = run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=black:s=256x256:d=0.2", *enc, "-f", "null", "-"], capture_output=True)
        if test.returncode == 0:
            return enc
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]


def notify(title: str, message: str):
    try:
        from .config import APP_NAME
        from desktop_notifier import DesktopNotifierSync

        DesktopNotifierSync(app_name=APP_NAME).send(title=title, message=message)
    except Exception:
        pass  # une notification ratée ne doit jamais bloquer un passage


def reveal(path: Path):
    """Montre le fichier dans le Finder / l'Explorateur."""
    if IS_MAC:
        subprocess.Popen(["open", "-R", str(path)])
    elif IS_WIN:
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
