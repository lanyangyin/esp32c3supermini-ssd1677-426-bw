# web_server/handlers_sys.py — 处理系统、上传、字体、WiFi 相关的 HTTP 接口

import gc
import os
import json
import network
import epd

from .utils import (
    FRAME_BYTES,
    CHUNK,
    CORS_HEADERS,
    _send,
    _send_json,
    _send_file,
    _ensure_dir,
    _extract_query_param,
    _read_body,
)


# ============================================================
# 上传并直接显示
# ============================================================

def handle_upload(conn, body_prefix):
    """接收 192000 字节的 4bpp 数据，直接写入显存并刷新"""
    epd.init()
    received = 0

    # 获取底层缓存
    buf = epd._display.buffer

    if body_prefix:
        n = min(len(body_prefix), FRAME_BYTES)
        buf[received:received + n] = body_prefix[:n]
        received += n
        gc.collect()

    while received < FRAME_BYTES:
        chunk = conn.recv(min(CHUNK, FRAME_BYTES - received))
        if not chunk:
            break
        buf[received:received + len(chunk)] = chunk
        received += len(chunk)
        gc.collect()

    epd.show()
    epd.sleep()
    return received


# ============================================================
# 灰度测试
# ============================================================

def handle_gray_test(conn, headers):
    val_str = headers.split(b"/gray/")[1].split()[0].decode()
    try:
        val = int(val_str)
        if 0 <= val <= 15:
            epd.clear(val)
            _send(conn, b"200 OK", b"OK")
        else:
            _send(conn, b"400 Bad Request", b"out of range")
    except ValueError:
        _send(conn, b"404 Not Found", b"no gray")


# ============================================================
# 字体接口
# ============================================================

def handle_fonts_list(conn):
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
                                                       b"Access-Control-Allow-Origin: *\r\n"
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

    # 如果没有 index，现场扫描
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
            out.append({"family": fam, "size": size, "storage": storage, "chunk_count": cnt})
        del groups
        gc.collect()

    scan('/www/fonts', 'flash')
    try:
        os.stat("/sd")
        scan('/sd/fonts', 'sd')
    except OSError:
        pass

    gc.collect()
    out.sort(key=lambda x: (x['storage'] != 'flash', x['family'], x['size']))
    _send_json(conn, {"fonts": out})


def handle_fonts_get(conn, headers):
    storage = _extract_query_param(headers, 'storage') or 'flash'
    name = _extract_query_param(headers, 'name')

    if not name:
        _send(conn, b"400 Bad Request", b"missing name")
        return

    if '/' in name or '\\' in name or '..' in name:
        _send(conn, b"400 Bad Request", b"bad name")
        return

    if storage == 'sd':
        try:
            os.stat("/sd")
        except OSError:
            _send(conn, b"400 Bad Request", b"sd not mounted")
            return
        path = '/sd/fonts/' + name + '.bin'
    else:
        path = '/www/fonts/' + name + '.bin'

    _send_file(conn, path, content_type=b"application/octet-stream", cache_seconds=3600)


# ============================================================
# 系统状态 / WiFi
# ============================================================

def handle_system_status(conn):
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


def handle_wifi_scan(conn):
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


def handle_wifi_list(conn):
    try:
        import wifi_manager
        cfg = wifi_manager.load_config()
        _send_json(conn, cfg.get("known_networks", []))
    except Exception as e:
        _send(conn, b"500 Internal Server Error", str(e))


def handle_wifi_save(conn, headers, body_prefix):
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


def handle_wifi_delete(conn, headers, body_prefix):
    try:
        body = _read_body(conn, headers, body_prefix)
        data = json.loads(body)
        ssid = data["ssid"]

        import wifi_manager
        cfg = wifi_manager.load_config()
        cfg["known_networks"] = [n for n in cfg.get("known_networks", []) if n["ssid"] != ssid]
        wifi_manager.save_config(cfg)
        _send(conn, b"200 OK", b"OK")
    except Exception as e:
        _send(conn, b"500 Internal Server Error", str(e))