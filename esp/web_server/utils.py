# web_server/utils.py — 底层公共工具：HTTP 响应、请求解析、存储映射、缩略图生成

import socket
import gc
import os
import json
import epd

# ---------- 全局配置 ----------
FRAME_BYTES = 800 * 480 // 2  # 192000 (4bpp)
CHUNK = 400 * 11
MAX_BINS_SOFT = 10000
MAX_BGS_SOFT = 10000
MAX_NOTE_LEN = 200
BOOT_CFG_PATH = '/boot_cfg.json'

THUMB_W = 200
THUMB_H = 120
THUMB_ROW_BYTES = THUMB_W // 2  # 100
THUMB_SIZE = THUMB_ROW_BYTES * THUMB_H  # 12000

CORS_HEADERS = (
    b"Access-Control-Allow-Origin: *\r\n"
    b"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
    b"Access-Control-Allow-Headers: Content-Type\r\n"
)

MIME_MAP = {
    ".html": b"text/html; charset=utf-8",
    ".htm": b"text/html; charset=utf-8",
    ".js": b"application/javascript; charset=utf-8",
    ".css": b"text/css; charset=utf-8",
    ".json": b"application/json; charset=utf-8",
    ".png": b"image/png",
    ".jpg": b"image/jpeg",
    ".svg": b"image/svg+xml",
    ".ico": b"image/x-icon",
    ".bin": b"application/octet-stream",
    ".idx": b"application/json; charset=utf-8",
    ".map": b"text/plain; charset=utf-8",
    ".thumb": b"application/octet-stream",
}


# ============================================================
# 适配器：对接 text_renderer 和 epd
# ============================================================
class EpdStreamAdapter:
    """将 text_renderer 的流式输出对接给 epd（直接发 SPI）"""
    def __init__(self, epd_mod):
        self.epd = epd_mod

    def init(self):
        self.epd.init()

    def begin_frame(self):
        self.epd.begin_frame()

    def write_chunk(self, chunk):
        self.epd.write_chunk(chunk)

    def end_frame(self):
        self.epd.end_frame()

    def sleep(self):
        self.epd.sleep()


# ============================================================
# 路径与存储工具
# ============================================================

def _ensure_dir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


def _get_storage_paths(storage):
    """返回 (bins_dir, bgs_dir)"""
    if storage == 'sd':
        return ("/sd/bins", "/sd/bgs")
    return ("/bins", "/bgs")


def _statvfs_free(path):
    try:
        st = os.statvfs(path)
        return st[0] * st[4]
    except Exception:
        return None


def _load_meta(path):
    try:
        with open(path, "r") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {}


def _save_meta(path, d):
    try:
        with open(path, "w") as f:
            json.dump(d, f)
    except OSError:
        pass


def _set_note(meta_path, slot, note):
    d = _load_meta(meta_path)
    key = str(slot)
    note = str(note or "")[:MAX_NOTE_LEN]
    if note:
        d[key] = note
    else:
        d.pop(key, None)
    _save_meta(meta_path, d)


def _scan_dir_slots(dir_path, meta_path, soft_limit):
    _ensure_dir(dir_path)
    meta = _load_meta(meta_path)
    out = []
    try:
        names = os.listdir(dir_path)
    except OSError:
        names = []
    for name in names:
        if not name.endswith('.bin'):
            continue
        try:
            idx = int(name[:-4])
        except (ValueError, TypeError):
            continue
        if idx < 0 or idx >= soft_limit:
            continue
        try:
            st = os.stat(dir_path + "/" + name)
        except OSError:
            continue
        has_thumb = False
        try:
            if os.stat(dir_path + "/" + str(idx) + ".thumb")[6] == THUMB_SIZE:
                has_thumb = True
        except OSError:
            pass
        out.append({
            "index": idx,
            "exists": True,
            "size": st[6],
            "note": str(meta.get(str(idx), ""))[:MAX_NOTE_LEN],
            "has_thumb": has_thumb,
        })
    out.sort(key=lambda x: x["index"])
    return out


def _scan_all_slots(kind):
    out = []
    for storage in ('sd', 'flash'):
        bins_dir, bgs_dir = _get_storage_paths(storage)

        # 安全检测 SD 卡是否挂载
        if storage == 'sd':
            try:
                os.stat("/sd")
            except OSError:
                continue

        d = bins_dir if kind == 'bins' else bgs_dir
        meta = d + "/meta.json"
        soft = MAX_BINS_SOFT if kind == 'bins' else MAX_BGS_SOFT
        for s in _scan_dir_slots(d, meta, soft):
            s["storage"] = storage
            out.append(s)
    return out


def _resolve_slot_path(kind, storage, slot):
    order = ['sd', 'flash'] if not storage else [storage]
    for st in order:
        bins_dir, bgs_dir = _get_storage_paths(st)

        if st == 'sd':
            try:
                os.stat("/sd")
            except OSError:
                continue

        d = bins_dir if kind == 'bins' else bgs_dir
        p = d + "/" + str(slot) + ".bin"
        try:
            os.stat(p)
            return (p, d + "/meta.json")
        except OSError:
            continue
    return (None, None)


def _storage_from_query(headers, default='flash'):
    s = _extract_query_param(headers, 'storage')
    if s in ('sd', 'flash'):
        return s
    return default


def _thumb_path_for(storage, kind, slot):
    bins_dir, bgs_dir = _get_storage_paths(storage)
    if bins_dir is None:
        return None
    d = bins_dir if kind == 'bins' else bgs_dir
    return d + '/' + str(slot) + '.thumb'


def _remove_thumb_for(bin_path):
    if not bin_path or not bin_path.endswith('.bin'):
        return
    try:
        os.remove(bin_path[:-4] + '.thumb')
    except OSError:
        pass


# ============================================================
# HTTP 底层工具
# ============================================================

def _mime_of(path):
    for ext, mime in MIME_MAP.items():
        if path.endswith(ext):
            return mime
    return b"application/octet-stream"


def _send(conn, status, body, content_type=b"text/plain"):
    body = body if isinstance(body, bytes) else str(body).encode()
    headers = (
            b"HTTP/1.1 " + status + b"\r\n"
                                    b"Content-Type: " + content_type + b"\r\n"
                                                                       b"Content-Length: " + str(
        len(body)).encode() + b"\r\n"
            + CORS_HEADERS +
            b"Connection: close\r\n\r\n"
    )
    conn.send(headers)
    if body:
        conn.send(body)


def _send_json(conn, data, status=b"200 OK"):
    _send(conn, status, json.dumps(data), content_type=b"application/json")


def _send_file(conn, path, content_type=None, cache_seconds=600):
    try:
        size = os.stat(path)[6]
    except OSError:
        _send(conn, b"404 Not Found", b"not found")
        return
    if content_type is None:
        content_type = _mime_of(path)
    cache_line = b""
    if cache_seconds and cache_seconds > 0:
        cache_line = b"Cache-Control: public, max-age=" + str(cache_seconds).encode() + b"\r\n"
    conn.send(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: " + content_type + b"\r\n"
                                           b"Content-Length: " + str(size).encode() + b"\r\n"
        + cache_line + CORS_HEADERS +
        b"Connection: close\r\n\r\n"
    )
    try:
        with open(path, "rb") as f:
            sent = 0
            last_gc = 0
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                off = 0
                chunk_len = len(chunk)
                while off < chunk_len:
                    n = conn.send(chunk[off:])
                    if n is None or n <= 0:
                        return
                    off += n
                sent += chunk_len
                if sent - last_gc >= 65536:
                    gc.collect()
                    last_gc = sent
    except Exception as e:
        print("[WEB] send_file error:", e)


def _send_thumb(conn, src_path, cache_seconds=3600):
    """发送缩略图（优先读取已有 .thumb，否则实时降采样）"""
    if src_path.endswith('.bin'):
        thumb_path = src_path[:-4] + '.thumb'
    else:
        thumb_path = src_path + '.thumb'

    try:
        if os.stat(thumb_path)[6] == THUMB_SIZE:
            _send_file(conn, thumb_path, content_type=b"application/octet-stream", cache_seconds=cache_seconds)
            return
    except OSError:
        pass

    # 实时降采样 4bpp: 800x480 -> 200x120
    try:
        size_src = os.stat(src_path)[6]
    except OSError:
        _send(conn, b"404 Not Found", b"not found")
        return
    if size_src != FRAME_BYTES:
        _send(conn, b"500 Internal Server Error", b"bad src size")
        return

    cache_line = b""
    if cache_seconds and cache_seconds > 0:
        cache_line = b"Cache-Control: public, max-age=" + str(cache_seconds).encode() + b"\r\n"

    conn.send(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Length: " + str(THUMB_SIZE).encode() + b"\r\n"
        + cache_line + CORS_HEADERS +
        b"Connection: close\r\n\r\n"
    )

    SRC_ROW_BYTES = 400
    BLOCK_H = 4

    try:
        src_buf = bytearray(BLOCK_H * SRC_ROW_BYTES)
        out_row = bytearray(THUMB_ROW_BYTES)
        mv_src = memoryview(src_buf)

        with open(src_path, "rb") as f:
            for ty in range(THUMB_H):
                # 读 4 行源数据（每行 400 字节，共 1600 字节）
                n = f.readinto(mv_src[:BLOCK_H * SRC_ROW_BYTES])
                if n is None or n < BLOCK_H * SRC_ROW_BYTES:
                    break

                src_row_off = 0  # 只取这 4 行的第一行进行水平采样
                for tx in range(THUMB_W):
                    sx = tx * 4
                    byte_idx = src_row_off + (sx >> 1)
                    src_byte = src_buf[byte_idx]
                    nib = (src_byte >> 4) if (sx & 1) == 0 else (src_byte & 0x0F)

                    if tx & 1:
                        out_row[tx >> 1] |= nib
                    else:
                        out_row[tx >> 1] = (nib << 4)

                off = 0
                while off < THUMB_ROW_BYTES:
                    sent = conn.send(out_row[off:])
                    if sent is None or sent <= 0:
                        return
                    off += sent
    except Exception as e:
        print("[WEB] thumb error:", e)


def _read_request(conn):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(512)
        if not chunk:
            return buf, b""
        buf += chunk
    idx = buf.find(b"\r\n\r\n") + 4
    return buf[:idx], buf[idx:]


def _read_body(conn, headers, body_prefix):
    cl = 0
    for line in headers.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                cl = int(line.split(b":", 1)[1].strip())
            except Exception:
                cl = 0
            break
    chunks = []
    if body_prefix:
        chunks.append(bytes(body_prefix))
    total = len(body_prefix) if body_prefix else 0
    while total < cl:
        want = min(1024, cl - total)
        chunk = conn.recv(want)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]
    return b"".join(chunks)


def _url_decode(s):
    out = bytearray()
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '%' and i + 2 < n:
            try:
                b = int(s[i + 1:i + 3], 16)
                out.append(b)
                i += 3
                continue
            except ValueError:
                pass
        if c == '+':
            out.append(0x20)
        else:
            try:
                out.append(ord(c) & 0xFF)
            except Exception:
                pass
        i += 1
    try:
        return out.decode('utf-8')
    except Exception:
        return ''


def _extract_query_param(headers, key):
    try:
        first_line = headers.split(b"\r\n", 1)[0]
        parts = first_line.split(b" ")
        if len(parts) < 2:
            return ""
        path = parts[1].decode()
        if "?" not in path:
            return ""
        qs = path.split("?", 1)[1]
        prefix = key + "="
        for kv in qs.split("&"):
            if kv.startswith(prefix):
                return _url_decode(kv[len(prefix):])
    except Exception:
        pass
    return ""


def _parse_int_after(headers, path_prefix, max_val=None):
    try:
        first_line = headers.split(b"\r\n", 1)[0]
        parts = first_line.split(b" ")
        if len(parts) < 2:
            return None
        path = parts[1]
        if not path.startswith(path_prefix):
            return None
        rest = path[len(path_prefix):]
        if b"?" in rest:
            rest = rest.split(b"?")[0]
        if b"/" in rest:
            rest = rest.split(b"/")[0]
        n = int(rest)
        if n < 0:
            return None
        if max_val is not None and n >= max_val:
            return None
        if n >= 100000:
            return None
        return n
    except Exception:
        return None