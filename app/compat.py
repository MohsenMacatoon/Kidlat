"""
Make videos play in every player, including the old Windows Media Player.

The format every player supports is MP4 with H.264 video (8-bit, 4:2:0) and AAC
sound. Many sites (YouTube 1440p/4K, VP9/AV1) and Firefox recordings (WebM:
VP8 + Opus) use newer formats that old players can't open.

to_mp4() checks a file and, only when needed, converts it:
  - parts already in the right format are copied (no quality loss, fast),
  - other parts are re-encoded (H.264 "veryfast", AAC 160 kbps).
"""

import collections
import json
import os
import re
import subprocess
import threading

import tools as _tools

GOOD_VIDEO = {"h264"}
GOOD_PIX = {"yuv420p", "yuvj420p", None, ""}       # 10-bit / 4:4:4 H.264 won't play in WMP
GOOD_AUDIO = {"aac", "mp3"}
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts", ".3gp",
             ".wmv", ".mpg", ".mpeg", ".ogv")
COVER_ART = ("mjpeg", "png", "bmp", "gif", "webp")


def _run(cmd, tools):
    return subprocess.run(cmd, capture_output=True, env=tools.env(),
                          creationflags=_tools.NO_WINDOW, stdin=subprocess.DEVNULL)


def probe(tools, path):
    """Return {"video", "audio", "pix", "duration", "ext"} (codec names), or None."""
    info = {"video": None, "audio": None, "pix": None, "ext": os.path.splitext(path)[1].lower(),
            "duration": 0.0}
    fp = tools.path("ffprobe")
    if fp:
        r = _run([fp, "-v", "error", "-show_entries",
                  "stream=codec_type,codec_name,pix_fmt:stream_disposition=attached_pic:"
                  "format=duration", "-of", "json", path], tools)
        if r.returncode == 0:
            try:
                data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
            except ValueError:
                data = None
            if data is not None:
                for s in data.get("streams", []):
                    kind = s.get("codec_type")
                    name = s.get("codec_name") or ""
                    if kind not in ("video", "audio") or info[kind]:
                        continue
                    if kind == "video" and (name in COVER_ART or
                                            (s.get("disposition") or {}).get("attached_pic")):
                        continue
                    info[kind] = name
                    if kind == "video":
                        info["pix"] = s.get("pix_fmt")
                try:
                    info["duration"] = float(data.get("format", {}).get("duration") or 0)
                except (TypeError, ValueError):
                    pass
                return info
    return _probe_with_ffmpeg(tools, path, info)


def _probe_with_ffmpeg(tools, path, info):
    """No ffprobe: read the same facts from 'ffmpeg -i'."""
    ff = tools.path("ffmpeg")
    if not ff:
        return None
    r = _run([ff, "-hide_banner", "-i", path], tools)
    text = r.stderr.decode("utf-8", "replace")
    if "Stream #" not in text:
        return None
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in text.splitlines():
        m = re.search(r"Stream #\S+.*?: (Video|Audio): (\w+)", line)
        if not m:
            continue
        kind, name = m.group(1).lower(), m.group(2)
        if info[kind] or (kind == "video" and (name in COVER_ART or "attached pic" in line)):
            continue
        info[kind] = name
        if kind == "video":
            pm = re.search(r"\), (\w+)\(|, (yuv\w+|nv12|p010\w*|gray\w*|rgb\w*)", line)
            info["pix"] = (pm.group(1) or pm.group(2)) if pm else None
    return info


def plays_everywhere(info):
    if not info:
        return True                      # can't tell -> leave it alone
    if not info["video"]:                # audio only: .m4a(aac) / .mp3 are fine
        return info["ext"] in (".m4a", ".mp3", ".aac") and info["audio"] in GOOD_AUDIO
    return (info["ext"] in (".mp4", ".m4v") and info["video"] in GOOD_VIDEO
            and info.get("pix") in GOOD_PIX
            and (info["audio"] is None or info["audio"] == "aac"))   # MP4 + AAC: safest


def needs_convert(tools, path):
    if not os.path.splitext(path)[1].lower() in VIDEO_EXT + (".m4a", ".opus", ".ogg", ".weba"):
        return False
    return not plays_everywhere(probe(tools, path))


def to_mp4(tools, path, progress=None, should_stop=None):
    """
    Convert `path` to a file every player can open. Returns the new path, or the
    old path if nothing was needed. Raises RuntimeError if FFmpeg fails.
    progress(fraction) is called while converting.
    """
    info = probe(tools, path)
    if plays_everywhere(info):
        return path
    ff = tools.path("ffmpeg")
    if not ff:
        raise RuntimeError("FFmpeg is needed (Video Tools)")
    stem = os.path.splitext(path)[0]
    audio_only = not info["video"]
    out = stem + (".m4a" if audio_only else ".mp4")   # same name as before is fine
    tmp = stem + ".converting" + (".m4a" if audio_only else ".mp4")
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-nostats",
           "-progress", "pipe:1", "-i", path, "-map", "0:v:0?", "-map", "0:a:0?",
           "-map_metadata", "0", "-sn", "-dn"]
    if not audio_only:
        if info["video"] in GOOD_VIDEO and info.get("pix") in GOOD_PIX:
            cmd += ["-c:v", "copy"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",     # H.264 needs even sizes
                    "-pix_fmt", "yuv420p", "-profile:v", "high"]
    if info["audio"]:
        keep = info["audio"] == "aac"
        cmd += ["-c:a", "copy"] if keep else ["-c:a", "aac", "-b:a", "160k"]
    cmd += ["-movflags", "+faststart", tmp]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=tools.env(), creationflags=_tools.NO_WINDOW,
                            stdin=subprocess.DEVNULL, text=True, errors="replace")
    running = getattr(tools, "running", None)
    if running is not None:
        running.add(proc)              # stopped if Kidlat is closed meanwhile
    # read FFmpeg's messages on the side: if they filled the pipe, FFmpeg would hang
    err_tail = collections.deque(maxlen=20)
    drain = threading.Thread(target=lambda: err_tail.extend(proc.stderr), daemon=True)
    drain.start()
    total = info["duration"] or 0
    stopped = False
    for line in proc.stdout:
        if should_stop and should_stop():
            stopped = True
            tools.kill(proc)
            break
        m = re.match(r"out_time_us=(\d+)", line.strip())
        if m and total and progress:
            progress(min(1.0, int(m.group(1)) / 1e6 / total))
    code = proc.wait()
    if running is not None:
        running.discard(proc)
    drain.join(timeout=5)
    err = "".join(err_tail)
    if stopped or code != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        if stopped or (should_stop and should_stop()):
            raise RuntimeError("stopped")
        raise RuntimeError("FFmpeg couldn't convert it: " + (err.strip()[-200:] or f"code {code}"))
    i = 1
    final = out
    while os.path.exists(final) and os.path.normcase(final) != os.path.normcase(path):
        final = f"{stem} ({i}){os.path.splitext(out)[1]}"
        i += 1
    os.replace(tmp, final)                 # replaces the old file if the name is the same
    if os.path.normcase(final) != os.path.normcase(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return final
