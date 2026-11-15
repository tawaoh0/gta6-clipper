"""Détection des clips via l'API Twitch officielle (Helix)."""
from datetime import datetime, timedelta, timezone

import requests

HELIX = "https://api.twitch.tv/helix"


class Twitch:
    def __init__(self, client_id: str, client_secret: str):
        if not client_id or not client_secret:
            raise SystemExit("Renseigne [twitch] client_id et client_secret dans les réglages.")
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=20,
        )
        r.raise_for_status()
        self.s = requests.Session()
        self.s.headers.update(
            {"Client-Id": client_id, "Authorization": f"Bearer {r.json()['access_token']}"}
        )

    def _get(self, path: str, params) -> dict:
        r = self.s.get(f"{HELIX}/{path}", params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def game_id(self, name: str) -> str:
        data = self._get("games", {"name": name})["data"]
        if not data:
            raise SystemExit(f"Catégorie Twitch introuvable : {name!r}. Vérifie game_name.")
        return data[0]["id"]

    def game_names(self, ids: set[str]) -> dict[str, str]:
        ids = [i for i in ids if i]
        out = {}
        for i in range(0, len(ids), 100):
            for g in self._get("games", [("id", x) for x in ids[i : i + 100]])["data"]:
                out[g["id"]] = g["name"]
        return out

    def user_ids(self, logins: list[str]) -> dict[str, str]:
        out = {}
        for i in range(0, len(logins), 100):
            batch = [l.lower().strip() for l in logins[i : i + 100] if l.strip()]
            for u in self._get("users", [("login", l) for l in batch])["data"]:
                out[u["login"]] = u["id"]
        missing = {l.lower() for l in logins} - set(out)
        if missing:
            print(f"  ⚠ logins Twitch inconnus (ignorés) : {', '.join(sorted(missing))}")
        return out

    def clips(self, broadcaster_id: str, hours: float) -> list[dict]:
        now = datetime.now(timezone.utc)
        params = {
            "broadcaster_id": broadcaster_id,
            "started_at": (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "first": 100,
        }
        return self._get("clips", params)["data"]


def find_candidates(cfg: dict, already_seen: set[str]) -> list[dict]:
    """Clips (du jeu choisi, ou de tous les jeux) des streamers de la liste blanche, triés par vitesse de vues."""
    t = cfg["twitch"]
    tw = Twitch(t["client_id"], t["client_secret"])
    # game_name vide = tous les jeux
    game = tw.game_id(t["game_name"]) if t.get("game_name", "").strip() else None
    users = tw.user_ids(cfg["streamers"]["whitelist"])
    now = datetime.now(timezone.utc)

    found = []
    for login, uid in users.items():
        for c in tw.clips(uid, t["lookback_hours"]):
            if c["id"] in already_seen or (game and c["game_id"] != game):
                continue
            if c["view_count"] < t["min_views"]:
                continue
            if not (t["min_duration"] <= c["duration"] <= t["max_duration"]):
                continue
            created = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
            age_h = max((now - created).total_seconds() / 3600, 0.5)
            c["score"] = round(c["view_count"] / age_h, 1)  # vues par heure
            c["login"] = login
            found.append(c)

    # Twitch crée souvent plusieurs clips du même moment : on garde le meilleur
    # clip par streamer et par fenêtre de 2 minutes.
    names = tw.game_names({c["game_id"] for c in found}) if found else {}
    for c in found:
        c["game_name"] = names.get(c["game_id"], "")
    found.sort(key=lambda c: c["score"], reverse=True)
    kept, moments = [], set()
    for c in found:
        if c.get("vod_offset") is not None:
            key = (c["login"], c.get("video_id"), int(c["vod_offset"]) // 120)
            if key in moments:
                continue
            moments.add(key)
        kept.append(c)
    return kept
