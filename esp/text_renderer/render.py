# text_renderer/render.py — 像素绘制、旋转、背景合成与流式输出
import gc
import os

from .layout import (
    SENTINEL,
    _make_bytearray_fill,
    _get_char_font,
    _style_ls,
    _char_advance,
    _glyph_of,
    _compute_line_metrics,
    _resolve_color,
)
from .fonts import _resolve_font

W = 800
H = 480
FRAME_BYTES = W * H // 8  # 48000（1bpp）

# 4×4 Bayer 矩阵（0~15）
_BAYER4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)


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
    fg_spec = style.get('fg', 0)
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
                    idx_buf[row_off + px] = outline_spec

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
                    idx_buf[row_off + px] = fg_spec
                if bold_dx:
                    px2 = px + bold_dx
                    if 0 <= px2 < inner_w:
                        idx_buf[row_off + px2] = fg_spec


def _blit_char_to_band_transposed(idx_buf, inner_h, band_start, band_end_x,
                                  cx, cy, glyph, style, font_h):
    gw, gh, gdata = glyph
    if gw == 0 or gh == 0:
        return
    bold = bool(style.get('bold', False))
    italic = bool(style.get('italic', False))
    outline_spec = style.get('outline')
    fg_spec = style.get('fg', 0)
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
                    idx_buf[col * inner_h + py] = outline_spec

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
                    idx_buf[col * inner_h + py] = fg_spec
                if bold_dx:
                    px2 = px + bold_dx
                    if band_start <= px2 < band_end_x:
                        col2 = px2 - band_start
                        idx_buf[col2 * inner_h + py] = fg_spec


def _blit_char(buf, w, h, glyph, x, y, style, font_size):
    gw, gh, gdata = glyph
    if gw == 0 or gh == 0:
        return
    bold = bool(style.get('bold', False))
    italic = bool(style.get('italic', False))
    outline_spec = style.get('outline')
    fg_spec = style.get('fg', 0)
    bold_dx = 1 if bold else 0
    shear_px = max(1, font_size // 8) if italic else 0
    rb = (gw + 7) // 8
    denom = max(1, gh - 1)

    if outline_spec is not None:
        for row_idx in range(gh):
            py = y + row_idx
            if py < 0 or py >= h:
                continue
            offset = ((gh - 1 - row_idx) * shear_px) // denom if shear_px else 0
            for x_disp in range(-1, gw + 1 + bold_dx):
                px = x + offset + x_disp
                if px < 0 or px >= w:
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
                    buf[py * w + px] = outline_spec

    for row_idx in range(gh):
        py = y + row_idx
        if py < 0 or py >= h:
            continue
        offset = ((gh - 1 - row_idx) * shear_px) // denom if shear_px else 0
        base = row_idx * rb
        for bit in range(gw):
            if gdata[base + (bit >> 3)] & (0x80 >> (bit & 7)):
                px = x + offset + bit
                if 0 <= px < w:
                    buf[py * w + px] = fg_spec
                if bold_dx:
                    px2 = px + bold_dx
                    if 0 <= px2 < w:
                        buf[py * w + px2] = fg_spec


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


def _to_4bpp(idx_buf, w, h):
    out = bytearray((w * h) // 2)
    o = 0
    for i in range(0, w * h, 2):
        hi = idx_buf[i] & 0x0F
        lo = idx_buf[i + 1] & 0x0F
        out[o] = (hi << 4) | lo
        o += 1
    return out


def _pack_row(idx_row, y=0):
    """把 0..15 灰度的索引行打包成 1bpp，使用 4×4 Bayer 抖动。

    关键修复：原实现是 `if v > 7` 简单阈值，导致文字抗锯齿边缘直接
    硬边化（马赛克感）。改用 Bayer 抖动后，16 级灰度的平滑边缘
    会通过点阵密度"模拟"，视觉上更接近原图。
    """
    n = len(idx_row)
    out = bytearray(n // 8)
    row_bayer = _BAYER4[y & 3]
    for x in range(n):
        v = idx_row[x]
        # v(0..15) 映射到 0..255，Bayer 阈值映射到 0..248 中间值
        thr = row_bayer[x & 3] * 16 + 8
        if v * 17 > thr:
            out[x >> 3] |= (0x80 >> (x & 7))
    return bytes(out)


def _open_background(background):
    if not background:
        return (None, False)
    if isinstance(background, str):
        try:
            return (open(background, "rb"), True)
        except OSError as e:
            print("[TEXT] background open fail:", e)
            return (None, False)
    return (None, False)


def _read_bg_row(bg_handle, bg_spec, out_y):
    row = bytearray(W)
    if bg_handle is not None:
        try:
            bg_handle.seek(out_y * 100)
            b = bg_handle.read(100)
            n = len(b)
            for i in range(100):
                byte_v = b[i] if i < n else 0xFF
                for bit in range(8):
                    row[i * 8 + bit] = 15 if (byte_v & (0x80 >> bit)) else 0
            return row
        except Exception:
            pass
    for i in range(W):
        row[i] = bg_spec
    return row


def _fill_bg_band(out_idx, bg_handle, bg_spec, band_start, band_h):
    if bg_handle is not None:
        try:
            bg_handle.seek(band_start * 100)
            total = band_h * 100
            data = bg_handle.read(total)
            n = len(data)
            for y_local in range(band_h):
                base = y_local * W
                bb = y_local * 100
                for i in range(100):
                    byte_v = data[bb + i] if (bb + i) < n else 0xFF
                    for bit in range(8):
                        out_idx[base + i * 8 + bit] = 15 if (byte_v & (0x80 >> bit)) else 0
            return True
        except Exception as e:
            print("[TEXT] bg read fail:", e)
    for y_local in range(band_h):
        base = y_local * W
        for x in range(W):
            out_idx[base + x] = bg_spec
    return True


# ---------- 非流式主入口 ----------

def render_full(text, size=32, direction=0, per_char=None, default_style=None,
                align='left', valign='top', line_height=1.5,
                margin_top=20, margin_bottom=20,
                margin_left=20, margin_right=20, background=None,
                font_path=None, font_paths=None, extra_fonts=None,
                line_extras=None):
    from .layout import _parse_default_style, _parse_per_char
    from .fonts import _resolve_font

    font = _resolve_font(font_path=font_path, font_paths=font_paths, size=size)
    if font is None:
        raise ValueError("字模加载失败")
    ds = _parse_default_style(default_style)
    parsed_per_char = _parse_per_char(per_char, ds)

    if direction in (90, 270):
        inner_w, inner_h = H, W
    else:
        inner_w, inner_h = W, H

    text_layer = _make_bytearray_fill(inner_w * inner_h, SENTINEL)

    from .layout import _compute_line_metrics
    line_infos, lines_text = _compute_line_metrics(
        text, font, parsed_per_char, ds, inner_w, inner_h,
        align, valign, line_height,
        margin_top, margin_bottom, margin_left, margin_right,
        extra_fonts or {}, line_extras=line_extras)

    for li, info in enumerate(line_infos):
        cy = info['cy']
        max_h = info['max_h']
        base_idx = info['start']
        cx = info['cx0']
        line_text = lines_text[li]

        for cii, ch in enumerate(line_text):
            gidx = base_idx + cii
            style = parsed_per_char.get(str(gidx), ds)
            cf = _get_char_font(font, style, extra_fonts or {})
            g = _glyph_of(cf, ch)
            adv = g[0] if g and g[0] > 0 else cf.space_width()
            ls = _style_ls(style)
            char_top = cy + (max_h - cf.max_h)

            if g is not None and g[0] > 0:
                _blit_char(text_layer, inner_w, inner_h, g, cx, char_top, style, cf.max_h)

            fg = style.get('fg', 0)
            if style.get('underline'):
                ul_y = char_top + cf.max_h - 2
                if 0 <= ul_y < inner_h:
                    for x in range(cx, cx + adv):
                        if 0 <= x < inner_w:
                            text_layer[ul_y * inner_w + x] = fg
            if style.get('strikethrough'):
                st_y = char_top + cf.max_h // 2
                if 0 <= st_y < inner_h:
                    for x in range(cx, cx + adv):
                        if 0 <= x < inner_w:
                            text_layer[st_y * inner_w + x] = fg
            cx += adv + ls

    rotated, rw, rh = _rotate(text_layer, inner_w, inner_h, direction)

    bg_handle, owned = _open_background(background)
    try:
        bg_layer = bytearray(W * H)
        _fill_bg_band(bg_layer, bg_handle, ds['bg'], 0, H)
    finally:
        if bg_handle is not None and owned:
            try:
                bg_handle.close()
            except:
                pass

    out_idx = bytearray(W * H)
    for i in range(W * H):
        v = rotated[i]
        out_idx[i] = bg_layer[i] if v == SENTINEL else v

    return _to_4bpp(out_idx, W, H)


def render_and_display(epd, text, size=32, **kw):
    render_and_display_stream(epd, text, size=size, **kw)


# =========================================================================
# 流式渲染（两遍扫描，低内存）
# =========================================================================

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

        fg = style.get('fg', 0)
        if style.get('underline'):
            ul_y = char_top + cf.max_h - 2
            if band_start <= ul_y < band_end:
                y_local = ul_y - band_start
                base = y_local * inner_w
                for x in range(cx, min(cx + adv, inner_w)):
                    if x >= 0:
                        idx_buf[base + x] = fg
        if style.get('strikethrough'):
            st_y = char_top + cf.max_h // 2
            if band_start <= st_y < band_end:
                y_local = st_y - band_start
                base = y_local * inner_w
                for x in range(cx, min(cx + adv, inner_w)):
                    if x >= 0:
                        idx_buf[base + x] = fg
        cx += adv + ls


def _draw_line_to_band_180(idx_buf, out_w, band_start, band_end,
                           line_text, line_info, per_char, ds, font, temp_fonts):
    cy = line_info['cy']
    max_h = line_info['max_h']
    base_idx = line_info['start']
    cx = line_info['cx0']

    def mx(px):
        return W - 1 - px

    def my(py):
        return H - 1 - py

    for cii, ch in enumerate(line_text):
        gidx = base_idx + cii
        style = per_char.get(str(gidx), ds) if per_char else ds
        cf = _get_char_font(font, style, temp_fonts)
        g = _glyph_of(cf, ch)
        adv = g[0] if g and g[0] > 0 else cf.space_width()
        ls = _style_ls(style)
        char_top = cy + (max_h - cf.max_h)
        fg = style.get('fg', 0)

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
                                    is_nb = True
                                    break
                                if bold_dx and _bit_on(gdata, rb, rel_x + dx - bold_dx, nr, gw, gh):
                                    is_nb = True
                                    break
                            if is_nb: break
                        if is_nb:
                            rx, ry = mx(px), my(py)
                            if band_start <= ry < band_end and 0 <= rx < out_w:
                                y_local = ry - band_start
                                idx_buf[y_local * out_w + rx] = outline_spec

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
                                idx_buf[y_local * out_w + rx] = fg
                        if bold_dx:
                            px2 = px + bold_dx
                            if 0 <= px2 < W:
                                rx2, ry = mx(px2), my(py)
                                if band_start <= ry < band_end and 0 <= rx2 < out_w:
                                    y_local = ry - band_start
                                    idx_buf[y_local * out_w + rx2] = fg

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
                            idx_buf[base + x_out] = fg
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
                            idx_buf[base + x_out] = fg
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

        fg = style.get('fg', 0)
        if style.get('underline'):
            ul_y = char_top + cf.max_h - 2
            if 0 <= ul_y < inner_h:
                for px in range(cx, cx + adv):
                    if band_start <= px < band_end_x:
                        col = px - band_start
                        idx_buf[col * inner_h + ul_y] = fg
        if style.get('strikethrough'):
            st_y = char_top + cf.max_h // 2
            if 0 <= st_y < inner_h:
                for px in range(cx, cx + adv):
                    if band_start <= px < band_end_x:
                        col = px - band_start
                        idx_buf[col * inner_h + st_y] = fg
        cx += adv + ls


def _stream_direct(epd, direction, line_infos, lines_text, font, per_char,
                   ds, bg_spec, band_rows, bg_handle, temp_fonts):
    for band_start in range(0, H, band_rows):
        band_end = min(band_start + band_rows, H)
        band_h = band_end - band_start
        idx_buf = bytearray(band_h * W)

        _fill_bg_band(idx_buf, bg_handle, bg_spec, band_start, band_h)

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
            global_y = band_start + y_local
            epd.write_chunk(_pack_row(idx_row, global_y))

        del idx_buf
        gc.collect()


def _stream_rotated(epd, direction, inner_w, inner_h,
                    line_infos, lines_text, font, per_char,
                    ds, bg_spec, band_rows, bg_handle, temp_fonts):
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
            bg_row = _read_bg_row(bg_handle, bg_spec, out_y)
            out_row = bytearray(W)

            if direction == 90:
                for out_x in range(W):
                    y_in = inner_h - 1 - out_x
                    tv = text_idx[text_base + y_in]
                    out_row[out_x] = bg_row[out_x] if tv == SENTINEL else tv
            else:
                for out_x in range(W):
                    tv = text_idx[text_base + out_x]
                    out_row[out_x] = bg_row[out_x] if tv == SENTINEL else tv

            epd.write_chunk(_pack_row(out_row, out_y))

        del text_idx
        gc.collect()


def render_and_display_stream(epd, text, size=32, direction=0, per_char=None,
                              default_style=None, align='left', valign='top',
                              line_height=1.5, margin_top=20, margin_bottom=20,
                              margin_left=20, margin_right=20,
                              band_rows=8, background=None,
                              font_path=None, font_paths=None,
                              extra_fonts=None, line_extras=None):
    gc.collect()
    font = _resolve_font(font_path=font_path, font_paths=font_paths, size=size)
    if font is None:
        raise ValueError("字模加载失败")

    from .layout import _parse_default_style, _parse_per_char

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

    bg_spec = ds['bg']
    bg_handle = None
    owned = False
    if background:
        bg_handle, owned = _open_background(background)

    gc.collect()
    font_desc = ("multi:%d chunks" % len(font_paths)) if font_paths else str(font_path)
    print("[TEXT] stream start: free=%d, lines=%d, dir=%d, band=%d, font=%s, extra=%d"
          % (gc.mem_free(), len(line_infos), direction, band_rows,
             font_desc, len(temp_fonts)))

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

    epd.init()
    epd.begin_frame()
    try:
        if direction == 0 or direction == 180:
            _stream_direct(epd, direction, line_infos, lines_text,
                           font, pc, ds, bg_spec, band_rows, bg_handle, temp_fonts)
        else:
            _stream_rotated(epd, direction, inner_w, inner_h,
                            line_infos, lines_text, font, pc,
                            ds, bg_spec, band_rows, bg_handle, temp_fonts)
    finally:
        if bg_handle is not None and owned:
            try:
                bg_handle.close()
            except:
                pass
        epd.end_frame()
        epd.sleep()
        gc.collect()
        print("[TEXT] stream done: free=%d" % gc.mem_free())