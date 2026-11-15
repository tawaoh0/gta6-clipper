"""Un passage complet : détection Twitch → téléchargement → sous-titres → montage → légende."""
import time
from datetime import datetime
from pathlib import Path

from filelock import FileLock, Timeout

from . import db as journal
from .config import DATA, OUTPUT, WORK, load_config, save_config
from .tools import notify

LOCK = DATA / "run.lock"


def log(msg: str):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def is_running() -> bool:
    try:
        with FileLock(LOCK, timeout=0):
            return False
    except Timeout:
        return True


def find_facecam(cfg, login: str, src: Path):
    """Position forcée dans les réglages, sinon détection automatique, sinon None (fond flouté)."""
    manual = cfg["streamers"].get("facecam", {}).get(login.lower())
    if manual:
        return manual
    if cfg["pipeline"].get("facecam", "auto") != "auto":
        return None
    from .facecam import detect

    rect, why = detect(src)
    log(f"   {why}" + ("" if rect else " → fond flouté"))
    return rect


def prepare(cfg, conn, clip) -> bool:
    from .caption import write_caption
    from .media import download, render, transcribe

    p = cfg["pipeline"]
    day_dir = OUTPUT / datetime.now().strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    work = WORK / clip["id"]
    work.mkdir(parents=True, exist_ok=True)
    try:
        log(f"⬇  {clip['broadcaster_name']} — « {clip['title']} » ({clip['view_count']} vues, {clip['score']}/h)")
        src = download(clip["url"], work)
        transcript, chunks = "", []
        if p["subtitles"]:
            log("   sous-titres… (le premier passage télécharge le modèle, ~500 Mo, une seule fois)")
            transcript, chunks = transcribe(src, p["whisper_model"], p["language"])
        facecam = find_facecam(cfg, clip["login"], src)
        out = day_dir / f"{clip['login']}_{clip['id'][:40]}.mp4"
        render(src, out, clip["broadcaster_name"], chunks, facecam=facecam, zoom=p["zoom"],
               cam_position=p.get("facecam_position", "top"))
        caption = write_caption(cfg, clip, transcript)
        out.with_suffix(".txt").write_text(caption, encoding="utf-8")
        status = "ready" if cfg["tiktok"]["enabled"] else "manual"
        journal.insert(conn, clip, status, video_path=str(out), caption=caption, ready_at=time.time())
        log(f"🎬 monté : {out.name}")
        return True
    except Exception as e:
        journal.insert(conn, clip, "error", error=f"{type(e).__name__}: {e}")
        log(f"❌ échec : {e}")
        return False
    finally:
        for f in work.glob("*"):
            f.unlink(missing_ok=True)
        work.rmdir()


def send_ready(cfg, conn) -> int:
    from .tiktok import upload_draft

    room = cfg["pipeline"]["drafts_per_day"] - journal.sent_last_24h(conn)
    if room <= 0:
        log("⏸  limite de brouillons TikTok atteinte pour 24 h, on garde les vidéos prêtes.")
        return 0
    rows = conn.execute(
        "SELECT * FROM clips WHERE status = 'ready' ORDER BY score DESC LIMIT ?", (room,)
    ).fetchall()
    sent = 0
    for r in rows:
        try:
            pid = upload_draft(cfg, Path(r["video_path"]))
            journal.update(conn, r["id"], status="sent", publish_id=pid, sent_at=time.time())
            sent += 1
            log(f"📤 brouillon envoyé sur TikTok : {r['streamer']}")
        except Exception as e:
            log(f"❌ envoi TikTok échoué : {e}")
            if "spam_risk" in str(e) or "401" in str(e):
                break
    if sent:
        notify("GTA6 Clipper", f"{sent} brouillon(s) dans ta boîte TikTok.")
    return sent


def run():
    cfg = load_config()
    try:
        lock = FileLock(LOCK, timeout=0)
        lock.acquire()
    except Timeout:
        log("Un passage est déjà en cours, on saute celui-ci.")
        return
    try:
        cfg["last_run_at"] = time.time()
        save_config(cfg)
        from .twitch import find_candidates

        conn = journal.connect()
        limit = time.time() - cfg["pipeline"]["ready_expiry_hours"] * 3600
        conn.execute("UPDATE clips SET status = 'expired' WHERE status = 'ready' AND ready_at < ?", (limit,))
        conn.commit()

        log("🔎 recherche des clips…")
        candidates = find_candidates(cfg, journal.known_ids(conn))
        log(f"   {len(candidates)} clip(s) candidat(s)")
        made = sum(prepare(cfg, conn, c) for c in candidates[: cfg["pipeline"]["clips_per_run"]])

        if cfg["tiktok"]["enabled"]:
            send_ready(cfg, conn)
        elif made:
            notify("GTA6 Clipper", f"{made} vidéo(s) prête(s) à poster.")
        log("✔ terminé")
    except SystemExit as e:  # erreurs de réglage (clés manquantes…) : message lisible
        log(f"❌ {e}")
    finally:
        lock.release()
