# web.py — 极简 HTTP：上传 TXT + 翻页按钮

import socket
import reader


HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EInkReader</title>
<style>
body{font-family:sans-serif;max-width:480px;margin:20px auto;padding:0 14px;background:#f5f5f5}
h2{margin:0 0 6px}
.card{background:#fff;border-radius:10px;padding:14px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
button{padding:10px 16px;font-size:15px;border:0;border-radius:8px;background:#06c;color:#fff;font-weight:600;cursor:pointer;margin:3px}
button.gray{background:#888}
button:disabled{opacity:.4;cursor:not-allowed}
a{text-decoration:none}
.info{font-size:13px;color:#666;margin:6px 0}
.nav{display:flex;gap:6px}
.nav button{flex:1}
</style>
</head><body>
<h2>EInkReader</h2>

<div class="card">
<div class="info">当前书：<b>%s</b></div>
<div class="info">页码：<b>%s</b></div>
<div class="nav">
<a href="/prev"><button %s>上一页</button></a>
<a href="/next"><button %s>下一页</button></a>
</div>
</div>

<div class="card">
<form method="POST" action="/upload" enctype="multipart/form-data">
<div class="info">上传 TXT（建议 < 200KB，UTF-8 英文）</div>
<input type="file" name="f" accept=".txt" style="width:100%%;padding:8px 0">
<button type="submit">上传并打开</button>
</form>
</div>

<div class="card">
<div class="info">书架（%d 本）</div>
%s
</div>

<div class="card">
<a href="/refresh"><button class="gray">强制全刷</button></a>
</div>

</body></html>"""


def _book_status():
    b = reader.get_book()
    if b is None:
        return '（未选择）', '—', 'disabled', 'disabled'
    title = b.title
    page = '%d / %d' % (b.page_idx + 1, b.page_count())
    prev_dis = '' if b.page_idx > 0 else 'disabled'
    next_dis = '' if b.page_idx < b.page_count() - 1 else 'disabled'
    return title, page, prev_dis, next_dis


def _books_html():
    books = reader.list_books()
    if not books:
        return '<div class="info">暂无，请上传</div>'
    parts = []
    for name in books:
        label = name[:-4] if name.endswith('.txt') else name
        parts.append('<a href="/open?name=%s"><button class="gray" style="margin:3px 0;width:100%%;text-align:left">%s</button></a>'
                     % (_url_quote(name), label))
    return '\n'.join(parts)


def _url_quote(s):
    out = []
    for c in s:
        if c.isalnum() or c in '._-':
            out.append(c)
        else:
            out.append('%%%02X' % ord(c))
    return ''.join(out)


def _send_html(conn):
    title, page, prev_dis, next_dis = _book_status()
    books = reader.list_books()
    body = HTML % (title, page, prev_dis, next_dis, len(books), _books_html())
    conn.send(b'HTTP/1.1 200 OK\r\n')
    conn.send(b'Content-Type: text/html; charset=utf-8\r\n')
    conn.send(b'Connection: close\r\n\r\n')
    conn.send(body.encode())


def _redirect(conn, to='/'):
    conn.send(b'HTTP/1.1 302 Found\r\n')
    conn.send(('Location: %s\r\n' % to).encode())
    conn.send(b'Connection: close\r\n\r\n')


def _read_request(conn):
    """读完整 HTTP 请求（含 body），返回 (headers, body_bytes)"""
    buf = b''
    # 读 headers
    while b'\r\n\r\n' not in buf:
        chunk = conn.recv(512)
        if not chunk:
            return buf, b''
        buf += chunk
    idx = buf.find(b'\r\n\r\n')
    headers = buf[:idx]
    body_start = buf[idx + 4:]

    # 解析 Content-Length
    cl = 0
    for line in headers.split(b'\r\n'):
        low = line.lower()
        if low.startswith(b'content-length:'):
            try:
                cl = int(line.split(b':', 1)[1].strip())
            except ValueError:
                pass
            break

    body = body_start
    while len(body) < cl:
        chunk = conn.recv(min(1024, cl - len(body)))
        if not chunk:
            break
        body += chunk
    return headers, body


def _parse_multipart(headers, body):
    """从 multipart body 中提取第一个文件名和内容"""
    boundary = None
    for line in headers.split(b'\r\n'):
        if b'boundary=' in line:
            boundary = line.split(b'boundary=', 1)[1].strip()
            if boundary.startswith(b'"') and boundary.endswith(b'"'):
                boundary = boundary[1:-1]
            break
    if not boundary:
        return None, None

    sep = b'--' + boundary
    parts = body.split(sep)
    for p in parts:
        if b'filename="' in p:
            # 提取文件名
            i = p.find(b'filename="') + 10
            j = p.find(b'"', i)
            filename = p[i:j].decode('utf-8', 'ignore')
            # 提取内容（headers 和 body 之间的 \r\n\r\n 之后）
            k = p.find(b'\r\n\r\n')
            if k < 0:
                continue
            content = p[k + 4:]
            # 去掉末尾的 \r\n
            if content.endswith(b'\r\n'):
                content = content[:-2]
            return filename, content
    return None, None


def _handle_upload(conn, headers, body):
    filename, content = _parse_multipart(headers, body)
    if filename is None or content is None:
        print('[WEB] 上传解析失败')
        _redirect(conn)
        return

    try:
        text = content.decode('utf-8')
    except Exception as e:
        print('[WEB] 解码失败:', e)
        _redirect(conn)
        return

    # 大小检查
    if len(text) > 200_000:
        print('[WEB] 文件太大: %d 字符' % len(text))
        _redirect(conn)
        return

    saved = reader.save_book(filename, text)
    print('[WEB] 已保存 %s (%d 字符)' % (saved, len(text)))

    # 自动打开
    book = reader.load_book(saved)
    if book:
        reader.set_book(book)
        reader.render(full=True)

    _redirect(conn)


def _handle_open(conn, query):
    # /open?name=xxx.txt
    name = None
    for kv in query.split('&'):
        if kv.startswith('name='):
            name = kv[5:]
            break
    if not name:
        _redirect(conn)
        return
    # URL 解码
    out = []
    i = 0
    while i < len(name):
        c = name[i]
        if c == '%' and i + 2 < len(name):
            try:
                out.append(chr(int(name[i+1:i+3], 16)))
                i += 3
                continue
            except ValueError:
                pass
        out.append(c)
        i += 1
    decoded = ''.join(out)

    book = reader.load_book(decoded)
    if book:
        reader.set_book(book)
        reader.render(full=True)
    _redirect(conn)


def start():
    reader.ensure_dir()

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', 80))
    s.listen(3)
    print('[WEB] http://192.168.4.1/')

    while True:
        conn, _ = s.accept()
        try:
            headers, body = _read_request(conn)
            if not headers:
                continue

            first = headers.split(b'\r\n', 1)[0].decode('utf-8', 'ignore')
            parts = first.split(' ')
            if len(parts) < 2:
                continue
            method = parts[0]
            path = parts[1]
            if '?' in path:
                path, query = path.split('?', 1)
            else:
                query = ''

            if path == '/':
                _send_html(conn)
            elif path == '/next':
                reader.next_page()
                _redirect(conn)
            elif path == '/prev':
                reader.prev_page()
                _redirect(conn)
            elif path == '/refresh':
                reader.render(full=True)
                _redirect(conn)
            elif path == '/open':
                _handle_open(conn, query)
            elif path == '/upload' and method == 'POST':
                _handle_upload(conn, headers, body)
            else:
                _send_html(conn)
        except Exception as e:
            print('[WEB] 错误:', e)
        finally:
            try:
                conn.close()
            except Exception:
                pass