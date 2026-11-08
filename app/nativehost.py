"""
Lets the Firefox extension start Kidlat when it isn't running.

Firefox "native messaging": Kidlat registers a small launcher in the registry
(HKCU\\Software\\Mozilla\\NativeMessagingHosts\\kidlat_launcher). Only the Kidlat
extension is allowed to use it. When the extension needs the app, Firefox runs
the launcher, which starts Kidlat as a normal, independent program and exits,
so closing Firefox never closes Kidlat or stops downloads.
"""

import json
import os
import subprocess
import sys

HOST = "kidlat_launcher"
EXTENSION_ID = "kidlat@mohsenmacatoon.github.io"
REG_KEY = r"Software\Mozilla\NativeMessagingHosts" + "\\" + HOST

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _app_command(*args):
    """Command that starts the Kidlat app itself."""
    if getattr(sys, "frozen", False):                      # Kidlat.exe
        return [sys.executable, *args]
    exe = sys.executable
    folder, name = os.path.split(exe)
    if name.lower() == "python.exe":
        w = os.path.join(folder, "pythonw.exe")
        if os.path.exists(w):
            exe = w
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kidlat.pyw")
    return [exe, script, *args]


def _quote(parts):
    return " ".join(f'"{p}"' if not p.startswith("--") else p for p in parts)


def register(data_dir):
    """Create/refresh the launcher. Safe to call on every start."""
    if sys.platform != "win32":
        return None
    import winreg
    folder = os.path.abspath(os.path.join(data_dir, "native"))
    os.makedirs(folder, exist_ok=True)
    bat = os.path.join(folder, HOST + ".bat")
    manifest = os.path.join(folder, HOST + ".json")
    command = _quote(_app_command("--native-host"))
    try:
        command.encode("mbcs")
        bat_text, bat_enc = "@echo off\r\n" + command + "\r\n", "mbcs"
    except UnicodeEncodeError:       # folder name with letters outside the Windows code page
        bat_text, bat_enc = "@echo off\r\nchcp 65001 >nul\r\n" + command + "\r\n", "utf-8"
    man_text = json.dumps({
        "name": HOST,
        "description": "Starts Kidlat Download Manager for its Firefox extension",
        "path": bat,
        "type": "stdio",
        "allowed_extensions": [EXTENSION_ID],
    }, indent=2)                     # JSON escapes every non-ASCII letter: plain ASCII file
    for path, text, enc in ((bat, bat_text, bat_enc), (manifest, man_text, "ascii")):
        try:
            with open(path, encoding=enc, newline="") as f:
                if f.read() == text:
                    continue
        except (OSError, UnicodeDecodeError):
            pass
        with open(path, "w", encoding=enc, newline="") as f:
            f.write(text)
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, manifest)
    except OSError as e:
        return str(e)
    return None


def launch_app():
    """Run by Firefox: start Kidlat on its own (outside Firefox) and exit."""
    cmd = _app_command("--startup")
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    if sys.platform == "win32":
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:   # break out of Firefox's job so Kidlat survives Firefox closing
            subprocess.Popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)
            return
        except OSError:
            pass
        subprocess.Popen(cmd, creationflags=flags, **kw)
    else:
        subprocess.Popen(cmd, start_new_session=True, **kw)
