# web_server/handlers_sys.py — 系统、上传、字体、WiFi 相关
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
    _send_response_only,
    _ensure_dir,
    _extract_query_param,
    _read_body,
    _parse_content_length,
)

_BAYER4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)

_gray_row_tpl_cache = {}


def _gray_row_templates(val):
    tpl = _gray_row_tpl_cache.get(val)
    if tpl is not None:
        return tpl
    v255 = val * 17
    rows = []
    for y in range(4):
        row = bytearray(100)
        for bi in range(100):
            bv = 0
            base_x = bi * 8
            for bit in range(8):
                thr = _BAYER4[y][(base_x + bit) & 3] * 16 + 8
                if v255 > thr:
                    bv |= 0x80 >> bit
            row[bi] = bv
        rows.append(bytes(row))
    _gray_row_tpl_cache[val] = rows
    return rows


def _norm_mode(raw):
    if raw in ('full', 'fast', 'partial', 'partial_multi'):
        return raw
    return 'full'


# ============================================================
# 上传并直接显示（含全刷、单矩形局刷、多矩形局刷）
# ============================================================

def handle_upload(conn, headers, body_prefix):
    mode = _norm_mode(_extract_query_param(headers, 'mode'))

    total_len = _parse_content_length(headers)
    if total_len <= 0:
        _send(conn, b"400 Bad Request", b"empty body")
        return

    try:
        conn.settimeout(30)
    except Exception:
        pass

    try:
        epd.init()

        if mode == 'partial_multi':
            # 多矩形局刷
            buffer = bytearray(total_len)
            received = 0
            if body_prefix:
                n = len(body_prefix)
                buffer[:n] = body_prefix
                received = n
            while received < total_len:
                chunk = conn.recv(min(CHUNK, total_len - received))
                if not chunk: break
                buffer[received:received + len(chunk)] = chunk
                received += len(chunk)
                gc.collect()

            if received < total_len:
                raise OSError("receive incomplete")

            idx = 0
            num_rects = (buffer[idx] << 8) | buffer[idx + 1];
            idx += 2
            data_len = (buffer[idx] << 8) | buffer[idx + 1];
            idx += 2

            epd._display.start_partial_multi()
            data_offset = idx + num_rects * 8

            for i in range(num_rects):
                x0 = (buffer[idx] << 8) | buffer[idx + 1];
                idx += 2
                y0 = (buffer[idx] << 8) | buffer[idx + 1];
                idx += 2
                x1 = (buffer[idx] << 8) | buffer[idx + 1];
                idx += 2
                y1 = (buffer[idx] << 8) | buffer[idx + 1];
                idx += 2

                aligned_x0 = x0 & ~7
                aligned_x1 = (x1 | 7)
                if aligned_x1 >= 800: aligned_x1 = 799
                w = aligned_x1 - aligned_x0 + 1
                h = y1 - y0 + 1
                row_bytes = w >> 3
                rect_len = row_bytes * h

                rect_data = buffer[data_offset: data_offset + rect_len]
                data_offset += rect_len

                # 核心修复：计算真实 Y 轴坐标
                real_y0 = 479 - y1
                real_y1 = 479 - y0
                epd._display.write_partial_rect(aligned_x0, real_y0, aligned_x1, real_y1, rect_data)

            epd._display.end_partial_multi()
            epd.sleep()
            _send_response_only(conn, b"200 OK", b"OK")
            return

        # ===== 单矩形或全屏 =====
        x0_str = _extract_query_param(headers, 'x0')
        y0_str = _extract_query_param(headers, 'y0')
        x1_str = _extract_query_param(headers, 'x1')
        y1_str = _extract_query_param(headers, 'y1')

        is_partial = False
        if mode == 'partial' and x0_str and y0_str and x1_str and y1_str:
            try:
                x0 = int(x0_str)
                y0 = int(y0_str)
                x1 = int(x1_str)
                y1 = int(y1_str)
                is_partial = True
            except ValueError:
                pass

        epd_written = 0
        if is_partial:
            x0 = x0 & ~7
            x1 = (x1 | 7)
            if x1 >= 800: x1 = 799
            if y0 < 0: y0 = 0
            if y1 >= 480: y1 = 479

            w = x1 - x0 + 1
            h = y1 - y0 + 1
            need = (w >> 3) * h

            # 核心修复：计算真实 Y 轴坐标
            real_y0 = 479 - y1
            real_y1 = 479 - y0
            epd._display.begin_frame_partial(x0, real_y0, x1, real_y1)
        else:
            epd.begin_frame()
            need = min(total_len, FRAME_BYTES)

        try:
            if body_prefix:
                n = min(len(body_prefix), need)
                if n > 0:
                    epd.write_chunk(body_prefix[:n])
                    epd_written = n
                    gc.collect()

            while epd_written < need:
                want = min(CHUNK, need - epd_written)
                chunk = conn.recv(want)
                if not chunk:
                    break
                epd.write_chunk(chunk)
                epd_written += len(chunk)
                gc.collect()
        except Exception as e:
            print("[UPLOAD] write error:", e)

        if is_partial:
            epd._display.cs(1)
            epd._display._cmd(0x22, 0xFF)
            epd._display._cmd(0x20)
            epd._display._busy_wait()
            epd._display._cmd(0x90)

        # 排空剩余
        prefix_len = len(body_prefix) if body_prefix else 0
        remaining = total_len - prefix_len
        already_from_conn = epd_written - min(prefix_len, need)
        drain_need = remaining - already_from_conn
        if drain_need > 0:
            try:
                while drain_need > 0:
                    chunk = conn.recv(min(CHUNK, drain_need))
                    if not chunk: break
                    drain_need -= len(chunk)
                    gc.collect()
            except Exception:
                pass

        _send_response_only(conn, b"200 OK", str(epd_written).encode())

        if not is_partial:
            try:
                epd.end_frame(mode)
            except Exception:
                pass
        try:
            epd.sleep()
        except Exception:
            pass

    except Exception as e:
        print("[UPLOAD] fatal:", e)
        _send_response_only(conn, b"500 Internal Server Error", str(e).encode())
        try:
            epd.sleep()
        except:
            pass


# ============================================================
# 去除残影 (全屏刷白)
# ============================================================

def handle_clear_display(conn, headers):
    mode = _norm_mode(_extract_query_param(headers, 'mode'))
    try:
        conn.settimeout(30)
    except Exception:
        pass

    try:
        epd.init()
        epd.begin_frame()
        row = b'\xFF' * 100
        for _ in range(480):
            epd.write_chunk(row)
        epd.end_frame(mode)
        epd.sleep()
        _send(conn, b"200 OK", b"OK")
    except Exception as e:
        print("[CLEAR] error:", e)
        try:
            epd.sleep()
        except Exception:
            pass
        _send(conn, b"500 Internal Server Error", str(e).encode())


# ============================================================
# 灰度测试
# ============================================================

def handle_gray_test(conn, headers):
    try:
        val_str = headers.split(b"/gray/")[1].split()[0].decode()
        val = int(val_str)
    except (ValueError, IndexError):
        _send(conn, b"404 Not Found", b"no gray")
        return

    if not (0 <= val <= 15):
        _send(conn, b"400 Bad Request", b"out of range")
        return

    mode = _norm_mode(_extract_query_param(headers, 'mode'))

    err = None
    try:
        rows = _gray_row_templates(val)
        epd.init()
        epd.begin_frame()
        try:
            for y in range(480):
                epd.write_chunk(rows[y & 3])
        except Exception as e:
            err = str(e)
            print("[GRAY] write error:", e)
    except Exception as e:
        err = str(e)
        print("[GRAY] init error:", e)

    if err:
        _send_response_only(conn, b"500 Internal Server Error", err.encode())
    else:
        _send_response_only(conn, b"200 OK", b"OK")

    try:
        epd.end_frame(mode)
    except Exception as e:
        print("[GRAY] end_frame error:", e)
    try:
        epd.sleep()
    except Exception:
        pass


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


def handle_wifi_connect(conn, headers, body_prefix):
    try:
        body = _read_body(conn, headers, body_prefix)
        data = json.loads(body) if body else {}
        ssid = (data.get("ssid") or "").strip()
        if not ssid:
            _send_json(conn, {"ok": False, "connected": False, "error": "ssid empty"},
                       b"400 Bad Request")
            return

        import wifi_manager
        cfg = wifi_manager.load_config()
        pwd = None
        for n in cfg.get("known_networks", []):
            if n["ssid"] == ssid:
                pwd = n.get("password", "")
                break
        if pwd is None:
            _send_json(conn, {"ok": False, "connected": False, "error": "not found"},
                       b"404 Not Found")
            return

        print("[WEB] 正在连接:", ssid)
        try:
            conn.settimeout(120)
        except Exception:
            pass

        ok = wifi_manager.connect_sta(ssid, pwd, timeout=20, retries=2)

        if ok:
            ip = "0.0.0.0"
            try:
                ip = network.WLAN(network.STA_IF).ifconfig()[0]
            except Exception:
                pass
            print("[WEB] 连接成功:", ssid, ip)
            _send_json(conn, {"ok": True, "connected": True, "ip": ip})
        else:
            print("[WEB] 连接失败:", ssid)
            _send_json(conn, {"ok": False, "connected": False, "error": "timeout"})
    except Exception as e:
        print("[WEB] wifi_connect error:", e)
        try:
            _send_json(conn, {"ok": False, "connected": False, "error": str(e)},
                       b"500 Internal Server Error")
        except Exception:
            pass