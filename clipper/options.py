"""Options de montage choisies dans les réglages → paramètres « extras » de media.render."""
from pathlib import Path


import copy

CLIP_OPTIONS = ("emojis", "sfx", "watermark", "sub_lang")


def defaults(cfg: dict) -> dict:
    """Options d'un clip quand rien n'a été choisi pour lui : celles des réglages."""
    p = cfg["pipeline"]
    return {"emojis": bool(p.get("emojis")), "sfx": bool(p.get("sfx")),
            "watermark": bool((p.get("watermark") or {}).get("on")), "sub_lang": p.get("sub_lang", "fr")}


def apply(cfg: dict, clip_opts: dict | None) -> dict:
    """Réglages avec les options choisies pour ce clip par-dessus (le filigrane garde son texte/logo)."""
    cfg = copy.deepcopy(cfg)
    p = cfg["pipeline"]
    for k, v in (clip_opts or {}).items():
        if k == "watermark":
            p["watermark"] = {**(p.get("watermark") or {}), "on": bool(v)}
        elif k == "sub_lang" and v in ("fr", "en", "both"):
            p["sub_lang"] = v
        elif k in ("emojis", "sfx"):
            p[k] = bool(v)
    return cfg


def make_extras(cfg: dict, words, words_en, source: Path | None, start: float, end: float) -> dict:
    p = cfg["pipeline"]
    extras = {"sub_lang": p.get("sub_lang", "fr"), "subs_en": words_en or []}
    if p.get("emojis"):
        from .analysis import RATE, loudness
        from .effects import pick_emojis
        loud = None
        try:
            loud = loudness(source) if source else None
        except Exception:
            pass
        extras["emojis"] = pick_emojis(words, start, end, loud, RATE)
    if p.get("sfx"):
        extras["sfx"] = {"on": True, "volume": p.get("sfx_volume", 1.0)}
    wm = p.get("watermark") or {}
    if wm.get("on"):
        extras["watermark"] = wm
    return extras
