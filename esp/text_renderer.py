# text_renderer.py — 适配 SSD1677 1bpp (800x480)
import framebuf
import gc
import os

W, H = 800, 480

SENTINEL = 0xFF
_FONT_SIZES = (12, 16, 20, 24, 32, 48)
FONT_VERSION_V2 = 0x02
FONT_VERSION_V3 = 0x03
FONT_VERSION_V4 = 0x04

COLOR_NAMES = {
    "black": 0, "white": 1, "yellow": 2,
    "red": 3, "blue": 4, "green": 5,
}

_LS_MIN = -100
_LS_MAX = 200


def _make_bytearray_fill(n, value=0):
    b = bytearray(n)
    if n == 0 or value == 0:
        return b
    block = bytearray(64)
    for i in range(64):
        block[i] = value
    pos = 0
    while pos < n:
        remain = n - pos
        if remain >= 64:
            b[pos:pos + 64] = block
            pos += 64
        else:
            b[pos:n] = block[:remain]
            pos = n
    return b


# ---------- v2/v3 单文件 ----------

class BitmapFont:
    def __init__(self, path):
        self.glyphs = {}
        self.max_h = 0
        self.is_unicode = False
        with open(path, "rb") as f:
            header = f.read(6)
            if len(header) < 6:
                raise ValueError("字模文件不完整")
            version = header[0]
            if version == FONT_VERSION_V2:
                count = header[1]
                self.max_h = header[2] | (header[3] << 8)
                self.is_unicode = False
            elif version == FONT_VERSION_V3:
                flags = header[1]
                count = header[2] | (header[3] << 8)
                self.max_h = header[4] | (header[5] << 8)
                self.is_unicode = bool(flags & 0x01)
            else:
                raise ValueError("v2/v3 版本不支持: %d" % version)
            for _ in range(count):
                if self.is_unicode:
                    ch = f.read(2)
                    if len(ch) < 2: break
                    code = (ch[0] << 8) | ch[1]
                else:
                    ch = f.read(1)
                    if len(ch) < 1: break
                    code = ch[0]
                wh = f.read(2)
                if len(wh) < 2: break
                w = wh[0]; h = wh[1]
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
        if g and g[0] > 0:
            return g[0]
        for c in (77, 48, 65):
            g = self.glyphs.get(c)
            if g:
                return max(2, g[0] // 2)
        return 6

    def close(self):
        self.glyphs.clear()
        gc.collect()


# ---------- v3 多 chunk ----------

class MultiChunkFont:
    def __init__(self, family, size, paths, storage='flash'):
        self.family = family
        self.size = size
        self.paths = list(paths)
        self.storage = storage
        self._loaded = [None] * len(paths)
        self._max_h = None
        self._is_unicode = True

    def _load_chunk(self, i):
        if self._loaded[i] is not None:
            return self._loaded[i]
        try:
            f = BitmapFont(self.paths[i])
        except (OSError, ValueError) as e:
            print("[FONT] chunk %d fail: %s" % (i, e))
            self._loaded[i] = False
            return False
        self._loaded[i] = f
        return f

    def _ensure_max_h(self):
        if self._max_h is not None:
            return
        for i in range(len(self.paths)):
            f = self._load_chunk(i)
            if f:
                self._max_h = f.max_h
                self._is_unicode = f.is_unicode
                return
        self._max_h = 0

    @property
    def max_h(self):
        self._ensure_max_h()
        return self._max_h

    @property
    def is_unicode(self):
        self._ensure_max_h()
        return self._is_unicode

    def glyph(self, code):
        for i in range(len(self.paths)):
            f = self._load_chunk(i)
            if not f:
                continue
            g = f.glyph(code)
            if g is not None:
                return g
        return None

    def advance(self, code):
        for i in range(len(self.paths)):
            f = self._load_chunk(i)
            if not f:
                continue
            w = f.advance(code)
            if w is not None:
                return w
        return None

    def space_width(self):
        for i in range(len(self.paths)):
            f = self._load_chunk(i)
            if not f:
                continue
            w = f.space_width()
            if w > 0:
                return w
        return 6

    def close(self):
        for f in self._loaded:
            if f:
                try: f.close()
                except: pass
        self._loaded = [None] * len(self.paths)
        gc.collect()


# ---------- v4 单文件 ----------

class V4Font:
    def __init__(self, path, cache_size=2):
        self.path = path
        self.max_h = 0
        self.is_unicode = True
        self._fh = None
        self._cache = {}
        self._lru = []
        self.cache_size = cache_size
        self._count = 0
        self._index_raw = None

        with open(path, "rb") as f:
            header = f.read(6)
            if len(header) < 6:
                raise ValueError("v4 头不完整")
            if header[0] != FONT_VERSION_V4:
                raise ValueError("不是 v4: %d" % header[0])
            flags = header[1]
            count = header[2] | (header[3] << 8)
            self.max_h = header[4] | (header[5] << 8)
            self.is_unicode = bool(flags & 0x01)
            self._count = count
            index_data = f.read(count * 6)
            if len(index_data) < count * 6:
                raise ValueError("v4 索引不完整")
            self._index_raw = index_data

    def _find(self, code):
        raw = self._index_raw
        lo, hi = 0, self._count - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            base = mid * 6
            c = (raw[base] << 8) | raw[base + 1]
            if c == code:
                off = (raw[base+2] << 16) | (raw[base+3] << 8) | raw[base+4]
                return (off, raw[base+5])
            elif c < code:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def advance(self, code):
        e = self._find(code)
        return None if e is None else e[1]

    def _ensure_fh(self):
        if self._fh is None:
            self._fh = open(self.path, 'rb')
        return self._fh

    def _read_glyph(self, code):
        entry = self._find(code)
        if entry is None:
            return None
        offset, w = entry
        if w == 0:
            return (0, 0, b"")
        fh = self._ensure_fh()
        rb = (w + 7) // 8
        size = rb * self.max_h
        try:
            fh.seek(offset)
            data = fh.read(size)
        except Exception as e:
            print("[V4] read fail %d: %s" % (code, e))
            return None
        if len(data) < size:
            return None
        return (w, self.max_h, data)

    def glyph(self, code):
        if code in self._cache:
            if code in self._lru:
                self._lru.remove(code)
            self._lru.append(code)
            return self._cache[code]
        g = self._read_glyph(code)
        if g is None:
            return None
        self._cache[code] = g
        self._lru.append(code)
        while len(self._lru) > self.cache_size:
            evict = self._lru.pop(0)
            del self._cache[evict]
        return g

    def space_width(self):
        g = self.glyph(32)
        if g and g[0] > 0:
            return g[0]
        return max(2, self.max_h // 4)

    def clear_cache(self):
        self._cache.clear()
        self._lru.clear()
        gc.collect()

    def close(self):
        self.clear_cache()
        if self._fh is not None:
            try: self._fh.close()
            except: pass
            self._fh = None


# ---------- v4 多 chunk ----------

class MultiChunkV4Font:
    def __init__(self, paths, cache_size=64, max_index_cache=4):
        self.paths = list(paths)
        self.max_h = 0
        self.is_unicode = True
        self._handles = [None] * len(paths)
        self._cache = {}
        self._lru = []
        self.cache_size = cache_size

        self._meta_codes = bytearray()
        self._meta_ci = bytearray()
        self._meta_n = 0

        self._index_cache = {}
        self._index_lru = []
        self.max_index_cache = max_index_cache

        for ci, path in enumerate(paths):
            try:
                with open(path, "rb") as f:
                    header = f.read(6)
                    if len(header) < 6:
                        continue
                    if header[0] != FONT_VERSION_V4:
                        continue
                    flags = header[1]
                    count = header[2] | (header[3] << 8)
                    mh = header[4] | (header[5] << 8)
                    if ci == 0:
                        self.max_h = mh
                        self.is_unicode = bool(flags & 0x01)
                    if count == 0:
                        continue
                    first = f.read(6)
                    if len(first) < 6:
                        continue
                    min_code = (first[0] << 8) | first[1]
                    f.seek(6 + (count - 1) * 6)
                    last = f.read(6)
                    if len(last) < 6:
                        continue
                    max_code = (last[0] << 8) | last[1]
                    self._meta_codes.extend(bytes([
                        (min_code >> 8) & 0xFF, min_code & 0xFF,
                        (max_code >> 8) & 0xFF, max_code & 0xFF,
                    ]))
                    self._meta_ci.append(ci & 0xFF)
                    self._meta_n += 1
            except Exception as e:
                print("[V4C] chunk %d init fail: %s" % (ci, e))

    def _find_chunk_for(self, code):
        lo, hi = 0, self._meta_n - 1
        mc = self._meta_codes
        while lo <= hi:
            mid = (lo + hi) // 2
            base = mid * 4
            min_code = (mc[base] << 8) | mc[base + 1]
            max_code = (mc[base + 2] << 8) | mc[base + 3]
            if code < min_code:
                hi = mid - 1
            elif code > max_code:
                lo = mid + 1
            else:
                return self._meta_ci[mid]
        return None

    def _get_chunk_index(self, ci):
        if ci in self._index_cache:
            if ci in self._index_lru:
                self._index_lru.remove(ci)
            self._index_lru.append(ci)
            return self._index_cache[ci]
        try:
            with open(self.paths[ci], "rb") as f:
                header = f.read(6)
                if len(header) < 6:
                    return None
                count = header[2] | (header[3] << 8)
                raw = f.read(count * 6)
                if len(raw) < count * 6:
                    return None
        except Exception as e:
            print("[V4C] idx %d fail: %s" % (ci, e))
            return None
        self._index_cache[ci] = raw
        self._index_lru.append(ci)
        while len(self._index_lru) > self.max_index_cache:
            evict = self._index_lru.pop(0)
            if evict in self._index_cache:
                del self._index_cache[evict]
        return raw

    def _search_in_index(self, raw, code):
        count = len(raw) // 6
        lo, hi = 0, count - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            base = mid * 6
            c = (raw[base] << 8) | raw[base + 1]
            if c == code:
                off = (raw[base+2] << 16) | (raw[base+3] << 8) | raw[base+4]
                return (off, raw[base+5])
            elif c < code:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def advance(self, code):
        ci = self._find_chunk_for(code)
        if ci is None:
            return None
        idx = self._get_chunk_index(ci)
        if idx is None:
            return None
        e = self._search_in_index(idx, code)
        return None if e is None else e[1]

    def _get_handle(self, ci):
        if self._handles[ci] is None:
            self._handles[ci] = open(self.paths[ci], 'rb')
        return self._handles[ci]

    def _read_glyph(self, code):
        ci = self._find_chunk_for(code)
        if ci is None:
            return None
        idx = self._get_chunk_index(ci)
        if idx is None:
            return None
        entry = self._search_in_index(idx, code)
        if entry is None:
            return None
        offset, w = entry
        if w == 0:
            return (0, 0, b"")
        try:
            fh = self._get_handle(ci)
            rb = (w + 7) // 8
            size = rb * self.max_h
            fh.seek(offset)
            data = fh.read(size)
        except Exception as e:
            print("[V4C] read %d fail: %s" % (code, e))
            return None
        if len(data) < size:
            return None
        return (w, self.max_h, data)

    def glyph(self, code):
        if code in self._cache:
            if code in self._lru:
                self._lru.remove(code)
            self._lru.append(code)
            return self._cache[code]
        g = self._read_glyph(code)
        if g is None:
            return None
        self._cache[code] = g
        self._lru.append(code)
        while len(self._lru) > self.cache_size:
            evict = self._lru.pop(0)
            del self._cache[evict]
        return g

    def space_width(self):
        g = self.glyph(32)
        if g and g[0] > 0:
            return g[0]
        return max(2, self.max_h // 4)

    def clear_cache(self):
        self._cache.clear()
        self._lru.clear()
        self._index_cache.clear()
        self._index_lru.clear()
        gc.collect()

    def close(self):
        self.clear_cache()
        for i in range(len(self._handles)):
            if self._handles[i] is not None:
                try: self._handles[i].close()
                except: pass
                self._handles[i] = None


# ---------- 字体缓存 ----------

_font_cache = {}

def _evict_if_needed():
    while len(_font_cache) >= 2:
        old = next(iter(_font_cache))
        f = _font_cache.pop(old)
        try: f.close()
        except: pass
        gc.collect()

def _detect_version(path):
    try:
        with open(path, "rb") as f:
            h = f.read(1)
        return h[0] if h else 0
    except Exception:
        return 0

def _create_font_direct(font_paths):
    if not font_paths:
        return None
    ver = _detect_version(font_paths[0])
    try:
        if ver == FONT_VERSION_V4:
            return MultiChunkV4Font(font_paths)
        else:
            return MultiChunkFont("?", 0, font_paths)
    except Exception as e:
        print("[FONT] create fail: %s" % e)
        return None

def get_font(font_path):
    key = "single:" + font_path
    if key in _font_cache:
        return _font_cache[key]
    ver = _detect_version(font_path)
    if ver == FONT_VERSION_V4:
        try:
            font = V4Font(font_path)
        except Exception as e:
            print("[FONT] v4 fail %s: %s" % (font_path, e))
            return None
    else:
        try:
            font = BitmapFont(font_path)
        except (OSError, ValueError) as e:
            print("[FONT] fail %s: %s" % (font_path, e))
            return None
    _evict_if_needed()
    _font_cache[key] = font
    return font

def get_multi_font(paths, family=None, size=0, storage='flash'):
    key = "multi:" + "|".join(paths)
    if key in _font_cache:
        return _font_cache[key]
    ver = _detect_version(paths[0]) if paths else 0
    if ver == FONT_VERSION_V4:
        font = MultiChunkV4Font(paths)
    else:
        font = MultiChunkFont(family or "?", size, paths, storage)
    _evict_if_needed()
    _font_cache[key] = font
    return font

def clear_font_cache():
    for f in _font_cache.values():
        try: f.close()
        except: pass
    _font_cache.clear()
    gc.collect()

def _resolve_font(font_path=None, font_paths=None, size=32):
    if font_paths:
        return get_multi_font(font_paths, size=size)
    if font_path:
        return get_font(font_path)
    return get_font("/www/fonts/font_%d.bin" % size)


# ---------- 码点 / 宽度 ----------

def _char_code(font, ch):
    if font.is_unicode:
        return ord(ch)
    code = ord(ch)
    if code > 0xFF:
        return None
    return code

def _char_advance(font, ch):
    code = _char_code(font, ch)
    if code is None:
        return font.space_width()
    w = font.advance(code)
    if w is None:
        return font.space_width()
    return w

def _glyph_of(font, ch):
    code = _char_code(font, ch)
    if code is None:
        return None
    return font.glyph(code)

def list_available_sizes():
    out = []
    for s in _FONT_SIZES:
        try:
            os.stat("/www/fonts/font_%d.bin" % s)
            out.append(s)
        except OSError:
            pass
    return out


# ---------- 颜色（强制黑白） ----------

def parse_color_spec(spec, default=('solid', 0)):
    if spec is None:
        return default
    if isinstance(spec, dict):
        if spec.get("type") == "pseudo":
            return ("pseudo", int(spec.get("a", 0)), int(spec.get("b", 1)),
                    int(spec.get("density", 8)))
        return ("solid", int(spec.get("idx", 0)))
    if isinstance(spec, int):
        return ("solid", spec)
    if isinstance(spec, (list, tuple)):
        if len(spec) >= 2:
            return ("pseudo", int(spec[0]), int(spec[1]), 8)
        return ("solid", int(spec[0]) if spec else 0)
    if isinstance(spec, str):
        s = spec.strip().lower()
        if s in COLOR_NAMES:
            return ("solid", COLOR_NAMES[s])
        if '+' in s:
            return ("pseudo", 0, 1, 8) # 忽略伪色，直接变黑白
    return default

def _resolve_color(spec, x, y):
    # 强制映射为 0 (黑) 或 1 (白)
    # 原代码中：0=黑，1=白
    if spec[0] == 'solid':
        idx = spec[1]
        return 0 if idx == 0 else 1 # 只要不是纯黑，全算白
    # 伪色统一算黑
    return 0


# ---------- 样式 ----------

def _parse_default_style(default_style):
    if default_style is None:
        default_style = {}
    try:
        letter_spacing = int(round(float(default_style.get('letter_spacing', 0))))
    except (TypeError, ValueError):
        letter_spacing = 0
    letter_spacing = max(_LS_MIN, min(_LS_MAX, letter_spacing))
    return {
        'fg': parse_color_spec(default_style.get('fg', 'black')),
        'bg': parse_color_spec(default_style.get('bg', 'white')),
        'outline': (None if default_style.get('outline') is None
                    else parse_color_spec(default_style.get('outline'))),
        'bold': bool(default_style.get('bold', False)),
        'italic': bool(default_style.get('italic', False)),
        'underline': bool(default_style.get('underline', False)),
        'strikethrough': bool(default_style.get('strikethrough', False)),
        'letter_spacing': letter_spacing,
    }

def _parse_per_char(per_char, ds):
    parsed = {}
    if per_char:
        for k, v in per_char.items():
            if not isinstance(v, dict):
                continue
            m = dict(ds)
            for field, value in v.items():
                if field in ('fg', 'bg'):
                    m[field] = parse_color_spec(value)
                elif field == 'outline':
                    m[field] = None if value is None else parse_color_spec(value)
                elif field in ('bold', 'italic', 'underline', 'strikethrough'):
                    m[field] = bool(value)
                elif field == 'letterSpacing':
                    try:
                        m['letter_spacing'] = max(_LS_MIN, min(_LS_MAX,
                            int(round(float(value)))))
                    except (TypeError, ValueError):
                        pass
                elif field == 'font_paths':
                    if value:
                        m['font_paths'] = list(value)
            parsed[str(k)] = m
    return parsed

def _get_char_font(default_font, style, temp_fonts):
    fp = style.get('font_paths')
    if not fp:
        return default_font
    key = '|'.join(fp)
    f = temp_fonts.get(key)
    return f if f is not None else default_font

def _style_ls(style):
    if not isinstance(style, dict):
        return 0
    v = style.get('letter_spacing', 0)
    try:
        return max(_LS_MIN, min(_LS_MAX, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0

def _norm_line_extras(line_extras):
    out = {}
    if not line_extras:
        return out
    try:
        for k, v in line_extras.items():
            out[int(k)] = int(v)
    except (TypeError, ValueError, AttributeError):
        return {}
    return out


# ---------- 零分配 blit ----------

def _bit_on(gdata, rb, x, y, gw, gh):
    if x < 0 or x >= gw or y < 0 or y >= gh:
        return False
    return bool(gdata[y * rb + (x >> 3)] & (0x80 >> (x & 7)))

def _blit_char_to_band(idx_buf, inner_w, band_start, band_end,
                       cx, cy, glyph, style, font_h):
    gw, gh, gdata = glyph
    if gw == 0 or gh == 0:
        return
    bold = bool(style.get('bold', False))
    italic = bool(style.get('italic', False))
    outline_spec = style.get('outline')
    fg_spec = style.get('fg', ('solid', 0))
    bold_dx = 1 if bold else 0
    shear_px = max(1, font_h // 8) if italic else 0
    rb = (gw + 7) // 8
    denom = max(1, gh - 1)

    if outline_spec is not None:
        row_start = max(0, band_start - cy - 1)
        row_end = min(gh, band_end - cy + 1)
        for row_idx in range(row_start, row_end):
            py = cy + row_idx
            if py < band_start or py >= band_end:
                continue
            if 0 <= row_idx < gh:
                offset = ((gh - 1 - row_idx) * shear_px) // denom if shear_px else 0
            else:
                offset = 0
            x_disp_start = -1
            x_disp_end = gw + 1 + bold_dx
            y_local = py - band_start
            row_off = y_local * inner_w
            for x_disp in range(x_disp_start, x_disp_end):
                px = cx + offset + x_disp
                if px < 0 or px >= inner_w:
                    continue
                if _bit_on(gdata, rb, x_disp, row_idx, gw, gh):
                    continue
                if bold_dx and _bit_on(gdata, rb, x_disp - bold_dx, row_idx, gw, gh):
                    continue
                is_neighbor = False
                for dy in (-1, 0, 1):
                    if is_neighbor: break
                    ny_row = row_idx + dy
                    if ny_row < 0 or ny_row >= gh:
                        continue
                    off2 = ((gh - 1 - ny_row) * shear_px) // denom if shear_px else 0
                    rel_x = x_disp + offset - off2
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        if _bit_on(gdata, rb, rel_x + dx, ny_row, gw, gh):
                            is_neighbor = True
                            break
                        if bold_dx and _bit_on(gdata, rb, rel_x + dx - bold_dx, ny_row, gw, gh):
                            is_neighbor = True
                            break
                    if is_neighbor: break
                if is_neighbor:
                    idx_buf[row_off + px] = _resolve_color(outline_spec, px, py)

    for row_idx in range(gh):
        py = cy + row_idx
        if py < band_start or py >= band_end:
            continue
        offset = ((gh - 1 - row_idx) * shear_px) // denom if shear_px else 0
        base = row_idx * rb
        y_local = py - band_start
        row_off = y_local * inner_w
        for bit in range(gw):
            if gdata[base + (bit >> 3)] & (0x80 >> (bit & 7)):
                px = cx + offset + bit
                if 0 <= px < inner_w:
                    idx_buf[row_off + px] = _resolve_color(fg_spec, px, py)
                if bold_dx:
                    px2 = px + bold_dx
                    if 0 <= px2 < inner_w:
                        idx_buf[row_off + px2] = _resolve_color(fg_spec, px2, py)

def _blit_char_to_band_transposed(idx_buf, inner_h, band_start, band_end_x,
                                   cx, cy, glyph, style, font_h):
    gw, gh, gdata = glyph
    if gw == 0 or gh == 0:
        return
    bold = bool(style.get('bold', False))
    italic = bool(style.get('italic', False))
    outline_spec = style.get('outline')
    fg_spec = style.get('fg', ('solid', 0))
    bold_dx = 1 if bold else 0
    shear_px = max(1, font_h // 8) if italic else 0
    rb = (gw + 7) // 8
    denom = max(1, gh - 1)

    if outline_spec is not None:
        for row_idx in range(gh):
            py = cy + row_idx
            if py < 0 or py >= inner_h:
                continue
            offset = ((gh - 1 - row_idx) * shear_px) // denom if shear_px else 0
            col_base_start = cx + offset
            for x_disp in range(-1, gw + 1 + bold_dx):
                px = col_base_start + x_disp
                if px < band_start or px >= band_end_x:
                    continue
                if _bit_on(gdata, rb, x_disp, row_idx, gw, gh):
                    continue
                if bold_dx and _bit_on(gdata, rb, x_disp - bold_dx, row_idx, gw, gh):
                    continue
                is_neighbor = False
                for dy in (-1, 0, 1):
                    if is_neighbor: break
                    ny_row = row_idx + dy
                    if ny_row < 0 or ny_row >= gh:
                        continue
                    off2 = ((gh - 1 - ny_row) * shear_px) // denom if shear_px else 0
                    rel_x = x_disp + offset - off2
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        if _bit_on(gdata, rb, rel_x + dx, ny_row, gw, gh):
                            is_neighbor = True
                            break
                        if bold_dx and _bit_on(gdata, rb, rel_x + dx - bold_dx, ny_row, gw, gh):
                            is_neighbor = True
                            break
                    if is_neighbor: break
                if is_neighbor:
                    col = px - band_start
                    idx_buf[col * inner_h + py] = _resolve_color(outline_spec, px, py)

    for row_idx in range(gh):
        py = cy + row_idx
        if py < 0 or py >= inner_h:
            continue
        offset = ((gh - 1 - row_idx) * shear_px) // denom if shear_px else 0
        base = row_idx * rb
        for bit in range(gw):
            if gdata[base + (bit >> 3)] & (0x80 >> (bit & 7)):
                px = cx + offset + bit
                if band_start <= px < band_end_x:
                    col = px - band_start
                    idx_buf[col * inner_h + py] = _resolve_color(fg_spec, px, py)
                if bold_dx:
                    px2 = px + bold_dx
                    if band_start <= px2 < band_end_x:
                        col2 = px2 - band_start
                        idx_buf[col2 * inner_h + py] = _resolve_color(fg_spec, px2, py)

# ---------- 旋转 ----------

def _rotate(buf, w, h, direction):
    if direction == 0:
        return (buf, w, h)
    if direction == 90:
        nw, nh = h, w
        out = bytearray(nw * nh)
        for y in range(h):
            for x in range(w):
                out[x * nw + (h - 1 - y)] = buf[y * w + x]
        return (out, nw, nh)
    if direction == 180:
        nw, nh = w, h
        out = bytearray(nw * nh)
        for y in range(h):
            for x in range(w):
                out[(h - 1 - y) * nw + (w - 1 - x)] = buf[y * w + x]
        return (out, nw, nh)
    if direction == 270:
        nw, nh = h, w
        out = bytearray(nw * nh)
        for y in range(h):
            for x in range(w):
                out[(w - 1 - x) * nw + y] = buf[y * w + x]
        return (out, nw, nh)
    return (buf, w, h)

# ---------- 打包 1bpp ----------

def _pack_row(idx_row):
    """将含有 SENTINEL(0xFF) 的索引行打包为 1bpp (1=黑, 0=白)"""
    n = len(idx_row)
    out = bytearray((n + 7) // 8)
    for i in range(n):
        val = idx_row[i]
        # 忽略 SENTINEL (0xFF) 或 1 (白)，其他颜色都算黑 (0)
        if val != SENTINEL and val != 1:
            out[i >> 3] |= (0x80 >> (i & 7))
    return bytes(out)

def _compute_line_metrics(text, font, per_char, ds, inner_w, inner_h,
                          align, valign, line_height, mt, mb, ml, mr,
                          temp_fonts, line_extras=None):
    lines_text = text.split('\n') if text else ['']
    lh_factor = float(line_height)
    extras = _norm_line_extras(line_extras)
    line_infos = []
    char_idx = 0
    total_h = 0
    for li, line in enumerate(lines_text):
        line_w = 0
        line_max_h = font.max_h
        for cii, ch in enumerate(line):
            style = per_char.get(str(char_idx + cii), ds)
            cf = _get_char_font(font, style, temp_fonts)
            line_w += _char_advance(cf, ch) + _style_ls(style)
            if cf.max_h > line_max_h:
                line_max_h = cf.max_h
        lh = max(0, int(line_max_h * lh_factor))
        if li in extras:
            lh = max(0, lh + extras[li])
        line_infos.append({
            'start': char_idx, 'count': len(line),
            'line_w': line_w, 'max_h': line_max_h, 'lh': lh,
        })
        char_idx += len(line)
        total_h += lh
    avail_h = inner_h - mt - mb
    if valign == 'center':
        cy0 = mt + max(0, (avail_h - total_h) // 2)
    elif valign == 'bottom':
        cy0 = mt + max(0, avail_h - total_h)
    else:
        cy0 = mt
    cy = cy0
    for info in line_infos:
        if align == 'center':
            cx = ml + max(0, (inner_w - ml - mr - info['line_w']) // 2)
        elif align == 'right':
            cx = inner_w - mr - info['line_w']
        else:
            cx = ml
        info['cx0'] = cx
        info['cy'] = cy
        cy += info['lh']
    return line_infos, lines_text

def _draw_line_to_band(idx_buf, inner_w, band_start, band_end,
                       line_text, line_info, per_char, ds, font, temp_fonts):
    cy = line_info['cy']
    max_h = line_info['max_h']
    base_idx = line_info['start']
    cx = line_info['cx0']
    for cii, ch in enumerate(line_text):
        gidx = base_idx + cii
        style = per_char.get(str(gidx), ds) if per_char else ds
        cf = _get_char_font(font, style, temp_fonts)
        g = _glyph_of(cf, ch)
        adv = g[0] if g and g[0] > 0 else cf.space_width()
        ls = _style_ls(style)
        char_top = cy + (max_h - cf.max_h)
        if g is not None and g[0] > 0:
            _blit_char_to_band(idx_buf, inner_w, band_start, band_end,
                                cx, char_top, g, style, cf.max_h)
        fg = style.get('fg', ('solid', 0))
        if style.get('underline'):
            ul_y = char_top + cf.max_h - 2
            if band_start <= ul_y < band_end:
                y_local = ul_y - band_start
                base = y_local * inner_w
                for x in range(cx, min(cx + adv, inner_w)):
                    if x >= 0:
                        idx_buf[base + x] = _resolve_color(fg, x, ul_y)
        if style.get('strikethrough'):
            st_y = char_top + cf.max_h // 2
            if band_start <= st_y < band_end:
                y_local = st_y - band_start
                base = y_local * inner_w
                for x in range(cx, min(cx + adv, inner_w)):
                    if x >= 0:
                        idx_buf[base + x] = _resolve_color(fg, x, st_y)
        cx += adv + ls

def _draw_line_to_band_180(idx_buf, out_w, band_start, band_end,
                            line_text, line_info, per_char, ds, font, temp_fonts):
    cy = line_info['cy']
    max_h = line_info['max_h']
    base_idx = line_info['start']
    cx = line_info['cx0']
    def mx(px): return W - 1 - px
    def my(py): return H - 1 - py
    for cii, ch in enumerate(line_text):
        gidx = base_idx + cii
        style = per_char.get(str(gidx), ds) if per_char else ds
        cf = _get_char_font(font, style, temp_fonts)
        g = _glyph_of(cf, ch)
        adv = g[0] if g and g[0] > 0 else cf.space_width()
        ls = _style_ls(style)
        char_top = cy + (max_h - cf.max_h)
        fg = style.get('fg', ('solid', 0))
        if g is not None and g[0] > 0:
            gw, gh, gdata = g
            bold = bool(style.get('bold', False))
            italic = bool(style.get('italic', False))
            outline_spec = style.get('outline')
            bold_dx = 1 if bold else 0
            shear_px = max(1, cf.max_h // 8) if italic else 0
            rb = (gw + 7) // 8
            denom = max(1, gh - 1)
            if outline_spec is not None:
                for row_idx in range(gh):
                    py = char_top + row_idx
                    if py < 0 or py >= H:
                        continue
                    offset = ((gh - 1 - row_idx) * shear_px) // denom if shear_px else 0
                    for x_disp in range(-1, gw + 1 + bold_dx):
                        px = cx + offset + x_disp
                        if px < 0 or px >= W:
                            continue
                        if _bit_on(gdata, rb, x_disp, row_idx, gw, gh):
                            continue
                        if bold_dx and _bit_on(gdata, rb, x_disp - bold_dx, row_idx, gw, gh):
                            continue
                        is_nb = False
                        for dy in (-1, 0, 1):
                            if is_nb: break
                            nr = row_idx + dy
                            if nr < 0 or nr >= gh: continue
                            off2 = ((gh - 1 - nr) * shear_px) // denom if shear_px else 0
                            rel_x = x_disp + offset - off2
                            for dx in (-1, 0, 1):
                                if dx == 0 and dy == 0: continue
                                if _bit_on(gdata, rb, rel_x + dx, nr, gw, gh):
                                    is_nb = True; break
                                if bold_dx and _bit_on(gdata, rb, rel_x + dx - bold_dx, nr, gw, gh):
                                    is_nb = True; break
                            if is_nb: break
                        if is_nb:
                            rx, ry = mx(px), my(py)
                            if band_start <= ry < band_end and 0 <= rx < out_w:
                                y_local = ry - band_start
                                idx_buf[y_local * out_w + rx] = _resolve_color(outline_spec, px, py)
            for row_idx in range(gh):
                py = char_top + row_idx
                if py < 0 or py >= H:
                    continue
                offset = ((gh - 1 - row_idx) * shear_px) // denom if shear_px else 0
                base = row_idx * rb
                for bit in range(gw):
                    if gdata[base + (bit >> 3)] & (0x80 >> (bit & 7)):
                        px = cx + offset + bit
                        if 0 <= px < W:
                            rx, ry = mx(px), my(py)
                            if band_start <= ry < band_end and 0 <= rx < out_w:
                                y_local = ry - band_start
                                idx_buf[y_local * out_w + rx] = _resolve_color(fg, px, py)
                        if bold_dx:
                            px2 = px + bold_dx
                            if 0 <= px2 < W:
                                rx2, ry = mx(px2), my(py)
                                if band_start <= ry < band_end and 0 <= rx2 < out_w:
                                    y_local = ry - band_start
                                    idx_buf[y_local * out_w + rx2] = _resolve_color(fg, px2, py)
        if style.get('underline'):
            ul_in = char_top + cf.max_h - 2
            ul_out = my(ul_in)
            if band_start <= ul_out < band_end:
                y_local = ul_out - band_start
                base = y_local * out_w
                for x_in in range(cx, cx + adv):
                    if 0 <= x_in < W:
                        x_out = mx(x_in)
                        if 0 <= x_out < out_w:
                            idx_buf[base + x_out] = _resolve_color(fg, x_in, ul_in)
        if style.get('strikethrough'):
            st_in = char_top + cf.max_h // 2
            st_out = my(st_in)
            if band_start <= st_out < band_end:
                y_local = st_out - band_start
                base = y_local * out_w
                for x_in in range(cx, cx + adv):
                    if 0 <= x_in < W:
                        x_out = mx(x_in)
                        if 0 <= x_out < out_w:
                            idx_buf[base + x_out] = _resolve_color(fg, x_in, st_in)
        cx += adv + ls

def _draw_line_to_text_idx(idx_buf, inner_h, band_start, band_end_x,
                            line_text, line_info, per_char, ds, font, temp_fonts):
    cy = line_info['cy']
    max_h = line_info['max_h']
    base_idx = line_info['start']
    cx = line_info['cx0']
    for cii, ch in enumerate(line_text):
        gidx = base_idx + cii
        style = per_char.get(str(gidx), ds) if per_char else ds
        cf = _get_char_font(font, style, temp_fonts)
        g = _glyph_of(cf, ch)
        adv = g[0] if g and g[0] > 0 else cf.space_width()
        ls = _style_ls(style)
        char_top = cy + (max_h - cf.max_h)
        if cx + adv <= band_start or cx >= band_end_x:
            cx += adv + ls
            continue
        if g is not None and g[0] > 0:
            _blit_char_to_band_transposed(idx_buf, inner_h, band_start, band_end_x,
                                           cx, char_top, g, style, cf.max_h)
        fg = style.get('fg', ('solid', 0))
        if style.get('underline'):
            ul_y = char_top + cf.max_h - 2
            if 0 <= ul_y < inner_h:
                for px in range(cx, cx + adv):
                    if band_start <= px < band_end_x:
                        col = px - band_start
                        idx_buf[col * inner_h + ul_y] = _resolve_color(fg, px, ul_y)
        if style.get('strikethrough'):
            st_y = char_top + cf.max_h // 2
            if 0 <= st_y < inner_h:
                for px in range(cx, cx + adv):
                    if band_start <= px < band_end_x:
                        col = px - band_start
                        idx_buf[col * inner_h + st_y] = _resolve_color(fg, px, st_y)
        cx += adv + ls


def _stream_direct(epd, direction, line_infos, lines_text, font, per_char,
                   ds, band_rows, temp_fonts):
    # 直接写入 epd.buffer
    buf_offset = 0
    for band_start in range(0, H, band_rows):
        band_end = min(band_start + band_rows, H)
        band_h = band_end - band_start
        idx_buf = _make_bytearray_fill(band_h * W, SENTINEL)
        for li, info in enumerate(line_infos):
            cy = info['cy']
            if cy + info['lh'] <= band_start or cy >= band_end:
                continue
            line_text = lines_text[li]
            if direction == 0:
                _draw_line_to_band(idx_buf, W, band_start, band_end,
                                    line_text, info, per_char, ds, font, temp_fonts)
            else:
                _draw_line_to_band_180(idx_buf, W, band_start, band_end,
                                        line_text, info, per_char, ds, font, temp_fonts)
        for y_local in range(band_h):
            base = y_local * W
            idx_row = idx_buf[base:base + W]
            packed_row = _pack_row(idx_row)
            epd.buffer[buf_offset:buf_offset+len(packed_row)] = packed_row
            buf_offset += len(packed_row)
        del idx_buf
        gc.collect()

def _stream_rotated(epd, direction, inner_w, inner_h,
                     line_infos, lines_text, font, per_char,
                     ds, band_rows, temp_fonts):
    buf_offset = 0
    for out_y_start in range(0, H, band_rows):
        out_y_end = min(out_y_start + band_rows, H)
        out_h = out_y_end - out_y_start
        if direction == 90:
            inner_col_start = out_y_start
            inner_col_end = out_y_end
        else:
            inner_col_start = inner_w - out_y_end
            inner_col_end = inner_w - out_y_start
        band_cols = inner_col_end - inner_col_start
        text_idx = _make_bytearray_fill(band_cols * inner_h, SENTINEL)
        for li, info in enumerate(line_infos):
            _draw_line_to_text_idx(text_idx, inner_h, inner_col_start, inner_col_end,
                                    lines_text[li], info, per_char, ds, font, temp_fonts)
        for i in range(out_h):
            out_y = out_y_start + i
            if direction == 90:
                inner_x = out_y
            else:
                inner_x = inner_w - 1 - out_y
            col_local = inner_x - inner_col_start
            if col_local < 0 or col_local >= band_cols:
                col_local = max(0, min(band_cols - 1, col_local))
            text_base = col_local * inner_h
            out_row = bytearray(W)
            if direction == 90:
                for out_x in range(W):
                    y_in = inner_h - 1 - out_x
                    tv = text_idx[text_base + y_in]
                    out_row[out_x] = tv if tv != SENTINEL else 1 # 1 为白底
            else:
                for out_x in range(W):
                    tv = text_idx[text_base + out_x]
                    out_row[out_x] = tv if tv != SENTINEL else 1
            packed_row = _pack_row(out_row)
            epd.buffer[buf_offset:buf_offset+len(packed_row)] = packed_row
            buf_offset += len(packed_row)
        del text_idx
        gc.collect()


def render_and_display_stream(epd, text, size=32, direction=0, per_char=None,
                               default_style=None, align='left', valign='top',
                               line_height=1.5, margin_top=20, margin_bottom=20,
                               margin_left=20, margin_right=20,
                               band_rows=8, font_path=None, font_paths=None,
                               extra_fonts=None, line_extras=None, **kwargs):
    gc.collect()
    font = _resolve_font(font_path=font_path, font_paths=font_paths, size=size)
    if font is None:
        raise ValueError("字模加载失败")
    ds = _parse_default_style(default_style)
    pc = _parse_per_char(per_char, ds)
    if direction in (90, 270):
        inner_w, inner_h = H, W
    else:
        inner_w, inner_h = W, H
    temp_fonts = extra_fonts if extra_fonts is not None else {}

    print("[TEXT] pass1 lines…")
    line_infos, lines_text = _compute_line_metrics(
        text, font, pc, ds, inner_w, inner_h,
        align, valign, line_height,
        margin_top, margin_bottom, margin_left, margin_right,
        temp_fonts, line_extras=line_extras)
    gc.collect()

    print("[TEXT] stream start: free=%d, lines=%d, dir=%d, band=%d"
          % (gc.mem_free(), len(line_infos), direction, band_rows))

    # 清空屏幕缓存
    for i in range(0, len(epd.buffer), 1024):
        epd.buffer[i:i+1024] = b'\x00' * min(1024, len(epd.buffer) - i)

    while band_rows > 1:
        try:
            probe = bytearray(band_rows * max(inner_w, W))
            del probe
            gc.collect()
            break
        except MemoryError:
            new_rows = max(1, band_rows // 2)
            print("[TEXT] band %d->%d" % (band_rows, new_rows))
            band_rows = new_rows
            gc.collect()

    try:
        if direction == 0 or direction == 180:
            _stream_direct(epd, direction, line_infos, lines_text,
                           font, pc, ds, band_rows, temp_fonts)
        else:
            _stream_rotated(epd, direction, inner_w, inner_h,
                            line_infos, lines_text, font, pc,
                            ds, band_rows, temp_fonts)
    finally:
        epd.show()
        gc.collect()
        print("[TEXT] stream done: free=%d" % gc.mem_free())

def render_and_display_bw(epd, text, size=32, **kwargs):
    render_and_display_stream(epd, text, size=size, **kwargs)