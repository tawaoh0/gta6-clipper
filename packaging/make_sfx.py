"""Fabrique les bruitages de l'app par calcul (aucun son tiré d'Internet : ils nous appartiennent).

  whoosh.wav : souffle qui monte puis retombe (bruit filtré dont la hauteur balaie)
  boom.wav   : impact grave (sinus qui descend + attaque de bruit), pour les gros moments
  pop.wav    : petit « pop » court, pour l'apparition des emojis
Usage : uv run python packaging/make_sfx.py
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "sfx"
rng = np.random.default_rng(7)


def save(name: str, x: np.ndarray):
    x = x / (np.abs(x).max() + 1e-9) * 0.89
    data = (x * 32767).astype(np.int16)
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def lowpass(x, cutoff):
    """Filtre passe-bas à une pôle, coupure variable (tableau ou nombre)."""
    cutoff = np.broadcast_to(cutoff, x.shape)
    a = np.exp(-2 * np.pi * cutoff / SR)
    y, prev = np.empty_like(x), 0.0
    for i in range(len(x)):
        prev = (1 - a[i]) * x[i] + a[i] * prev
        y[i] = prev
    return y


def whoosh(d=0.55):
    t = np.linspace(0, 1, int(SR * d))
    env = np.sin(np.pi * t) ** 1.6
    cut = 400 + 5200 * np.sin(np.pi * t) ** 2
    noise = rng.standard_normal(len(t))
    band = lowpass(noise, cut) - lowpass(noise, cut * 0.25)
    return band * env


def boom(d=0.9):
    n = int(SR * d)
    t = np.arange(n) / SR
    freq = 38 + 90 * np.exp(-t * 9)
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 4.2)
    click = lowpass(rng.standard_normal(n), 1800) * np.exp(-t * 45) * 0.6
    return np.tanh(2.2 * (body + click))


def pop(d=0.12):
    n = int(SR * d)
    t = np.arange(n) / SR
    freq = 900 * np.exp(-t * 18) + 300
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 38)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    save("whoosh.wav", whoosh())
    save("boom.wav", boom())
    save("pop.wav", pop())
    print("bruitages écrits dans", OUT)
