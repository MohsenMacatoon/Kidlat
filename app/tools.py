"""
Kidlat - video tools (yt-dlp, Deno, FFmpeg).

yt-dlp  : understands YouTube, Facebook, TikTok, X, Vimeo and 1000+ sites,
          plus streamed video (HLS/DASH) on random websites.
Deno    : JavaScript runtime that yt-dlp needs for YouTube.
FFmpeg  : joins separate video + audio (HD quality) and makes MP3s.

The tools are downloaded by Kidlat itself (resumable) into
%APPDATA%\\Kidlat\\tools and used from there.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile

IS_WIN = sys.platform == "win32"
EXE = ".exe" if IS_WIN else ""
NO_WINDOW = 0x08000000 if IS_WIN else 0     # CREATE_NO_WINDOW

TOOLS = {
    "yt-dlp": {
        "title": "yt-dlp",
        "why": "Needed for YouTube, Facebook, TikTok, X and 1000+ video sites",
        "url": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
        "file": "yt-dlp.exe",
        "approx": 18_000_000,
    },
    "deno": {
        "title": "Deno",
        "why": "Needed for YouTube only",
        "url": "https://github.com/denoland/deno/releases/latest/download/"
               "deno-x86_64-pc-windows-msvc.zip",
        "file": "deno.zip",
        "approx": 43_000_000,
    },
    "ffmpeg": {
        "title": "FFmpeg",
        "why": "HD quality (YouTube above 360p) and MP3 audio",
        "url": "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
               "ffmpeg-master-latest-win64-gpl-shared.zip",
        "file": "ffmpeg.zip",
        "approx": 91_000_000,
    },
}


class Tools:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "tools")
        self.dl_dir = os.path.join(self.dir, "_downloads")
        os.makedirs(self.dl_dir, exist_ok=True)
        self.listeners = []          # called with (tool_name, ok, message)
        self.running = set()         # long FFmpeg jobs, stopped when Kidlat closes

    # --- finding ---------------------------------------------------------
    def path(self, name):
        local = os.path.join(self.dir, name + EXE)
        if os.path.isfile(local):
            return local
        return shutil.which(name)

    def have(self, name):
        return self.path(name) is not None

    def status(self):
        return {n: self.have(n) for n in TOOLS}

    def env(self):
        env = dict(os.environ)
        env["PATH"] = self.dir + os.pathsep + env.get("PATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"      # live progress lines from yt-dlp
        return env

    # --- installing (called when Kidlat finishes downloading a tool) -------
    def install(self, name, downloaded_path):
        def work():
            try:
                self._install(name, downloaded_path)
                ok, msg = True, f"{TOOLS[name]['title']} is ready."
            except Exception as e:  # noqa: BLE001
                ok, msg = False, f"Could not install {TOOLS[name]['title']}: {e}"
            for cb in list(self.listeners):
                try:
                    cb(name, ok, msg)
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=work, daemon=True).start()

    def _install(self, name, src):
        if name == "yt-dlp":
            os.replace(src, os.path.join(self.dir, "yt-dlp" + EXE))
            return
        with zipfile.ZipFile(src) as z:
            for info in z.infolist():
                base = os.path.basename(info.filename)
                if not base or info.is_dir():
                    continue
                if name == "deno" and base.lower() != "deno.exe":
                    continue
                if name == "ffmpeg" and not (
                        "/bin/" in info.filename.replace("\\", "/")
                        and base.lower().endswith((".exe", ".dll"))):
                    continue
                target = os.path.join(self.dir, base)
                with z.open(info) as s, open(target + ".tmp", "wb") as d:
                    shutil.copyfileobj(s, d, 1024 * 1024)
                os.replace(target + ".tmp", target)
        os.remove(src)
        if not self.have(name):
            raise RuntimeError("the downloaded file did not contain it")

    # --- running yt-dlp --------------------------------------------------
    def ytdlp_cmd(self, *args, headers=None):
        exe = self.path("yt-dlp")
        if not exe:
            raise RuntimeError("yt-dlp is not installed. Click 'Video Tools' to get it.")
        cmd = [exe, "--encoding", "utf-8", "--no-playlist", "--no-warnings"]
        if self.have("ffmpeg"):
            ff = self.path("ffmpeg")
            cmd += ["--ffmpeg-location", os.path.dirname(ff)]
        deno = self.path("deno")
        if deno:
            cmd += ["--js-runtimes", f"deno:{deno}"]
        for name, value in (headers or {}).items():
            if not value or any(c in str(value) for c in "\r\n"):
                continue
            low = name.lower()
            if low == "user-agent":
                cmd += ["--user-agent", value]
            elif low == "referer":
                cmd += ["--referer", value]
            elif low in ("range", "host", "content-length", "accept-encoding"):
                continue
            else:                      # Cookie, Origin, Authorization, X-...
                cmd += ["--add-header", f"{name}:{value}"]
        return cmd + list(args)

    def popen(self, cmd):
        return subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=self.env(), creationflags=NO_WINDOW,
            bufsize=1, text=True, encoding="utf-8", errors="replace")

    def info(self, url, headers=None, timeout=90, extra=()):
        """Ask yt-dlp what video is at this address (title, qualities)."""
        cmd = self.ytdlp_cmd(*extra, "-J", url, headers=headers)
        r = subprocess.run(cmd, capture_output=True, env=self.env(),
                           creationflags=NO_WINDOW, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        out = r.stdout.decode("utf-8", "replace").strip()
        if r.returncode != 0 or not out.startswith("{"):
            err = r.stderr.decode("utf-8", "replace").strip().splitlines()
            msg = next((l for l in reversed(err) if "ERROR" in l), err[-1] if err else "")
            raise RuntimeError(clean_error(msg) or "yt-dlp could not read this page")
        data = json.loads(out)
        if data.get("_type") == "playlist" and data.get("entries"):
            data = next((e for e in data["entries"] if e), data)
        return data

    def update_ytdlp(self):
        exe = self.path("yt-dlp")
        if not exe:
            return False, "yt-dlp is not installed."
        r = subprocess.run([exe, "-U"], capture_output=True, env=self.env(),
                           creationflags=NO_WINDOW, timeout=300,
                           stdin=subprocess.DEVNULL)
        out = (r.stdout + r.stderr).decode("utf-8", "replace").strip()
        return r.returncode == 0, out.splitlines()[-1] if out else "Done"

    @staticmethod
    def kill(proc):
        if not proc or proc.poll() is not None:
            return
        try:
            if IS_WIN:   # yt-dlp.exe starts a child process; kill the whole tree
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=NO_WINDOW)
            else:
                proc.terminate()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


def clean_error(msg):
    msg = (msg or "").strip()
    if msg.startswith("ERROR:"):
        msg = msg[6:].strip()
    if "Sign in to confirm" in msg:
        msg += " (YouTube is asking for sign-in; try again later or update yt-dlp)"
    if "Unsupported URL" in msg:
        msg = "No video found on this page that Kidlat can download."
    if "DRM" in msg:
        msg = "This video is DRM-protected and can't be downloaded."
    return msg[:300]


# --------------------------------------------------------------------------- #
# quality choices
# --------------------------------------------------------------------------- #
def _size(f):
    return f.get("filesize") or f.get("filesize_approx") or 0


def _has_v(f):
    return f.get("vcodec") not in (None, "none")


def _has_a(f):
    return f.get("acodec") not in (None, "none")


def choices(info, has_ffmpeg):
    """
    Build the list shown in the quality picker.
    Each choice: dict(label, args, size, audio, height)
    """
    fmts = [f for f in info.get("formats") or [] if f.get("format_id")]
    out = []
    audio = [f for f in fmts if _has_a(f) and not _has_v(f)]
    best_audio = max(audio, key=lambda f: (f.get("ext") == "m4a", f.get("abr") or 0),
                     default=None)

    if has_ffmpeg:
        heights = sorted({f["height"] for f in fmts
                          if _has_v(f) and isinstance(f.get("height"), int)},
                         reverse=True)
        for h in heights:
            vids = [f for f in fmts if _has_v(f) and f.get("height") == h]
            best_v = max(vids, key=lambda f: (str(f.get("vcodec", "")).startswith("avc1"),
                                              f.get("ext") == "mp4", f.get("tbr") or 0))
            size = _size(best_v)
            if not _has_a(best_v) and best_audio:
                size += _size(best_audio)
            out.append({
                "label": f"{h}p video",
                # H.264 + AAC first: plays in every player without converting
                "args": ["-f", f"bv*[height<={h}][vcodec^=avc1]+ba[acodec^=mp4a]/"
                               f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/"
                               f"bv*[height<={h}]+ba/b[height<={h}]/b",
                         "--merge-output-format", "mp4"],
                "size": size, "audio": False, "height": h})
    else:
        # show the HD sizes too, greyed out, so people know what FFmpeg unlocks
        both_h = {f.get("height") for f in fmts if _has_v(f) and _has_a(f)}
        max_both = max([h for h in both_h if isinstance(h, int)], default=0)
        locked = sorted({f["height"] for f in fmts if _has_v(f) and not _has_a(f)
                         and isinstance(f.get("height"), int)
                         and f["height"] > max_both}, reverse=True)
        for h in locked:
            vids = [f for f in fmts if _has_v(f) and f.get("height") == h]
            best_v = max(vids, key=lambda f: (f.get("ext") == "mp4", f.get("tbr") or 0))
            size = _size(best_v) + (_size(best_audio) if best_audio else 0)
            out.append({"label": f"{h}p video", "args": [], "size": size,
                        "audio": False, "height": h, "needs_ffmpeg": True})
        both = [f for f in fmts if _has_v(f) and _has_a(f)]
        seen = set()
        for f in sorted(both, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
                        reverse=True):
            h = f.get("height")
            key = (h, f.get("ext"))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "label": f"{h}p video" if h else (f.get("format_note") or "Video"),
                "args": ["-f", f["format_id"]],
                "size": _size(f), "audio": False, "height": h or 0})

    if not out:      # single-file sites
        out.append({"label": "Best quality", "args": ["-f", "b/bv*+ba"],
                    "size": _size(info), "audio": False, "height": 0})

    if best_audio:
        out.append({"label": "Audio only (M4A)" if best_audio.get("ext") == "m4a"
                    else f"Audio only ({best_audio.get('ext', '').upper()})",
                    "args": ["-f", "ba[ext=m4a]/ba"],
                    "size": _size(best_audio), "audio": True, "height": 0})
    if has_ffmpeg:   # MP3 works even when the site has no separate audio track
        out.append({"label": "Audio only (MP3)",
                    "args": ["-f", "ba/b", "-x", "--audio-format", "mp3"],
                    "size": _size(best_audio) if best_audio else 0,
                    "audio": True, "height": 0})
    return out


def default_choice(chs):
    """Pick 720p if offered (good quality, saves data), else the best."""
    vids = [c for c in chs if not c["audio"] and not c.get("needs_ffmpeg")]
    for c in vids:
        if c["height"] and c["height"] <= 720:
            return chs.index(c)
    return chs.index(vids[0]) if vids else 0
