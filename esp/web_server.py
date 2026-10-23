# web_server.py
import socket
import gc
import os
import json
import network
from jd7601 import JD7601

EPD = JD7601()
FRAME_BYTES = 720 * 480 // 2
CHUNK = 360 * 11
WWW_INDEX = "/www/index.html"
WWW_CONVERT = "/www/converter.html"
WWW_WIFI = "/www/wifi.html"
WWW_TEXT = "/www/text_panel.html"
WWW_EDIT = "/www/bin_editor.html"
WWW_MOSAIC = "/www/mosaic.html"

THUMB_W = 360
THUMB_H = 240
THUMB_ROW_BYTES = THUMB_W // 2
THUMB_SIZE = THUMB_ROW_BYTES * THUMB_H

MAX_BINS_SOFT = 10000
MAX_BGS_SOFT = 10000
MAX_NOTE_LEN = 200

BOOT_CFG_PATH = '/boot_cfg.json'

CORS_HEADERS = (
    b"Access-Control-Allow-Origin: *\r\n"
    b"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
    b"Access-Control-Allow-Headers: Content-Type\r\n"
)

COLOR_MAP = {
    "black": 0x00, "white": 0x11, "yellow": 0x22,
    "red": 0x33, "blue": 0x55, "green": 0x66,
}

MIME_MAP = {
    ".html": b"text/html; charset=utf-8",
    ".htm":  b"text/html; charset=utf-8",
    ".js":   b"application/javascript; charset=utf-8",
    ".css":  b"text/css; charset=utf-8",
    ".json": b"application/json; charset=utf-8",
    ".png":  b"image/png",
    ".jpg":  b"image/jpeg",
    ".svg":  b"image/svg+xml",
    ".ico":  b"image/x-icon",
    ".bin":  b"application/octet-stream",
    ".idx":  b"application/json; charset=utf-8",
    ".map":  b"text/plain; charset=utf-8",
    ".thumb": b"application/octet-stream",
}

ROUTE_MAP = {
    "/":            WWW_INDEX,
    "/index":       WWW_INDEX,
    "/index.html":  WWW_INDEX,
    "/convert":     WWW_CONVERT,
    "/edit":        WWW_EDIT,
    "/edit.html":   WWW_EDIT,
    "/wifi":        WWW_WIFI,
    "/text":        WWW_TEXT,
    "/text.html":   WWW_TEXT,
    "/mosaic":      WWW_MOSAIC,
    "/mosaic.html": WWW_MOSAIC,
}


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
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
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


# ★ 修复 + 优先 .thumb 文件
def _send_thumb(conn, src_path, cache_seconds=3600):
    # 快速路径：优先用预生成的 {slot}.thumb
    if src_path.endswith('.bin'):
        thumb_path = src_path[:-4] + '.thumb'
    else:
        thumb_path = src_path + '.thumb'

    try:
        if os.stat(thumb_path)[6] == THUMB_SIZE:
            _send_file(conn, thumb_path,
                       content_type=b"application/octet-stream",
                       cache_seconds=cache_seconds)
            return
    except OSError:
        pass

    # 慢速路径：从 bin 实时降采样（带 readinto 修复）
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

    SRC_ROW_BYTES = 360
    BLOCK_H = 8

    try:
        src_buf = bytearray(16 * SRC_ROW_BYTES)
        out_block = bytearray(BLOCK_H * THUMB_ROW_BYTES)
        mv_src = memoryview(src_buf)   # ★ 关键修复：memoryview 才是可写视图
        with open(src_path, "rb") as f:
            for ty_base in range(0, THUMB_H, BLOCK_H):
                block_h = min(BLOCK_H, THUMB_H - ty_base)
                src_rows = block_h * 2
                want = src_rows * SRC_ROW_BYTES
                n = f.readinto(mv_src[:want])   # ★ 修复：传入 memoryview 切片
                if n is None or n < want:
                    break

                blk_len = block_h * THUMB_ROW_BYTES

                for ty_local in range(block_h):
                    sy = ty_local * 2
                    src_row_off = sy * SRC_ROW_BYTES
                    dst_row_off = ty_local * THUMB_ROW_BYTES
                    for tx in range(THUMB_W):
                        sx = tx * 2
                        src_byte = src_buf[src_row_off + (sx >> 1)]
                        if sx & 1:
                            nib = src_byte & 0xF
                        else:
                            nib = (src_byte >> 4) & 0xF
                        if tx & 1:
                            out_block[dst_row_off + (tx >> 1)] |= nib
                        else:
                            out_block[dst_row_off + (tx >> 1)] = (nib << 4)

                mv = memoryview(out_block)
                off = 0
                while off < blk_len:
                    sent = conn.send(mv[off:blk_len])
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
                b = int(s[i+1:i+3], 16)
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
        try:
            return out.decode('latin-1')
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


def _display_solid(byte_val):
    EPD.init()
    EPD.begin_frame()
    row = bytes([byte_val]) * 360
    for _ in range(480):
        EPD.write_chunk(row)
    EPD.end_frame()
    EPD.sleep()


def _handle_upload(conn, body_prefix):
    EPD.init()
    EPD.begin_frame()
    received = 0
    if body_prefix:
        n = min(len(body_prefix), FRAME_BYTES)
        EPD.write_chunk(body_prefix[:n])
        received += n
        gc.collect()
    while received < FRAME_BYTES:
        chunk = conn.recv(min(CHUNK, FRAME_BYTES - received))
        if not chunk:
            break
        EPD.write_chunk(chunk)
        received += len(chunk)
        gc.collect()
    EPD.end_frame()
    EPD.sleep()
    return received


def _ensure_dir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


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
        # 检查 .thumb 是否存在
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
    import sd_helper
    out = []
    for storage in ('sd', 'flash'):
        bins_dir, bgs_dir = sd_helper.get_storage_paths(storage)
        if bins_dir is None:
            continue
        d = bins_dir if kind == 'bins' else bgs_dir
        meta = d + "/meta.json"
        soft = MAX_BINS_SOFT if kind == 'bins' else MAX_BGS_SOFT
        for s in _scan_dir_slots(d, meta, soft):
            s["storage"] = storage
            out.append(s)
    return out


def _resolve_slot_path(kind, storage, slot):
    import sd_helper
    order = ['sd', 'flash'] if not storage else [storage]
    for st in order:
        bins_dir, bgs_dir = sd_helper.get_storage_paths(st)
        if bins_dir is None:
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


def _statvfs_free(path):
    try:
        st = os.statvfs(path)
        return st[0] * st[4]
    except Exception:
        return None


# ---------- 缩略图上传 ----------

def _thumb_path_for(storage, kind, slot):
    import sd_helper
    bins_dir, bgs_dir = sd_helper.get_storage_paths(storage)
    if bins_dir is None:
        return None
    d = bins_dir if kind == 'bins' else bgs_dir
    return d + '/' + str(slot) + '.thumb'


def _handle_thumb_upload(conn, kind, slot, headers, body_prefix):
    storage = _storage_from_query(headers, default='flash')
    p = _thumb_path_for(storage, kind, slot)
    if p is None:
        _send(conn, b"400 Bad Request", b"storage unavailable")
        return
    _ensure_dir(p.rsplit('/', 1)[0])
    try:
        conn.settimeout(30)
    except Exception:
        pass

    try:
        os.remove(p)
    except OSError:
        pass

    try:
        f = open(p, "wb")
    except OSError as e:
        _send(conn, b"500 Internal Server Error", str(e).encode())
        return

    received = 0
    try:
        if body_prefix:
            n = min(len(body_prefix), THUMB_SIZE)
            f.write(body_prefix[:n])
            received += n
        while received < THUMB_SIZE:
            want = min(1024, THUMB_SIZE - received)
            chunk = conn.recv(want)
            if not chunk:
                break
            f.write(chunk)
            received += len(chunk)
        f.close()
    except Exception as e:
        try: f.close()
        except: pass
        try: os.remove(p)
        except: pass
        _send(conn, b"500 Internal Server Error", str(e).encode())
        return

    if received != THUMB_SIZE:
        try: os.remove(p)
        except: pass
        _send(conn, b"400 Bad Request",
              ("expected %d, got %d" % (THUMB_SIZE, received)).encode())
        return

    _send(conn, b"200 OK", str(received).encode())


def _remove_thumb_for(bin_path):
    """删除与 bin 同目录的 .thumb"""
    if not bin_path or not bin_path.endswith('.bin'):
        return
    try:
        os.remove(bin_path[:-4] + '.thumb')
    except OSError:
        pass


# ---------- 开机指定 bin ----------

def _handle_boot_bin_get(conn):
    try:
        with open(BOOT_CFG_PATH, 'r') as f:
            cfg = json.load(f)
            if not isinstance(cfg, dict):
                cfg = {}
    except (OSError, ValueError):
        cfg = {}

    storage = cfg.get('storage')
    index = cfg.get('index')
    if storage not in ('sd', 'flash'):
        storage = None
    if not isinstance(index, int) or index < 0:
        index = None

    _send_json(conn, {
        "storage": storage,
        "index": index,
        "defined": (storage is not None and index is not None),
    })


def _handle_boot_bin_set(conn, headers, body_prefix):
    try:
        body = _read_body(conn, headers, body_prefix)
        data = json.loads(body) if body else {}
    except Exception as e:
        _send(conn, b"400 Bad Request", str(e).encode())
        return

    storage = data.get('storage')
    index = data.get('index')

    if storage not in ('sd', 'flash') or not isinstance(index, int) or index < 0:
        try:
            os.remove(BOOT_CFG_PATH)
            _send(conn, b"200 OK", b"cleared")
        except OSError:
            _send(conn, b"200 OK", b"cleared")
        return

    try:
        with open(BOOT_CFG_PATH, 'w') as f:
            json.dump({'storage': storage, 'index': index}, f)
        _send(conn, b"200 OK", b"OK")
    except OSError as e:
        _send(conn, b"500 Internal Server Error", str(e).encode())


# ---------- bins ----------

def _handle_bins_list(conn):
    try:
        conn.settimeout(10)
    except Exception:
        pass
    import sd_helper
    slots = _scan_all_slots('bins')
    free_sd = None
    if sd_helper.is_sd_active():
        free_sd = _statvfs_free(sd_helper.SD_MOUNT)
    free_flash = _statvfs_free('/')
    _send_json(conn, {
        "slots": slots,
        "total": len(slots),
        "slot_size": FRAME_BYTES,
        "thumb_size": THUMB_SIZE,
        "sd_active": sd_helper.is_sd_active(),
        "free_sd": free_sd,
        "free_flash": free_flash,
    })


def _handle_bin_get(conn, slot, headers):
    storage = _storage_from_query(headers, default='')
    p, _ = _resolve_slot_path('bins', storage, slot)
    if p is None:
        _send(conn, b"404 Not Found", b"not found")
        return
    _send_file(conn, p, content_type=b"application/octet-stream", cache_seconds=0)


def _handle_bin_delete(conn, slot, headers):
    try:
        conn.settimeout(10)
    except Exception:
        pass
    storage = _storage_from_query(headers, default='')
    p, meta = _resolve_slot_path('bins', storage, slot)
    if p is None:
        _send(conn, b"404 Not Found", b"not found")
        return
    _remove_thumb_for(p)   # ★ 删除 thumb
    try:
        os.remove(p)
    except OSError:
        pass
    if meta:
        _set_note(meta, slot, "")
    _send(conn, b"200 OK", b"OK")


def _receive_to_file(conn, path, body_prefix):
    try:
        os.remove(path)
    except OSError:
        pass
    try:
        f = open(path, "wb")
    except OSError as e:
        return (False, 0, "open fail: " + str(e))

    received = 0
    total = FRAME_BYTES
    chunk_size = 1024
    try:
        if body_prefix:
            n = min(len(body_prefix), total)
            f.write(body_prefix[:n])
            received += n
        while received < total:
            want = min(chunk_size, total - received)
            chunk = conn.recv(want)
            if not chunk:
                break
            f.write(chunk)
            received += len(chunk)
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
    return (True, received, "")


def _display_bin_file(path):
    try:
        EPD.init()
        EPD.begin_frame()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                EPD.write_chunk(chunk)
                gc.collect()
        EPD.end_frame()
        EPD.sleep()
        return (True, "")
    except Exception as e:
        try: EPD.end_frame()
        except Exception: pass
        try: EPD.sleep()
        except Exception: pass
        return (False, str(e))


def _handle_bin_upload(conn, slot, headers, body_prefix):
    import sd_helper
    storage = _storage_from_query(headers, default='flash')
    bins_dir, _ = sd_helper.get_storage_paths(storage)
    if bins_dir is None:
        _send(conn, b"400 Bad Request", b"storage unavailable")
        return
    _ensure_dir(bins_dir)
    try:
        conn.settimeout(60)
    except Exception:
        pass
    path = bins_dir + "/" + str(slot) + ".bin"
    ok, received, err = _receive_to_file(conn, path, body_prefix)
    if not ok:
        _send(conn, b"507 Insufficient Storage", err.encode())
        return
    _remove_thumb_for(path)   # ★ bin 覆盖 → thumb 失效
    note = _extract_query_param(headers, "note")
    if note:
        _set_note(bins_dir + "/meta.json", slot, note)
    _send(conn, b"200 OK", str(received).encode())


def _handle_bin_upload_and_display(conn, slot, headers, body_prefix):
    import sd_helper
    storage = _storage_from_query(headers, default='flash')
    bins_dir, _ = sd_helper.get_storage_paths(storage)
    if bins_dir is None:
        _send(conn, b"400 Bad Request", b"storage unavailable")
        return
    _ensure_dir(bins_dir)
    try:
        conn.settimeout(60)
    except Exception:
        pass
    path = bins_dir + "/" + str(slot) + ".bin"
    ok, received, err = _receive_to_file(conn, path, body_prefix)
    if not ok:
        _send(conn, b"507 Insufficient Storage", err.encode())
        return
    _remove_thumb_for(path)
    note = _extract_query_param(headers, "note")
    if note:
        _set_note(bins_dir + "/meta.json", slot, note)
    try:
        conn.settimeout(120)
    except Exception:
        pass
    ok2, err2 = _display_bin_file(path)
    if not ok2:
        _send(conn, b"500 Internal Server Error",
              ("display fail: " + err2).encode())
        return
    _send(conn, b"200 OK", str(received).encode())


def _handle_bin_display(conn, slot, headers):
    storage = _storage_from_query(headers, default='')
    p, _ = _resolve_slot_path('bins', storage, slot)
    if p is None:
        _send(conn, b"404 Not Found", b"not found")
        return
    try:
        conn.settimeout(120)
    except Exception:
        pass
    ok, err = _display_bin_file(p)
    if not ok:
        _send(conn, b"500 Internal Server Error", err.encode())
        return
    _send(conn, b"200 OK", b"OK")


def _handle_bin_note(conn, slot, headers, body_prefix):
    storage = _storage_from_query(headers, default='')
    p, meta = _resolve_slot_path('bins', storage, slot)
    if p is None or meta is None:
        _send(conn, b"404 Not Found", b"not found")
        return
    try:
        conn.settimeout(10)
    except Exception:
        pass
    try:
        body = _read_body(conn, headers, body_prefix)
        try:
            data = json.loads(body)
            note = str(data.get("note", ""))[:MAX_NOTE_LEN]
        except Exception:
            try:
                note = body.decode('utf-8')[:MAX_NOTE_LEN]
            except Exception:
                note = ""
    except Exception:
        note = ""
    _set_note(meta, slot, note)
    _send(conn, b"200 OK", b"OK")


# ---------- bgs ----------

def _handle_bgs_list(conn):
    try:
        conn.settimeout(10)
    except Exception:
        pass
    import sd_helper
    slots = _scan_all_slots('bgs')
    free_sd = None
    if sd_helper.is_sd_active():
        free_sd = _statvfs_free(sd_helper.SD_MOUNT)
    free_flash = _statvfs_free('/')
    _send_json(conn, {
        "slots": slots,
        "total": len(slots),
        "slot_size": FRAME_BYTES,
        "thumb_size": THUMB_SIZE,
        "sd_active": sd_helper.is_sd_active(),
        "free_sd": free_sd,
        "free_flash": free_flash,
    })


def _handle_bg_get(conn, slot, headers):
    storage = _storage_from_query(headers, default='')
    p, _ = _resolve_slot_path('bgs', storage, slot)
    if p is None:
        _send(conn, b"404 Not Found", b"not found")
        return
    _send_file(conn, p, content_type=b"application/octet-stream", cache_seconds=0)


def _handle_bg_delete(conn, slot, headers):
    storage = _storage_from_query(headers, default='')
    p, meta = _resolve_slot_path('bgs', storage, slot)
    if p is None:
        _send(conn, b"404 Not Found", b"not found")
        return
    _remove_thumb_for(p)
    try:
        os.remove(p)
    except OSError:
        pass
    if meta:
        _set_note(meta, slot, "")
    _send(conn, b"200 OK", b"OK")


def _handle_bg_upload(conn, slot, headers, body_prefix):
    import sd_helper
    storage = _storage_from_query(headers, default='flash')
    _, bgs_dir = sd_helper.get_storage_paths(storage)
    if bgs_dir is None:
        _send(conn, b"400 Bad Request", b"storage unavailable")
        return
    _ensure_dir(bgs_dir)
    try:
        conn.settimeout(60)
    except Exception:
        pass
    path = bgs_dir + "/" + str(slot) + ".bin"
    ok, received, err = _receive_to_file(conn, path, body_prefix)
    if not ok:
        _send(conn, b"507 Insufficient Storage", err.encode())
        return
    _remove_thumb_for(path)
    note = _extract_query_param(headers, "note")
    if note:
        _set_note(bgs_dir + "/meta.json", slot, note)
    _send(conn, b"200 OK", str(received).encode())


def _handle_bg_note(conn, slot, headers, body_prefix):
    storage = _storage_from_query(headers, default='')
    p, meta = _resolve_slot_path('bgs', storage, slot)
    if p is None or meta is None:
        _send(conn, b"404 Not Found", b"not found")
        return
    try:
        conn.settimeout(10)
    except Exception:
        pass
    try:
        body = _read_body(conn, headers, body_prefix)
        try:
            data = json.loads(body)
            note = str(data.get("note", ""))[:MAX_NOTE_LEN]
        except Exception:
            try:
                note = body.decode('utf-8')[:MAX_NOTE_LEN]
            except Exception:
                note = ""
    except Exception:
        note = ""
    _set_note(meta, slot, note)
    _send(conn, b"200 OK", b"OK")


# ---------- 字体 ----------

def _handle_fonts_list(conn):
    try:
        conn.settimeout(30)
    except Exception:
        pass
    try:
        st = os.stat('/fonts_index.json')
        size = st[6]
        conn.send(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"Content-Length: " + str(size).encode() + b"\r\n"
            b"Cache-Control: no-store\r\n"
            + CORS_HEADERS +
            b"Connection: close\r\n\r\n"
        )
        with open('/fonts_index.json', 'rb') as f:
            sent = 0
            while sent < size:
                chunk = f.read(512)
                if not chunk:
                    break
                off = 0
                while off < len(chunk):
                    n = conn.send(chunk[off:])
                    if n is None or n <= 0:
                        break
                    off += n
                sent += off
                if off < len(chunk):
                    break
                gc.collect()
        return
    except OSError:
        pass
    except Exception as e:
        print('[WEB] /fonts/list 索引发送失败:', e)
        try:
            conn.close()
        except Exception:
            pass
        return

    print('[WEB] /fonts/list 无索引，现场扫描')
    import sd_helper
    out = []

    def scan(fs_root, storage):
        try:
            names = os.listdir(fs_root)
        except OSError:
            return
        groups = {}
        for name in names:
            if not name.endswith('.bin'):
                continue
            base = name[:-4]
            parts = base.rsplit('_', 2)
            if len(parts) != 3:
                continue
            try:
                family = parts[0]
                size = int(parts[1])
            except ValueError:
                continue
            key = (family, size)
            groups[key] = groups.get(key, 0) + 1
        del names
        gc.collect()
        for (fam, size), cnt in groups.items():
            out.append({"family": fam, "size": size, "storage": storage,
                        "chunk_count": cnt})
        del groups
        gc.collect()

    scan('/www/fonts', 'flash')
    if sd_helper.is_sd_active():
        scan(sd_helper.SD_MOUNT + '/e6/fonts', 'sd')
    gc.collect()
    out.sort(key=lambda x: (x['storage'] != 'flash', x['family'], x['size']))
    _send_json(conn, {"fonts": out})


def _handle_fonts_get(conn, headers):
    storage = _extract_query_param(headers, 'storage') or 'flash'
    name = _extract_query_param(headers, 'name')
    if not name:
        _send(conn, b"400 Bad Request", b"missing name")
        return
    if '/' in name or '\\' in name or '..' in name:
        _send(conn, b"400 Bad Request", b"bad name")
        return
    if storage == 'sd':
        import sd_helper
        if not sd_helper.is_sd_active():
            _send(conn, b"400 Bad Request", b"sd not mounted")
            return
        path = sd_helper.SD_MOUNT + '/e6/fonts/' + name + '.bin'
    else:
        path = '/www/fonts/' + name + '.bin'
    _send_file(conn, path,
               content_type=b"application/octet-stream",
               cache_seconds=3600)


# ---------- /text/render ----------

def _handle_text_render(conn, headers, body_prefix):
    import gc as _gc
    _gc.collect()
    print("[TEXT] start: free=%d" % _gc.mem_free())

    try:
        body = _read_body(conn, headers, body_prefix)
        body_size = len(body)
        print("[TEXT] body read: %d bytes, free=%d" % (body_size, _gc.mem_free()))

        _gc.collect()
        try:
            params = json.loads(body)
        except MemoryError:
            print("[TEXT] OOM in json.loads, body_size=%d" % body_size)
            raise
        del body
        _gc.collect()
        print("[TEXT] parsed json, free=%d" % _gc.mem_free())

        from text_renderer import (
            render_full, render_and_display,
            render_and_display_stream, _create_font_direct,
        )

        size = int(params.get("size", 32))
        font_path = params.get("font_path") or None
        font_paths = params.get("font_paths") or None
        per_char = params.get("per_char")

        default_key = '|'.join(font_paths) if font_paths else ''
        extra_fonts = {}
        if per_char:
            fp_set = set()
            for v in per_char.values():
                if isinstance(v, dict) and v.get('font_paths'):
                    fp = v['font_paths']
                    if fp:
                        key = '|'.join(fp)
                        if key == default_key:
                            continue
                        fp_set.add(key)

            print("[TEXT] distinct extra fonts: %d" % len(fp_set))

            MAX_EXTRA = 2
            if len(fp_set) > MAX_EXTRA:
                print("[TEXT] too many extra fonts, truncating to %d" % MAX_EXTRA)
                fp_set = set(list(fp_set)[:MAX_EXTRA])

            for key in fp_set:
                fp = key.split('|')
                f = _create_font_direct(fp)
                if f:
                    extra_fonts[key] = f
                    _gc.collect()
                    print("[TEXT] loaded extra: free=%d" % _gc.mem_free())

        args = dict(
            size=size,
            direction=int(params.get("direction", 0)),
            per_char=per_char,
            default_style=params.get("default_style"),
            align=params.get("align", "left"),
            valign=params.get("valign", "top"),
            line_height=float(params.get("line_height", 1.5)),
            margin_top=int(params.get("margin_top", 20)),
            margin_bottom=int(params.get("margin_bottom", 20)),
            margin_left=int(params.get("margin_left", 20)),
            margin_right=int(params.get("margin_right", 20)),
            font_path=font_path,
            font_paths=font_paths,
            extra_fonts=extra_fonts,
            line_extras=params.get("line_extras") or None,
        )
        text = params.get("text", "")
        dry_run = bool(params.get("dry_run", False))
        stream = bool(params.get("stream", True))
        background = params.get("background") or None

        try:
            if dry_run:
                bin_data = render_full(text, background=background, **args)
                _send(conn, b"200 OK", bin_data,
                      content_type=b"application/octet-stream")
            elif stream:
                try:
                    conn.settimeout(300)
                except Exception:
                    pass
                band = int(params.get("band_rows", 8))

                _send_json(conn, {"ok": True, "mode": "stream",
                                  "band_rows": band, "bg": background,
                                  "extra_fonts": len(extra_fonts),
                                  "started": True})

                try:
                    render_and_display_stream(EPD, text, band_rows=band,
                                              background=background, **args)
                except Exception as render_err:
                    print("[TEXT] deferred render fail:", render_err)
                    try:
                        import sys as _sys
                        _sys.print_exception(render_err)
                    except Exception:
                        pass
            else:
                render_and_display(EPD, text, background=background, **args)
                _send_json(conn, {"ok": True, "mode": "buffered"})
        finally:
            for f in extra_fonts.values():
                try: f.close()
                except: pass
            extra_fonts.clear()
            _gc.collect()
            print("[TEXT] end: free=%d" % _gc.mem_free())

    except MemoryError as e:
        free = _gc.mem_free()
        print("[TEXT] OOM! free=%d" % free)
        try:
            _send_json(conn, {"ok": False, "error": "OOM, free=%d" % free},
                       b"500 Internal Server Error")
        except Exception:
            pass
    except Exception as e:
        print("[TEXT] error:", e)
        try:
            _send_json(conn, {"ok": False, "error": str(e)},
                       b"500 Internal Server Error")
        except Exception:
            pass


# ---------- 系统 / WiFi ----------

def _system_status(conn):
    info = {
        "ap":  {"active": False, "ip": None, "ssid": None},
        "sta": {"connected": False, "ip": None, "ssid": None, "rssi": None},
    }
    try:
        ap = network.WLAN(network.AP_IF)
        if ap.active():
            essid = None
            try:
                essid = ap.config("essid")
                if isinstance(essid, bytes):
                    essid = essid.decode()
            except Exception:
                pass
            info["ap"] = {"active": True, "ip": ap.ifconfig()[0], "ssid": essid}
    except Exception:
        pass
    try:
        sta = network.WLAN(network.STA_IF)
        if sta.isconnected():
            essid = None
            try:
                essid = sta.config("essid")
                if isinstance(essid, bytes):
                    essid = essid.decode()
            except Exception:
                pass
            try:
                rssi = sta.status("rssi")
            except Exception:
                rssi = None
            info["sta"] = {"connected": True, "ip": sta.ifconfig()[0],
                           "ssid": essid, "rssi": rssi}
    except Exception:
        pass
    _send_json(conn, info)


def _wifi_scan(conn):
    try:
        conn.settimeout(30)
    except Exception:
        pass
    try:
        import wifi_manager
        nets = wifi_manager.scan_networks()
        _send_json(conn, [{"ssid": s, "rssi": r, "auth": a} for s, r, a in nets])
    except Exception as e:
        _send(conn, b"500 Internal Server Error", str(e))


def _wifi_list(conn):
    try:
        import wifi_manager
        cfg = wifi_manager.load_config()
        _send_json(conn, cfg.get("known_networks", []))
    except Exception as e:
        _send(conn, b"500 Internal Server Error", str(e))


def _wifi_save(conn, headers, body_prefix):
    try:
        body = _read_body(conn, headers, body_prefix)
        data = json.loads(body)
        ssid = data["ssid"]
        password = data.get("password", "")
        priority = int(data.get("priority", 1))
        import wifi_manager
        cfg = wifi_manager.load_config()
        nets = cfg.get("known_networks", [])
        found = False
        for n in nets:
            if n["ssid"] == ssid:
                n["password"] = password
                n["priority"] = priority
                found = True
                break
        if not found:
            nets.append({"ssid": ssid, "password": password, "priority": priority})
        cfg["known_networks"] = nets
        wifi_manager.save_config(cfg)
        _send(conn, b"200 OK", b"OK")
    except Exception as e:
        _send(conn, b"500 Internal Server Error", str(e))


def _wifi_delete(conn, headers, body_prefix):
    try:
        body = _read_body(conn, headers, body_prefix)
        data = json.loads(body)
        ssid = data["ssid"]
        import wifi_manager
        cfg = wifi_manager.load_config()
        cfg["known_networks"] = [n for n in cfg.get("known_networks", [])
                                  if n["ssid"] != ssid]
        wifi_manager.save_config(cfg)
        _send(conn, b"200 OK", b"OK")
    except Exception as e:
        _send(conn, b"500 Internal Server Error", str(e))


# ---------- 静态文件 ----------

def _serve_static(conn, headers):
    try:
        line = headers.split(b"\r\n", 1)[0]
        path = line.split(b" ")[1].decode()
    except Exception:
        _send_file(conn, WWW_INDEX)
        return
    if "?" in path:
        path = path.split("?")[0]
    if "#" in path:
        path = path.split("#")[0]

    if path in ROUTE_MAP:
        _send_file(conn, ROUTE_MAP[path])
        return
    if ".." in path or "//" in path:
        _send_file(conn, WWW_INDEX)
        return

    full = "/www" + path
    exists = False
    try:
        os.stat(full)
        exists = True
    except OSError:
        exists = False
    except Exception:
        exists = False

    if exists:
        ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
        if ext in ('js', 'css'):
            _send_file(conn, full, cache_seconds=0)
        else:
            _send_file(conn, full)
        return

    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
    if ext in ('js', 'css', 'map', 'json', 'png', 'jpg', 'jpeg',
               'svg', 'ico', 'bin', 'woff', 'woff2', 'ttf', 'thumb'):
        _send(conn, b"404 Not Found", b"not found: " + path.encode())
        return
    _send_file(conn, WWW_INDEX)


def start_server():
    try:
        import sd_helper
        sd_helper.ensure_dirs()
        if sd_helper.is_sd_active():
            print("[WEB] storage: SD + Flash")
        else:
            print("[WEB] storage: Flash only (SD not mounted)")
    except Exception as e:
        print("[WEB] sd_helper error:", e)
        _ensure_dir("/bins")
        _ensure_dir("/bgs")

    try:
        st = os.stat('/fonts_index.json')
        print('[WEB] /fonts_index.json: %d 字节' % st[6])
    except OSError:
        print('[WEB] 警告: /fonts_index.json 不存在')

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(5)

    try:
        ap = network.WLAN(network.AP_IF)
        if ap.active():
            print("[WEB] AP: http://" + ap.ifconfig()[0])
    except Exception:
        pass
    try:
        sta = network.WLAN(network.STA_IF)
        if sta.isconnected():
            print("[WEB] STA: http://" + sta.ifconfig()[0])
    except Exception:
        pass

    while True:
        conn, _ = s.accept()
        try:
            headers, body_prefix = _read_request(conn)

            if headers.startswith(b"OPTIONS "):
                conn.send(
                    b"HTTP/1.1 200 OK\r\n"
                    + CORS_HEADERS +
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
                continue

            # boot bin
            if headers.startswith(b"GET /boot/bin"):
                _handle_boot_bin_get(conn)
            elif headers.startswith(b"POST /boot/bin"):
                _handle_boot_bin_set(conn, headers, body_prefix)

            # thumb upload（★ 必须在 /bins/upload/ 之前）
            elif headers.startswith(b"POST /bins/thumb_upload/"):
                slot = _parse_int_after(headers, b"/bins/thumb_upload/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_thumb_upload(conn, 'bins', slot, headers, body_prefix)
            elif headers.startswith(b"POST /bgs/thumb_upload/"):
                slot = _parse_int_after(headers, b"/bgs/thumb_upload/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_thumb_upload(conn, 'bgs', slot, headers, body_prefix)

            # thumb fetch
            elif headers.startswith(b"GET /bins/thumb/"):
                slot = _parse_int_after(headers, b"/bins/thumb/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    storage = _storage_from_query(headers, default='')
                    p, _ = _resolve_slot_path('bins', storage, slot)
                    if p is None:
                        _send(conn, b"404 Not Found", b"not found")
                    else:
                        _send_thumb(conn, p)
            elif headers.startswith(b"GET /bgs/thumb/"):
                slot = _parse_int_after(headers, b"/bgs/thumb/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    storage = _storage_from_query(headers, default='')
                    p, _ = _resolve_slot_path('bgs', storage, slot)
                    if p is None:
                        _send(conn, b"404 Not Found", b"not found")
                    else:
                        _send_thumb(conn, p)

            # bins
            elif headers.startswith(b"GET /bins/list"):
                _handle_bins_list(conn)
            elif headers.startswith(b"GET /bins/get/"):
                slot = _parse_int_after(headers, b"/bins/get/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_bin_get(conn, slot, headers)
            elif headers.startswith(b"POST /bins/upload_and_display/"):
                slot = _parse_int_after(headers, b"/bins/upload_and_display/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_bin_upload_and_display(conn, slot, headers, body_prefix)
            elif headers.startswith(b"POST /bins/upload/"):
                slot = _parse_int_after(headers, b"/bins/upload/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_bin_upload(conn, slot, headers, body_prefix)
            elif headers.startswith(b"POST /bins/delete/"):
                slot = _parse_int_after(headers, b"/bins/delete/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_bin_delete(conn, slot, headers)
            elif headers.startswith(b"POST /bins/note/"):
                slot = _parse_int_after(headers, b"/bins/note/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_bin_note(conn, slot, headers, body_prefix)
            elif headers.startswith(b"GET /bins/display/"):
                slot = _parse_int_after(headers, b"/bins/display/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_bin_display(conn, slot, headers)

            # bgs
            elif headers.startswith(b"GET /bgs/list"):
                _handle_bgs_list(conn)
            elif headers.startswith(b"GET /bgs/get/"):
                slot = _parse_int_after(headers, b"/bgs/get/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_bg_get(conn, slot, headers)
            elif headers.startswith(b"POST /bgs/upload/"):
                slot = _parse_int_after(headers, b"/bgs/upload/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_bg_upload(conn, slot, headers, body_prefix)
            elif headers.startswith(b"POST /bgs/delete/"):
                slot = _parse_int_after(headers, b"/bgs/delete/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_bg_delete(conn, slot, headers)
            elif headers.startswith(b"POST /bgs/note/"):
                slot = _parse_int_after(headers, b"/bgs/note/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    _handle_bg_note(conn, slot, headers, body_prefix)

            # fonts
            elif headers.startswith(b"GET /fonts/list"):
                _handle_fonts_list(conn)
            elif headers.startswith(b"GET /fonts/get"):
                _handle_fonts_get(conn, headers)

            # upload / text
            elif headers.startswith(b"POST /upload"):
                n = _handle_upload(conn, body_prefix)
                _send(conn, b"200 OK", n)
            elif headers.startswith(b"POST /text/render"):
                _handle_text_render(conn, headers, body_prefix)
            elif headers.startswith(b"GET /text/sizes"):
                try:
                    from text_renderer import list_available_sizes
                    _send_json(conn, {"sizes": list_available_sizes()})
                except Exception as e:
                    _send_json(conn, {"sizes": [], "error": str(e)})

            elif b"GET /color/" in headers:
                name = headers.split(b"/color/")[1].split()[0].decode()
                val = COLOR_MAP.get(name)
                if val is None:
                    _send(conn, b"404 Not Found", b"no color")
                else:
                    _display_solid(val)
                    _send(conn, b"200 OK", b"OK")

            elif headers.startswith(b"GET /status"):
                _system_status(conn)
            elif headers.startswith(b"GET /wifi/scan"):
                _wifi_scan(conn)
            elif headers.startswith(b"GET /wifi/list"):
                _wifi_list(conn)
            elif headers.startswith(b"POST /wifi/save"):
                _wifi_save(conn, headers, body_prefix)
            elif headers.startswith(b"POST /wifi/delete"):
                _wifi_delete(conn, headers, body_prefix)
            elif headers.startswith(b"GET "):
                _serve_static(conn, headers)
            else:
                _send_file(conn, WWW_INDEX)

        except Exception as e:
            print("[WEB] error:", e)
            try:
                _send(conn, b"500 Internal Server Error", str(e))
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass