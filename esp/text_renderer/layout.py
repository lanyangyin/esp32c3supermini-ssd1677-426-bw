# text_renderer/layout.py — 颜色/样式解析、排版与宽度计算

import gc

# 字间距钳制范围
_LS_MIN = -100
_LS_MAX = 200

SENTINEL = 0xFF


def _make_bytearray_fill(n, value=0):
    """快速生成填满指定值的 bytearray"""
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


# ---------- 颜色（多级灰度 0~15） ----------

def parse_color_spec(spec, default=0):
    """解析多级灰度：直接返回 0~15 的数值"""
    if spec is None:
        return default
    if isinstance(spec, dict):
        val = spec.get("val", spec.get("idx", default))
        return max(0, min(15, int(val)))
    if isinstance(spec, int):
        return max(0, min(15, spec))
    return default


def _resolve_color(spec, x, y):
    """由于是纯灰度，不需要抖动，直接返回目标灰度值"""
    return spec


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
        'fg': parse_color_spec(default_style.get('fg', 0)),
        'bg': parse_color_spec(default_style.get('bg', 15)),
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


# ---------- 排版计算 ----------

def _compute_line_metrics(text, font, per_char, ds, inner_w, inner_h,
                          align, valign, line_height, mt, mb, ml, mr,
                          temp_fonts, line_extras=None):
    """
    第一遍扫描：计算每一行的宽度、最大高度、垂直起始坐标等。
    返回 (line_infos, lines_text)
    """
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
            'start': char_idx,
            'count': len(line),
            'line_w': line_w,
            'max_h': line_max_h,
            'lh': lh,
        })
        char_idx += len(line)
        total_h += lh

    # 计算 Y 轴起点
    avail_h = inner_h - mt - mb
    if valign == 'center':
        cy0 = mt + max(0, (avail_h - total_h) // 2)
    elif valign == 'bottom':
        cy0 = mt + max(0, avail_h - total_h)
    else:
        cy0 = mt

    # 计算每行的 X 坐标和 Y 坐标
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