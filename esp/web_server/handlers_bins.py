# web_server/handlers_bins.py — 处理与 BIN 文件相关的 HTTP 接口

import gc
import os
import json

from .utils import (
    FRAME_BYTES,
    _send,
    _send_json,
    _send_file,
    _send_thumb,
    _ensure_dir,
    _get_storage_paths,
    _scan_all_slots,
    _resolve_slot_path,
    _storage_from_query,
    _receive_to_file,
    _set_note,
    _remove_thumb_for,
    _statvfs_free,
    _read_body,
    _extract_query_param,
)
import epd


def _display_bin_file(path):
    """读取 4bpp BIN 文件并流式显示到屏幕"""
    try:
        epd.init()
        epd.begin_frame()

        with open(path, "rb") as f:
            chunk_size = 4096
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                epd.write_chunk(chunk)
                gc.collect()

        epd.end_frame()
        epd.sleep()
        return (True, "")
    except Exception as e:
        try:
            epd.sleep()
        except Exception:
            pass
        return (False, str(e))


# ============================================================
# BIN 接口
# ============================================================

def handle_bins_list(conn):
    try:
        conn.settimeout(10)
    except Exception:
        pass

    sd_active = False
    try:
        os.listdir("/sd")
        sd_active = True
    except OSError:
        pass

    slots = _scan_all_slots('bins')
    free_sd = _statvfs_free("/sd") if sd_active else None
    free_flash = _statvfs_free('/')

    _send_json(conn, {
        "slots": slots,
        "total": len(slots),
        "slot_size": FRAME_BYTES,
        "thumb_size": 12000,  # 200x120 / 2
        "sd_active": sd_active,
        "free_sd": free_sd,
        "free_flash": free_flash,
    })


def handle_bin_get(conn, slot, headers):
    storage = _storage_from_query(headers, default='')
    p, _ = _resolve_slot_path('bins', storage, slot)
    if p is None:
        _send(conn, b"404 Not Found", b"not found")
        return
    _send_file(conn, p, content_type=b"application/octet-stream", cache_seconds=0)


def handle_bin_delete(conn, slot, headers):
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


def handle_bin_upload(conn, slot, headers, body_prefix):
    storage = _storage_from_query(headers, default='flash')
    bins_dir, _ = _get_storage_paths(storage)

    if storage == 'sd':
        try:
            os.stat("/sd")
        except OSError:
            _send(conn, b"400 Bad Request", b"sd not mounted")
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


def handle_bin_upload_and_display(conn, slot, headers, body_prefix):
    """上传并立即显示。"""
    # 先按普通逻辑上传并保存
    storage = _storage_from_query(headers, default='flash')
    bins_dir, _ = _get_storage_paths(storage)

    if storage == 'sd':
        try:
            os.stat("/sd")
        except OSError:
            _send(conn, b"400 Bad Request", b"sd not mounted")
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

    # 开始显示
    try:
        conn.settimeout(120)
    except Exception:
        pass

    ok2, err2 = _display_bin_file(path)
    if not ok2:
        _send(conn, b"500 Internal Server Error", ("display fail: " + err2).encode())
        return

    _send(conn, b"200 OK", str(received).encode())


def handle_bin_display(conn, slot, headers):
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


def handle_bin_note(conn, slot, headers, body_prefix):
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
            note = str(data.get("note", ""))[:200]
        except Exception:
            try:
                note = body.decode('utf-8')[:200]
            except Exception:
                note = ""
    except Exception:
        note = ""

    _set_note(meta, slot, note)
    _send(conn, b"200 OK", b"OK")