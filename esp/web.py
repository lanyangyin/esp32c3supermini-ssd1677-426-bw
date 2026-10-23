# web.py — HTTP 服务器（流式上传 + 阅读器控制 + 网络管理）
import socket
import os
import json
import gc
import reader
import wifi_manager
import storage_state


WWW_ROOT = '/www'

MAX_UPLOAD_SIZE = 200_000
MAX_BODY_SMALL = 8192


# ---------- URL 工具 ----------

def _url_decode(raw):
    out = []
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == '%' and i + 2 < n:
            try:
                out.append(chr(int(raw[i+1:i+3], 16)))
                i += 3
                continue
            except ValueError:
                pass
        elif c == '+':
            out.append(' ')
            i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def _parse_query(query, key):
    prefix = key + '='
    for kv in query.split('&'):
        if kv.startswith(prefix):
            return _url_decode(kv[len(prefix):])
    return None


# ---------- 响应工具 ----------

def _content_type(path):
    if path.endswith('.html') or path.endswith('.htm'):
        return b'text/html; charset=utf-8'
    if path.endswith('.js'):
        return b'application/javascript; charset=utf-8'
    if path.endswith('.css'):
        return b'text/css; charset=utf-8'
    if path.endswith('.json'):
        return b'application/json; charset=utf-8'
    return b'application/octet-stream'


def _send_static(conn, path):
    try:
        size = os.stat(path)[6]
    except OSError:
        _send_text(conn, b'404 Not Found', b'not found')
        return
    ctype = _content_type(path)
    conn.send(b'HTTP/1.1 200 OK\r\n')
    conn.send(b'Content-Type: ' + ctype + b'\r\n')
    conn.send(b'Content-Length: ' + str(size).encode() + b'\r\n')
    conn.send(b'Cache-Control: no-store\r\n')
    conn.send(b'Connection: close\r\n\r\n')
    try:
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(512)
                if not chunk:
                    break
                conn.send(chunk)
    except Exception as e:
        print('[WEB] 静态发送失败:', e)


def _send_json(conn, data, status=b'200 OK'):
    body = json.dumps(data).encode()
    conn.send(b'HTTP/1.1 ' + status + b'\r\n')
    conn.send(b'Content-Type: application/json; charset=utf-8\r\n')
    conn.send(b'Content-Length: ' + str(len(body)).encode() + b'\r\n')
    conn.send(b'Cache-Control: no-store\r\n')
    conn.send(b'Connection: close\r\n\r\n')
    conn.send(body)


def _send_text(conn, status, body):
    if isinstance(body, str):
        body = body.encode()
    conn.send(b'HTTP/1.1 ' + status + b'\r\n')
    conn.send(b'Content-Type: text/plain; charset=utf-8\r\n')
    conn.send(b'Content-Length: ' + str(len(body)).encode() + b'\r\n')
    conn.send(b'Connection: close\r\n\r\n')
    conn.send(body)


def _redirect(conn, to='/'):
    conn.send(b'HTTP/1.1 302 Found\r\n')
    conn.send(b'Location: ' + to.encode() + b'\r\n')
    conn.send(b'Content-Length: 0\r\n')
    conn.send(b'Connection: close\r\n\r\n')


# ---------- 请求头读取 ----------

def _read_headers(conn):
    """只读 headers 和已读到的部分 body，不预读 body。
    返回 (headers_bytes, body_start_bytes) 或 (None, b'')。"""
    buf = b''
    while b'\r\n\r\n' not in buf:
        chunk = conn.recv(512)
        if not chunk:
            return None, b''
        buf += chunk
        if len(buf) > 8192:
            return None, b''
    idx = buf.find(b'\r\n\r\n')
    return buf[:idx], buf[idx + 4:]


def _parse_content_length(headers):
    for line in headers.split(b'\r\n'):
        if line.lower().startswith(b'content-length:'):
            try:
                return int(line.split(b':', 1)[1].strip())
            except ValueError:
                return 0
    return 0


def _read_body_small(conn, headers, body_start, max_size=MAX_BODY_SMALL):
    """读小 body（JSON 之类）。超 max_size 返回 None。"""
    cl = _parse_content_length(headers)
    if cl > max_size:
        return None
    body = body_start
    while len(body) < cl:
        chunk = conn.recv(min(1024, cl - len(body)))
        if not chunk:
            break
        body += chunk
    return body


# ---------- 阅读器 API ----------

def _handle_api_books(conn):
    book = reader.get_book()
    if book is None:
        current = None
        active_key = None
    else:
        current = {
            'title': book.title,
            'page': book.page_idx + 1,
            'total': book.page_count(),
            'progress': round(book.progress() * 100, 1),
            'storage': book.storage,
        }
        active_key = book.key()

    _send_json(conn, {
        'current': current,
        'active_key': active_key,
        'books': reader.list_books(),
        'ext_available': reader.is_ext_available(),
        'ext_type': storage_state.EXT_TYPE,
    })


def _handle_open(conn, query):
    name = _parse_query(query, 'name')
    storage = _parse_query(query, 'storage')
    if not name or '..' in name or '/' in name or '\\' in name:
        _redirect(conn)
        return
    if storage not in ('ext', 'int'):
        storage = None
    book = reader.load_book(name, storage)
    if book:
        reader.set_book(book)
        reader.render(full=True)
        print('[WEB] 已打开:', name, 'from', book.storage)
    _redirect(conn)


def _handle_delete_book(conn, query):
    name = _parse_query(query, 'name')
    storage = _parse_query(query, 'storage')
    if not name or '..' in name or '/' in name or '\\' in name:
        _redirect(conn)
        return
    if storage not in ('ext', 'int'):
        storage = 'int'
    if reader.delete_book(name, storage):
        print('[WEB] 已删除:', storage, name)
    _redirect(conn)


# ---------- 流式上传 ----------

def _find_multipart_bounds(tmp_path, boundary):
    """从临时 body 文件里找第一个文件内容的 [start, end)。峰值内存 < 2 KB。"""
    closing = b'\r\n--' + boundary

    with open(tmp_path, 'rb') as f:
        head = f.read(4096)
        k = head.find(b'\r\n\r\n')
        if k < 0:
            return -1, -1, None
        content_start = k + 4

        # 提取 filename
        i = head.find(b'filename="')
        if i < 0:
            return -1, -1, None
        i += 10
        j = head.find(b'"', i)
        if j < 0:
            return -1, -1, None
        try:
            filename = head[i:j].decode('utf-8', 'ignore')
        except Exception:
            filename = 'upload.txt'

        # 流式扫描 closing
        f.seek(content_start)
        buf = bytearray(512)
        window = bytearray()
        pos = content_start
        keep = len(closing) - 1

        while True:
            n = f.readinto(buf)
            if n == 0:
                return -1, -1, None
            window.extend(buf[:n])
            idx = window.find(closing)
            if idx >= 0:
                content_end = pos + idx
                return content_start, content_end, filename
            if len(window) > keep:
                removed = len(window) - keep
                del window[:removed]
                pos += removed


def _handle_upload(conn, headers, body_start):
    cl = _parse_content_length(headers)
    if cl == 0:
        _send_text(conn, b'400 Bad Request', b'empty body')
        return
    if cl > MAX_UPLOAD_SIZE:
        print('[WEB] 上传太大: %d' % cl)
        _send_text(conn, b'413 Payload Too Large',
                   ('file too large: %d, max %d'
                    % (cl, MAX_UPLOAD_SIZE)).encode())
        return

    # 提取 boundary
    boundary = None
    for line in headers.split(b'\r\n'):
        if b'boundary=' in line:
            b = line.split(b'boundary=', 1)[1].strip()
            if b.startswith(b'"') and b.endswith(b'"'):
                b = b[1:-1]
            boundary = b
            break
    if not boundary:
        _send_text(conn, b'400 Bad Request', b'no boundary')
        return

    # 临时 body 文件
    if reader.is_ext_available():
        tmp_body = '/ext/_upload.tmp'
    else:
        tmp_body = '/_upload.tmp'
    try:
        os.remove(tmp_body)
    except OSError:
        pass

    # 流式接收
    print('[WEB] 接收上传 %d 字节…' % cl)
    try:
        with open(tmp_body, 'wb') as f:
            if body_start:
                f.write(body_start)
            received = len(body_start)
            while received < cl:
                chunk = conn.recv(min(1024, cl - received))
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
    except OSError as e:
        print('[WEB] 接收失败:', e)
        try:
            os.remove(tmp_body)
        except OSError:
            pass
        _send_text(conn, b'500 Internal Server Error', str(e).encode())
        return

    # 找 multipart 边界
    try:
        content_start, content_end, filename = _find_multipart_bounds(
            tmp_body, boundary)
    except Exception as e:
        print('[WEB] 解析失败:', e)
        content_start, content_end, filename = -1, -1, None

    if content_start < 0 or content_end <= content_start or not filename:
        try:
            os.remove(tmp_body)
        except OSError:
            pass
        _send_text(conn, b'400 Bad Request', b'parse failed')
        return

    size = content_end - content_start
    if size > MAX_UPLOAD_SIZE:
        try:
            os.remove(tmp_body)
        except OSError:
            pass
        _send_text(conn, b'413 Payload Too Large', b'content too large')
        return

    # 直接写到目标
    safe_name = reader._safe_filename(filename)
    if reader.is_ext_available():
        storage = 'ext'
        target_dir = reader.STORAGE_EXT
    else:
        storage = 'int'
        target_dir = reader.STORAGE_INT
    reader._ensure_dirs()
    target_path = target_dir + '/' + safe_name

    try:
        os.remove(target_path)
    except OSError:
        pass

    try:
        with open(tmp_body, 'rb') as src, open(target_path, 'wb') as dst:
            src.seek(content_start)
            remaining = size
            while remaining > 0:
                n = min(1024, remaining)
                chunk = src.read(n)
                if not chunk:
                    break
                dst.write(chunk)
                remaining -= len(chunk)
    except OSError as e:
        print('[WEB] 写入失败:', e)
        try:
            os.remove(tmp_body)
        except OSError:
            pass
        try:
            os.remove(target_path)
        except OSError:
            pass
        _send_text(conn, b'500 Internal Server Error', str(e).encode())
        return

    try:
        os.remove(tmp_body)
    except OSError:
        pass

    gc.collect()
    print('[WEB] 已保存 %s/%s (%d 字节), free=%d'
          % (storage, safe_name, size, gc.mem_free()))

    # 自动打开
    book = reader.load_book(safe_name, storage)
    if book:
        reader.set_book(book)
        gc.collect()
        if gc.mem_free() > 20_000:
            reader.render(full=True)
        else:
            print('[WEB] 内存紧张，跳过首次渲染，free=%d' % gc.mem_free())

    _redirect(conn)


# ---------- 网络 API ----------

def _handle_status(conn):
    ap_ssid, ap_pwd, ap_ip = wifi_manager.get_ap_info()
    sta_ssid, sta_ip = wifi_manager.get_sta_info()
    sta_rssi = None
    try:
        import network
        w = network.WLAN(network.STA_IF)
        if w.isconnected():
            try:
                sta_rssi = w.status('rssi')
            except Exception:
                pass
    except Exception:
        pass
    _send_json(conn, {
        'ap': {'ssid': ap_ssid, 'ip': ap_ip, 'active': True},
        'sta': {
            'ssid': sta_ssid, 'ip': sta_ip,
            'connected': sta_ssid != '(not connected)',
            'rssi': sta_rssi,
        },
    })


def _handle_wifi_scan(conn):
    try:
        nets = wifi_manager.scan_networks()
        _send_json(conn, [{'ssid': s, 'rssi': r, 'auth': a} for s, r, a in nets])
    except Exception as e:
        _send_json(conn, {'error': str(e)}, b'500 Internal Server Error')


def _handle_wifi_list(conn):
    cfg = wifi_manager.load_config()
    _send_json(conn, cfg.get('known_networks', []))


def _parse_json(body):
    if body is None:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def _handle_wifi_save(conn, headers, body_start):
    body = _read_body_small(conn, headers, body_start)
    data = _parse_json(body)
    if data is None:
        _send_json(conn, {'error': 'invalid json'}, b'400 Bad Request')
        return
    ssid = (data.get('ssid') or '').strip()
    if not ssid:
        _send_json(conn, {'error': 'ssid empty'}, b'400 Bad Request')
        return
    pwd = data.get('password', '')
    try:
        priority = int(data.get('priority', 1))
    except (ValueError, TypeError):
        priority = 1
    cfg = wifi_manager.load_config()
    nets = cfg.get('known_networks', [])
    found = False
    for n in nets:
        if n['ssid'] == ssid:
            n['password'] = pwd
            n['priority'] = priority
            found = True
            break
    if not found:
        nets.append({'ssid': ssid, 'password': pwd, 'priority': priority})
    cfg['known_networks'] = nets
    wifi_manager.save_config(cfg)
    print('[WEB] WiFi 已保存:', ssid)
    _send_json(conn, {'ok': True})


def _handle_wifi_delete(conn, headers, body_start):
    body = _read_body_small(conn, headers, body_start)
    data = _parse_json(body)
    if data is None:
        _send_json(conn, {'error': 'invalid json'}, b'400 Bad Request')
        return
    ssid = data.get('ssid', '')
    cfg = wifi_manager.load_config()
    cfg['known_networks'] = [
        n for n in cfg.get('known_networks', []) if n['ssid'] != ssid
    ]
    wifi_manager.save_config(cfg)
    print('[WEB] WiFi 已删除:', ssid)
    _send_json(conn, {'ok': True})


def _handle_wifi_connect(conn, headers, body_start):
    body = _read_body_small(conn, headers, body_start)
    data = _parse_json(body)
    if data is None:
        _send_json(conn, {'error': 'invalid json'}, b'400 Bad Request')
        return
    ssid = (data.get('ssid') or '').strip()
    if not ssid:
        _send_json(conn, {'error': 'ssid empty'}, b'400 Bad Request')
        return

    cfg = wifi_manager.load_config()
    pwd = None
    for n in cfg.get('known_networks', []):
        if n['ssid'] == ssid:
            pwd = n.get('password', '')
            break
    if pwd is None:
        _send_json(conn, {'error': 'not found'}, b'404 Not Found')
        return

    print('[WEB] 正在连接:', ssid)
    ok = wifi_manager.connect_sta(ssid, pwd, timeout=15, retries=1)

    if ok:
        ip = '0.0.0.0'
        try:
            import network
            ip = network.WLAN(network.STA_IF).ifconfig()[0]
        except Exception:
            pass
        print('[WEB] 连接成功:', ssid, ip)
        _send_json(conn, {'ok': True, 'connected': True, 'ip': ip})
    else:
        print('[WEB] 连接失败:', ssid)
        _send_json(conn, {'ok': True, 'connected': False})


# ---------- 路由 ----------

def _dispatch(conn):
    headers, body_start = _read_headers(conn)
    if headers is None:
        return

    first = headers.split(b'\r\n', 1)[0].decode('utf-8', 'ignore')
    parts = first.split(' ')
    if len(parts) < 2:
        _send_text(conn, b'400 Bad Request', b'bad request')
        return
    method = parts[0]
    path = parts[1]
    if '?' in path:
        path, query = path.split('?', 1)
    else:
        query = ''

    # 页面
    if method == 'GET' and path == '/':
        _send_static(conn, WWW_ROOT + '/index.html')
        return
    if method == 'GET' and path == '/wifi':
        _send_static(conn, WWW_ROOT + '/wifi.html')
        return

    # 阅读器
    if method == 'GET' and path == '/api/books':
        _handle_api_books(conn)
        return
    if method == 'GET' and path == '/next':
        reader.next_page()
        _redirect(conn)
        return
    if method == 'GET' and path == '/prev':
        reader.prev_page()
        _redirect(conn)
        return
    if method == 'GET' and path == '/refresh':
        reader.render(full=True)
        _redirect(conn)
        return
    if method == 'GET' and path == '/open':
        _handle_open(conn, query)
        return
    if method == 'GET' and path == '/delete':
        _handle_delete_book(conn, query)
        return
    if method == 'POST' and path == '/upload':
        _handle_upload(conn, headers, body_start)
        return

    # 网络
    if method == 'GET' and path == '/status':
        _handle_status(conn)
        return
    if method == 'GET' and path == '/wifi/scan':
        _handle_wifi_scan(conn)
        return
    if method == 'GET' and path == '/wifi/list':
        _handle_wifi_list(conn)
        return
    if method == 'POST' and path == '/wifi/save':
        _handle_wifi_save(conn, headers, body_start)
        return
    if method == 'POST' and path == '/wifi/delete':
        _handle_wifi_delete(conn, headers, body_start)
        return
    if method == 'POST' and path == '/wifi/connect':
        _handle_wifi_connect(conn, headers, body_start)
        return

    # 静态文件兜底
    if method == 'GET':
        if '..' in path or '//' in path:
            _send_text(conn, b'404 Not Found', b'not found')
            return
        full = WWW_ROOT + path
        try:
            os.stat(full)
            _send_static(conn, full)
            return
        except OSError:
            pass

    _send_text(conn, b'404 Not Found', b'not found')


# ---------- 启动 ----------

def _ensure_www():
    try:
        os.mkdir(WWW_ROOT)
    except OSError:
        pass


def start():
    try:
        reader._ensure_dirs()
    except Exception:
        pass
    _ensure_www()

    try:
        os.stat(WWW_ROOT + '/index.html')
    except OSError:
        print('[WEB] 警告: /www/index.html 不存在')
        print('[WEB] 请先上传 esp/www/ 到 /www/')

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', 80))
    s.listen(3)
    print('[WEB] 服务器已启动 :80')

    while True:
        conn, addr = s.accept()
        try:
            _dispatch(conn)
        except Exception as e:
            print('[WEB] 处理请求失败:', e)
            try:
                _send_text(conn, b'500 Internal Server Error', str(e))
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass