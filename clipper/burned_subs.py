"""Détecte les sous-titres déjà incrustés dans le stream (ceux que le streamer affiche en direct).

Idée : on cherche du texte (détecteur PP-OCR d'OpenCV) sur ~20 images du clip. Des sous-titres en direct :
  - reviennent souvent, toujours à la même hauteur ;
  - sont plutôt centrés horizontalement ;
  - changent de largeur d'une image à l'autre (le texte change), contrairement à un logo ou un pseudo fixe.
Renvoie la bande (haut, bas) en fractions de la hauteur, ou None.
"""
from pathlib import Path

import cv2
import numpy as np

from .config import RES

MODEL = RES / "models" / "text_detection_en_ppocrv3_2023may.onnx"
SAMPLES = 20
IN_W, IN_H = 640, 352          # multiples de 32 demandés par le modèle


def _model():
    m = cv2.dnn_TextDetectionModel_DB(str(MODEL))
    m.setBinaryThreshold(0.3).setPolygonThreshold(0.5).setMaxCandidates(200).setUnclipRatio(2.0)
    m.setInputParams(1.0 / 255.0, (IN_W, IN_H), (122.67891434, 116.66876762, 104.00698793))
    return m


def _frames(path: Path, n: int):
    from .analysis import frames
    yield from frames(path)


def detect(path: Path) -> tuple[tuple[float, float] | None, str]:
    if not MODEL.exists():
        return None, "modèle de détection de texte absent"
    model = _model()
    boxes = []  # (frame, y0, y1, x_center, width) en fractions
    frames = 0
    for f in _frames(path, SAMPLES):
        frames += 1
        img = cv2.resize(f, (IN_W, IN_H))
        polys, _ = model.detect(img)
        for p in polys:
            p = np.array(p, dtype=float)
            x0, x1 = p[:, 0].min() / IN_W, p[:, 0].max() / IN_W
            y0, y1 = p[:, 1].min() / IN_H, p[:, 1].max() / IN_H
            if (x1 - x0) > 0.05 and 0.015 < (y1 - y0) < 0.14:
                boxes.append((frames, y0, y1, (x0 + x1) / 2, x1 - x0))
    if frames < 6 or not boxes:
        return None, "pas de sous-titres incrustés"

    # Texte fixe (interface du jeu, pseudo, compteurs) : même place et même largeur sur la plupart des images.
    def same(a, b):
        return abs(a[3] - b[3]) < 0.02 and abs(a[1] - b[1]) < 0.012 and abs(a[4] - b[4]) < 0.08 * max(a[4], b[4])
    moving = []
    for b in boxes:
        frames_with_twin = {o[0] for o in boxes if o[0] != b[0] and same(b, o)}
        if len(frames_with_twin) < 0.5 * frames:
            moving.append(b)
    boxes = moving
    if not boxes:
        return None, "pas de sous-titres incrustés"

    # On regroupe les textes par hauteur (tranches de 2 %) et on garde la tranche la plus « sous-titre ».
    best = None
    for c in np.arange(0.0, 1.0, 0.02):
        group = [b for b in boxes if b[1] <= c + 0.01 and b[2] >= c - 0.01 and abs(b[3] - 0.5) < 0.22]
        seen = {b[0] for b in group}
        presence = len(seen) / frames
        if presence < 0.25:
            continue
        widths = [b[4] for b in group]
        changing = np.std(widths) / (np.mean(widths) + 1e-6)
        if changing < 0.12:   # même largeur partout : logo ou texte fixe, pas des sous-titres
            continue
        score = presence + min(changing, 0.6)
        if not best or score > best[0]:
            best = (score, group, presence)
    if not best:
        return None, "pas de sous-titres incrustés"
    group = best[1]
    y0 = float(np.percentile([b[1] for b in group], 5)) - 0.015
    y1 = float(np.percentile([b[2] for b in group], 95)) + 0.015
    band = (max(0.0, y0), min(1.0, y1))
    return band, f"sous-titres du stream trouvés ({band[0]:.0%}–{band[1]:.0%} de la hauteur, {best[2]:.0%} des images)"
