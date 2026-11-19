"""Place utilisée sur le disque et nettoyage des vieilles vidéos."""
import time
from pathlib import Path

from . import db as journal
from .config import DATA, SOURCES

THUMBS = DATA / "thumbs"
DONE = ("posted", "sent", "discarded", "expired", "error")   # clips qu'on ne postera plus


def _size(paths) -> int:
    total = 0
    for p in paths:
        try:
            total += Path(p).stat().st_size
        except OSError:
            pass
    return total


def usage() -> dict:
    conn = journal.connect()
    rows = conn.execute("SELECT status, video_path FROM clips WHERE video_path IS NOT NULL").fetchall()
    todo = [r["video_path"] for r in rows if r["status"] not in DONE]
    done = [r["video_path"] for r in rows if r["status"] in DONE]
    return {
        "todo": _size(todo),
        "done": _size(done),
        "sources": _size(SOURCES.glob("*")) if SOURCES.exists() else 0,
        "cache": _size(THUMBS.glob("*")) if THUMBS.exists() else 0,
    }


def clean(days: int | None, everything_done: bool = False) -> dict:
    """Supprime les vidéos des clips publiés / écartés depuis plus de `days` jours
    (ou tous si everything_done), avec leur vidéo d'origine et leurs miniatures.
    Les clips encore « à poster » ne sont jamais touchés."""
    conn = journal.connect()
    limit = time.time() - (days or 0) * 86400
    freed, count = 0, 0
    for r in conn.execute("SELECT * FROM clips WHERE status IN (%s)" % ",".join("?" * len(DONE)), DONE).fetchall():
        when = r["sent_at"] or r["ready_at"] or r["seen_at"] or 0
        if not everything_done and (not days or when > limit):
            continue
        files = [r["video_path"], r["source_path"]]
        if r["video_path"]:
            files.append(str(Path(r["video_path"]).with_suffix(".txt")))
        files += [str(p) for p in THUMBS.glob(f"*{r['id']}*")] if THUMBS.exists() else []
        removed = False
        for f in filter(None, files):
            p = Path(f)
            if p.exists():
                freed += p.stat().st_size
                p.unlink(missing_ok=True)
                removed = True
        if removed:
            count += 1
            journal.update(conn, r["id"], source_path=None)
    # Miniatures orphelines (clips supprimés de la base)
    return {"clips": count, "freed": freed}
