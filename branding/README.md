# Identité Grand Clip Auto

## Logo officiel : « Tuile » (K1, Chrome rétro), choisi le 20 nov. 2026

Un C carré et penché, en chrome années 80 (ciel clair, ligne d'horizon sombre, reflet rose-orangé), avec un relief rose vers violet nuit, posé devant un soleil rayé et une grille néon, dans une tuile aux coins arrondis.

| Fichier | Usage |
|---|---|
| `logo-tuile.svg` | Source vectorielle du logo, à utiliser partout |
| `export/logo-*.png` | Le logo en 16 à 1024 px, fond transparent aux coins |
| `export/logo-full-*.png` | Version plein cadre sans coins (iPhone, Windows) |
| `export/og-image.png` | Image de partage 1200×630 (Discord, WhatsApp, X…) |
| `export/app/icon.png` · `icon.icns` · `icon.ico` | Icônes de l'app, prêtes à copier dans `packaging/` |
| `logo-chrome.html` | Planche : les 3 variantes, le nom chromé, les mises en situation |

**Pour l'app :** copier `export/app/icon.png`, `icon.icns` et `icon.ico` dans `packaging/`, puis refaire le build Mac et Windows. Ces fichiers se refont avec `uv run python branding/make_app_icons.py`.

## Le nom

« GRAND CLIP AUTO » en Anton, penché de 10°, avec le même chrome et un relief rose. En version empilée : « GRAND » et « CLIP » en chrome, « AUTO » en néon rose (voir `logo-chrome.html`).

## Couleurs

| | |
|---|---|
| Nuit | `#0a0811` |
| Magenta | `#ff2e88` |
| Orange | `#ff7a45` |
| Or | `#ffc35a` |
| Violet nuit (relief) | `#1e0b33` |

Polices : Anton (titres), Manrope (texte), JetBrains Mono (petits libellés).
