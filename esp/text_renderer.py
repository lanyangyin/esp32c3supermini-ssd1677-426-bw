# text_renderer.py — 极简黑白灰度渲染
import framebuf
import gc
import os

W, H = 800, 480
SENTINEL = 0xFF

BAYER4 = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]


def _make_bytearray_fill(n, value=0):
    b = bytearray(n)
    if n == 0 or value == 0:
        return b
    for i in range(n):
        b[i] = value
    return b


class BitmapFont:
    def __init__(self, path):
        self.glyphs = {}
        self.max_h = 0
        self.is_unicode = False
        with open(path, "rb") as f:
            header = f.read(6)
            version = header[0]
            if version == 0x03:
                flags = header[1]
                count = header[2] | (header[3] << 8)
                self.max_h = header[4] | (header[5] << 8)
                self.is_unicode = bool(flags & 0x01)
            else:
                raise ValueError("不支持的字体版本")
            for _ in range(count):
                if self.is_unicode:
                    ch = f.read(2)
                    code = (ch[0] << 8) | ch[1]
                else:
                    ch = f.read(1)
                    code = ch[0]
                wh = f.read(2)
                w, h = wh[0], wh[1]
                if w == 0 or h == 0:
                    self.glyphs[code] = (0, 0, b"")
                    continue
                rb = (w + 7) // 8
                data = bytearray(f.read(rb * h))
                self.glyphs[code] = (w, h, data)

    def glyph(self, code):
        return self.glyphs.get(code)

    def advance(self, code):
        g = self.glyphs.get(code)
        return None if g is None else g[0]

    def space_width(self):
        g = self.glyphs.get(32)
        return g[0] if g and g[0] > 0 else 6

    def close(self):
        self.glyphs.clear()
        gc.collect()


# 兼容 v4 多 chunk
class MultiChunkV4Font:
    def __init__(self, paths):
        self.paths = list(paths)
        self._loaded = [None] * len(paths)
        self._max_h = None
        self._is_unicode = True

    def _load_chunk(self, i):
        if self._loaded[i] is not None: return self._loaded[i]
        try:
            with open(self.paths[i], "rb") as f:
                header = f.read(6)
                if header[0] != 0x04: return False
                count = header[2] | (header[3] << 8)
                self._max_h = header[4] | (header[5] << 8)
                self._is_unicode = bool(header[1] & 0x01)
                # 仅读取索引以备查（为了缩减代码量，直接加载全部）
                index = {}
                for j in range(count):
                    off = 6 + j * 6
                    f.seek(off)
                    buf = f.read(6)
                    c = (buf[0] << 8) | buf[1]
                    gOff = (buf[2] << 16) | (buf[3] << 8) | buf[4]
                    w = buf[5]
                    index[c] = (gOff, w)
                self._loaded[i] = (index, self.paths[i])
                return self._loaded[i]
        except Exception:
            self._loaded[i] = False
            return False

    def glyph(self, code):
        for i in range(len(self.paths)):
            res = self._load_chunk(i)
            if not res: continue
            index, path = res
            if code in index:
                gOff, w = index[code]
                if w == 0: return (0, 0, b"")
                rb = (w + 7) // 8
                size = rb * self._max_h
                with open(path, "rb") as f:
                    f.seek(gOff)
                    data = f.read(size)
                return (w, self._max_h, data)
        return None

    def advance(self, code):
        g = self.glyph(code)
        return None if g is None else g[0]

    @property
    def max_h(self):
        for i in range(len(self.paths)):
            self._load_chunk(i)
            if self._max_h: return self._max_h
        return 0

    @property
    def is_unicode(self):
        return True

    def space_width(self):
        return 8

    def close(self):
        pass


def _resolve_font(font_paths=None):
    if not font_paths:
        font_paths = ['/www/fonts/base_32_0.bin']
    try:
        with open(font_paths[0], "rb") as f:
            h = f.read(1)
        ver = h[0] if h else 0
        if ver == 0x04: return MultiChunkV4Font(font_paths)
        return BitmapFont(font_paths[0])
    except Exception as e:
        print("[FONT] load fail:", e)
        return None


# ---------- 渲染核心 ----------

def _layout_text(text, font, per_char, ds, inner_w, inner_h, opts):
    lines = text.split('\n') if text else ['']
    line_infos = []
    total_h = 0
    ci = 0
    for li, line in enumerate(lines):
        line_w = 0
        max_h = font.max_h
        for ch in line:
            style = per_char.get(str(ci), ds)
            w = font.advance(ord(ch)) if font else 0
            line_w += w or font.space_width()
            ci += 1
        lh = int(max_h * opts['line_height'])
        line_infos.append({'start': ci - len(line), 'line_w': line_w, 'lh': lh, 'max_h': max_h})
        total_h += lh

    avail_h = inner_h - opts['margin_top'] - opts['margin_bottom']
    cy = opts['margin_top']
    if opts['valign'] == 'center':
        cy += (avail_h - total_h) // 2
    elif opts['valign'] == 'bottom':
        cy += (avail_h - total_h)

    for info in line_infos:
        cx = opts['margin_left']
        if opts['align'] == 'center':
            cx += (inner_w - opts['margin_left'] - opts['margin_right'] - info['line_w']) // 2
        elif opts['align'] == 'right':
            cx += (inner_w - opts['margin_left'] - opts['margin_right'] - info['line_w'])
        info['cx0'] = cx
        info['cy'] = cy
        cy += info['lh']

    return line_infos, lines


def _dither(px, py, gray_level):
    # gray_level: 0 (纯黑) -> 100 (纯白)
    if gray_level <= 0: return 0
    if gray_level >= 100: return 1
    threshold = (100 - gray_level) * 16 // 100
    return 0 if BAYER4[py & 3][px & 3] < threshold else 1


def _to_1bpp(buf, w, h):
    out = bytearray((w * h) // 8)
    for i in range(0, w * h, 8):
        b = 0
        for bit in range(8):
            idx = i + bit
            if idx < len(buf) and buf[idx] == 0:  # 0 为黑
                b |= (0x80 >> bit)
        out[i // 8] = b
    return out


def render_and_display_stream(epd, text, size=32, direction=0, per_char=None,
                              default_style=None, align='left', valign='top',
                              line_height=1.5, margin_top=20, margin_bottom=20,
                              margin_left=20, margin_right=20,
                              band_rows=8, background=None, font_paths=None):
    font = _resolve_font(font_paths)
    if not font: raise ValueError("字体加载失败")

    ds = default_style or {}
    global_gray = ds.get('gray_level', 0)

    opts = {
        'align': align, 'valign': valign, 'line_height': line_height,
        'margin_top': margin_top, 'margin_bottom': margin_bottom,
        'margin_left': margin_left, 'margin_right': margin_right
    }

    # 兼容旧坐标系统，直接按屏幕尺寸 800x480 渲染
    line_infos, lines = _layout_text(text, font, per_char, ds, W, H, opts)

    # 1. 生成完整索引画布（0=黑，1=白，0xFF=透明）
    idx_canvas = bytearray([0xFF]) * (W * H)

    for li, line in enumerate(lines):
        info = line_infos[li]
        cx = info['cx0']
        cy = info['cy']
        base_ci = info['start']
        for cii, ch in enumerate(line):
            gci = base_ci + cii
            style = per_char.get(str(gci), ds) if per_char else ds
            gray = style.get('gray_level', global_gray)

            g = font.glyph(ord(ch))
            if not g:
                cx += font.space_width()
                continue
            gw, gh, data = g
            if gw == 0 or gh == 0:
                cx += font.space_width()
                continue

            # 将字模按灰度画入画布
            rb = (gw + 7) // 8
            for ry in range(gh):
                py = cy + ry
                if py < 0 or py >= H: continue
                for rx in range(gw):
                    px = cx + rx
                    if px < 0 or px >= W: continue
                    if data[ry * rb + (rx >> 3)] & (0x80 >> (rx & 7)):
                        # 这是一个字模像素，决定它是黑是白
                        idx_canvas[py * W + px] = _dither(px, py, gray)
            cx += gw

    # 2. 处理背景图
    bg_buf = None
    if background:
        try:
            with open(background, 'rb') as f:
                bg_buf = f.read(W * H // 8)
        except Exception as e:
            print("[TEXT] bg read fail:", e)

    # 3. 上屏
    epd.fill(1)  # 默认白底
    if bg_buf:
        # 如果有背景，直接把背景数据加载到 epd.buffer
        epd.buffer[:] = bg_buf

    # 将 idx_canvas 中的文字合并到 epd.buffer
    for i in range(W * H):
        if idx_canvas[i] != 0xFF:
            py = i // W
            px = i % W
            if idx_canvas[i] == 0:  # 黑色
                epd.buffer[py * (W // 8) + (px >> 3)] |= (0x80 >> (px & 7))
            else:  # 白色
                epd.buffer[py * (W // 8) + (px >> 3)] &= ~(0x80 >> (px & 7))

    epd.show()