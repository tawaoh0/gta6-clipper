"""Recadre un clip sur le vrai moment fort, sans IA payante.

1. On télécharge un passage de la VOD autour du clip (avant + après), si la VOD est publique.
2. On mesure le volume sonore : les cris, rires et explosions de voix repèrent le moment fort.
3. On place le début un peu avant ce pic (pour comprendre la situation) et la fin après la réaction,
   puis on cale les deux sur les pauses entre les phrases pour ne pas couper au milieu d'un mot.
Si le moment fort est déjà bien placé dans le clip du spectateur, on garde son découpage.
"""
from pathlib import Path

import numpy as np

from .tools import ffmpeg

BEFORE, AFTER, MAX_WINDOW = 20, 10, 100   # secondes
RATE = 10                                  # mesures de volume par seconde


def window(clip: dict) -> tuple[float, float] | None:
    """Passage de VOD à télécharger : le clip (ou l'ensemble des doublons) + marge avant/après."""
    if not clip.get("video_id") or clip.get("vod_offset") is None:
        return None
    a, b = clip.get("vod_range") or (clip["vod_offset"], clip["vod_offset"] + clip["duration"])
    a, b = max(0.0, a - BEFORE), b + AFTER
    if b - a > MAX_WINDOW:  # beaucoup de doublons : on centre sur le clip principal
        mid = clip["vod_offset"] + clip["duration"] / 2
        a, b = max(0.0, mid - MAX_WINDOW / 2), mid + MAX_WINDOW / 2
    return a, b


class _Quiet:
    """Évite que yt-dlp écrive ses erreurs dans le journal : on affiche notre propre message."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def download_window(video_id: str, a: float, b: float, dest_dir: Path) -> Path:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import download_range_func

    opts = {
        "outtmpl": str(dest_dir / "window.%(ext)s"),
        "format": "best[height<=1080]/best",
        "quiet": True, "no_warnings": True, "noprogress": True,
        "download_ranges": download_range_func(None, [(a, b)]),
        "force_keyframes_at_cuts": False,
        "ffmpeg_location": ffmpeg(),
        "logger": _Quiet(),
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.twitch.tv/videos/{video_id}", download=True)
        return Path(ydl.prepare_filename(info))


def loudness(path: Path) -> np.ndarray:
    """Volume (dB) RATE fois par seconde, en mono."""
    import av

    out = []
    with av.open(str(path)) as c:
        if not c.streams.audio:
            return np.zeros(0)
        res = av.AudioResampler(format="flt", layout="mono", rate=16000)
        buf = np.zeros(0, dtype=np.float32)
        step = 16000 // RATE
        for frame in c.decode(audio=0):
            for f in res.resample(frame):
                buf = np.concatenate([buf, f.to_ndarray().reshape(-1)])
                while len(buf) >= step:
                    seg, buf = buf[:step], buf[step:]
                    out.append(20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-6))
    return np.array(out)


def _smooth(x: np.ndarray, seconds: float) -> np.ndarray:
    k = max(1, int(seconds * RATE))
    return np.convolve(x, np.ones(k) / k, mode="same") if len(x) else x


def _snap_start(t: float, chunks, lo: float, hi: float) -> float:
    """Début calé juste avant une phrase (une prise de parole) proche de t."""
    starts = [s for i, (s, _, _) in enumerate(chunks)
              if lo <= s <= hi and (i == 0 or s - chunks[i - 1][1] > 0.25)]
    return max(0.0, min(starts, key=lambda s: abs(s - t)) - 0.15) if starts else t


def _snap_end(t: float, chunks, lo: float, hi: float, dur: float) -> float:
    """Fin calée juste après une fin de phrase proche de t."""
    ends = [e for i, (_, e, _) in enumerate(chunks)
            if lo <= e <= hi and (i == len(chunks) - 1 or chunks[i + 1][0] - e > 0.25)]
    return min(dur, min(ends, key=lambda e: abs(e - t)) + 0.3) if ends else t


def best_range(path: Path, chunks, clip_start: float, clip_end: float, max_len: float) -> tuple[float, float, str]:
    """(début, fin, explication) dans la vidéo `path`. clip_start/clip_end = découpe du spectateur."""
    env = loudness(path)
    dur = len(env) / RATE if len(env) else clip_end
    if len(env) < RATE * 3:
        return clip_start, clip_end, "pas de son exploitable : découpe d'origine"
    e = _smooth(env, 1.0)
    base = float(np.median(e))
    # Le moment fort : le pic de volume, en préférant ce qui est dans (ou près de) la découpe du spectateur.
    t = np.arange(len(e)) / RATE
    # On ne cherche qu'autour du clip du spectateur : un peu avant (il a souvent cliqué trop tard), un peu après.
    near = (t >= clip_start - 8) & (t <= clip_end + 5)
    inside = (t >= clip_start) & (t <= clip_end)
    score = np.where(near, (e - base) + 3 * inside, -1e9)
    p = float(t[int(np.argmax(score))])
    peak_db = float(e[int(np.argmax(score))] - base)

    # On garde le clip du spectateur et on l'agrandit juste ce qu'il faut pour contenir le moment fort,
    # avec ~4 s de contexte avant et ~3 s de réaction après.
    start, end = clip_start, clip_end
    if peak_db < 4:
        why = "pas de moment fort net : découpe du spectateur"
    elif clip_start + 3 <= p <= clip_end - 2:
        why = "le moment fort est déjà dans le clip"
    else:
        start = min(start, max(0.0, p - 4.0))
        end = max(end, min(dur, p + 3.0))
        why = f"agrandi pour inclure le moment fort à {p - clip_start:+.0f} s du début du clip"
        if end - start > max_len:  # trop long : on rogne du côté opposé au moment fort
            if p < (start + end) / 2:
                end = start + max_len
            else:
                start = end - max_len

    # Réaction qui continue après la fin : on prolonge un peu (dans la limite de durée).
    while end < dur - 0.5 and end - start < max_len and e[min(int(end * RATE), len(e) - 1)] > base + 6:
        end += 0.5

    start = _snap_start(start, chunks, start - 2.0, start + 1.5)
    end = _snap_end(end, chunks, end - 1.5, end + 2.5, dur)
    if end - start > max_len:
        end = start + max_len
    if end - start < 5:
        return clip_start, clip_end, "découpe d'origine"
    return round(float(start), 2), round(float(end), 2), why
