"""
Kidlat Download Manager
Free, no subscription, no ads. Double-click this file (or Kidlat.exe) to start.
"""

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine  # noqa: E402
from bridge import Bridge, ping_existing  # noqa: E402


def data_dir():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "Kidlat")


def main():
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
        try:  # own taskbar icon instead of the Python one
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kidlat.DownloadManager")
        except Exception:  # noqa: BLE001
            pass

    manager = engine.Manager(data_dir())
    port = int(manager.settings["port"])

    # already running? just bring that window to the front
    if ping_existing(port):
        if "--startup" not in sys.argv:
            ping_existing(port, "/show")
        return

    from gui import App  # imported late so a second launch exits fast

    app = None
    try:
        bridge = Bridge(port,
                        on_add=lambda items: app.call(lambda: app.handle_browser(items)),
                        on_show=lambda: app.call(app.show))
        bridge_ok = True
    except OSError:
        bridge, bridge_ok = None, False

    app = App(manager, bridge_ok)
    if "--startup" in sys.argv:          # launched by Windows at login
        app.root.iconify()
    try:
        import autostart
        autostart.refresh()              # fix the entry if the folder was moved
    except Exception:  # noqa: BLE001
        pass
    if bridge:
        bridge.start()

    # links passed on the command line:  Kidlat.exe https://example.com/file.zip
    for arg in sys.argv[1:]:
        if arg.startswith(("http://", "https://")):
            app.root.after(500, lambda a=arg: app.add_url(a))

    app.run()


if __name__ == "__main__":
    main()
