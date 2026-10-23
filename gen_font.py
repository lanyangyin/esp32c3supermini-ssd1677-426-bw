#!/usr/bin/env python3
"""
gen_font.py — TTF → 1bpp 字模（v4 格式）+ 顶部索引 + 字符清单

v4 格式（与 E6 墨水屏工程一致）：
    Header: 6 字节
        [0]     version = 0x04
        [1]     flags   bit0=1 表示 Unicode，0 表示 ASCII
        [2..3]  count   uint16 LE，字形数
        [4..5]  max_h   uint16 LE，最大高度
    Index:  count × 6 字节，按 code 升序
        [0..1]  code    uint16 BE
        [2..4]  offset  uint24 BE
        [5]     width   uint8
    Data:   1bpp 位图，MSB first，每行紧密排列

输出：
    {family}_{size}_{chunk}.bin    字模 chunk
    {family}_{size}_{chunk}.map    字符清单（文本，调试用）
    {family}_{size}.idx            顶部索引（JSON）

用法：
    # 生成 ASCII 基础字模，多个字号
    python gen_font.py MyFont.ttf --charset=ascii --sizes=16,24,32

    # 生成中文字模（放 SD 卡）
    python gen_font.py SourceHanSans.ttf --charset=cjk --sizes=24,32 \
        --max-chunk-kb=100

    # 只生成指定的几十个字符
    python gen_font.py MyFont.ttf --charset=none --chars="你好世界" \
        --sizes=32 --family=hello

    # 检查字体里有没有某个字符
    python gen_font.py MyFont.ttf --check "你好∠"

依赖：
    pip install pillow fonttools

    fonttools 只有 --charset=auto 和 --check 需要。没装时 auto 退化为 full。
"""
import sys
import argparse
import time
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

VERSION_V3 = 0x03
VERSION_V4 = 0x04
OUT_DIR = Path("esp/fonts")

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


# ---------- Unicode 块表 ----------

UNICODE_BLOCKS = [
    (0x0000, 0x007F, "Basic Latin"),
    (0x0080, 0x00FF, "Latin-1 Supplement"),
    (0x0100, 0x017F, "Latin Extended-A"),
    (0x0180, 0x024F, "Latin Extended-B"),
    (0x0300, 0x036F, "Combining Diacritical Marks"),
    (0x0370, 0x03FF, "Greek and Coptic"),
    (0x0400, 0x04FF, "Cyrillic"),
    (0x2000, 0x206F, "General Punctuation"),
    (0x2190, 0x21FF, "Arrows"),
    (0x2200, 0x22FF, "Mathematical Operators"),
    (0x2460, 0x24FF, "Enclosed Alphanumerics"),
    (0x2500, 0x257F, "Box Drawing"),
    (0x25A0, 0x25FF, "Geometric Shapes"),
    (0x2600, 0x26FF, "Miscellaneous Symbols"),
    (0x3000, 0x303F, "CJK Symbols and Punctuation"),
    (0x3040, 0x309F, "Hiragana"),
    (0x30A0, 0x30FF, "Katakana"),
    (0x4E00, 0x9FFF, "CJK Unified Ideographs"),
    (0xFF00, 0xFFEF, "Halfwidth and Fullwidth Forms"),
]


def _unicode_block_name(code):
    for lo, hi, name in UNICODE_BLOCKS:
        if lo <= code <= hi:
            return name
    return "Unknown"


# ---------- 字符集 ----------

CHARSET_ASCII = list(range(0x20, 0x7F))
CHARSET_BASIC = list(range(0x20, 0x100))

CHARSET_SYMBOLS = [
    # 常用标点
    0x2010, 0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D,
    0x2020, 0x2021, 0x2022, 0x2026, 0x2030, 0x2032, 0x2033,
    0x2039, 0x203A, 0x203B, 0x2044,
    0x20A3, 0x20A4, 0x20A9, 0x20AB, 0x20AC,
    # 数学
    0x00B0, 0x00B1, 0x00D7, 0x00F7,
    0x2202, 0x2206, 0x220F, 0x2211, 0x2212, 0x221A, 0x221B,
    0x221E, 0x222B, 0x2248, 0x2260, 0x2264, 0x2265,
    # 希腊字母
    0x03B1, 0x03B2, 0x03B3, 0x03B4, 0x03B5, 0x03B8, 0x03BB,
    0x03BC, 0x03C0, 0x03C3, 0x03C9,
    0x0391, 0x0392, 0x0393, 0x0394, 0x0398, 0x039B, 0x039C,
    0x03A0, 0x03A3, 0x03A9,
    # 箭头
    0x2190, 0x2191, 0x2192, 0x2193, 0x2194, 0x2195,
    0x2196, 0x2197, 0x2198, 0x2199, 0x21A9, 0x21AA,
    # 几何
    0x25A0, 0x25A1, 0x25AA, 0x25AB,
    0x25B2, 0x25B3, 0x25BC, 0x25BD,
    0x25C6, 0x25C7, 0x25CB, 0x25CF, 0x25E6,
    0x2605, 0x2606,
    # 扑克牌
    0x2660, 0x2661, 0x2662, 0x2663, 0x2664, 0x2665, 0x2666, 0x2667,
    # 天气 / 表情
    0x2600, 0x2601, 0x2602, 0x2603, 0x263A, 0x263B,
    # 音符
    0x266A, 0x266B, 0x266C,
    # 常用符号
    0x2713, 0x2714, 0x2717, 0x2718,
    0x274C, 0x2764,
    # 带圈数字
    0x2460, 0x2461, 0x2462, 0x2463, 0x2464,
    0x2465, 0x2466, 0x2467, 0x2468, 0x2469,
    # 制表符
    0x2500, 0x2502, 0x250C, 0x2510, 0x2514, 0x2518,
    0x251C, 0x2524, 0x252C, 0x2534, 0x253C,
    0x2550, 0x2551, 0x2554, 0x2557, 0x255A, 0x255D,
    0x2560, 0x2563, 0x2566, 0x2569, 0x256C,
    # 方块填充
    0x2580, 0x2584, 0x2588, 0x258C, 0x2590, 0x2591, 0x2592, 0x2593,
    # 警告
    0x26A0, 0x26A1,
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
        if 'cmap' in font:
            for table in font['cmap'].tables:
                cmap.update(table.cmap.keys())
        font.close()
        if not cmap:
            return None
        result = sorted(c for c in cmap if 0 <= c <= 0xFFFF)
        return result if result else None
    except Exception as e:
        print("[WARN] 读取 TTF cmap 失败: %s" % e)
        return None


def _extend_chars(codes, chars, source_label):
    if not chars:
        return 0
    seen = set(codes)
    added = 0
    for ch in chars:
        code = ord(ch)
        if code > 0xFFFF or 0xD800 <= code <= 0xDFFF:
            continue
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
        added += 1
    print("[INFO] %s 添加了 %d 个新码点" % (source_label, added))
    return added


def parse_charset(spec, ttf_path=None, chars=None, extra_file=None):
    spec = (spec or "").lower().strip()

    if spec in ('none', 'text', 'literal'):
        codes = []
        print("[INFO] charset=none：仅使用 --chars / --file 指定的字符")
        if not chars and not extra_file:
            print("[ERROR] charset=none 但没提供 --chars / --file")
            raise ValueError("charset=none 需要至少一个字符来源")
    elif spec in ('auto', 'ttf'):
        codes = _read_ttf_codepoints(ttf_path) if ttf_path else None
        if codes is None:
            print("[WARN] 无法读取 TTF cmap（未装 fonttools 或读取失败）")
            print("       回退到 full 字符集（%d 字符）" % len(CHARSET_FULL))
            print("       安装 fonttools: pip install fonttools")
            codes = list(CHARSET_FULL)
        else:
            print("[INFO] 从 TTF cmap 读取到 %d 个码点" % len(codes))
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
        raise ValueError("未知字符集: %s（可选 auto/ttf/ascii/basic/symbols/full/latin1/cjk/none）" % spec)

    _extend_chars(codes, chars, "--chars")

    if extra_file:
        p = Path(extra_file)
        txt = p.read_text(encoding='utf-8')
        added = _extend_chars(codes, txt, "--file %s" % extra_file)
        if added == 0:
            print("[WARN] --file 里没有新的码点")

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
                raise ValueError("无效的 size: '%s'" % token)
    return sizes


# ---------- 字形渲染 ----------

def _render_glyph(font, ch, size, ascent, total_h):
    """用 top-left 对齐渲染单个字符，返回 (width, 1bpp bytes)"""
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


# ---------- 写文件 ----------

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


def write_font_idx(path, family, size, max_h, chunks_info):
    data = {
        "version": 1,
        "family": family,
        "size": size,
        "max_h": max_h,
        "unicode": True,
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
        f.write("# E-Ink font map\n")
        f.write("# family: %s\n" % family)
        f.write("# size: %d\n" % size)
        f.write("# format: %s\n" % fmt)
        f.write("# max_h: %d\n" % max_h)
        f.write("# count: %d\n" % len(chars))
        f.write("# missing: %d\n" % missing)
        f.write("# charset: %s\n" % charset_name)
        f.write("# source: %s\n" % ttf_name)
        if chunk_idx is not None:
            f.write("# chunk: %d" % chunk_idx)
            if chunk_total:
                f.write(" of %d" % chunk_total)
            f.write("\n")
        f.write("# generated: %s\n" % ts)
        f.write("# columns: code<TAB>char<TAB>width\n")
        for code, w, _data in chars:
            try:
                esc = _esc_char(chr(code))
            except Exception:
                esc = '?'
            f.write("0x%04X\t'%s'\t%d\n" % (code, esc, w))
    return path.stat().st_size


# ---------- check ----------

def check_chars(ttf_path, chars, size=32):
    try:
        font = ImageFont.truetype(ttf_path, size)
        ascent, descent = font.getmetrics()
        total_h = ascent + descent
    except Exception as e:
        print("[ERROR] 无法加载字体: %s" % e)
        return

    cmap = None
    cmap_available = False
    try:
        from fontTools.ttLib import TTFont
        ftt = TTFont(ttf_path, fontNumber=0, lazy=True)
        cmap = set()
        if 'cmap' in ftt:
            for table in ftt['cmap'].tables:
                cmap.update(table.cmap.keys())
        ftt.close()
        cmap_available = True
    except ImportError:
        pass
    except Exception as e:
        print("[WARN] fonttools 读取 cmap 失败: %s" % e)

    print("=" * 80)
    print("字体: %s" % ttf_path)
    print("字号: %dpx   字符数: %d" % (size, len(chars)))
    if cmap_available:
        print("cmap: %d 个码点（精确判定）" % len(cmap))
    else:
        print("cmap: 不可用（靠渲染判断，可能不准）")
    print("=" * 80)
    print("%-4s %-8s %-32s %-6s %-6s %-6s" % ("字符", "码点", "Unicode 块", "cmap", "渲染", "宽"))
    print("-" * 80)

    ok_list = []
    fail_list = []

    for ch in chars:
        code = ord(ch)
        if code > 0xFFFF:
            print("%-4s U+%06X  [超出 BMP]" % (ch, code))
            fail_list.append((ch, code, "超 BMP"))
            continue
        block = _unicode_block_name(code)
        cmap_ok = (code in cmap) if cmap_available else None
        cmap_str = "✓" if cmap_ok else ("✗" if cmap_ok is False else "?")

        if ch.isspace() and ch != ' ':
            render_str = "空白"
            render_ok = True
            width = 0
        else:
            try:
                w, _ = _render_glyph(font, ch, size, ascent, total_h)
                width = w
                render_ok = w > 0
            except Exception:
                width = 0
                render_ok = False
            render_str = "✓" if render_ok else "✗"

        supported = cmap_ok is True or (cmap_ok is None and render_ok)

        if supported:
            ok_list.append((ch, code, block, width))
        else:
            fail_list.append((ch, code, block))

        print("%-4s U+%04X   %-32s %-6s %-6s %-6d" % (
            ch if ch != ' ' else '␣',
            code, block, cmap_str, render_str, width))

    print("-" * 80)
    if fail_list:
        print("[不支持] %d 个字符:" % len(fail_list))
        for ch, code, block in fail_list:
            print("   %s  U+%04X  %s" % (ch, code, block))
    if ok_list:
        print("[支持] %d 个字符" % len(ok_list))
        print("   --chars \"%s\"" % "".join(c[0] for c in ok_list))


# ---------- 主入口 ----------

def main():
    if len(sys.argv) == 1:
        print(__doc__)
        sys.exit(0)

    ap = argparse.ArgumentParser(
        description="生成 1bpp 字模（v4 格式）+ 字符清单 + 顶部索引",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument("ttf", help="TTF/OTF 字体文件路径")
    ap.add_argument("--sizes", default=DEFAULT_SIZES,
                    help="字号列表（默认全部 16 个）\n"
                         "关键字：all / reasonable / mini / small / big")
    ap.add_argument("--family", default=None, help="字体族名（默认 TTF 文件名）")
    ap.add_argument("--charset", default="auto",
                    help="字符集（默认 auto）\n"
                         "  auto    - TTF cmap 全部 BMP（需要 fonttools）\n"
                         "  ascii   - 0x20-0x7E（95 字符）\n"
                         "  basic   - 0x20-0xFF（224 字符）\n"
                         "  symbols - 图形/符号/箭头等（约 200 字符）\n"
                         "  full    - basic + symbols（约 400 字符）\n"
                         "  cjk     - CJK 基本区 + ASCII（约 2 万）\n"
                         "  none    - 空基础集，只用 --chars / --file 补充")
    ap.add_argument("--chars", default=None,
                    help="直接传一串字符（叠加到 --charset）\n"
                         "  例：--chars \"Hello ∠・⌒\"\n"
                         "  配合 --charset=none 可只生成这串字符")
    ap.add_argument("--file", dest="extra_file", default=None,
                    help="从 UTF-8 文本文件读字符（自动去重、跳过换行）")
    ap.add_argument("--format", default="v4", choices=["v3", "v4"],
                    help="v4 = 索引格式（默认，前端懒加载）\n"
                         "v3 = 老格式")
    ap.add_argument("--max-chunk-kb", type=int, default=None,
                    help="每 chunk 最大 KB（默认 v4=100, v3=19）")
    ap.add_argument("--no-map", action="store_true",
                    help="不生成 .map 字符清单")
    ap.add_argument("--out", default=str(OUT_DIR),
                    help="输出目录（默认 esp/fonts）")
    ap.add_argument("--check", default=None,
                    help="只检查一串字符能否在 TTF 中渲染（不生成字模）")
    ap.add_argument("--check-size", type=int, default=32,
                    help="检查用字号（默认 32）")

    args = ap.parse_args()

    ttf = Path(args.ttf)
    if not ttf.is_file():
        print("[ERROR] 字体文件不存在: %s" % ttf)
        sys.exit(2)

    # check 模式
    if args.check is not None:
        chars = list(args.check)
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
            print("[ERROR] 没有可检查的字符")
            sys.exit(2)
        check_chars(str(ttf), uniq, size=args.check_size)
        return

    # 解析字符集
    try:
        codes = parse_charset(args.charset, str(ttf),
                              chars=args.chars, extra_file=args.extra_file)
    except ValueError as e:
        print("[ERROR] %s" % e)
        sys.exit(2)

    if not codes:
        print("[ERROR] 字符集为空，请检查 --charset / --chars / --file")
        sys.exit(2)

    # 解析字号
    try:
        sizes = sorted(set(parse_sizes(args.sizes)))
    except ValueError as e:
        print("[ERROR] %s" % e)
        sys.exit(2)
    if not sizes:
        print("[ERROR] --sizes 为空")
        sys.exit(2)
    for s in sizes:
        if s < 6 or s > 200:
            print("[ERROR] 字号 %d 超出 6-200" % s)
            sys.exit(2)

    # 计算每 chunk 上限
    if args.max_chunk_kb is not None:
        max_bytes = args.max_chunk_kb * 1024
    else:
        max_bytes = (DEFAULT_V4_CHUNK_KB if args.format == 'v4' else DEFAULT_V3_CHUNK_KB) * 1024

    family = args.family or ttf.stem
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("字体文件: %s" % ttf)
    print("字体族名: %s" % family)
    print("格式: %s" % args.format.upper())
    print("字符集: %s，共 %d 个码点" % (args.charset, len(codes)))
    print("字号: %s（共 %d 个）" % (", ".join(str(s) for s in sizes), len(sizes)))
    print("每 chunk 上限: %d bytes（%.1f KB）" % (max_bytes, max_bytes / 1024))
    print("字符清单: %s" % ("关闭" if args.no_map else "开启"))
    print("输出目录: %s" % out_dir)
    print()

    total_bin = 0
    total_map = 0
    total_idx = 0
    total_files = 0

    for size in sizes:
        try:
            max_h, chars = gen_font(str(ttf), size, codes)
        except Exception as e:
            print("[ERROR] 生成 %dpx 失败: %s" % (size, e))
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
        idx_size = write_font_idx(p_idx, family, size, max_h, chunks_info)
        total_idx += idx_size
        total_files += 1

        print("  %s %dpx: %d chunk, bin %.1f KB, map %.1f KB, idx %.1f KB, 缺失 %d"
              % (family, size, chunk_total,
                 font_bin_bytes / 1024, font_map_bytes / 1024,
                 idx_size / 1024, missing))

    print()
    print("=" * 60)
    print("完成: %d 个文件, bin %.1f KB, map %.1f KB, idx %.1f KB"
          % (total_files, total_bin / 1024, total_map / 1024, total_idx / 1024))


if __name__ == '__main__':
    main()