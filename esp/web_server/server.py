# web_server/server.py — HTTP 监听、路由分发、静态文件服务

import socket
import gc
import os
import network

from .utils import (
    _ensure_dir,
    _send,
    _send_file,
    _read_request,
    _send_json,
    _parse_int_after,
    _extract_query_param,
    _storage_from_query,
    _resolve_slot_path,
    _send_thumb,
    _read_body,
    _set_note,
    BOOT_CFG_PATH,
)

from .handlers_bins import (
    handle_bins_list, handle_bin_get, handle_bin_delete,
    handle_bin_upload, handle_bin_upload_and_display,
    handle_bin_display, handle_bin_note,
)
from .handlers_bgs import (
    handle_bgs_list, handle_bg_get, handle_bg_delete,
    handle_bg_upload, handle_bg_note,
)
from .handlers_sys import (
    handle_upload, handle_gray_test, handle_fonts_list, handle_fonts_get,
    handle_system_status, handle_wifi_scan, handle_wifi_list,
    handle_wifi_save, handle_wifi_delete,
)
from .handlers_text import handle_text_render

# ---------- 静态文件路由表 ----------
WWW_INDEX = "/www/index.html"
WWW_CONVERT = "/www/converter.html"
WWW_WIFI = "/www/wifi.html"
WWW_TEXT = "/www/text_panel.html"
WWW_EDIT = "/www/bin_editor.html"
WWW_MOSAIC = "/www/mosaic.html"

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


# ============================================================
# 开机指定 BIN 专属接口
# ============================================================

def _handle_boot_bin_get(conn):
    try:
        import json
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
        import json
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


# ============================================================
# 静态文件服务
# ============================================================

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


# ============================================================
# 主服务启动
# ============================================================

def start_server():
    # 确保 Flash 上的基础目录存在
    for d in ("/bins", "/bgs"):
        _ensure_dir(d)

    # 安全检测：如果用户挂载了 SD 卡，才去创建 SD 内的目录
    try:
        os.stat("/sd")
        for d in ("/sd/bins", "/sd/bgs"):
            _ensure_dir(d)
        print("[WEB] storage: SD + Flash")
    except OSError:
        print("[WEB] storage: Flash only")

    # 检查字体索引
    try:
        st = os.stat('/fonts_index.json')
        print('[WEB] /fonts_index.json: %d 字节' % st[6])
    except OSError:
        print('[WEB] 警告: /fonts_index.json 不存在，请执行')
        print('import build_font_index')
        print('build_font_index.build()')

    # 绑定端口
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(5)

    # 打印网络信息
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

    # 主循环
    while True:
        conn, _ = s.accept()
        try:
            headers, body_prefix = _read_request(conn)

            # 处理跨域预检请求
            if headers.startswith(b"OPTIONS "):
                from .utils import CORS_HEADERS
                conn.send(
                    b"HTTP/1.1 200 OK\r\n"
                    + CORS_HEADERS +
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
                continue

            # ---------------- 路由分发 ----------------

            # 开机指定 BIN
            if headers.startswith(b"GET /boot/bin"):
                _handle_boot_bin_get(conn)
            elif headers.startswith(b"POST /boot/bin"):
                _handle_boot_bin_set(conn, headers, body_prefix)

            # 缩略图上传（必须放在 /bins/upload 之前）
            elif headers.startswith(b"POST /bins/thumb_upload/"):
                slot = _parse_int_after(headers, b"/bins/thumb_upload/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    from .utils import _handle_thumb_upload
                    _handle_thumb_upload(conn, 'bins', slot, headers, body_prefix)
            elif headers.startswith(b"POST /bgs/thumb_upload/"):
                slot = _parse_int_after(headers, b"/bgs/thumb_upload/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    from .utils import _handle_thumb_upload
                    _handle_thumb_upload(conn, 'bgs', slot, headers, body_prefix)

            # 缩略图读取
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

            # BIN 接口
            elif headers.startswith(b"GET /bins/list"):
                handle_bins_list(conn)
            elif headers.startswith(b"GET /bins/get/"):
                slot = _parse_int_after(headers, b"/bins/get/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    handle_bin_get(conn, slot, headers)
            elif headers.startswith(b"POST /bins/upload_and_display/"):
                slot = _parse_int_after(headers, b"/bins/upload_and_display/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    handle_bin_upload_and_display(conn, slot, headers, body_prefix)
            elif headers.startswith(b"POST /bins/upload/"):
                slot = _parse_int_after(headers, b"/bins/upload/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    handle_bin_upload(conn, slot, headers, body_prefix)
            elif headers.startswith(b"POST /bins/delete/"):
                slot = _parse_int_after(headers, b"/bins/delete/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    handle_bin_delete(conn, slot, headers)
            elif headers.startswith(b"POST /bins/note/"):
                slot = _parse_int_after(headers, b"/bins/note/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    handle_bin_note(conn, slot, headers, body_prefix)
            elif headers.startswith(b"GET /bins/display/"):
                slot = _parse_int_after(headers, b"/bins/display/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    handle_bin_display(conn, slot, headers)

            # BGS 接口
            elif headers.startswith(b"GET /bgs/list"):
                handle_bgs_list(conn)
            elif headers.startswith(b"GET /bgs/get/"):
                slot = _parse_int_after(headers, b"/bgs/get/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    handle_bg_get(conn, slot, headers)
            elif headers.startswith(b"POST /bgs/upload/"):
                slot = _parse_int_after(headers, b"/bgs/upload/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    handle_bg_upload(conn, slot, headers, body_prefix)
            elif headers.startswith(b"POST /bgs/delete/"):
                slot = _parse_int_after(headers, b"/bgs/delete/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    handle_bg_delete(conn, slot, headers)
            elif headers.startswith(b"POST /bgs/note/"):
                slot = _parse_int_after(headers, b"/bgs/note/")
                if slot is None:
                    _send(conn, b"400 Bad Request", b"bad slot")
                else:
                    handle_bg_note(conn, slot, headers, body_prefix)

            # 字体接口
            elif headers.startswith(b"GET /fonts/list"):
                handle_fonts_list(conn)
            elif headers.startswith(b"GET /fonts/get"):
                handle_fonts_get(conn, headers)

            # 系统接口
            elif headers.startswith(b"POST /upload"):
                n = handle_upload(conn, body_prefix)
                _send(conn, b"200 OK", n)
            elif headers.startswith(b"POST /text/render"):
                handle_text_render(conn, headers, body_prefix)
            elif b"GET /gray/" in headers:
                handle_gray_test(conn, headers)
            elif headers.startswith(b"GET /status"):
                handle_system_status(conn)
            elif headers.startswith(b"GET /wifi/scan"):
                handle_wifi_scan(conn)
            elif headers.startswith(b"GET /wifi/list"):
                handle_wifi_list(conn)
            elif headers.startswith(b"POST /wifi/save"):
                handle_wifi_save(conn, headers, body_prefix)
            elif headers.startswith(b"POST /wifi/delete"):
                handle_wifi_delete(conn, headers, body_prefix)

            # 静态文件兜底
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