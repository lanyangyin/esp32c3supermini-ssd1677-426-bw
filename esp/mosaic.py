# mosaic.py — 网格拼图（多级灰度 800x480，4bpp）
#
# 用法（REPL）：
#   from mosaic import compose_and_display, compose_to_file, compose_bytes
#
#   # 2×2 拼图
#   grid = [
#       ['/bgs/0.bin',  'solid:15'],          # 背景图 + 白
#       ['solid:8', '/bins/0.bin'],           # 中灰 + BIN
#   ]
#   compose_and_display(grid)
#
# src 格式：
#   '/path/to/N.bin'     文件源（800x480 4bpp 灰度）
#   'solid:N'            纯色填充，N=0~15（0=黑，15=白）
#   None 或 ''           默认为白（15）
#
# 每一格显示"源 bin 同位置"的图像；纯色则按该位置的灰度值填充。
# 内存策略：逐行处理 + 句柄缓存，8×8=64 格时峰值 < 5KB。

import gc

W, H = 800, 480
FRAME_BYTES = W * H // 2  # 192000
MAX_CELLS = 64
WHITE_IDX = 15


def _norm_grid(grid):
    if not grid:
        raise ValueError('grid 为空')
    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    if cols == 0:
        raise ValueError('grid 无列')
    if rows * cols > MAX_CELLS:
        raise ValueError('格子数 {} 超过上限 {}'.format(rows * cols, MAX_CELLS))
    return [list(r) + [None] * (cols - len(r)) for r in grid]


def _boundaries(total, n):
    return [round(i * total / n) for i in range(n + 1)]


def _parse_src(src):
    """返回 (kind, params)。
    kind = 'solid'  → params = (idx,)
    kind = 'empty'  → params = None
    """
    if not src or not isinstance(src, str):
        return ('empty', None)
    if src.startswith('solid:'):
        try:
            idx = int(src[6:])
            if 0 <= idx <= 15:
                return ('solid', (idx,))
        except Exception:
            pass
        return ('empty', None)
    return ('file', src)


def _iter_rows(grid):
    grid = _norm_grid(grid)
    rows = len(grid)
    cols = len(grid[0])
    col_x = _boundaries(W, cols)
    row_y = _boundaries(H, rows)

    # 预解析每格：cell_specs = [(ry, cx, kind, params)]
    cell_specs = []
    handles = {}
    try:
        for ry in range(rows):
            for cx in range(cols):
                src = grid[ry][cx]
                kind, params = _parse_src(src)
                if kind == 'file':
                    if src not in handles:
                        try:
                            handles[src] = open(src, 'rb')
                        except OSError as e:
                            print('[mosaic] open fail:', src, e)
                            kind = 'empty'
                            params = None
                cell_specs.append((ry, cx, kind, params))

        # 每行属于哪个 ry
        row_of = [0] * H
        for ry in range(rows):
            for y in range(row_y[ry], row_y[ry + 1]):
                if 0 <= y < H:
                    row_of[y] = ry

        out_row = bytearray(W)

        for out_y in range(H):
            ry = row_of[out_y]
            # 先用白色填满整行
            for x in range(W):
                out_row[x] = WHITE_IDX

            for (c_ry, cx, kind, params) in cell_specs:
                if c_ry != ry:
                    continue
                x0 = col_x[cx]
                x1 = col_x[cx + 1]
                if x1 <= x0:
                    continue

                if kind == 'empty':
                    continue
                elif kind == 'solid':
                    idx = params[0]
                    for x in range(x0, x1):
                        out_row[x] = idx
                elif kind == 'file':
                    f = handles[params]
                    # 每个像素 4bit，800 像素 = 400 字节每行
                    byte_start = x0 >> 1
                    byte_end = (x1 + 1) >> 1
                    byte_count = byte_end - byte_start
                    if byte_count <= 0:
                        continue
                    try:
                        f.seek(out_y * 400 + byte_start)
                        line = f.read(byte_count)
                    except Exception:
                        continue
                    n = len(line)
                    for x in range(x0, x1):
                        bi = (x >> 1) - byte_start
                        if bi < 0 or bi >= n:
                            continue
                        bval = line[bi]
                        # 提取 4bit 灰度值
                        nib = (bval & 0xF) if (x & 1) else ((bval >> 4) & 0xF)
                        out_row[x] = nib

            # 将 800 像素打包为 400 字节（4bpp）
            packed = bytearray(400)
            for i in range(0, W, 2):
                hi = out_row[i] & 0x0F
                lo = out_row[i + 1] & 0x0F
                packed[i >> 1] = (hi << 4) | lo
            yield bytes(packed)

            if out_y % 40 == 0:
                gc.collect()

    finally:
        for f in handles.values():
            try:
                f.close()
            except Exception:
                pass


def compose_to_file(grid, path):
    """流式写文件。返回写入字节数。"""
    n = 0
    with open(path, 'wb') as f:
        for row in _iter_rows(grid):
            f.write(row)
            n += len(row)
    return n


def compose_bytes(grid):
    """返回完整 bytes（192000）。"""
    out = bytearray(FRAME_BYTES)
    pos = 0
    for row in _iter_rows(grid):
        out[pos:pos + len(row)] = row
        pos += len(row)
    return bytes(out)


def compose_and_display(grid, epd=None):
    """流式显示到屏幕。"""
    if epd is None:
        import epd

    epd.init()

    try:
        for i, row in enumerate(_iter_rows(grid)):
            start_idx = i * 400
            epd._display.buffer[start_idx:start_idx + 400] = row
    finally:
        epd.show()
        epd.sleep()