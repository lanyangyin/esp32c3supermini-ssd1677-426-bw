# epd.py — SSD1677 黑白墨水屏封装（增强版 + 旋转支持）
import framebuf
import ssd1677
from machine import Pin, SPI

# ---------- 默认引脚 ----------
SCK, MOSI, CS, DC, RST, BUSY = 18, 23, 4, 13, 14, 27

# ---------- 颜色 ----------
BLACK = 1
WHITE = 0


# ======================================================
# 放大字模（含反色缓存）
# ======================================================
class _ScaledFont:
    def __init__(self, scale):
        self.scale = scale
        self.w = 8 * scale
        self.h = 8 * scale
        self.bytes_per_row = (self.w + 7) // 8
        self.buf_size = self.bytes_per_row * self.h
        self.cache = {}  # (ch, invert) -> FrameBuffer

    def glyph(self, ch, invert=False):
        key = (ch, invert)
        if key in self.cache:
            return self.cache[key]

        tmp = bytearray(8)
        fb = framebuf.FrameBuffer(tmp, 8, 8, framebuf.MONO_HLSB)
        fb.fill(0)
        try:
            fb.text(ch, 0, 0, 1)
        except Exception:
            self.cache[key] = None
            return None

        out = bytearray(self.buf_size)
        s = self.scale
        for py in range(8):
            for px in range(8):
                if fb.pixel(px, py):
                    for dy in range(s):
                        ny = py * s + dy
                        row = ny * self.bytes_per_row
                        for dx in range(s):
                            nx = px * s + dx
                            out[row + (nx >> 3)] |= 0x80 >> (nx & 7)

        if invert:
            for i in range(len(out)):
                out[i] ^= 0xFF

        fbuf = framebuf.FrameBuffer(out, self.w, self.h, framebuf.MONO_HLSB)
        self.cache[key] = fbuf
        return fbuf


# ======================================================
# 主类
# ======================================================
class EPD:
    def __init__(self, rotation=0, baudrate=4_000_000):
        self.rotation = rotation
        self._scaled_fonts = {}

        # 1. 初始化底层物理驱动 (固定 800x480)
        spi = SPI(1, baudrate=baudrate, polarity=0, phase=0,
                  sck=Pin(SCK), mosi=Pin(MOSI))
        self._phys = ssd1677.SSD1677(
            800, 480, spi,
            dc=Pin(DC), cs=Pin(CS), rst=Pin(RST), busy=Pin(BUSY)
        )

        # 2. 根据旋转角度，设置逻辑画布尺寸与缓存策略
        if rotation in (90, 270):
            self.width = 480
            self.height = 800
            # 独立分配逻辑缓存
            self.buffer = bytearray(self.width * self.height // 8)
            self._drawer = framebuf.FrameBuffer(self.buffer, self.width, self.height, framebuf.MONO_HLSB)
            self._is_shared = False
        else:
            self.width = 800
            self.height = 480
            self.buffer = self._phys.buffer
            self._drawer = self._phys  # 直接复用物理驱动的画图能力(自带脏区追踪)
            self._is_shared = True

        self._dirty = None

    def _mark_dirty(self, x0, y0, x1, y1):
        if not self._is_shared:
            # 非共享模式，手动记录逻辑脏区
            x0 = max(0, min(x0, self.width - 1))
            x1 = max(0, min(x1, self.width - 1))
            y0 = max(0, min(y0, self.height - 1))
            y1 = max(0, min(y1, self.height - 1))
            if x0 > x1: x0, x1 = x1, x0
            if y0 > y1: y0, y1 = y1, y0

            if self._dirty is None:
                self._dirty = [x0, y0, x1, y1]
            else:
                self._dirty[0] = min(self._dirty[0], x0)
                self._dirty[1] = min(self._dirty[1], y0)
                self._dirty[2] = max(self._dirty[2], x1)
                self._dirty[3] = max(self._dirty[3], y1)

    # ============== 旋转同步逻辑 ==============
    def _sync(self):
        if self.rotation == 0:
            pass  # 共享内存，无需同步

        elif self.rotation == 180:
            # 180度翻转 (上下颠倒)
            src = self.buffer
            dst = self._phys.buffer
            for i in range(48000):
                b = src[47999 - i]
                # 位反转查表或者手动翻转
                b = ((b & 0xF0) >> 4) | ((b & 0x0F) << 4)
                b = ((b & 0xCC) >> 2) | ((b & 0x33) << 2)
                b = ((b & 0xAA) >> 1) | ((b & 0x55) << 1)
                dst[i] = b

        elif self.rotation == 90:
            # 顺时针90度 (竖屏，排线在底部或左侧)
            src = self.buffer
            dst = self._phys.buffer
            # 清空目标缓存 (注意: 这里不用 _phys.fill 以避免意外)
            for i in range(48000): dst[i] = 0

            for y in range(800):
                py_byte = y >> 3
                py_bit = 0x80 >> (y & 7)
                src_row = y * 60
                for x_byte in range(60):
                    b = src[src_row + x_byte]
                    if b:
                        for bit in range(8):
                            if b & (0x80 >> bit):
                                lx = x_byte * 8 + bit
                                px = 479 - lx
                                dst[px * 100 + py_byte] |= py_bit

        elif self.rotation == 270:
            # 逆时针90度 (竖屏，排线在顶部或右侧)
            src = self.buffer
            dst = self._phys.buffer
            for i in range(48000): dst[i] = 0

            for y in range(800):
                py_byte = (799 - y) >> 3
                py_bit = 0x80 >> ((799 - y) & 7)
                src_row = y * 60
                for x_byte in range(60):
                    b = src[src_row + x_byte]
                    if b:
                        for bit in range(8):
                            if b & (0x80 >> bit):
                                lx = x_byte * 8 + bit
                                px = lx
                                dst[px * 100 + py_byte] |= py_bit

    # ============== 显示控制 ==============
    def show(self):
        self._sync()
        if not self._is_shared:
            self._phys._dirty = [0, 0, 799, 479]  # 强制全屏刷新
        self._phys.show()
        self._dirty = None

    def show_partial(self):
        if self._is_shared:
            self._phys.show_partial()  # 横屏支持真·局部刷新
        else:
            self.show()  # 旋转模式退化为全刷

    def clear(self, c=WHITE):
        self.fill(c)
        self.show()

    def sleep(self):
        self._phys.sleep()

    # ============== 基础绘制（自动代理并记录脏区） ==============
    def fill(self, c=WHITE):
        self._mark_dirty(0, 0, self.width - 1, self.height - 1)
        self._drawer.fill(c)

    def pixel(self, x, y, c=None):
        if c is None:
            return self._drawer.pixel(x, y)
        self._mark_dirty(x, y, x, y)
        self._drawer.pixel(x, y, c)

    def hline(self, x, y, w, c=BLACK):
        self._mark_dirty(x, y, x + w - 1, y)
        self._drawer.hline(x, y, w, c)

    def vline(self, x, y, h, c=BLACK):
        self._mark_dirty(x, y, x, y + h - 1)
        self._drawer.vline(x, y, h, c)

    def line(self, x1, y1, x2, y2, c=BLACK):
        self._mark_dirty(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self._drawer.line(x1, y1, x2, y2, c)

    def rect(self, x, y, w, h, c=BLACK):
        self._mark_dirty(x, y, x + w - 1, y + h - 1)
        self._drawer.rect(x, y, w, h, c)

    def fill_rect(self, x, y, w, h, c=BLACK):
        self._mark_dirty(x, y, x + w - 1, y + h - 1)
        self._drawer.fill_rect(x, y, w, h, c)

    def ellipse(self, x, y, xr, yr, c=BLACK):
        self._mark_dirty(x - xr, y - yr, x + xr, y + yr)
        self._drawer.ellipse(x, y, xr, yr, c)

    def fill_ellipse(self, x, y, xr, yr, c=BLACK):
        if xr <= 0 or yr <= 0: return
        self._mark_dirty(x - xr, y - yr, x + xr, y + yr)
        for dy in range(-yr, yr + 1):
            rem = yr * yr - dy * dy
            if rem < 0: continue
            dx = int(xr * (rem ** 0.5) // yr)
            self._drawer.hline(x - dx, y + dy, 2 * dx + 1, c)

    def circle(self, x, y, r, c=BLACK):
        self.ellipse(x, y, r, r, c)

    def fill_circle(self, x, y, r, c=BLACK):
        self.fill_ellipse(x, y, r, r, c)

    def triangle(self, x1, y1, x2, y2, x3, y3, c=BLACK):
        self.line(x1, y1, x2, y2, c)
        self.line(x2, y2, x3, y3, c)
        self.line(x3, y3, x1, y1, c)

    def fill_triangle(self, x1, y1, x2, y2, x3, y3, c=BLACK):
        self._mark_dirty(min(x1, x2, x3), min(y1, y2, y3), max(x1, x2, x3), max(y1, y2, y3))
        pts = sorted([(x1, y1), (x2, y2), (x3, y3)], key=lambda p: p[1])
        (xa, ya), (xb, yb), (xc, yc) = pts
        if ya == yc: return
        edges = [((xa, ya), (xb, yb)), ((xb, yb), (xc, yc)), ((xa, ya), (xc, yc))]
        for y in range(ya, yc + 1):
            xs = []
            for p1, p2 in edges:
                if p1[1] == p2[1]: continue
                if p1[1] <= y <= p2[1]:
                    xi = int(p1[0] + (p2[0] - p1[0]) * (y - p1[1]) / (p2[1] - p1[1]))
                    xs.append(xi)
            if len(xs) >= 2:
                x0, x1_ = min(xs), max(xs)
                self._drawer.hline(x0, y, x1_ - x0 + 1, c)

    # ============== 圆角 / 虚线 / 边框 ==============
    def _arc_quadrant(self, cx, cy, r, c, quadrant):
        x, y, d = r, 0, 1 - r
        while x >= y:
            if quadrant == 2:
                pts = ((cx - x, cy - y), (cx - y, cy - x))
            elif quadrant == 1:
                pts = ((cx + x, cy - y), (cx + y, cy - x))
            elif quadrant == 3:
                pts = ((cx - x, cy + y), (cx - y, cy + x))
            else:
                pts = ((cx + x, cy + y), (cx + y, cy + x))
            for px, py in pts:
                self._drawer.pixel(px, py, c)
            y += 1
            if d <= 0:
                d += 2 * y + 1
            else:
                x -= 1
                d += 2 * (y - x) + 1

    def round_rect(self, x, y, w, h, r, c=BLACK):
        self._mark_dirty(x, y, x + w - 1, y + h - 1)
        if r <= 0:
            self._drawer.rect(x, y, w, h, c);
            return
        r = min(r, w // 2, h // 2)
        self._drawer.hline(x + r, y, w - 2 * r, c)
        self._drawer.hline(x + r, y + h - 1, w - 2 * r, c)
        self._drawer.vline(x, y + r, h - 2 * r, c)
        self._drawer.vline(x + w - 1, y + r, h - 2 * r, c)
        self._arc_quadrant(x + r, y + r, r, c, 2)
        self._arc_quadrant(x + w - 1 - r, y + r, r, c, 1)
        self._arc_quadrant(x + r, y + h - 1 - r, r, c, 3)
        self._arc_quadrant(x + w - 1 - r, y + h - 1 - r, r, c, 0)

    def fill_round_rect(self, x, y, w, h, r, c=BLACK):
        self._mark_dirty(x, y, x + w - 1, y + h - 1)
        if r <= 0:
            self._drawer.fill_rect(x, y, w, h, c);
            return
        r = min(r, w // 2, h // 2)
        self._drawer.fill_rect(x + r, y, w - 2 * r, h, c)
        self._drawer.fill_rect(x, y + r, r, h - 2 * r, c)
        self._drawer.fill_rect(x + w - r, y + r, r, h - 2 * r, c)
        self.fill_circle(x + r, y + r, r, c)
        self.fill_circle(x + w - 1 - r, y + r, r, c)
        self.fill_circle(x + r, y + h - 1 - r, r, c)
        self.fill_circle(x + w - 1 - r, y + h - 1 - r, r, c)

    def dash_hline(self, x, y, w, c=BLACK, dash=4, gap=2):
        self._mark_dirty(x, y, x + w - 1, y)
        xx, end = x, x + w
        while xx < end:
            self._drawer.hline(xx, y, min(dash, end - xx), c)
            xx += dash + gap

    def dash_vline(self, x, y, h, c=BLACK, dash=4, gap=2):
        self._mark_dirty(x, y, x, y + h - 1)
        yy, end = y, y + h
        while yy < end:
            self._drawer.vline(x, yy, min(dash, end - yy), c)
            yy += dash + gap

    def border(self, x, y, w, h, c=BLACK, thickness=1):
        for i in range(thickness):
            self.rect(x + i, y + i, w - 2 * i, h - 2 * i, c)

    def invert_rect(self, x, y, w, h):
        self._mark_dirty(x, y, x + w - 1, y + h - 1)
        x0, y0 = max(0, x), max(0, y)
        x1 = min(self.width - 1, x + w - 1)
        y1 = min(self.height - 1, y + h - 1)
        for yy in range(y0, y1 + 1):
            for xx in range(x0, x1 + 1):
                self._drawer.pixel(xx, yy, 1 - self._drawer.pixel(xx, yy))

    # ============== 文本 ==============
    def measure_text(self, s, scale=1):
        return (8 * scale * len(s), 8 * scale)

    def _blit_text(self, s, x, y, color, scale):
        font = self._scaled_fonts.get(scale)
        if font is None:
            font = _ScaledFont(scale)
            self._scaled_fonts[scale] = font
        invert = (color == WHITE)
        cx = x
        for ch in s:
            g = font.glyph(ch, invert)
            if g is not None:
                self._mark_dirty(cx, y, cx + font.w - 1, y + font.h - 1)
                self._drawer.blit(g, cx, y, 0 if not invert else 1)
            cx += font.w

    def text(self, s, x, y, color=BLACK, scale=1):
        if scale <= 1:
            self._mark_dirty(x, y, x + 8 * len(s) - 1, y + 7)
            self._drawer.text(s, x, y, color)
        else:
            self._blit_text(s, x, y, color, scale)

    def text_invert(self, s, x, y, scale=1, pad=2):
        w, h = self.measure_text(s, scale)
        self.fill_rect(x - pad, y - pad, w + 2 * pad, h + 2 * pad, BLACK)
        self._blit_text(s, x, y, WHITE, scale)

    def text_center(self, s, y, color=BLACK, scale=1):
        w, _ = self.measure_text(s, scale)
        self.text(s, (self.width - w) // 2, y, color, scale)

    def text_right(self, s, x_right, y, color=BLACK, scale=1):
        w, _ = self.measure_text(s, scale)
        self.text(s, x_right - w, y, color, scale)

    def _wrap_lines(self, s, width, scale):
        cw = 8 * scale
        lines, cur = [], ""
        for ch in s:
            if ch == "\n":
                lines.append(cur);
                cur = "";
                continue
            if (len(cur) + 1) * cw > width:
                lines.append(cur);
                cur = ch
            else:
                cur += ch
        if cur: lines.append(cur)
        return lines

    def text_wrap(self, s, x, y, width, color=BLACK, scale=1, line_space=0):
        lines = self._wrap_lines(s, width, scale)
        cy = y
        lh = 8 * scale + line_space
        for line in lines:
            self.text(line, x, cy, color, scale)
            cy += lh
        return cy

    def text_center_wrap(self, s, y, width, color=BLACK, scale=1, line_space=0):
        lines = self._wrap_lines(s, width, scale)
        cy = y
        lh = 8 * scale + line_space
        for line in lines:
            w = 8 * scale * len(line)
            self.text(line, (width - w) // 2, cy, color, scale)
            cy += lh
        return cy

    # ============== 图片 / 组件 ==============
    def blit(self, fbuf, x, y, key=-1):
        w = getattr(fbuf, "width", self.width)
        h = getattr(fbuf, "height", self.height)
        self._mark_dirty(x, y, x + w - 1, y + h - 1)
        if key == -1:
            self._drawer.blit(fbuf, x, y)
        else:
            self._drawer.blit(fbuf, x, y, key)

    def bitmap(self, data, x, y, w, h, key=-1):
        fb = framebuf.FrameBuffer(data, w, h, framebuf.MONO_HLSB)
        self.blit(fb, x, y, key)

    def progress_bar(self, x, y, w, h, value, vmax=100, c=BLACK, border=True):
        self._mark_dirty(x, y, x + w - 1, y + h - 1)
        if border:
            self._drawer.rect(x, y, w, h, c)
            ix, iy, iw, ih = x + 1, y + 1, w - 2, h - 2
        else:
            ix, iy, iw, ih = x, y, w, h
        if vmax <= 0: vmax = 1
        v = max(0, min(value, vmax))
        fill_w = iw * v // vmax
        if fill_w > 0:
            self._drawer.fill_rect(ix, iy, fill_w, ih, c)

    def checkbox(self, x, y, size, checked=False, c=BLACK):
        self._mark_dirty(x, y, x + size - 1, y + size - 1)
        self._drawer.rect(x, y, size, size, c)
        if checked:
            self._drawer.line(x + size // 4, y + size // 2, x + size // 2, y + 3 * size // 4, c)
            self._drawer.line(x + size // 2, y + 3 * size // 4, x + 3 * size // 4, y + size // 4, c)


# ======================================================
# 模块级单例 + 函数式接口（兼容旧写法）
# ======================================================
_default = EPD(rotation=0)  # 默认使用横屏，如需竖屏请调用 init(rotation=90)
_display = _default._phys
buffer = _default.buffer


def init(rotation=0, baudrate=4_000_000):
    """重新初始化 (0: 横屏, 90: 竖屏右转, 180: 倒置横屏, 270: 竖屏左转)"""
    global _default, _display, buffer
    _default = EPD(rotation=rotation, baudrate=baudrate)
    _display = _default._phys
    buffer = _default.buffer
    return _default


# --- 基础 ---
def fill(c=WHITE):                 _default.fill(c)


def pixel(x, y, c=None):           return _default.pixel(x, y, c)


def hline(x, y, w, c=BLACK):       _default.hline(x, y, w, c)


def vline(x, y, h, c=BLACK):       _default.vline(x, y, h, c)


def line(x1, y1, x2, y2, c=BLACK): _default.line(x1, y1, x2, y2, c)


def rect(x, y, w, h, c=BLACK):     _default.rect(x, y, w, h, c)


def fill_rect(x, y, w, h, c=BLACK): _default.fill_rect(x, y, w, h, c)


def ellipse(x, y, xr, yr, c=BLACK):         _default.ellipse(x, y, xr, yr, c)


def fill_ellipse(x, y, xr, yr, c=BLACK):    _default.fill_ellipse(x, y, xr, yr, c)


def circle(x, y, r, c=BLACK):               _default.circle(x, y, r, c)


def fill_circle(x, y, r, c=BLACK):          _default.fill_circle(x, y, r, c)


def triangle(x1, y1, x2, y2, x3, y3, c=BLACK):     _default.triangle(x1, y1, x2, y2, x3, y3, c)


def fill_triangle(x1, y1, x2, y2, x3, y3, c=BLACK): _default.fill_triangle(x1, y1, x2, y2, x3, y3, c)


def round_rect(x, y, w, h, r, c=BLACK):     _default.round_rect(x, y, w, h, r, c)


def fill_round_rect(x, y, w, h, r, c=BLACK): _default.fill_round_rect(x, y, w, h, r, c)


def dash_hline(x, y, w, c=BLACK, dash=4, gap=2): _default.dash_hline(x, y, w, c, dash, gap)


def dash_vline(x, y, h, c=BLACK, dash=4, gap=2): _default.dash_vline(x, y, h, c, dash, gap)


def border(x, y, w, h, c=BLACK, thickness=1):    _default.border(x, y, w, h, c, thickness)


def invert_rect(x, y, w, h):       _default.invert_rect(x, y, w, h)


# --- 文本 ---
def measure_text(s, scale=1):      return _default.measure_text(s, scale)


def text(s, x, y, color=BLACK, scale=1):         _default.text(s, x, y, color, scale)


def text_invert(s, x, y, scale=1, pad=2):        _default.text_invert(s, x, y, scale, pad)


def text_center(s, y, color=BLACK, scale=1):     _default.text_center(s, y, color, scale)


def text_right(s, x_right, y, color=BLACK, scale=1): _default.text_right(s, x_right, y, color, scale)


def text_wrap(s, x, y, width, color=BLACK, scale=1, line_space=0):
    return _default.text_wrap(s, x, y, width, color, scale, line_space)


def text_center_wrap(s, y, width, color=BLACK, scale=1, line_space=0):
    return _default.text_center_wrap(s, y, width, color, scale, line_space)


# --- 图片与组件 ---
def blit(fbuf, x, y, key=-1):      _default.blit(fbuf, x, y, key)


def bitmap(data, x, y, w, h, key=-1): _default.bitmap(data, x, y, w, h, key)


def progress_bar(x, y, w, h, value, vmax=100, c=BLACK, border=True):
    _default.progress_bar(x, y, w, h, value, vmax, c, border)


def checkbox(x, y, size, checked=False, c=BLACK):
    _default.checkbox(x, y, size, checked, c)


# --- 显示 ---
def show():        _default.show()


def show_full():   _default.show()


def show_partial(): _default.show_partial()


def clear(c=WHITE): _default.clear(c)


def sleep():       _default.sleep()