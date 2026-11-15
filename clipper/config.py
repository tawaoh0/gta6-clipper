"""Emplacements et réglages, identiques sur Mac et Windows.

- Réglages, journal, base de clips : dossier de données de l'utilisateur
  (Mac : ~/Library/Application Support/GTA6 Clipper, Windows : %LOCALAPPDATA%\\GTA6 Clipper).
- Vidéos montées : dossier Vidéos de l'utilisateur / GTA6 Clipper.
- Ressources (interface, modèles, polices) : dans le projet, ou dans l'app empaquetée.
"""
import copy
import json
import sys
import tomllib
from pathlib import Path

from platformdirs import user_data_dir, user_videos_dir

APP_NAME = "GTA6 Clipper"
FROZEN = getattr(sys, "frozen", False)
ROOT = Path(__file__).resolve().parent.parent           # dossier du projet (mode développement)
RES = Path(getattr(sys, "_MEIPASS", ROOT))               # ressources embarquées
DATA = Path(user_data_dir(APP_NAME, appauthor=False))
OUTPUT = Path(user_videos_dir()) / APP_NAME
WORK = DATA / "work"
SETTINGS_FILE = DATA / "settings.json"

DEFAULTS = {
    "onboarded": False,
    "twitch": {
        "client_id": "",
        "client_secret": "",
        "game_name": "Grand Theft Auto VI",
        "lookback_hours": 6,
        "min_views": 30,
        "min_duration": 8,
        "max_duration": 60,
    },
    "streamers": {"whitelist": [], "facecam": {}},
    "pipeline": {
        "clips_per_run": 3,
        "drafts_per_day": 5,
        "ready_expiry_hours": 12,
        "language": "fr",
        "whisper_model": "small",
        "subtitles": True,
        "zoom": 1.25,
        "facecam": "auto",
        "facecam_position": "top",
        "auto_minutes": 0,          # 0 = pas de passage automatique
    },
    "tiktok": {"enabled": False, "client_key": "", "client_secret": "", "redirect_port": 3455},
    "claude": {"enabled": True, "model": "claude-opus-5-5", "api_key": ""},
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _import_old_toml() -> dict:
    """Reprend l'ancien config.toml du projet (version ligne de commande), s'il existe."""
    old = ROOT / "config.toml"
    if FROZEN or not old.exists():
        return {}
    with old.open("rb") as f:
        cfg = tomllib.load(f)
    cfg["tiktok"] = {**cfg.get("tiktok", {}), "enabled": False}
    cfg["onboarded"] = bool(cfg.get("twitch", {}).get("client_id"))
    return cfg


def ensure_dirs():
    for d in (DATA, OUTPUT, WORK):
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_dirs()
    if SETTINGS_FILE.exists():
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    else:
        saved = _import_old_toml()
        save_config(_merge(DEFAULTS, saved))
    return _merge(DEFAULTS, saved)


def save_config(cfg: dict):
    ensure_dirs()
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)
    try:
        SETTINGS_FILE.chmod(0o600)  # contient les clés : lisible par l'utilisateur seulement
    except OSError:
        pass
