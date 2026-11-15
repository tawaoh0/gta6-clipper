"""Serveur local de l'interface (accessible seulement depuis cet ordinateur).

Utilisé par la fenêtre de l'application (app.py) ou directement dans le navigateur
(`python -m clipper ui`).
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import UPDATE_REPO, __version__, jobs
from . import db as journal
from .config import DATA, FROZEN, OUTPUT, RES, ROOT, load_config, save_config
from .pipeline import is_running
from .tools import NO_WINDOW, ffmpeg, reveal

UI = RES / "ui" / "index.html"
FONTS = RES / "assets" / "fonts"
THUMBS = DATA / "thumbs"
LOG = DATA / "run.log"
CLIP_ID = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
NOISE = ("objc[", "Class AVF", "MPS backend", "One of the duplicates")
EXTERNAL = ("https://dev.twitch.tv/", "https://www.twitch.tv/", "https://twitch.tv/",
            "https://clips.twitch.tv/", "https://console.anthropic.com/", "https://www.tiktok.com/",
            "https://developers.tiktok.com/", "https://github.com/")


# ---------- état ----------

def log_tail(n=150) -> list[str]:
    if not LOG.exists():
        return []
    lines = LOG.read_text(errors="replace").splitlines()[-n * 3 :]
    return [l for l in lines if l.strip() and not any(x in l for x in NOISE)][-n:]


PROGRESS = [  # (marqueur dans le journal, étape lisible) ; la dernière trouvée gagne
    ("🔎", "Recherche des nouveaux clips sur Twitch"),
    ("candidat", "Choix des meilleurs clips"),
    ("⬇", "Téléchargement du clip"),
    ("sous-titres", "Écriture des sous-titres"),
    ("visage", "Montage de la vidéo"),
    ("cam ", "Montage de la vidéo"),
    ("🎬", "Clip suivant"),
]


def _run_lines(lines: list[str], upto: int | None = None) -> list[str]:
    lines = lines if upto is None else lines[: upto + 1]
    start = max((i for i, l in enumerate(lines) if "🔎" in l), default=0)
    return lines[start:]


def _found(run: list[str]) -> int | None:
    m = re.search(r"(\d+) clip\(s\) candidat", " ".join(run))
    return int(m.group(1)) if m else None


def progress(lines: list[str]) -> dict:
    """Étape en cours du passage, lue dans les dernières lignes du journal."""
    run = _run_lines(lines)
    step, detail = "Démarrage du passage", ""
    for l in run:
        for mark, label in PROGRESS:
            if mark in l:
                step = label
        d = re.search(r"⬇\s+(.+?) — « (.+?) »", l)
        if d:
            detail = f"{d.group(1)} · « {d.group(2)} »"
    return {"step": step, "detail": detail, "made": sum("🎬" in l for l in run), "found": _found(run)}


def last_run_summary(lines: list[str]) -> dict:
    """Résumé du dernier passage terminé."""
    end = max((i for i, l in enumerate(lines) if "✔ terminé" in l or ("❌" in l and "échec" not in l)),
              default=None)
    if end is None:
        return {}
    run = _run_lines(lines, end)
    err = next((l.split("❌", 1)[1].strip() for l in run if "❌" in l and "échec" not in l), "")
    return {"made": sum("🎬" in l for l in run), "found": _found(run) or 0,
            "failed": sum("échec" in l for l in run), "error": err}


def state() -> dict:
    cfg = load_config()
    conn = journal.connect()
    rows = conn.execute("SELECT * FROM clips ORDER BY seen_at DESC LIMIT 300").fetchall()
    clips = []
    for r in rows:
        d = dict(r)
        d["has_video"] = bool(d["video_path"]) and Path(d["video_path"]).exists()
        d["file"] = Path(d["video_path"]).name if d["video_path"] else ""
        d["editable"] = jobs.can_edit(r)
        d.pop("chunks", None)
        clips.append(d)
    t, p, c = cfg["twitch"], cfg["pipeline"], cfg["claude"]
    lines = log_tail()
    running = is_running()
    every = p.get("auto_minutes") or 0
    last = cfg.get("last_run_at") or 0
    return {
        "clips": clips,
        "running": running,
        "progress": progress(lines) if running else None,
        "last_run": {"at": last, **last_run_summary(lines)} if last else None,
        "schedule": every or None,
        "next_run_at": max(last + every * 60, time.time()) if every else None,
        "onboarded": cfg.get("onboarded", False),
        "claude": bool(c.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")),
        "twitch_ok": bool(t["client_id"] and t["client_secret"]),
        "output_dir": str(OUTPUT),
        "version": __version__,
        "update": jobs.UPDATE if jobs.UPDATE.get("latest") else None,
        "jobs": jobs.snapshot(),
        "tiktok": {"connected": jobs.tiktok_connected(),
                   "has_keys": bool(cfg["tiktok"]["client_key"] and cfg["tiktok"]["client_secret"]),
                   "redirect": f"http://localhost:{cfg['tiktok']['redirect_port']}/callback/"},
        "settings": {
            "whitelist": cfg["streamers"]["whitelist"],
            "game_name": t.get("game_name", ""),
            "facecam": p.get("facecam", "auto"),
            "facecam_position": p.get("facecam_position", "top"),
            "clips_per_run": p["clips_per_run"],
            "min_views": t["min_views"],
            "lookback_hours": t["lookback_hours"],
            "max_duration": t["max_duration"],
            "twitch_client_id": t["client_id"],
            "twitch_secret_set": bool(t["client_secret"]),
            "claude_key_set": bool(c.get("api_key")),
            "hook_on_screen": p.get("hook_on_screen", True),
            "remove_burned_subs": p.get("remove_burned_subs", True),
            "smart_cut": p.get("smart_cut", True),
            "tiktok_client_key": cfg["tiktok"]["client_key"],
            "tiktok_secret_set": bool(cfg["tiktok"]["client_secret"]),
        },
        "log": lines,
    }


# ---------- réglages (settings.json) ----------

SETTINGS = {  # champ de l'interface -> (section, clé, type)
    "whitelist": ("streamers", "whitelist", list),
    "game_name": ("twitch", "game_name", str),
    "facecam": ("pipeline", "facecam", str),
    "facecam_position": ("pipeline", "facecam_position", str),
    "clips_per_run": ("pipeline", "clips_per_run", int),
    "min_views": ("twitch", "min_views", int),
    "lookback_hours": ("twitch", "lookback_hours", int),
    "max_duration": ("twitch", "max_duration", int),
    "twitch_client_id": ("twitch", "client_id", str),
    "twitch_client_secret": ("twitch", "client_secret", str),
    "claude_api_key": ("claude", "api_key", str),
    "hook_on_screen": ("pipeline", "hook_on_screen", bool),
    "remove_burned_subs": ("pipeline", "remove_burned_subs", bool),
    "smart_cut": ("pipeline", "smart_cut", bool),
    "tiktok_client_key": ("tiktok", "client_key", str),
    "tiktok_client_secret": ("tiktok", "client_secret", str),
}
SECRETS = ("twitch_client_secret", "claude_api_key", "tiktok_client_secret")


def save_settings(data: dict):
    cfg = load_config()
    for field, value in data.items():
        if field == "onboarded":
            cfg["onboarded"] = bool(value)
            continue
        if field not in SETTINGS:
            continue
        section, key, kind = SETTINGS[field]
        if kind is list:
            value = [re.sub(r"[^a-z0-9_]", "", str(v).lower()) for v in value]
            value = [v for v in dict.fromkeys(value) if v]
        elif kind is bool:
            value = bool(value)
        elif kind is int:
            value = max(0, int(value))
        else:
            value = str(value).strip()
            if field == "facecam" and value not in ("auto", "off"):
                continue
            if field == "facecam_position" and value not in ("top", "bottom"):
                continue
            # Les clés secrètes ne sont jamais renvoyées à l'interface : champ vide = on garde l'ancienne.
            if field in SECRETS and not value:
                continue
        cfg[section][key] = value
    save_config(cfg)


def test_twitch(client_id: str, client_secret: str) -> dict:
    import requests

    cfg = load_config()
    client_id = client_id.strip() or cfg["twitch"]["client_id"]
    client_secret = client_secret.strip() or cfg["twitch"]["client_secret"]
    if not client_id or not client_secret:
        return {"ok": False, "error": "Il faut l'identifiant client et le secret."}
    try:
        r = requests.post("https://id.twitch.tv/oauth2/token", timeout=15, data={
            "client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"})
    except requests.RequestException:
        return {"ok": False, "error": "Twitch ne répond pas : vérifie ta connexion internet."}
    if r.status_code != 200:
        return {"ok": False, "error": "Twitch refuse ces clés : vérifie l'identifiant et le secret."}
    return {"ok": True}


# ---------- actions ----------

def start_run() -> bool:
    if is_running():
        return False
    cmd = [sys.executable, "--run"] if FROZEN else [sys.executable, "-m", "clipper", "run"]
    with LOG.open("a", encoding="utf-8") as log:
        subprocess.Popen(cmd, cwd=None if FROZEN else ROOT, stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env={**os.environ, "PYTHONUNBUFFERED": "1"},
                         **(NO_WINDOW or {"start_new_session": True}))
    return True


def set_schedule(minutes: int | None):
    cfg = load_config()
    cfg["pipeline"]["auto_minutes"] = max(15, minutes) if minutes else 0
    save_config(cfg)


def scheduler():
    """Passages automatiques tant que l'application est ouverte."""
    while True:
        time.sleep(30)
        try:
            cfg = load_config()
            every = cfg["pipeline"].get("auto_minutes") or 0
            if every and time.time() - (cfg.get("last_run_at") or 0) >= every * 60:
                start_run()
        except Exception as e:
            print(f"planificateur : {e}", flush=True)


def thumb(clip_id: str, video: Path) -> Path | None:
    THUMBS.mkdir(parents=True, exist_ok=True)
    out = THUMBS / f"{clip_id}.jpg"
    if not out.exists():
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", "1.5", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(out)], **NO_WINDOW)
    return out if out.exists() else None


def frame(clip_id: str, source: Path, t: float) -> Path | None:
    """Image de la vidéo d'origine à l'instant t (pour placer le cadre de la cam)."""
    THUMBS.mkdir(parents=True, exist_ok=True)
    out = THUMBS / f"src_{clip_id}_{round(t * 10)}.jpg"
    if not out.exists():
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(source),
                        "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3", str(out)], **NO_WINDOW)
    return out if out.exists() else None


def open_url(url: str) -> bool:
    """Ouvre un lien dans le navigateur habituel (seulement les sites prévus)."""
    if not url.startswith(EXTERNAL):
        return False
    webbrowser.open(url)
    return True


# ---------- serveur HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def _clip(self, clip_id):
        if not CLIP_ID.match(clip_id):
            return None
        return journal.connect().execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()

    def _file(self, path: Path, ctype: str):
        size = path.stat().st_size
        extra = {"Accept-Ranges": "bytes"}
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if rng:  # le lecteur vidéo demande des morceaux pour pouvoir avancer
            start = int(rng.group(1) or 0)
            end = min(int(rng.group(2) or size - 1), size - 1)
            with path.open("rb") as f:
                f.seek(start)
                body = f.read(end - start + 1)
            return self._send(206, body, ctype, {**extra, "Content-Range": f"bytes {start}-{end}/{size}"})
        self._send(200, path.read_bytes(), ctype, extra)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/":
                return self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._json(state())
            m = re.match(r"^/fonts/([A-Za-z0-9_-]+\.ttf)$", path)
            if m and (FONTS / m.group(1)).exists():
                return self._file(FONTS / m.group(1), "font/ttf")
            m = re.match(r"^/(source|frame)/([^/]+)$", path)
            if m:
                kind, cid = m.groups()
                r = self._clip(cid)
                if not r or not jobs.can_edit(r):
                    return self._send(404, b"introuvable", "text/plain")
                src = Path(r["source_path"])
                if kind == "source":
                    return self._file(src, "video/mp4")
                q = re.search(r"[?&]t=([\d.]+)", self.path)
                img = frame(cid, src, float(q.group(1)) if q else 1.5)
                return self._file(img, "image/jpeg") if img else self._send(404)
            m = re.match(r"^/(video|thumb|download)/([^/]+)$", path)
            if m:
                kind, cid = m.groups()
                r = self._clip(cid)
                if not r or not r["video_path"] or not Path(r["video_path"]).exists():
                    return self._send(404, b"introuvable", "text/plain")
                video = Path(r["video_path"])
                if kind == "thumb":
                    t = thumb(cid, video)
                    return self._file(t, "image/jpeg") if t else self._send(404)
                if kind == "download":
                    return self._send(200, video.read_bytes(), "video/mp4", {
                        "Content-Disposition": f'attachment; filename="{video.name}"'})
                return self._file(video, "video/mp4")
            self._send(404, b"introuvable", "text/plain")
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        # Protection : seules les pages servies par ce serveur peuvent envoyer des actions.
        port = self.server.server_address[1]
        origin = self.headers.get("Origin", "")
        if origin and origin not in (f"http://localhost:{port}", f"http://127.0.0.1:{port}"):
            return self._json({"error": "origine refusée"}, 403)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            if path == "/api/run":
                return self._json({"started": start_run()})
            if path == "/api/schedule":
                set_schedule(int(data["minutes"]) if data.get("minutes") else None)
                return self._json({"ok": True})
            if path == "/api/settings":
                save_settings(data)
                return self._json({"ok": True})
            if path == "/api/twitch-test":
                return self._json(test_twitch(str(data.get("client_id", "")),
                                              str(data.get("client_secret", ""))))
            if path == "/api/open-folder":
                OUTPUT.mkdir(parents=True, exist_ok=True)
                reveal(OUTPUT)
                return self._json({"ok": True})
            if path == "/api/tiktok/login":
                return self._json({"started": jobs.tiktok_login()})
            if path == "/api/tiktok/logout":
                from .tiktok import TOKEN_FILE
                TOKEN_FILE.unlink(missing_ok=True)
                return self._json({"ok": True})
            if path == "/api/update-check":
                jobs.check_update(UPDATE_REPO)
                return self._json({"update": jobs.UPDATE if jobs.UPDATE.get("latest") else None})
            if path == "/api/open-url":
                return self._json({"ok": open_url(str(data.get("url", "")))})
            m = re.match(r"^/api/clip/([^/]+)/(posted|discard|restore|caption|reveal|rerender|tiktok)$", path)
            if m:
                cid, action = m.groups()
                r = self._clip(cid)
                if not r:
                    return self._json({"error": "clip inconnu"}, 404)
                conn = journal.connect()
                if action == "rerender":
                    return self._json({"started": jobs.rerender(cid, data)})
                if action == "tiktok":
                    return self._json({"started": jobs.tiktok_send(cid)})
                if action == "reveal":
                    if not r["video_path"] or not Path(r["video_path"]).exists():
                        return self._json({"error": "la vidéo n'existe plus"}, 404)
                    reveal(Path(r["video_path"]))
                elif action == "caption":
                    journal.update(conn, cid, caption=str(data.get("caption", ""))[:2200])
                else:
                    status = {"posted": "posted", "discard": "discarded", "restore": "manual"}[action]
                    fields = {"status": status}
                    if action == "posted":
                        fields["sent_at"] = time.time()
                    journal.update(conn, cid, **fields)
                return self._json({"ok": True})
            self._json({"error": "action inconnue"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 400)


def start_server(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Démarre le serveur en tâche de fond. port=0 : un port libre au hasard."""
    load_config()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=jobs.update_loop, args=(UPDATE_REPO,), daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def serve(open_browser=True, port=8777):
    server, url = start_server(port)
    url = url.replace("127.0.0.1", "localhost")
    print(f"Interface : {url}  (Ctrl+C pour arrêter)", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
