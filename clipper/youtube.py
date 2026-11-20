"""Envoi direct sur YouTube (API officielle YouTube Data v3).

Connexion : OAuth 2.0 « application de bureau » de Google, avec PKCE et retour sur
http://127.0.0.1:<port libre> (méthode recommandée par Google pour les apps installées).
L'utilisateur crée lui-même son identifiant client dans Google Cloud (gratuit) ;
le jeton est rangé dans le dossier de l'app, lisible par lui seul.

Envoi : « resumable upload » en un seul envoi du fichier, titre + description + tags,
catégorie Jeux vidéo (20). Une vidéo verticale de moins de 3 min devient un Short.
"""
import base64
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

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
TOKEN_FILE = DATA / "youtube_token.json"
GAMING = "20"


def _save(tok: dict):
    old = json.loads(TOKEN_FILE.read_text()) if TOKEN_FILE.exists() else {}
    tok = {**old, **tok}
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 120
    TOKEN_FILE.write_text(json.dumps(tok, indent=2))
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def connected() -> bool:
    return TOKEN_FILE.exists()


def channel_name() -> str:
    if not TOKEN_FILE.exists():
        return ""
    return json.loads(TOKEN_FILE.read_text()).get("channel", "")


def logout():
    TOKEN_FILE.unlink(missing_ok=True)


def login(cfg: dict):
    """Ouvre Google dans le navigateur pour autoriser l'app à publier sur la chaîne."""
    y = cfg["youtube"]
    if not y.get("client_id") or not y.get("client_secret"):
        raise RuntimeError("Renseigne d'abord l'identifiant client et le secret Google.")
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in result
            self.wfile.write(("<h2>C'est bon, tu peux fermer cet onglet et revenir dans Grand Clip Auto.</h2>" if ok
                              else "<h2>Connexion annulée.</h2>").encode())

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 2
    redirect = f"http://127.0.0.1:{server.server_address[1]}"
    webbrowser.open(AUTH + "?" + urllib.parse.urlencode({
        "client_id": y["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }))
    deadline = time.time() + 300
    try:
        while "code" not in result and "error" not in result:
            if time.time() > deadline:
                raise RuntimeError("Connexion YouTube abandonnée : pas de réponse au bout de 5 minutes.")
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise RuntimeError(f"Google a refusé l'autorisation ({result['error']}).")
    if result.get("state") != state:
        raise RuntimeError("Réponse Google invalide. Relance la connexion.")

    r = requests.post(TOKEN, timeout=20, data={
        "client_id": y["client_id"], "client_secret": y["client_secret"], "code": result["code"],
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect,
    })
    tok = r.json()
    if "access_token" not in tok:
        raise RuntimeError(f"Échange du code refusé par Google : {tok.get('error_description') or tok.get('error')}")
    _save(tok)
    # Nom de la chaîne, pour l'afficher dans les réglages
    try:
        ch = requests.get(CHANNELS, params={"part": "snippet", "mine": "true"}, timeout=15,
                          headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
        name = ch["items"][0]["snippet"]["title"] if ch.get("items") else ""
        _save({"channel": name})
    except Exception:
        pass


def access_token(cfg: dict) -> str:
    if not TOKEN_FILE.exists():
        raise RuntimeError("YouTube n'est pas connecté : Réglages → YouTube → Connecter ma chaîne.")
    tok = json.loads(TOKEN_FILE.read_text())
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    if not tok.get("refresh_token"):
        raise RuntimeError("Connexion YouTube expirée : reconnecte ta chaîne dans les réglages.")
    r = requests.post(TOKEN, timeout=20, data={
        "client_id": cfg["youtube"]["client_id"], "client_secret": cfg["youtube"]["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
    })
    new = r.json()
    if "access_token" not in new:
        raise RuntimeError("Connexion YouTube expirée : reconnecte ta chaîne dans les réglages.")
    _save(new)
    return new["access_token"]


def split_caption(text: str) -> tuple[str, str, list[str]]:
    """Texte « Shorts » de l'app → (titre ≤ 100 caractères, description, tags sans #)."""
    lines = [l.rstrip() for l in (text or "").splitlines()]
    first = next((i for i, l in enumerate(lines) if l.strip()), None)
    title = lines[first].strip() if first is not None else "Clip"
    rest = "\n".join(lines[first + 1:]).strip() if first is not None else ""
    tags = [w.lstrip("#") for w in (text or "").split() if w.startswith("#") and len(w) > 1]
    if "#shorts" not in title.lower() and "#shorts" not in rest.lower():
        rest = (rest + "\n\n#Shorts").strip()
    return title[:100], rest[:4900], list(dict.fromkeys(tags))[:15]


def upload(cfg: dict, video: Path, text: str, privacy: str | None = None) -> str:
    """Envoie la vidéo sur la chaîne. Renvoie l'identifiant YouTube de la vidéo."""
    token = access_token(cfg)
    title, description, tags = split_caption(text)
    privacy = privacy or cfg["youtube"].get("privacy", "private")
    meta = {
        "snippet": {"title": title, "description": description, "tags": tags, "categoryId": GAMING},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    size = video.stat().st_size
    init = requests.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"}, json=meta, timeout=30,
                         headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
                                  "X-Upload-Content-Length": str(size)})
    if init.status_code != 200 or "Location" not in init.headers:
        raise RuntimeError(_error(init))
    with video.open("rb") as f:
        put = requests.put(init.headers["Location"], data=f, timeout=900,
                           headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
    if put.status_code not in (200, 201):
        raise RuntimeError(_error(put))
    return put.json()["id"]


def _error(r: requests.Response) -> str:
    try:
        err = r.json().get("error", {})
        reason = (err.get("errors") or [{}])[0].get("reason", "")
        msg = err.get("message", "")
    except Exception:
        reason, msg = "", r.text[:200]
    if reason in ("quotaExceeded", "uploadLimitExceeded"):
        return "Limite d'envois YouTube atteinte pour aujourd'hui : réessaie demain."
    if r.status_code in (401, 403) and reason in ("authError", "insufficientPermissions", "forbidden", ""):
        return f"YouTube refuse l'envoi ({msg or r.status_code}). Reconnecte ta chaîne dans les réglages."
    return f"YouTube a refusé l'envoi : {msg or r.status_code}"
