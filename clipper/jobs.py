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

    cam_mode = opts.get("cam", "keep")          # "keep" | "none" | [x, y, w, h] (fractions de l'image)
    if cam_mode == "keep":
        facecam = json.loads(r["facecam"]) if r["facecam"] else None
    elif cam_mode == "none":
        facecam = None
    else:
        x, y, w, h = (min(max(float(v), 0.0), 1.0) for v in cam_mode)
        if w < 0.03 or h < 0.03:
            raise RuntimeError("Le cadre de la cam est trop petit.")
        facecam = [x, y, min(w, 1 - x), min(h, 1 - y)]
    cam_position = opts.get("cam_position") if opts.get("cam_position") in ("top", "bottom") else (r["cam_position"] or "top")
    hook = str(opts.get("hook", r["hook"] or ""))[:120]

    out = Path(r["video_path"])
    tmp = out.with_name(out.stem + ".new.mp4")
    render(src, tmp, r["streamer"], json.loads(r["chunks"] or "[]"), facecam=facecam,
           zoom=cfg["pipeline"]["zoom"], cam_position=cam_position, start=start, end=end,
           hook=hook, hook_seconds=cfg["pipeline"].get("hook_seconds", 2.5))
    tmp.replace(out)
    (THUMBS / f"{clip_id}.jpg").unlink(missing_ok=True)
    journal.update(conn, clip_id, trim_start=start, trim_end=end if end < dur - 0.05 else None,
                   facecam=json.dumps(facecam) if facecam else None, cam_position=cam_position,
                   hook=hook, ready_at=time.time())
    return "Vidéo remontée"


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
