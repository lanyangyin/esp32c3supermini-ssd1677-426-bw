"""
gen_font.py — 生成 E6 墨水屏字模（v3 chunk / v4 索引）+ 字符清单 + 顶部索引

v4 默认按 --max-chunk-kb 切分（默认 100KB），同时生成 .idx 顶部索引（JSON），
前端可据此懒加载 chunk，不会一次性下载整字体。

新增：
  --chars "一串字符"   直接传字符串作为字符集（叠加到 --charset）
  --charset=none       只用 --chars / --file / --extra，不加载内置字符集
  --check "一串字符"   只检查能否渲染，不生成
  --check-file 文件名  从 UTF-8 文件读取检查

示例：
  # 检查 ∠ ・ ⌒ 是否支持
  python gen_font.py SmileySans-Oblique.ttf --check "∠・⌒"

  # 生成 ASCII + 额外三个字符
  python gen_font.py SmileySans-Oblique.ttf --charset=ascii --chars "∠・⌒" --sizes=32

  # 只生成指定字符串
  python gen_font.py SmileySans-Oblique.ttf --charset=none --chars "Hello 世界" --sizes=32
"""
import sys
import argparse
import time
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

VERSION_V3 = 0x03
VERSION_V4 = 0x04
OUT_DIR = Path("esp/www/fonts")

ALL_SIZES = [8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 56, 64, 72, 96]
DEFAULT_SIZES = ",".join(str(s) for s in ALL_SIZES)

SIZE_KEYWORDS = {
    "all":        ALL_SIZES,
    "reasonable": [16, 24, 32],
    "mini":       [16, 24],
    "small":      [12, 16, 20, 24],
    "big":        [32, 48, 64],
}

DEFAULT_V4_CHUNK_KB = 100
DEFAULT_V3_CHUNK_KB = 19
INSPECT_WARN_CHARS = 5000


# ---------- Unicode 块表（常用部分） ----------

UNICODE_BLOCKS = [
    (0x0000, 0x007F, "Basic Latin"),
    (0x0080, 0x00FF, "Latin-1 Supplement"),
    (0x0100, 0x017F, "Latin Extended-A"),
    (0x0180, 0x024F, "Latin Extended-B"),
    (0x0250, 0x02AF, "IPA Extensions"),
    (0x02B0, 0x02FF, "Spacing Modifier Letters"),
    (0x0300, 0x036F, "Combining Diacritical Marks"),
    (0x0370, 0x03FF, "Greek and Coptic"),
    (0x0400, 0x04FF, "Cyrillic"),
    (0x0500, 0x052F, "Cyrillic Supplement"),
    (0x0530, 0x058F, "Armenian"),
    (0x0590, 0x05FF, "Hebrew"),
    (0x0600, 0x06FF, "Arabic"),
    (0x0700, 0x074F, "Syriac"),
    (0x0900, 0x097F, "Devanagari"),
    (0x0E00, 0x0E7F, "Thai"),
    (0x1E00, 0x1EFF, "Latin Extended Additional"),
    (0x1F00, 0x1FFF, "Greek Extended"),
    (0x2000, 0x206F, "General Punctuation"),
    (0x2070, 0x209F, "Superscripts and Subscripts"),
    (0x20A0, 0x20CF, "Currency Symbols"),
    (0x20D0, 0x20FF, "Combining Diacritical Marks for Symbols"),
    (0x2100, 0x214F, "Letterlike Symbols"),
    (0x2150, 0x218F, "Number Forms"),
    (0x2190, 0x21FF, "Arrows"),
    (0x2200, 0x22FF, "Mathematical Operators"),
    (0x2300, 0x23FF, "Miscellaneous Technical"),
    (0x2400, 0x243F, "Control Pictures"),
    (0x2440, 0x245F, "Optical Character Recognition"),
    (0x2460, 0x24FF, "Enclosed Alphanumerics"),
    (0x2500, 0x257F, "Box Drawing"),
    (0x2580, 0x259F, "Block Elements"),
    (0x25A0, 0x25FF, "Geometric Shapes"),
    (0x2600, 0x26FF, "Miscellaneous Symbols"),
    (0x2700, 0x27BF, "Dingbats"),
    (0x27C0, 0x27EF, "Miscellaneous Mathematical Symbols-A"),
    (0x27F0, 0x27FF, "Supplemental Arrows-A"),
    (0x2800, 0x28FF, "Braille Patterns"),
    (0x2900, 0x297F, "Supplemental Arrows-B"),
    (0x2980, 0x29FF, "Miscellaneous Mathematical Symbols-B"),
    (0x2A00, 0x2AFF, "Supplemental Mathematical Operators"),
    (0x2E80, 0x2EFF, "CJK Radicals Supplement"),
    (0x2F00, 0x2FDF, "Kangxi Radicals"),
    (0x2FF0, 0x2FFF, "Ideographic Description Characters"),
    (0x3000, 0x303F, "CJK Symbols and Punctuation"),
    (0x3040, 0x309F, "Hiragana"),
    (0x30A0, 0x30FF, "Katakana"),
    (0x3100, 0x312F, "Bopomofo"),
    (0x3130, 0x318F, "Hangul Compatibility Jamo"),
    (0x3190, 0x319F, "Kanbun"),
    (0x31A0, 0x31BF, "Bopomofo Extended"),
    (0x31C0, 0x31EF, "CJK Strokes"),
    (0x31F0, 0x31FF, "Katakana Phonetic Extensions"),
    (0x3200, 0x32FF, "Enclosed CJK Letters and Months"),
    (0x3300, 0x33FF, "CJK Compatibility"),
    (0x3400, 0x4DBF, "CJK Unified Ideographs Extension A"),
    (0x4DC0, 0x4DFF, "Yijing Hexagram Symbols"),
    (0x4E00, 0x9FFF, "CJK Unified Ideographs"),
    (0xA000, 0xA48F, "Yi Syllables"),
    (0xA490, 0xA4CF, "Yi Radicals"),
    (0xA700, 0xA71F, "Modifier Tone Letters"),
    (0xA720, 0xA7FF, "Latin Extended-D"),
    (0xF900, 0xFAFF, "CJK Compatibility Ideographs"),
    (0xFB00, 0xFB4F, "Alphabetic Presentation Forms"),
    (0xFB50, 0xFDFF, "Arabic Presentation Forms-A"),
    (0xFE00, 0xFE0F, "Variation Selectors"),
    (0xFE10, 0xFE1F, "Vertical Forms"),
    (0xFE20, 0xFE2F, "Combining Half Marks"),
    (0xFE30, 0xFE4F, "CJK Compatibility Forms"),
    (0xFE50, 0xFE6F, "Small Form Variants"),
    (0xFE70, 0xFEFF, "Arabic Presentation Forms-B"),
    (0xFF00, 0xFFEF, "Halfwidth and Fullwidth Forms"),
    (0xFFF0, 0xFFFF, "Specials"),
    (0x10000, 0x10FFFF, "Supplementary Planes"),
]


def _unicode_block_name(code):
    for lo, hi, name in UNICODE_BLOCKS:
        if lo <= code <= hi:
            return name
    return "Unknown"


def _display_char(ch):
    code = ord(ch)
    if ch == ' ':
        return '␣'
    if ch == '\t':
        return '\\t'
    if ch == '\n':
        return '\\n'
    if ch == '\r':
        return '\\r'
    if code < 0x20 or (0x7F <= code < 0xA0):
        return '\\x%02X' % code
    if 0xD800 <= code <= 0xDFFF:
        return '?'
    return ch


# ---------- 字符集定义 ----------

CHARSET_ASCII = list(range(0x20, 0x7F))
CHARSET_BASIC = list(range(0x20, 0x100))
CHARSET_SYMBOLS = [
    0x2010, 0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D,
    0x2020, 0x2021, 0x2022, 0x2026, 0x2030, 0x2032, 0x2033,
    0x2039, 0x203A, 0x203B, 0x2044,
    0x20A3, 0x20A4, 0x20A9, 0x20AB, 0x20AC,
    0x00B0, 0x00B1, 0x00D7, 0x00F7,
    0x2202, 0x2206, 0x220F, 0x2211, 0x2212, 0x221A, 0x221B,
    0x221E, 0x222B, 0x2248, 0x2260, 0x2264, 0x2265,
    0x03B1, 0x03B2, 0x03B3, 0x03B4, 0x03B5, 0x03B8, 0x03BB,
    0x03BC, 0x03C0, 0x03C3, 0x03C9,
    0x0391, 0x0392, 0x0393, 0x0394, 0x0398, 0x039B, 0x039C,
    0x03A0, 0x03A3, 0x03A9,
    0x2190, 0x2191, 0x2192, 0x2193, 0x2194, 0x2195,
    0x2196, 0x2197, 0x2198, 0x2199, 0x21A9, 0x21AA,
    0x21B0, 0x21B1, 0x21B2, 0x21B3,
    0x25A0, 0x25A1, 0x25AA, 0x25AB,
    0x25B2, 0x25B3, 0x25BC, 0x25BD,
    0x25C6, 0x25C7, 0x25CB, 0x25CF, 0x25E6,
    0x2605, 0x2606, 0x2729, 0x272A, 0x272F, 0x2734, 0x2735,
    0x2736, 0x2737, 0x2738, 0x2739, 0x2740, 0x2741, 0x2742,
    0x2743, 0x2744, 0x2745, 0x2746, 0x2747, 0x2748, 0x2749,
    0x2660, 0x2661, 0x2662, 0x2663, 0x2664, 0x2665, 0x2666, 0x2667,
    0x2600, 0x2601, 0x2602, 0x2603, 0x260E, 0x2610, 0x2611, 0x2612,
    0x2615, 0x2618, 0x261E, 0x2620,
    0x263A, 0x263B, 0x263C,
    0x266A, 0x266B, 0x266C,
    0x2702, 0x2705, 0x2708, 0x2709, 0x270E,
    0x2713, 0x2714, 0x2717, 0x2718, 0x271D, 0x2720,
    0x2712, 0x274C, 0x274E, 0x2764,
    0x2460, 0x2461, 0x2462, 0x2463, 0x2464,
    0x2465, 0x2466, 0x2467, 0x2468, 0x2469,
    0x2474, 0x2475, 0x2476, 0x2477, 0x2478, 0x2479, 0x247A, 0x247B,
    0x2500, 0x2502, 0x250C, 0x2510, 0x2514, 0x2518,
    0x251C, 0x2524, 0x252C, 0x2534, 0x253C,
    0x2550, 0x2551, 0x2554, 0x2557, 0x255A, 0x255D,
    0x2560, 0x2563, 0x2566, 0x2569, 0x256C,
    0x2580, 0x2584, 0x2588, 0x258C, 0x2590, 0x2591, 0x2592, 0x2593,
    0x26A0, 0x26A1,
    0x2716, 0x2718,
    0x02C6, 0x02DC, 0x02C7, 0x02D8, 0x02D9, 0x02DA, 0x02DB, 0x02DD,
]
CHARSET_FULL = sorted(set(CHARSET_BASIC + CHARSET_SYMBOLS))
_FULL_SET = set(CHARSET_FULL)


def _read_ttf_codepoints(ttf_path):
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    try:
        font = TTFont(ttf_path, fontNumber=0, lazy=True)
        cmap = set()
        if "cmap" in font:
            for table in font["cmap"].tables:
                cmap.update(table.cmap.keys())
        font.close()
        if not cmap:
            return None
        result = sorted(c for c in cmap if 0 <= c <= 0xFFFF)
        return result if result else None
    except Exception as e:
        print("[警告] 读取 TTF cmap 失败: " + str(e))
        return None


def _extend_chars(codes, chars, source_label):
    """把 --chars 字符串里的字符码点追加到 codes，去重跳过非法/重复。"""
    if not chars:
        return 0
    seen = set(codes)
    added = 0
    skipped_surrogate = 0
    skipped_dup = 0
    skipped_out_of_bmp = 0
    for ch in chars:
        code = ord(ch)
        if code > 0xFFFF:
            skipped_out_of_bmp += 1
            continue
        if 0xD800 <= code <= 0xDFFF:
            skipped_surrogate += 1
            continue
        if code in seen:
            skipped_dup += 1
            continue
        seen.add(code)
        codes.append(code)
        added += 1
    msg = "[提示] %s 添加了 %d 个新码点" % (source_label, added)
    if skipped_dup:
        msg += "（重复 %d）" % skipped_dup
    if skipped_out_of_bmp:
        msg += "（超 BMP 跳过 %d）" % skipped_out_of_bmp
    if skipped_surrogate:
        msg += "（代理码点跳过 %d）" % skipped_surrogate
    print(msg)
    return added


def parse_charset(spec, ttf_path=None, extra=None, extra_range=None,
                  extra_file=None, chars=None):
    spec = (spec or "").lower().strip()

    if spec in ('none', 'text', 'literal'):
        codes = []
        print("[提示] charset=%s：仅使用 --chars / --file / --extra 指定的字符" % spec)
        if not chars and not extra_file and not extra and not extra_range:
            print("[错误] charset=%s 但没提供 --chars / --file / --extra / --range" % spec)
            raise ValueError("charset=%s 需要至少一个字符来源" % spec)
    elif spec in ('auto', 'ttf'):
        codes = _read_ttf_codepoints(ttf_path) if ttf_path else None
        if codes is None:
            print("[警告] 无法读取 TTF cmap（未安装 fonttools 或读取失败）")
            print("       回退到 full 字符集（431 字符）")
            print("       安装 fonttools: pip install fonttools")
            codes = list(CHARSET_FULL)
        else:
            print("[提示] 从 TTF cmap 读取到 " + str(len(codes)) + " 个码点")
    elif spec == 'ascii':
        codes = list(CHARSET_ASCII)
    elif spec in ('basic', 'latin1'):
        codes = list(CHARSET_BASIC)
    elif spec == 'symbols':
        codes = list(CHARSET_SYMBOLS)
    elif spec == 'full':
        codes = list(CHARSET_FULL)
    elif spec == 'cjk':
        codes = list(range(0x4E00, 0x9FFF))
        codes.extend(CHARSET_ASCII)
    else:
        raise ValueError("未知字符集: " + spec +
                         "（可选 auto/ttf/ascii/basic/symbols/full/latin1/cjk/none）")

    # 叠加 --chars
    _extend_chars(codes, chars, "--chars")

    if extra:
        before = len(codes)
        for token in extra.split(','):
            token = token.strip()
            if token:
                try:
                    codes.append(int(token, 0))
                except ValueError:
                    print("[警告] --extra 中无法解析: " + token)
        print("[提示] --extra 添加了 %d 个码点" % (len(codes) - before))

    if extra_range:
        before = len(codes)
        for rng in extra_range.split(','):
            rng = rng.strip()
            if not rng:
                continue
            lo, hi = rng.split('-')
            codes.extend(list(range(int(lo, 0), int(hi, 0) + 1)))
        print("[提示] --range 添加了 %d 个码点" % (len(codes) - before))

    if extra_file:
        p = Path(extra_file)
        txt = p.read_text(encoding='utf-8')
        added = _extend_chars(codes, txt, "--file " + extra_file)
        if added == 0:
            print("[警告] --file 里没有新的码点")

    return sorted(set(c for c in codes if 0 <= c <= 0xFFFF))


def parse_sizes(spec):
    spec = spec.strip()
    if spec.lower() in SIZE_KEYWORDS:
        return list(SIZE_KEYWORDS[spec.lower()])
    sizes = []
    for token in spec.split(','):
        token = token.strip()
        if token:
            try:
                sizes.append(int(token))
            except ValueError:
                raise ValueError("无效的 size: '" + token + "'")
    return sizes


# ---------- 字形渲染（关键：用绘制区域顶部作为数据第 0 行） ----------

def _render_glyph(font, ch, size, ascent, total_h):
    big = Image.new("L", (size * 4, size * 4), 0)
    draw = ImageDraw.Draw(big)
    baseline_y = size * 2
    glyph_top_y = baseline_y - ascent
    try:
        draw.text((size, glyph_top_y), ch, font=font, fill=255)
    except Exception:
        return (0, b"")
    bbox = big.getbbox()
    if not bbox:
        return (0, b"")
    x0, y0, x1, y1 = bbox
    w = x1 - x0
    if w == 0 or w > 250:
        return (0, b"")
    rb = (w + 7) // 8
    data = bytearray(rb * total_h)
    for ry in range(total_h):
        sy = glyph_top_y + ry
        if sy < 0 or sy >= big.height:
            continue
        base = ry * rb
        for x in range(w):
            if big.getpixel((x0 + x, sy)) > 128:
                data[base + (x >> 3)] |= (0x80 >> (x & 7))
    return (w, bytes(data))


def gen_font(ttf_path, size, codes):
    font = ImageFont.truetype(ttf_path, size)
    ascent, descent = font.getmetrics()
    total_h = ascent + descent
    chars = []
    for code in codes:
        try:
            ch = chr(code)
        except Exception:
            continue
        if ch.isspace() and ch != ' ':
            chars.append((code, 0, b""))
            continue
        w, data = _render_glyph(font, ch, size, ascent, total_h)
        chars.append((code, w, data))
    return total_h, chars


# ---------- 切分 ----------

def split_chars_for_v3(chars, max_bytes):
    chunks = []
    current = []
    current_bytes = 6
    for item in chars:
        code, w, data = item
        glyph_bytes = 4 + (len(data) if w > 0 else 0)
        if current and (current_bytes + glyph_bytes > max_bytes):
            chunks.append(current)
            current = []
            current_bytes = 6
        current.append(item)
        current_bytes += glyph_bytes
    if current:
        chunks.append(current)
    if not chunks:
        chunks = [[]]
    return chunks


def split_chars_for_v4(chars, max_bytes):
    chunks = []
    current = []
    current_bytes = 6
    for item in chars:
        code, w, data = item
        glyph_bytes = 6 + (len(data) if w > 0 else 0)
        if current and (current_bytes + glyph_bytes > max_bytes):
            chunks.append(current)
            current = []
            current_bytes = 6
        current.append(item)
        current_bytes += glyph_bytes
    if current:
        chunks.append(current)
    if not chunks:
        chunks = [[]]
    return chunks


# ---------- 写入 ----------

def write_font_v3(max_h, chars, path):
    count = len(chars)
    flags = 0x01
    with open(path, "wb") as f:
        f.write(bytes([VERSION_V3, flags,
                       count & 0xFF, (count >> 8) & 0xFF,
                       max_h & 0xFF, (max_h >> 8) & 0xFF]))
        for code, w, data in chars:
            f.write(bytes([(code >> 8) & 0xFF, code & 0xFF, w, max_h]))
            if w > 0:
                f.write(data)
    return path.stat().st_size


def write_font_v4(max_h, chars, path):
    count = len(chars)
    flags = 0x01
    header_size = 6
    index_size = count * 6
    data_start = header_size + index_size

    index_entries = []
    data_parts = []
    offset = data_start
    for code, w, data in chars:
        index_entries.append((code, offset, w))
        if w > 0:
            data_parts.append(data)
            offset += len(data)

    with open(path, "wb") as f:
        f.write(bytes([VERSION_V4, flags,
                       count & 0xFF, (count >> 8) & 0xFF,
                       max_h & 0xFF, (max_h >> 8) & 0xFF]))
        for code, off, w in index_entries:
            f.write(bytes([
                (code >> 8) & 0xFF, code & 0xFF,
                (off >> 16) & 0xFF, (off >> 8) & 0xFF, off & 0xFF,
                w
            ]))
        for data in data_parts:
            f.write(data)
    return path.stat().st_size


def write_font_idx(path, family, size, max_h, is_unicode, chunks_info):
    data = {
        "version": 1,
        "family": family,
        "size": size,
        "max_h": max_h,
        "unicode": bool(is_unicode),
        "count": sum(c[3] for c in chunks_info),
        "chunks": [
            {"min": c[0], "max": c[1], "ci": c[2],
             "count": c[3], "name": c[4]}
            for c in chunks_info
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    return path.stat().st_size


def _esc_char(ch):
    if ch == '\t': return '\\t'
    if ch == '\n': return '\\n'
    if ch == '\r': return '\\r'
    if ch == '\'': return "\\'"
    if ch == '\\': return '\\\\'
    o = ord(ch)
    if o < 0x20 or o == 0x7F:
        return '\\x%02X' % o
    return ch


def write_font_map(path, family, size, fmt, max_h, chars,
                   missing, charset_name, ttf_name,
                   chunk_idx=None, chunk_total=None):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# E6 font map\n")
        f.write("# family: " + family + "\n")
        f.write("# size: " + str(size) + "\n")
        f.write("# format: " + fmt + "\n")
        f.write("# max_h: " + str(max_h) + "\n")
        f.write("# count: " + str(len(chars)) + "\n")
        f.write("# missing: " + str(missing) + "\n")
        f.write("# charset: " + charset_name + "\n")
        f.write("# source: " + ttf_name + "\n")
        if chunk_idx is not None:
            f.write("# chunk: " + str(chunk_idx) +
                    (" of " + str(chunk_total) if chunk_total else "") + "\n")
        f.write("# generated: " + ts + "\n")
        f.write("# columns: code<TAB>char<TAB>width\n")
        for code, w, _data in chars:
            try:
                esc = _esc_char(chr(code))
            except Exception:
                esc = '?'
            f.write("0x%04X\t'%s'\t%d\n" % (code, esc, w))
    return path.stat().st_size


# ---------- inspect ----------

def check_font_info(ttf_path):
    p = Path(ttf_path)
    if not p.is_file():
        return None
    info = {"path": str(p), "file_size": p.stat().st_size, "has_cmap": False}
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(str(p), fontNumber=0, lazy=True)
        head = font.get("head")
        info["units_per_em"] = head.unitsPerEm if head else None
        info["has_bitmap"] = ("EBDT" in font or "CBDT" in font)
        info["is_cff"] = ("CFF " in font or "CFF2" in font)
        info["glyph_count"] = font["maxp"].numGlyphs if "maxp" in font else None
        cmap = set()
        if "cmap" in font:
            for table in font["cmap"].tables:
                cmap.update(table.cmap.keys())
        info["codepoints"] = cmap
        info["has_cmap"] = True
        font.close()
    except ImportError:
        pass
    except Exception as e:
        info["ft_error"] = str(e)
    return info


def measure_sizes(ttf_path, sizes, codes):
    results = []
    for size in sizes:
        try:
            font = ImageFont.truetype(ttf_path, size)
            ascent, descent = font.getmetrics()
            total_h = ascent + descent
            total_bytes = 0
            total_w = 0
            valid_count = 0
            missing = 0
            max_w = 0
            for code in codes:
                try:
                    ch = chr(code)
                except Exception:
                    missing += 1
                    continue
                if ch.isspace() and ch != ' ':
                    total_bytes += 4
                    missing += 1
                    continue
                w, data = _render_glyph(font, ch, size, ascent, total_h)
                if w == 0:
                    total_bytes += 4
                    missing += 1
                    continue
                total_bytes += 4 + len(data)
                total_w += w
                if w > max_w:
                    max_w = w
                valid_count += 1
            avg_w = total_w / valid_count if valid_count else 0
            results.append({
                "size": size, "ok": True, "max_h": total_h,
                "bytes": total_bytes + 6, "avg_w": avg_w,
                "max_w": max_w, "missing": missing, "valid": valid_count,
            })
        except Exception as e:
            results.append({"size": size, "ok": False, "error": str(e)})
    return results


def _report_inspect(info, results, codes, charset_name):
    print("=" * 74)
    print("字体文件: " + info["path"])
    print("文件大小: %.1f KB" % (info["file_size"] / 1024))
    if "units_per_em" in info:
        print("unitsPerEm: %d" % info["units_per_em"])
    if "is_cff" in info:
        print("轮廓类型: " + ("CFF" if info["is_cff"] else "TrueType"))
    if "has_bitmap" in info:
        print("含 Bitmap: " + ("是" if info["has_bitmap"] else "否"))
    if "glyph_count" in info:
        print("字形总数: %d" % info["glyph_count"])
    if info.get("has_cmap"):
        print("可用码点数: %d" % len(info["codepoints"]))
    elif "ft_error" in info:
        print("[fonttools 错误] " + info["ft_error"])
    else:
        print("[提示] 未安装 fonttools，元数据不完整")
    print("=" * 74)
    print("")
    print("字符集: " + charset_name + "，共 " + str(len(codes)) + " 个码点")
    print("")
    print("%-6s %-6s %-8s %-10s %-10s %-10s %-8s %-8s" % (
        "size", "ok", "max_h", "bytes", "KB", "avg_w", "max_w", "缺失"))
    print("-" * 74)
    total = 0
    for r in results:
        if not r["ok"]:
            print("%-6d %-6s  %s" % (r["size"], "FAIL", r.get("error", "")))
            continue
        print("%-6d %-6s %-8d %-10d %-10.1f %-10.1f %-8d %-8d" % (
            r["size"], "yes", r["max_h"], r["bytes"], r["bytes"] / 1024.0,
            r["avg_w"], r["max_w"], r["missing"]))
        total += r["bytes"]
    print("-" * 74)
    print("%-6s %-6s %-8s %-10d %-10.1f" % ("合计", "", "", total, total / 1024.0))

    if info.get("has_cmap"):
        cmap = info["codepoints"]
        missing = [c for c in codes if c not in cmap]
        covered = len(codes) - len(missing)
        print("")
        print("字符覆盖: %d / %d (%.1f%%)" % (
            covered, len(codes), 100.0 * covered / max(1, len(codes))))


# ---------- 字符检查 ----------

def _render_check_char(font, ch, size, ascent, total_h):
    big = Image.new("L", (size * 6, size * 6), 0)
    draw = ImageDraw.Draw(big)
    baseline_y = size * 3
    glyph_top_y = baseline_y - ascent
    try:
        draw.text((size, glyph_top_y), ch, font=font, fill=255)
    except Exception:
        return 0, None
    bbox = big.getbbox()
    if not bbox:
        return 0, None
    x0, y0, x1, y1 = bbox
    crop = big.crop(bbox)
    return x1 - x0, crop.tobytes()


def check_chars(ttf_path, chars, size=32):
    try:
        font = ImageFont.truetype(ttf_path, size)
        ascent, descent = font.getmetrics()
        total_h = ascent + descent
    except Exception as e:
        print("[错误] 无法加载字体: " + str(e))
        return [], []

    cmap = None
    cmap_available = False
    try:
        from fontTools.ttLib import TTFont
        ftt = TTFont(ttf_path, fontNumber=0, lazy=True)
        cmap = set()
        if "cmap" in ftt:
            for table in ftt["cmap"].tables:
                cmap.update(table.cmap.keys())
        ftt.close()
        cmap_available = True
    except ImportError:
        pass
    except Exception as e:
        print("[警告] fonttools 读取 cmap 失败: " + str(e))

    notdef_pixels = None
    if not cmap_available:
        try:
            _w, notdef_pixels = _render_check_char(font, '\u0378', size, ascent, total_h)
        except Exception:
            notdef_pixels = None

    print("=" * 92)
    print("字符检查 — 字体: " + ttf_path)
    print("字号: " + str(size) + "px   共 " + str(len(chars)) + " 个字符")
    if cmap_available:
        print("cmap: 已加载，" + str(len(cmap)) + " 个码点（精确判定）")
    else:
        print("cmap: 不可用（未安装 fonttools），靠渲染 + notdef 对比（可能不准）")
        print("      建议：pip install fonttools")
    print("=" * 92)
    print("")
    header = "%-4s %-8s %-38s %-6s %-6s %-6s %-6s" % (
        "字符", "码点", "Unicode 块", "cmap", "渲染", "宽", "inFull")
    print(header)
    print("-" * 92)

    ok_list = []
    fail_list = []

    for ch in chars:
        code = ord(ch)
        if code > 0xFFFF:
            print("%-4s U+%06X  [超出 BMP，gen_font 不支持]" % (_display_char(ch), code))
            fail_list.append((ch, code, "超出 BMP"))
            continue

        block = _unicode_block_name(code)

        if cmap_available:
            cmap_ok = code in cmap
            cmap_str = "✓" if cmap_ok else "✗"
        else:
            cmap_ok = None
            cmap_str = "?"

        if ch.isspace() and ch != ' ':
            render_str = "空白"
            render_ok = True
            width = 0
        elif ch == ' ':
            try:
                width = _render_check_char(font, 'M', size, ascent, total_h)[0] // 2
            except Exception:
                width = 0
            render_ok = width > 0
            render_str = "✓" if render_ok else "✗"
        else:
            try:
                width, pixels = _render_check_char(font, ch, size, ascent, total_h)
            except Exception:
                width, pixels = 0, None
            render_ok = width > 0
            if render_ok and (not cmap_available) and notdef_pixels is not None and pixels == notdef_pixels:
                render_ok = False
                render_str = "notdef"
            else:
                render_str = "✓" if render_ok else "✗"

        in_full = "✓" if code in _FULL_SET else "✗"

        if cmap_ok is True:
            supported = True
        elif cmap_ok is False:
            supported = False
        else:
            supported = render_ok

        if supported:
            ok_list.append((ch, code, block, width))
        else:
            fail_list.append((ch, code, block))

        print("%-4s U+%04X   %-38s %-6s %-6s %-6d %-6s" % (
            _display_char(ch), code, block, cmap_str, render_str, width, in_full))

    print("-" * 92)
    print("")

    if fail_list:
        print("[不支持] " + str(len(fail_list)) + " 个字符:")
        for ch, code, block in fail_list:
            print("   %s  U+%04X  %s" % (_display_char(ch), code, block))
        print("")
        print("建议：这些字符不在该 TTF 中，需要换字体（如 Noto Sans Symbols、思源黑体、"
              "Noto Sans CJK）")
        print("")

    if ok_list:
        print("[支持] " + str(len(ok_list)) + " 个字符")
        print("")
        codes = [code for _, code, _, _ in ok_list]
        extra_str = ",".join("0x%04X" % c for c in codes)
        print("提示：把这些字符加入字模，可添加参数：")
        print("   --extra " + extra_str)
        print("或直接传字符串（新功能）：")
        print("   --chars \"" + "".join(ch for ch, _, _, _ in ok_list) + "\"")
        print("")
        missing_from_full = [c for c in codes if c not in _FULL_SET]
        if missing_from_full:
            print("注意：下列码点不在内置 CHARSET_FULL 里，只加 --charset=full 不会包含：")
            print("   " + ",".join("0x%04X" % c for c in missing_from_full))
            print("")
    else:
        print("⚠ 所有字符都不支持，请检查字体文件是否正确")

    return [c[0] for c in ok_list], [c[0] for c in fail_list]


# ---------- 主入口 ----------

def main():
    if len(sys.argv) == 1:
        print(__doc__)
        sys.exit(0)

    ap = argparse.ArgumentParser(
        description="生成 E6 墨水屏字模（v3 chunk / v4 索引）+ 字符清单 + 顶部索引",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument("ttf", help="TTF/OTF 字体文件路径")
    ap.add_argument("--sizes", default=DEFAULT_SIZES,
                    help="字号列表（默认全部 16 个）\n"
                         "关键字：all / reasonable / mini / small / big")
    ap.add_argument("--family", default=None, help="字体族名")
    ap.add_argument("--charset", default="auto",
                    help="字符集基础（默认 auto，从 TTF cmap 读取全部支持的字符）\n"
                         "  auto    - TTF cmap 全部 BMP 字符（默认，最全）\n"
                         "  ttf     - 同 auto\n"
                         "  ascii   - 0x20-0x7E（95 字符）\n"
                         "  basic   - 0x20-0xFF（224 字符）\n"
                         "  symbols - 图形符号、箭头、数学等（约 130 字符）\n"
                         "  full    - basic + symbols（约 430 字符）\n"
                         "  latin1  - 同 basic\n"
                         "  cjk     - CJK 基本区 + ASCII（约 2 万汉字）\n"
                         "  none    - 空基础集（只用 --chars / --file / --extra）")
    ap.add_argument("--chars", default=None,
                    help="★ 直接传一串字符作为附加字符（叠加到 --charset 上）\n"
                         "  例：--chars \"Hello World ∠・⌒\"\n"
                         "  配合 --charset=none 可只生成这串字符")
    ap.add_argument("--extra", default=None,
                    help="附加码点（逗号分隔，十六进制或十进制）\n"
                         "  例：--extra 0x2220,0x30FB,0x2312")
    ap.add_argument("--range", dest="extra_range", default=None,
                    help="附加码点范围（逗号分隔）\n"
                         "  例：--range 0x4E00-0x4E10,0x3000-0x303F")
    ap.add_argument("--file", dest="extra_file", default=None,
                    help="从 UTF-8 文本文件读取字符（全文所有字符都会加入）")
    ap.add_argument("--format", default="v4", choices=["v3", "v4"],
                    help="v3 = 老 chunk（v3 格式）\n"
                         "v4 = v4 索引（默认，前端懒加载）")
    ap.add_argument("--max-chunk-kb", type=int, default=None,
                    help="每 chunk 最大 KB（默认 v4=100, v3=19）")
    ap.add_argument("--max-chunk-bytes", type=int, default=None,
                    help="每 chunk 最大字节（优先级高于 --max-chunk-kb）")
    ap.add_argument("--no-map", action="store_true",
                    help="不生成 .map 字符清单")
    ap.add_argument("--out", default=str(OUT_DIR), help="输出目录")
    ap.add_argument("--inspect", action="store_true", help="只检查不生成")
    ap.add_argument("--inspect-sizes", default=DEFAULT_SIZES)
    ap.add_argument("--check", default=None,
                    help="检查一串字符能否在 TTF 里渲染（不生成字模，检查完退出）\n"
                         "  例：--check \"∠・⌒\"")
    ap.add_argument("--check-file", default=None,
                    help="从 UTF-8 文件读取字符，检查能否渲染")
    ap.add_argument("--check-size", type=int, default=32,
                    help="检查用字号（默认 32）")

    args = ap.parse_args()

    ttf = Path(args.ttf)
    if not ttf.is_file():
        print("[错误] 字体文件不存在: " + str(ttf))
        sys.exit(2)

    # ---------- 字符检查模式 ----------
    if args.check is not None or args.check_file is not None:
        chars = []
        if args.check is not None:
            chars.extend(list(args.check))
        if args.check_file is not None:
            p = Path(args.check_file)
            if not p.is_file():
                print("[错误] 检查文件不存在: " + str(p))
                sys.exit(2)
            try:
                txt = p.read_text(encoding='utf-8')
            except Exception as e:
                print("[错误] 无法读取检查文件: " + str(e))
                sys.exit(2)
            chars.extend(list(txt))
        seen = set()
        uniq = []
        for ch in chars:
            if ch in ('\r', '\n'):
                continue
            if ch in seen:
                continue
            seen.add(ch)
            uniq.append(ch)
        if not uniq:
            print("[错误] 没有可检查的字符")
            sys.exit(2)
        check_chars(str(ttf), uniq, size=args.check_size)
        return

    # ---------- 解析字符集 ----------
    if args.chars:
        print("[提示] --chars 收到 " + str(len(args.chars)) + " 个字符")
        preview = args.chars if len(args.chars) <= 80 else args.chars[:80] + "..."
        print("       内容: " + repr(preview))

    try:
        codes = parse_charset(args.charset, str(ttf),
                              args.extra, args.extra_range, args.extra_file,
                              chars=args.chars)
    except ValueError as e:
        print("[错误] " + str(e))
        sys.exit(2)

    if not codes:
        print("[错误] 字符集为空，请检查 --charset / --chars / --file 参数")
        sys.exit(2)

    # ---------- inspect ----------
    if args.inspect:
        try:
            inspect_sizes = parse_sizes(args.inspect_sizes)
        except ValueError as e:
            print("[错误] " + str(e))
            sys.exit(2)
        info = check_font_info(str(ttf))
        if info is None:
            print("[错误] 无法读取字体")
            sys.exit(2)
        print("正在测量 " + str(len(inspect_sizes)) + " 个 size × " +
              str(len(codes)) + " 个字符…")
        results = measure_sizes(str(ttf), inspect_sizes, codes)
        _report_inspect(info, results, codes, args.charset)
        return

    # ---------- 生成 ----------
    try:
        sizes = sorted(set(parse_sizes(args.sizes)))
    except ValueError as e:
        print("[错误] " + str(e))
        sys.exit(2)
    if not sizes:
        print("[错误] --sizes 为空")
        sys.exit(2)
    for s in sizes:
        if s < 6 or s > 200:
            print("[错误] 字号 " + str(s) + " 超出 6-200")
            sys.exit(2)

    if args.max_chunk_bytes is not None:
        max_bytes = args.max_chunk_bytes
    elif args.max_chunk_kb is not None:
        max_bytes = args.max_chunk_kb * 1024
    else:
        max_bytes = None

    if args.format == "v4" and max_bytes is None:
        max_bytes = DEFAULT_V4_CHUNK_KB * 1024
    elif args.format == "v3" and max_bytes is None:
        max_bytes = DEFAULT_V3_CHUNK_KB * 1024

    family = args.family or ttf.stem
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("字体文件: " + str(ttf))
    print("字体族名: " + family)
    print("格式: " + args.format.upper())
    print("字符集: " + args.charset + "，共 " + str(len(codes)) + " 个码点")
    print("字号: " + ", ".join(str(s) for s in sizes) + "（共 " + str(len(sizes)) + " 个）")
    print("每 chunk 上限: " + str(max_bytes) + " bytes (" +
          ("%.2f" % (max_bytes / 1024)) + " KB)")
    print("字符清单: " + ("关闭" if args.no_map else "开启 (.map)"))
    print("顶部索引: 开启 (.idx)")
    print("输出目录: " + str(out_dir))
    print("")

    total_bin = 0
    total_map = 0
    total_idx = 0
    total_files = 0

    for size in sizes:
        try:
            max_h, chars = gen_font(str(ttf), size, codes)
        except Exception as e:
            print("[错误] 生成 " + str(size) + "px 失败: " + str(e))
            continue
        missing = sum(1 for _, w, _ in chars if w == 0)

        if args.format == "v4":
            chunks = split_chars_for_v4(chars, max_bytes)
        else:
            chunks = split_chars_for_v3(chars, max_bytes)
        chunk_total = len(chunks)
        chunks_info = []
        font_bin_bytes = 0
        font_map_bytes = 0

        for ci, chunk in enumerate(chunks):
            base_name = "%s_%d_%d" % (family, size, ci)
            p_bin = out_dir / (base_name + ".bin")
            if args.format == "v4":
                n = write_font_v4(max_h, chunk, p_bin)
            else:
                n = write_font_v3(max_h, chunk, p_bin)
            font_bin_bytes += n
            total_files += 1
            total_bin += n

            if chunk:
                mn = chunk[0][0]
                mx = chunk[-1][0]
                chunks_info.append((mn, mx, ci, len(chunk), base_name))

            if not args.no_map:
                p_map = out_dir / (base_name + ".map")
                chunk_missing = sum(1 for _, w, _ in chunk if w == 0)
                m = write_font_map(p_map, family, size, args.format, max_h, chunk,
                                   chunk_missing, args.charset, ttf.name,
                                   chunk_idx=ci, chunk_total=chunk_total)
                font_map_bytes += m
                total_map += m

        p_idx = out_dir / ("%s_%d.idx" % (family, size))
        idx_size = write_font_idx(p_idx, family, size, max_h, True, chunks_info)
        total_idx += idx_size
        total_files += 1

        print("  字体 %s %dpx: %d chunk, bin %.1f KB, map %.1f KB, idx %.1f KB, 缺失 %d"
              % (family, size, chunk_total,
                 font_bin_bytes / 1024, font_map_bytes / 1024,
                 idx_size / 1024, missing))
        print("")

    print("=" * 60)
    print("完成: %d 个文件, bin %.1f MB, map %.1f KB, idx %.1f KB"
          % (total_files, total_bin / 1024 / 1024,
             total_map / 1024, total_idx / 1024))


if __name__ == "__main__":
    main()