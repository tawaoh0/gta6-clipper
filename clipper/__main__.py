"""
Utilisation (mode développement) :
  uv run python -m clipper ui               ouvrir l'interface dans le navigateur (--no-open : sans l'ouvrir)
  uv run python -m clipper app              ouvrir l'application (fenêtre, pas encore prête)
  uv run python -m clipper run              un passage complet
  uv run python -m clipper render FICHIER.mp4 [streamer]   tester le montage sur une vidéo locale
  uv run python -m clipper status           ce qui a été fait
  uv run python -m clipper login            connecter un compte TikTok (envoi en brouillon, désactivé par défaut)
"""
import sys
import traceback
from pathlib import Path

from .config import DATA, OUTPUT, load_config


def status():
    from . import db as journal

    conn = journal.connect()
    for s, n in conn.execute("SELECT status, COUNT(*) FROM clips GROUP BY status"):
        print(f"{s:>9} : {n}")
    print(f"\nRéglages et journal : {DATA}\nVidéos : {OUTPUT}\n\nDerniers clips :")
    for r in conn.execute("SELECT * FROM clips ORDER BY seen_at DESC LIMIT 10"):
        extra = f" — {r['error']}" if r["error"] else ""
        print(f"  [{r['status']}] {r['streamer']} : {r['title']} ({r['views']} vues){extra}")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "run":
        from .pipeline import run
        from .tools import notify
        try:
            run()
        except Exception:
            traceback.print_exc()
            notify("GTA6 Clipper", "Erreur pendant le passage, voir le journal.")
            sys.exit(1)
    elif cmd == "ui":
        from .web import serve
        serve(open_browser="--no-open" not in argv)
    elif cmd == "app":
        from .app import main as app_main
        app_main()
    elif cmd == "login":
        from .tiktok import login
        login(load_config())
    elif cmd == "render" and len(argv) > 2:
        from .media import render, transcribe
        from .pipeline import find_facecam
        cfg = load_config()
        src = Path(argv[2]).resolve()
        streamer = argv[3] if len(argv) > 3 else "streamer"
        _, chunks = transcribe(src, cfg["pipeline"]["whisper_model"], cfg["pipeline"]["language"])
        out = OUTPUT / f"test_{src.stem}.mp4"
        render(src, out, streamer, chunks, facecam=find_facecam(cfg, streamer, src),
               zoom=cfg["pipeline"]["zoom"], cam_position=cfg["pipeline"]["facecam_position"])
        print(f"✅ {out}")
    elif cmd == "status":
        status()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
