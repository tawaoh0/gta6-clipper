"""Détection automatique de la facecam, refaite pour chaque clip.

1. On prend 16 images réparties sur le clip.
2. On y cherche tous les visages (YuNet, le détecteur de visages d'OpenCV).
3. On regroupe les visages qui restent au même endroit d'une image à l'autre.
4. Pour chaque groupe, on cherche le cadre autour (lignes droites continues sur
   l'image médiane : le contenu qui bouge s'efface, le cadre de la cam reste net).
5. On garde le groupe dont le fond reste immobile : la chambre du streamer ne bouge
   pas pendant le clip, alors que le jeu ou une vidéo regardée change sans arrêt.
   C'est ce qui évite de prendre un personnage du jeu ou d'un trailer pour la cam.
6. Si la « cam » occupe plus d'un tiers de l'écran, le streamer est en plein écran :
   on ne découpe rien, on garde le plan entier.

Renvoie (rectangle en fractions [x, y, l, h] ou None, explication).
"""
from pathlib import Path

import cv2
import numpy as np

from .config import RES

MODEL = RES / "models" / "face_detection_yunet_2023mar.onnx"
SAMPLES = 16
WORK_W = 1280
MIN_PRESENCE = 0.7   # visage au même endroit dans au moins 70 % des images (sinon la mise en page change pendant le clip)
MAX_BG_MOTION = 20   # au-delà, le fond bouge trop pour être une webcam
MIN_FACE_MOTION = 3  # en dessous, le visage est figé (miniature, affiche)
FULLSCREEN = 0.35    # cam > 35 % de l'écran = streamer en plein écran


def _frames(path: Path) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    out = []
    for i in range(SAMPLES):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int((i + 0.5) * n / SAMPLES))
        ok, f = cap.read()
        if ok:
            h, w = f.shape[:2]
            out.append(cv2.resize(f, (WORK_W, round(h * WORK_W / w))))
    cap.release()
    return out


def _face_groups(frames) -> list[dict]:
    H, W = frames[0].shape[:2]
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (W, H), score_threshold=0.6)
    hits = []  # (image, cx, cy, taille)
    for i, f in enumerate(frames):
        _, faces = det.detect(f)
        for x, y, w, h, *_ in faces if faces is not None else []:
            hits.append((i, x + w / 2, y + h / 2, (w + h) / 2))

    groups = []
    for i, cx, cy, s in sorted(hits, key=lambda h: -h[3]):
        for g in groups:
            if np.hypot(cx - g["cx"], cy - g["cy"]) < g["s"] * 0.6 and 0.6 < s / g["s"] < 1.6:
                g["hits"].append((i, cx, cy, s))
                break
        else:
            groups.append({"cx": cx, "cy": cy, "s": s, "hits": [(i, cx, cy, s)]})
    for g in groups:
        arr = np.array(g["hits"])
        g["cx"], g["cy"], g["s"] = (float(v) for v in np.median(arr[:, 1:], axis=0))
        g["presence"] = len(set(arr[:, 0])) / len(frames)
    return [g for g in groups if g["presence"] >= MIN_PRESENCE]


def _line_strength(edges: np.ndarray, vertical: bool, pos: int, a: int, b: int) -> float:
    """Part des pixels de bord sur un segment (avec ±1 px de tolérance)."""
    block = edges[a:b, max(pos - 1, 0) : pos + 2] if vertical else edges[max(pos - 1, 0) : pos + 2, a:b]
    if block.size == 0:
        return 0.0
    return float(block.max(axis=1 if vertical else 0).mean())


def _find_border(edges, vertical, start, stop, a, b, thresh=0.75) -> int | None:
    """Premier bord continu en partant du visage vers l'extérieur."""
    step = 1 if stop > start else -1
    for pos in range(int(start), int(stop), step):
        if _line_strength(edges, vertical, pos, a, b) >= thresh:
            return pos
    return None


def _cam_rect(edges, cx, cy, s) -> tuple[float, float, float, float]:
    H, W = edges.shape
    ya, yb = int(max(cy - s * 0.9, 0)), int(min(cy + s * 1.2, H))
    xa, xb = int(max(cx - s * 1.0, 0)), int(min(cx + s * 1.0, W))
    left = _find_border(edges, True, max(cx - s * 0.9, 0), 0, ya, yb)
    right = _find_border(edges, True, min(cx + s * 0.9, W - 1), W, ya, yb)
    top = _find_border(edges, False, max(cy - s * 0.9, 0), 0, xa, xb)
    bottom = _find_border(edges, False, min(cy + s * 1.1, H - 1), H, xa, xb)

    # Pas de bord trouvé : bord de l'image, ou marge raisonnable autour du visage.
    x0 = left + 2 if left is not None else max(cx - s * 2.2, 0)
    x1 = right - 1 if right is not None else min(cx + s * 2.2, W)
    y0 = top + 2 if top is not None else max(cy - s * 1.6, 0)
    y1 = bottom - 1 if bottom is not None else min(cy + s * 1.9, H)

    # Une webcam fait entre 0,9:1 et 1,9:1 : si le cadre est bien plus large,
    # un bord vertical a été raté (il se confond avec le jeu). On le cherche là où il devrait être.
    h = y1 - y0
    if (x1 - x0) > h * 1.95:
        anchor_left = left is not None or x0 <= 1
        best, best_val = None, 0.0
        for k in range(int(h * 0.9), int(h * 1.9)):
            pos = int(x0 + k) if anchor_left else int(x1 - k)
            if 0 < pos < W:
                v = _line_strength(edges, True, pos, int(y0), int(y1))
                if v > best_val + 0.02:
                    best, best_val = pos, v
        if best is not None and best_val >= 0.45:
            x0, x1 = (x0, best - 1) if anchor_left else (best + 2, x1)
        elif anchor_left:
            x1 = min(x0 + h * 16 / 9, W)
        else:
            x0 = max(x1 - h * 16 / 9, 0)

    if x1 - x0 < s * 1.8 or y1 - y0 < s * 1.6:  # cadre trop serré : marge autour du visage
        x0, x1 = max(cx - s * 2.2, 0), min(cx + s * 2.2, W)
        y0, y1 = max(cy - s * 1.6, 0), min(cy + s * 1.9, H)
    return max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)


def _background_motion(gray_stack, rect, cx, cy, s) -> float:
    """Mouvement du fond juste autour de la tête (le mur derrière le streamer).

    On ne regarde que les côtés et le dessus de la tête, à l'intérieur du cadre :
    les bras et le buste bougent, le mur non.
    """
    rx0, ry0, rx1, ry1 = (int(v) for v in rect)
    x0, x1 = max(int(cx - s * 2.0), rx0), min(int(cx + s * 2.0), rx1)
    y0, y1 = max(int(cy - s * 1.5), ry0), min(int(cy + s * 0.2), ry1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return 99.0
    std = gray_stack[:, y0:y1, x0:x1].std(axis=0)
    mask = np.ones_like(std, dtype=bool)
    hx0, hx1 = int(cx - s * 0.85) - x0, int(cx + s * 0.85) - x0
    mask[:, max(hx0, 0) : max(hx1, 0)] = False  # la tête elle-même
    vals = std[mask]
    return float(np.median(vals)) if vals.size > 50 else 99.0


def _face_motion(gray_stack, cx, cy, s) -> float:
    """Le visage d'un vrai streamer bouge un peu ; une miniature ou une affiche, non."""
    H, W = gray_stack.shape[1:]
    x0, x1 = max(int(cx - s * 0.5), 0), min(int(cx + s * 0.5), W)
    y0, y1 = max(int(cy - s * 0.5), 0), min(int(cy + s * 0.5), H)
    return float(gray_stack[:, y0:y1, x0:x1].std(axis=0).mean())


def detect(path: Path, debug_png: Path | None = None) -> tuple[list[float] | None, str]:
    frames = _frames(path)
    if len(frames) < 4:
        return None, "clip trop court"
    H, W = frames[0].shape[:2]
    groups = _face_groups(frames)
    if not groups:
        return None, "aucun visage stable"

    median = np.median(np.stack(frames), axis=0).astype(np.uint8)
    edges = (cv2.Canny(cv2.cvtColor(median, cv2.COLOR_BGR2GRAY), 40, 120) > 0).astype(np.uint8)
    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]).astype(np.float32)

    best = None
    for g in sorted(groups, key=lambda g: -g["presence"])[:5]:
        rect = _cam_rect(edges, g["cx"], g["cy"], g["s"])
        g["rect"], g["motion"] = rect, _background_motion(gray, rect, g["cx"], g["cy"], g["s"])
        g["alive"] = _face_motion(gray, g["cx"], g["cy"], g["s"])
        g["score"] = g["presence"] / (1 + g["motion"] / 6)
        ok = g["motion"] <= MAX_BG_MOTION and g["alive"] >= MIN_FACE_MOTION
        if ok and (best is None or g["score"] > best["score"]):
            best = g

    # Secours pour les streamers qui bougent beaucoup (le fond paraît agité) :
    # un visage vivant, présent presque tout le clip au même endroit et hors du centre
    # de l'écran, c'est une facecam. Un personnage du jeu ne reste jamais si longtemps figé.
    if best is None:
        for g in sorted((g for g in groups if "rect" in g), key=lambda g: (-g["presence"], g["motion"])):
            off_center = abs(g["cx"] / W - 0.5) > 0.2 or abs(g["cy"] / H - 0.5) > 0.25
            if g["presence"] >= 0.8 and g["alive"] >= MIN_FACE_MOTION and off_center:
                best = g
                break

    if debug_png:
        dbg = median.copy()
        for g in groups:
            if "rect" in g:
                color = (0, 255, 0) if g is best else (0, 0, 255)
                x0, y0, x1, y1 = (int(v) for v in g["rect"])
                cv2.rectangle(dbg, (x0, y0), (x1, y1), color, 3)
                cv2.putText(dbg, f"p{g['presence']:.2f} m{g['motion']:.0f} a{g['alive']:.0f}", (x0 + 6, y0 + 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.1, color, 3)
        cv2.imwrite(str(debug_png), dbg)

    if best is None:
        return None, "visages trouvés mais aucun sur un fond immobile (jeu ou vidéo)"
    x0, y0, x1, y1 = best["rect"]
    if (x1 - x0) * (y1 - y0) > FULLSCREEN * W * H:
        return None, "streamer en plein écran, plan entier gardé"
    rect = [round(v, 4) for v in (x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H)]
    return rect, f"facecam détectée {rect}"
