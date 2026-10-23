# reader.py — 把 TXT 按 800×480 分页
import epd

# 用 32px 字体，一行大约 24 个 ASCII 字符、16 行左右
LINE_H = 36
MARGIN_X = 20
MARGIN_Y = 20
CHARS_PER_LINE = 24
LINES_PER_PAGE = 12

class Reader:
    def __init__(self):
        self.pages = []
        self.idx = 0

    def load(self, text):
        lines = []
        for raw in text.splitlines():
            # 简单折行：按固定字符数切
            while len(raw) > CHARS_PER_LINE:
                lines.append(raw[:CHARS_PER_LINE])
                raw = raw[CHARS_PER_LINE:]
            lines.append(raw)

        self.pages = [
            lines[i:i + LINES_PER_PAGE]
            for i in range(0, len(lines), LINES_PER_PAGE)
        ]
        self.idx = 0

    def render_current(self, partial=False):
        epd.clear()
        page = self.pages[self.idx] if self.pages else []
        for i, line in enumerate(page):
            epd.text(line, MARGIN_X, MARGIN_Y + i * LINE_H, 1)
        if partial:
            epd.show_partial()
        else:
            epd.show_full()

    def next(self):
        if self.idx < len(self.pages) - 1:
            self.idx += 1
            self.render_current(partial=True)

    def prev(self):
        if self.idx > 0:
            self.idx -= 1
            self.render_current(partial=True)

    def page_info(self):
        if not self.pages:
            return "0 / 0"
        return f"{self.idx + 1} / {len(self.pages)}"