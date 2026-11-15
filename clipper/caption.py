"""Titre, description et hashtags du post TikTok."""
import re

import anthropic
from pydantic import BaseModel

BASE_TAGS = ["#twitch", "#clip"]
# Hashtags connus par jeu (nom Twitch en minuscules) ; sinon on dérive un hashtag du nom du jeu.
GAME_TAGS = {
    "grand theft auto vi": ["#gta6", "#gtavi", "#gta"],
    "grand theft auto v": ["#gta5", "#gtarp", "#gta"],
    "league of legends": ["#leagueoflegends", "#lol"],
    "just chatting": ["#justchatting", "#irl"],
}


def game_tags(game: str) -> list[str]:
    if not game:
        return []
    known = GAME_TAGS.get(game.lower())
    if known:
        return known
    tag = re.sub(r"[^0-9a-z]", "", game.lower())
    return [f"#{tag}"] if tag else []


SYSTEM = """Tu écris les légendes TikTok d'un compte qui publie des clips Twitch \
(tous jeux confondus), avec l'accord des streamers. Style : français, direct, drôle si le clip l'est, jamais putaclic \
mensonger. Tu ne décris que ce qui est réellement dans le clip (titre + transcription fournis). \
N'invente pas de spoiler, de fuite ou d'info sur le jeu. \
Tes hashtags doivent coller au jeu indiqué (jamais un autre jeu). Pas de mention de vues ou d'abonnés."""


class Caption(BaseModel):
    hook: str          # première ligne accrocheuse, 70 caractères max
    hashtags: list[str]  # 3 à 5 hashtags en plus des hashtags de base, avec le #


def _fallback(clip: dict) -> Caption:
    return Caption(hook=clip["title"][:90], hashtags=[])


def write_caption(cfg: dict, clip: dict, transcript: str) -> str:
    cap = _fallback(clip)
    if cfg["claude"]["enabled"]:
        try:
            key = cfg["claude"].get("api_key") or None  # sinon : variable ANTHROPIC_API_KEY
            client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
            resp = client.beta.messages.parse(
                model=cfg["claude"]["model"],
                max_tokens=2000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low"},
                system=SYSTEM,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Streamer : {clip['broadcaster_name']}\n"
                        f"Jeu : {clip.get('game_name') or 'inconnu'}\n"
                        f"Titre du clip sur Twitch : {clip['title']}\n"
                        f"Transcription : {transcript[:3000] or '(pas de parole)'}"
                    ),
                }],
                output_format=Caption,
            )
            if resp.stop_reason != "refusal" and resp.parsed_output:
                cap = resp.parsed_output
        except (anthropic.APIError, TypeError) as e:
            # TypeError = aucune clé Claude configurée
            print(f"  ⚠ Claude indisponible ({e.__class__.__name__}), légende simple utilisée")

    tags = game_tags(clip.get("game_name", "")) + BASE_TAGS + [t if t.startswith("#") else f"#{t}" for t in cap.hashtags]
    tags = list(dict.fromkeys(t.lower().replace(" ", "") for t in tags))[:9]
    credit = f"🎥 Clip : {clip['broadcaster_name']} sur Twitch (twitch.tv/{clip['login']})"
    return f"{cap.hook.strip()}\n\n{credit}\n\n{' '.join(tags)}"
