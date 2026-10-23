# reader.py — 书籍加载、分页、页面渲染、翻页状态

import os
import time
import epd

W, H = 800, 480

# 字体：framebuf 内置 8x8，正文放大 3 倍 = 24px
FONT_SIZE = 8
SCALE = 3
CHAR_W = FONT_SIZE * SCALE       # 24
LINE_H = CHAR_W + 6              # 24 + 6 行间距 = 30

MARGIN_X = 20
MARGIN_TOP = 50
MARGIN_BOTTOM = 50

# 每行字符数、每页行数
COLS = (W - MARGIN_X * 2) // CHAR_W          # (800-40)/24 = 31
ROWS = (H - MARGIN_TOP - MARGIN_BOTTOM) // LINE_H  # (480-100)/30 = 12

# 每 10 页全刷一次
FULL_REFRESH_EVERY = 10

BOOKS_DIR = '/books'


class Book:
    def __init__(self, title, text):
        self.title = title
        self.lines = _wrap(text, COLS)
        self.pages = _paginate(self.lines, ROWS)
        self.page_idx = 0

    def page_count(self):
        return len(self.pages)

    def current(self):
        if not self.pages:
            return []
        return self.pages[self.page_idx]

    def next(self):
        if self.page_idx < len(self.pages) - 1:
            self.page_idx += 1
            return True
        return False

    def prev(self):
        if self.page_idx > 0:
            self.page_idx -= 1
            return True
        return False

    def goto(self, idx):
        if 0 <= idx < len(self.pages):
            self.page_idx = idx
            return True
        return False

    def progress(self):
        if not self.pages:
            return 0.0
        return (self.page_idx + 1) / len(self.pages)


# ---------- 分页 ----------

def _wrap(text, cols):
    """按字符数折行（等宽字体下等价于按像素宽度）"""
    lines = []
    for raw in text.split('\n'):
        raw = raw.replace('\r', '').rstrip()
        if not raw:
            lines.append('')
            continue
        while len(raw) > cols:
            # 尝试在空格处断行（英文友好）
            cut = raw.rfind(' ', 0, cols)
            if cut < cols // 2:
                cut = cols
            lines.append(raw[:cut].rstrip())
            raw = raw[cut:].lstrip()
        lines.append(raw)
    return lines


def _paginate(lines, rows):
    return [lines[i:i + rows] for i in range(0, len(lines), rows)]


# ---------- 书籍管理 ----------

def ensure_dir():
    try:
        os.mkdir(BOOKS_DIR)
    except OSError:
        pass


def list_books():
    ensure_dir()
    try:
        names = sorted(n for n in os.listdir(BOOKS_DIR) if n.endswith('.txt'))
    except OSError:
        return []
    return names


def load_book(filename):
    path = BOOKS_DIR + '/' + filename
    try:
        with open(path, 'r') as f:
            text = f.read()
    except OSError as e:
        print('读取失败:', e)
        return None
    title = filename[:-4] if filename.endswith('.txt') else filename
    return Book(title, text)


def save_book(filename, text):
    ensure_dir()
    if not filename.endswith('.txt'):
        filename += '.txt'
    # 文件名安全化
    safe = ''.join(c if c.isalnum() or c in '._-' else '_' for c in filename)
    path = BOOKS_DIR + '/' + safe
    with open(path, 'w') as f:
        f.write(text)
    return safe


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
    """把当前页画到屏幕。full=True 强制全刷，否则局刷"""
    global _pages_since_full
    if _current_book is None:
        return

    epd.fill(0)

    # 页眉
    title = _current_book.title[:25]
    epd.text(title, MARGIN_X, 12, 1, 2)

    # 分隔线
    epd.fill_rect(0, 42, W, 1, 1)

    # 正文
    y = MARGIN_TOP
    for line in _current_book.current():
        if line:
            epd.text(line, MARGIN_X, y, 1, SCALE)
        y += LINE_H

    # 页脚：页码 + 进度条
    info = '%d / %d' % (_current_book.page_idx + 1, _current_book.page_count())
    epd.text(info, MARGIN_X, H - 36, 1, 2)

    bar_y = H - 12
    bar_w = W - MARGIN_X * 2
    epd.rect(MARGIN_X, bar_y, bar_w, 6, 1)
    p = _current_book.progress()
    epd.fill_rect(MARGIN_X, bar_y, int(bar_w * p), 6, 1)

    # 决定全刷还是局刷
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