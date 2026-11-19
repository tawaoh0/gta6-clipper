"""Un passage complet : détection Twitch → téléchargement → sous-titres → montage → légende."""
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from filelock import FileLock, Timeout

from . import db as journal
from .config import DATA, SOURCES, WORK, load_config, output_dir, save_config
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
    from .moment import best_range, download_window, window as window_of

    p = cfg["pipeline"]
    day_dir = output_dir(cfg) / datetime.now().strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    work = WORK / clip["id"]
    work.mkdir(parents=True, exist_ok=True)
    try:
        log(f"⬇  {clip['broadcaster_name']} — « {clip['title']} » ({clip['view_count']} vues, {clip['score']}/h)")
        src, clip_start, clip_end = None, 0.0, float(clip["duration"])
        win = window_of(clip) if p.get("smart_cut", True) else None
        if win:
            try:
                src = download_window(clip["video_id"], *win, work)
                clip_start = clip["vod_offset"] - win[0]
                clip_end = clip_start + clip["duration"]
                log(f"   passage de VOD récupéré ({win[1] - win[0]:.0f} s autour du clip)")
            except Exception as e:
                why = "VOD réservée aux abonnés" if "subscriber" in str(e) else "VOD indisponible"
                log(f"   {why} : on garde le clip tel quel")
                win, src = None, None
        if src is None:
            src = download(clip["url"], work)
        transcript, chunks = "", []
        if p["subtitles"]:
            log("   sous-titres… (le premier passage télécharge le modèle, ~500 Mo, une seule fois)")
            transcript, chunks = transcribe(src, p["whisper_model"], p["language"])
        start, end, cut_reason = clip_start, clip_end, "découpe du spectateur"
        if win:
            start, end, cut_reason = best_range(src, chunks, clip_start, clip_end,
                                                float(cfg["twitch"]["max_duration"]))
            log(f"   découpe : {cut_reason} ({start:.1f} → {end:.1f} s)")
            transcript = " ".join(t for a, b, t in chunks if b > start and a < end) or transcript
        facecam = find_facecam(cfg, clip["login"], src)
        band = None
        if p.get("remove_burned_subs", True) and chunks:
            from .burned_subs import detect as find_burned
            band, why = find_burned(src)
            log(f"   {why}")
        from .hooks import candidates as hook_candidates
        ideas = hook_candidates(chunks, start, end, src)
        caption = write_caption(cfg, clip, transcript, hook_hint=ideas[0] if ideas else "")
        hook = caption.split("\n", 1)[0].strip() if p.get("hook_on_screen", True) else ""
        out = day_dir / f"{clip['login']}_{clip['id'][:40]}.mp4"
        cam_position = p.get("facecam_position", "top")
        render(src, out, clip["broadcaster_name"], chunks, facecam=facecam, zoom=p["zoom"],
               cam_position=cam_position, hook=hook, hook_seconds=p.get("hook_seconds", 2.5),
               burned_band=band, start=start, end=end)
        out.with_suffix(".txt").write_text(caption, encoding="utf-8")
        # On garde la vidéo d'origine quelques jours pour pouvoir ajuster et remonter le clip.
        kept = SOURCES / f"{clip['id']}{src.suffix}"
        shutil.move(str(src), kept)
        status = "ready" if cfg["tiktok"]["enabled"] else "manual"
        journal.insert(conn, clip, status, video_path=str(out), caption=caption, ready_at=time.time(),
                       source_path=str(kept), chunks=json.dumps(chunks), cam_position=cam_position,
                       facecam=json.dumps(facecam) if facecam else None, hook=hook,
                       burned_band=json.dumps(band) if band else None, trim_start=start, trim_end=end,
                       clip_start=clip_start, clip_end=clip_end, cut_reason=cut_reason, duration=float(clip.get("duration") or 0))
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
        notify("Grand Clip Auto", f"{sent} brouillon(s) dans ta boîte TikTok.")
    return sent


def prune_sources(cfg, conn):
    """Supprime les vidéos d'origine trop vieilles ou de clips déjà publiés / écartés."""
    days = cfg["pipeline"].get("keep_sources_days", 7)
    old = time.time() - days * 86400
    for r in conn.execute("SELECT id, source_path, status, seen_at FROM clips WHERE source_path IS NOT NULL"):
        if r["seen_at"] < old or (r["status"] in ("posted", "sent") and r["seen_at"] < time.time() - 86400):
            Path(r["source_path"]).unlink(missing_ok=True)
            journal.update(conn, r["id"], source_path=None)


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
        prune_sources(cfg, conn)

        log("🔎 recherche des clips…")
        candidates = find_candidates(cfg, journal.known_ids(conn))
        log(f"   {len(candidates)} clip(s) candidat(s)")
        made = sum(prepare(cfg, conn, c) for c in candidates[: cfg["pipeline"]["clips_per_run"]])

        if cfg["tiktok"]["enabled"]:
            send_ready(cfg, conn)
        elif made:
            notify("Grand Clip Auto", f"{made} vidéo(s) prête(s) à poster.")
        log("✔ terminé")
    except SystemExit as e:  # erreurs de réglage (clés manquantes…) : message lisible
        log(f"❌ {e}")
    finally:
        lock.release()
