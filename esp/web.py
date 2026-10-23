# web.py — 上传 TXT + 翻页按钮
import socket
import reader

r = reader.Reader()

HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head><body>
<h2>EInk Reader</h2>
<p>页码: <span id="p">%s</span></p>
<form method="POST" action="/upload" enctype="multipart/form-data">
<input type="file" name="f" accept=".txt">
<button type="submit">上传 TXT</button>
</form>
<p>
<a href="/prev"><button>上一页</button></a>
<a href="/next"><button>下一页</button></a>
</p>
</body></html>"""

def start():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 80))
    s.listen(2)
    print("Web: http://<ESP32-IP>/")

    while True:
        conn, _ = s.accept()
        try:
            req = conn.recv(2048).decode("utf-8", "ignore")
            first = req.split("\r\n", 1)[0]

            if first.startswith("GET /next"):
                r.next()
                _redirect(conn)
            elif first.startswith("GET /prev"):
                r.prev()
                _redirect(conn)
            elif first.startswith("POST /upload"):
                _handle_upload(conn, req)
            else:
                _send_html(conn)
        finally:
            conn.close()

def _send_html(conn):
    body = HTML % r.page_info()
    conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n")
    conn.send(body.encode())

def _redirect(conn):
    conn.send(b"HTTP/1.1 302 Found\r\nLocation: /\r\n\r\n")

def _handle_upload(conn, req):
    # 极简 multipart 解析：只取第一个文件的内容
    boundary = req.split("boundary=")[1].split("\r\n")[0]
    parts = req.split("--" + boundary)
    for p in parts:
        if "filename=" in p:
            body = p.split("\r\n\r\n", 1)[1].rsplit("\r\n", 1)[0]
            r.load(body)
            r.render_current(partial=False)
            break
    _redirect(conn)