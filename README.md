# Grand Clip Auto

Toutes les 15 minutes, l'outil cherche les clips Twitch GTA 6 qui décollent chez les streamers de ta liste blanche, les monte en vertical avec sous-titres et crédit, écrit la légende, et envoie la vidéo **en brouillon** dans ton TikTok. Toi, tu ouvres la notif TikTok, tu colles la légende et tu publies.

```
Twitch (API Helix) → filtre liste blanche + vues/heure → yt-dlp → Whisper (sous-titres)
→ ffmpeg 9:16 → Claude (légende) → TikTok (brouillon) → notif macOS
```

## Mise en route (≈ 20 min)

### 1. Twitch
1. Va sur https://dev.twitch.tv/console/apps → **Register Your Application**.
2. Nom : ce que tu veux. OAuth Redirect URL : `http://localhost`. Catégorie : *Application Integration*. Client type : *Confidential*.
3. Copie le **Client ID**, génère un **Client Secret**, et colle les deux dans `config.toml` → `[twitch]`.

### 2. Liste blanche
Dans `config.toml` → `[streamers] whitelist`, mets les logins Twitch des streamers qui t'ont dit oui. Garde une trace de leur accord (capture du DM).

Optionnel : si un streamer a toujours sa facecam au même endroit, renseigne sa position dans `[streamers.facecam]` pour avoir le montage « facecam en haut / jeu en bas ». Exemple pour une cam en haut à gauche qui prend un quart de la largeur : `[0.0, 0.0, 0.25, 0.30]`.

### 3. TikTok
1. https://developers.tiktok.com → connecte-toi → **Manage apps** → **Connect an app**.
2. Ajoute les produits **Login Kit** (plateforme *Desktop*) et **Content Posting API**.
3. Redirect URI (Login Kit → Desktop) : `http://localhost:3455/callback/`
4. Scopes : `user.info.basic` et `video.upload`.
5. Tant que l'app n'est pas validée, utilise le mode **Sandbox** et ajoute ton compte TikTok comme *target user*.
6. Copie **Client key** et **Client secret** dans `config.toml` → `[tiktok]`.
7. Soumets l'app à la validation dès maintenant (description : « outil personnel qui envoie mes montages en brouillon dans mon compte »). Une fois validée, on pourra passer en publication directe.
8. Connecte ton compte (une seule fois) :
   ```bash
   uv run python -m clipper login
   ```

### 4. Claude (légendes)
Mets ta clé dans le terminal (ou dans le plist, voir plus bas) :
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```
Sans clé, la légende reprend simplement le titre du clip Twitch.

## Interface

```bash
uv run python -m clipper ui
```

Elle s'ouvre sur http://localhost:8777 et n'est accessible que depuis ce Mac. On y trouve :
- la file des clips à poster : clique un clip pour le regarder, corriger la légende, la copier, télécharger la vidéo, puis la marquer publiée ou l'écarter ;
- le bouton « Lancer un passage » et l'interrupteur du lancement automatique ;
- les réglages (liste blanche, position de la cam, vues minimum…) et le journal.

## Utilisation en ligne de commande

| Commande | Effet |
|---|---|
| `uv run python -m clipper run` | Un passage complet, à la main |
| `uv run python -m clipper schedule 15` | Lancement automatique toutes les 15 min |
| `uv run python -m clipper unschedule` | Arrêter l'automatisation |
| `uv run python -m clipper status` | Ce qui a été monté / envoyé / raté |
| `uv run python -m clipper render video.mp4 NomStreamer` | Tester le montage sur une vidéo locale |

Les vidéos sont dans `output/AAAA-MM-JJ/` avec leur légende en `.txt`. Toutes les légendes envoyées s'empilent dans `output/A_POSTER.md`.

L'automatisation tourne tant que le Mac est allumé et que ta session est ouverte. Pour 24h/24, il faudra un petit serveur.

## Limites à connaître

- **5 brouillons en attente max par 24 h** côté TikTok. Publie tes brouillons au fur et à mesure pour libérer des places. L'outil s'arrête tout seul à 5 envois par 24 h (`drafts_per_day`) et garde les vidéos prêtes, puis les abandonne au bout de 12 h, une fois que le moment n'est plus tendance.
- La légende ne peut pas être envoyée avec un brouillon (limite de l'API TikTok) : copie-la depuis `A_POSTER.md`.
- Les clips gardés sont ceux qui prennent le plus de vues par heure. Monte `min_views` si tu as trop de clips moyens.
- Avec l'API Twitch publique, un clip met quelques minutes à apparaître après sa création.
