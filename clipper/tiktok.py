"""Connexion TikTok (Login Kit Desktop + PKCE) et envoi en brouillon (Content Posting API)."""
import hashlib
import json
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

from .config import DATA

API = "https://open.tiktokapis.com/v2"
TOKEN_FILE = DATA / "tiktok_token.json"
MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024


def _redirect(cfg) -> str:
    return f"http://localhost:{cfg['tiktok']['redirect_port']}/callback/"


def _save(tok: dict):
    tok["expires_at"] = time.time() + tok["expires_in"] - 120
    TOKEN_FILE.write_text(json.dumps(tok, indent=2))
    TOKEN_FILE.chmod(0o600)


def login(cfg: dict):
    """Ouvre le navigateur pour autoriser l'app sur ton compte TikTok."""
    t = cfg["tiktok"]
    verifier = secrets.token_urlsafe(64)[:96]
    # La doc TikTok Desktop demande le SHA-256 en hexadécimal.
    challenge = hashlib.sha256(verifier.encode()).hexdigest()
    state = secrets.token_urlsafe(16)
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": t["client_key"],
        "response_type": "code",
        "scope": "user.info.basic,video.upload",
        "redirect_uri": _redirect(cfg),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>C'est bon, tu peux fermer cet onglet.</h2>".encode())

        def log_message(self, *a):
            pass

    server = HTTPServer(("localhost", t["redirect_port"]), Handler)
    server.timeout = 2
    print("Ouverture de TikTok dans le navigateur pour autoriser l'app…")
    webbrowser.open(url)
    deadline = time.time() + 300
    try:
        while "code" not in result and "error" not in result:
            if time.time() > deadline:
                raise SystemExit("Connexion TikTok abandonnée : pas de réponse au bout de 5 minutes.")
            server.handle_request()
    finally:
        server.server_close()

    if "error" in result:
        raise SystemExit(f"Autorisation refusée : {result.get('error_description', result['error'])}")
    if result.get("state") != state:
        raise SystemExit("Réponse TikTok invalide (state différent). Relance la connexion.")

    r = requests.post(f"{API}/oauth/token/", data={
        "client_key": t["client_key"],
        "client_secret": t["client_secret"],
        "code": result["code"],
        "grant_type": "authorization_code",
        "redirect_uri": _redirect(cfg),
        "code_verifier": verifier,
    }, timeout=20)
    tok = r.json()
    if "access_token" not in tok:
        raise SystemExit(f"Échec de l'échange du code : {tok}")
    _save(tok)
    print("✅ Compte TikTok connecté.")


def access_token(cfg: dict) -> str:
    if not TOKEN_FILE.exists():
        raise RuntimeError("TikTok n'est pas connecté : Réglages → TikTok → Connecter mon compte.")
    tok = json.loads(TOKEN_FILE.read_text())
    if time.time() < tok["expires_at"]:
        return tok["access_token"]
    r = requests.post(f"{API}/oauth/token/", data={
        "client_key": cfg["tiktok"]["client_key"],
        "client_secret": cfg["tiktok"]["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
    }, timeout=20)
    new = r.json()
    if "access_token" not in new:
        raise RuntimeError(f"Rafraîchissement du jeton TikTok impossible : {new}")
    _save(new)
    return new["access_token"]


def _chunks(size: int) -> tuple[int, int]:
    if size <= MAX_CHUNK:
        return size, 1
    chunk = MAX_CHUNK
    return chunk, size // chunk  # le dernier morceau absorbe le reste (≤ 128 Mo)


def upload_draft(cfg: dict, video: Path) -> str:
    """Envoie la vidéo dans la boîte de réception TikTok. Renvoie le publish_id."""
    token = access_token(cfg)
    size = video.stat().st_size
    chunk_size, count = _chunks(size)
    r = requests.post(
        f"{API}/post/publish/inbox/video/init/",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        json={"source_info": {
            "source": "FILE_UPLOAD", "video_size": size,
            "chunk_size": chunk_size, "total_chunk_count": count,
        }},
        timeout=30,
    )
    body = r.json()
    if body.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"TikTok init refusé ({r.status_code}) : {body.get('error')}")
    upload_url, publish_id = body["data"]["upload_url"], body["data"]["publish_id"]

    with video.open("rb") as f:
        for i in range(count):
            start = i * chunk_size
            end = size - 1 if i == count - 1 else start + chunk_size - 1
            f.seek(start)
            data = f.read(end - start + 1)
            put = requests.put(upload_url, data=data, headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{end}/{size}",
            }, timeout=300)
            if put.status_code not in (200, 201, 206):
                raise RuntimeError(f"Envoi du morceau {i + 1}/{count} échoué : {put.status_code} {put.text[:200]}")
    return publish_id
