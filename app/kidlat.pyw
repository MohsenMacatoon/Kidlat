"""
Kidlat Download Manager
Free, no subscription, no ads. Double-click this file (or Kidlat.exe) to start.
"""

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _play_from_explorer():
    """File Explorer right-click: play a folder / selected files in order."""
    if len(sys.argv) >= 3 and sys.argv[1] in ("--play-folder", "--play-add"):
        import playlist
        target = sys.argv[2]
        if sys.argv[1] == "--play-folder":
            err = playlist.play(playlist.media_in_folder(target),
                                os.path.basename(target.rstrip("\\/")) or "Kidlat")
            if err and sys.platform == "win32":
                ctypes.windll.user32.MessageBoxW(None, err, "Kidlat", 0x40)
        else:
            playlist.queue_and_play(target)
        return True
    return False


if _play_from_explorer():
    sys.exit(0)

import engine  # noqa: E402
from bridge import Bridge, ping_existing  # noqa: E402


def only_copy_running():
    """Windows: True if no other Kidlat is running or starting (named mutex).
    Stops two copies (e.g. Windows startup + Firefox) sharing one download list."""
    if sys.platform != "win32":
        return True
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        handle = k32.CreateMutexW(None, False, "Local\\KidlatDownloadManager")
        if not handle:
            return True
        globals()["_KIDLAT_MUTEX"] = handle          # held until Kidlat closes
        return ctypes.get_last_error() != 183        # ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return True


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

    if "--native-host" in sys.argv:      # started by Firefox: launch the app, exit
        import nativehost
        nativehost.launch_app()
        return

    manager = engine.Manager(data_dir())
    port = int(manager.settings["port"])

    # already running? just bring that window to the front
    if ping_existing(port):
        if "--startup" not in sys.argv:
            ping_existing(port, "/show")
        return
    if not only_copy_running():          # another copy is starting right now
        import time
        for _ in range(30):
            time.sleep(0.5)
            if ping_existing(port):
                if "--startup" not in sys.argv:
                    ping_existing(port, "/show")
                break
        return

    from gui import App  # imported late so a second launch exits fast

    app = None
    from recorder import Recorder
    recorder = Recorder(manager)
    manager.recorder = recorder
    try:
        bridge = Bridge(port,
                        on_add=lambda items: app.call(lambda: app.handle_browser(items)),
                        on_show=lambda: app.call(app.show),
                        recorder=recorder)
        bridge_ok = True
    except OSError:
        bridge, bridge_ok = None, False

    app = App(manager, bridge_ok)
    if "--startup" in sys.argv:          # launched by Windows at login
        app.root.iconify()
    def setup_windows_links():
        # each step on its own, so one failing doesn't skip the others
        import autostart
        import nativehost
        import playlist
        for step in (autostart.refresh,                         # fix it if the folder moved
                     lambda: nativehost.register(data_dir()),   # Firefox can start Kidlat
                     lambda: playlist.set_explorer_menu(        # "Play in order" menu
                         manager.settings.get("explorer_play", True))):
            try:
                step()
            except Exception as e:  # noqa: BLE001
                print("Kidlat setup step failed:", e, file=sys.stderr)

    setup_windows_links()
    if bridge:
        bridge.start()

    # links passed on the command line:  Kidlat.exe https://example.com/file.zip
    for arg in sys.argv[1:]:
        if arg.startswith(("http://", "https://")):
            app.root.after(500, lambda a=arg: app.add_url(a))

    app.run()


if __name__ == "__main__":
    main()
