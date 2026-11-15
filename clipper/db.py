"""Journal SQLite : chaque clip vu, son état et ce qui a été envoyé."""
import shutil
import sqlite3
import time

from .config import DATA, FROZEN, ROOT

# Statuts : ready | sent | manual (prêt, à poster à la main) | posted (publié à la main)
#           | discarded (écarté) | expired | error
SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    streamer TEXT,
    title TEXT,
    url TEXT,
    views INTEGER,
    score REAL,
    status TEXT,
    video_path TEXT,
    caption TEXT,
    publish_id TEXT,
    error TEXT,
    seen_at REAL,
    ready_at REAL,
    sent_at REAL
);
"""
EXTRA_COLUMNS = {  # colonnes ajoutées après coup (pour pouvoir remonter un clip)
    "game": "TEXT", "source_path": "TEXT", "chunks": "TEXT", "facecam": "TEXT", "cam_position": "TEXT",
    "trim_start": "REAL", "trim_end": "REAL", "hook": "TEXT", "duration": "REAL",
    "burned_band": "TEXT", "clip_start": "REAL", "clip_end": "REAL", "cut_reason": "TEXT",
}   # colonnes ajoutées après coup


def connect() -> sqlite3.Connection:
    path = DATA / "clips.db"
    old = ROOT / "data" / "clips.db"   # ancienne version ligne de commande
    if not path.exists() and not FROZEN and old.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    have = {r[1] for r in db.execute("PRAGMA table_info(clips)")}
    for col, kind in EXTRA_COLUMNS.items():
        if col not in have:
            db.execute(f"ALTER TABLE clips ADD COLUMN {col} {kind}")
    return db


def known_ids(db) -> set[str]:
    return {r["id"] for r in db.execute("SELECT id FROM clips")}


def insert(db, clip: dict, status: str, **fields):
    row = {
        "id": clip["id"],
        "streamer": clip["broadcaster_name"],
        "title": clip["title"],
        "url": clip["url"],
        "views": clip["view_count"],
        "score": clip.get("score", 0),
        "game": clip.get("game_name", ""),
        "status": status,
        "seen_at": time.time(),
        **fields,
    }
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    db.execute(f"INSERT OR REPLACE INTO clips ({cols}) VALUES ({marks})", list(row.values()))
    db.commit()


def update(db, clip_id: str, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE clips SET {sets} WHERE id = ?", [*fields.values(), clip_id])
    db.commit()


def sent_last_24h(db) -> int:
    since = time.time() - 24 * 3600
    return db.execute(
        "SELECT COUNT(*) FROM clips WHERE status = 'sent' AND sent_at > ?", (since,)
    ).fetchone()[0]
