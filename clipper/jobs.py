"""Tâches lancées depuis l'interface et suivies en arrière-plan :
remonter un clip ajusté, envoyer un brouillon TikTok, connecter TikTok, chercher une mise à jour."""
import json
import threading
import time
from pathlib import Path

from . import __version__
from . import db as journal
from .config import DATA, load_config

JOBS: dict[str, dict] = {}      # clé -> {"state": "running"|"done"|"error", "message": ...}
_lock = threading.Lock()
THUMBS = DATA / "thumbs"


def _start(key: str, fn, *args) -> bool:
    with _lock:
        if JOBS.get(key, {}).get("state") == "running":
            return False
        JOBS[key] = {"state": "running", "message": "", "at": time.time()}

    def wrapper():
        try:
            msg = fn(*args) or ""
            JOBS[key] = {"state": "done", "message": msg, "at": time.time()}
        except BaseException as e:  # SystemExit compris (messages d'erreur lisibles de tiktok.py)
            JOBS[key] = {"state": "error", "message": str(e) or e.__class__.__name__, "at": time.time()}

    threading.Thread(target=wrapper, daemon=True).start()
    return True


def snapshot() -> dict:
    return dict(JOBS)


# ---------- remonter un clip ----------

def can_edit(row) -> bool:
    return bool(row["source_path"]) and Path(row["source_path"]).exists()


def rerender(clip_id: str, opts: dict) -> bool:
    return _start(f"render:{clip_id}", _rerender, clip_id, opts)


def _rerender(clip_id: str, opts: dict) -> str:
    from .media import probe, render

    cfg = load_config()
    conn = journal.connect()
    r = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if not r or not can_edit(r):
        raise RuntimeError("La vidéo d'origine n'est plus disponible pour ce clip.")
    src = Path(r["source_path"])
    dur = probe(src)["duration"]
    start = max(0.0, min(float(opts.get("start", r["trim_start"] or 0)), dur - 1))
    end = opts.get("end", r["trim_end"])
    end = min(float(end), dur) if end else dur
    if end - start < 1:
        raise RuntimeError("Le clip doit durer au moins 1 seconde.")

    cam_mode = opts.get("cam", "keep")          # "keep" | "auto" | "none" | [x, y, w, h] (fractions de l'image)
    if cam_mode == "keep":
        facecam = json.loads(r["facecam"]) if r["facecam"] else None
    elif cam_mode == "none":
        facecam = None
    elif cam_mode == "auto":
        from .facecam import detect as find_cam
        facecam, _ = find_cam(src)
    else:
        x, y, w, h = (min(max(float(v), 0.0), 1.0) for v in cam_mode)
        if w < 0.03 or h < 0.03:
            raise RuntimeError("Le cadre de la cam est trop petit.")
        facecam = [x, y, min(w, 1 - x), min(h, 1 - y)]
    cam_position = opts.get("cam_position") if opts.get("cam_position") in ("top", "bottom") else (r["cam_position"] or "top")
    hook = str(opts.get("hook", r["hook"] or ""))[:120]
    unknown = r["burned_band"] is None or not json.loads(r["burned_band"])
    wants = opts.get("burned") == "remove" or (opts.get("burned") is None and cfg["pipeline"].get("remove_burned_subs", True))
    if unknown and wants:
        from .burned_subs import detect as find_burned  # ancien clip : on cherche maintenant
        found, _ = find_burned(src)
        journal.update(conn, clip_id, burned_band=json.dumps(found or False))
        r = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    band = json.loads(r["burned_band"]) if r["burned_band"] else None
    if opts.get("burned") == "keep":
        band = None
        journal.update(conn, clip_id, burned_band=json.dumps(False))

    zoom_at = None
    if opts.get("zoom", cfg["pipeline"].get("reaction_zoom", True)):
        from .hooks import _peak
        zoom_at = _peak(src, start, end)

    # Options de ce clip (emojis, bruitages, filigrane, langue) : celles demandées, sinon celles
    # avec lesquelles il a été monté, sinon les réglages.
    from . import options as clip_options
    chosen = {**clip_options.defaults(cfg), **(json.loads(r["options"]) if r["options"] else {}),
              **{k: v for k, v in (opts.get("options") or {}).items() if k in clip_options.CLIP_OPTIONS}}
    cfg = clip_options.apply(cfg, chosen)
    journal.update(conn, clip_id, options=json.dumps(chosen))

    words = json.loads(r["chunks"] or "[]")
    words_en = json.loads(r["words_en"]) if r["words_en"] else []
    if cfg["pipeline"].get("sub_lang", "fr") in ("en", "both") and not words_en and words:
        from .media import translate_en
        words_en = translate_en(src, cfg["pipeline"]["language"])
        journal.update(conn, clip_id, words_en=json.dumps(words_en))
    from .options import make_extras
    extras = make_extras(cfg, words, words_en, src, start, end)

    out = Path(r["video_path"])
    tmp = out.with_name(out.stem + ".new.mp4")
    render(src, tmp, r["streamer"], json.loads(r["chunks"] or "[]"), facecam=facecam,
           zoom=cfg["pipeline"]["zoom"], cam_position=cam_position, start=start, end=end,
           hook=hook, hook_seconds=cfg["pipeline"].get("hook_seconds", 2.5), burned_band=band,
           sub_style=cfg["pipeline"].get("sub_style"), zoom_at=zoom_at, extras=extras)
    tmp.replace(out)
    (THUMBS / f"{clip_id}.jpg").unlink(missing_ok=True)
    # La légende commence par l'accroche : si elle n'a pas été réécrite à la main, on la met à jour.
    caption = r["caption"] or ""
    first, _, rest = caption.partition("\n")
    if hook and first.strip() in (r["hook"] or "", r["title"] or "") and first.strip() != hook:
        caption = hook + ("\n" + rest if rest else "")
        Path(r["video_path"]).with_suffix(".txt").write_text(caption, encoding="utf-8")
    journal.update(conn, clip_id, caption=caption, trim_start=start, trim_end=end if end < dur - 0.05 else None,
                   facecam=json.dumps(facecam) if facecam else None, cam_position=cam_position,
                   hook=hook, ready_at=time.time(), zoom_at=zoom_at)
    return "Vidéo remontée"


def hook_ideas(clip_id: str, start: float | None = None, end: float | None = None) -> list[str]:
    """Accroches possibles tirées des paroles de l'extrait choisi."""
    from .hooks import candidates

    r = journal.connect().execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if not r:
        return []
    chunks = json.loads(r["chunks"] or "[]")
    s = start if start is not None else (r["trim_start"] or 0.0)
    e = end if end is not None else (r["trim_end"] or (chunks[-1][1] if chunks else 0.0))
    src = Path(r["source_path"]) if can_edit(r) else None
    ideas = candidates(chunks, s, e, src)
    if r["title"] and f"« {r['title']} »" not in ideas:
        ideas.append(r["title"][:70])
    return ideas


# ---------- TikTok ----------

def tiktok_connected() -> bool:
    from .tiktok import TOKEN_FILE

    return TOKEN_FILE.exists()


def tiktok_login() -> bool:
    def go():
        from .tiktok import login

        cfg = load_config()
        if not cfg["tiktok"]["client_key"] or not cfg["tiktok"]["client_secret"]:
            raise RuntimeError("Renseigne d'abord la clé client et le secret TikTok.")
        login(cfg)
        return "Compte TikTok connecté"
    return _start("tiktok:login", go)


def tiktok_send(clip_id: str) -> bool:
    def go():
        from .tiktok import upload_draft

        conn = journal.connect()
        r = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not r or not r["video_path"] or not Path(r["video_path"]).exists():
            raise RuntimeError("La vidéo n'existe plus.")
        pid = upload_draft(load_config(), Path(r["video_path"]))
        journal.update(conn, clip_id, status="sent", publish_id=pid, sent_at=time.time())
        return "Brouillon envoyé : ouvre l'app TikTok, onglet Boîte de réception"
    return _start(f"tiktok:{clip_id}", go)


# ---------- plusieurs parties ----------

def make_parts(clip_id: str, seconds: float) -> bool:
    from .parts import split
    return _start(f"parts:{clip_id}", split, clip_id, seconds)


# ---------- Top du jour ----------

def make_top(day: str | None = None) -> bool:
    from .compile import build
    return _start("top", build, day)


# ---------- YouTube ----------

def youtube_login() -> bool:
    def go():
        from .youtube import channel_name, login
        login(load_config())
        name = channel_name()
        return f"Chaîne YouTube connectée{f' : {name}' if name else ''}"
    return _start("youtube:login", go)


def youtube_send(clip_id: str, text: str, privacy: str | None) -> bool:
    def go():
        from .youtube import upload
        conn = journal.connect()
        r = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not r or not r["video_path"] or not Path(r["video_path"]).exists():
            raise RuntimeError("La vidéo n'existe plus.")
        vid = upload(load_config(), Path(r["video_path"]), text, privacy)
        journal.update(conn, clip_id, youtube_id=vid)
        return vid
    return _start(f"youtube:{clip_id}", go)


# ---------- mises à jour ----------

UPDATE = {"checked_at": 0, "latest": None, "url": None, "notes": ""}


def _version(v: str) -> tuple:
    return tuple(int(x) for x in v.lstrip("v").split(".")[:3] if x.isdigit())


def check_update(repo: str):
    """Regarde la dernière version publiée sur GitHub (dépôt public des versions)."""
    import sys

    import requests

    if not repo:
        return
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=10,
                         headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return
        rel = r.json()
        tag = rel.get("tag_name", "")
        want = ".dmg" if sys.platform == "darwin" else "setup.exe"
        asset = next((a["browser_download_url"] for a in rel.get("assets", []) if a["name"].endswith(want)), None)
        UPDATE.update(checked_at=time.time(), notes=(rel.get("body") or "")[:600],
                      latest=tag.lstrip("v") if tag and _version(tag) > _version(__version__) else None,
                      url=asset or rel.get("html_url"))
    except Exception:
        pass


def update_loop(repo: str):
    while True:
        check_update(repo)
        time.sleep(6 * 3600)
