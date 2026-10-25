# web_server/utils.py — 底层公共工具
import socket
import gc
import os
import json
import epd

# 1bpp：800 * 480 / 8 = 48000 字节（原注释写 96000 是错的）
FRAME_BYTES = 800 * 480 // 8  # 48000
CHUNK = 400 * 11
MAX_BINS_SOFT = 10000
MAX_BGS_SOFT = 10000
MAX_NOTE_LEN = 200
BOOT_CFG_PATH = '/boot_cfg.json'

THUMB_W = 200
THUMB_H = 120
THUMB_ROW_BYTES = THUMB_W // 8  # 25 (1bpp)
THUMB_SIZE = THUMB_ROW_BYTES * THUMB_H  # 3000

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


class EpdStreamAdapter:
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


def _ensure_dir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


def _get_storage_paths(storage):
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


def _send_response_only(conn, status, body, content_type=b"text/plain; charset=utf-8"):
    """只发送 HTTP 响应，调用方之后继续使用 conn 或让 server 的 finally 关闭。

    用途：先响应避免浏览器 RST，再执行 end_frame()（阻塞 10~15 秒）。
    """
    body = body if isinstance(body, bytes) else str(body).encode()
    try:
        conn.send(b"HTTP/1.1 " + status + b"\r\n")
        conn.send(b"Content-Type: " + content_type + b"\r\n")
        conn.send(b"Content-Length: " + str(len(body)).encode() + b"\r\n")
        conn.send(CORS_HEADERS)
        conn.send(b"Connection: close\r\n\r\n")
        if body:
            conn.send(body)
        return True
    except Exception as e:
        print("[WEB] send_response_only error:", e)
        return False


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

    SRC_ROW_BYTES = 100
    BLOCK_H = 4

    try:
        src_buf = bytearray(BLOCK_H * SRC_ROW_BYTES)
        out_row = bytearray(THUMB_ROW_BYTES)
        mv_src = memoryview(src_buf)

        with open(src_path, "rb") as f:
            for ty in range(THUMB_H):
                n = f.readinto(mv_src[:BLOCK_H * SRC_ROW_BYTES])
                if n is None or n < BLOCK_H * SRC_ROW_BYTES:
                    break

                for tx in range(THUMB_W):
                    sx = tx * 4
                    byte_idx = (sx >> 3)
                    bit_idx = 7 - (sx & 7)
                    b = src_buf[byte_idx]
                    is_white = (b >> bit_idx) & 1

                    if not is_white:
                        out_row[tx >> 3] |= (0x80 >> (tx & 7))
                    else:
                        out_row[tx >> 3] &= ~(0x80 >> (tx & 7))

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


def _parse_content_length(headers):
    for line in headers.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                return int(line.split(b":", 1)[1].strip())
            except Exception:
                return 0
    return 0


def _read_body(conn, headers, body_prefix):
    cl = _parse_content_length(headers)
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


def _receive_to_file(conn, path, body_prefix, total_len=None, store_len=None):
    """接收数据保存到文件。

    total_len：HTTP body 总长度（Content-Length）。如果指定，会读取完整的
               body（包括超出 store_len 的部分并丢弃），避免浏览器 RST。
    store_len：只保存前 store_len 字节（默认 FRAME_BYTES）。
    """
    if store_len is None:
        store_len = FRAME_BYTES

    try:
        os.remove(path)
    except OSError:
        pass
    try:
        f = open(path, "wb")
    except OSError as e:
        return (False, 0, "open fail: " + str(e))

    received = 0
    stored = 0
    chunk_size = 1024

    try:
        if body_prefix:
            n = len(body_prefix)
            save_n = min(n, store_len)
            if save_n > 0:
                f.write(body_prefix[:save_n])
                stored += save_n
            received += n

        if total_len is not None and total_len > 0:
            need = total_len - received
        else:
            need = store_len - stored

        while need > 0:
            want = min(chunk_size, need)
            chunk = conn.recv(want)
            if not chunk:
                break
            received += len(chunk)
            if stored < store_len:
                save_n = min(len(chunk), store_len - stored)
                f.write(chunk[:save_n])
                stored += save_n
            need -= len(chunk)
            if (received % 32768) < chunk_size:
                gc.collect()

        try:
            f.flush()
        except Exception:
            pass
        f.close()
    except Exception as e:
        try:
            f.close()
        except Exception:
            pass
        try:
            os.remove(path)
        except Exception:
            pass
        return (False, received, str(e))
    gc.collect()
    return (True, stored, "")


def _handle_thumb_upload(conn, kind, slot, headers, body_prefix):
    storage = _storage_from_query(headers, default='flash')
    if storage == 'sd':
        try:
            os.stat("/sd")
        except OSError:
            _send(conn, b"400 Bad Request", b"sd not mounted")
            return

    bins_dir, bgs_dir = _get_storage_paths(storage)
    d = bins_dir if kind == 'bins' else bgs_dir
    _ensure_dir(d)
    path = d + "/" + str(slot) + ".thumb"

    total_len = _parse_content_length(headers)
    ok, n, err = _receive_to_file(conn, path, body_prefix,
                                  total_len=total_len, store_len=THUMB_SIZE)
    if not ok:
        _send(conn, b"507 Insufficient Storage", err.encode())
        return
    _send(conn, b"200 OK", str(n).encode())