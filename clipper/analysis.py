"""Lecture unique d'une vidéo pour toutes les analyses.

La détection de la cam, celle des sous-titres incrustés et la mesure du son relisaient
chacune la vidéo. On lit maintenant une seule fois :
  - 20 images réparties sur la vidéo (gardées en mémoire, en 1280 px de large) ;
  - le volume sonore, 10 mesures par seconde.
Les résultats sont gardés le temps du montage (clé = chemin + date de modification).
"""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

SAMPLES = 20
WORK_W = 1280
RATE = 10   # mesures de volume par seconde


def _key(path) -> tuple[str, float]:
    p = Path(path)
    return str(p), p.stat().st_mtime


@lru_cache(maxsize=3)
def _frames_cached(path: str, mtime: float) -> tuple[np.ndarray, ...]:
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    out = []
    for i in range(SAMPLES):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int((i + 0.5) * n / SAMPLES))
        ok, f = cap.read()
        if ok:
            h, w = f.shape[:2]
            out.append(cv2.resize(f, (WORK_W, round(h * WORK_W / w))))
    cap.release()
    return tuple(out)


def frames(path) -> list[np.ndarray]:
    """Images réparties sur la vidéo, en WORK_W de large (lues une seule fois)."""
    return list(_frames_cached(*_key(path)))


@lru_cache(maxsize=3)
def _loudness_cached(path: str, mtime: float) -> np.ndarray:
    import av

    out = []
    with av.open(path) as c:
        if not c.streams.audio:
            return np.zeros(0)
        res = av.AudioResampler(format="flt", layout="mono", rate=16000)
        step = 16000 // RATE
        buf = np.zeros(0, dtype=np.float32)
        for frame in c.decode(audio=0):
            for f in res.resample(frame):
                buf = np.concatenate([buf, f.to_ndarray().reshape(-1)])
                k = len(buf) // step
                if k:
                    blocks = buf[: k * step].reshape(k, step)
                    out.extend(20 * np.log10(np.sqrt((blocks ** 2).mean(axis=1)) + 1e-6))
                    buf = buf[k * step:]
    return np.array(out)


def loudness(path) -> np.ndarray:
    """Volume (dB), RATE mesures par seconde, en mono (calculé une seule fois)."""
    return _loudness_cached(*_key(path))
