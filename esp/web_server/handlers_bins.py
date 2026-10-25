# web_server/handlers_bins.py — BIN 文件相关 HTTP 接口
import gc
import os
import json

from .utils import (
    FRAME_BYTES,
    _send,
    _send_json,
    _send_file,
    _send_thumb,
    _send_response_only,
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
    _parse_content_length,
)
import epd


def _norm_mode(raw):
    if raw in ('full', 'fast', 'partial'):
        return raw
    return 'full'


def _display_bin_file_phase1(path):
    """Phase 1：init + begin_frame + 所有数据写入 EPD。不刷新屏幕。

    返回 (ok, err, bytes_written)
    """
    try:
        size = os.stat(path)[6]
    except OSError as e:
        return (False, "file not found: " + str(e), 0)
    if size != FRAME_BYTES:
        return (False, "bad size %d (expected %d)" % (size, FRAME_BYTES), 0)

    try:
        epd.init()
        epd.begin_frame()
    except Exception as e:
        return (False, "init fail: " + str(e), 0)

    written = 0
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                epd.write_chunk(chunk)
                written += len(chunk)
                gc.collect()
    except Exception as e:
        return (False, "write fail: " + str(e), written)

    return (True, "", written)


def _display_bin_file_phase2(mode='full'):
    """Phase 2：end_frame(mode) + sleep。阻塞 4.2s（full）或 0.9s（fast/partial）。"""
    try:
        epd.end_frame(mode)
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
        "thumb_size": 3000,
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

    total_len = _parse_content_length(headers)
    path = bins_dir + "/" + str(slot) + ".bin"
    ok, received, err = _receive_to_file(conn, path, body_prefix, total_len=total_len)

    if not ok:
        _send(conn, b"507 Insufficient Storage", err.encode())
        return

    _remove_thumb_for(path)
    note = _extract_query_param(headers, "note")
    if note:
        _set_note(bins_dir + "/meta.json", slot, note)

    _send(conn, b"200 OK", str(received).encode())


def handle_bin_upload_and_display(conn, slot, headers, body_prefix):
    """上传并立即显示。

    query: ?mode=full | fast | partial（默认 full）
    """
    mode = _norm_mode(_extract_query_param(headers, 'mode'))
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

    total_len = _parse_content_length(headers)
    path = bins_dir + "/" + str(slot) + ".bin"
    ok, received, err = _receive_to_file(conn, path, body_prefix, total_len=total_len)

    if not ok:
        _send(conn, b"507 Insufficient Storage", err.encode())
        return

    _remove_thumb_for(path)
    note = _extract_query_param(headers, "note")
    if note:
        _set_note(bins_dir + "/meta.json", slot, note)

    # Phase 1：写入 EPD
    ok2, err2, written = _display_bin_file_phase1(path)
    if not ok2:
        _send(conn, b"500 Internal Server Error", ("display fail: " + err2).encode())
        return

    _send_response_only(conn, b"200 OK", str(received).encode())

    # Phase 2：刷新
    ok3, err3 = _display_bin_file_phase2(mode)
    if not ok3:
        print("[BIN] display finish error:", err3)


def handle_bin_display(conn, slot, headers):
    """显示已保存的 BIN。

    query: ?mode=full | fast | partial（默认 full）
    """
    mode = _norm_mode(_extract_query_param(headers, 'mode'))
    storage = _storage_from_query(headers, default='')
    p, _ = _resolve_slot_path('bins', storage, slot)
    if p is None:
        _send(conn, b"404 Not Found", b"not found")
        return

    try:
        conn.settimeout(120)
    except Exception:
        pass

    ok, err, written = _display_bin_file_phase1(p)
    if not ok:
        _send(conn, b"500 Internal Server Error", err.encode())
        return

    _send_response_only(conn, b"200 OK", b"OK")

    ok2, err2 = _display_bin_file_phase2(mode)
    if not ok2:
        print("[BIN] display finish error:", err2)


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