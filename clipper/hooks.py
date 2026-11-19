"""Phrases d'accroche tirées de ce qui se dit vraiment dans le clip (sans IA payante).

On reconstitue les phrases à partir des sous-titres, puis on note chacune :
  - elle est dite pendant (ou juste avant) le moment fort, quand le son monte ;
  - c'est une exclamation ou une question (« Attends, il a fait quoi ?! ») ;
  - elle est courte (3 à 9 mots), lisible en 2 secondes ;
  - elle contient un mot qui accroche (non, quoi, regarde, jamais…).
La meilleure devient l'accroche, entre guillemets comme une citation du streamer.
"""
import re
from pathlib import Path

import numpy as np

PUNCHY = {
    "non", "quoi", "regarde", "regardez", "attends", "jamais", "incroyable", "oh", "wesh", "hein", "pourquoi",
    "comment", "sérieux", "vraiment", "impossible", "dingue", "fou", "folle", "enfin", "trop", "mort", "mdr",
    "bordel", "allez", "go", "vite", "arrête", "stop", "aide", "help", "omg", "ouah", "waouh", "wow", "eh",
}
FILLER = re.compile(r"^(euh|bah|ben|donc|et|mais|alors|ouais|bon)\b[\s,]*", re.I)


def sentences(words) -> list[tuple[float, float, str, float]]:
    """Regroupe les mots en phrases (coupure sur la ponctuation ou un silence).
    Renvoie (début, fin, texte, confiance moyenne de la transcription)."""
    out, cur, probs, t0, t1 = [], [], [], None, None

    def flush():
        out.append((t0, t1, " ".join(cur), sum(probs) / len(probs)))

    for w in words:
        a, b, text = w[0], w[1], w[2]
        prob = w[3] if len(w) > 3 else 0.9
        if cur and (a - t1 > 0.7):
            flush()
            cur, probs = [], []
        if not cur:
            t0 = a
        cur.append(text)
        probs.append(prob)
        t1 = b
        if re.search(r"[.!?…]$", text):
            flush()
            cur, probs = [], []
    if cur:
        flush()
    return out


def _clean(text: str) -> str:
    text = re.sub(r"\s+([,.!?…])", r"\1", text.strip())
    text = re.sub(r"\s+'", "'", text)
    for _ in range(2):
        text = FILLER.sub("", text)
    text = text.strip(" ,.")
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _peak(source: Path | None, start: float, end: float) -> float | None:
    if not source:
        return None
    try:
        from .moment import RATE, _smooth, loudness
        e = _smooth(loudness(source), 1.0)
        a, b = int(start * RATE), int(end * RATE)
        if b - a < RATE:
            return None
        return (a + int(np.argmax(e[a:b]))) / RATE
    except Exception:
        return None


def candidates(chunks, start: float, end: float, source: Path | None = None, limit: int = 6) -> list[str]:
    """Les meilleures accroches possibles pour l'extrait [start, end], de la meilleure à la moins bonne."""
    sents = [s for s in sentences(chunks or []) if s[1] > start and s[0] < end]
    if not sents:
        return []
    peak = _peak(source, start, end)
    length = max(end - start, 1.0)
    scored = []
    for a, b, raw, prob in sents:
        if prob < 0.55:   # transcription peu sûre : la phrase risque d'être fausse
            continue
        text = _clean(raw)
        words = text.split()
        if not 2 <= len(words) <= 12 or len(text) > 70:
            continue
        score = 0.0
        if re.search(r"[!?]", text):
            score += 2.0
        lw = {re.sub(r"[^\wàâçéèêëîïôûùüœ']", "", w.lower()) for w in words}
        score += 1.2 * min(len(lw & PUNCHY), 2)
        score += 1.0 if 3 <= len(words) <= 9 else 0.0
        if peak is not None:
            # Dite pendant la montée du son, ou dans les 4 s qui précèdent : c'est la réaction.
            dist = 0 if a - 4 <= peak <= b + 1 else min(abs(peak - a), abs(peak - b))
            score += 2.5 * max(0.0, 1 - dist / (length / 2))
        score += 1.5 * (prob - 0.7)
        score -= 0.4 * max(0, len(words) - 9) + 0.05 * max(0, len(text) - 45)
        scored.append((score, text))
    seen, out = set(), []
    for _, text in sorted(scored, key=lambda x: -x[0]):
        key = text.lower()
        if key not in seen:
            seen.add(key)
            out.append(f"« {text} »")
        if len(out) >= limit:
            break
    return out
