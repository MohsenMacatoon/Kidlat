"""
Play videos/music in the right order.

When you select many files in File Explorer and press Play, Windows sends them
to the player in a scrambled order. A playlist fixes that: Kidlat writes one,
sorted exactly like File Explorer sorts by name ("Part 2" before "Part 10"),
and opens it in your default player.

Also adds File Explorer right-click items:
  - folders / empty space in a folder:  "Play all in order (Kidlat)"
  - selected video or music files:      "Play selected in order (Kidlat)"
"""

import functools
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from xml.sax.saxutils import escape

VIDEO = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".m4v", ".flv", ".mpg",
         ".mpeg", ".3gp", ".ts", ".ogv", ".m2ts", ".vob"}
AUDIO = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".aiff"}
MEDIA = VIDEO | AUDIO

PLAY_DIR = os.path.join(tempfile.gettempdir(), "Kidlat playlists")


# --------------------------------------------------------------------------- #
# sorting exactly like File Explorer ("Name" column)
# --------------------------------------------------------------------------- #
def _natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def explorer_sorted(paths):
    if sys.platform == "win32":
        try:
            import ctypes
            cmp = ctypes.windll.shlwapi.StrCmpLogicalW
            cmp.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            return sorted(paths, key=functools.cmp_to_key(
                lambda a, b: cmp(os.path.basename(a), os.path.basename(b))))
        except Exception:  # noqa: BLE001
            pass
    return sorted(paths, key=lambda p: _natural_key(os.path.basename(p)))


def media_in_folder(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    files = [os.path.join(folder, n) for n in names
             if os.path.splitext(n)[1].lower() in MEDIA
             and os.path.isfile(os.path.join(folder, n))]
    return explorer_sorted(files)


# --------------------------------------------------------------------------- #
# your default player
# --------------------------------------------------------------------------- #
ASSOCF_INIT_IGNOREUNKNOWN = 0x400
ASSOCSTR_COMMAND = 1


def open_command(ext):
    """The command Windows runs when you double-click a file of this type, e.g.
    '"C:\\Program Files\\VideoLAN\\VLC\\vlc.exe" --started-from-file "%1"'. None if unknown."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        size = wintypes.DWORD(4096)
        buf = ctypes.create_unicode_buffer(4096)
        hr = ctypes.windll.shlwapi.AssocQueryStringW(ASSOCF_INIT_IGNOREUNKNOWN, ASSOCSTR_COMMAND,
                                                    ext, None, buf, ctypes.byref(size))
        return buf.value.strip() if hr == 0 and buf.value.strip() else None
    except Exception:  # noqa: BLE001
        return None


def command_exe(cmd):
    """First part of a command line: the program."""
    cmd = (cmd or "").strip()
    if cmd.startswith('"'):
        return cmd[1:].split('"', 1)[0]
    return cmd.split(" ", 1)[0]


def fill_command(cmd, path):
    """Put `path` where the command expects the file (%1 or %L)."""
    mark = "\x00KIDLAT\x00"
    found = []
    c = re.sub(r"%[1Ll]", lambda m: found.append(1) or mark, cmd)
    c = os.path.expandvars(c)                            # %ProgramFiles% and friends
    c = re.sub(r"%[*~2-9IiDdVvWwUuSsHh](?![A-Za-z0-9_])", "", c)   # other placeholders
    if found:
        return c.replace(mark, path)
    return f'{c} "{path}"'


def pick_player(cmd):
    """("m3u8", command) for normal players (VLC, MPC, PotPlayer, mpv, ...), or
    ("wpl", None) for Windows Media Player, Store apps or no default player:
    a .wpl playlist opens in Windows Media Player."""
    exe = command_exe(cmd).lower() if cmd else ""
    name = os.path.basename(exe.replace("\\", "/"))
    if not exe or "wmplayer" in name or "\\windowsapps\\" in exe or "openwith" in name:
        return "wpl", None
    return "m3u8", cmd


def _start(path):
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", path], close_fds=True)


# --------------------------------------------------------------------------- #
# playlists
# --------------------------------------------------------------------------- #
def write_wpl(files, title, path):
    """Windows Media Player playlist (UTF-8, keeps any characters in names)."""
    items = "\n".join(f'      <media src="{escape(os.path.abspath(f), {chr(34): "&quot;"})}"/>'
                      for f in files)
    xml = ('<?wpl version="1.0"?>\n<smil>\n  <head>\n'
           '    <meta name="Generator" content="Kidlat"/>\n'
           f'    <meta name="ItemCount" content="{len(files)}"/>\n'
           f"    <title>{escape(title)}</title>\n  </head>\n  <body>\n    <seq>\n"
           f"{items}\n    </seq>\n  </body>\n</smil>\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)


def write_m3u8(files, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for p in files:
            f.write(f"#EXTINF:-1,{os.path.splitext(os.path.basename(p))[0]}\n")
            f.write(os.path.abspath(p) + "\n")


def play(files, title="Kidlat"):
    """Open `files` in your default player, in this exact order."""
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        return "No video or music files to play."
    os.makedirs(PLAY_DIR, exist_ok=True)
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip()[:80] or "Kidlat"
    kind, cmd = pick_player(open_command(os.path.splitext(files[0])[1].lower()))
    try:
        if kind == "m3u8":                    # VLC & co.: start it the way Windows would
            plist = os.path.join(PLAY_DIR, safe + ".m3u8")
            write_m3u8(files, plist)
            subprocess.Popen(fill_command(cmd, plist), close_fds=True)
            return None
        plist = os.path.join(PLAY_DIR, safe + ".wpl")   # Windows Media Player playlist
        write_wpl(files, title, plist)
        try:
            _start(plist)
        except OSError:                       # nothing opens .wpl here: try .m3u8
            plist = os.path.join(PLAY_DIR, safe + ".m3u8")
            write_m3u8(files, plist)
            _start(plist)
    except OSError as e:
        return f"Couldn't open the player: {e}"
    return None


# --------------------------------------------------------------------------- #
# File Explorer: "Play selected in order" with many files selected.
# Windows starts Kidlat once per selected file; the first one waits until the
# others have checked in, sorts all of them and plays them as one playlist.
# --------------------------------------------------------------------------- #
QUEUE_DIR = os.path.join(tempfile.gettempdir(), "Kidlat play queue")
QUIET = 1.2          # seconds with no new file = everyone has checked in
LATE = 4.0           # files arriving this soon after playing are added (playlist restarts)


def _take_queued():
    files = []
    try:
        names = os.listdir(QUEUE_DIR)
    except OSError:
        return files
    for x in names:
        if x.endswith(".txt"):
            p = os.path.join(QUEUE_DIR, x)
            try:
                with open(p, encoding="utf-8") as f:
                    files.append(f.read().strip())
                os.remove(p)
            except OSError:
                pass
    return [f for f in files if f]


def _count_queued():
    try:
        return sum(1 for x in os.listdir(QUEUE_DIR) if x.endswith(".txt"))
    except OSError:
        return 0


def queue_and_play(path):
    os.makedirs(QUEUE_DIR, exist_ok=True)
    tmp = os.path.join(QUEUE_DIR, uuid.uuid4().hex + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(os.path.abspath(path))
    os.replace(tmp, tmp[:-4] + ".txt")            # appears complete, never half-written
    lock = os.path.join(QUEUE_DIR, "leader.lock")
    try:                                          # clear a lock left by a crash
        if time.time() - os.path.getmtime(lock) > 30:
            os.remove(lock)
    except OSError:
        pass
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return                                     # another copy is collecting
    try:
        files = []
        last, quiet_since = -1, time.time()
        deadline = time.time() + 20
        while time.time() < deadline:              # wait until no new files arrive
            n = _count_queued()
            if n != last:
                last, quiet_since = n, time.time()
            elif time.time() - quiet_since > QUIET:
                break
            time.sleep(0.1)
            os.utime(lock)                         # "still collecting"
        files = explorer_sorted(list(dict.fromkeys(_take_queued())))
        title = os.path.basename(os.path.dirname(files[0])) if files else "Kidlat"
        play(files, title)
        # a very slow PC may start some copies late: add them and start over
        end = time.time() + LATE
        while time.time() < end:
            time.sleep(0.25)
            os.utime(lock)
            late = _take_queued()
            if late:
                files = explorer_sorted(list(dict.fromkeys(files + late)))
                time.sleep(QUIET)
                more = _take_queued()
                if more:
                    files = explorer_sorted(list(dict.fromkeys(files + more)))
                play(files, title)
                end = time.time() + LATE
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# File Explorer right-click menu (current user only, no admin needed)
# --------------------------------------------------------------------------- #
_CLASSES = r"Software\Classes"
VERB = "KidlatPlayInOrder"
FOLDER_KEYS = {
    _CLASSES + "\\Directory\\shell\\" + VERB: "%1",
    _CLASSES + "\\Directory\\Background\\shell\\" + VERB: "%V",
}
FILE_KEYS = [_CLASSES + rf"\SystemFileAssociations\{ext}\shell\{VERB}" for ext in sorted(MEDIA)]
OLD_KEYS = [_CLASSES + rf"\SystemFileAssociations\{t}\shell\{VERB}" for t in ("video", "audio")]


def _command(flag, arg):
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {flag} "{arg}"'
    exe = sys.executable
    folder, name = os.path.split(exe)
    if name.lower() == "python.exe" and os.path.exists(os.path.join(folder, "pythonw.exe")):
        exe = os.path.join(folder, "pythonw.exe")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kidlat.pyw")
    return f'"{exe}" "{script}" {flag} "{arg}"'


def menu_entries():
    """(registry key, label, command, many_files) for every right-click item."""
    out = [(k, "▶ Play all in order (Kidlat)", _command("--play-folder", arg), False)
           for k, arg in FOLDER_KEYS.items()]
    out += [(k, "▶ Play selected in order (Kidlat)", _command("--play-add", "%1"), True)
            for k in FILE_KEYS]
    return out


def set_explorer_menu(on):
    """Add or remove the right-click items. Returns None or an error message."""
    if sys.platform != "win32":
        return None
    import winreg
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kidlat.ico")

    def delete(key):
        for sub in (key + r"\command", key):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass

    for key in OLD_KEYS:                 # from the first version (could show twice)
        delete(key)
    try:
        for key, label, command, multi in menu_entries():
            if not on:
                delete(key)
                continue
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, label)
                if os.path.exists(icon):
                    winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
                if multi:   # show it for any number of selected files (not just 15)
                    winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key + r"\command", 0,
                                    winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, command)
    except OSError as e:
        return f"Windows blocked the change: {e}"
    return None
