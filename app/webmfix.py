"""
Makes WebM recordings easy to seek.

Firefox's recorder writes WebM as a "live stream": no total length and no
index (Cues) telling players where each moment is in the file. Players then
have to scan the file every time you jump, which looks like freezing.

fix_webm() rewrites the file with:
  - the total length (Duration),
  - an index entry for every keyframe cluster (Cues),
  - a SeekHead pointing to the index, and proper sizes for every part.
The video and sound data are copied unchanged (no quality loss, no FFmpeg).
It also cuts off a half-written piece at the end (e.g. page refreshed).
"""

import mmap
import os
import struct

EBML = 0x1A45DFA3
SEGMENT = 0x18538067
SEEKHEAD, INFO, TRACKS, CUES, CLUSTER = 0x114D9B74, 0x1549A966, 0x1654AE6B, 0x1C53BB6B, 0x1F43B675
TAGS, CHAPTERS, ATTACH = 0x1254C367, 0x1043A770, 0x1941A469
LEVEL1 = {SEEKHEAD, INFO, TRACKS, CUES, CLUSTER, TAGS, CHAPTERS, ATTACH}
TIMECODE_SCALE, DURATION = 0x2AD7B1, 0x4489
TRACK_ENTRY, TRACK_NUMBER, TRACK_TYPE = 0xAE, 0xD7, 0x83
CL_TIMECODE, SIMPLE_BLOCK, BLOCK_GROUP, BLOCK, REF_BLOCK = 0xE7, 0xA3, 0xA0, 0xA1, 0xFB
SEEK, SEEK_ID, SEEK_POS = 0x4DBB, 0x53AB, 0x53AC
CUE_POINT, CUE_TIME, CUE_TRACK_POS, CUE_TRACK, CUE_CLUSTER_POS = 0xBB, 0xB3, 0xB7, 0xF7, 0xF1
VOID = 0xEC
UNKNOWN = -1


class WebmError(Exception):
    pass


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def read_id(buf, pos):
    b = buf[pos]
    if b >= 0x80:
        n = 1
    elif b >= 0x40:
        n = 2
    elif b >= 0x20:
        n = 3
    elif b >= 0x10:
        n = 4
    else:
        raise WebmError("bad element id")
    if pos + n > len(buf):
        raise WebmError("truncated")
    return int.from_bytes(buf[pos:pos + n], "big"), pos + n


def read_size(buf, pos):
    b = buf[pos]
    n = 1
    mask = 0x80
    while n <= 8 and not (b & mask):
        n += 1
        mask >>= 1
    if n > 8:
        raise WebmError("bad size")
    if pos + n > len(buf):
        raise WebmError("truncated")
    value = b & (mask - 1)
    for i in range(1, n):
        value = (value << 8) | buf[pos + i]
    if value == (1 << (7 * n)) - 1:
        value = UNKNOWN
    return value, pos + n


def read_uint(buf, start, size):
    return int.from_bytes(buf[start:start + size], "big") if size else 0


def children(buf, start, end):
    """Yield (id, header_start, data_start, data_end) of elements in [start, end)."""
    pos = start
    while pos < end:
        try:
            eid, p = read_id(buf, pos)
            size, p = read_size(buf, p)
        except (WebmError, IndexError):
            return
        if size == UNKNOWN or p + size > end:
            return
        yield eid, pos, p, p + size
        pos = p + size


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def enc_id(eid):
    return eid.to_bytes((eid.bit_length() + 7) // 8, "big")


def enc_size8(n):
    return bytes([0x01]) + n.to_bytes(7, "big")


def el(eid, payload):
    return enc_id(eid) + enc_size8(len(payload)) + payload


def el_uint8(eid, value):
    return el(eid, value.to_bytes(8, "big"))


def el_float(eid, value):
    return el(eid, struct.pack(">d", value))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def needs_fix(path):
    """True if the file has no index or no length (typical for recordings)."""
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
            info = _scan(buf)
    except (OSError, ValueError, WebmError):
        return False
    return not (info["had_cues"] and info["had_duration"])


def _scan(buf):
    if len(buf) < 4:
        raise WebmError("not a WebM file")
    eid, p = read_id(buf, 0)
    if eid != EBML:
        raise WebmError("not a WebM file")
    size, p = read_size(buf, p)
    header_end = p + size
    eid, p = read_id(buf, header_end)
    if eid != SEGMENT:
        raise WebmError("no segment")
    seg_size, seg_data = read_size(buf, p)
    seg_end = len(buf) if seg_size == UNKNOWN else min(len(buf), seg_data + seg_size)

    info = tracks = None
    clusters = []            # (data_start, data_end)
    had_cues = had_duration = False
    pos = seg_data
    while pos < seg_end:
        try:
            eid, p = read_id(buf, pos)
            size, p = read_size(buf, p)
        except (WebmError, IndexError):
            break
        if eid == CLUSTER:
            if size == UNKNOWN or p + size > seg_end:
                # unknown/cut-off size: the cluster ends at the next top-level element
                end = p
                for cid, _h, _d, cend in children(buf, p, seg_end):
                    if cid in LEVEL1:
                        break
                    end = cend
                # stop at a top-level id that may follow
                nxt = end
                clusters.append((p, end))
                if nxt <= pos:
                    break
                pos = nxt
                continue
            clusters.append((p, p + size))
        elif size == UNKNOWN or p + size > seg_end:
            break
        elif eid == INFO:
            info = (p, p + size)
            had_duration = any(c[0] == DURATION for c in children(buf, p, p + size))
        elif eid == TRACKS:
            tracks = (p, p + size, pos)
        elif eid == CUES:
            had_cues = True
        pos = p + size
    if not info or not tracks:
        raise WebmError("missing file information")
    return {"header": (0, header_end), "info": info, "tracks": tracks,
            "clusters": clusters, "had_cues": had_cues, "had_duration": had_duration}


def fix_webm(src, dst=None):
    """
    Rewrite src as a seekable WebM. Writes to dst (or replaces src).
    Returns the duration in seconds.
    """
    dst = dst or src
    tmp = dst + ".fixing"
    with open(src, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        s = _scan(buf)
        i0, i1 = s["info"]
        scale = 1_000_000
        info_children = []
        for eid, h, d, e in children(buf, i0, i1):
            if eid == TIMECODE_SCALE:
                scale = read_uint(buf, d, e - d) or 1_000_000
            if eid in (DURATION, VOID):
                continue
            info_children.append(bytes(buf[h:e]))

        # which track is video (cues point at video keyframes)
        video_track = audio_track = None
        t0, t1, t_head = s["tracks"]
        for eid, _h, d, e in children(buf, t0, t1):
            if eid != TRACK_ENTRY:
                continue
            num = ttype = None
            for cid, _ch, cd, ce in children(buf, d, e):
                if cid == TRACK_NUMBER:
                    num = read_uint(buf, cd, ce - cd)
                elif cid == TRACK_TYPE:
                    ttype = read_uint(buf, cd, ce - cd)
            if ttype == 1 and video_track is None:
                video_track = num
            elif ttype == 2 and audio_track is None:
                audio_track = num
        cue_track = video_track or audio_track

        # walk the clusters: timecodes, keyframes, where each one ends
        cluster_info = []      # (data_start, data_end, cue_time or None)
        last_time = 0
        for c0, c1 in s["clusters"]:
            base = 0
            cue_time = None
            good_end = c0
            for eid, _h, d, e in children(buf, c0, c1):
                good_end = e
                if eid == CL_TIMECODE:
                    base = read_uint(buf, d, e - d)
                elif eid in (SIMPLE_BLOCK, BLOCK_GROUP):
                    if eid == BLOCK_GROUP:
                        block = next(((bd, be) for bid, _bh, bd, be in children(buf, d, e)
                                      if bid == BLOCK), None)
                        if not block:
                            continue
                        bd, be = block
                        key = not any(bid == REF_BLOCK for bid, *_ in children(buf, d, e))
                    else:
                        bd, be = d, e
                        key = None
                    try:
                        track, p = read_size(buf, bd)
                        rel = struct.unpack(">h", bytes(buf[p:p + 2]))[0]
                        flags = buf[p + 2]
                    except (WebmError, IndexError, struct.error):
                        continue
                    if key is None:
                        key = bool(flags & 0x80)
                    t = base + rel
                    last_time = max(last_time, t)
                    if cue_time is None and key and track == cue_track:
                        cue_time = max(0, t)
            if good_end > c0:
                cluster_info.append((c0, good_end, cue_time))
        if not cluster_info:
            raise WebmError("no video data")

        # frame length estimate so the duration includes the last frame
        duration_ticks = last_time + max(1, int(40_000_000 / scale))

        # ---- build the new file layout (positions relative to segment data) ----
        info_el = el(INFO, b"".join(info_children) + el_float(DURATION, float(duration_ticks)))
        tracks_el = bytes(buf[t_head:t1])                       # copied as-is

        n_cues = sum(1 for c in cluster_info if c[2] is not None)
        cue_point_len = len(el(CUE_POINT, el_uint8(CUE_TIME, 0) +
                                el(CUE_TRACK_POS, el_uint8(CUE_TRACK, 0) +
                                   el_uint8(CUE_CLUSTER_POS, 0))))
        cues_len = len(el(CUES, b"\0" * (cue_point_len * n_cues))) if n_cues else 0
        seek_entry_len = len(el(SEEK, el(SEEK_ID, b"\0\0\0\0") + el_uint8(SEEK_POS, 0)))
        n_seek = 3 if n_cues else 2
        seekhead_len = len(el(SEEKHEAD, b"\0" * (seek_entry_len * n_seek)))

        pos_info = seekhead_len
        pos_tracks = pos_info + len(info_el)
        pos_cues = pos_tracks + len(tracks_el)
        pos = pos_cues + cues_len
        cluster_pos = []
        for c0, c1, _t in cluster_info:
            cluster_pos.append(pos)
            pos += len(enc_id(CLUSTER)) + 8 + (c1 - c0)
        segment_len = pos

        seeks = [(INFO, pos_info), (TRACKS, pos_tracks)] + ([(CUES, pos_cues)] if n_cues else [])
        seekhead = el(SEEKHEAD, b"".join(
            el(SEEK, el(SEEK_ID, enc_id(i)) + el_uint8(SEEK_POS, p)) for i, p in seeks))
        cues = b""
        if n_cues:
            cues = el(CUES, b"".join(
                el(CUE_POINT, el_uint8(CUE_TIME, t) +
                   el(CUE_TRACK_POS, el_uint8(CUE_TRACK, cue_track) +
                      el_uint8(CUE_CLUSTER_POS, cp)))
                for (_c0, _c1, t), cp in zip(cluster_info, cluster_pos) if t is not None))
        assert len(seekhead) == seekhead_len and len(cues) == cues_len

        h0, h1 = s["header"]
        with open(tmp, "wb") as out:
            out.write(buf[h0:h1])
            out.write(enc_id(SEGMENT) + enc_size8(segment_len))
            out.write(seekhead)
            out.write(info_el)
            out.write(tracks_el)
            out.write(cues)
            for c0, c1, _t in cluster_info:
                out.write(enc_id(CLUSTER) + enc_size8(c1 - c0))
                for k in range(c0, c1, 4 * 1024 * 1024):
                    out.write(buf[k:min(c1, k + 4 * 1024 * 1024)])
    os.replace(tmp, dst)
    return duration_ticks * scale / 1e9

