"""L'application de bureau : une fenêtre qui affiche l'interface.

Le même exécutable sert aussi aux passages : lancé avec --run, il fait un passage
complet sans fenêtre (c'est ce que l'interface appelle en arrière-plan).
"""
import multiprocessing
import sys

from .config import APP_NAME


def run_pass():
    import traceback

    from .pipeline import run
    from .tools import notify

    try:
        run()
    except Exception:
        traceback.print_exc()
        notify(APP_NAME, "Erreur pendant le passage, voir le journal.")
        sys.exit(1)


def main():
    multiprocessing.freeze_support()
    if "--run" in sys.argv:
        return run_pass()

    import webview

    from . import web

    server, url = web.start_server(port=0)
    web.WINDOW = webview.create_window(APP_NAME, url, width=1320, height=880, min_size=(960, 640),
                          background_color="#0a0811", text_select=True)
    try:
        webview.start()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
