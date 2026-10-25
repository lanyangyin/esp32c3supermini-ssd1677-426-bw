# mosaic.py — 网格拼图（1bpp，支持全刷/快刷/局刷）
#
# src 格式：
#   '/path/to/N.bin'     文件源（1bpp，48000 字节）
#   'solid:N'            纯色填充，N=0~15（0=黑，15=白）
#   None 或 ''           默认为白（15）

import gc

W, H = 800, 480
FRAME_BYTES = W * H // 8  # 48000 (1bpp)
MAX_CELLS = 64
WHITE_IDX = 15

BAYER = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)


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

        row_of = [0] * H
        for ry in range(rows):
            for y in range(row_y[ry], row_y[ry + 1]):
                if 0 <= y < H:
                    row_of[y] = ry

        out_row = bytearray(W)

        for out_y in range(H):
            ry = row_of[out_y]
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
                    byte_start = x0 >> 3
                    byte_end = (x1 + 7) >> 3
                    byte_count = byte_end - byte_start
                    if byte_count <= 0:
                        continue
                    try:
                        f.seek(out_y * 100 + byte_start)
                        line = f.read(byte_count)
                    except Exception:
                        continue
                    n = len(line)
                    for x in range(x0, x1):
                        bi = (x >> 3) - byte_start
                        if bi < 0 or bi >= n:
                            continue
                        bval = line[bi]
                        bit = 7 - (x & 7)
                        v = (bval >> bit) & 1
                        out_row[x] = 15 if v else 0

            packed = bytearray(100)
            for bi in range(100):
                bv = 0
                base_x = bi * 8
                for bit in range(8):
                    v = out_row[base_x + bit]
                    if v * 17 > BAYER[out_y & 3][(base_x + bit) & 3] * 16 + 8:
                        bv |= 0x80 >> bit
                packed[bi] = bv
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
    n = 0
    with open(path, 'wb') as f:
        for row in _iter_rows(grid):
            f.write(row)
            n += len(row)
    return n


def compose_bytes(grid):
    out = bytearray(FRAME_BYTES)
    pos = 0
    for row in _iter_rows(grid):
        out[pos:pos + len(row)] = row
        pos += len(row)
    return bytes(out)


def compose_and_display(grid, epd_mod=None, mode='full'):
    """流式显示到屏幕。

    mode: 'full' | 'fast' | 'partial'
    """
    if epd_mod is None:
        import epd as epd_mod

    epd_mod.init()
    epd_mod.begin_frame()
    try:
        for row in _iter_rows(grid):
            epd_mod.write_chunk(row)
    finally:
        epd_mod.end_frame(mode)
        epd_mod.sleep()