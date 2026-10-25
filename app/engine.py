"""
Kidlat Download Manager - download engine.

Pure Python standard library (no pip installs needed).

Features
- Splits a file into parts and downloads them over several connections at once
- Dynamic re-splitting: when a connection finishes its part, it takes over
  half of the biggest remaining part (keeps every connection busy)
- Pause / resume, even after closing the app or restarting the PC
- Automatic retry when the internet drops (keeps what was already downloaded)
- Global speed limiter, download queue, auto-sorting into category folders
"""

import http.client
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import tools as _tools

CHUNK = 64 * 1024
MIN_SPLIT = 512 * 1024          # don't split parts smaller than this
MIN_SEGMENT = 1024 * 1024       # initial part size floor
PART_EXT = ".kidlat"
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) "
              "Gecko/20100101 Firefox/131.0")

CATEGORIES = {
    "Video": ["mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg",
              "mpeg", "3gp", "ts", "m2ts", "vob", "ogv"],
    "Music": ["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "wma", "aiff",
              "mid", "midi"],
    "Compressed": ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz", "iso",
                   "cab", "arj", "lz", "zst"],
    "Documents": ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt",
                  "rtf", "odt", "ods", "odp", "epub", "csv", "md", "mobi"],
    "Programs": ["exe", "msi", "apk", "bat", "msix", "appx", "jar", "deb",
                 "rpm", "dmg"],
    "Images": ["jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tif",
               "tiff", "ico", "heic", "psd"],
}

DEFAULT_SETTINGS = {
    "download_dir": os.path.join(os.path.expanduser("~"), "Downloads", "Kidlat"),
    "use_categories": True,
    "connections": 8,
    "max_active": 3,
    "speed_limit_kbps": 0,      # 0 = unlimited
    "retries": 50,              # per part, before giving up
    "timeout": 30,
    "port": 9614,
    "ask_on_browser_capture": True,
}

QUEUED, CONNECTING, DOWNLOADING, PAUSED, COMPLETE, ERROR = (
    "Queued", "Connecting", "Downloading", "Paused", "Complete", "Error")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def category_for(filename):
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return "Other"


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name):
    name = _BAD.sub("_", name or "").strip().strip(".")
    if not name:
        name = "download"
    stem, ext = os.path.splitext(name)
    if stem.upper() in _RESERVED:
        stem = "_" + stem
    if len(stem) > 180:
        stem = stem[:180]
    return stem + ext[:20]


def human_size(n):
    if n is None or n < 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024


def human_time(sec):
    if sec is None or sec < 0 or sec == float("inf"):
        return ""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _filename_from_cd(cd):
    if not cd:
        return None
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd, re.I)
    if m:
        enc = m.group(1) or "utf-8"
        try:
            return urllib.parse.unquote(m.group(2).strip().strip('"'), encoding=enc)
        except LookupError:
            return urllib.parse.unquote(m.group(2).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or \
        re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    if m:
        raw = m.group(1).strip()
        try:  # servers often send UTF-8 bytes decoded as latin-1
            raw = raw.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return raw
    return None


def guess_filename(url, headers=None, content_type=None):
    name = _filename_from_cd((headers or {}).get("Content-Disposition"))
    if not name:
        path = urllib.parse.urlparse(url).path
        name = urllib.parse.unquote(os.path.basename(path.rstrip("/")))
    name = safe_filename(name)
    if "." not in name and content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            name += ext
    return name


class RateLimiter:
    """Global token bucket shared by every connection."""

    def __init__(self, bytes_per_sec=0):
        self.rate = bytes_per_sec
        self.allowance = bytes_per_sec
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def set_rate(self, bytes_per_sec):
        with self.lock:
            self.rate = bytes_per_sec
            self.allowance = bytes_per_sec

    def consume(self, n):
        with self.lock:
            rate = self.rate
            if rate <= 0:
                return
            now = time.monotonic()
            self.allowance = min(rate, self.allowance + (now - self.last) * rate)
            self.last = now
            self.allowance -= n
            deficit = -self.allowance if self.allowance < 0 else 0
        if deficit:
            time.sleep(deficit / rate)


class StopRequested(Exception):
    pass


class HTTPFatal(Exception):
    """Errors that retrying won't fix (404, link expired, ...)."""


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
class Segment:
    __slots__ = ("start", "end", "pos", "busy")

    def __init__(self, start, end, pos=None):
        self.start = start
        self.end = end            # inclusive, -1 = unknown length
        self.pos = start if pos is None else pos
        self.busy = False

    @property
    def done(self):
        return self.end >= 0 and self.pos > self.end

    @property
    def remaining(self):
        return self.end - self.pos + 1 if self.end >= 0 else 0

    def to_dict(self):
        return {"start": self.start, "end": self.end, "pos": self.pos}

    @classmethod
    def from_dict(cls, d):
        return cls(d["start"], d["end"], d["pos"])


class Download:
    def __init__(self, url, filename=None, folder=None, headers=None,
                 connections=8, did=None):
        self.id = did or uuid.uuid4().hex[:12]
        self.url = url
        self.final_url = url
        self.filename = filename
        self.folder = folder
        self.headers = dict(headers or {})
        self.connections = connections
        self.size = -1
        self.resumable = False
        self.probed = False
        self.status = QUEUED
        self.error = ""
        self.segments = []
        self.added = time.time()
        self.finished = None
        self.speed = 0.0
        self.active_conns = 0
        self._last_bytes = 0
        self._job = None
        self.lock = threading.RLock()
        # video-site downloads (handled by yt-dlp)
        self.kind = "file"            # "file" or "media"
        self.fallback_url = ""
        self.fmt_args = []
        self.fmt_label = ""
        self.title = ""
        self.m_done = 0
        self.m_files = []             # files yt-dlp writes (for cleanup)
        self.tag = ""                 # "tool:<name>" for video tool downloads
        self.substatus = ""
        self.subs = []                # fast mode: saved progress of each stream
        self._subs = []               # fast mode: live stream downloads
        self.fast = False             # fast mode running (Kidlat's own engine)
        self.max_range = 0            # cap on bytes per request (0 = none)

    # --- derived ----------------------------------------------------------
    @property
    def downloaded(self):
        if self.kind == "media":
            if not self.subs:
                return self.m_done
            total = sum(x["size"] for x in self.subs if x.get("done"))
            live = self._subs
            if live:
                total += sum(s.downloaded for s in live)
            else:                          # paused: count what's saved on disk
                total += sum(g["pos"] - g["start"] for x in self.subs
                             if not x.get("done") for g in x.get("segments", []))
            return total
        with self.lock:
            return sum(s.pos - s.start for s in self.segments)

    @property
    def progress(self):
        if self.status == COMPLETE:
            return 100.0
        if self.size and self.size > 0:
            return min(100.0, self.downloaded * 100.0 / self.size)
        return 0.0

    @property
    def eta(self):
        if self.size > 0 and self.speed > 1:
            return (self.size - self.downloaded) / self.speed
        return None

    @property
    def path(self):
        return os.path.join(self.folder, self.filename)

    @property
    def part_path(self):
        return self.path + PART_EXT

    @property
    def category(self):
        if self.tag.startswith("tool:"):
            return "Programs"
        return category_for(self.filename or "")

    def to_dict(self):
        with self.lock:
            return {
                "id": self.id, "url": self.url, "final_url": self.final_url,
                "filename": self.filename, "folder": self.folder,
                "headers": self.headers, "connections": self.connections,
                "size": self.size, "resumable": self.resumable,
                "probed": self.probed, "status": self.status,
                "error": self.error, "added": self.added,
                "finished": self.finished,
                "segments": [s.to_dict() for s in self.segments],
                "kind": self.kind, "fallback_url": self.fallback_url,
                "fmt_args": self.fmt_args, "fmt_label": self.fmt_label,
                "title": self.title, "m_done": self.m_done,
                "m_files": self.m_files, "tag": self.tag, "subs": self.subs,
            }

    @classmethod
    def from_dict(cls, d):
        dl = cls(d["url"], d.get("filename"), d.get("folder"), d.get("headers"),
                 d.get("connections", 8), d["id"])
        dl.final_url = d.get("final_url", d["url"])
        dl.size = d.get("size", -1)
        dl.resumable = d.get("resumable", False)
        dl.probed = d.get("probed", False)
        dl.status = d.get("status", PAUSED)
        dl.error = d.get("error", "")
        dl.added = d.get("added", time.time())
        dl.finished = d.get("finished")
        dl.segments = [Segment.from_dict(s) for s in d.get("segments", [])]
        dl.kind = d.get("kind", "file")
        dl.fallback_url = d.get("fallback_url", "")
        dl.fmt_args = d.get("fmt_args", [])
        dl.fmt_label = d.get("fmt_label", "")
        dl.title = d.get("title", "")
        dl.m_done = d.get("m_done", 0)
        dl.m_files = d.get("m_files", [])
        dl.tag = d.get("tag", "")
        dl.subs = d.get("subs", [])
        if dl.status in (DOWNLOADING, CONNECTING):
            dl.status = PAUSED
        return dl


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def normalize_url(url):
    """Encode spaces / non-ASCII characters that people paste into links."""
    url = url.strip()
    p = urllib.parse.urlsplit(url)
    try:
        netloc = p.netloc.encode("idna").decode("ascii") if p.netloc else p.netloc
    except UnicodeError:
        netloc = p.netloc
    safe = "/%:@!$&'()*+,;=-._~"
    return urllib.parse.urlunsplit((
        p.scheme, netloc,
        urllib.parse.quote(p.path, safe=safe),
        urllib.parse.quote(p.query, safe=safe + "?"),
        urllib.parse.quote(p.fragment, safe=safe + "?")))


def open_url(url, headers, timeout, start=None, end=None):
    url = normalize_url(url)
    h = {"User-Agent": DEFAULT_UA, "Accept": "*/*",
         "Accept-Encoding": "identity"}
    h.update({k: v for k, v in (headers or {}).items() if v})
    if start is not None:
        h["Range"] = f"bytes={start}-" + ("" if end is None or end < 0 else str(end))
    try:
        req = urllib.request.Request(url, headers=h)
        return urllib.request.urlopen(req, timeout=timeout)
    except (ValueError, http.client.InvalidURL) as e:
        raise HTTPFatal(f"Invalid link: {e}") from e
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403, 404, 410, 451):
            raise HTTPFatal(f"Server said {e.code} {e.reason}. "
                            "The link may have expired - copy a fresh link and "
                            "use 'Refresh link'.") from e
        if e.code == 416:
            raise HTTPFatal("Server rejected the byte range (416).") from e
        raise


def probe(url, headers=None, timeout=30):
    """Ask the server about a file: name, size, and whether it can resume."""
    resp = open_url(url, headers, timeout, start=0, end=0)
    try:
        hdrs = resp.headers
        info = {"final_url": resp.geturl(), "size": -1, "resumable": False,
                "content_type": hdrs.get("Content-Type", "")}
        if resp.status == 206:
            cr = hdrs.get("Content-Range", "")
            m = re.search(r"/(\d+)", cr)
            if m:
                info["size"] = int(m.group(1))
                info["resumable"] = True
        else:
            cl = hdrs.get("Content-Length")
            if cl and cl.isdigit():
                info["size"] = int(cl)
        info["filename"] = guess_filename(info["final_url"], hdrs,
                                          info["content_type"])
        if not _filename_from_cd(hdrs.get("Content-Disposition")):
            # prefer the name in the original link if redirect target is ugly
            orig = guess_filename(url)
            if "." in orig and "." not in info["filename"]:
                info["filename"] = orig
        return info
    finally:
        resp.close()


# --------------------------------------------------------------------------- #
# job: runs one download
# --------------------------------------------------------------------------- #
class Job(threading.Thread):
    def __init__(self, manager, dl):
        super().__init__(daemon=True, name=f"job-{dl.id}")
        self.m = manager
        self.dl = dl
        self.stop = threading.Event()
        self.fatal = None
        self.workers = []

    def request_stop(self):
        self.stop.set()

    # .........................................................................
    def run(self):
        dl = self.dl
        try:
            dl.status = CONNECTING
            dl.error = ""
            self.m.changed()
            if not dl.probed:
                info = self._retry(lambda: probe(dl.url, dl.headers,
                                                 self.m.settings["timeout"]))
                dl.final_url = info["final_url"]
                dl.size = info["size"]
                dl.resumable = info["resumable"]
                if not dl.filename:
                    dl.filename = info["filename"]
                dl.probed = True
                self.m.assign_location(dl)
            os.makedirs(dl.folder, exist_ok=True)

            if dl.resumable and dl.size > 0:
                self._run_segmented()
            else:
                self._run_single()

            if self.fatal:
                raise self.fatal
            if self.stop.is_set():
                raise StopRequested
            self._finish()
        except StopRequested:
            dl.status = PAUSED
        except Exception as e:  # noqa: BLE001
            dl.status = ERROR
            dl.error = str(e) or e.__class__.__name__
        finally:
            dl.speed = 0
            dl.active_conns = 0
            dl._job = None
            self.m.save()
            self.m.changed()
            self.m.schedule()

    # .........................................................................
    def _retry(self, fn):
        retries = self.m.settings["retries"]
        delay = 2
        for attempt in range(retries + 1):
            if self.stop.is_set():
                raise StopRequested
            try:
                return fn()
            except (HTTPFatal, StopRequested):
                raise
            except Exception as e:  # noqa: BLE001  network hiccup
                if attempt == retries:
                    raise
                self.dl.error = f"Retrying ({attempt + 1}/{retries}): {e}"
                self.m.changed()
                if self.stop.wait(delay):
                    raise StopRequested
                delay = min(delay * 2, 30)

    # .........................................................................
    def _run_segmented(self):
        dl = self.dl
        with dl.lock:
            if not dl.segments or dl.segments[0].end < 0:
                n = max(1, min(dl.connections, dl.size // MIN_SEGMENT or 1))
                step = dl.size // n
                dl.segments = []
                for i in range(n):
                    s = i * step
                    e = dl.size - 1 if i == n - 1 else (i + 1) * step - 1
                    dl.segments.append(Segment(s, e))
            for s in dl.segments:
                s.busy = False
        # create / grow the .kidlat file to full size (sparse on NTFS)
        mode = "r+b" if os.path.exists(dl.part_path) else "wb"
        with open(dl.part_path, mode) as f:
            f.seek(0, 2)
            if f.tell() < dl.size:
                f.truncate(dl.size)
        dl.status = DOWNLOADING
        self.m.changed()
        self.workers = [threading.Thread(target=self._worker, daemon=True)
                        for _ in range(dl.connections)]
        for w in self.workers:
            w.start()
        for w in self.workers:
            w.join()

    def _take_segment(self):
        dl = self.dl
        with dl.lock:
            for s in dl.segments:
                if not s.busy and not s.done:
                    s.busy = True
                    return s
            # all parts taken -> split the biggest busy one
            busy = [s for s in dl.segments if s.busy and not s.done]
            if not busy:
                return None
            big = max(busy, key=lambda s: s.remaining)
            if big.remaining < 2 * MIN_SPLIT:
                return None
            mid = big.pos + big.remaining // 2
            new = Segment(mid, big.end)
            big.end = mid - 1
            new.busy = True
            dl.segments.append(new)
            dl.segments.sort(key=lambda s: s.start)
            return new

    def _worker(self):
        dl = self.dl
        with open(dl.part_path, "r+b") as f:
            while not self.stop.is_set() and not self.fatal:
                seg = self._take_segment()
                if seg is None:
                    return
                with dl.lock:
                    dl.active_conns += 1
                try:
                    self._retry(lambda: self._fetch_segment(seg, f))
                except StopRequested:
                    return
                except Exception as e:  # noqa: BLE001
                    self.fatal = e
                    self.stop.set()
                finally:
                    with dl.lock:
                        seg.busy = False
                        dl.active_conns -= 1

    def _fetch_segment(self, seg, f):
        dl = self.dl
        # Some servers (YouTube) slow down very large requests, so a part can
        # be fetched as several smaller requests (dl.max_range bytes each).
        cap = dl.max_range
        while not seg.done:
            if self.stop.is_set():
                raise StopRequested
            with dl.lock:
                req_end = seg.end if not cap else min(seg.end, seg.pos + cap - 1)
            resp = open_url(dl.final_url, dl.headers, self.m.settings["timeout"],
                            start=seg.pos, end=req_end)
            try:
                if resp.status != 206:
                    raise HTTPFatal("Server stopped supporting resume for this file.")
                cr = resp.headers.get("Content-Range", "")
                m = re.search(r"/(\d+)", cr)
                if m and int(m.group(1)) != dl.size:
                    raise HTTPFatal("The file on the server changed size. "
                                    "Delete and download again.")
                dl.error = ""
                while not self.stop.is_set():
                    data = resp.read(CHUNK)
                    if not data:
                        break
                    self.m.limiter.consume(len(data))
                    with dl.lock:
                        room = min(seg.end, req_end) - seg.pos + 1  # end may shrink
                        if room <= 0:
                            break
                        data = data[:room]
                        f.seek(seg.pos)
                        f.write(data)
                        seg.pos += len(data)
                        if seg.pos > min(seg.end, req_end):
                            break
                if self.stop.is_set():
                    raise StopRequested
                with dl.lock:
                    finished_request = seg.pos > min(seg.end, req_end)
                if not finished_request:
                    raise ConnectionError("connection closed early")
            finally:
                resp.close()

    # .........................................................................
    def _run_single(self):
        """Server can't resume: one connection, restart from 0 on resume."""
        dl = self.dl
        dl.connections = 1

        def attempt():
            with dl.lock:
                dl.segments = [Segment(0, -1)]
            seg = dl.segments[0]
            resp = open_url(dl.final_url, dl.headers, self.m.settings["timeout"])
            try:
                dl.status = DOWNLOADING
                dl.active_conns = 1
                dl.error = ""
                self.m.changed()
                with open(dl.part_path, "wb") as f:
                    while True:
                        if self.stop.is_set():
                            raise StopRequested
                        data = resp.read(CHUNK)
                        if not data:
                            break
                        self.m.limiter.consume(len(data))
                        f.write(data)
                        with dl.lock:
                            seg.pos += len(data)
                if dl.size > 0 and seg.pos < dl.size:
                    raise ConnectionError("connection closed early")
                with dl.lock:
                    seg.end = seg.pos - 1
                    if dl.size <= 0:
                        dl.size = seg.pos
            finally:
                resp.close()

        self._retry(attempt)

    # .........................................................................
    def _finish(self):
        dl = self.dl
        if dl.size > 0 and dl.downloaded < dl.size:
            raise RuntimeError("Download incomplete")
        target = self.m.unique_path(dl.folder, dl.filename, exclude=dl)
        os.replace(dl.part_path, target)
        dl.filename = os.path.basename(target)
        dl.status = COMPLETE
        dl.finished = time.time()
        dl.error = ""
        self.m.on_complete(dl)


# --------------------------------------------------------------------------- #
# media job: video sites through yt-dlp
# --------------------------------------------------------------------------- #
_PROGRESS = ("KDL %(progress.downloaded_bytes)s %(progress.total_bytes)s "
             "%(progress.total_bytes_estimate)s %(progress.speed)s")


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


class MediaJob(threading.Thread):
    def __init__(self, manager, dl):
        super().__init__(daemon=True, name=f"media-{dl.id}")
        self.m = manager
        self.dl = dl
        self.stop = threading.Event()
        self.proc = None
        self.sub_job = None

    def request_stop(self):
        self.stop.set()
        self.m.tools.kill(self.proc)
        if self.sub_job:
            self.sub_job.stop.set()

    def run(self):
        dl = self.dl
        try:
            dl.status = CONNECTING
            dl.error = ""
            dl.substatus = "Starting yt-dlp..."
            self.m.changed()
            os.makedirs(dl.folder, exist_ok=True)
            urls = list(dict.fromkeys(u for u in (dl.url, dl.fallback_url) if u))
            if self._run_fast_any(urls):
                urls = []                       # done with Kidlat's own engine
            last_err = ""
            for i, url in enumerate(urls):
                ok, err = self._run_once(url)
                if self.stop.is_set():
                    raise StopRequested
                if ok:
                    break
                last_err = err
                if i == 0 and len(urls) > 1:
                    dl.substatus = "Trying another way..."
                    self.m.changed()
            else:
                if urls:
                    raise RuntimeError(last_err or "yt-dlp failed")
            dl.status = COMPLETE
            dl.finished = time.time()
            dl.error = dl.substatus = ""
            dl.size = dl.m_done = max(dl.m_done, dl.size)
            self.m.on_complete(dl)
        except StopRequested:
            dl.status = PAUSED
        except Exception as e:  # noqa: BLE001
            dl.status = ERROR
            dl.error = _tools.clean_error(str(e)) or e.__class__.__name__
        finally:
            dl.speed = 0
            dl.substatus = "" if dl.status != ERROR else dl.substatus
            dl._job = None
            self.m.save()
            self.m.changed()
            self.m.schedule()

    # ---- fast mode: Kidlat's multi-connection engine -------------------------
    def _fmt(self):
        a = self.dl.fmt_args or []
        if "-x" in a or "-f" not in a:          # MP3 conversion -> yt-dlp does it
            return None
        return a[a.index("-f") + 1]

    def _run_fast_any(self, urls):
        """Try fast mode; return True if the video was fully downloaded."""
        fmt = self._fmt()
        if not fmt:
            return False
        for url in urls:
            try:
                if self._run_fast(url, fmt):
                    return True
            except StopRequested:
                raise
            except Exception as e:  # noqa: BLE001  fall back to yt-dlp
                if self.stop.is_set():
                    raise StopRequested
                print("Kidlat fast mode skipped:", e, file=sys.stderr)
            finally:
                self.dl.fast = False
                self.dl._subs = []
        self._cleanup_fast()
        return False

    def _cleanup_fast(self):
        dl = self.dl
        for x in dl.subs:
            p = os.path.join(dl.folder, x["file"]) + PART_EXT
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        dl.subs = []

    def _plan(self, url, fmt):
        """Ask yt-dlp for the real file addresses of the chosen quality."""
        dl = self.dl
        dl.substatus = "Finding the video files..."
        self.m.changed()
        info = self.m.tools.info(url, dl.headers, extra=("-f", fmt))
        reqs = info.get("requested_formats") or [info]
        streams = []
        for r in reqs:
            if r.get("protocol") not in ("http", "https") or not r.get("url"):
                return None, None                 # streamed in pieces -> yt-dlp
            h = dict(r.get("http_headers") or {})
            if dl.headers.get("Cookie") and "Cookie" not in h:
                h["Cookie"] = dl.headers["Cookie"]
            h.pop("Accept-Encoding", None)
            streams.append({"id": str(r.get("format_id")), "url": r["url"],
                            "headers": h, "ext": r.get("ext") or "mp4",
                            "size": r.get("filesize") or 0})
        if len(streams) > 1 and not self.m.tools.have("ffmpeg"):
            return None, None
        return info, streams

    def _run_fast(self, url, fmt):
        dl = self.dl
        info, streams = self._plan(url, fmt)
        if self.stop.is_set():
            raise StopRequested
        if not streams:
            return False
        title = info.get("title") or dl.title or "video"
        stem = safe_filename(title)
        if len(streams) > 1:
            a = dl.fmt_args
            ext = a[a.index("--merge-output-format") + 1] \
                if "--merge-output-format" in a else "mkv"
        else:
            ext = streams[0]["ext"]
        # keep progress from an earlier (paused) run when it's the same files
        old = {x["id"]: x for x in dl.subs}
        dl.subs = []
        for st in streams:
            prev = old.get(st["id"])
            entry = {"id": st["id"], "file": f"{stem}.f{st['id']}.{st['ext']}",
                     "size": prev["size"] if prev else 0,
                     "segments": prev["segments"] if prev else [],
                     "done": prev.get("done", False) if prev else False}
            dl.subs.append(entry)
        dl.fast = True
        conns = max(1, int(self.m.settings.get("connections") or 8))
        for st, entry in zip(streams, dl.subs):
            if entry["done"] and os.path.exists(os.path.join(dl.folder, entry["file"])):
                continue
            sub = Download(st["url"], entry["file"], dl.folder, st["headers"], conns)
            sub.final_url = st["url"]
            sub.max_range = 10 * 1024 * 1024
            info2 = probe(st["url"], st["headers"], self.m.settings["timeout"])
            sub.size = info2["size"]
            sub.resumable = info2["resumable"]
            if entry["size"] and entry["size"] == sub.size and entry["segments"]:
                sub.segments = [Segment.from_dict(x) for x in entry["segments"]]
            entry["size"] = max(sub.size, 0)
            dl.size = sum(x["size"] for x in dl.subs) or -1
            dl._subs = [sub]
            dl.status = DOWNLOADING
            dl.substatus = ("Downloading video" if len(streams) > 1 and st is streams[0]
                            else "Downloading audio" if len(streams) > 1 else "")
            job = Job(self.m, sub)
            self.sub_job = job
            if self.stop.is_set():
                job.stop.set()
            saver = threading.Thread(target=self._save_sub, args=(sub, entry, job),
                                     daemon=True)
            saver.start()
            try:
                if sub.resumable and sub.size > 0:
                    job._run_segmented()
                else:
                    job._run_single()
            finally:
                job.stop.set()
                saver.join()
                entry["segments"] = [x.to_dict() for x in sub.segments]
                self.sub_job = None
            if job.fatal:
                raise job.fatal
            if self.stop.is_set():
                raise StopRequested
            if sub.size > 0 and sub.downloaded < sub.size:
                raise RuntimeError("stream incomplete")
            os.replace(sub.part_path, sub.path)
            entry["done"] = True
            entry["size"] = sub.size
            dl._subs = []
            self.m.save()

        # join video + audio, or just rename the single file
        final = self.m.unique_path(dl.folder, f"{stem}.{ext}", exclude=dl)
        parts = [os.path.join(dl.folder, x["file"]) for x in dl.subs]
        if len(parts) > 1:
            dl.substatus = "Joining video and audio..."
            dl.speed = 0
            self.m.changed()
            self._merge(parts, final)
            for p in parts:
                try:
                    os.remove(p)
                except OSError:
                    pass
        else:
            os.replace(parts[0], final)
        dl.subs = []
        dl.filename = os.path.basename(final)
        dl.size = dl.m_done = os.path.getsize(final)
        return True

    def _save_sub(self, sub, entry, job):
        """Keep the saved progress fresh so a crash/restart can resume."""
        while not job.stop.wait(2):
            with sub.lock:
                entry["segments"] = [x.to_dict() for x in sub.segments]

    def _merge(self, parts, final):
        ff = self.m.tools.path("ffmpeg")
        tmp_out = os.path.splitext(final)[0] + ".joining" + os.path.splitext(final)[1]
        cmd = [ff, "-y", "-loglevel", "error", "-i", parts[0], "-i", parts[1],
               "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", tmp_out]
        r = subprocess.run(cmd, capture_output=True, env=self.m.tools.env(),
                           creationflags=_tools.NO_WINDOW, stdin=subprocess.DEVNULL)
        if r.returncode != 0:     # format can't hold these streams -> MKV
            tmp_out = os.path.splitext(tmp_out)[0] + ".mkv"
            final = os.path.splitext(final)[0] + ".mkv"
            cmd[-1] = tmp_out
            r = subprocess.run(cmd, capture_output=True, env=self.m.tools.env(),
                               creationflags=_tools.NO_WINDOW, stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                raise RuntimeError("FFmpeg could not join video and audio: "
                                   + r.stderr.decode("utf-8", "replace")[-200:])
        os.replace(tmp_out, final)

    def _run_once(self, url):
        dl = self.dl
        limit = int(self.m.settings.get("speed_limit_kbps") or 0)
        template = os.path.join(dl.folder, "%(title).150B.%(ext)s")
        args = list(dl.fmt_args or ["-f", "b/bv*+ba"]) + [
            "-o", template, "--newline", "--progress",
            "--progress-template", _PROGRESS, "--progress-delta", "0.5",
            "--print", "after_move:KDLPATH %(filepath)s",
            "-N", "8", "--no-mtime", "--retries", "30",
            "--fragment-retries", "30", "--no-overwrites",
        ]
        if limit:
            args += ["--limit-rate", f"{limit}K"]
        cmd = self.m.tools.ytdlp_cmd(*args, url, headers=dl.headers)
        self.proc = self.m.tools.popen(cmd)
        if self.stop.is_set():
            self.m.tools.kill(self.proc)
        done_before = 0         # bytes of earlier streams (video before audio)
        part_total = 0
        part_done = 0
        final = None
        errors = []
        for line in self.proc.stdout:
            line = line.strip()
            if line.startswith("KDL "):
                p = line.split()
                part_done = _num(p[1])
                part_total = _num(p[2]) or _num(p[3]) or part_total
                dl.speed = _num(p[4])
                dl.m_done = int(done_before + part_done)
                dl.size = int(done_before + part_total) if part_total else -1
                if dl.status != DOWNLOADING:
                    dl.status = DOWNLOADING
                dl.substatus = ""
                dl.error = ""
            elif line.startswith("KDLPATH "):
                final = line[8:].strip()
            elif "Destination:" in line and line.startswith(
                    ("[download]", "[hlsnative]", "[dashsegments]")):
                if part_total:
                    done_before += part_total
                    part_total = part_done = 0
                path = line.split("Destination:", 1)[1].strip()
                with dl.lock:
                    if path not in dl.m_files:
                        dl.m_files.append(path)
                if not dl.filename or dl.filename == "video":
                    dl.filename = os.path.basename(path)
            elif line.startswith(("[Merger]", "[ExtractAudio]", "[FixupM3u8]",
                                  "[VideoConvertor]", "[FixupM4a]")):
                dl.substatus = "Joining video and audio..." \
                    if line.startswith("[Merger]") else "Finishing..."
                dl.speed = 0
            elif "has already been downloaded" in line:
                p = line.split("[download]", 1)[-1].split(" has already")[0].strip()
                final = final or p
            elif line.startswith("ERROR:"):
                errors.append(line)
            elif "Retrying" in line or "Got error" in line:
                dl.substatus = "Connection problem - retrying..."
            self.m.changed()
        code = self.proc.wait()
        self.proc = None
        if code == 0 and final and os.path.exists(final):
            dl.filename = os.path.basename(final)
            dl.folder = os.path.dirname(final)
            try:
                size = os.path.getsize(final)
                dl.size = dl.m_done = size
            except OSError:
                pass
            return True, ""
        if code == 0 and final is None:
            return True, ""
        return False, errors[-1] if errors else f"yt-dlp stopped (code {code})"


# --------------------------------------------------------------------------- #
# manager
# --------------------------------------------------------------------------- #
class Manager:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.state_file = os.path.join(data_dir, "downloads.json")
        self.settings_file = os.path.join(data_dir, "settings.json")
        self.settings = dict(DEFAULT_SETTINGS)
        self.downloads = []           # newest last
        self.lock = threading.RLock()
        self.limiter = RateLimiter()
        self.listeners = []
        self.completion_listeners = []
        self.tools = _tools.Tools(data_dir)
        self.completion_listeners.append(self._tool_finished)
        self._dirty = False
        self._load()
        self.apply_settings()
        self._ticker = threading.Thread(target=self._tick, daemon=True)
        self._ticker.start()

    # --- persistence -----------------------------------------------------
    def _load(self):
        try:
            with open(self.settings_file, encoding="utf-8") as f:
                self.settings.update(json.load(f))
        except (OSError, ValueError):
            pass
        try:
            with open(self.state_file, encoding="utf-8") as f:
                self.downloads = [Download.from_dict(d) for d in json.load(f)]
        except (OSError, ValueError):
            self.downloads = []
        for d in self.downloads:
            d._last_bytes = d.downloaded

    def save(self):
        with self.lock:
            data = [d.to_dict() for d in self.downloads]
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.state_file)
        except OSError:
            pass
        self._dirty = False

    def save_settings(self):
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=1)
        self.apply_settings()

    def apply_settings(self):
        self.limiter.set_rate(int(self.settings.get("speed_limit_kbps") or 0) * 1024)
        socket.setdefaulttimeout(self.settings["timeout"])

    # --- events ----------------------------------------------------------
    def changed(self):
        self._dirty = True
        for cb in list(self.listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    def on_complete(self, dl):
        for cb in list(self.completion_listeners):
            try:
                cb(dl)
            except Exception:  # noqa: BLE001
                pass

    def _tick(self):
        """Speed sampling every second + periodic save (crash safety)."""
        last_save = time.monotonic()
        while True:
            time.sleep(1)
            with self.lock:
                items = list(self.downloads)
            for d in items:
                if d.kind == "media" and not d.fast:
                    if d.status != DOWNLOADING:
                        d.speed = 0
                    d._last_bytes = d.downloaded
                    continue
                now = d.downloaded
                inst = max(0, now - d._last_bytes)
                d._last_bytes = now
                if d.status == DOWNLOADING:
                    d.speed = inst if d.speed == 0 else d.speed * 0.6 + inst * 0.4
                else:
                    d.speed = 0
            if self._dirty or any(d.status == DOWNLOADING for d in items):
                if time.monotonic() - last_save > 3:
                    self.save()
                    last_save = time.monotonic()

    # --- paths -----------------------------------------------------------
    def folder_for(self, filename):
        base = self.settings["download_dir"]
        if self.settings.get("use_categories"):
            return os.path.join(base, category_for(filename))
        return base

    def assign_location(self, dl):
        dl.filename = safe_filename(dl.filename or "download")
        if not dl.folder:
            dl.folder = self.folder_for(dl.filename)
        dl.filename = os.path.basename(
            self.unique_path(dl.folder, dl.filename, exclude=dl))

    def unique_path(self, folder, filename, exclude=None):
        stem, ext = os.path.splitext(filename)
        with self.lock:
            taken = {os.path.normcase(d.path) for d in self.downloads
                     if d is not exclude and d.filename and d.folder
                     and d.status != COMPLETE}
        i = 0
        while True:
            name = filename if i == 0 else f"{stem} ({i}){ext}"
            p = os.path.join(folder, name)
            if not os.path.exists(p) and os.path.normcase(p) not in taken:
                return p
            i += 1

    # --- public API ------------------------------------------------------
    def add(self, url, filename=None, folder=None, headers=None, start=True,
            info=None, connections=None):
        dl = Download(url, filename, folder, headers,
                      connections or int(self.settings["connections"]))
        if info:
            dl.final_url = info.get("final_url", url)
            dl.size = info.get("size", -1)
            dl.resumable = info.get("resumable", False)
            dl.probed = True
            if not dl.filename:
                dl.filename = info.get("filename")
        if dl.filename:
            self.assign_location(dl)
        dl.status = QUEUED if start else PAUSED
        with self.lock:
            self.downloads.append(dl)
        self.save()
        self.changed()
        if start:
            self.schedule()
        return dl

    def add_media(self, url, title, fmt_args, fmt_label, est_size=0,
                  fallback_url="", headers=None, folder=None, audio=False,
                  start=True):
        """A video-site download, handled by yt-dlp."""
        dl = Download(url, None, None, headers, 1)
        dl.kind = "media"
        dl.title = title or "video"
        ext = ".m4a" if audio else ".mp4"
        if "mp3" in fmt_args:
            ext = ".mp3"
        dl.filename = safe_filename(dl.title) + ext
        dl.folder = folder or self.folder_for(dl.filename)
        dl.fallback_url = fallback_url or ""
        dl.fmt_args = list(fmt_args)
        dl.fmt_label = fmt_label
        dl.size = int(est_size) if est_size else -1
        dl.probed = True
        dl.resumable = True
        dl.status = QUEUED if start else PAUSED
        with self.lock:
            self.downloads.append(dl)
        self.save()
        self.changed()
        if start:
            self.schedule()
        return dl

    def add_tool(self, name, info=None):
        spec = _tools.TOOLS[name]
        dl = self.add(spec["url"], spec["file"] + ".download",
                      self.tools.dl_dir, start=False, info=info)
        dl.tag = "tool:" + name
        dl.status = QUEUED
        self.save()
        self.schedule()
        return dl

    def _tool_finished(self, dl):
        if dl.tag.startswith("tool:"):
            self.tools.install(dl.tag[5:], dl.path)

    def get(self, did):
        with self.lock:
            for d in self.downloads:
                if d.id == did:
                    return d
        return None

    def start(self, did):
        d = self.get(did)
        if d and d.status in (PAUSED, ERROR, QUEUED):
            d.status = QUEUED
            d.error = ""
            self.changed()
            self.schedule()

    def pause(self, did):
        d = self.get(did)
        if not d:
            return
        if d._job:
            d._job.request_stop()
        elif d.status == QUEUED:
            d.status = PAUSED
            self.changed()

    def remove(self, did, delete_file=False):
        d = self.get(did)
        if not d:
            return
        job = d._job
        if job:
            job.request_stop()
            job.join(timeout=10)
        with self.lock:
            self.downloads.remove(d)
        if d.kind == "media":
            leftovers = []
            for f in d.m_files:
                leftovers += [f + ".part", f + ".ytdl", f + ".part-Frag1"]
                if delete_file or d.status != COMPLETE:
                    leftovers.append(f)
            if delete_file and d.status == COMPLETE and d.filename and d.folder:
                leftovers.append(d.path)
            for p in leftovers:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
        elif d.filename and d.folder:
            for p in (d.part_path, d.path if delete_file else None):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        self.save()
        self.changed()

    def refresh_link(self, did, new_url):
        """Swap an expired link for a fresh one, keeping downloaded data."""
        d = self.get(did)
        if not d or d._job:
            return False
        d.url = d.final_url = new_url
        d.error = ""
        if d.status == ERROR:
            d.status = PAUSED
        self.save()
        self.changed()
        return True

    def start_all(self):
        for d in list(self.downloads):
            if d.status in (PAUSED, ERROR):
                d.status = QUEUED
        self.changed()
        self.schedule()

    def pause_all(self):
        for d in list(self.downloads):
            self.pause(d.id)

    def schedule(self):
        with self.lock:
            active = sum(1 for d in self.downloads if d._job)
            limit = max(1, int(self.settings["max_active"]))
            for d in self.downloads:
                if active >= limit:
                    break
                if d.status == QUEUED and not d._job:
                    d._job = (MediaJob if d.kind == "media" else Job)(self, d)
                    d._job.start()
                    active += 1

    def shutdown(self):
        jobs = [d._job for d in self.downloads if d._job]
        for j in jobs:
            j.request_stop()
        for j in jobs:
            j.join(timeout=5)
        self.save()
