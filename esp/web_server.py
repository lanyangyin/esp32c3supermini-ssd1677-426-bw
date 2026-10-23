# web_server.py — 适配 SSD1677 黑白屏 (800x480, 1bpp)
import socket
import gc
import os
import json
import network
import epd
import time  # ★ 新增 time 用于 sleep

FRAME_BYTES = 800 * 480 // 8  # 48000 字节

CHUNK = 1024
WWW_INDEX = "/www/index.html"
WWW_CONVERT = "/www/converter.html"
WWW_WIFI = "/www/wifi.html"
WWW_TEXT = "/www/text_panel.html"
WWW_EDIT = "/www/bin_editor.html"
WWW_MOSAIC = "/www/mosaic.html"

# 缩略图尺寸 (1bpp)
THUMB_W = 400
THUMB_H = 240
THUMB_ROW_BYTES = THUMB_W // 8
THUMB_SIZE = THUMB_ROW_BYTES * THUMB_H

MAX_BINS_SOFT = 10000
MAX_BGS_SOFT = 10000
MAX_NOTE_LEN = 200

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

ROUTE_MAP = {
    "/": WWW_INDEX,
    "/index": WWW_INDEX,
    "/index.html": WWW_INDEX,
    "/convert": WWW_CONVERT,
    "/edit": WWW_EDIT,
    "/edit.html": WWW_EDIT,
    "/wifi": WWW_WIFI,
    "/text": WWW_TEXT,
    "/text.html": WWW_TEXT,
    "/mosaic": WWW_MOSAIC,
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
    """针对 1bpp (800x480) 生成 1bpp (400x240) 缩略图"""
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

    SRC_ROW_BYTES = 100  # 800 / 8
    try:
        # 逐行处理，每 2 行原图取 1 行，每 2 个像素取 1 个
        out_block = bytearray(THUMB_SIZE)
        with open(src_path, "rb") as f:
            for ty in range(THUMB_H):
                # 跳过错过的行
                f.seek((ty * 2) * SRC_ROW_BYTES)
                src_row = f.read(SRC_ROW_BYTES)
                if len(src_row) < SRC_ROW_BYTES:
                    break

                dst_row_off = ty * THUMB_ROW_BYTES
                for tx in range(THUMB_W):
                    # 取原图每 2 个像素的第一个
                    sx = tx * 2
                    byte_val = src_row[sx >> 3]
                    bit = (byte_val >> (7 - (sx & 7))) & 0x01
                    if bit:
                        out_block[dst_row_off + (tx >> 3)] |= (0x80 >> (tx & 7))

        mv = memoryview(out_block)
        off = 0
        while off < THUMB_SIZE:
            sent = conn.send(mv[off:])
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


def _handle_upload(conn, body_prefix):
    """接收 48000 字节黑白数据，直接写入 epd.buffer 并刷新"""
    received = 0
    buf = epd.buffer

    if body_prefix:
        n = min(len(body_prefix), FRAME_BYTES)
        buf[:n] = body_prefix[:n]
        received += n

    while received < FRAME_BYTES:
        chunk = conn.recv(min(CHUNK, FRAME_BYTES - received))
        if not chunk:
            break
        buf[received:received + len(chunk)] = chunk
        received += len(chunk)
        gc.collect()

    if received == FRAME_BYTES:
        epd.show()
        return received
    return 0


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
        try:
            f.close()
        except:
            pass
        try:
            os.remove(p)
        except:
            pass
        _send(conn, b"500 Internal Server Error", str(e).encode())
        return

    if received != THUMB_SIZE:
        try:
            os.remove(p)
        except:
            pass
        _send(conn, b"400 Bad Request",
              ("expected %d, got %d" % (THUMB_SIZE, received)).encode())
        return

    _send(conn, b"200 OK", str(received).encode())


def _remove_thumb_for(bin_path):
    if not bin_path or not bin_path.endswith('.bin'):
        return
    try:
        os.remove(bin_path[:-4] + '.thumb')
    except OSError:
        pass


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
    _remove_thumb_for(p)
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
        with open(path, "rb") as f:
            data = f.read(FRAME_BYTES)
            if len(data) == FRAME_BYTES:
                epd.buffer[:] = data
                epd.show()
                return (True, "")
            return (False, "size mismatch")
    except Exception as e:
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
    _remove_thumb_for(path)
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
        scan(sd_helper.SD_MOUNT + '/fonts', 'sd')
    gc.collect()
    out.sort(key=lambda x: (x['storage'] != 'flash', x['family'], x['size']))
    _send_json(conn, {"fonts": out})


# ★ 新增：API 触发重建字体索引
def _handle_fonts_rebuild(conn):
    print("[WEB] /fonts/rebuild 触发重建字体索引...")
    try:
        import build_font_index
        ok = build_font_index.build(verbose=False)
        if ok:
            try:
                st = os.stat('/fonts_index.json')
                _send_json(conn, {"ok": True, "size": st[6]})
            except OSError:
                _send_json(conn, {"ok": True})
        else:
            _send_json(conn, {"ok": False, "error": "build_font_index.build() failed"},
                       b"500 Internal Server Error")
    except Exception as e:
        print("[WEB] fonts rebuild error:", e)
        _send_json(conn, {"ok": False, "error": str(e)},
                   b"500 Internal Server Error")


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
        path = sd_helper.SD_MOUNT + '/fonts/' + name + '.bin'
    else:
        path = '/www/fonts/' + name + '.bin'
    _send_file(conn, path,
               content_type=b"application/octet-stream",
               cache_seconds=3600)


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

        from text_renderer import render_and_display_bw

        size = int(params.get("size", 32))
        font_paths = params.get("font_paths") or None
        per_char = params.get("per_char")
        text = params.get("text", "")

        try:
            conn.settimeout(300)
        except Exception:
            pass

        # 先返回响应，避免网页端长时间等待
        _send_json(conn, {"ok": True, "mode": "stream", "started": True})

        try:
            render_and_display_bw(
                epd, text,
                size=size,
                font_paths=font_paths,
                per_char=per_char,
                **{k: v for k, v in params.items() if k not in ('text', 'size', 'font_paths', 'per_char')}
            )
        except Exception as render_err:
            print("[TEXT] deferred render fail:", render_err)
            try:
                import sys as _sys
                _sys.print_exception(render_err)
            except Exception:
                pass
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
    finally:
        _gc.collect()


def _system_status(conn):
    info = {
        "ap": {"active": False, "ip": None, "ssid": None},
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

    # ★ 检查字体索引，若缺失则自动构建
    try:
        st = os.stat('/fonts_index.json')
        print('[WEB] /fonts_index.json: %d 字节' % st[6])
    except OSError:
        print('[WEB] /fonts_index.json 不存在，正在自动构建（可能需要几秒钟）...')
        try:
            import build_font_index
            build_font_index.build(verbose=False)
            st = os.stat('/fonts_index.json')
            print('[WEB] 自动构建完成: %d 字节' % st[6])
        except Exception as e:
            print('[WEB] 自动构建失败:', e)

    # 等待一下，确保网络接口彻底就绪（构建过程可能打断了接口状态）
    time.sleep(1)

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # ★ 增加 bind 重试机制
    bound = False
    for attempt in range(10):
        try:
            s.bind(addr)
            bound = True
            break
        except OSError as e:
            print("[WEB] bind attempt %d failed: %s" % (attempt + 1, e))
            time.sleep_ms(500)
            # 如果 socket 彻底损坏，尝试重建
            try:
                s.close()
            except Exception:
                pass
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    if not bound:
        raise OSError("无法绑定 80 端口，请检查网络状态或重启设备")

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

            # thumb upload
            if headers.startswith(b"POST /bins/thumb_upload/"):
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
            elif headers.startswith(b"GET /fonts/rebuild") or headers.startswith(b"POST /fonts/rebuild"):
                _handle_fonts_rebuild(conn)  # ★ 挂载新 API
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