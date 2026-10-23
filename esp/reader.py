# reader.py — 流式分页 + 流式渲染（ext + int 双存储）

import os
import gc
import time
import epd
import storage_state

from array import array

W, H = 800, 480

FONT_SIZE = 8
SCALE = 3
CHAR_W = FONT_SIZE * SCALE
LINE_H = CHAR_W + 6

MARGIN_X = 20
MARGIN_TOP = 50
MARGIN_BOTTOM = 50

COLS = (W - MARGIN_X * 2) // CHAR_W
ROWS = (H - MARGIN_TOP - MARGIN_BOTTOM) // LINE_H

FULL_REFRESH_EVERY = 10

MAX_BOOK_SIZE = 500_000
MAX_OFFSETS = 2000

STORAGE_EXT = '/ext/books'
STORAGE_INT = '/books'


def _ext_label():
    t = storage_state.EXT_TYPE
    if t == 'w25q':
        return 'W25Q'
    if t == 'sd':
        return 'SD'
    return 'EXT'


class Book:
    __slots__ = ('title', 'filename', 'storage', 'path',
                 'offsets', 'page_idx')

    def __init__(self, title, filename, storage, path, offsets):
        self.title = title
        self.filename = filename
        self.storage = storage
        self.path = path
        self.offsets = offsets
        self.page_idx = 0

    def page_count(self):
        return len(self.offsets)

    def next(self):
        if self.page_idx < len(self.offsets) - 1:
            self.page_idx += 1
            return True
        return False

    def prev(self):
        if self.page_idx > 0:
            self.page_idx -= 1
            return True
        return False

    def progress(self):
        if not self.offsets:
            return 0.0
        return (self.page_idx + 1) / len(self.offsets)

    def key(self):
        return self.storage + '/' + self.filename


def _is_ascii_file(path, probe_size=4096):
    try:
        with open(path, 'rb') as f:
            head = f.read(probe_size)
    except OSError:
        return False
    for b in head:
        if b > 127:
            return False
    return True


def _scan_pages(path, cols, rows):
    """扫描文件，返回每页起始字节偏移的 array。峰值内存 < 1 KB。"""
    offsets = array('I', [0])

    page_lines = 0
    col = 0
    pos = 0

    buf = bytearray(512)
    try:
        with open(path, 'rb') as f:
            while True:
                n = f.readinto(buf)
                if n == 0:
                    break
                for i in range(n):
                    b = buf[i]
                    if b == 0x0A:  # \n
                        page_lines += 1
                        if page_lines >= rows:
                            if len(offsets) >= MAX_OFFSETS:
                                return offsets
                            offsets.append(pos + i + 1)
                            page_lines = 0
                        col = 0
                    else:
                        col += 1
                        if col >= cols:
                            page_lines += 1
                            if page_lines >= rows:
                                if len(offsets) >= MAX_OFFSETS:
                                    return offsets
                                offsets.append(pos + i + 1)
                                page_lines = 0
                            col = 0
                pos += n
    except OSError as e:
        print('[READER] 扫描失败:', e)

    return offsets


def _draw_line(line_bytes, y):
    """画一行（最多 COLS 个字节的 ASCII）"""
    if not line_bytes:
        return
    try:
        s = bytes(line_bytes).decode('ascii', 'replace')
    except Exception:
        return
    epd.text(s, MARGIN_X, y, 1, SCALE)


def _render_page_direct(book):
    """从当前页偏移边读边画。峰值内存 < 1 KB。"""
    try:
        f = open(book.path, 'rb')
    except OSError as e:
        print('[READER] 打开失败:', e)
        return

    try:
        f.seek(book.offsets[book.page_idx])
        y = MARGIN_TOP
        visual_count = 0
        buf = bytearray(256)
        cur_line = bytearray()

        while visual_count < ROWS:
            n = f.readinto(buf)
            if n == 0:
                break
            for i in range(n):
                b = buf[i]
                if b == 0x0A:
                    _draw_line(cur_line, y)
                    y += LINE_H
                    visual_count += 1
                    cur_line = bytearray()
                    if visual_count >= ROWS:
                        break
                else:
                    cur_line.append(b)
                    if len(cur_line) >= COLS:
                        _draw_line(cur_line, y)
                        y += LINE_H
                        visual_count += 1
                        cur_line = bytearray()
                        if visual_count >= ROWS:
                            break

        if cur_line and visual_count < ROWS:
            _draw_line(cur_line, y)
    finally:
        try:
            f.close()
        except Exception:
            pass


# ---------- 存储 ----------

def is_ext_available():
    try:
        os.statvfs('/ext')
        return True
    except OSError:
        return False


def _storage_path(storage):
    if storage == 'ext':
        return STORAGE_EXT
    return STORAGE_INT


def _ensure_dirs():
    try:
        os.mkdir(STORAGE_INT)
    except OSError:
        pass
    if is_ext_available():
        try:
            os.mkdir(STORAGE_EXT)
        except OSError:
            pass


def _scan_dir(storage):
    path = _storage_path(storage)
    result = []
    try:
        for n in os.listdir(path):
            if not n.endswith('.txt'):
                continue
            full = path + '/' + n
            try:
                sz = os.stat(full)[6]
            except OSError:
                sz = 0
            result.append({'name': n, 'storage': storage, 'size': sz})
    except OSError:
        pass
    return result


def list_books():
    _ensure_dirs()
    books = []
    if is_ext_available():
        books.extend(_scan_dir('ext'))
    books.extend(_scan_dir('int'))
    books.sort(key=lambda b: (0 if b['storage'] == 'ext' else 1, b['name']))
    return books


def find_book(name):
    if is_ext_available():
        try:
            os.stat(STORAGE_EXT + '/' + name)
            return 'ext'
        except OSError:
            pass
    try:
        os.stat(STORAGE_INT + '/' + name)
        return 'int'
    except OSError:
        return None


def load_book(filename, storage=None):
    """只扫描页偏移，不读内容。内存增量 < 5 KB。"""
    if isinstance(filename, dict):
        storage = filename.get('storage')
        filename = filename.get('name')
    if not filename:
        print('[READER] 无效文件名')
        return None
    if storage not in ('ext', 'int'):
        storage = find_book(filename)
        if storage is None:
            print('[READER] 文件不存在:', filename)
            return None

    path = _storage_path(storage) + '/' + filename

    try:
        sz = os.stat(path)[6]
    except OSError as e:
        print('[READER] 无法读取:', e)
        return None

    if sz > MAX_BOOK_SIZE:
        print('[READER] %s 有 %d 字节，超过上限 %d'
              % (filename, sz, MAX_BOOK_SIZE))
        return None

    if not _is_ascii_file(path):
        print('[READER] %s 含非 ASCII 字符，当前字体不支持' % filename)
        return None

    gc.collect()
    try:
        offsets = _scan_pages(path, COLS, ROWS)
    except MemoryError:
        print('[READER] 扫描 OOM')
        return None

    if len(offsets) == 0:
        print('[READER] 空文件')
        return None

    title = filename[:-4] if filename.endswith('.txt') else filename
    return Book(title, filename, storage, path, offsets)


def _safe_filename(filename):
    if not filename.endswith('.txt'):
        filename += '.txt'
    out = []
    for c in filename:
        o = ord(c)
        if (0x30 <= o <= 0x39) or (0x41 <= o <= 0x5A) or (0x61 <= o <= 0x7A) \
                or o == 0x2E or o == 0x5F or o == 0x2D:
            out.append(c)
        else:
            out.append('_')
    return ''.join(out)


def save_book(filename, text, prefer_ext=True):
    _ensure_dirs()
    if prefer_ext and is_ext_available():
        storage = 'ext'
    else:
        storage = 'int'
    safe = _safe_filename(filename)
    path = _storage_path(storage) + '/' + safe
    with open(path, 'w') as f:
        f.write(text)
    return safe, storage


def delete_book(filename, storage):
    path = _storage_path(storage) + '/' + filename
    try:
        os.remove(path)
        return True
    except OSError as e:
        print('[READER] 删除失败:', e)
        return False


# ---------- 渲染 ----------

_current_book = None
_pages_since_full = 0


def set_book(book):
    global _current_book, _pages_since_full
    _current_book = book
    _pages_since_full = 0


def get_book():
    return _current_book


def render(full=False):
    global _pages_since_full
    if _current_book is None:
        return

    epd.fill(0)

    title = _current_book.title[:22]
    epd.text(title, MARGIN_X, 12, 1, 2)

    if _current_book.storage == 'ext':
        tag = _ext_label()
    else:
        tag = 'Flash'
    tag_x = W - MARGIN_X - len(tag) * 16
    epd.text(tag, tag_x, 12, 1, 2)

    epd.fill_rect(0, 42, W, 1, 1)

    # 流式渲染当前页
    _render_page_direct(_current_book)

    info = '%d / %d' % (_current_book.page_idx + 1, _current_book.page_count())
    epd.text(info, MARGIN_X, H - 36, 1, 2)

    bar_y = H - 12
    bar_w = W - MARGIN_X * 2
    epd.rect(MARGIN_X, bar_y, bar_w, 6, 1)
    p = _current_book.progress()
    epd.fill_rect(MARGIN_X, bar_y, int(bar_w * p), 6, 1)

    need_full = full or _pages_since_full >= FULL_REFRESH_EVERY
    t0 = time.ticks_ms()
    if need_full:
        epd.show()
        _pages_since_full = 0
        print('[READER] 全刷 %d ms' % time.ticks_diff(time.ticks_ms(), t0))
    else:
        epd.show_partial()
        _pages_since_full += 1
        print('[READER] 局刷 %d ms' % time.ticks_diff(time.ticks_ms(), t0))


def next_page():
    if _current_book is None:
        return False
    if _current_book.next():
        render()
        return True
    return False


def prev_page():
    if _current_book is None:
        return False
    if _current_book.prev():
        render()
        return True
    return False