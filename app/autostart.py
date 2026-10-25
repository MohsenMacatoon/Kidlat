"""
Start Kidlat when the user logs in to Windows.

Uses the per-user "Run" key in the registry (HKEY_CURRENT_USER), the same
place IDM and most apps use. No admin rights, no PowerShell, no shortcut files.
"""

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Kidlat Download Manager"
OLD_LNK = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                       "Start Menu", "Programs", "Startup", "Kidlat.lnk")


def supported():
    return sys.platform == "win32"


def command():
    """The command Windows should run at login (always starts minimized)."""
    if getattr(sys, "frozen", False):                      # Kidlat.exe
        return f'"{sys.executable}" --startup'
    exe = sys.executable
    folder, name = os.path.split(exe)
    if name.lower() == "python.exe":                       # no console window
        w = os.path.join(folder, "pythonw.exe")
        if os.path.exists(w):
            exe = w
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kidlat.pyw")
    return f'"{exe}" "{script}" --startup'


def is_enabled():
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except OSError:
        return False


def set_enabled(on):
    """Returns None on success, or an error message."""
    if not supported():
        return "Only available on Windows."
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        return f"Windows blocked the change: {e}"
    # remove the shortcut made by the old "Start Kidlat with Windows.bat"
    try:
        if os.path.exists(OLD_LNK):
            os.remove(OLD_LNK)
    except OSError:
        pass
    return None


def refresh():
    """Keep the saved command correct if the Kidlat folder was moved."""
    if not is_enabled():
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            current, _ = winreg.QueryValueEx(k, NAME)
        if current != command():
            set_enabled(True)
    except OSError:
        pass
