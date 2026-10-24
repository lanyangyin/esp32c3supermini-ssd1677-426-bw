# text_renderer/fonts.py — 字模解析、加载与缓存管理

import gc
import os

FONT_VERSION_V2 = 0x02
FONT_VERSION_V3 = 0x03
FONT_VERSION_V4 = 0x04

_FONT_SIZES = (12, 16, 20, 24, 32, 48)


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
                w = wh[0];
                h = wh[1]
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
                try:
                    f.close()
                except:
                    pass
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
                off = (raw[base + 2] << 16) | (raw[base + 3] << 8) | raw[base + 4]
                return (off, raw[base + 5])
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
            try:
                self._fh.close()
            except:
                pass
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
                off = (raw[base + 2] << 16) | (raw[base + 3] << 8) | raw[base + 4]
                return (off, raw[base + 5])
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
                try:
                    self._handles[i].close()
                except:
                    pass
                self._handles[i] = None


# ---------- 字体缓存 ----------

_font_cache = {}


def _evict_if_needed():
    while len(_font_cache) >= 2:
        old = next(iter(_font_cache))
        f = _font_cache.pop(old)
        try:
            f.close()
        except:
            pass
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
        try:
            f.close()
        except:
            pass
    _font_cache.clear()
    gc.collect()


def list_available_sizes():
    """扫描可用的字号（安全检测 SD 卡是否存在）"""
    out = []

    # 优先检查 Flash
    for s in _FONT_SIZES:
        try:
            os.stat("/www/fonts/font_%d.bin" % s)
            out.append(s)
        except OSError:
            pass

    # 如果 SD 卡挂载了，也扫描 SD 卡
    sd_mounted = False
    try:
        os.stat("/sd")
        sd_mounted = True
    except OSError:
        pass

    if sd_mounted:
        for s in _FONT_SIZES:
            try:
                os.stat("/sd/fonts/font_%d.bin" % s)
                if s not in out:
                    out.append(s)
            except OSError:
                pass

    return sorted(list(set(out)))