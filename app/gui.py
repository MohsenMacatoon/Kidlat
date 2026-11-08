"""
Kidlat Download Manager - Windows window (tkinter, ships with Python).
"""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
from tkinter import filedialog, messagebox, ttk

import autostart
import playlist
import recorder
import engine
import tools
from engine import (COMPLETE, CONNECTING, DOWNLOADING, ERROR, PAUSED, QUEUED,
                    human_size, human_time)

APP_NAME = "Kidlat Download Manager"
ACCENT = "#2b6cb0"
SEG_DONE = "#2f855a"
SEG_LEFT = "#e2e8f0"
SEG_ACTIVE = "#ed8936"

SIDEBAR = [
    ("all", "All Downloads"),
    ("cat:Video", "    Video"),
    ("cat:Music", "    Music"),
    ("cat:Compressed", "    Compressed"),
    ("cat:Documents", "    Documents"),
    ("cat:Programs", "    Programs"),
    ("cat:Images", "    Images"),
    ("cat:Other", "    Other"),
    ("unfinished", "Unfinished"),
    ("finished", "Finished"),
]

COLUMNS = [
    ("name", "File Name", 300, "w"),
    ("size", "Size", 85, "e"),
    ("progress", "Progress", 75, "e"),
    ("status", "Status", 160, "w"),
    ("speed", "Speed", 90, "e"),
    ("eta", "Time Left", 80, "e"),
    ("added", "Added", 120, "w"),
]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def open_path(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as e:
        messagebox.showerror(APP_NAME, f"Could not open:\n{path}\n\n{e}")


def show_in_folder(path):
    if sys.platform == "win32":
        if os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            folder = os.path.dirname(path)
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)  # noqa: S606
    else:
        open_path(os.path.dirname(path))


MEDIA_ENDINGS = (".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ".mov", ".avi", ".flv",
                 ".wav", ".aac", ".ogg")


def strip_media_ending(name):
    """'Mr. Bean Ep. 1.mp4' -> 'Mr. Bean Ep. 1', but 'Mr. Bean Ep. 1' stays whole."""
    name = name.strip()
    if name.lower().endswith(MEDIA_ENDINGS):
        name = os.path.splitext(name)[0].rstrip()
    return name


def where(d):
    """The file on disk (or the unfinished file); None if it has no place yet."""
    if not d.folder or not d.filename:
        return None
    return d.path if d.status == COMPLETE else d.part_path


def status_text(d):
    if d.substatus and d.status in (CONNECTING, DOWNLOADING):
        return d.substatus
    if d.status == COMPLETE and d.error:
        return f"Complete - {d.error}"
    if d.kind == "record":
        if d.status == PAUSED and d.error:
            return d.error
        if d.status == PAUSED:
            return f"Paused at {recorder.clock(d.rec_pos)} - continue on the page"
    if d.status == DOWNLOADING:
        if d.kind == "media":
            return "Downloading"
        return f"Downloading ({d.active_conns})" if d.active_conns else "Downloading"
    if d.status == ERROR:
        return f"Error: {d.error}" if d.error else "Error"
    if d.error and d.status in (CONNECTING, DOWNLOADING):
        return "Retrying..."
    return d.status


def center(win, parent, w, h):
    parent.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
    win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")


# --------------------------------------------------------------------------- #
# Add-download dialog
# --------------------------------------------------------------------------- #
class AddDialog(tk.Toplevel):
    def __init__(self, app, url="", filename="", headers=None, from_browser=False):
        super().__init__(app.root)
        self.app = app
        self.m = app.m
        self.headers = headers or {}
        self.info = None
        self.info_url = None
        self.title("Download File Info" if from_browser else "Add New Download")
        self.resizable(False, False)
        self.transient(app.root)
        center(self, app.root, 620, 250)
        self.attributes("-topmost", True)
        self.after(1500, lambda: self.attributes("-topmost", False))

        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="URL:").grid(row=0, column=0, sticky="w", pady=4)
        self.url = tk.StringVar(value=url)
        e_url = ttk.Entry(f, textvariable=self.url)
        e_url.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)

        ttk.Label(f, text="Save As:").grid(row=1, column=0, sticky="w", pady=4)
        self.name = tk.StringVar(value=engine.safe_filename(filename) if filename else "")
        ttk.Entry(f, textvariable=self.name).grid(row=1, column=1, columnspan=2,
                                                 sticky="ew", pady=4)

        ttk.Label(f, text="Folder:").grid(row=2, column=0, sticky="w", pady=4)
        self.folder = tk.StringVar(value="")
        self.auto_folder = True
        ttk.Entry(f, textvariable=self.folder).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(f, text="Browse...", command=self.browse).grid(row=2, column=2,
                                                                  padx=(6, 0))

        self.info_lbl = ttk.Label(f, text="Checking file...", foreground="#555",
                                  wraplength=470)
        self.info_lbl.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 4))

        b = ttk.Frame(f)
        b.grid(row=4, column=0, columnspan=3, sticky="e", pady=(12, 0))
        self.btn_start = ttk.Button(b, text="Start Download", command=lambda: self.ok(True))
        self.btn_start.pack(side="left", padx=4)
        ttk.Button(b, text="Download Later", command=lambda: self.ok(False)).pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda e: self.ok(True))
        self.bind("<Escape>", lambda e: self.destroy())
        self.name.trace_add("write", lambda *_: self._update_folder())
        self._update_folder()

        if url:
            self.check()
        else:
            self.info_lbl.config(text="Paste a link and press Check.")
            ttk.Button(f, text="Check", command=self.check).grid(row=3, column=2, sticky="e")
            clip = self._clipboard_url()
            if clip:
                self.url.set(clip)
                self.check()
            e_url.focus_set()
        self.lift()
        self.focus_force()

    def _clipboard_url(self):
        try:
            t = self.clipboard_get().strip()
        except tk.TclError:
            return ""
        return t if t.startswith(("http://", "https://")) and " " not in t else ""

    def _update_folder(self):
        if self.auto_folder:
            name = self.name.get() or "file"
            self.folder.set(self.m.folder_for(name))

    def browse(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.folder.get()
                                    or self.m.settings["download_dir"])
        if d:
            self.auto_folder = False
            self.folder.set(os.path.normpath(d))

    def check(self):
        url = self.url.get().strip()
        if not url.startswith(("http://", "https://")):
            self.info_lbl.config(text="Enter a link that starts with http:// or https://")
            return
        self.info_lbl.config(text="Checking file...")

        def work():
            try:
                info = engine.probe(url, self.headers, self.m.settings["timeout"])
                err = None
            except Exception as e:  # noqa: BLE001
                info, err = None, str(e)
            self.app.call(lambda: self._checked(info, err, url))

        threading.Thread(target=work, daemon=True).start()

    def _checked(self, info, err, url=None):
        if not self.winfo_exists():
            return
        if url is not None and url != self.url.get().strip():
            return                                # the link was changed meanwhile
        if err:
            self.info_lbl.config(text=f"Couldn't check the file: {err[:90]}  "
                                      "(you can still try to download)")
            if not self.name.get():
                self.name.set(engine.guess_filename(self.url.get()))
            return
        self.info = info
        self.info_url = url
        if not self.name.get() or self.name.get() == getattr(self, "_auto_name", None):
            self.name.set(info["filename"])
            self._auto_name = info["filename"]
        resume = "Yes" if info["resumable"] else "No (restarts if paused)"
        self.info_lbl.config(text=f"Size: {human_size(info['size'])}     "
                                  f"Resume supported: {resume}     "
                                  f"Type: {engine.category_for(self.name.get())}")

    def ok(self, start):
        url = self.url.get().strip()
        if not url.startswith(("http://", "https://")):
            messagebox.showwarning(APP_NAME, "Please enter a valid link.", parent=self)
            return
        name = engine.safe_filename(self.name.get() or engine.guess_filename(url))
        folder = self.folder.get().strip() or None
        info = self.info if self.info and self.info.get("final_url") \
            and self.info_url == url else None
        dl = self.m.add(url, name, folder, self.headers, start=start, info=info)
        self.destroy()
        if start:
            self.app.open_progress(dl.id)


# --------------------------------------------------------------------------- #
# Progress dialog (per-download, shows every connection)
# --------------------------------------------------------------------------- #
class ProgressDialog(tk.Toplevel):
    def __init__(self, app, did):
        super().__init__(app.root)
        self.app = app
        self.did = did
        self.title("Download progress")
        self.resizable(True, False)
        center(self, app.root, 560, 330)
        self.minsize(460, 330)
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        self.vars = {}
        rows = [("name", "File"), ("url", "Address"), ("status", "Status"),
                ("size", "File size"), ("done", "Downloaded"),
                ("speed", "Transfer rate"), ("eta", "Time left"),
                ("resume", "Resume capability")]
        for i, (k, label) in enumerate(rows):
            ttk.Label(f, text=label + ":", foreground="#555").grid(row=i, column=0, sticky="w")
            v = tk.StringVar()
            ttk.Label(f, textvariable=v).grid(row=i, column=1, sticky="w", padx=8)
            self.vars[k] = v

        d0 = app.m.get(did)
        ttk.Label(f, text="Progress:" if d0 and d0.kind == "media" else
                  "Each connection fills its own part of the file:",
                  foreground="#555").grid(row=len(rows), column=0, columnspan=2,
                                          sticky="w", pady=(10, 2))
        self.canvas = tk.Canvas(f, height=18, highlightthickness=1,
                                highlightbackground="#cbd5e0", bg=SEG_LEFT)
        self.canvas.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="ew")

        b = ttk.Frame(f)
        b.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        self.btn_toggle = ttk.Button(b, text="Pause", command=self.toggle)
        self.btn_toggle.pack(side="left", padx=4)
        self.btn_open = ttk.Button(b, text="Open", command=self.open_file)
        self.btn_folder = ttk.Button(b, text="Open Folder", command=self.open_folder)
        self.btn_hide = ttk.Button(b, text="Hide", command=self.destroy)
        self.btn_hide.pack(side="left", padx=4)
        self.refresh()

    def toggle(self):
        d = self.app.m.get(self.did)
        if not d:
            return
        if d._job or d.status == QUEUED:
            self.app.m.pause(d.id)
        else:
            self.app.m.start(d.id)

    def open_file(self):
        d = self.app.m.get(self.did)
        if d and d.folder and d.filename:
            open_path(d.path)
            self.destroy()

    def open_folder(self):
        d = self.app.m.get(self.did)
        p = where(d) if d else None
        if p:
            show_in_folder(p)

    def refresh(self):
        if not self.winfo_exists():
            return
        d = self.app.m.get(self.did)
        if not d:
            self.destroy()
            return
        self.title(f"{d.progress:.0f}% {d.filename or 'Download'}")
        v = self.vars
        v["name"].set((d.filename or "?")[:70])
        v["url"].set(d.url[:70] + ("..." if len(d.url) > 70 else ""))
        st = status_text(d)
        if d.error:
            st += f" - {d.error[:70]}"
        v["status"].set(st)
        v["size"].set(human_size(d.size))
        v["done"].set(f"{human_size(d.downloaded)}  ({d.progress:.1f}%)")
        v["speed"].set(f"{human_size(d.speed)}/s" if d.speed else "")
        v["eta"].set(human_time(d.eta))
        v["resume"].set("Yes" if d.resumable else ("No" if d.probed else "?"))
        if d.kind == "media":
            v["resume"].set(f"Yes  •  {d.fmt_label}" if d.fmt_label else "Yes")
        if d.kind == "record" and self.btn_toggle.winfo_ismapped():
            self.btn_toggle.pack_forget()
        running = bool(d._job) or d.status == QUEUED
        self.btn_toggle.config(text="Pause" if running else "Resume")
        if d.status == COMPLETE and not getattr(self, "_done_shown", False):
            self._done_shown = True
            self.btn_toggle.pack_forget()
            self.btn_hide.config(text="Close")
            self.btn_open.pack(side="left", padx=4, before=self.btn_hide)
            self.btn_folder.pack(side="left", padx=4, before=self.btn_hide)
        self._draw_segments(d)
        self.after(500, self.refresh)

    def _draw_segments(self, d):
        c = self.canvas
        c.delete("all")
        w = max(1, c.winfo_width())
        h = 18
        if d.status == COMPLETE:
            c.create_rectangle(0, 0, w, h, fill=SEG_DONE, width=0)
            return
        if d.size <= 0:
            return
        if d.kind == "media":
            c.create_rectangle(0, 0, d.downloaded / d.size * w, h, fill=SEG_DONE, width=0)
            return
        with d.lock:
            segs = [(s.start, s.pos, s.busy) for s in d.segments]
        for start, pos, busy in segs:
            x0 = start / d.size * w
            x1 = pos / d.size * w
            if x1 > x0:
                c.create_rectangle(x0, 0, x1, h, fill=SEG_DONE, width=0)
            if busy:
                c.create_rectangle(x1, 0, x1 + 3, h, fill=SEG_ACTIVE, width=0)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        s = app.m.settings
        self.title("Options")
        self.resizable(False, False)
        self.transient(app.root)
        center(self, app.root, 640, 420)
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        self.dir = tk.StringVar(value=s["download_dir"])
        self.cats = tk.BooleanVar(value=s["use_categories"])
        self.conns = tk.IntVar(value=s["connections"])
        self.active = tk.IntVar(value=s["max_active"])
        self.limit = tk.IntVar(value=s["speed_limit_kbps"])
        self.ask = tk.BooleanVar(value=s["ask_on_browser_capture"])
        self.popup = tk.BooleanVar(value=s.get("show_complete_popup", True))
        self.compat = tk.BooleanVar(value=s.get("compat_mp4", True))
        self.explorer = tk.BooleanVar(value=s.get("explorer_play", True))
        self.port = tk.IntVar(value=s["port"])
        self.autostart = tk.BooleanVar(value=autostart.is_enabled())

        r = 0
        ttk.Label(f, text="Save downloads to:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.dir).grid(row=r, column=1, sticky="ew", pady=4)
        ttk.Button(f, text="Browse...", command=self.browse).grid(row=r, column=2, padx=(6, 0))
        r += 1
        ttk.Checkbutton(f, text="Sort into folders by type (Video, Music, Programs...)",
                        variable=self.cats).grid(row=r, column=0, columnspan=3, sticky="w", pady=4)
        r += 1
        ttk.Label(f, text="Connections per download:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=1, to=32, textvariable=self.conns, width=6).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(f, text="Downloads at the same time:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=1, to=10, textvariable=self.active, width=6).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(f, text="Speed limit (KB/s, 0 = none):").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=0, to=1_000_000, increment=50, textvariable=self.limit,
                    width=10).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Checkbutton(f, text="Ask before starting downloads caught from Firefox",
                        variable=self.ask).grid(row=r, column=0, columnspan=3, sticky="w", pady=4)
        r += 1
        ttk.Checkbutton(f, text="Show a popup when a download finishes",
                        variable=self.popup).grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        ttk.Checkbutton(f, text="Save videos as MP4 that plays in every player, incl. Windows Media Player (needs FFmpeg)",
                        variable=self.compat).grid(row=r, column=0, columnspan=3, sticky="w", pady=4)
        r += 1
        ttk.Checkbutton(f, text='Add "Play in order" to the File Explorer right-click menu',
                        variable=self.explorer).grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        if autostart.supported():
            ttk.Checkbutton(f, text="Start Kidlat when I log in to Windows (opens minimized)",
                            variable=self.autostart).grid(row=r, column=0, columnspan=3,
                                                          sticky="w", pady=4)
            r += 1
        ttk.Label(f, text="Browser bridge port (restart app):").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=1024, to=65535, textvariable=self.port, width=8).grid(row=r, column=1, sticky="w")
        r += 1
        b = ttk.Frame(f)
        b.grid(row=r, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(b, text="Save", command=self.save).pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    def browse(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.dir.get())
        if d:
            self.dir.set(os.path.normpath(d))

    def save(self):
        try:
            s = self.app.m.settings
            s["download_dir"] = self.dir.get().strip() or engine.DEFAULT_SETTINGS["download_dir"]
            s["use_categories"] = self.cats.get()
            s["connections"] = max(1, min(32, int(self.conns.get())))
            s["max_active"] = max(1, min(10, int(self.active.get())))
            s["speed_limit_kbps"] = max(0, int(self.limit.get()))
            s["ask_on_browser_capture"] = self.ask.get()
            s["show_complete_popup"] = self.popup.get()
            s["compat_mp4"] = self.compat.get()
            s["explorer_play"] = self.explorer.get()
            s["port"] = int(self.port.get())
        except (tk.TclError, ValueError):
            messagebox.showerror(APP_NAME, "Please enter whole numbers.", parent=self)
            return
        self.app.m.save_settings()
        self.app.m.schedule()
        playlist.set_explorer_menu(self.explorer.get())
        if autostart.supported() and self.autostart.get() != autostart.is_enabled():
            err = autostart.set_enabled(self.autostart.get())
            if err:
                messagebox.showerror(APP_NAME, err, parent=self)
                return
        self.destroy()


# --------------------------------------------------------------------------- #
# Video Tools (yt-dlp / Deno / FFmpeg)
# --------------------------------------------------------------------------- #
class VideoToolsDialog(tk.Toplevel):
    def __init__(self, app, message=""):
        super().__init__(app.root)
        self.app = app
        self.m = app.m
        self.title("Video Tools")
        self.resizable(False, False)
        self.transient(app.root)
        center(self, app.root, 640, 360)
        self.info = {}
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        intro = message or ("These free tools let Kidlat download from YouTube and "
                            "other video sites. Pick what you need.")
        ttk.Label(f, text=intro, wraplength=600, justify="left").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        self.rows = {}
        for i, (name, spec) in enumerate(tools.TOOLS.items(), start=1):
            var = tk.BooleanVar(value=not self.m.tools.have(name))
            cb = ttk.Checkbutton(f, variable=var, text=spec["title"])
            cb.grid(row=i * 2 - 1, column=0, sticky="w", pady=(6, 0))
            size = ttk.Label(f, text=f"~{human_size(spec['approx'])}", foreground="#555")
            size.grid(row=i * 2 - 1, column=2, sticky="e", padx=10, pady=(6, 0))
            st = ttk.Label(f, text="", width=16)
            st.grid(row=i * 2 - 1, column=3, sticky="w", pady=(6, 0))
            ttk.Label(f, text=spec["why"], foreground="#555").grid(
                row=i * 2, column=0, columnspan=3, sticky="w", padx=(22, 0))
            self.rows[name] = (var, cb, size, st)

        r = len(tools.TOOLS) * 2 + 1
        self.total = ttk.Label(f, text="", foreground="#555", wraplength=600)
        self.total.grid(row=r, column=0, columnspan=4, sticky="w", pady=(14, 0))
        b = ttk.Frame(f)
        b.grid(row=r + 1, column=0, columnspan=4, sticky="e", pady=(14, 0))
        self.btn_get = ttk.Button(b, text="Download selected", command=self.get)
        self.btn_get.pack(side="left", padx=4)
        self.btn_upd = ttk.Button(b, text="Update yt-dlp", command=self.update_ytdlp)
        self.btn_upd.pack(side="left", padx=4)
        ttk.Button(b, text="Fix a recording...", command=self.pick_repair).pack(side="left", padx=4)
        ttk.Button(b, text="Close", command=self.destroy).pack(side="left", padx=4)
        for v, *_ in self.rows.values():
            v.trace_add("write", lambda *_: self._totals())
        threading.Thread(target=self._probe_sizes, daemon=True).start()
        self.refresh()

    def _probe_sizes(self):
        for name, spec in tools.TOOLS.items():
            try:
                info = engine.probe(spec["url"], None, 30)
            except Exception:  # noqa: BLE001
                continue
            self.info[name] = info
            self.app.call(lambda n=name, i=info: self._sized(n, i))

    def _sized(self, name, info):
        if self.winfo_exists() and info.get("size", -1) > 0:
            self.rows[name][2].config(text=human_size(info["size"]))
            self._totals()

    def _totals(self):
        total = 0
        for name, (var, *_rest) in self.rows.items():
            if var.get() and not self.m.tools.have(name):
                total += (self.info.get(name) or {}).get("size", 0) or tools.TOOLS[name]["approx"]
        self.total.config(text=(f"Selected: {human_size(total)}. Downloads resume if your "
                                "internet drops, so you can pause and continue later.")
                          if total else "Everything selected is installed.")

    def _tool_download(self, name):
        for d in self.m.downloads:
            if d.tag == "tool:" + name and d.status != COMPLETE:
                return d
        return None

    def refresh(self):
        if not self.winfo_exists():
            return
        for name, (var, cb, _size, st) in self.rows.items():
            d = self._tool_download(name)
            if self.m.tools.have(name):
                st.config(text="Installed ✓", foreground="#2f855a")
                cb.state(["disabled"])
                var.set(False)
            elif d:
                txt = f"{d.progress:.0f}%" if d._job else d.status
                st.config(text=f"Downloading {txt}" if d._job else txt,
                          foreground=ACCENT if d.status != ERROR else "#c53030")
                cb.state(["disabled"])
            else:
                st.config(text="Not installed", foreground="#555")
                cb.state(["!disabled"])
        self.btn_upd.state(["!disabled"] if self.m.tools.have("yt-dlp") else ["disabled"])
        self.after(800, self.refresh)

    def get(self):
        added = 0
        for name, (var, *_rest) in self.rows.items():
            if not var.get() or self.m.tools.have(name):
                continue
            d = self._tool_download(name)
            if d:
                self.m.start(d.id)
            else:
                self.m.add_tool(name, self.info.get(name))
            added += 1
        if not added:
            messagebox.showinfo(APP_NAME, "Nothing to download - tick a tool first.",
                                parent=self)

    def pick_repair(self):
        files = filedialog.askopenfilenames(
            parent=self, title="Recordings that freeze when you skip",
            initialdir=self.m.folder_for("a.webm"), filetypes=[("WebM video", "*.webm")])
        if files:
            self.app.repair_files(list(files))

    def update_ytdlp(self):
        self.btn_upd.state(["disabled"])
        self.btn_upd.config(text="Updating...")

        def work():
            try:
                ok, msg = self.m.tools.update_ytdlp()
            except Exception as e:  # noqa: BLE001
                ok, msg = False, str(e)
            self.app.call(lambda: self._updated(ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def _updated(self, ok, msg):
        if self.winfo_exists():
            self.btn_upd.config(text="Update yt-dlp")
        (messagebox.showinfo if ok else messagebox.showerror)(
            APP_NAME, msg, parent=self if self.winfo_exists() else self.app.root)


# --------------------------------------------------------------------------- #
# Quality picker for video-site downloads
# --------------------------------------------------------------------------- #
class MediaDialog(tk.Toplevel):
    def __init__(self, app, url, fallback="", title="", headers=None, url_headers=None):
        super().__init__(app.root)
        self.app = app
        self.m = app.m
        self.url = url
        self.fallback = fallback if fallback and fallback != url else ""
        self.headers = headers or {}
        self.url_headers = url_headers or {}
        self.vtitle = title
        self.first_title = title or ""
        self.choices = []
        self.used_url = url
        self.title("Download Video")
        self.resizable(False, False)
        self.transient(app.root)
        center(self, app.root, 600, 460)
        self.attributes("-topmost", True)
        self.after(1500, lambda: self.attributes("-topmost", False))

        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        self.name_l = ttk.Label(f, text=title or url, font=("Segoe UI", 10, "bold"),
                                wraplength=560)
        self.name_l.grid(row=0, column=0, columnspan=3, sticky="w")
        self.msg = ttk.Label(f, text="Getting video info... (this can take a few seconds)",
                             foreground="#555", wraplength=560)
        self.msg.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 8))

        self.q = tk.IntVar(value=0)
        self.qbox = ttk.Labelframe(f, text="Quality", padding=8)
        self.qbox.grid(row=2, column=0, columnspan=3, sticky="ew")

        ttk.Label(f, text="Save as:").grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.save_as = tk.StringVar(value=engine.safe_filename(title) if title else "")
        ttk.Entry(f, textvariable=self.save_as).grid(row=4, column=1, columnspan=2,
                                                     sticky="ew", pady=(10, 0), padx=(6, 0))
        ttk.Label(f, text="Folder:").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.folder = tk.StringVar(value=self.m.folder_for("video.mp4"))
        self.auto_folder = True
        ttk.Entry(f, textvariable=self.folder).grid(row=3, column=1, sticky="ew",
                                                    pady=(10, 0), padx=6)
        ttk.Button(f, text="Browse...", command=self.browse).grid(row=3, column=2,
                                                                  pady=(10, 0))
        b = ttk.Frame(f)
        b.grid(row=5, column=0, columnspan=3, sticky="e", pady=(14, 0))
        self.btn_start = ttk.Button(b, text="Start Download", command=lambda: self.ok(True))
        self.btn_later = ttk.Button(b, text="Download Later", command=lambda: self.ok(False))
        self.btn_start.pack(side="left", padx=4)
        self.btn_later.pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.btn_start.state(["disabled"])
        self.btn_later.state(["disabled"])
        self.bind("<Escape>", lambda e: self.destroy())
        self.lift()
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        info, err = None, ""
        for u in [self.url, self.fallback]:
            if not u:
                continue
            try:
                h = dict(self.headers)
                h.update(self.url_headers.get(u) or {})
                info = self.m.tools.info(u, h)
                self.used_url = u
                break
            except Exception as e:  # noqa: BLE001
                err = str(e)
        self.app.call(lambda: self._loaded(info, err))

    def _loaded(self, info, err):
        if not self.winfo_exists():
            return
        if not info:
            self.msg.config(text="Couldn't get this video:\n" + tools.clean_error(err),
                            foreground="#c53030")
            return
        if info.get("is_live"):
            self.msg.config(text="This is a live stream. Kidlat can only download "
                                 "videos that have finished streaming.",
                            foreground="#c53030")
            return
        self.vtitle = info.get("title") or self.vtitle or "video"
        self.name_l.config(text=self.vtitle)
        if not self.save_as.get().strip() or self.save_as.get() == engine.safe_filename(self.first_title):
            self.save_as.set(engine.safe_filename(self.vtitle))
        dur = info.get("duration")
        has_ff = self.m.tools.have("ffmpeg")
        self.choices = tools.choices(info, has_ff)
        notes = []
        if dur:
            notes.append(f"Length {human_time(dur)}")
        site = info.get("extractor_key") or ""
        if site and site.lower() != "generic":
            notes.append(f"from {site}")
        if not has_ff:
            notes.append("HD needs FFmpeg (Video Tools)")
        self.msg.config(text="   •   ".join(notes), foreground="#555")
        vids = [c for c in self.choices if not c["audio"]]
        auds = [c for c in self.choices if c["audio"]]
        if len(vids) > 10:                        # too many to list: keep the useful ones
            keep = vids[:10]
            self.choices = keep + auds
        shown = self.choices
        for i, c in enumerate(shown):
            size = human_size(c["size"]) if c["size"] else "size unknown"
            text = f"{c['label']}   ({size})"
            if c.get("needs_ffmpeg"):
                text += "   - needs FFmpeg"
            rb = ttk.Radiobutton(self.qbox, text=text, value=i, variable=self.q,
                                 command=self._quality_changed)
            rb.grid(row=i, column=0, sticky="w", pady=1)
            if c.get("needs_ffmpeg"):
                rb.state(["disabled"])
        if any(c.get("needs_ffmpeg") for c in self.choices):
            ttk.Button(self.qbox, text="Get FFmpeg for HD...",
                       command=lambda: VideoToolsDialog(self.app)).grid(
                row=0, column=1, rowspan=2, sticky="ne", padx=(20, 0))
        self.q.set(tools.default_choice(self.choices))
        self._quality_changed()
        self._fit()
        self.btn_start.state(["!disabled"])
        self.btn_later.state(["!disabled"])
        self.btn_start.focus_set()
        self.bind("<Return>", lambda e: self.ok(True))

    def _fit(self):
        """Grow the window so every quality and the buttons are visible."""
        self.update_idletasks()
        need = self.winfo_reqheight() + 10
        if need > self.winfo_height():
            x, y = self.winfo_x(), self.winfo_y()
            top = max(0, min(y, self.winfo_screenheight() - need - 60))
            self.geometry(f"{self.winfo_width()}x{need}+{x}+{top}")

    def _quality_changed(self):
        if self.auto_folder and self.choices:
            c = self.choices[self.q.get()]
            self.folder.set(self.m.folder_for("a.m4a" if c["audio"] else "a.mp4"))

    def browse(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.folder.get())
        if d:
            self.auto_folder = False
            self.folder.set(os.path.normpath(d))

    def ok(self, start):
        if not self.choices:
            return
        c = self.choices[self.q.get()]
        other = self.fallback if self.used_url == self.url else self.url
        name = strip_media_ending(self.save_as.get()) or self.vtitle
        dl = self.m.add_media(self.used_url, name, c["args"], c["label"],
                              c["size"], fallback_url=other, headers=self.headers,
                              folder=self.folder.get().strip() or None,
                              audio=c["audio"], start=start,
                              url_headers=self.url_headers)
        self.destroy()
        if start:
            self.app.open_progress(dl.id)


# --------------------------------------------------------------------------- #
# Rename
# --------------------------------------------------------------------------- #
def rename_dialog(app, dl, parent=None):
    parent = parent or app.root
    win = tk.Toplevel(parent)
    win.title("Rename")
    win.resizable(False, False)
    win.transient(parent)
    win.attributes("-topmost", True)
    center(win, app.root, 520, 140)
    f = ttk.Frame(win, padding=12)
    f.pack(fill="both", expand=True)
    stem, ext = os.path.splitext(dl.filename or "")
    ttk.Label(f, text=f"New name (the {ext or 'file'} ending is kept):").pack(anchor="w")
    v = tk.StringVar(value=stem)
    e = ttk.Entry(f, textvariable=v, width=60)
    e.pack(fill="x", pady=8)
    e.focus_set()
    e.select_range(0, "end")
    msg = ttk.Label(f, text="", foreground="#c53030")
    msg.pack(anchor="w")

    def ok(_e=None):
        name = v.get().strip()
        if ext and name.lower().endswith(ext.lower()):
            name = name[:-len(ext)].rstrip()
        if not name:
            return
        err = app.m.rename(dl.id, name + ext)
        if err:
            msg.config(text=err)
        else:
            win.destroy()

    b = ttk.Frame(f)
    b.pack(anchor="e")
    ttk.Button(b, text="Rename", command=ok).pack(side="left", padx=4)
    ttk.Button(b, text="Cancel", command=win.destroy).pack(side="left", padx=4)
    win.bind("<Return>", ok)
    win.bind("<Escape>", lambda _e: win.destroy())
    return win


# --------------------------------------------------------------------------- #
# Completion popup
# --------------------------------------------------------------------------- #
class DonePopup(tk.Toplevel):
    def __init__(self, app, dl, count=1):
        super().__init__(app.root)
        self.title("Download complete")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"440x150+{sw - 460}+{sh - 230}")
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        head = ("Recording saved" if dl.kind == "record" else
                "Download complete" if count == 1 else f"{count} downloads complete")
        ttk.Label(f, text=head, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.dl = dl
        self.app = app
        self.name_l = ttk.Label(f, text=dl.filename[:55])
        self.name_l.pack(anchor="w", pady=(4, 0))
        ttk.Label(f, text=human_size(dl.size), foreground="#555").pack(anchor="w")
        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(10, 0))
        ttk.Button(b, text="Open", command=lambda: (open_path(dl.path), self.destroy())).pack(side="left", padx=3)
        ttk.Button(b, text="Open Folder", command=lambda: (show_in_folder(dl.path), self.destroy())).pack(side="left", padx=3)
        ttk.Button(b, text="Rename", command=self.rename).pack(side="left", padx=3)
        ttk.Button(b, text="Close", command=self.destroy).pack(side="left", padx=3)
        self.closer = self.after(10000, lambda: self.winfo_exists() and self.destroy())

    def rename(self):
        self.after_cancel(self.closer)              # stay open while renaming
        w = rename_dialog(self.app, self.dl, self)
        self.wait_window(w)
        if self.winfo_exists():
            self.name_l.config(text=self.dl.filename[:55])
            self.closer = self.after(6000, lambda: self.winfo_exists() and self.destroy())


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, manager, bridge_ok=True):
        self.m = manager
        self.bridge_ok = bridge_ok
        self.inbox = queue.Queue()
        self.progress_windows = {}
        self.filter = "all"

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1050x580")
        self.root.minsize(760, 380)
        self._set_icon()
        self._style()
        self._build()
        self.m.completion_listeners.append(lambda d: self.call(lambda: self._completed(d)))
        self.m.tools.listeners.append(
            lambda n, ok, msg: self.call(lambda: self._tool_done(n, ok, msg)))
        self.pending_media = None
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.after(300, self._poll)

    # --- thread-safe calls into the UI -----------------------------------
    def call(self, fn):
        self.inbox.put(fn)

    def _set_icon(self):
        here = os.path.dirname(os.path.abspath(sys.argv[0]))
        for base in (getattr(sys, "_MEIPASS", ""), here,
                     os.path.dirname(os.path.abspath(__file__))):
            ico = os.path.join(base, "kidlat.ico")
            if base and os.path.exists(ico):
                try:
                    self.root.iconbitmap(default=ico)
                except tk.TclError:
                    pass
                return

    def _style(self):
        st = ttk.Style(self.root)
        for theme in ("vista", "winnative", "clam"):
            if theme in st.theme_names():
                st.theme_use(theme)
                break
        st.configure("Treeview", rowheight=24)
        st.configure("Tool.TButton", padding=(10, 6))

    def _build(self):
        r = self.root
        # toolbar
        tb = ttk.Frame(r, padding=(6, 6))
        tb.pack(fill="x")
        buttons = [
            ("➕  Add URL", self.add_url),
            ("▶  Resume", self.resume_sel),
            ("⏸  Pause", self.pause_sel),
            ("⏩  Resume All", self.m.start_all),
            ("⏹  Pause All", self.m.pause_all),
            ("🗑  Delete", self.delete_sel),
            ("📂  Open Folder", self.folder_sel),
            ("🎬  Video Tools", lambda: VideoToolsDialog(self)),
            ("⚙  Options", lambda: SettingsDialog(self)),
        ]
        for text, cmd in buttons:
            ttk.Button(tb, text=text, command=cmd, style="Tool.TButton").pack(side="left", padx=2)

        body = ttk.PanedWindow(r, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6)

        # sidebar
        side = ttk.Frame(body)
        self.side = ttk.Treeview(side, show="tree", selectmode="browse")
        for key, label in SIDEBAR:
            self.side.insert("", "end", iid=key, text=label)
        self.side.selection_set("all")
        self.side.bind("<<TreeviewSelect>>", self._on_filter)
        self.side.pack(fill="both", expand=True)
        body.add(side, weight=0)

        # list
        main = ttk.Frame(body)
        self.tree = ttk.Treeview(main, columns=[c[0] for c in COLUMNS],
                                 show="headings", selectmode="extended")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key == "name"))
        self.tree.tag_configure("done", foreground="#2f855a")
        self.tree.tag_configure("err", foreground="#c53030")
        self.tree.tag_configure("run", foreground=ACCENT)
        ys = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        body.add(main, weight=1)

        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Button-3>", self._on_right)
        self.tree.bind("<Delete>", lambda e: self.delete_sel())
        self.tree.bind("<F2>", lambda e: self.rename_sel())
        self.root.bind("<Control-n>", lambda e: self.add_url())
        self.root.bind("<Control-v>", lambda e: self.add_url()
                       if self.root.focus_get() in (self.tree, self.side) else None)

        # status bar
        sb = ttk.Frame(r, padding=(8, 4))
        sb.pack(fill="x")
        self.status_l = ttk.Label(sb, text="")
        self.status_l.pack(side="left")
        bridge = (f"Firefox bridge: listening on port {self.m.settings['port']}"
                  if self.bridge_ok else
                  f"Firefox bridge: port {self.m.settings['port']} is busy - change it in Options")
        ttk.Label(sb, text=bridge, foreground="#555" if self.bridge_ok else "#c53030").pack(side="right")

        # context menu
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Open", command=self.open_sel)
        self.menu.add_command(label="▶ Play in order", command=self.play_sel)
        self.menu.add_command(label="Open Folder", command=self.folder_sel)
        self.menu.add_command(label="Show Progress", command=self.progress_sel)
        self.menu.add_separator()
        self.menu.add_command(label="Resume", command=self.resume_sel)
        self.menu.add_command(label="Pause", command=self.pause_sel)
        self.menu.add_command(label="Redownload", command=self.redownload_sel)
        self.menu.add_command(label="Finish recording (join parts)", command=self.finish_rec_sel)
        self.menu.add_command(label="Convert to MP4 (plays in every player)", command=self.compat_sel)
        self.menu.add_command(label="Fix freezing when skipping (WebM)", command=self.repair_sel)
        self.menu.add_command(label="Convert to normal speed (fast recording)", command=self.speed_sel)
        self.menu.add_command(label="Refresh Link (expired link)...", command=self.refresh_sel)
        self.menu.add_separator()
        self.menu.add_command(label="Rename...   (F2)", command=self.rename_sel)
        self.menu.add_command(label="Copy Link", command=self.copy_sel)
        self.menu.add_command(label="Delete", command=self.delete_sel)
        self.menu.add_command(label="Delete With File", command=lambda: self.delete_sel(True))

    # --- selection helpers -----------------------------------------------
    def selected(self):
        return [d for d in (self.m.get(i) for i in self.tree.selection()) if d]

    def _on_filter(self, _e=None):
        sel = self.side.selection()
        self.filter = sel[0] if sel else "all"
        self.tree.delete(*self.tree.get_children())
        self.refresh()

    def _visible(self, d):
        f = self.filter
        if f == "all":
            return True
        if f == "finished":
            return d.status == COMPLETE
        if f == "unfinished":
            return d.status != COMPLETE
        if f.startswith("cat:"):
            return d.category == f[4:]
        return True

    def _on_double(self, e):
        iid = self.tree.identify_row(e.y)
        d = self.m.get(iid) if iid else None
        if not d:
            return
        if d.status == COMPLETE:
            open_path(d.path)
        else:
            self.open_progress(d.id)

    def _on_right(self, e):
        iid = self.tree.identify_row(e.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if self.tree.selection():
            self.menu.tk_popup(e.x_root, e.y_root)

    # --- actions ---------------------------------------------------------
    def add_url(self, url="", filename="", headers=None, from_browser=False):
        AddDialog(self, url, filename, headers, from_browser)

    def open_progress(self, did):
        w = self.progress_windows.get(did)
        if w is not None and w.winfo_exists():
            w.lift()
            return
        self.progress_windows[did] = ProgressDialog(self, did)

    def resume_sel(self):
        for d in self.selected():
            self.m.start(d.id)

    def pause_sel(self):
        for d in self.selected():
            self.m.pause(d.id)

    def play_sel(self):
        files = []
        for d in self.selected():
            if d.status != COMPLETE:
                continue
            if not d.folder or not d.filename:
                continue
            if d.kind == "record" and d.rec_parts:    # saved as parts: play them all
                files += [os.path.join(d.folder, n) for n in d.rec_parts]
            else:
                files.append(os.path.join(d.folder, d.filename))
        files = [f for f in files if os.path.splitext(f)[1].lower() in playlist.MEDIA]
        if not files:
            messagebox.showinfo(APP_NAME, "Select finished videos or music first "
                                "(Ctrl+click or Shift+click to pick many).", parent=self.root)
            return
        err = playlist.play(playlist.explorer_sorted(files), "Kidlat")
        if err:
            messagebox.showerror(APP_NAME, err, parent=self.root)

    def open_sel(self):
        for d in self.selected()[:1]:
            if d.status == COMPLETE:
                open_path(d.path)

    def folder_sel(self):
        sel = self.selected()
        p = where(sel[0]) if sel else None
        if p:
            show_in_folder(p)
        else:
            folder = self.m.settings["download_dir"]
            os.makedirs(folder, exist_ok=True)
            open_path(folder)

    def progress_sel(self):
        for d in self.selected()[:1]:
            self.open_progress(d.id)

    def finish_rec_sel(self):
        rec = getattr(self.m, "recorder", None)
        for d in self.selected():
            if d.kind == "record" and d.status != COMPLETE and rec:
                threading.Thread(target=rec.done, args=(d.id,), daemon=True).start()

    def compat_sel(self):
        todo = [d for d in self.selected() if d.status == COMPLETE and not d.rec_parts]
        if not todo:
            messagebox.showinfo(APP_NAME, "Pick a finished video.", parent=self.root)
            return
        if not self.m.tools.have("ffmpeg"):
            messagebox.showinfo(APP_NAME, "This needs FFmpeg. Get it in Video Tools first.",
                                parent=self.root)
            return

        def work():
            done, errs = 0, []
            for d in todo:
                before = d.filename
                err = self.m.make_compatible(d, force=True)
                if err:
                    errs.append(f"{before}: {err}")
                elif d.filename != before:
                    done += 1
            msg = (errs[0] if errs else
                   f"Converted {done} video(s) to MP4." if done else
                   "Already fine - these play in every player.")
            self.call(lambda: messagebox.showinfo(APP_NAME, msg, parent=self.root))

        threading.Thread(target=work, daemon=True).start()

    def speed_sel(self):
        rec = getattr(self.m, "recorder", None)
        todo = [d for d in self.selected() if d.kind == "record" and d.rec_speeds
                and d.status == COMPLETE]
        if not todo:
            messagebox.showinfo(APP_NAME, "Pick a recording that was made at 1.5× or 2× "
                                "and is still fast.", parent=self.root)
            return

        def work():
            errs = [e for e in (rec.convert_pending(d.id) for d in todo) if e]
            msg = errs[0] if errs else "Done - the recording now plays at normal speed."
            self.call(lambda: messagebox.showinfo(APP_NAME, msg, parent=self.root))

        threading.Thread(target=work, daemon=True).start()

    def repair_sel(self):
        paths = []
        for d in self.selected():
            if d.status == COMPLETE:
                paths += [os.path.join(d.folder, p) for p in d.rec_parts] if d.rec_parts else [d.path]
        self.repair_files([p for p in paths if p.lower().endswith(".webm")])

    def repair_files(self, paths):
        if not paths:
            messagebox.showinfo(APP_NAME, "Pick a finished .webm video.", parent=self.root)
            return

        def work():
            fixed = sum(1 for p in paths if os.path.exists(p) and recorder.repair(p))
            msg = (f"Fixed {fixed} video(s). Skipping around should be smooth now."
                   if fixed else "Already fine - nothing needed fixing.")
            self.call(lambda: messagebox.showinfo(APP_NAME, msg, parent=self.root))

        threading.Thread(target=work, daemon=True).start()

    def rename_sel(self):
        sel = self.selected()
        if len(sel) == 1:
            rename_dialog(self, sel[0])

    def copy_sel(self):
        sel = self.selected()
        if sel:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(d.url for d in sel))

    def redownload_sel(self):
        for d in self.selected():
            if d.kind == "record":
                continue
            if d.kind == "media":
                self.m.add_media(d.url, d.title, d.fmt_args, d.fmt_label, d.size,
                                 d.fallback_url, d.headers, d.folder,
                                 audio=d.category == "Music",
                                 url_headers=d.url_headers)
            else:
                self.m.add(d.url, d.filename, d.folder, d.headers)

    def refresh_sel(self):
        sel = self.selected()
        if len(sel) != 1:
            return
        d = sel[0]
        if d._job:
            self.m.pause(d.id)
        win = tk.Toplevel(self.root)
        win.title("Refresh download link")
        win.transient(self.root)
        center(win, self.root, 560, 150)
        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"New link for: {d.filename}\n"
                          "(Downloaded data is kept. The new link must be for the same file.)"
                  ).pack(anchor="w")
        v = tk.StringVar(value="")
        try:
            clip = self.root.clipboard_get().strip()
            if clip.startswith("http"):
                v.set(clip)
        except tk.TclError:
            pass
        ttk.Entry(f, textvariable=v).pack(fill="x", pady=8)

        def ok():
            url = v.get().strip()
            if url.startswith(("http://", "https://")):
                deadline = time.time() + 5
                while d._job and time.time() < deadline:
                    win.update()
                    time.sleep(0.05)
                if self.m.refresh_link(d.id, url):
                    self.m.start(d.id)
                win.destroy()

        ttk.Button(f, text="Use this link and resume", command=ok).pack(anchor="e")

    def delete_sel(self, with_file=False):
        sel = self.selected()
        if not sel:
            return
        what = f"{len(sel)} download(s)"
        msg = (f"Delete {what} AND the downloaded files from your disk?"
               if with_file else
               f"Remove {what} from the list?\n(Finished files stay on disk; "
               "unfinished data is deleted.)")
        if not messagebox.askyesno(APP_NAME, msg, parent=self.root):
            return
        threading.Thread(target=self.m.remove_many, args=([d.id for d in sel], with_file),
                         daemon=True).start()

    # --- browser bridge --------------------------------------------------
    def handle_browser(self, items):
        """Runs on the UI thread."""
        media = [i for i in items if i.get("kind") == "media"]
        items = [i for i in items if i.get("kind") != "media"]
        for it in media[:1]:
            headers = {}
            if it.get("referrer"):
                headers["Referer"] = it["referrer"]
            if it.get("cookies"):
                headers["Cookie"] = it["cookies"]
            if it.get("userAgent"):
                headers["User-Agent"] = it["userAgent"]
            self.show()
            uh = it.get("urlHeaders") if isinstance(it.get("urlHeaders"), dict) else {}
            self.open_media(it["url"], it.get("fallbackUrl", ""), it.get("title", ""),
                            headers, uh)
        for it in items:
            headers = {}
            if it.get("referrer"):
                headers["Referer"] = it["referrer"]
            if it.get("cookies"):
                headers["Cookie"] = it["cookies"]
            if it.get("userAgent"):
                headers["User-Agent"] = it["userAgent"]
            if isinstance(it.get("extraHeaders"), dict):
                headers.update({k: v for k, v in it["extraHeaders"].items()
                                if isinstance(v, str)})
            name = it.get("filename") or ""
            if name:
                name = os.path.basename(name.replace("\\", "/"))
            if self.m.settings.get("ask_on_browser_capture", True) and len(items) == 1:
                self.show()
                self.add_url(it["url"], name, headers, from_browser=True)
            else:
                self.m.add(it["url"], name or None, None, headers, save=len(items) == 1)
        if len(items) > 1:
            self.m.save()
            self.show()

    def open_media(self, url, fallback="", title="", headers=None, url_headers=None):
        need = []
        if not self.m.tools.have("yt-dlp"):
            need.append("yt-dlp")
        host = urllib.parse.urlparse(url).hostname or ""
        if ("youtube.com" in host or "youtu.be" in host) and not self.m.tools.have("deno"):
            need.append("Deno")
        if need:
            self.pending_media = (url, fallback, title, headers, url_headers)
            VideoToolsDialog(self, "To download this video Kidlat needs: "
                             + " and ".join(need) + ". Download them below - the video "
                             "window opens by itself when they're ready.")
            return
        MediaDialog(self, url, fallback, title, headers, url_headers)

    def _tool_done(self, name, ok, msg):
        if not ok:
            messagebox.showerror(APP_NAME, msg, parent=self.root)
            return
        if self.pending_media and self.m.tools.have("yt-dlp"):
            host = urllib.parse.urlparse(self.pending_media[0]).hostname or ""
            yt = "youtube.com" in host or "youtu.be" in host
            if not yt or self.m.tools.have("deno"):
                args, self.pending_media = self.pending_media, None
                self.show()
                MediaDialog(self, *args)

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(800, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def _completed(self, d):
        if d.tag.startswith("tool:"):
            return
        if self.m.settings.get("show_complete_popup", True):
            w = self.progress_windows.get(d.id)
            if w is not None and w.winfo_exists():
                return   # the progress window already shows Open buttons
            # one popup at a time: many files finishing together show as one count
            old = getattr(self, "_popup", None)
            count = 1
            if old is not None and old.winfo_exists():
                count = getattr(old, "count", 1) + 1 \
                    if time.time() - getattr(old, "born", 0) < 10 else 1
                old.destroy()
            self._popup = DonePopup(self, d, count)
            self._popup.count = count
            self._popup.born = time.time()

    # --- refresh loop ----------------------------------------------------
    def _poll(self):
        while True:
            try:
                fn = self.inbox.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except tk.TclError:
                if not self._alive():
                    return              # window closed
            except Exception as e:      # noqa: BLE001  never stop the loop
                print("Kidlat UI error:", e, file=sys.stderr)
        try:
            self.refresh()
            self.root.after(500, self._poll)
        except tk.TclError:
            pass

    def _alive(self):
        try:
            return bool(self.root.winfo_exists())
        except tk.TclError:
            return False

    def refresh(self):
        with self.m.lock:
            items = list(self.m.downloads)
        existing = set(self.tree.get_children())
        wanted = []
        total_speed = 0
        active = 0
        for d in reversed(items):              # newest on top
            total_speed += d.speed
            active += 1 if d._job else 0
            if not self._visible(d):
                continue
            wanted.append(d.id)
            tag = ("done",) if d.status == COMPLETE else \
                  ("err",) if d.status == ERROR else \
                  ("run",) if d._job else ()
            values = (
                d.filename or engine.guess_filename(d.url),
                human_size(d.size),
                f"{d.progress:.1f}%" if d.size > 0 else ("100%" if d.status == COMPLETE else ""),
                status_text(d),
                f"{human_size(d.speed)}/s" if d.speed else "",
                human_time(d.eta),
                time.strftime("%b %d, %I:%M %p", time.localtime(d.added)),
            )
            if d.id in existing:
                if self.tree.item(d.id, "values") != values:
                    self.tree.item(d.id, values=values, tags=tag)
            else:
                self.tree.insert("", "end", iid=d.id, values=values, tags=tag)
        for iid in existing - set(wanted):
            self.tree.delete(iid)
        for idx, iid in enumerate(wanted):
            if self.tree.index(iid) != idx:
                self.tree.move(iid, "", idx)
        self.status_l.config(
            text=f"{len(items)} downloads   •   {active} active   •   "
                 f"{human_size(total_speed)}/s"
                 + (f"   •   limit {self.m.settings['speed_limit_kbps']} KB/s"
                    if self.m.settings.get("speed_limit_kbps") else ""))

    def quit(self):
        running = [d for d in self.m.downloads
                   if d._job or (d.kind == "record" and d.status == DOWNLOADING)]
        if running and not messagebox.askyesno(
                APP_NAME, f"{len(running)} download(s) are running.\n"
                "Quit anyway? (They will be paused and can be resumed later.)",
                parent=self.root):
            return
        self.root.withdraw()
        self.m.shutdown()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
