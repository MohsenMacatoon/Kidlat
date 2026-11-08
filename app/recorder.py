"""
Saves recordings made with the extension's ⏺ Record button.

A recording can be stopped and continued later (even after refreshing the page
or restarting the PC). Each continuation is saved as its own part:
    "Title (part 1).webm", "Title (part 2).webm", ...
When the recording is finished, the parts are joined into "Title.webm" with
FFmpeg (if installed); otherwise they are kept as separate, playable files.

Pieces arrive while the video plays and are written straight to disk, so long
recordings don't fill up memory.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import engine
import tools as _tools
import webmfix
from engine import COMPLETE, DOWNLOADING, PAUSED

IDLE_PAUSE = 600           # no data for this long (page closed) -> part ends


def clock(sec):
    sec = int(max(0, sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def repair(path):
    """Add the missing index/length so players can jump around without freezing."""
    try:
        if path.lower().endswith(".webm") and webmfix.needs_fix(path):
            webmfix.fix_webm(path)
            return True
    except Exception:  # noqa: BLE001  never lose a recording over this
        pass
    return False


def keep_awake(on):
    """Stop Windows from sleeping (the screen may still turn off).
    Same idea as video players and IDM use while busy."""
    if sys.platform != "win32":
        return on
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:  # noqa: BLE001
        pass
    return on


def url_key(url):
    return (url or "").split("#")[0]


class Recorder:
    def __init__(self, manager):
        self.m = manager
        self.active = {}       # part id -> {"file", "dl", "path", "next", "last", "lock"}
        self.lock = threading.Lock()
        self.finishing = set()  # recordings being joined/converted right now
        threading.Thread(target=self._watchdog, daemon=True).start()

    # ------------------------------------------------------------------ helpers
    def _session(self, sid):
        d = self.m.get(sid) if sid else None
        if d and d.kind == "record" and d.status != COMPLETE:
            return d
        return None

    def _parts_of(self, dl):
        with self.lock:
            return [pid for pid, r in self.active.items() if r["dl"] is dl]

    def _new_session(self, title, page_url):
        name = engine.safe_filename(title or "Recording") + ".webm"
        folder = self.m.folder_for(name)
        os.makedirs(folder, exist_ok=True)
        final = self.m.unique_path(folder, name)
        dl = engine.Download(page_url or "recording", os.path.basename(final), folder,
                             None, 1)
        dl.kind = "record"
        dl.title = title or "Recording"
        dl.probed = True
        dl.rec_parts = []
        dl.rec_pos = 0.0
        dl.rec_url = url_key(page_url)
        with self.m.lock:
            self.m.downloads.append(dl)
        return dl

    # ------------------------------------------------------------------ API
    def start(self, title, page_url="", session=None, speed=1.0):
        """Start recording (a new recording, or the next part of a paused one)."""
        dl = self._session(session)
        if dl is None:
            dl = self._new_session(title, page_url)
        for pid in self._parts_of(dl):          # old page died without stopping
            self.finish(pid)
        stem = os.path.splitext(dl.filename)[0]
        n = len(dl.rec_parts) + 1
        part_name = f"{stem} (part {n}).webm"
        path = self.m.unique_path(dl.folder, part_name)
        f = open(path, "wb")
        dl.rec_parts.append(os.path.basename(path))
        if abs(speed - 1) > 0.01:
            dl.rec_speeds[os.path.basename(path)] = speed
        dl.status = DOWNLOADING
        dl.substatus = "Recording…" if n == 1 else f"Recording part {n}…"
        dl.error = ""
        pid = uuid.uuid4().hex[:12]
        with self.lock:
            self.active[pid] = {"file": f, "dl": dl, "path": path, "next": 0,
                                "last": time.monotonic(), "lock": threading.Lock()}
        self.m.save()
        self.m.changed()
        return {"id": pid, "session": dl.id, "part": n, "pos": dl.rec_pos,
                "file": os.path.basename(path)}

    def reopen(self, sid, name, have):
        """Kidlat was restarted (or closed the part after a long sleep) while the page
        kept recording: keep writing the same part file, from byte `have` on, so
        nothing is lost. None if that isn't possible (the page then starts a new part)."""
        dl = self._session(sid)
        if not dl or name not in dl.rec_parts or have <= 0:
            return None
        path = os.path.join(dl.folder, name)
        with self.lock:
            open_pids = [pid for pid, x in self.active.items() if x["path"] == path]
        for pid in open_pids:                    # still open here: close, then reopen
            with self.lock:
                x = self.active.pop(pid, None)
            if x:
                with x["lock"]:
                    try:
                        x["file"].close()
                    except OSError:
                        pass
        try:
            size = os.path.getsize(path)
            if have > size:                      # pieces are missing: can't continue it
                return None
            f = open(path, "r+b")
            f.truncate(have)                     # drop a piece that was half written
            f.seek(have)
        except OSError:
            return None
        dl.m_done = max(0, dl.m_done - (size - have))
        dl.status = DOWNLOADING
        dl.substatus = "Recording…" if len(dl.rec_parts) == 1 else \
            f"Recording part {len(dl.rec_parts)}…"
        dl.error = ""
        pid = uuid.uuid4().hex[:12]
        with self.lock:
            self.active[pid] = {"file": f, "dl": dl, "path": path, "next": 0,
                                "last": time.monotonic(), "lock": threading.Lock()}
        self.m.save()
        self.m.changed()
        return {"id": pid, "size": have}

    def append(self, pid, seq, data, pos=None):
        with self.lock:
            r = self.active.get(pid)
        if not r:
            return False
        with r["lock"]:
            if r["file"].closed:                 # this part was just closed
                return False
            r["last"] = time.monotonic()
            if seq < r["next"]:                  # already have this piece
                return True
            if data:
                r["file"].write(data)
                r["file"].flush()
                r["dl"].m_done += len(data)
            r["next"] = seq + 1
            if pos is not None and pos >= 0:
                r["dl"].rec_pos = float(pos)
        self.m.changed()
        return True

    def finish(self, pid):
        """End one part. The recording stays paused and can be continued."""
        with self.lock:
            r = self.active.pop(pid, None)
        if not r:
            return None
        with r["lock"]:
            try:
                r["file"].close()
            except OSError:
                pass
        dl = r["dl"]
        try:                                     # nothing recorded -> drop the part
            if os.path.getsize(r["path"]) == 0:
                os.remove(r["path"])
                name = os.path.basename(r["path"])
                if name in dl.rec_parts:
                    dl.rec_parts.remove(name)
        except OSError:
            pass
        if dl.status == DOWNLOADING and not self._parts_of(dl):
            dl.status = PAUSED
            dl.substatus = ""
            dl.speed = 0
        self.m.save()
        self.m.changed()
        return dl.rec_pos

    def done(self, sid):
        """Finish the whole recording: join the parts into one video."""
        dl = self.m.get(sid)
        if not dl or dl.kind != "record":
            return None
        with self.lock:
            if sid in self.finishing:            # Finish pressed twice (page and app)
                return None
            self.finishing.add(sid)
        try:
            return self._done(dl)
        except Exception as e:  # noqa: BLE001  e.g. a part is open in a video player
            why = "a part is open in another program" if isinstance(e, PermissionError) \
                else (str(e) or e.__class__.__name__)
            dl.status = PAUSED                    # nothing lost: it can be finished again
            dl.substatus = ""
            dl.speed = 0
            dl.error = (f"Couldn't save yet ({why}). Close the video if it's open, "
                        "then right-click → Finish recording")
            self.m.save()
            self.m.changed()
            return None
        finally:
            with self.lock:
                self.finishing.discard(sid)

    def _done(self, dl):
        for pid in self._parts_of(dl):
            self.finish(pid)
        if dl.status == COMPLETE:
            return dl.path
        dl.status = DOWNLOADING
        dl.substatus = "Joining parts…"
        dl.error = ""
        self.m.changed()
        parts = [os.path.join(dl.folder, p) for p in dl.rec_parts
                 if os.path.exists(os.path.join(dl.folder, p))]
        if not parts and dl.folder and dl.filename and os.path.exists(dl.path):
            parts = [dl.path]        # Kidlat closed while saving: the joined file is there
        # parts recorded fast (e.g. 2x): bring them back to normal speed first
        have_ff = self.m.tools.have("ffmpeg")
        fast = {os.path.basename(p): dl.rec_speeds.get(os.path.basename(p), 1) for p in parts}
        if have_ff:
            for p in parts:
                sp = fast[os.path.basename(p)]
                if abs(sp - 1) > 0.01:
                    dl.substatus = f"Bringing {sp:g}× part back to normal speed…"
                    self.m.changed()
                    if self.normal_speed(p, sp):
                        fast[os.path.basename(p)] = 1
        final = dl.path if parts == [dl.path] else \
            self.m.unique_path(dl.folder, dl.filename, exclude=dl)
        note = ""
        still_fast = {k: v for k, v in fast.items() if abs(v - 1) > 0.01}
        if len(parts) == 1:
            if parts[0] != final:
                os.replace(parts[0], final)
            if still_fast:
                still_fast = {os.path.basename(final): list(still_fast.values())[0]}
        elif len(parts) > 1:
            if not still_fast and self._join(parts, final):
                for p in parts:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            else:
                final = parts[0]
                note = (f"Saved as {len(parts)} parts - get FFmpeg in Video Tools "
                        "to join them next time" if not self.m.tools.have("ffmpeg")
                        else f"Saved as {len(parts)} parts (they couldn't be joined)")
        else:
            final = None
        if final:
            dl.substatus = "Making it easy to skip around…"
            self.m.changed()
            for p in ([final] if not note else parts):
                repair(p)
            dl.filename = os.path.basename(final)
            conv_err = None
            if not note and not still_fast:          # one finished video: make it MP4
                conv_err = self.m.make_compatible(dl)
                final = dl.path
            try:
                dl.size = dl.m_done = sum(os.path.getsize(os.path.join(dl.folder, p))
                                          for p in ([dl.filename] if not note else
                                                    [os.path.basename(x) for x in parts]))
            except OSError:
                dl.size = dl.m_done
        dl.status = COMPLETE if final else engine.ERROR
        dl.error = note if final else "Nothing was recorded"
        if final and not note and conv_err:
            dl.error = "kept as WebM (MP4 copy failed: " + conv_err[:80] + ")"
        dl.substatus = ""
        dl.speed = 0
        dl.finished = time.time()
        dl.rec_parts = [] if not note else dl.rec_parts
        dl.rec_speeds = still_fast
        if still_fast and final:
            sp = max(still_fast.values())
            dl.error = ((note + " · ") if note else "") + (
                f"Recorded at {sp:g}× - get FFmpeg in Video Tools, then right-click → "
                "Convert to normal speed")
        self.m.save()
        self.m.changed()
        if final:
            self.m.on_complete(dl)
        return final

    def normal_speed(self, path, speed):
        """Undo a fast recording: same picture (not re-encoded), sound slowed back."""
        ff = self.m.tools.path("ffmpeg")
        if not ff or speed <= 0:
            return False
        tmp = os.path.splitext(path)[0] + ".slowing.webm"
        rate = int(round(48000 / speed))
        cmd = [ff, "-y", "-loglevel", "error",
               "-itsscale", f"{speed:g}", "-i", path, "-i", path,
               "-map", "0:v:0?", "-map", "1:a:0?", "-c:v", "copy",
               "-af", f"aresample=48000,asetrate={rate},aresample=48000",
               "-c:a", "libopus", "-b:a", "160k", tmp]
        try:
            r = subprocess.run(cmd, capture_output=True, env=self.m.tools.env(),
                               creationflags=_tools.NO_WINDOW, stdin=subprocess.DEVNULL)
            if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, path)
                repair(path)
                return True
        except OSError:
            pass
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False

    def convert_pending(self, sid):
        """Right-click → Convert to normal speed (for recordings saved without FFmpeg)."""
        dl = self.m.get(sid)
        if not dl or not dl.rec_speeds:
            return "Nothing to convert."
        if not self.m.tools.have("ffmpeg"):
            return "Get FFmpeg in Video Tools first."
        dl.status = DOWNLOADING
        dl.substatus = "Bringing it back to normal speed…"
        self.m.changed()
        failed = []
        for name, sp in list(dl.rec_speeds.items()):
            p = os.path.join(dl.folder, name)
            if os.path.exists(p) and self.normal_speed(p, sp):
                del dl.rec_speeds[name]
            else:
                failed.append(name)
        if not failed and len(dl.rec_parts) > 1:      # now the parts can be joined too
            dl.substatus = "Joining parts…"
            self.m.changed()
            parts = [os.path.join(dl.folder, p) for p in dl.rec_parts]
            name = engine.safe_filename(dl.title or "Recording") + ".webm"
            final = self.m.unique_path(dl.folder, name, exclude=dl)
            if all(os.path.exists(p) for p in parts) and self._join(parts, final):
                for p in parts:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                repair(final)
                dl.filename = os.path.basename(final)
                dl.rec_parts = []
        elif not failed and len(dl.rec_parts) == 1:
            dl.filename = dl.rec_parts[0]
            dl.rec_parts = []
        if not failed and not dl.rec_parts:           # one normal video now: make it MP4
            self.m.make_compatible(dl)
        dl.status = COMPLETE
        dl.substatus = ""
        dl.error = "" if not failed else "Some parts couldn't be converted"
        try:
            dl.size = os.path.getsize(dl.path)
        except OSError:
            pass
        self.m.save()
        self.m.changed()
        return None if not failed else dl.error

    def lookup(self, page_url):
        """A paused recording of this page, if there is one."""
        key = url_key(page_url)
        with self.m.lock:
            items = list(self.m.downloads)
        for d in reversed(items):
            if d.kind == "record" and d.status != COMPLETE and d.rec_url == key \
                    and d.rec_parts:
                return {"session": d.id, "pos": d.rec_pos, "title": d.title,
                        "parts": len(d.rec_parts),
                        "recording": bool(self._parts_of(d))}
        return None

    def close_session(self, sid):
        """Called when a recording is removed from the list."""
        dl = self.m.get(sid)
        for pid in self._parts_of(dl) if dl else []:
            self.finish(pid)

    def finish_all(self):
        for pid in list(self.active):
            self.finish(pid)

    # ------------------------------------------------------------------ internals
    def _join(self, parts, final):
        ff = self.m.tools.path("ffmpeg")
        if not ff:
            return False
        fd, listfile = tempfile.mkstemp(suffix=".txt", prefix="kidlat-join-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for p in parts:
                f.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
        tmp = os.path.splitext(final)[0] + ".joining.webm"
        try:
            for extra in (["-c", "copy"],                       # fast, no quality loss
                          ["-c:v", "libvpx-vp9", "-b:v", "2500k", "-deadline", "realtime",
                           "-cpu-used", "8", "-c:a", "libopus"]):   # if sizes differ
                r = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat",
                                    "-safe", "0", "-i", listfile, *extra, tmp],
                                   capture_output=True, env=self.m.tools.env(),
                                   creationflags=_tools.NO_WINDOW,
                                   stdin=subprocess.DEVNULL)
                if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                    os.replace(tmp, final)
                    return True
            return False
        finally:
            for p in (listfile, tmp):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass

    def _watchdog(self):
        """End parts whose page was closed or refreshed without pressing Stop.
        Also keeps the PC from going to sleep while recording or downloading."""
        awake = False
        while True:
            busy = bool(self.active) or bool(self.finishing) or \
                any(d._job for d in list(self.m.downloads))
            if busy != awake:
                awake = keep_awake(busy)
            time.sleep(15)
            now = time.monotonic()
            with self.lock:
                stale = [pid for pid, r in self.active.items()
                         if now - r["last"] > IDLE_PAUSE]
            for pid in stale:
                self.finish(pid)
